"""Stdlib-only helpers for the thin Streamlit demo.

ALL presentation logic the demo needs lives here so it stays unit-tested;
demo/app.py only wires widgets to these functions.
"""


def format_answer(result):
    """Render a planner result dict as plain text for the demo UI."""
    lines = [result.get("answer", "")]
    if result.get("citations"):
        lines.append("")
        lines.append("Sources:")
        for citation in result["citations"]:
            lines.append(f"  - {citation}")
    if result.get("gate_token"):
        lines.append("")
        lines.append(f"Awaiting human confirmation: {result['gate_token']}")
    return "\n".join(lines).strip()


def pending_gates(gate):
    """Summaries of gates awaiting a human decision."""
    return [
        {
            "token": g["token"],
            "action": g["action"],
            "summary": g["summary"],
            "status": g["status"],
        }
        for g in gate.pending()
    ]


def gate_action_payload(gate, token):
    """Full payload for one gate token (pending or decided), else None."""
    record = gate.get(token)
    if not record:
        return None
    return {
        "token": record["token"],
        "action": record["action"],
        "summary": record["summary"],
        "payload": record.get("payload", {}),
        "status": record["status"],
    }


def recent_audit_rows(audit_log, n=10):
    """Compact rows for the demo's audit viewer."""
    return [
        {
            "seq": e["seq"],
            "actor": e["actor"],
            "action": e["action"],
            "hash": e["hash"][:12],
        }
        for e in audit_log.recent(n)
    ]
