"""Least-privilege gateway over the hospital telemetry database.

The planner may only issue SELECT statements against four read-only views.
Every denial cites a stable rule ID:

- SEC-SELECT-01: statement is not a SELECT (or smuggles extra statements).
- SEC-WRITE-02:  SELECT text contains a write keyword.
- SEC-VIEW-03:   query references an identifier outside the view allowlist.

String literals are stripped before scanning so that a quoted word like
'update' inside a value can never false-positive. Result sets are auto-capped
at MAX_LIMIT rows.
"""

import re
import sqlite3

from . import authz
from . import hospital as hosp

MAX_LIMIT = 100

VIEW_SQL = {
    "v_bed_occupancy": """
        SELECT w.ward_id, w.ward_name, w.ward_type, w.total_beds,
               w.capabilities,
               SUM(CASE WHEN b.occupied_by IS NOT NULL THEN 1 ELSE 0 END)
                   AS occupied_beds,
               SUM(CASE WHEN b.occupied_by IS NULL THEN 1 ELSE 0 END)
                   AS available_beds,
               ROUND(100.0 * SUM(CASE WHEN b.occupied_by IS NOT NULL
                                     THEN 1 ELSE 0 END) / w.total_beds, 1)
                   AS occupancy_pct
        FROM wards w
        JOIN beds b ON b.ward_id = w.ward_id
        GROUP BY w.ward_id
    """,
    "v_ed_boarders": """
        SELECT arrival_id, patient_ref, acuity, complaint_category,
               wait_hours, arrival_day, iso_required
        FROM ed_arrivals
        WHERE status = 'boarded'
    """,
    "v_discharge_ready": """
        SELECT i.stay_id, i.patient_ref, i.ward_id, w.ward_name, i.bed_id,
               SUM(CASE WHEN c.status = 'pass' THEN 1 ELSE 0 END)
                   AS items_passed,
               COUNT(*) AS items_total,
               GROUP_CONCAT(CASE WHEN c.status != 'pass' THEN c.item END)
                   AS blocked_by,
               CASE WHEN SUM(CASE WHEN c.status != 'pass' THEN 1 ELSE 0 END) = 0
                    THEN 1 ELSE 0 END AS ready
        FROM inpatients i
        JOIN wards w ON w.ward_id = i.ward_id
        JOIN discharge_checklist c ON c.stay_id = i.stay_id
        GROUP BY i.stay_id
    """,
    "v_staffing_gaps": """
        SELECT s.ward_id, w.ward_name, s.day, s.shift,
               s.nurses_scheduled, s.nurses_required,
               (s.nurses_required - s.nurses_scheduled) AS gap
        FROM staffing s
        JOIN wards w ON w.ward_id = s.ward_id
        WHERE s.nurses_scheduled < s.nurses_required
    """,
}

_WRITE_KEYWORDS = {
    "insert", "update", "delete", "drop", "alter", "create",
    "truncate", "attach", "detach", "vacuum", "pragma", "grant", "revoke",
}
_WRITE_PHRASES = ("replace into",)

_LITERAL_RE = re.compile(r"'(?:[^']|'')*'")
_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?(?:\*/|$)", re.S)
_TOKEN_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")
_FROM_RE = re.compile(r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE)
_LIMIT_RE = re.compile(r"\blimit\s+(\d+)\s*;?\s*$", re.IGNORECASE)


class SecurityError(Exception):
    def __init__(self, rule_id, reason):
        super().__init__(f"[{rule_id}] {reason}")
        self.rule_id = rule_id
        self.reason = reason


def install_views(conn):
    for name, sql in VIEW_SQL.items():
        conn.execute(f"DROP VIEW IF EXISTS {name}")
        conn.execute(f"CREATE VIEW {name} AS {sql}")
    conn.commit()


def _strip_noise(sql):
    """Remove string literals and -- or /* */ comments before security scanning."""
    return _COMMENT_RE.sub(" ", _LITERAL_RE.sub(" ", sql))


def check(sql):
    """Raise SecurityError (with cited rule ID) if the SQL is not allowed."""
    cleaned = _strip_noise(sql)
    if ";" in cleaned:
        raise SecurityError("SEC-SELECT-01", "multiple statements are not allowed")
    head = cleaned.lstrip(" \t\n\r(").lower()
    if not head.startswith("select"):
        raise SecurityError("SEC-SELECT-01", "only SELECT statements are permitted")
    tokens = {t.lower() for t in _TOKEN_RE.findall(cleaned)}
    lowered = cleaned.lower()
    if tokens & _WRITE_KEYWORDS or any(p in lowered for p in _WRITE_PHRASES):
        raise SecurityError("SEC-WRITE-02", "write operations are denied on telemetry views")
    for ident in _FROM_RE.findall(cleaned):
        if ident.lower() not in VIEW_SQL:
            raise SecurityError(
                "SEC-VIEW-03",
                f"identifier '{ident}' is not an exposed read-only view",
            )
    return True


def _cap_limit(sql):
    sql = sql.strip().rstrip(";")
    match = _LIMIT_RE.search(sql)
    if match:
        if int(match.group(1)) > MAX_LIMIT:
            sql = _LIMIT_RE.sub(f"LIMIT {MAX_LIMIT}", sql)
        return sql
    return f"{sql} LIMIT {MAX_LIMIT}"


def run_query(conn, sql, params=()):
    """Security-checked SELECT against the allowlisted views. Returns row dicts."""
    check(sql)
    sql = _cap_limit(sql)
    # Engine-level backstop: text scanning misses comma joins, /**/ and
    # double-quoted names, so SQLite itself refuses any non-view read.
    with authz.views_only(conn, VIEW_SQL) as denied:
        try:
            cursor = conn.execute(sql, params)
        except sqlite3.DatabaseError:
            if denied:
                raise SecurityError(
                    "SEC-VIEW-03",
                    f"identifier '{denied[0]}' is not an exposed read-only view",
                ) from None
            raise
        columns = [d[0] for d in cursor.description]
        rows = cursor.fetchmany(MAX_LIMIT)
    return [dict(zip(columns, row)) for row in rows]


def bootstrap(conn, seed=42):
    """Convenience: seed data + install views. Used by the demo and tests."""
    hosp.seed_demo_data(conn, seed=seed)
    install_views(conn)
    return conn
