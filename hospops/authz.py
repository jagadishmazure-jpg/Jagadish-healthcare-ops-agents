"""Engine-level backstop for the SELECT-only gateway (SQLite authorizer).

The regex gateway rejects writes and multi-statements, but text scanning
cannot see every way SQLite names a table: comma joins, ``/**/`` comments
in place of whitespace, and double-quoted identifiers all reach base tables
or ``sqlite_master`` without a ``FROM <name>`` token for a regex to match.

While a gateway query runs, SQLite itself is asked to authorize every
access. Reads are allowed only on the allowlisted views, or on base-table
columns that SQLite reads *on behalf of* one of those views. Everything
else (base tables named directly, the schema table, pragmas, writes) is
denied before the statement executes.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager

_SQLITE_RECURSIVE = getattr(sqlite3, "SQLITE_RECURSIVE", 33)
_ALWAYS_OK = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION, _SQLITE_RECURSIVE}


@contextmanager
def views_only(conn: sqlite3.Connection, allowed_views):
    """Authorize only reads of ``allowed_views`` for the duration of the block.

    Yields a list that collects the names of denied objects, so the caller
    can raise an error that cites them.
    """
    allowed = {v.lower() for v in allowed_views}
    denied: list[str] = []

    def _authorize(action, arg1, arg2, _db, source):
        if action == sqlite3.SQLITE_READ:
            if (arg1 or "").lower() in allowed or (source or "").lower() in allowed:
                return sqlite3.SQLITE_OK
            denied.append(arg1 or "?")
            return sqlite3.SQLITE_DENY
        if action in _ALWAYS_OK:
            return sqlite3.SQLITE_OK
        denied.append(arg1 or arg2 or f"action {action}")
        return sqlite3.SQLITE_DENY

    conn.set_authorizer(_authorize)
    try:
        yield denied
    finally:
        conn.set_authorizer(None)
