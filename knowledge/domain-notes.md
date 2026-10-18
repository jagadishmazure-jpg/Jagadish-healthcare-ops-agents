# Hospital patient-flow operations: domain notes

Distilled operational knowledge behind this repo. Operational only -- no
clinical content. All examples use the fictional Lakeside General Hospital.

## Why patient flow is an ops problem

Hospitals rarely run out of *beds*; they run out of *the right bed at the
right time*. The failure modes are operational, not medical:

- **ED boarding:** admitted patients wait in the ED because no inpatient bed
  is assigned. Boarding correlates with worse throughput everywhere
  downstream, longer waits for new arrivals, and staff burnout.
- **Occupancy crises:** a ward above ~95% has no buffer for the next
  admission. One busy night cascades into diversions and cancelled electives.
- **Discharge delays:** a patient who is medically ready but missing a
  signature, a ride, or a follow-up appointment holds a bed someone else
  needs. Discharge planning is a checklist problem, and checklists fail
  silently unless someone names the missing item.

## The operational levers

1. **Triage acuity (ESI 1-5)** decides *where* a patient may go, never the
   clinical priority -- that belongs to clinicians.
2. **Bed matching** is a constrained assignment: acuity fit, capability
   (isolation, telemetry), and ward headroom. A transparent score beats a
   black box because the bed manager must defend the choice at 3am.
3. **Discharge checklists** turn "almost ready" into a named blocker. The
   override path exists because reality is messy, but it is gated and audited.
4. **Escalation tiers** convert continuous pressure (occupancy %, wait hours)
   into discrete, actionable states with named owners.
5. **Staffing** is the hidden constraint: a bed without a nurse is not a bed.

## What this repo deliberately excludes

Clinical decision support -- diagnosis, treatment, medication, symptom
guidance -- is a different discipline with different liability. The sibling
repo Jagadish-clinical-ops-agents covers that side; this repo refuses
clinical questions outright and stays in operations.
