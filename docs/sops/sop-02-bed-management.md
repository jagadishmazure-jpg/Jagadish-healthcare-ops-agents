# SOP-02: Bed Management (Lakeside General Hospital)

## Assignment priority

Free beds are assigned in this order:

1. Highest acuity first (acuity 1 before acuity 5).
2. Within the same acuity, longest wait first.
3. Among eligible wards, the weighted score decides: 50% acuity fit,
   30% capability match, 20% ward headroom.

## Capability rules

- Patients flagged isolation-required go only to beds in wards tagged `iso`.
- Acuity 1 goes to ICU only. Acuity 2 goes to ICU or stepdown.
- Never fill a ward past 98% -- keep headroom for incoming trauma.

## Occupancy thresholds

- A ward above 95% occupancy triggers a **T3 escalation**; above 98% a **T4**.
- Bed reassignments affecting more than 5 patients need human confirmation
  through the confirmation gate before execution.

## Bed turnaround

Environmental services has a 45-minute target from discharge to bed-ready.
The bed board must reflect real bed state; stale "available" flags are treated
as a data-quality incident.
