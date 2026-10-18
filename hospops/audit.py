"""SHA-256 hash-chained immutable audit log.

Every planner question, tool call, SQL statement issued, bed-assignment
recommendation, escalation decision, discharge override, and confirmation-gate
action is appended here. ``verify_chain()`` detects any later tampering with
the recorded history.
"""

import hashlib
import json
import time


class AuditLog:
    def __init__(self):
        self._events = []

    def record(self, actor, action, details=None):
        """Append one event; returns the event dict (including its hash)."""
        details = details or {}
        prev = self._events[-1]["hash"] if self._events else "GENESIS"
        event = {
            "seq": len(self._events) + 1,
            "ts": time.time(),
            "actor": actor,
            "action": action,
            "details": details,
            "prev_hash": prev,
        }
        event["hash"] = self._hash(event)
        self._events.append(event)
        return event

    @staticmethod
    def _hash(event):
        payload = json.dumps(
            {k: event[k] for k in ("seq", "ts", "actor", "action", "details", "prev_hash")},
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def verify_chain(self):
        """True iff every link's prev_hash matches and no event was altered."""
        prev = "GENESIS"
        for event in self._events:
            if event["prev_hash"] != prev:
                return False
            if event["hash"] != self._hash(event):
                return False
            prev = event["hash"]
        return True

    def events(self):
        return list(self._events)

    def recent(self, n=10):
        return list(self._events[-n:])
