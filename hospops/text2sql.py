"""Rule-based NL -> SQL over the read-only hospital views.

Five planner intents, each mapped to a fixed parameterized SELECT template over
the view allowlist. The gateway in views.py enforces SELECT-only, identifier
allowlist, and the LIMIT cap, so generated SQL can never escape the sandbox.

On a database error the planner retries once with a simplified query
(``SELECT * FROM <view> LIMIT 20``) before raising EscalationNeeded.
"""

import re
import sqlite3

from . import views

OCCUPANCY_SQL = (
    "SELECT ward_id, ward_name, ward_type, occupancy_pct, available_beds, "
    "total_beds FROM v_bed_occupancy ORDER BY occupancy_pct DESC LIMIT 50"
)
WAIT_SQL = (
    "SELECT acuity, COUNT(*) AS boarders, ROUND(AVG(wait_hours), 1) AS "
    "avg_wait_hours, MAX(wait_hours) AS max_wait_hours FROM v_ed_boarders "
    "GROUP BY acuity ORDER BY acuity ASC LIMIT 50"
)
DISCHARGE_SQL = (
    "SELECT stay_id, patient_ref, ward_name, items_passed, items_total, "
    "blocked_by, ready FROM v_discharge_ready "
    "ORDER BY ready DESC, items_passed DESC LIMIT 50"
)
STAFFING_SQL = (
    "SELECT ward_name, day, shift, nurses_scheduled, nurses_required, gap "
    "FROM v_staffing_gaps ORDER BY gap DESC LIMIT 50"
)
BOARDER_SQL = (
    "SELECT arrival_id, patient_ref, acuity, complaint_category, wait_hours, "
    "iso_required FROM v_ed_boarders "
    "ORDER BY acuity ASC, wait_hours DESC LIMIT 50"
)

_TEMPLATES = {
    "bed_occupancy": ("v_bed_occupancy", OCCUPANCY_SQL),
    "ed_wait_times": ("v_ed_boarders", WAIT_SQL),
    "discharge_ready": ("v_discharge_ready", DISCHARGE_SQL),
    "staffing_gaps": ("v_staffing_gaps", STAFFING_SQL),
    "boarder_backlog": ("v_ed_boarders", BOARDER_SQL),
}

_INTENT_PATTERNS = [
    ("bed_occupancy", re.compile(r"occup|capacity|\bfull\b|available beds|\bbed\b.*\bward\b", re.I)),
    ("ed_wait_times", re.compile(r"wait.*time|waiting|average.*wait|how long.*wait", re.I)),
    ("discharge_ready", re.compile(r"discharg", re.I)),
    ("staffing_gaps", re.compile(r"staff|nurs|understaff|shortage|shift", re.I)),
    ("boarder_backlog", re.compile(r"\bboard|\bbacklog\b|crowd", re.I)),
]


class UnknownIntent(Exception):
    """The question matched no governed intent."""


class EscalationNeeded(Exception):
    """Even the simplified retry failed; a human must take over."""


def detect_intents(question):
    """Return every matching intent name, in priority order (fan-out)."""
    return [name for name, pattern in _INTENT_PATTERNS if pattern.search(question)]


def build_sql(intent):
    """Return (sql, params) for an intent. Raises UnknownIntent for bad names."""
    if intent not in _TEMPLATES:
        raise UnknownIntent(intent)
    _view, sql = _TEMPLATES[intent]
    return sql, ()


def run(conn, intent):
    """Execute one intent with a single simplified-query retry on DB errors."""
    view, sql = _TEMPLATES[intent]
    try:
        rows = views.run_query(conn, sql)
    except sqlite3.Error:
        try:
            rows = views.run_query(conn, f"SELECT * FROM {view} LIMIT 20")
            sql = f"SELECT * FROM {view} LIMIT 20  -- simplified retry"
        except sqlite3.Error as exc:
            raise EscalationNeeded(intent) from exc
    return {"intent": intent, "sql": sql, "rows": rows}


def run_question(conn, question):
    """Detect the first intent and run it. Raises UnknownIntent when nothing matches."""
    intents = detect_intents(question)
    if not intents:
        raise UnknownIntent(question)
    return run(conn, intents[0])
