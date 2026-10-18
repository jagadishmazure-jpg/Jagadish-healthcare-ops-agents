"""Regression checks for the hospital patient-flow operations agents.

Deterministic (seeded) synthetic telemetry; stdlib only.
"""

import os
import sqlite3

import pytest

from hospops import audit as audit_mod
from hospops import bedmatch, demo_helpers, hospital, sop_rag, text2sql, views
from hospops import demand as demand_mod
from hospops import discharge as discharge_mod
from hospops import escalation as esc
from hospops import feedback as feedback_mod
from hospops import orchestrator as orch_mod

SOPS_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "docs", "sops")


@pytest.fixture()
def conn():
    c = hospital.connect()
    return views.bootstrap(c, seed=42)


@pytest.fixture()
def planner(conn):
    return orch_mod.PlannerAgent(conn, SOPS_DIR)


# -- seed data / anchors -----------------------------------------------------

def test_tables_exist(conn):
    tables = {r["name"] for r in
              conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"wards", "beds", "ed_arrivals", "inpatients",
            "discharge_checklist", "staffing"} <= tables


def test_anchor_ward_occupancy(conn):
    row = views.run_query(
        conn, "SELECT * FROM v_bed_occupancy WHERE ward_id='W-MED-A'")[0]
    assert row["occupied_beds"] == 39
    assert row["available_beds"] == 1
    assert row["occupancy_pct"] == 97.5


def test_anchor_blocked_discharge(conn):
    gate = discharge_mod.DischargeGate(conn)
    assert gate.is_ready("ST-1040") is False
    assert gate.blocked_by("ST-1040") == ["discharge_summary_signed"]


def test_anchor_acuity1_boarder(conn):
    rows = views.run_query(
        conn, "SELECT * FROM v_ed_boarders WHERE arrival_id='ED-0007'")
    assert len(rows) == 1
    assert rows[0]["acuity"] == 1
    assert rows[0]["wait_hours"] == 14.0


def test_seed_deterministic():
    c1 = views.bootstrap(hospital.connect(), seed=42)
    c2 = views.bootstrap(hospital.connect(), seed=42)
    n1 = c1.execute("SELECT COUNT(*) n FROM ed_arrivals").fetchone()["n"]
    n2 = c2.execute("SELECT COUNT(*) n FROM ed_arrivals").fetchone()["n"]
    assert n1 == n2 == 28
    o1 = views.run_query(c1, "SELECT * FROM v_bed_occupancy ORDER BY ward_id")
    o2 = views.run_query(c2, "SELECT * FROM v_bed_occupancy ORDER BY ward_id")
    assert [r["occupied_beds"] for r in o1] == [r["occupied_beds"] for r in o2]


# -- gateway -----------------------------------------------------------------

def test_gateway_rejects_delete(conn):
    with pytest.raises(views.SecurityError) as exc:
        views.run_query(conn, "DELETE FROM wards")
    assert exc.value.rule_id == "SEC-SELECT-01"


def test_gateway_rejects_insert(conn):
    with pytest.raises(views.SecurityError) as exc:
        views.run_query(conn, "SELECT * FROM v_bed_occupancy WHERE 1=1; INSERT INTO wards VALUES ('x','y','z',1,'')")
    assert exc.value.rule_id in ("SEC-SELECT-01", "SEC-WRITE-02")


def test_gateway_rejects_unknown_table(conn):
    with pytest.raises(views.SecurityError) as exc:
        views.run_query(conn, "SELECT * FROM wards")
    assert exc.value.rule_id == "SEC-VIEW-03"


def test_gateway_literal_false_positive(conn):
    rows = views.run_query(
        conn, "SELECT ward_id FROM v_bed_occupancy WHERE ward_name != 'insert'")
    assert len(rows) == 6


def test_gateway_multi_statement(conn):
    with pytest.raises(views.SecurityError) as exc:
        views.run_query(conn, "SELECT * FROM v_bed_occupancy; SELECT 1")
    assert exc.value.rule_id == "SEC-SELECT-01"


def test_limit_capped(conn):
    rows = views.run_query(conn, "SELECT * FROM v_ed_boarders LIMIT 5000")
    assert len(rows) <= 100


def test_staffing_gap_anchor(conn):
    rows = views.run_query(
        conn, "SELECT * FROM v_staffing_gaps WHERE ward_id='W-MED-A' "
               "AND day=90 AND shift='NIGHT'")
    assert len(rows) == 1
    assert rows[0]["gap"] == 3


def test_staffing_gaps_only_positive(conn):
    rows = views.run_query(conn, "SELECT * FROM v_staffing_gaps")
    assert rows and all(r["gap"] > 0 for r in rows)


# -- text2sql ------------------------------------------------------------------

def test_detect_all_five_intents():
    assert "bed_occupancy" in text2sql.detect_intents("bed occupancy by ward")
    assert "ed_wait_times" in text2sql.detect_intents("average ED wait time?")
    assert "discharge_ready" in text2sql.detect_intents("who is ready for discharge")
    assert "staffing_gaps" in text2sql.detect_intents("nurse staffing gaps tonight")
    assert "boarder_backlog" in text2sql.detect_intents("ED boarder backlog")


def test_unknown_intent_raises(conn):
    with pytest.raises(text2sql.UnknownIntent):
        text2sql.run_question(conn, "tell me about quantum cryptography")


def test_build_sql_unknown_raises():
    with pytest.raises(text2sql.UnknownIntent):
        text2sql.build_sql("nope")


def test_run_returns_rows(conn):
    result = text2sql.run(conn, "bed_occupancy")
    assert len(result["rows"]) == 6
    assert result["intent"] == "bed_occupancy"


def test_run_question_first_intent(conn):
    result = text2sql.run_question(conn, "show discharge ready list")
    assert result["intent"] == "discharge_ready"


def test_fanout_multiple():
    intents = text2sql.detect_intents("bed occupancy and ED boarding backlog")
    assert "bed_occupancy" in intents and "boarder_backlog" in intents


def test_run_retries_then_escalates(conn):
    real = views.run_query
    calls = {"n": 0}

    def flaky(c, sql, params=()):
        calls["n"] += 1
        raise sqlite3.Error("boom")

    views.run_query = flaky
    try:
        with pytest.raises(text2sql.EscalationNeeded):
            text2sql.run(conn, "bed_occupancy")
    finally:
        views.run_query = real
    assert calls["n"] == 2  # one try + one simplified retry


# -- bed matching ---------------------------------------------------------------

def test_acuity1_goes_to_icu(conn):
    result = bedmatch.match(conn)
    by_boarder = {a["boarder"]: a for a in result["assignments"]}
    assert by_boarder["ED-0007"]["ward_type"] == "ICU"
    for a in result["assignments"]:
        if a["acuity"] == 1:
            assert a["ward_type"] == "ICU"


def test_unmatched_boarder_escalates_with_reason(conn):
    result = bedmatch.match(conn)
    assert result["escalated"]
    for e in result["escalated"]:
        assert e["reason"]


def test_iso_patient_requires_iso_bed():
    boarder = {"acuity": 3, "iso_required": 1}
    ward = {"ward_type": "MEDICAL", "capabilities": "telemetry",
            "occupancy_pct": 80.0}
    assert bedmatch.score_candidate(boarder, ward) is None
    ward_iso = {"ward_type": "MEDICAL", "capabilities": "telemetry,iso",
                "occupancy_pct": 80.0}
    assert bedmatch.score_candidate(boarder, ward_iso) is not None


def test_acuity1_rejected_from_medical():
    boarder = {"acuity": 1, "iso_required": 0}
    ward = {"ward_type": "MEDICAL", "capabilities": "telemetry",
            "occupancy_pct": 50.0}
    assert bedmatch.score_candidate(boarder, ward) is None


def test_score_breakdown_sums():
    boarder = {"acuity": 4, "iso_required": 0}
    ward = {"ward_type": "MEDICAL", "capabilities": "telemetry",
            "occupancy_pct": 80.0}
    total, parts = bedmatch.score_candidate(boarder, ward)
    assert set(parts) == {"acuity_fit", "capability", "load", "total"}
    assert parts["total"] == round(
        parts["acuity_fit"] + parts["capability"] + parts["load"], 4)
    assert total == parts["total"]


def test_match_deterministic(conn):
    a = bedmatch.match(conn)
    b = bedmatch.match(conn)
    assert [(x["boarder"], x["bed_id"]) for x in a["assignments"]] == \
           [(x["boarder"], x["bed_id"]) for x in b["assignments"]]


def test_assignments_acute_first(conn):
    result = bedmatch.match(conn, max_assignments=6)
    acuities = [a["acuity"] for a in result["assignments"]]
    assert acuities == sorted(acuities)


def test_max_assignments_respected(conn):
    result = bedmatch.match(conn, max_assignments=2)
    assert len(result["assignments"]) == 2


def test_taken_beds_unique(conn):
    result = bedmatch.match(conn)
    beds = [a["bed_id"] for a in result["assignments"]]
    assert len(beds) == len(set(beds))


# -- discharge gates -------------------------------------------------------------

def test_ready_stay_is_ready(conn):
    gate = discharge_mod.DischargeGate(conn)
    ready = gate.ready_stays()
    assert ready
    assert gate.is_ready(ready[0]["stay_id"]) is True


def test_checklist_has_five_items(conn):
    gate = discharge_mod.DischargeGate(conn)
    items = gate.checklist("ST-1040")
    assert len(items) == 5
    assert {i for i, _ in items} == set(hospital.CHECKLIST_ITEMS)


def test_blocked_stays_listed(conn):
    gate = discharge_mod.DischargeGate(conn)
    blocked = gate.blocked_stays()
    assert any(s["stay_id"] == "ST-1040" for s in blocked)


def test_override_requires_approval(conn):
    gate = discharge_mod.DischargeGate(conn)
    cg = esc.ConfirmationGate()
    token = gate.request_override("ST-1040", "family transport confirmed",
                                  "Dr. Rao", cg)
    with pytest.raises(discharge_mod.OverrideNotApproved):
        gate.apply_override("ST-1040", token, cg)


def test_override_flow_audited(conn):
    gate = discharge_mod.DischargeGate(conn)
    cg = esc.ConfirmationGate()
    audit = audit_mod.AuditLog()
    token = gate.request_override("ST-1040", "family transport confirmed",
                                  "Dr. Rao", cg)
    cg.approve(token)
    record = gate.apply_override("ST-1040", token, cg, audit=audit)
    assert record["approver"] == "Dr. Rao"
    assert gate.is_ready("ST-1040") is True
    assert any(e["action"] == "discharge.override_applied"
               for e in audit.events())


def test_override_token_bound_to_stay(conn):
    gate = discharge_mod.DischargeGate(conn)
    cg = esc.ConfirmationGate()
    token = gate.request_override("ST-1040", "reason", "Dr. Rao", cg)
    cg.approve(token)
    with pytest.raises(discharge_mod.OverrideNotApproved):
        gate.apply_override("ST-1041", token, cg)


def test_override_needs_reason(conn):
    gate = discharge_mod.DischargeGate(conn)
    cg = esc.ConfirmationGate()
    with pytest.raises(ValueError):
        gate.request_override("ST-1040", "  ", "Dr. Rao", cg)


# -- escalation tiers & gates ----------------------------------------------------

def test_t4_acuity1_long_wait():
    assert esc.assess_tier(
        boarders=[{"acuity": 1, "wait_hours": 14.0}]) == "T4"


def test_t4_occupancy_98():
    assert esc.assess_tier(
        occupancy=[{"occupancy_pct": 98.5}]) == "T4"


def test_t3_occupancy_95():
    assert esc.assess_tier(
        occupancy=[{"occupancy_pct": 97.5}]) == "T3"


def test_t3_override_requested():
    assert esc.assess_tier(override_requested=True) == "T3"


def test_t3_many_boarders():
    boarders = [{"acuity": 4, "wait_hours": 2.0}] * 11
    assert esc.assess_tier(boarders=boarders) == "T3"


def test_t2_watch():
    assert esc.assess_tier(boarders=[{"acuity": 4, "wait_hours": 2.0}]) == "T2"
    assert esc.assess_tier(staffing_gaps=[{"gap": 2}]) == "T2"
    assert esc.assess_tier(blocked_discharges=[{"stay_id": "ST-1"}]) == "T2"


def test_t1_clear():
    assert esc.assess_tier() == "T1"


def test_gate_thresholds():
    assert esc.ConfirmationGate.requires_gate("bed_reassignment", 6) is True
    assert esc.ConfirmationGate.requires_gate("bed_reassignment", 5) is False
    assert esc.ConfirmationGate.requires_gate("discharge_override") is True
    assert esc.ConfirmationGate.requires_gate("something_else") is False


def test_gate_approve_deny():
    gate = esc.ConfirmationGate()
    token = gate.request("bed_reassignment", "move 8 boarders", {})
    assert gate.get(token)["status"] == "pending"
    gate.approve(token)
    assert gate.get(token)["status"] == "approved"
    token2 = gate.request("bed_reassignment", "move 8 boarders", {})
    gate.deny(token2, reason="no beds")
    assert gate.get(token2)["status"] == "denied"
    with pytest.raises(KeyError):
        gate.approve("GATE-9999")


# -- SOP RAG ---------------------------------------------------------------------

def _index():
    return sop_rag.SopIndex(sop_rag.load_chunks(SOPS_DIR))


def test_sop_retrieves_triage():
    idx = _index()
    answer = idx.answer("what are the acuity triage levels")
    assert answer["found"] is True
    assert any("sop-01-triage-and-boarding.md" in c
               for c in answer["citations"])


def test_sop_citation_format():
    idx = _index()
    hits = idx.retrieve("discharge checklist items", k=1)
    assert hits[0]["citation"].startswith("sop-03-discharge-planning.md §")


def test_sop_not_found_explicit():
    idx = _index()
    answer = idx.answer("quantum cryptography key rotation policy")
    assert answer["found"] is False
    assert "could not find" in answer["message"].lower()
    assert answer["citations"] == []


def test_sop_citations_match_excerpts():
    idx = _index()
    answer = idx.answer("when is a discharge override allowed")
    assert [e["citation"] for e in answer["excerpts"]] == answer["citations"]


# -- demand signal ------------------------------------------------------------------

def test_fake_demand_ok():
    sig = demand_mod.FakeDemand(seed=42).get_signal("metro-north", 90)
    assert sig["ok"] is True
    assert 0 <= sig["surge_index"] <= 100
    assert isinstance(sig["drivers"], list) and sig["drivers"]


def test_fake_demand_deterministic():
    a = demand_mod.FakeDemand(seed=42).get_signal("metro-north", 90)
    b = demand_mod.FakeDemand(seed=42).get_signal("metro-north", 90)
    assert a["surge_index"] == b["surge_index"]


def test_live_demand_gap_never_hallucinates():
    sig = demand_mod.LiveDemand().get_signal("metro-north", 90)
    assert sig["ok"] is False
    assert "not configured" in sig["gap"]
    assert "surge_index" not in sig


# -- audit ----------------------------------------------------------------------------

def test_audit_chain_verifies(conn):
    audit = audit_mod.AuditLog()
    audit.record("planner", "question_received", {"q": "beds?"})
    audit.record("planner", "tool.telemetry", {"rows": 6})
    assert audit.verify_chain() is True
    assert [e["seq"] for e in audit.events()] == [1, 2]


def test_audit_tamper_detected():
    audit = audit_mod.AuditLog()
    audit.record("planner", "question_received", {"q": "beds?"})
    audit._events[0]["details"]["q"] = "tampered"
    assert audit.verify_chain() is False


# -- feedback with PII scrubbing ---------------------------------------------------------

def test_feedback_up_ok():
    audit = audit_mod.AuditLog()
    event = feedback_mod.capture_feedback(
        audit, session_id="s1", turn_id="t1", rating="up")
    assert event["action"] == "feedback"
    assert event["details"]["correction"] == ""


def test_feedback_down_requires_correction():
    audit = audit_mod.AuditLog()
    with pytest.raises(feedback_mod.FeedbackError):
        feedback_mod.capture_feedback(
            audit, session_id="s1", turn_id="t1", rating="down",
            correction="   ")


def test_feedback_bad_rating_raises():
    audit = audit_mod.AuditLog()
    with pytest.raises(feedback_mod.FeedbackError):
        feedback_mod.capture_feedback(
            audit, session_id="s1", turn_id="t1", rating="maybe")


def test_feedback_pii_scrubbed():
    audit = audit_mod.AuditLog()
    event = feedback_mod.capture_feedback(
        audit, session_id="s1", turn_id="t1", rating="down",
        correction="nurse jane.doe@example.com called 415-555-0132, "
                   "SSN 123-45-6789 on file is wrong")
    text = event["details"]["correction"]
    assert "jane.doe@example.com" not in text
    assert "415-555-0132" not in text
    assert "123-45-6789" not in text
    assert "[REDACTED-EMAIL]" in text
    assert "[REDACTED-PHONE]" in text
    assert "[REDACTED-SSN]" in text


def test_feedback_list_filters():
    audit = audit_mod.AuditLog()
    audit.record("planner", "question_received", {"q": "x"})
    feedback_mod.capture_feedback(
        audit, session_id="s1", turn_id="t1", rating="up")
    assert len(feedback_mod.list_feedback(audit)) == 1


def test_feedback_chain_still_verifies():
    audit = audit_mod.AuditLog()
    feedback_mod.capture_feedback(
        audit, session_id="s1", turn_id="t1", rating="down",
        correction="wrong ward suggested")
    assert audit.verify_chain() is True


# -- orchestrator ------------------------------------------------------------------------

def test_ask_end_to_end(planner):
    result = planner.ask("bed occupancy by ward")
    assert result["refused"] is False
    assert result["answer"]
    assert result["row_counts"]["bed_occupancy"] == 6
    assert result["tier"] == "T3"  # North Medical at 97.5%


def test_clinical_question_refused(planner):
    result = planner.ask("what medication should I take for chest pain?")
    assert result["refused"] is True
    assert "can't provide clinical" in result["answer"]
    actions = [e["action"] for e in planner.audit.events()]
    assert actions == ["question_received", "clinical_refused"]


def test_clinical_refusal_leaks_no_data(planner):
    result = planner.ask("diagnose my symptoms please")
    assert result["refused"] is True
    assert "North Medical" not in result["answer"]
    assert result["citations"] == []


def test_multi_intent_fanout(planner):
    result = planner.ask("bed occupancy and ED boarder backlog")
    assert "bed_occupancy" in result["intents"]
    assert "boarder_backlog" in result["intents"]
    assert len(result["row_counts"]) >= 2


def test_reassignment_gate_minted(planner):
    result = planner.ask("reassign all ED boarders to ward beds")
    assert result["gate_token"] is not None
    assert result["gate_token"].startswith("GATE-")
    assert "confirmation" in result["answer"]


def test_override_gate_minted(planner):
    result = planner.ask(
        "I want to override the discharge for a blocked patient")
    assert result["gate_token"] is not None
    assert result["tier"] == "T3"


def test_bedmatch_in_answer(planner):
    result = planner.ask("show me the ED boarder backlog")
    assert result["bedmatch"] is not None
    assert result["bedmatch"]["assignments"]
    assert "Bed match" in result["answer"]


def test_unknown_question_fallback(planner):
    result = planner.ask("tell me about quantum cryptography")
    assert "couldn't map" in result["answer"]
    assert result["tier"] == "T1"


def test_demand_gap_in_answer(planner):
    planner.demand = demand_mod.LiveDemand()  # unconfigured
    result = planner.ask("ED boarder backlog and wait times")
    assert "gap" in result["answer"].lower()


# -- demo helpers --------------------------------------------------------------------------

def test_format_answer(planner):
    result = planner.ask("reassign all ED boarders to ward beds")
    text = demo_helpers.format_answer(result)
    assert "Sources:" in text
    assert "Awaiting human confirmation" in text


def test_pending_gates(planner):
    planner.ask("reassign all ED boarders to ward beds")
    pending = demo_helpers.pending_gates(planner.gate)
    assert len(pending) == 1
    assert pending[0]["action"] == "bed_reassignment"


def test_gate_action_payload_none(planner):
    assert demo_helpers.gate_action_payload(planner.gate, "GATE-9999") is None


def test_recent_audit_rows(planner):
    planner.ask("bed occupancy by ward")
    rows = demo_helpers.recent_audit_rows(planner.audit, n=5)
    assert rows
    assert set(rows[0]) == {"seq", "actor", "action", "hash"}
    assert len(rows[0]["hash"]) == 12


# ---------- review fixes (grokbot-portfolio, 2026-10-18) ----------

def test_load_term_uses_projected_occupancy():
    boarder = {"acuity": 4, "iso_required": 0, "complaint_category": "general"}
    ward = {"ward_type": "MEDICAL", "capabilities": "telemetry",
            "occupancy_pct": 50.0, "occupied_beds": 5, "total_beds": 10}
    _, first = bedmatch.score_candidate(boarder, ward, 0)
    _, later = bedmatch.score_candidate(boarder, ward, 4)
    assert first["load"] == 0.1 and later["load"] == 0.02


def test_capability_term_reflects_telemetry_need():
    cardiac = {"acuity": 3, "iso_required": 0, "complaint_category": "cardiac"}
    no_tele = {"ward_type": "SURGICAL", "capabilities": "", "occupancy_pct": 0.0}
    tele = {"ward_type": "SURGICAL", "capabilities": "telemetry", "occupancy_pct": 0.0}
    assert bedmatch.score_candidate(cardiac, no_tele)[1]["capability"] == 0.0
    assert bedmatch.score_candidate(cardiac, tele)[1]["capability"] == 0.3


def test_adult_boarders_never_placed_in_pediatrics(conn):
    result = bedmatch.match(conn)
    assert all(a["ward_type"] != "PEDS" for a in result["assignments"])


def test_scrubber_catches_parenthesised_phone_and_mrn():
    from hospops.feedback import PIIScrubber
    out = PIIScrubber().scrub("call (555) 123-4567 about MRN 00123456")
    assert "555" not in out and "00123456" not in out
    assert "[REDACTED-PHONE]" in out and "[REDACTED-MRN]" in out
