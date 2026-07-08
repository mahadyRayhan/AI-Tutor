"""
Pre-Class Telemetry Dry Run
Exercises every telemetry logger directly and asserts that each table receives a
row. This is the "if it fails silently in the dry run, it fails silently for 12
weeks" safety check from the Class Data Collection Plan.

It tests the telemetry layer in isolation (no LLM / server needed): it calls the
same functions the live hooks call, then verifies the rows landed and are linked
by a single study_id.

Run: cd backend && python ../SRL-script/test_telemetry_dryrun.py
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from app.db.sqlite_db import db
from app.core import telemetry

TEST_USER = "__dryrun_student__"


def cleanup():
    sid_row = db.fetch_one(
        "SELECT study_id FROM study_participants WHERE username = ?", (TEST_USER,)
    )
    sid = sid_row["study_id"] if sid_row else None
    tables = ["event_log", "evidence_log", "bkt_history", "prediction_log",
              "calibration_log", "jol_log", "response_log", "behavior_log",
              "affect_log", "turn_log", "path_event", "quiz_log", "misconception_log"]
    for t in tables:
        db.execute(f"DELETE FROM {t} WHERE username = ?", (TEST_USER,))
    db.execute("DELETE FROM session_log WHERE username = ?", (TEST_USER,))
    if sid:
        db.execute("DELETE FROM assessment WHERE study_id = ?", (sid,))
    db.execute("DELETE FROM study_participants WHERE username = ?", (TEST_USER,))
    telemetry._STUDY_ID_CACHE.pop(TEST_USER, None)


def run():
    print("=" * 70)
    print("PRE-CLASS TELEMETRY DRY RUN")
    print("=" * 70)

    cleanup()  # start clean

    # 1. Study ID is created
    sid = telemetry.get_study_id(TEST_USER)
    print(f"  Study ID assigned: {sid}")

    # 2. Fire every logger once (mirrors the live hooks)
    telemetry.log_event(TEST_USER, "session_start", {"k": "v"}, session_id="sess-1")
    telemetry.log_evidence(TEST_USER, "Arrays", "quiz", True, question_id="q1")
    telemetry.log_prediction(TEST_USER, "Arrays", "quiz", 0.42, 0.40, True)
    telemetry.log_bkt_snapshot(TEST_USER, "Arrays", "quiz", 0.68, 1, 0, 0, "evidence_quiz")
    telemetry.log_calibration(TEST_USER, "Arrays", "quiz", 0.68, 0.40, -0.28, -0.30, 0.20, 0.22)
    telemetry.log_jol(TEST_USER, "Arrays", 4, True, quiz_id="q1")
    telemetry.log_response(TEST_USER, "sess-1", "Arrays", "CONCEPT", "developing", "Explanation|Challenge")
    telemetry.log_behavior(TEST_USER, "sess-1", "copy_code", 142, message_id="m1")
    telemetry.log_behavior(TEST_USER, "sess-1", "dwell", 3.5, message_id="m1")
    telemetry.log_affect(TEST_USER, "sess-1", 0.12, "frustrated", True)
    telemetry.log_assessment(sid, "pretest", "item_3", "declarative", "Arrays", 1.0)
    telemetry.touch_session(TEST_USER, "sess-1")
    telemetry.log_turn(TEST_USER, "sess-1", 1, "what is an array?", "An array is...",
                       "CONCEPT", ["Arrays"], "Arrays", "developing",
                       0.7, 0, "Planning", 0.05, 0, 3, 1200, False, None)
    telemetry.log_path_event(TEST_USER, "Arrays", "Loops", "skip_ahead", "Build a Calculator")
    telemetry.log_quiz_item(TEST_USER, "sess-1", "Arrays", "What is arr[0]?", "first element",
                            "the first element", True, 4, 8.3, False, 1)
    telemetry.log_misconception(TEST_USER, "Arrays", "stored", detail="said arr[0] is second", ema_value=0.30)

    # 3. Verify each table got the expected row(s)
    checks = [
        ("study_participants", "SELECT COUNT(*) c FROM study_participants WHERE username=?", (TEST_USER,), 1),
        ("event_log",          "SELECT COUNT(*) c FROM event_log WHERE username=?", (TEST_USER,), 1),
        ("evidence_log",       "SELECT COUNT(*) c FROM evidence_log WHERE username=?", (TEST_USER,), 1),
        ("prediction_log",     "SELECT COUNT(*) c FROM prediction_log WHERE username=?", (TEST_USER,), 1),
        ("bkt_history",        "SELECT COUNT(*) c FROM bkt_history WHERE username=?", (TEST_USER,), 1),
        ("calibration_log",    "SELECT COUNT(*) c FROM calibration_log WHERE username=?", (TEST_USER,), 1),
        ("jol_log",            "SELECT COUNT(*) c FROM jol_log WHERE username=?", (TEST_USER,), 1),
        ("response_log",       "SELECT COUNT(*) c FROM response_log WHERE username=?", (TEST_USER,), 1),
        ("behavior_log",       "SELECT COUNT(*) c FROM behavior_log WHERE username=?", (TEST_USER,), 2),
        ("affect_log",         "SELECT COUNT(*) c FROM affect_log WHERE username=?", (TEST_USER,), 1),
        ("assessment",         "SELECT COUNT(*) c FROM assessment WHERE study_id=?", (sid,), 1),
        ("session_log",        "SELECT COUNT(*) c FROM session_log WHERE username=?", (TEST_USER,), 1),
        ("turn_log",           "SELECT COUNT(*) c FROM turn_log WHERE username=?", (TEST_USER,), 1),
        ("path_event",         "SELECT COUNT(*) c FROM path_event WHERE username=?", (TEST_USER,), 1),
        ("quiz_log",           "SELECT COUNT(*) c FROM quiz_log WHERE username=?", (TEST_USER,), 1),
        ("misconception_log",  "SELECT COUNT(*) c FROM misconception_log WHERE username=?", (TEST_USER,), 1),
    ]

    passed = failed = 0
    print("\n  Table population checks:")
    for table, sql, params, expected in checks:
        got = db.fetch_one(sql, params)["c"]
        ok = got >= expected
        icon = "✅" if ok else "❌"
        print(f"    {icon} {table:<22} rows={got} (expected ≥{expected})")
        passed += ok
        failed += (not ok)

    # 4. Verify single-study_id linkage across tables
    print("\n  Linkage check (all rows share one study_id):")
    link_ok = True
    for table in ["event_log", "evidence_log", "prediction_log", "bkt_history",
                  "calibration_log", "jol_log", "response_log", "behavior_log",
                  "affect_log", "turn_log", "path_event", "quiz_log", "misconception_log"]:
        rows = db.fetch_all(f"SELECT DISTINCT study_id FROM {table} WHERE username=?", (TEST_USER,))
        ids = [r["study_id"] for r in rows]
        if ids != [sid]:
            print(f"    ❌ {table}: study_ids = {ids} (expected [{sid}])")
            link_ok = False
    if link_ok:
        print(f"    ✅ All tables linked to {sid}")
    else:
        failed += 1

    # 5. event_log payload round-trips as JSON
    import json
    ev = db.fetch_one("SELECT payload FROM event_log WHERE username=?", (TEST_USER,))
    try:
        json.loads(ev["payload"])
        print("\n  ✅ event_log payload is valid JSON")
    except Exception:
        print("\n  ❌ event_log payload is not valid JSON")
        failed += 1

    print("\n" + "-" * 70)
    total = passed + failed
    print(f"  Results: {passed}/{total} checks passed")
    print("  RESULT:", "✅ ALL LOGGERS WORKING" if failed == 0 else "❌ FIX BEFORE CLASS")
    print("=" * 70)

    cleanup()
    return failed == 0


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
