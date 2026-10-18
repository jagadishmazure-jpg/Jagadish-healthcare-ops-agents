"""User-feedback capture with PII scrubbing, wired into the audit chain.

A thumbs up/down plus a free-text correction is appended as a ``feedback``
audit event via :meth:`AuditLog.record`, so feedback inherits the chain's
tamper evidence and is verifiable with :meth:`AuditLog.verify_chain`
alongside every other event.

Free text is scrubbed for personal data (email, phone, SSN, MRN patterns) *before*
storage -- a correction like "patient jane@x.com called" must never land in
the log with the address intact.
"""

import re

ACTION = "feedback"
ACTOR = "user"
RATINGS = ("up", "down")


class FeedbackError(ValueError):
    """Raised when a feedback submission is malformed."""


class PIIScrubber:
    """Redact email, phone (incl. "(555) 123-4567"), SSN and MRN patterns."""

    PATTERNS = [
        (re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"),
         "[REDACTED-EMAIL]"),
        (re.compile(r"(?<!\w)(?:\+?1[-.\s]?)?(?:\(\d{3}\)\s?|\d{3}[-.\s]?)\d{3}[-.\s]?\d{4}\b"),
         "[REDACTED-PHONE]"),
        (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
         "[REDACTED-SSN]"),
        (re.compile(r"\bMRN[\s:#-]*\d{5,10}\b", re.I),
         "[REDACTED-MRN]"),
    ]

    def scrub(self, text):
        for pattern, replacement in self.PATTERNS:
            text = pattern.sub(replacement, text)
        return text


_SCRUBBER = PIIScrubber()


def _require_nonblank(name, value):
    if not isinstance(value, str) or not value.strip():
        raise FeedbackError(f"{name} must be a non-blank string")
    return value.strip()


def capture_feedback(audit, *, session_id, turn_id, rating, correction=""):
    """Append one feedback event to the audit chain; returns the event.

    ``rating`` must be ``"up"`` or ``"down"``. A ``"down"`` rating requires
    a non-blank ``correction`` describing what was wrong; an ``"up"`` rating
    may omit it. ``session_id`` / ``turn_id`` link the feedback to the
    conversation turn it refers to (supplied by the calling UI/orchestrator).
    The correction is PII-scrubbed before it is stored.
    """
    session_id = _require_nonblank("session_id", session_id)
    turn_id = _require_nonblank("turn_id", turn_id)
    if rating not in RATINGS:
        raise FeedbackError(
            f"rating must be one of {RATINGS}, got {rating!r}")
    correction = correction.strip() if isinstance(correction, str) else ""
    if rating == "down" and not correction:
        raise FeedbackError("a thumbs-down requires a non-blank correction")
    details = {
        "session_id": session_id,
        "turn_id": turn_id,
        "rating": rating,
        "correction": _SCRUBBER.scrub(correction),
    }
    return audit.record(ACTOR, ACTION, details)


def list_feedback(audit):
    """Return all feedback events currently in the chain, in order."""
    return [e for e in audit.events() if e["action"] == ACTION]
