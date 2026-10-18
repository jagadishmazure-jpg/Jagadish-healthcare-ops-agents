"""Synthetic hospital telemetry for Lakeside General Hospital (fictional).

Seeded, deterministic, and operational only: ED arrivals with triage acuity,
ward/bed inventory with capability tags, inpatients, discharge checklists,
and staffing shifts. Patient references are fictional IDs (PT-####); no names,
no real patient data. Nothing here is clinical advice -- see orchestrator.py.
"""

import random
import sqlite3

WARDS = [
    # ward_id, ward_name, ward_type, total_beds, capabilities
    ("W-MED-A", "North Medical", "MEDICAL", 40, "telemetry"),
    ("W-MED-B", "South Medical", "MEDICAL", 36, "telemetry,iso"),
    ("W-SURG", "Surgical Unit", "SURGICAL", 30, "telemetry"),
    ("W-ICU", "Intensive Care", "ICU", 16, "telemetry,iso,vent"),
    ("W-STEP", "Stepdown Unit", "STEPDOWN", 20, "telemetry"),
    ("W-PEDS", "Pediatrics", "PEDS", 18, ""),
]

CHECKLIST_ITEMS = [
    "meds_reconciled",
    "followup_scheduled",
    "discharge_summary_signed",
    "transport_arranged",
    "home_care_confirmed",
]

COMPLAINTS = ["cardiac", "respiratory", "trauma", "neuro", "abdominal", "general"]

SCHEMA = """
CREATE TABLE wards (
    ward_id TEXT PRIMARY KEY,
    ward_name TEXT NOT NULL,
    ward_type TEXT NOT NULL,
    total_beds INTEGER NOT NULL,
    capabilities TEXT NOT NULL DEFAULT ''
);
CREATE TABLE beds (
    bed_id TEXT PRIMARY KEY,
    ward_id TEXT NOT NULL REFERENCES wards(ward_id),
    occupied_by TEXT
);
CREATE TABLE ed_arrivals (
    arrival_id TEXT PRIMARY KEY,
    patient_ref TEXT NOT NULL,
    acuity INTEGER NOT NULL CHECK (acuity BETWEEN 1 AND 5),
    complaint_category TEXT NOT NULL,
    arrival_day INTEGER NOT NULL,
    wait_hours REAL NOT NULL,
    iso_required INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (status IN ('boarded','admitted','discharged'))
);
CREATE TABLE inpatients (
    stay_id TEXT PRIMARY KEY,
    patient_ref TEXT NOT NULL,
    ward_id TEXT NOT NULL REFERENCES wards(ward_id),
    bed_id TEXT NOT NULL REFERENCES beds(bed_id),
    acuity INTEGER NOT NULL,
    admit_day INTEGER NOT NULL
);
CREATE TABLE discharge_checklist (
    stay_id TEXT NOT NULL REFERENCES inpatients(stay_id),
    item TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pass','pending','fail')),
    PRIMARY KEY (stay_id, item)
);
CREATE TABLE staffing (
    ward_id TEXT NOT NULL REFERENCES wards(ward_id),
    day INTEGER NOT NULL,
    shift TEXT NOT NULL CHECK (shift IN ('DAY','NIGHT')),
    nurses_scheduled INTEGER NOT NULL,
    nurses_required INTEGER NOT NULL,
    PRIMARY KEY (ward_id, day, shift)
);
"""


def connect(db_path=":memory:"):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def seed_demo_data(conn, seed=42):
    """Create schema and load deterministic synthetic telemetry.

    Anchors (stable across seeds by construction):
    - W-MED-A runs at 39/40 beds occupied (97.5%).
    - One discharge-ready inpatient is blocked by a pending checklist item.
    - One acuity-1 ED boarder has waited 14 hours (T4 trigger).
    - W-MED-A night shift on day 90 is 3 nurses short.
    """
    rng = random.Random(seed)
    conn.executescript(SCHEMA)

    for ward_id, name, wtype, beds, caps in WARDS:
        conn.execute(
            "INSERT INTO wards VALUES (?,?,?,?,?)",
            (ward_id, name, wtype, beds, caps),
        )
        for n in range(1, beds + 1):
            conn.execute(
                "INSERT INTO beds (bed_id, ward_id, occupied_by) VALUES (?,?,NULL)",
                (f"{ward_id}-{n:02d}", ward_id),
            )

    # -- ED arrivals -------------------------------------------------------
    statuses = (["boarded"] * 12 + ["admitted"] * 10 + ["discharged"] * 6)
    rng.shuffle(statuses)
    for i in range(1, 29):
        acuity = rng.choice([1, 2, 2, 3, 3, 3, 4, 4, 5, 5])
        status = statuses[i - 1]
        wait = round(rng.uniform(0.5, 18.0), 1) if status == "boarded" else 0.0
        conn.execute(
            """INSERT INTO ed_arrivals
               (arrival_id, patient_ref, acuity, complaint_category,
                arrival_day, wait_hours, iso_required, status)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                f"ED-{i:04d}", f"PT-{i:04d}", acuity,
                rng.choice(COMPLAINTS), rng.randint(85, 90), wait,
                1 if rng.random() < 0.15 else 0, status,
            ),
        )
    # Anchor: acuity-1 boarder waiting 14h.
    conn.execute(
        """UPDATE ed_arrivals SET acuity=1, wait_hours=14.0, status='boarded',
                  complaint_category='cardiac', iso_required=0
           WHERE arrival_id='ED-0007'
        """,
    )

    # -- Inpatients: fill beds ---------------------------------------------
    stay_seq = 1001
    first_medb_stay = None
    for ward_id, _name, _wtype, total_beds, _caps in WARDS:
        if ward_id == "W-MED-A":
            occupied = 39  # anchor: 97.5% occupancy
        else:
            occupied = int(total_beds * rng.uniform(0.68, 0.86))
        bed_rows = conn.execute(
            "SELECT bed_id FROM beds WHERE ward_id=? ORDER BY bed_id",
            (ward_id,),
        ).fetchall()
        for bed in bed_rows[:occupied]:
            patient_ref = f"PT-{stay_seq:04d}"
            conn.execute(
                "UPDATE beds SET occupied_by=? WHERE bed_id=?",
                (patient_ref, bed["bed_id"]),
            )
            conn.execute(
                """INSERT INTO inpatients
                   (stay_id, patient_ref, ward_id, bed_id, acuity, admit_day)
                   VALUES (?,?,?,?,?,?)""",
                (
                    f"ST-{stay_seq}", patient_ref, ward_id, bed["bed_id"],
                    rng.randint(2, 5), rng.randint(82, 89),
                ),
            )
            if ward_id == "W-MED-B" and first_medb_stay is None:
                first_medb_stay = f"ST-{stay_seq}"
            ready = rng.random() < 0.15
            for item in CHECKLIST_ITEMS:
                status = "pass" if (ready or rng.random() < 0.6) else "pending"
                conn.execute(
                    "INSERT INTO discharge_checklist VALUES (?,?,?)",
                    (f"ST-{stay_seq}", item, status),
                )
            stay_seq += 1

    # Anchor: one discharge-ready inpatient blocked by a single missing item.
    for item in CHECKLIST_ITEMS:
        conn.execute(
            "UPDATE discharge_checklist SET status=? WHERE stay_id=? AND item=?",
            ("pass" if item != "discharge_summary_signed" else "pending",
             first_medb_stay, item),
        )

    # -- Staffing ------------------------------------------------------------
    for ward_id, _name, _wtype, total_beds, _caps in WARDS:
        for day in (88, 89, 90):
            for shift in ("DAY", "NIGHT"):
                required = max(2, total_beds // (8 if shift == "DAY" else 12))
                scheduled = required + rng.randint(-2, 1)
                if ward_id == "W-MED-A" and day == 90 and shift == "NIGHT":
                    scheduled = required - 3  # anchor: 3-nurse shortfall
                conn.execute(
                    "INSERT INTO staffing VALUES (?,?,?,?,?)",
                    (ward_id, day, shift, max(0, scheduled), required),
                )
    conn.commit()
    return conn
