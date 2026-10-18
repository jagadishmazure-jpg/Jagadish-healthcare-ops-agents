# SOP-04: Escalation Policy (Lakeside General Hospital)

## Tier definitions

- **T4 -- critical:** an acuity-1 boarder waiting over 8 hours, or any ward
  above 98% occupancy. Page the administrator on call immediately.
- **T3 -- serious:** any ward above 95% occupancy, more than 10 boarders, or
  a discharge override requested. Notify the house supervisor and bed manager.
- **T2 -- watch:** any boarders, any staffing shortfall, or any blocked
  discharge. Bed manager monitors; no page required.
- **T1 -- normal:** no boarders, no shortfalls, no blocked discharges.

## Confirmation gates

- Bed reassignments affecting more than 5 patients: gated.
- Every discharge override: gated, regardless of count.
- A gate token is minted by the planner; a human approves or denies it in
  the operations dashboard. Nothing executes while a token is pending.

## Command center

T3 sustained for 4 hours, or any T4, activates the hospital command center
until the tier drops back to T2 for two consecutive checks.
