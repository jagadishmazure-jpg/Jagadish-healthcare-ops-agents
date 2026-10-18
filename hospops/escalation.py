"""Escalation tiers (T1-T4) and human confirmation gates.

Tiers mirror docs/sops/sop-04-escalation-policy.md. High-impact actions --
bed reassignments affecting more than REASSIGN_GATE_THRESHOLD patients, and
every discharge override -- are gated: the planner mints a gate token and a
human must approve/deny before anything executes.
"""

REASSIGN_GATE_THRESHOLD = 5


def assess_tier(boarders=(), occupancy=(), staffing_gaps=(),
               blocked_discharges=(), override_requested=False):
    """Worst-case tier across boarding, occupancy, staffing, and discharges."""
    if any(b.get("acuity") == 1 and (b.get("wait_hours") or 0) > 8
           for b in boarders):
        return "T4"
    if any((o.get("occupancy_pct") or 0) > 98 for o in occupancy):
        return "T4"
    if any((o.get("occupancy_pct") or 0) > 95 for o in occupancy):
        return "T3"
    if len(list(boarders)) > 10:
        return "T3"
    if override_requested:
        return "T3"
    if boarders or staffing_gaps or blocked_discharges:
        return "T2"
    return "T1"


class ConfirmationGate:
    """In-memory approve/deny ledger for high-impact actions."""

    def __init__(self):
        self._pending = {}
        self._decided = {}
        self._seq = 0

    @staticmethod
    def requires_gate(action_type, patients_affected=0):
        if action_type == "bed_reassignment":
            return patients_affected > REASSIGN_GATE_THRESHOLD
        if action_type == "discharge_override":
            return True
        return False

    def request(self, action_type, summary, payload=None):
        self._seq += 1
        token = f"GATE-{self._seq:04d}"
        self._pending[token] = {
            "token": token,
            "action": action_type,
            "summary": summary,
            "payload": payload or {},
            "status": "pending",
        }
        return token

    def approve(self, token):
        return self._decide(token, "approved")

    def deny(self, token, reason=""):
        return self._decide(token, "denied", reason)

    def _decide(self, token, status, reason=""):
        if token not in self._pending:
            raise KeyError(f"unknown or already-decided gate token: {token}")
        record = self._pending.pop(token)
        record["status"] = status
        record["reason"] = reason
        self._decided[token] = record
        return record

    def pending(self):
        return [dict(v) for v in self._pending.values()]

    def decided(self):
        return [dict(v) for v in self._decided.values()]

    def get(self, token):
        if token in self._pending:
            return dict(self._pending[token])
        return dict(self._decided.get(token, {})) or None
