"""Discharge-readiness checklist gates (domain differentiator).

A patient is discharge-ready only when every checklist item passes:

- meds_reconciled, followup_scheduled, discharge_summary_signed,
  transport_arranged, home_care_confirmed

Anything less is reported as blocked, naming the missing items. An override
(discharging despite a missing item) is never silent: it requires a human
confirmation gate (see escalation.py) and is written to the audit chain with
the approver and reason. This is an operational safety gate, not a clinical
judgment -- it does not decide *whether* a patient should go home.
"""

from . import views


class OverrideNotApproved(Exception):
    """A discharge override was attempted without an approved gate token."""


class DischargeGate:
    def __init__(self, conn):
        self.conn = conn
        self._overrides = {}

    def checklist(self, stay_id):
        rows = self.conn.execute(
            "SELECT item, status FROM discharge_checklist WHERE stay_id=? "
            "ORDER BY item",
            (stay_id,),
        ).fetchall()
        return [(r["item"], r["status"]) for r in rows]

    def blocked_by(self, stay_id):
        return [item for item, status in self.checklist(stay_id)
                if status != "pass"]

    def is_ready(self, stay_id):
        """True only when every checklist item passes (or an approved override exists)."""
        if stay_id in self._overrides:
            return True
        return not self.blocked_by(stay_id)

    def blocked_stays(self):
        """Stays with at least one non-passing item, most-blocked first."""
        rows = views.run_query(
            self.conn,
            "SELECT stay_id, patient_ref, ward_name, items_passed, "
            "items_total, blocked_by FROM v_discharge_ready "
            "WHERE ready = 0 ORDER BY items_passed ASC",
        )
        return rows

    def ready_stays(self):
        rows = views.run_query(
            self.conn,
            "SELECT stay_id, patient_ref, ward_name, bed_id FROM v_discharge_ready "
            "WHERE ready = 1 ORDER BY stay_id ASC",
        )
        return rows

    def request_override(self, stay_id, reason, approver, gate):
        """Mint a confirmation-gate token for discharging despite blockers."""
        missing = self.blocked_by(stay_id)
        if not missing:
            raise ValueError(f"{stay_id} is already discharge-ready; no override needed")
        if not reason or not reason.strip():
            raise ValueError("an override requires a non-blank reason")
        if not approver or not approver.strip():
            raise ValueError("an override requires a named approver")
        return gate.request(
            "discharge_override",
            f"Discharge override for {stay_id} "
            f"(blocked by: {', '.join(missing)})",
            {"stay_id": stay_id, "blocked_by": missing,
             "reason": reason.strip(), "approver": approver.strip()},
        )

    def apply_override(self, stay_id, token, gate, audit=None):
        """Apply an override only if its gate token was human-approved."""
        record = gate.get(token)
        if record is None or record.get("status") != "approved":
            raise OverrideNotApproved(
                f"gate token {token} is not approved; override refused")
        payload = record.get("payload", {})
        if payload.get("stay_id") != stay_id:
            raise OverrideNotApproved(
                f"gate token {token} was minted for a different stay")
        self._overrides[stay_id] = {
            "token": token,
            "approver": payload.get("approver"),
            "reason": payload.get("reason"),
            "blocked_by": payload.get("blocked_by"),
        }
        if audit is not None:
            audit.record("planner", "discharge.override_applied", {
                "stay_id": stay_id, "token": token,
                "approver": payload.get("approver"),
            })
        return dict(self._overrides[stay_id])
