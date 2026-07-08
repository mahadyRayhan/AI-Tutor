"""
Weekly data-integrity verification for the class trial.

Run this EVERY WEEK from week 1. Its job is to catch a silently broken logger
early — not after the semester is over. It checks that every telemetry table is
growing, that rows carry valid study_ids, and that key columns aren't NULL.

Exit code 0 = all green, 1 = something needs attention.

Usage
-----
  python scripts/verify_telemetry.py
  python scripts/verify_telemetry.py --since 2026-09-01   # rows on/after a date
"""

import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'backend'))
from app.db.sqlite_db import db

TELEMETRY_TABLES = [
    "study_participants", "event_log", "evidence_log", "bkt_history",
    "prediction_log", "calibration_log", "jol_log", "response_log",
    "behavior_log", "affect_log", "assessment", "session_log",
    "turn_log", "path_event", "quiz_log", "misconception_log",
]

# Tables expected to accumulate rows during normal use (warn if empty mid-trial)
EXPECTED_GROWING = [
    "event_log", "evidence_log", "bkt_history", "prediction_log",
    "response_log", "affect_log", "session_log", "turn_log",
]


def count(table, since=None):
    if since:
        row = db.fetch_one(f"SELECT COUNT(*) c FROM {table} WHERE ts_utc >= ?", (since,))
    else:
        row = db.fetch_one(f"SELECT COUNT(*) c FROM {table}")
    return row["c"] if row else 0


def null_study_ids(table):
    try:
        row = db.fetch_one(
            f"SELECT COUNT(*) c FROM {table} WHERE study_id IS NULL OR study_id = ''"
        )
        return row["c"] if row else 0
    except Exception:
        return -1  # table has no study_id column


def check_event_log_monotonic():
    """event_log.seq should be a gap-free autoincrement (no manual deletes)."""
    row = db.fetch_one("SELECT MIN(seq) lo, MAX(seq) hi, COUNT(*) c FROM event_log")
    if not row or row["c"] == 0:
        return True, "empty"
    span = row["hi"] - row["lo"] + 1
    gaps = span - row["c"]
    return gaps == 0, f"{row['c']} rows, {gaps} gaps"


def main():
    since = None
    if "--since" in sys.argv:
        since = sys.argv[sys.argv.index("--since") + 1]

    print("=" * 68)
    print("TELEMETRY VERIFICATION" + (f"  (since {since})" if since else ""))
    print("=" * 68)

    all_ok = True

    # 1. Row counts per table
    print("\n  Table row counts:")
    for t in TELEMETRY_TABLES:
        try:
            c = count(t, since)
            flag = ""
            if t in EXPECTED_GROWING and c == 0:
                flag = "  ⚠️  EMPTY (expected data)"
                all_ok = False
            print(f"    {t:<22} {c:>8}{flag}")
        except Exception as e:
            print(f"    {t:<22}  ❌ ERROR: {e}")
            all_ok = False

    # 2. Orphaned study_ids
    print("\n  NULL/empty study_id check:")
    for t in TELEMETRY_TABLES:
        n = null_study_ids(t)
        if n > 0:
            print(f"    {t:<22}  ❌ {n} rows with no study_id")
            all_ok = False
        elif n == 0:
            print(f"    {t:<22}  ✅")

    # 3. Participant coverage — every active student should appear in core tables
    print("\n  Participant coverage:")
    participants = db.fetch_all("SELECT study_id, username FROM study_participants")
    if not participants:
        print("    ⚠️  No participants registered yet.")
    else:
        for p in participants:
            ev = db.fetch_one(
                "SELECT COUNT(*) c FROM event_log WHERE study_id = ?", (p["study_id"],)
            )["c"]
            status = "✅" if ev > 0 else "⚠️  no events"
            print(f"    {p['study_id']:<10} {p['username']:<18} events={ev:<5} {status}")

    # 4. event_log integrity
    print("\n  event_log integrity:")
    ok, detail = check_event_log_monotonic()
    print(f"    monotonic seq: {'✅' if ok else '❌'}  ({detail})")
    if not ok:
        all_ok = False

    print("\n" + "-" * 68)
    print("  RESULT:", "✅ ALL GREEN" if all_ok else "❌ ISSUES FOUND — investigate above")
    print("=" * 68)
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
