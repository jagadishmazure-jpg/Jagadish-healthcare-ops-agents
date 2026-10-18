"""ReAct-style planner orchestrating telemetry, SOP, bed-match, and demand tools.

Loop: reason (detect intents, possibly several -> fan-out) -> act (run each
tool, audited) -> synthesize (cited answer + escalation tier + optional
confirmation-gate token for high-impact actions). Every step is audit-logged.

Hard boundary: this planner answers OPERATIONAL questions (beds, throughput,
staffing, discharge flow). Anything clinical -- diagnosis, treatment,
medication, symptoms -- is refused explicitly, never answered. See the
"Boundary with Jagadish-clinical-ops-agents" section in the README.
"""

import re

from . import audit as audit_mod
from . import bedmatch
from . import demand as demand_mod
from . import discharge as discharge_mod
from . import escalation as esc
from . import sop_rag
from . import text2sql

_CLINICAL_RE = re.compile(
    r"\b(diagnos|treatment|medicat|dosage|prescrib|symptom|disease|\bcure\b)",
    re.I,
)
_REASSIGN_RE = re.compile(
    r"reassign|transfer (\w+ )?patients?|move (\w+ )?patients?|assign beds", re.I
)
_OVERRIDE_RE = re.compile(r"override.*discharg|discharg.*override", re.I)


class PlannerAgent:
    def __init__(self, conn, sops_dir, demand_backend=None):
        self.conn = conn
        self.audit = audit_mod.AuditLog()
        self.gate = esc.ConfirmationGate()
        self.discharge_gate = discharge_mod.DischargeGate(conn)
        self.sops = sop_rag.SopIndex(sop_rag.load_chunks(sops_dir))
        self.demand = demand_backend or demand_mod.FakeDemand()

    # -- tool-facing helpers -------------------------------------------------
    def _tool_telemetry(self, intent):
        result = text2sql.run(self.conn, intent)
        self.audit.record("planner", "tool.telemetry", {
            "intent": intent, "sql": result["sql"], "rows": len(result["rows"]),
        })
        return result

    def _tool_sop(self, question):
        answer = self.sops.answer(question)
        self.audit.record("planner", "tool.sop", {
            "found": answer["found"], "citations": answer["citations"],
        })
        return answer

    def _tool_demand(self, region, day):
        signal = self.demand.get_signal(region, day)
        self.audit.record("planner", "tool.demand", {
            "region": region, "day": day,
            "ok": signal.get("ok"),
            "surge_index": signal.get("surge_index"),
        })
        return signal

    def _tool_bedmatch(self, max_assignments=3):
        result = bedmatch.match(self.conn, max_assignments=max_assignments)
        self.audit.record("planner", "tool.bedmatch", {
            "assignments": len(result["assignments"]),
            "escalated": len(result["escalated"]),
        })
        return result

    # -- main entry point ----------------------------------------------------
    def ask(self, question, region="metro-north", day=90):
        self.audit.record("planner", "question_received", {"question": question})

        if _CLINICAL_RE.search(question):
            self.audit.record("planner", "clinical_refused", {})
            return {
                "question": question,
                "refused": True,
                "answer": (
                    "I can't provide clinical or medical advice -- no diagnosis, "
                    "treatment, medication, or symptom guidance. I can help with "
                    "hospital operations: bed occupancy, ED wait times and "
                    "boarding, discharge planning, or staffing."
                ),
                "citations": [],
                "tier": "T1",
                "gate_token": None,
                "audit_events": len(self.audit.events()),
            }

        intents = text2sql.detect_intents(question)

        telemetry, occupancy_rows, boarder_rows = {}, [], []
        staffing_rows, blocked_rows = [], []
        for intent in intents:  # multi-intent fan-out
            try:
                result = self._tool_telemetry(intent)
            except text2sql.EscalationNeeded:
                self.audit.record("planner", "telemetry.escalated",
                                  {"intent": intent})
                continue
            telemetry[intent] = result
            if intent == "bed_occupancy":
                occupancy_rows = result["rows"]
            if intent == "boarder_backlog":
                boarder_rows = result["rows"]
            if intent == "staffing_gaps":
                staffing_rows = result["rows"]
            if intent == "discharge_ready":
                blocked_rows = [r for r in result["rows"] if not r["ready"]]

        sop_answer = self._tool_sop(question)

        demand_signal = None
        if boarder_rows or "ed_wait_times" in intents:
            demand_signal = self._tool_demand(region, day)

        bedmatch_result = None
        if "boarder_backlog" in intents and boarder_rows:
            bedmatch_result = self._tool_bedmatch()

        override_requested = bool(_OVERRIDE_RE.search(question))
        tier = esc.assess_tier(
            boarders=boarder_rows,
            occupancy=occupancy_rows,
            staffing_gaps=staffing_rows,
            blocked_discharges=blocked_rows,
            override_requested=override_requested,
        )

        gate_token = self._maybe_gate(question, boarder_rows, override_requested)

        answer, citations = self._synthesize(
            question, intents, telemetry, sop_answer, demand_signal,
            bedmatch_result, blocked_rows, tier, gate_token, override_requested,
        )
        self.audit.record("planner", "answer_synthesized", {
            "tier": tier, "gate_token": gate_token, "citations": citations,
        })
        return {
            "question": question,
            "refused": False,
            "intents": intents,
            "row_counts": {k: len(v["rows"]) for k, v in telemetry.items()},
            "answer": answer,
            "citations": citations,
            "tier": tier,
            "gate_token": gate_token,
            "demand": demand_signal,
            "bedmatch": bedmatch_result,
            "audit_events": len(self.audit.events()),
        }

    def _maybe_gate(self, question, boarder_rows, override_requested):
        if _REASSIGN_RE.search(question) and boarder_rows:
            affected = len(boarder_rows)
            if esc.ConfirmationGate.requires_gate("bed_reassignment", affected):
                token = self.gate.request(
                    "bed_reassignment",
                    f"Reassign {affected} ED boarders to ward beds "
                    "(acuity-weighted matching)",
                    {"patients_affected": affected},
                )
                self.audit.record("planner", "gate.requested",
                                  {"token": token, "patients_affected": affected})
                return token
        if override_requested:
            token = self.gate.request(
                "discharge_override",
                "Discharge override requested by planner",
                {"question": question},
            )
            self.audit.record("planner", "gate.requested", {"token": token})
            return token
        return None

    def _synthesize(self, question, intents, telemetry, sop_answer,
                    demand_signal, bedmatch_result, blocked_rows,
                    tier, gate_token, override_requested):
        lines, citations = [], []
        if not intents and not sop_answer["found"]:
            return ("I couldn't map that to a hospital-operations question or "
                    "an SOP. Try asking about bed occupancy, ED wait times, "
                    "boarding, discharges, or staffing."), []
        for intent, result in telemetry.items():
            lines.append(f"[{intent}] {len(result['rows'])} row(s) from governed views.")
            if intent == "bed_occupancy" and result["rows"]:
                worst = result["rows"][0]
                lines.append(
                    f"Highest occupancy: {worst['ward_name']} -- "
                    f"{worst['occupancy_pct']}% "
                    f"({worst['available_beds']} beds available)."
                )
            if intent == "boarder_backlog" and result["rows"]:
                top = result["rows"][0]
                lines.append(
                    f"Longest-waiting acute boarder: {top['arrival_id']} "
                    f"(acuity {top['acuity']}, {top['wait_hours']}h waiting)."
                )
            if intent == "discharge_ready" and blocked_rows:
                lines.append(
                    f"{len(blocked_rows)} discharge(s) blocked, e.g. "
                    f"{blocked_rows[0]['stay_id']} held by: "
                    f"{blocked_rows[0]['blocked_by']}."
                )
        if bedmatch_result:
            for a in bedmatch_result["assignments"]:
                lines.append(
                    f"Bed match: {a['boarder']} (acuity {a['acuity']}) -> "
                    f"{a['bed_id']} [{a['ward_type']}] score {a['score']} "
                    f"{a['breakdown']}."
                )
            for e in bedmatch_result["escalated"]:
                lines.append(f"No bed for {e['boarder']}: {e['reason']}.")
        if override_requested:
            lines.append(
                "Discharge overrides always need human confirmation -- "
                "use the gate token above with the discharge checklist."
            )
        if sop_answer["found"]:
            lines.append(sop_answer["message"])
            for excerpt in sop_answer["excerpts"]:
                lines.append(f"- {excerpt['citation']}: {excerpt['text'][:200]}")
            citations.extend(sop_answer["citations"])
        if demand_signal:
            if demand_signal.get("ok"):
                lines.append(
                    f"Regional demand signal ({demand_signal['region']}, day "
                    f"{demand_signal['day']}): surge index "
                    f"{demand_signal['surge_index']} "
                    f"({', '.join(demand_signal['drivers'])})."
                )
            else:
                lines.append(
                    f"Demand signal gap: {demand_signal.get('gap')}."
                )
        lines.append(f"Escalation tier: {tier}.")
        if gate_token:
            lines.append(
                f"High-impact action gated behind human confirmation: {gate_token}. "
                "Approve or deny it in the demo before anything executes."
            )
        return "\n".join(lines), citations
