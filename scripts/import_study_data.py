"""
Import roster + assessment data for the class trial.

Two functions, both keyed by study_id so logs link to ground truth:

  1. Roster import  — map usernames to study_ids (and cohort).
  2. Assessment import — load pre/post test + MAI items per study_id.

CSV formats
-----------
Roster CSV (columns):   username, study_id, cohort
Assessment CSV (cols):  study_id, instrument, item_id, bloom_tier, concept, score
    instrument ∈ {pretest, posttest, mai_pre, mai_post}
    bloom_tier ∈ {declarative, procedural, applied, ''}   ('' for MAI items)

Self-report CSV (cols):  username, dimension, item_id, score
    dimension ∈ {self_efficacy, interest, goal_orientation}

Usage
-----
  python scripts/import_study_data.py roster       path/to/roster.csv
  python scripts/import_study_data.py assessment   path/to/assessment.csv
  python scripts/import_study_data.py self_report  path/to/self_report.csv

Survey items (Tier 2 motivational sub-model) — Likert 1–5:
  self_efficacy:
    se1  "I am confident I can solve C programming problems on my own."
    se2  "Even when a C problem is hard, I believe I can figure it out."
    se3  "I can debug my own C code when it doesn't work."
  interest:
    in1  "I find C programming interesting."
    in2  "Learning to program feels useful/valuable to me."
  goal_orientation:  (higher = mastery-oriented, lower = performance-oriented)
    go1  "I redo problems to understand them, not just to pass."
    go2  "I care more about learning the concept than about the grade."
"""

import csv
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'backend'))

from app.db.sqlite_db import db
from app.core import telemetry
from datetime import datetime, timezone


def import_roster(csv_path: str):
    n = 0
    with open(csv_path, newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            username = row["username"].strip()
            study_id = row["study_id"].strip()
            cohort = row.get("cohort", "default").strip() or "default"
            # Upsert the explicit mapping (overrides any auto-generated id)
            db.execute(
                "INSERT OR REPLACE INTO study_participants (study_id, username, cohort, enrolled_at) "
                "VALUES (?, ?, ?, ?)",
                (study_id, username, cohort, datetime.now(timezone.utc).isoformat()),
            )
            n += 1
    # Clear the in-process cache so new mappings take effect immediately
    telemetry._STUDY_ID_CACHE.clear()
    print(f"✅ Imported {n} roster entries.")


def import_assessment(csv_path: str):
    n = 0
    valid_instruments = {"pretest", "posttest", "mai_pre", "mai_post"}
    with open(csv_path, newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            instrument = row["instrument"].strip()
            if instrument not in valid_instruments:
                print(f"⚠️  Skipping unknown instrument: {instrument}")
                continue
            telemetry.log_assessment(
                study_id=row["study_id"].strip(),
                instrument=instrument,
                item_id=row["item_id"].strip(),
                bloom_tier=row.get("bloom_tier", "").strip(),
                concept=row.get("concept", "").strip(),
                score=float(row["score"]),
            )
            n += 1
    print(f"✅ Imported {n} assessment items.")


def import_self_report(csv_path: str):
    n = 0
    valid = {"self_efficacy", "interest", "goal_orientation"}
    with open(csv_path, newline='') as f:
        for row in csv.DictReader(f):
            dim = row["dimension"].strip()
            if dim not in valid:
                print(f"⚠️  Skipping unknown dimension: {dim}")
                continue
            telemetry.log_self_report(row["username"].strip(), dim,
                                      row["item_id"].strip(), float(row["score"]))
            n += 1
    telemetry._STUDY_ID_CACHE.clear()
    print(f"✅ Imported {n} self-report items.")


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    mode, path = sys.argv[1], sys.argv[2]
    if not os.path.exists(path):
        print(f"❌ File not found: {path}")
        sys.exit(1)
    if mode == "roster":
        import_roster(path)
    elif mode == "assessment":
        import_assessment(path)
    elif mode == "self_report":
        import_self_report(path)
    else:
        print(f"❌ Unknown mode: {mode}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
