"""
Test 3: Socratic Withholding
Tests that the system withholds answers for known concepts and asks the
student to try first, then provides the answer on the second attempt.

Withholding triggers when ALL of these are true:
  1. Intent is CONCEPT or PROBLEM
  2. Topic matches a known concept in user_knowledge
  3. Student hasn't bypassed withholding this session
  4. Query does NOT contain reminder words (remind, forgot, explain, help, how)
  5. Mastery level is NOT "reviewing"

This test validates the matching logic, bypass conditions, and the reviewing
exemption — all without needing the full server pipeline.

Run: cd backend && python ../SRL-script/test_socratic_withholding.py
"""

import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from app.db.sqlite_db import db
from datetime import datetime

TEST_USER = "__test_withholding_user__"
TEST_SESSION = "test-withhold-session-001"

# ── Reproduce the withholding logic from cot_rag_agent.py lines 1636-1678 ──

REMINDER_WORDS = ["remind", "forgot", "explain", "don't remember", "help", "how"]


def get_known_concepts(username):
    garbage = {"potato", "anyway", "accidental", "send", "skip"}
    rows = db.fetch_all(
        "SELECT concept FROM user_knowledge WHERE username = ? ORDER BY timestamp ASC",
        (username,),
    )
    return [r["concept"] for r in rows if r["concept"].lower() not in garbage and len(r["concept"]) >= 3]


def check_withholding(query, entities, intent, known_concepts, mastery_level, has_bypassed):
    """
    Returns (should_withhold: bool, matched_concept: str, reason: str)
    Mirrors the logic in cot_rag_agent.py run_stream().
    """
    if intent not in ("CONCEPT", "PROBLEM"):
        return False, "", "intent is not CONCEPT/PROBLEM"

    topic = entities[0] if entities else query
    topic_lower = topic.lower().strip()

    # Strict matching (C3 FIX)
    is_known = False
    matched_concept = ""
    for k in known_concepts:
        k_lower = k.lower().strip()
        if len(k_lower) >= 4 and len(topic_lower) >= 4:
            if (k_lower == topic_lower
                    or (len(k_lower) > 4 and k_lower == topic_lower + "s")
                    or (len(topic_lower) > 4 and topic_lower == k_lower + "s")):
                is_known = True
                matched_concept = k
                break

    if not is_known:
        return False, "", f"'{topic}' not in known concepts"

    if has_bypassed:
        return False, matched_concept, "already bypassed this session"

    is_asking_for_reminder = any(w in query.lower() for w in REMINDER_WORDS)
    if is_asking_for_reminder:
        return False, matched_concept, f"query contains reminder word"

    if mastery_level == "reviewing":
        return False, matched_concept, "reviewing students bypass withholding"

    return True, matched_concept, "withholding triggered"


def setup():
    db.execute("DELETE FROM users WHERE username = ?", (TEST_USER,))
    db.execute("DELETE FROM user_knowledge WHERE username = ?", (TEST_USER,))
    db.execute("DELETE FROM sessions WHERE session_id = ?", (TEST_SESSION,))

    db.execute(
        "INSERT INTO users (username, password_hash, role, learning_profile) VALUES (?, ?, ?, ?)",
        (TEST_USER, "fakehash", "student", "{}"),
    )
    # Seed known concepts
    for concept in ["Pointers", "Arrays", "Loops"]:
        db.execute(
            "INSERT OR IGNORE INTO user_knowledge (username, concept, timestamp) VALUES (?, ?, ?)",
            (TEST_USER, concept, datetime.now()),
        )
    # Create a session
    db.execute(
        "INSERT INTO sessions (session_id, username, created_at, state) VALUES (?, ?, ?, ?)",
        (TEST_SESSION, TEST_USER, datetime.now(), "{}"),
    )


def cleanup():
    db.execute("DELETE FROM users WHERE username = ?", (TEST_USER,))
    db.execute("DELETE FROM user_knowledge WHERE username = ?", (TEST_USER,))
    db.execute("DELETE FROM sessions WHERE session_id = ?", (TEST_SESSION,))


def run_tests():
    print("=" * 70)
    print("TEST 3: Socratic Withholding")
    print("=" * 70)

    setup()
    known = get_known_concepts(TEST_USER)
    print(f"  Known concepts: {known}\n")

    passed = 0
    failed = 0
    failures = []

    def check(name, query, entities, intent, mastery_level, has_bypassed, expect_withhold):
        nonlocal passed, failed, failures
        result, matched, reason = check_withholding(
            query, entities, intent, known, mastery_level, has_bypassed
        )
        ok = result == expect_withhold

        if ok:
            passed += 1
            print(f"  ✅ [PASS] {name}")
        else:
            failed += 1
            failures.append((name, f"expected withhold={expect_withhold}, got={result}"))
            print(f"  ❌ [FAIL] {name}")
            print(f"       Expected withhold={expect_withhold}, got={result}")

        print(f"       → Reason: {reason}" + (f" (matched: '{matched}')" if matched else ""))

    # ── Group A: Should trigger withholding ──
    print("  --- Should WITHHOLD (concept known, no bypass words) ---")
    check("Known concept, direct question",
          "pointers", ["Pointers"], "CONCEPT", "novice", False,
          expect_withhold=True)

    check("Known concept, PROBLEM intent",
          "pointers problem", ["Pointers"], "PROBLEM", "novice", False,
          expect_withhold=True)

    check("Plural match: 'array' matches 'Arrays'",
          "array", ["array"], "CONCEPT", "developing", False,
          expect_withhold=True)

    check("Known concept, vague question without bypass words",
          "tell me about loops again", ["Loops"], "CONCEPT", "novice", False,
          expect_withhold=True)

    # ── Group B: Should NOT withhold (bypass words present) ──
    print("\n  --- Should NOT withhold (reminder/help words) ---")
    check("Contains 'explain'",
          "explain pointers to me", ["Pointers"], "CONCEPT", "novice", False,
          expect_withhold=False)

    check("Contains 'how'",
          "how do pointers work?", ["Pointers"], "CONCEPT", "novice", False,
          expect_withhold=False)

    check("Contains 'help'",
          "I need help with arrays", ["Arrays"], "CONCEPT", "novice", False,
          expect_withhold=False)

    check("Contains 'forgot'",
          "I forgot about loops", ["Loops"], "CONCEPT", "novice", False,
          expect_withhold=False)

    check("Contains 'remind'",
          "remind me about pointers", ["Pointers"], "CONCEPT", "novice", False,
          expect_withhold=False)

    # ── Group C: Should NOT withhold (unknown concept) ──
    print("\n  --- Should NOT withhold (concept not known) ---")
    check("Unknown concept: 'Structs'",
          "structs", ["Structs"], "CONCEPT", "novice", False,
          expect_withhold=False)

    check("Unknown concept: 'Recursion'",
          "recursion", ["Recursion"], "CONCEPT", "novice", False,
          expect_withhold=False)

    # ── Group D: Should NOT withhold (already bypassed) ──
    print("\n  --- Should NOT withhold (already bypassed this session) ---")
    check("Known concept but already bypassed",
          "pointers", ["Pointers"], "CONCEPT", "novice", True,
          expect_withhold=False)

    # ── Group E: Should NOT withhold (reviewing mastery level) ──
    print("\n  --- Should NOT withhold (reviewing students get refresher) ---")
    check("Reviewing student, known concept",
          "pointers", ["Pointers"], "CONCEPT", "reviewing", False,
          expect_withhold=False)

    # ── Group F: Should NOT withhold (wrong intent) ──
    print("\n  --- Should NOT withhold (non-CONCEPT/PROBLEM intent) ---")
    check("GREETING intent",
          "hello", ["hello"], "GREETING", "novice", False,
          expect_withhold=False)

    check("REVIEW intent",
          "int *p = NULL;", ["code"], "REVIEW", "novice", False,
          expect_withhold=False)

    # ── Group G: Short/garbage entity shouldn't match ──
    print("\n  --- Should NOT withhold (short entities < 4 chars) ---")
    check("Short entity 'int' doesn't match 'Pointers'",
          "int", ["int"], "CONCEPT", "novice", False,
          expect_withhold=False)

    # ── Summary ──
    print("\n" + "-" * 70)
    total = passed + failed
    print(f"  Results: {passed}/{total} passed  ({passed / total * 100:.1f}%)")

    if failures:
        print(f"\n  {len(failures)} FAILURES:")
        for name, detail in failures:
            print(f"    {name}: {detail}")
    else:
        print("  All tests passed!")

    print("=" * 70)

    cleanup()
    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
