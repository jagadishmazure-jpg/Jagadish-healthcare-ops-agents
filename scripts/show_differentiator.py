"""Print the domain differentiator on the seeded data (rendered into README, checked in CI)."""
from hospops import bedmatch, hospital, views
from hospops.discharge import DischargeGate
from hospops.feedback import PIIScrubber
from hospops.views import SecurityError

conn = hospital.connect()
views.bootstrap(conn)
result = bedmatch.match(conn)
print(f"bed matcher: {len(result['assignments'])} assigned, {len(result['escalated'])} escalated")
for a in result["assignments"][:3]:
    print(f"  {a['boarder']} (acuity {a['acuity']}) -> {a['bed_id']} [{a['ward_type']}] "
          f"score {a['score']} {a['breakdown']}")
for e in result["escalated"]:
    print(f"  escalated {e['boarder']} (acuity {e['acuity']}): {e['reason']}")
gate = DischargeGate(conn)
print(f"discharge gate ST-1040: ready={gate.is_ready('ST-1040')} blocked_by={gate.blocked_by('ST-1040')}")
print("scrubbed:", PIIScrubber().scrub("nurse jane.doe@example.com called (415) 555-0132 about MRN 00123456"))
try:
    views.run_query(conn, "SELECT * FROM v_bed_occupancy, beds")
except SecurityError as exc:
    print("gateway:", exc)
