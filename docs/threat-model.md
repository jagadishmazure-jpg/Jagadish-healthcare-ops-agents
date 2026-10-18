# Threat model (STRIDE-lite)

Scope: the planner, its tools, and the demo UI. Synthetic data only.

## Spoofing

- A caller could impersonate a bed manager to approve gates. Mitigation:
  gate approvals must bind to an authenticated identity in production; the
  demo's in-memory gate is explicitly not production auth.

## Tampering

- Telemetry rows or audit events altered after the fact. Mitigation:
  hash-chained audit log (`verify_chain`); gateway denies all writes
  (SEC-WRITE-02); production audit goes to append-only storage.

## Repudiation

- "I never approved that override." Mitigation: every gate decision and
  override records approver, reason, timestamp, and chain hash.

## Information disclosure

- Patient data leaking through answers or feedback text. Mitigation:
  fictional IDs only; `PIIScrubber` redacts email/phone/SSN patterns before
  feedback is stored; error paths never echo raw row data; SOP excerpts are
  capped at 400 chars.

## Denial of service

- Expensive questions (full-table scans). Mitigation: LIMIT cap (100 rows),
  fixed intent templates instead of free-form SQL generation.

## Elevation of privilege

- Prompt injection asking the planner to skip gates or give clinical advice.
  Mitigation: clinical questions are refused by pattern before any tool runs;
  gate requirements are code, not prompt instructions, and cannot be talked
  away.

## Out of scope

Real PHI handling, production identity, and network security -- covered by
the hospital's own controls in a pilot (see implementation guide).
