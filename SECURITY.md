# Security policy

## Scope

This repo is a synthetic-data demonstration. No real patient data is used,
stored, or transmitted.

## Reporting a vulnerability

Open a private security advisory on GitHub (Security tab) rather than a
public issue. Expect an initial response within 5 business days.

## Controls in this repo

- Read-only telemetry views with a SELECT-only gateway (rule IDs
  SEC-SELECT-01/02/03); writes are denied.
- Hash-chained audit log with tamper detection (`verify_chain`).
- PII scrubbing (email / phone / SSN patterns) on free-text feedback before
  storage.
- Explicit refusal of clinical questions (operational scope only).
- Confirmation gates for bed reassignments affecting >5 patients and for
  every discharge override.
