"""Gateway bypasses found in review: each used to read a base table or the schema.

Before the SQLite-authorizer backstop, check() only inspected names that
followed FROM/JOIN plus whitespace, so these three queries passed it.
"""
import pytest

from hospops import views
from hospops.views import SecurityError

BASE = next(iter(views.VIEW_SQL))


@pytest.fixture()
def conn():
    import sqlite3
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    views.bootstrap(c)
    yield c
    c.close()


@pytest.mark.parametrize("sql", [
    f"SELECT * FROM {BASE}, sqlite_master",
    f'SELECT name FROM {BASE} JOIN "sqlite_master"',
    "SELECT name FROM/**/sqlite_master",
])
def test_bypass_is_denied_with_rule_id(conn, sql):
    with pytest.raises(SecurityError) as err:
        views.run_query(conn, sql)
    assert err.value.rule_id == "SEC-VIEW-03"


def test_views_still_readable_and_capped(conn):
    rows = views.run_query(conn, f"SELECT * FROM {BASE} LIMIT 100000")
    assert len(rows) <= views.MAX_LIMIT


def test_authorizer_is_removed_after_the_query(conn):
    views.run_query(conn, f"SELECT * FROM {BASE}")
    assert conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] > 0
