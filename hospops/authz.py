"""Engine-level backstop for the SELECT-only gateway (SQLite authorizer).

The regex gateway rejects writes and multi-statements, but text scanning
cannot see every way SQLite names a table: comma joins, ``/**/`` comments
in place of whitespace, and double-quoted identifiers all reach base tables
or ``sqlite_master`` without a ``FROM <name>`` token for a regex to match.

While a gateway query runs, SQLite itself is asked to authorize every
access. Reads are allowed only on the allowlisted views, or on base-table
columns that SQLite reads *on behalf of* one of those views (the
authorizer's view-name argument names an allowed view). A base-table read
with no view name is a direct read and is denied. The schema table is
always denied, as are pragmas, writes and ``load_extension``. All of this
happens before the statement executes, independent of any text check.

One narrow exception: SQLite reports a read with an empty column name and
no view name for a table that supplies no columns (one side of a view
self-join, or ``count(*)``). No column data is returned, so it is allowed
only for tables that sit behind an allowed view. Residual: ``SELECT
count(*) FROM <such table>`` returns a row count.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager

_SQLITE_RECURSIVE = getattr(sqlite3, "SQLITE_RECURSIVE", 33)
_ALWAYS_OK = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION, _SQLITE_RECURSIVE}
_SCHEMA_TABLES = {"sqlite_master", "sqlite_schema", "sqlite_temp_master", "sqlite_temp_schema"}
_DENIED_FUNCTIONS = {"load_extension"}


def _tables_behind(conn, allowed):
    """Base tables that SQLite reads on behalf of the allowed views."""
    tables: set[str] = set()

    def _record(action, arg1, _arg2, _db, source):
        if action == sqlite3.SQLITE_READ and (source or "").lower() in allowed:
            tables.add((arg1 or "").lower())
        return sqlite3.SQLITE_OK

    conn.set_authorizer(_record)
    try:
        for view in allowed:
            try:
                conn.execute(f'EXPLAIN SELECT * FROM "{view}"').fetchall()
            except sqlite3.DatabaseError:
                pass  # view not installed on this connection
    finally:
        conn.set_authorizer(None)
    return tables - _SCHEMA_TABLES


@contextmanager
def views_only(conn: sqlite3.Connection, allowed_views):
    """Authorize only reads of ``allowed_views`` for the duration of the block.

    Yields a list that collects the names of denied objects, so the caller
    can raise an error that cites them.
    """
    allowed = {v.lower() for v in allowed_views} - _SCHEMA_TABLES
    underlying = _tables_behind(conn, allowed)
    denied: list[str] = []

    def _authorize(action, arg1, arg2, _db, source):
        if action == sqlite3.SQLITE_READ:
            table = (arg1 or "").lower()
            if table in _SCHEMA_TABLES:
                denied.append(arg1)
                return sqlite3.SQLITE_DENY
            if table in allowed or (source or "").lower() in allowed:
                return sqlite3.SQLITE_OK
            # SQLite reports a read with an empty column name (and no view
            # name) for a flattened view's table that supplies no columns,
            # e.g. one side of a view self-join. No column data is read, so
            # allow it for tables behind an allowed view.
            if not arg2 and table in underlying:
                return sqlite3.SQLITE_OK
            denied.append(arg1 or "?")
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in _DENIED_FUNCTIONS:
            denied.append(arg2)
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
