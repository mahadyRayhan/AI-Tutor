"""
Eval 1.1 — Mastery-Aware Adaptation Quality
============================================
Tests whether SAGE-SRL adapts its pedagogical responses based on the
student's Bayesian Knowledge Tracing (BKT) state.

Method:
  Ask the SAME concept question for three simulated student states:
    State A — Fresh student (no mastery history)         → expected: novice
    State B — Quiz-only mastery (P̃_quiz ≈ 0.95)         → expected: developing
    State C — Fully certified student reviewing           → expected: reviewing

  An LLM judge then scores each response on a 1–5 rubric:
    - Appropriate difficulty
    - Scaffolding depth
    - Assumed prerequisite knowledge
    - Mastery adaptation (does the response adapt to the student's level?)

Dataset: 20 C concepts × 3 student states = 60 interactions

Usage:
    python SRL-script/eval_1_1_mastery_adaptation.py [--dry-run] [--concepts N]
"""

import sys
import os
import json
import asyncio
import logging
import csv
import argparse
from datetime import datetime, timedelta

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'backend'))

from app.core import config
from app.db.llm_interface import LLMInterface
from app.db.vector_store import ChromaVectorStore
from app.db.graph_db import Neo4jGraphDB
from app.agents.cot_rag_agent import ChainOfThoughtRAGAgent
from app.db.sqlite_db import db
from app.core.history_manager import history_manager
from app.core.bkt_model import _read_row, _apply_decay, _parse_ts, EVIDENCE_CONFIG

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

# ── Configuration ────────────────────────────────────────────────────────────

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), '..', 'SRL-eval')
OUTPUT_CSV = os.path.join(OUTPUT_DIR, 'eval_1_1_mastery_adaptation.csv')
OUTPUT_JSON = os.path.join(OUTPUT_DIR, 'eval_1_1_mastery_adaptation.json')

STUDENT_STATES = {
    "fresh":     "eval_fresh_student",
    "quiz_only": "eval_quizonly_student",
    "certified": "eval_certified_student",
}

EXPECTED_MASTERY = {
    "fresh": "novice",
    "quiz_only": "developing",
    "certified": "reviewing",
}

# 20 C concepts spanning the curriculum — ordered by prerequisite depth
CONCEPT_QUERIES = [
    {"concept": "Variables and Types", "query": "What is a variable in C?"},
    {"concept": "Variables and Types", "query": "What is the difference between int and float?"},
    {"concept": "Input Output",        "query": "How does printf work in C?"},
    {"concept": "Input Output",        "query": "How do I read user input with scanf?"},
    {"concept": "Operators",           "query": "How do arithmetic operators work in C?"},
    {"concept": "Operators",           "query": "What is the difference between == and =?"},
    {"concept": "Conditionals",        "query": "How do if-else statements work in C?"},
    {"concept": "Conditionals",        "query": "When should I use a switch statement?"},
    {"concept": "Loops",               "query": "How does a for loop work in C?"},
    {"concept": "Loops",               "query": "What is the difference between while and do-while?"},
    {"concept": "Arrays",              "query": "How do arrays work in C?"},
    {"concept": "Arrays",              "query": "How do I find the maximum value in an array?"},
    {"concept": "Strings",             "query": "How are strings represented in C?"},
    {"concept": "Strings",             "query": "How do I compare two strings in C?"},
    {"concept": "Functions",           "query": "How do I write a function in C?"},
    {"concept": "Functions",           "query": "What is the difference between pass by value and pass by reference?"},
    {"concept": "Scope",               "query": "What is variable scope in C?"},
    {"concept": "Scope",               "query": "What is the difference between local and global variables?"},
    {"concept": "Pointers",            "query": "What are pointers in C?"},
    {"concept": "Pointers",            "query": "How do I use malloc for dynamic memory?"},
]

# LLM judge rubric
JUDGE_SYSTEM_PROMPT = """You are an expert evaluator assessing how well an AI tutor adapts its response to a student's current knowledge level.

You will be given:
1. The student's KNOWLEDGE STATE (fresh beginner, partial mastery, or certified expert reviewing)
2. The CONCEPT being asked about
3. The TUTOR'S RESPONSE

Rate the response on four dimensions using a 1-5 scale:

**Appropriate Difficulty (1-5):**
  1 = Completely mismatched (e.g., advanced explanation for a beginner, or overly basic for an expert)
  3 = Somewhat appropriate but not tailored
  5 = Perfectly calibrated to the student's level

**Scaffolding Depth (1-5):**
  1 = No scaffolding at all (just gives the answer) or excessive hand-holding for an expert
  3 = Some scaffolding but generic
  5 = Scaffolding perfectly matches need — deep for beginners, light for experts

**Assumed Prerequisite Knowledge (1-5):**
  1 = Assumes knowledge the student doesn't have, or explains basics unnecessarily to an expert
  3 = Partially appropriate assumptions
  5 = Accurately assumes what this student should/shouldn't know

**Mastery Adaptation (1-5):**
  1 = Response is completely generic — no sign it was adapted to the student's level. A beginner and expert would get the same explanation.
  2 = Minimal adaptation — one or two minor wording differences but fundamentally the same approach.
  3 = Some adaptation — the tone or depth shifts slightly, but the structure and content are largely the same.
  4 = Good adaptation — clear differences in explanation depth, examples, or approach based on the student's level.
  5 = Excellent adaptation — the response is clearly tailored: beginners get ground-up explanations with analogies, intermediate students get gap-filling, experts get concise refreshers focusing on edge cases.

Respond with ONLY a JSON object (no markdown, no extra text):
{"difficulty": <int>, "scaffolding": <int>, "prerequisites": <int>, "adaptation": <int>, "reasoning": "<brief explanation>"}"""


def _judge_prompt(state_label: str, concept: str, query: str, response: str) -> str:
    state_descriptions = {
        "fresh": "FRESH BEGINNER — This student has never interacted with the system before. They have no mastery history, no prior quiz results, and no known concepts. They are a complete novice.",
        "quiz_only": "PARTIAL MASTERY — This student has answered quiz questions correctly for this concept (quiz P̃ ≈ 0.95) but has NOT demonstrated procedural (micro) or applied (code) skills. They can recall facts but haven't proven they can apply the knowledge.",
        "certified": "CERTIFIED EXPERT REVIEWING — This student previously achieved full mastery (all three tiers certified) on this concept, but time has passed and their knowledge has decayed. They are reviewing to refresh. They have deep prior understanding that just needs reactivation.",
    }
    return f"""Student Knowledge State: {state_descriptions[state_label]}

Concept: {concept}
Student's Question: "{query}"

Tutor's Response:
---
{response}
---

Rate the tutor's adaptation to this student's knowledge state."""


# ── Mastery-level verification (mirrors cot_rag_agent.py logic) ──────────────

def _compute_expected_mastery(username: str, concept: str) -> dict:
    """Compute mastery level using the same logic as the ITS inner loop."""
    row = _read_row(username, concept)
    if row is None:
        return {"level": "novice", "detail": "No BKT row", "p_quiz": 0, "p_micro": 0, "p_code": 0}

    q = _apply_decay(row["p_mastery_quiz"] or EVIDENCE_CONFIG["quiz"]["P_L0"],
                     "quiz", _parse_ts(row["last_quiz_at"]), lam=row["decay_quiz_lam"])
    m = _apply_decay(row["p_mastery_micro"] or EVIDENCE_CONFIG["micro"]["P_L0"],
                     "micro", _parse_ts(row["last_micro_at"]), lam=row["decay_micro_lam"])
    c = _apply_decay(row["p_mastery_code"] or EVIDENCE_CONFIG["code"]["P_L0"],
                     "code", _parse_ts(row["last_code_at"]), lam=row["decay_code_lam"])
    n_q = row["n_evidence_quiz"] or 0
    n_m = row["n_evidence_micro"] or 0
    n_c = row["n_evidence_code"] or 0
    ever_cert = bool(row["ever_certified"])
    avg_p = (q + m + c) / 3.0

    if ever_cert:
        level = "reviewing"
    elif avg_p >= 0.75 and min(n_q, n_m, n_c) >= 2:
        level = "proficient"
    elif avg_p >= 0.35 or max(n_q, n_m, n_c) >= 2:
        level = "developing"
    else:
        level = "novice"

    return {
        "level": level, "p_quiz": round(q, 3), "p_micro": round(m, 3),
        "p_code": round(c, 3), "n_q": n_q, "n_m": n_m, "n_c": n_c,
        "ever_certified": ever_cert, "avg_p": round(avg_p, 3),
    }


# ── Database helpers to set up student profiles ──────────────────────────────

def _cleanup_eval_users():
    for username in STUDENT_STATES.values():
        db.execute("DELETE FROM user_knowledge WHERE username=?", (username,))
        db.execute("DELETE FROM sessions WHERE username=?", (username,))
        db.execute("DELETE FROM messages WHERE username=?", (username,))
        db.execute("DELETE FROM users WHERE username=?", (username,))
    _eval_sessions.clear()


_eval_sessions = {}

def _ensure_user_exists(username: str) -> str:
    """Ensures user exists. Creates a FRESH session per call to avoid
    micro-challenge accumulation and session state interference."""
    existing = db.fetch_one("SELECT 1 FROM users WHERE username=?", (username,))
    if not existing:
        db.execute(
            "INSERT INTO users (username, password_hash, role, name, created_at) VALUES (?, ?, ?, ?, ?)",
            (username, "eval_hash", "student", username, datetime.now())
        )
    # Fresh session per query — prevents challenge limit, withholding state, etc.
    session_id = history_manager.create_session(username, "Eval 1.1")
    # Clear global skipped challenges and any accumulated session state
    _reset_eval_state(username)
    return session_id


def _reset_eval_state(username: str):
    """Reset micro-challenge queue and session flags so each query starts clean."""
    import json as _json
    row = db.fetch_one("SELECT learning_profile FROM users WHERE username=?", (username,))
    if row and row["learning_profile"]:
        profile = _json.loads(row["learning_profile"])
        profile.pop("skipped_challenges", None)
        db.execute("UPDATE users SET learning_profile=? WHERE username=?",
                   (_json.dumps(profile), username))


def _satisfy_prerequisites(username: str, exclude_concept: str):
    """Mark all concepts EXCEPT the tested one (and word-related ones) as ever_certified.
    This satisfies the prerequisite gate so it doesn't confound mastery adaptation testing.
    Related concepts (sharing significant words) are also excluded to prevent the ITS
    fuzzy match from picking up a sibling/parent concept instead of the tested one."""
    past = datetime.now() - timedelta(days=60)
    exclude_words = set(w.lower() for w in exclude_concept.split() if len(w) >= 3)
    for c in ALL_CONCEPTS:
        if c == exclude_concept:
            continue
        c_words = set(w.lower() for w in c.split() if len(w) >= 3)
        if exclude_words & c_words:
            continue
        existing = db.fetch_one(
            "SELECT 1 FROM user_knowledge WHERE username=? AND concept=?", (username, c)
        )
        if not existing:
            db.execute(
                """INSERT INTO user_knowledge
                   (username, concept, timestamp,
                    p_mastery_quiz, p_mastery_micro, p_mastery_code, p_mastery,
                    n_evidence_quiz, n_evidence_micro, n_evidence_code,
                    is_certified, ever_certified, ease_factor)
                   VALUES (?, ?, ?, 0.95, 0.95, 0.95, 0.90, 3, 3, 3, 1, 1, 2.5)""",
                (username, c, past)
            )


def _setup_fresh_student(concept: str) -> str:
    username = STUDENT_STATES["fresh"]
    sid = _ensure_user_exists(username)
    # Remove any row for the tested concept so ITS sees "novice"
    db.execute("DELETE FROM user_knowledge WHERE username=? AND concept=?", (username, concept))
    # Satisfy prerequisites for other concepts
    _satisfy_prerequisites(username, concept)
    return sid


def _setup_quiz_only_student(concept: str) -> str:
    username = STUDENT_STATES["quiz_only"]
    sid = _ensure_user_exists(username)
    # Satisfy prerequisites
    _satisfy_prerequisites(username, concept)
    # Set up tested concept with quiz-only mastery
    db.execute("DELETE FROM user_knowledge WHERE username=? AND concept=?", (username, concept))
    db.execute(
        """INSERT OR REPLACE INTO user_knowledge
           (username, concept, timestamp,
            p_mastery_quiz, p_mastery_micro, p_mastery_code, p_mastery,
            n_evidence_quiz, n_evidence_micro, n_evidence_code,
            is_certified, ever_certified,
            last_quiz_at, ease_factor)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (username, concept, datetime.now(),
         0.96, 0.05, 0.01,  # quiz high, micro/code at priors
         0.96 * 0.60 + 0.05 * 0.25 + 0.01 * 0.10,  # composite
         5, 0, 0,  # only quiz evidence
         0, 0,  # not certified
         datetime.now(), 2.5)
    )
    return sid


# All concepts from CONCEPT_QUERIES + all knowledge graph nodes + common prerequisites
# This ensures the prerequisite gate never blocks eval queries
_QUERY_CONCEPTS = sorted(set(item["concept"] for item in CONCEPT_QUERIES))
try:
    import json as _json
    _kg_path = os.path.join(os.path.dirname(__file__), '..', 'resources', 'metadata', 'c_knowledge_graph.json')
    with open(_kg_path) as _f:
        _kg = _json.load(_f)
    _GRAPH_CONCEPTS = [item['entity'] for item in _kg]
except Exception:
    _GRAPH_CONCEPTS = []
ALL_CONCEPTS = sorted(set(_QUERY_CONCEPTS + _GRAPH_CONCEPTS + [
    "char", "int", "float", "double", "void",  # primitive types often used as prerequisites
]))

def _setup_certified_student(concept: str) -> str:
    """Set up a student who mastered the FULL curriculum and is now reviewing.
    All concepts get ever_certified=1 so prerequisite gates don't interfere."""
    username = STUDENT_STATES["certified"]
    sid = _ensure_user_exists(username)
    past = datetime.now() - timedelta(days=21)
    # Insert all concepts as ever_certified (full curriculum mastery)
    for c in ALL_CONCEPTS:
        existing = db.fetch_one(
            "SELECT 1 FROM user_knowledge WHERE username=? AND concept=?", (username, c)
        )
        if not existing:
            db.execute(
                """INSERT INTO user_knowledge
                   (username, concept, timestamp,
                    p_mastery_quiz, p_mastery_micro, p_mastery_code, p_mastery,
                    n_evidence_quiz, n_evidence_micro, n_evidence_code,
                    is_certified, ever_certified,
                    last_quiz_at, last_micro_at, last_code_at,
                    decay_quiz_lam, decay_micro_lam, decay_code_lam,
                    ease_factor)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (username, c, past,
                 0.98, 0.97, 0.96,
                 0.98 * 0.60 + 0.97 * 0.25 + 0.96 * 0.10,
                 5, 4, 3,
                 0, 1,  # decertified but ever_certified=1
                 past, past, past,
                 0.04951, 0.09902, 0.13863,
                 2.5)
            )
    # Ensure the tested concept has the right state (re-insert with fresh timestamp)
    db.execute("DELETE FROM user_knowledge WHERE username=? AND concept=?", (username, concept))
    db.execute(
        """INSERT INTO user_knowledge
           (username, concept, timestamp,
            p_mastery_quiz, p_mastery_micro, p_mastery_code, p_mastery,
            n_evidence_quiz, n_evidence_micro, n_evidence_code,
            is_certified, ever_certified,
            last_quiz_at, last_micro_at, last_code_at,
            decay_quiz_lam, decay_micro_lam, decay_code_lam,
            ease_factor)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (username, concept, past,
         0.98, 0.97, 0.96,
         0.98 * 0.60 + 0.97 * 0.25 + 0.96 * 0.10,
         5, 4, 3,
         0, 1,  # decertified but ever_certified=1
         past, past, past,
         0.04951, 0.09902, 0.13863,
         2.5)
    )
    return sid


# ── Main evaluation loop ────────────────────────────────────────────────────

async def run_evaluation(max_concepts: int = None, dry_run: bool = False):
    logging.basicConfig(level=logging.WARNING)
    logger = logging.getLogger("Eval_1_1")
    logger.setLevel(logging.INFO)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(os.path.join(OUTPUT_DIR, 'figures'), exist_ok=True)

    concepts = CONCEPT_QUERIES[:max_concepts] if max_concepts else CONCEPT_QUERIES
    logger.info(f"Running Eval 1.1 with {len(concepts)} concepts × 3 states = {len(concepts)*3} interactions")

    # Clean previous eval data
    _cleanup_eval_users()

    # Initialize the agent (mirrors backend/app/main.py startup)
    llm_fast = LLMInterface(
        google_model_id=config.DEFAULT_GOOGLE_MODEL_ID,
        logger=logger
    )
    llm_smart = LLMInterface(
        google_model_id=config.DEFAULT_REASONING_MODEL_ID,
        logger=logger
    )
    vec = ChromaVectorStore(persist_directory=config.DEFAULT_VECTOR_DB_PATH, logger=logger)
    graph = Neo4jGraphDB(logger=logger)
    agent = ChainOfThoughtRAGAgent(llm_fast, llm_smart, vec, graph, logger=logger)

    # LLM judge interface (uses smart/reasoning model)
    judge_llm = llm_smart

    results = []
    raw_data = []
    mastery_checks = {"correct": 0, "total": 0}

    for i, item in enumerate(concepts):
        concept = item["concept"]
        query = item["query"]
        logger.info(f"[{i+1}/{len(concepts)}] Concept: {concept} | Query: {query}")

        for state_label, username in STUDENT_STATES.items():
            # Set up the student's DB state and get session_id
            if state_label == "fresh":
                session_id = _setup_fresh_student(concept)
            elif state_label == "quiz_only":
                session_id = _setup_quiz_only_student(concept)
            elif state_label == "certified":
                session_id = _setup_certified_student(concept)

            # Verify mastery classification matches expectations
            mastery_info = _compute_expected_mastery(username, concept)
            expected = EXPECTED_MASTERY[state_label]
            mastery_match = mastery_info["level"] == expected
            mastery_checks["total"] += 1
            if mastery_match:
                mastery_checks["correct"] += 1
            else:
                logger.warning(
                    f"  MASTERY MISMATCH: {username}/{concept} — "
                    f"expected={expected}, got={mastery_info['level']} "
                    f"(avg_p={mastery_info.get('avg_p')}, ever_cert={mastery_info.get('ever_certified')})"
                )

            if dry_run:
                response_text = f"[DRY RUN] Would query system as {username} with state={state_label}"
                judge_scores = {"difficulty": 0, "scaffolding": 0, "prerequisites": 0, "adaptation": 0, "reasoning": "dry run"}
            else:
                # Get system response
                response_text = ""
                try:
                    async for event in agent.run_stream(
                        query, "student",
                        username=username,
                        session_id=session_id
                    ):
                        if event.get("type") == "complete":
                            response_text = event.get("data", {}).get("answer", "")
                except Exception as e:
                    response_text = f"[ERROR] {e}"
                    logger.error(f"System error for {username}/{concept}: {e}")

                # LLM Judge scoring (synchronous call)
                judge_scores = _run_judge(judge_llm, state_label, concept, query, response_text)

            row = {
                "concept": concept,
                "query": query,
                "student_state": state_label,
                "username": username,
                "mastery_level": mastery_info["level"],
                "mastery_expected": expected,
                "mastery_match": mastery_match,
                "p_quiz": mastery_info.get("p_quiz", 0),
                "p_micro": mastery_info.get("p_micro", 0),
                "p_code": mastery_info.get("p_code", 0),
                "difficulty_score": judge_scores.get("difficulty", 0),
                "scaffolding_score": judge_scores.get("scaffolding", 0),
                "prerequisites_score": judge_scores.get("prerequisites", 0),
                "adaptation_score": judge_scores.get("adaptation", 0),
                "avg_score": round(
                    (judge_scores.get("difficulty", 0) +
                     judge_scores.get("scaffolding", 0) +
                     judge_scores.get("prerequisites", 0) +
                     judge_scores.get("adaptation", 0)) / 4.0, 2
                ),
                "judge_reasoning": judge_scores.get("reasoning", ""),
            }
            results.append(row)

            raw_data.append({
                **row,
                "system_response": response_text,
                "mastery_info": mastery_info,
            })

            logger.info(
                f"  [{state_label}] level={mastery_info['level']}{'✓' if mastery_match else '✗'} "
                f"D={row['difficulty_score']} S={row['scaffolding_score']} "
                f"P={row['prerequisites_score']} A={row['adaptation_score']} avg={row['avg_score']}"
            )

            await asyncio.sleep(0.5)  # rate limit

    # Save CSV
    if results:
        fieldnames = list(results[0].keys())
        with open(OUTPUT_CSV, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)
        logger.info(f"CSV saved: {OUTPUT_CSV}")

    # Save JSON (includes full responses for qualitative review)
    with open(OUTPUT_JSON, 'w') as f:
        json.dump(raw_data, f, indent=2, default=str)
    logger.info(f"JSON saved: {OUTPUT_JSON}")

    # Print summary
    _print_summary(results, mastery_checks)

    # Cleanup eval users
    _cleanup_eval_users()

    return results


def _run_judge(judge_llm, state_label, concept, query, response_text) -> dict:
    user_prompt = _judge_prompt(state_label, concept, query, response_text)
    full_prompt = f"{JUDGE_SYSTEM_PROMPT}\n\n---\n\n{user_prompt}"
    try:
        judge_response = judge_llm.generate_response(full_prompt)
        text = judge_response.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        scores = json.loads(text)
        return {
            "difficulty": int(scores.get("difficulty", 0)),
            "scaffolding": int(scores.get("scaffolding", 0)),
            "prerequisites": int(scores.get("prerequisites", 0)),
            "adaptation": int(scores.get("adaptation", 0)),
            "reasoning": scores.get("reasoning", ""),
        }
    except json.JSONDecodeError as e:
        return {
            "difficulty": 0,
            "scaffolding": 0,
            "prerequisites": 0,
            "adaptation": 0,
            "reasoning": f"Judge JSON parse error: {e} | Raw: {judge_response[:200]}",
        }
    except Exception as e:
        return {
            "difficulty": 0,
            "scaffolding": 0,
            "prerequisites": 0,
            "adaptation": 0,
            "reasoning": f"Judge error: {e}",
        }


def _print_summary(results, mastery_checks):
    if not results:
        print("No results to summarize.")
        return

    print("\n" + "=" * 70)
    print("EVAL 1.1 — Mastery-Aware Adaptation Quality — Summary")
    print("=" * 70)

    # Mastery classification accuracy
    total = mastery_checks["total"]
    correct = mastery_checks["correct"]
    pct = (correct / total * 100) if total > 0 else 0
    print(f"\n  Mastery Classification: {correct}/{total} correct ({pct:.0f}%)")

    for state in ["fresh", "quiz_only", "certified"]:
        rows = [r for r in results if r["student_state"] == state]
        if not rows:
            continue
        n = len(rows)
        avg_d = sum(r["difficulty_score"] for r in rows) / n
        avg_s = sum(r["scaffolding_score"] for r in rows) / n
        avg_p = sum(r["prerequisites_score"] for r in rows) / n
        avg_a = sum(r["adaptation_score"] for r in rows) / n
        avg_all = sum(r["avg_score"] for r in rows) / n
        level = rows[0]["mastery_level"]
        print(f"\n  State: {state:12s} → level: {level:12s} (n={n})")
        print(f"    Difficulty:     {avg_d:.2f} / 5")
        print(f"    Scaffolding:    {avg_s:.2f} / 5")
        print(f"    Prerequisites:  {avg_p:.2f} / 5")
        print(f"    Adaptation:     {avg_a:.2f} / 5")
        print(f"    Overall Avg:    {avg_all:.2f} / 5")

    # Cross-state adaptation delta
    fresh_rows = [r for r in results if r["student_state"] == "fresh"]
    cert_rows = [r for r in results if r["student_state"] == "certified"]
    if fresh_rows and cert_rows:
        fresh_avg = sum(r["avg_score"] for r in fresh_rows) / len(fresh_rows)
        cert_avg = sum(r["avg_score"] for r in cert_rows) / len(cert_rows)
        fresh_adapt = sum(r["adaptation_score"] for r in fresh_rows) / len(fresh_rows)
        cert_adapt = sum(r["adaptation_score"] for r in cert_rows) / len(cert_rows)
        print(f"\n  Adaptation gap (certified - fresh):")
        print(f"    Overall:    {cert_avg - fresh_avg:+.2f}")
        print(f"    Adaptation: {cert_adapt - fresh_adapt:+.2f}")
        print("  (Positive = system gives better-adapted responses to known experts)")

    print("\n" + "=" * 70)


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Eval 1.1: Mastery-Aware Adaptation Quality")
    parser.add_argument("--dry-run", action="store_true", help="Skip actual system calls, test framework only")
    parser.add_argument("--concepts", type=int, default=None, help="Limit to first N concepts (default: all 20)")
    args = parser.parse_args()

    asyncio.run(run_evaluation(max_concepts=args.concepts, dry_run=args.dry_run))
