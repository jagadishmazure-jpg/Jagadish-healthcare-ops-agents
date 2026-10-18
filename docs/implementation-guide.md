# Implementation guide

How to take this repo from synthetic demo to a real hospital pilot.

## 1. Replace the data layer, keep the contracts

- Swap `hospital.seed_demo_data` for connectors to the ADT feed (admissions /
  discharges / transfers), the bed board, and the staffing system (HL7v2 /
  FHIR in production). Keep the four view contracts (`v_bed_occupancy`,
  `v_ed_boarders`, `v_discharge_ready`, `v_staffing_gaps`) -- every downstream
  module only reads those.
- Keep the SELECT-only gateway; point it at a read replica, never the
  system of record.

## 2. Harden the differentiators

- **Bed matcher:** calibrate `WEIGHTS` against historical placements with the
  bed-management team; add ward-specific hard constraints (e.g. negative
  pressure rooms) as capability tags.
- **Discharge gates:** bind checklist items to real workflow events (pharmacy
  system, scheduling system) instead of manual status flips.

## 3. Identity and privacy

- Replace fictional `PT-####` refs with keyed HMAC pseudonyms (key from env,
  rotation documented). Extend `PIIScrubber` with MRN patterns and run it on
  every free-text field, including SOP excerpts shown in the UI.
- Audit log goes to append-only storage (WORM) with external timestamping.

## 4. Evaluation before go-live

- Build a golden set of historical flow questions with bed-manager-approved
  answers; gate releases on it in CI.
- Run the planner shadow-mode (recommendations logged, humans decide) for
  4-6 weeks; measure override rate and time-to-bed before enabling any
  automated assignment.

## 5. What stays human

Bed reassignments affecting more than 5 patients and every discharge
override stay behind confirmation gates permanently. The planner proposes;
humans dispose.
