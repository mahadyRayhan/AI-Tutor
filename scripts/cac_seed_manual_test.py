#!/usr/bin/env python
"""Seed four learners in known states, for manual CAC testing.

  python scripts/cac_seed_manual_test.py            # seed
  python scripts/cac_seed_manual_test.py --verify   # show what CAC decides for each
  python scripts/cac_seed_manual_test.py --clean    # remove them

Each persona isolates ONE phase. Password for all four: cactest
Writes only rows belonging to usernames prefixed `cactest_`, so it cannot
disturb real learners; --clean removes exactly those rows.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from app.db.sqlite_db import db                      # noqa: E402
from app.core.user_manager import user_manager        # noqa: E402

PREFIX = "cactest_"
PASSWORD = "cactest"
NOW = datetime.now(timezone.utc)

BASELINE      = PREFIX + "baseline"        # Phase 0/1 · region gate
IMPULSIVE     = PREFIX + "impulsive"       # Phase 2   · help-seeking -> rung
LOADED        = PREFIX + "loaded"          # Phase 3   · load -> horizon
OVERCONFIDENT = PREFIX + "overconfident"   # Phase 4   · calibration -> edge
ALL = (BASELINE, IMPULSIVE, LOADED, OVERCONFIDENT)


def _ts(days_ago=0):
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S.%f")


def _user(name):
    if not db.fetch_one("SELECT 1 FROM users WHERE username=?", (name,)):
        user_manager.create_user(name, PASSWORD, role="student",
                                 name=name, email=f"{name}@test.local")
        print(f"  created user {name} / {PASSWORD}")
    else:
        print(f"  user {name} exists")


def _certify(user, concept, p=0.99, days_ago=0):
    """Certified at `p`, last practised `days_ago`. Decay applies from there."""
    db.execute("DELETE FROM user_knowledge WHERE username=? AND concept=?", (user, concept))
    db.execute(
        "INSERT INTO user_knowledge (username, concept, p_mastery_quiz, p_mastery_micro, "
        " p_mastery_code, n_evidence_quiz, n_evidence_micro, n_evidence_code, "
        " is_certified, ever_certified, last_quiz_at, last_micro_at, last_code_at) "
        "VALUES (?,?,?,?,?,?,?,?,1,1,?,?,?)",
        (user, concept, p, p, p, 5, 5, 5,
         _ts(days_ago), _ts(days_ago), _ts(days_ago)))


def _turns(user, n=20, c_code=8.0, latency_ms=20000):
    """turn_log rows. c_code and latency drive `cognitive_load`."""
    db.execute("DELETE FROM turn_log WHERE username=?", (user,))
    for i in range(n):
        db.execute(
            "INSERT INTO turn_log (username, session_id, turn_index, query_text, "
            " response_text, intent, entities, topic, mastery_level, c_code, "
            " delta_f, n_strike, n_sources, latency_ms, was_blocked, ts_utc) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)",
            (user, "seed", i, "q", "a", "CONCEPT", "[]", "Control Flow",
             "novice", c_code, 0.0, 0, 3, latency_ms, _ts()))


def _evidence(user, n=20, correct=False):
    """evidence_log drives the error-rate third of cognitive_load."""
    db.execute("DELETE FROM evidence_log WHERE username=?", (user,))
    for i in range(n):
        db.execute(
            "INSERT INTO evidence_log (username, concept, tier, is_correct, ts_utc) "
            "VALUES (?,?,?,?,?)",
            (user, "Control Flow", "declarative", 1 if correct else 0, _ts()))


def _predictions(user, n_right=6, n_wrong=5, n_confident_wrong=4):
    """prediction_log drives `calibration.gap_ratio` (model-side overconfidence)."""
    db.execute("DELETE FROM prediction_log WHERE username=?", (user,))
    for _ in range(n_right):
        db.execute("INSERT INTO prediction_log (username, concept, tier, p_bkt_pred, "
                   "p_eff_pred, is_correct, ts_utc) VALUES (?,?,?,?,?,1,?)",
                   (user, "Strings", "declarative", 0.85, 0.85, _ts()))
    for i in range(n_wrong):
        p = 0.88 if i < n_confident_wrong else 0.30   # confident-wrong vs honest miss
        db.execute("INSERT INTO prediction_log (username, concept, tier, p_bkt_pred, "
                   "p_eff_pred, is_correct, ts_utc) VALUES (?,?,?,?,?,0,?)",
                   (user, "Strings", "declarative", p, p, _ts()))


def _quizzes(user, n_quiz=4, n_skip=6, n_giveup=3):
    """quiz_log + quiz_skip events drive `help_seeking.quality`."""
    db.execute("DELETE FROM quiz_log WHERE username=?", (user,))
    db.execute("DELETE FROM event_log WHERE username=? AND event_type='quiz_skip'", (user,))
    for i in range(n_quiz):
        give = i < n_giveup
        db.execute(
            "INSERT INTO quiz_log (username, session_id, concept, question_text, "
            " correct_answer, student_answer, is_correct, confidence_1_5, "
            " time_to_answer_sec, is_verification, ts_utc) VALUES (?,?,?,?,?,?,?,?,?,0,?)",
            (user, "seed", "Control Flow", "q?", "a", "idk" if give else "a",
             0 if give else 1, 2, 2.0 if give else 40.0, _ts()))
    for _ in range(n_skip):
        db.execute("INSERT INTO event_log (username, session_id, event_type, payload, ts_utc) "
                   "VALUES (?,?,?,?,?)",
                   (user, "seed", "quiz_skip", json.dumps({"concept": "Control Flow"}), _ts()))


def seed():
    print("\nT1 " + BASELINE + "  — Phase 0/1 region gate")
    _user(BASELINE); _certify(BASELINE, "Variables")
    _turns(BASELINE, c_code=0.5, latency_ms=1500); _evidence(BASELINE, correct=True)
    print("     Variables certified · load low · help-seeking measured")

    print("\nT2 " + IMPULSIVE + " — Phase 2 help-seeking -> rung cap")
    _user(IMPULSIVE); _certify(IMPULSIVE, "Variables")
    _turns(IMPULSIVE, c_code=0.5, latency_ms=1500); _evidence(IMPULSIVE, correct=True)
    _quizzes(IMPULSIVE)
    print("     6 skips / 4 quizzes / 3 instant give-ups  -> quality=impulsive")

    print("\nT3 " + LOADED + "    — Phase 3 load -> horizon")
    _user(LOADED); _certify(LOADED, "Variables")
    _turns(LOADED, c_code=8.0, latency_ms=20000); _evidence(LOADED, correct=False)
    print("     complexity 8 · latency 20s · every answer wrong -> load 1.0")

    print("\nT4 " + OVERCONFIDENT + " — Phase 4 calibration -> edge")
    _user(OVERCONFIDENT)
    _certify(OVERCONFIDENT, "Variables")
    _certify(OVERCONFIDENT, "Control Flow")
    _certify(OVERCONFIDENT, "Arrays")
    # 2 days, not more. The micro and code tiers decay steeply: at 2 days they
    # sit at 0.837 / 0.774 — still certified (>= 0.75) but under the raised bar
    # (< 0.90), which is exactly the marginal case Phase 4 acts on. By day 4 the
    # certification is gone entirely and there is nothing left to tighten.
    _certify(OVERCONFIDENT, "Strings", p=0.99, days_ago=2)
    _turns(OVERCONFIDENT, c_code=0.5, latency_ms=1500)
    _evidence(OVERCONFIDENT, correct=True)
    _predictions(OVERCONFIDENT)
    print("     Strings certified 2 days ago (decayed to ~0.77-0.93: passes 0.75, fails 0.90)")
    print("     4 of 5 misses were confident misses -> gap_ratio 0.8")
    print(f"\nseeded. password for all four: {PASSWORD}")


def verify():
    from app.core import cac_graph, bkt_model
    from app.core.learner_model import _help_seeking, _cognitive_load, _calibration

    for u in ALL:
        if not db.fetch_one("SELECT 1 FROM users WHERE username=?", (u,)):
            print(f"{u}: NOT SEEDED"); continue
        v = cac_graph.build_view(u)
        print(f"\n{u}")
        print(f"  certified      : {sorted(v.certified) or '(none)'}")
        print(f"  load           : {v.cognitive_load}   help-seeking: {v.help_seeking_quality}")
        print(f"  gap_ratio      : {v.gap_ratio}  over {v.gap_n_wrong} misses")
        for topic in ("Control Flow", "Arrays", "Strings", "File I/O"):
            d = cac_graph.decide(v, [topic], region_gate=True)
            bits = [f"rung={d.rung_cap.label}"]
            if not d.in_horizon:   bits.append("horizon CONTRACTED")
            if not d.edge_ok:      bits.append(f"edge theta={d.theta_edge}")
            if d.redirect_to:      bits.append(f"redirect->{d.redirect_to}")
            print(f"    ask '{topic}': {' · '.join(bits)}")


def clean():
    tables = ("user_knowledge", "turn_log", "evidence_log", "prediction_log",
              "quiz_log", "event_log", "cac_access_event", "response_log",
              "jol_log", "bkt_history", "users")
    for t in tables:
        try:
            db.execute(f"DELETE FROM {t} WHERE username LIKE ?", (PREFIX + "%",))
        except Exception as e:
            print(f"  skip {t}: {e}")
    print(f"removed all rows for {PREFIX}*")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--clean", action="store_true")
    a = ap.parse_args()
    if a.clean:
        clean()
    elif a.verify:
        verify()
    else:
        seed()
