# ADR 0001: SQLite authorizer as the gateway's backstop

Status: accepted (2026-10-18, standardization review)

## Context
The SELECT-only gateway checked SQL text with regular expressions. Text
scanning cannot see every way SQLite names a table: comma joins, comments
used as whitespace and double-quoted identifiers all reached base tables or
`sqlite_master` in review. Adding more regexes chases spellings.

## Decision
Keep the regex rules (they give clear, early rule IDs for writes and
multi-statements) and add `hospops/authz.py`: while a gateway query runs,
`sqlite3.Connection.set_authorizer` allows `SELECT`, functions and
recursive CTEs, and allows `READ` only on the allowlisted views or on
base-table columns SQLite reads on behalf of one of those views. Everything
else is denied before the statement executes and surfaces as `SEC-VIEW-03`. The
row cap is enforced with `fetchmany`, not only by rewriting `LIMIT`.

## Consequences
- The allowlist holds regardless of how a table is spelled.
- Engine code that needs base tables (seeding, internal optimizers) keeps
  using the connection directly; only planner-facing SQL goes through the
  gateway.
- The authorizer is removed after each query (tested), so it never leaks
  into other code on the same connection.

## Revision 2026-10-18 (template sync): strict, view-aware authorizer

The authorizer now uses SQLite's view-name argument. A base-table read is
allowed only on behalf of an allowed view. A direct read, where the view name
is None, is denied. The schema tables and `load_extension` are always denied.
This fixes a false denial on view self-joins: SQLite reports the unused side
as a read with an empty column name and no view name. That read shape is
allowed only for tables behind an allowed view. Residual: a column-free
`count(*)` or `EXISTS` can return a row count, but no column data.
Regression tests are in `tests/test_authorizer_sync.py`.
