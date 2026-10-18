"""Strict, view-aware authorizer: template sync regression (2026-10-18).

These tests drive ``hospops.authz.views_only`` directly, so they hold
independent of the text gateway. The earlier version falsely denied a
self-join of a view (SQLite reports the unused side as a read with an
empty column name and no view name); that is fixed here, while a direct
base-table read (view-name argument None) stays denied.
"""

import sqlite3

import pytest

from hospops import authz, views

VIEWS = sorted(str(v) for v in views.VIEW_SQL)


@pytest.fixture()
def conn(tmp_path):
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    views.bootstrap(c)
    yield c
    c.close()


def _run(conn, sql, allowed=VIEWS):
    with authz.views_only(conn, allowed) as denied:
        try:
            rows = conn.execute(sql).fetchall()
        except sqlite3.DatabaseError:
            return None, list(denied)
    return rows, list(denied)


def _base_and_view(conn):
    behind = sorted(authz._tables_behind(conn, {v.lower() for v in VIEWS}))
    assert behind, "every repo publishes views over base tables"
    base = behind[0]
    for v in VIEWS:
        if base in authz._tables_behind(conn, {v.lower()}):
            return base, v
    raise AssertionError(base)


@pytest.mark.parametrize("view", VIEWS)
def test_view_self_join_not_falsely_denied(conn, view):
    rows, denied = _run(conn, f'SELECT a.* FROM {view} a, {view} b LIMIT 1')
    assert rows is not None and denied == [], denied


@pytest.mark.parametrize("view", VIEWS)
def test_every_view_readable(conn, view):
    rows, denied = _run(conn, f"SELECT * FROM {view} LIMIT 3")
    assert rows is not None and denied == []


def test_base_table_comma_join_with_view_denied(conn):
    base, view = _base_and_view(conn)
    for sql in (f"SELECT * FROM {base}, {view}",
                f'SELECT/**/*/**/FROM/**/"{base}"/**/,/**/{view}',
                f"SELECT {view}.* FROM {view} JOIN (SELECT * FROM main.[{base}]) x ON 1=1 LIMIT 1"):
        rows, denied = _run(conn, sql)
        assert rows is None and base in [d.lower() for d in denied], sql


def test_where_on_base_column_denied(conn):
    base, _ = _base_and_view(conn)
    col = conn.execute(f'PRAGMA table_info("{base}")').fetchone()[1]
    rows, denied = _run(conn, f'SELECT count(*) FROM {base} WHERE "{col}" IS NOT NULL')
    assert rows is None and denied


def test_count_star_residual_is_row_count_only(conn):
    # Documented residual: count(*) on a table behind a view returns a count, no column data.
    base, _ = _base_and_view(conn)
    # Same class: EXISTS (SELECT 1 FROM <table>) reads no columns. Any column use is denied.
    rows, denied = _run(conn, f"SELECT count(*) FROM {base}")
    assert rows is not None and isinstance(rows[0][0], int) and denied == []
    col = conn.execute(f'PRAGMA table_info("{base}")').fetchone()[1]
    rows, denied = _run(conn, f'SELECT 1 WHERE EXISTS (SELECT 1 FROM main.[{base}] WHERE "{col}" IS NOT NULL)')
    assert rows is None and base in [d.lower() for d in denied]


def test_sqlite_master_denied_even_if_allowed(conn):
    rows, denied = _run(conn, "SELECT name FROM sqlite_master", allowed=VIEWS + ["sqlite_master"])
    assert rows is None and "sqlite_master" in denied


def test_load_extension_denied(conn):
    rows, denied = _run(conn, "SELECT load_extension('x')")
    assert rows is None and "load_extension" in denied


def test_pragma_and_writes_denied(conn):
    for sql in ("PRAGMA table_info(sqlite_master)", f"DELETE FROM {_base_and_view(conn)[0]}"):
        rows, denied = _run(conn, sql)
        assert rows is None and denied, sql
