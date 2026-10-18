"""Acuity-weighted bed-assignment engine (domain differentiator).

ED boarders are matched to free ward beds by a transparent weighted score:

    score = 0.50 * acuity_fit + 0.30 * capability_match + 0.20 * (1 - occupancy)

- acuity_fit: how appropriate the ward type is for the triage acuity
  (ESI-style 1 = most urgent .. 5 = least). Hard rule: acuity 1 goes to ICU
  only; isolation-required patients go to isolation-capable beds only.
- capability_match: share of the boarder's monitoring needs the ward meets
  (cardiac and neuro complaints need ``telemetry``; other complaints have no
  extra need and score 1.0).
- load: projected headroom *after* the beds already assigned in this run,
  so successive boarders spread across wards instead of piling into the
  first good one until it is full.

Pediatric beds are not offered to these boarders: the synthetic ED feed has
no age field, so every boarder is treated as an adult.

Every assignment carries its score breakdown, so a bed manager can see *why*
a bed was chosen. Boarders with no eligible bed are escalated, never silently
dropped. Matching is operational only -- it never changes clinical priority.
"""

from . import views

# Ward types considered appropriate per acuity, with fit weights.
ACUITY_WARD_FIT = {
    1: {"ICU": 1.0},
    2: {"ICU": 1.0, "STEPDOWN": 0.8},
    3: {"STEPDOWN": 1.0, "MEDICAL": 0.9, "SURGICAL": 0.9},
    4: {"MEDICAL": 1.0, "SURGICAL": 1.0, "STEPDOWN": 0.7},
    5: {"MEDICAL": 1.0, "SURGICAL": 0.7},
}

# Monitoring a complaint needs from the ward (capability tags in wards.capabilities).
COMPLAINT_NEEDS = {"cardiac": {"telemetry"}, "neuro": {"telemetry"}}

WEIGHTS = {"acuity_fit": 0.50, "capability": 0.30, "load": 0.20}


def _capabilities(ward_row):
    caps = (ward_row.get("capabilities") or "")
    return {c.strip() for c in caps.split(",") if c.strip()}


def score_candidate(boarder, ward_row, assigned_in_run=0):
    """Return (score, breakdown) or None when the ward is ineligible.

    ``assigned_in_run`` is how many beds this matching run has already given
    out in the ward; the load term uses the projected occupancy.
    """
    fit = ACUITY_WARD_FIT.get(boarder["acuity"], {}).get(ward_row["ward_type"], 0.0)
    if fit <= 0:
        return None
    if boarder.get("iso_required") and "iso" not in _capabilities(ward_row):
        return None
    needs = COMPLAINT_NEEDS.get(boarder.get("complaint_category"), set())
    capability = (len(needs & _capabilities(ward_row)) / len(needs)) if needs else 1.0
    if ward_row.get("total_beds"):
        occupied = (ward_row.get("occupied_beds") or 0) + assigned_in_run
        load = max(0.0, 1.0 - occupied / ward_row["total_beds"])
    else:
        load = 1.0 - (ward_row["occupancy_pct"] or 0.0) / 100.0
    parts = {
        "acuity_fit": round(WEIGHTS["acuity_fit"] * fit, 4),
        "capability": round(WEIGHTS["capability"] * capability, 4),
        "load": round(WEIGHTS["load"] * load, 4),
    }
    parts["total"] = round(sum(parts.values()), 4)
    return parts["total"], parts


def _free_beds(conn):
    rows = conn.execute(
        "SELECT bed_id, ward_id FROM beds WHERE occupied_by IS NULL"
    ).fetchall()
    return [dict(r) for r in rows]


def _boarders(conn):
    return views.run_query(
        conn,
        "SELECT arrival_id, patient_ref, acuity, complaint_category, "
        "wait_hours, iso_required FROM v_ed_boarders "
        "ORDER BY acuity ASC, wait_hours DESC",
    )


def _occupancy(conn):
    rows = views.run_query(
        conn,
        "SELECT ward_id, ward_type, capabilities, occupancy_pct, "
        "available_beds, occupied_beds, total_beds FROM v_bed_occupancy",
    )
    return {r["ward_id"]: r for r in rows}


def match(conn, max_assignments=None):
    """Match boarders to free beds.

    Returns {"assignments": [...], "escalated": [...]}. Each assignment holds
    the boarder, bed, ward, score and the transparent breakdown. Boarders with
    no eligible bed land in "escalated" with a reason -- never dropped.
    Deterministic for a fixed database state.
    """
    occupancy = _occupancy(conn)
    free = _free_beds(conn)
    taken = set()
    ward_taken = {}
    assignments, escalated = [], []

    for boarder in _boarders(conn):
        if max_assignments is not None and len(assignments) >= max_assignments:
            break
        best = None
        for bed in free:
            if bed["bed_id"] in taken:
                continue
            ward = occupancy[bed["ward_id"]]
            remaining = ward["available_beds"] - ward_taken.get(bed["ward_id"], 0)
            if remaining <= 0:
                continue
            scored = score_candidate(boarder, ward, ward_taken.get(bed["ward_id"], 0))
            if scored is None:
                continue
            total, parts = scored
            if best is None or total > best["score"]:
                best = {
                    "boarder": boarder["arrival_id"],
                    "patient_ref": boarder["patient_ref"],
                    "acuity": boarder["acuity"],
                    "bed_id": bed["bed_id"],
                    "ward_id": bed["ward_id"],
                    "ward_type": ward["ward_type"],
                    "score": total,
                    "breakdown": parts,
                }
        if best is None:
            escalated.append({
                "boarder": boarder["arrival_id"],
                "patient_ref": boarder["patient_ref"],
                "acuity": boarder["acuity"],
                "reason": "no eligible free bed (acuity/capability constraints)",
            })
        else:
            taken.add(best["bed_id"])
            ward_taken[best["ward_id"]] = ward_taken.get(best["ward_id"], 0) + 1
            assignments.append(best)
    return {"assignments": assignments, "escalated": escalated}
