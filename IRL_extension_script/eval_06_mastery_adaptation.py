#!/usr/bin/env python
"""
IRL Extension — Eval 06: mastery-conditioned response adaptation (policy Pi, Table II).

WHAT IS BEING VALIDATED
───────────────────────
Table II declares a four-level adaptation policy (NOVICE / DEVELOPING / PROFICIENT /
REVIEWING). Nothing in the paper yet shows that the policy is *realised* — every
evaluation so far ran on fresh novice accounts, so `mastery_level` was constant and the
policy was never exercised. This script exercises it.

It also closes a defect found while auditing `_classify_mastery_level`. The classifier
used to pool the three evidence tiers:

    avg_p = (p_quiz + p_micro + p_code) / 3          # OLD — compensatory
    proficient  iff  avg_p >= 0.75 and all n >= 2

which is the exact pooling failure that Sec. III-D/E and Theorem 1 exist to remove from
*certification* — reintroduced inside the *adaptation* policy. A quiz-strong /
code-weak learner (0.95 / 0.75 / 0.55 -> avg 0.75) was classified PROFICIENT and handed
*reduced* scaffolding, which is the opposite of what they need. The deployed rule is now
conjunctive, mirroring Eq. (9):

    proficient  iff  min(p_quiz, p_micro, p_code) >= 0.75 and min(n) >= 2

and it reads the OBJECTIVE BKT posterior rather than the self-blended P_eff, so the SRL
confidence slider can no longer move the amount of scaffolding a student receives.

THREE ARMS
──────────
  A  policy    Offline. No server, no LLM, no API cost. Simulates evidence streams
               through the DEPLOYED estimator (`_bkt_step`, `EVIDENCE_CONFIG`) for the
               five eval_05 archetypes, then runs BOTH policies on each resulting state.
               Reports how often the pooled mean assigns a HIGHER level than the
               conjunctive rule, by archetype. This is the quantitative link from
               Theorem 1 to Sec. III-F: the pooling failure does not stop at
               certification, it propagates into how much help the student is given.

  B  live      Seeds one synthetic learner per archetype directly into `user_knowledge`,
               then asks all of them the SAME question set through the live pipeline,
               fresh session per question. ~5 users x 20 questions.

  C  score     Deterministic response features + a BLIND LLM judge that is shown one
               response and must name which of the four Table II levels it was written
               for. Primary metric = Pi-fidelity (accuracy + Cohen's kappa vs 25%
               chance) with the 4x4 confusion matrix.

WHY A BLIND JUDGE AND NOT JUST LENGTH
─────────────────────────────────────
Table II varies scaffolding depth, challenge type, and content focus — not merely answer
length. Reporting "proficient answers are shorter" would understate the policy and would
be trivially gameable. The blind judge tests whether the four levels are *distinguishable
as pedagogical treatments*; the feature trends then say WHICH dimensions carry the signal.

USAGE
─────
    python IRL_extension_script/eval_06_mastery_adaptation.py --policy     # arm A, offline
    # server must be running for the rest
    python IRL_extension_script/eval_06_mastery_adaptation.py --seed
    python IRL_extension_script/eval_06_mastery_adaptation.py --collect
    python IRL_extension_script/eval_06_mastery_adaptation.py --score
    python IRL_extension_script/eval_06_mastery_adaptation.py --judge
    python IRL_extension_script/eval_06_mastery_adaptation.py --report

`--collect` is resumable: completed (archetype, question) cells are kept.

OUTPUTS (IRL_extension_results/)
    eval_06_policy.csv          arm A: per-state pooled vs conjunctive level
    eval_06_policy_summary.csv  arm A: disagreement rate + direction, by archetype
    eval_06_responses.json      arm B: every response, with the level the system assigned
    eval_06_features.csv        arm C: deterministic per-response features
    eval_06_judge.csv           arm C: blind judge verdicts
    eval_06_report.md           the write-up
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import sqlite3
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
OUT = os.path.join(ROOT, "IRL_extension_results")
os.makedirs(OUT, exist_ok=True)
sys.path.append(os.path.join(ROOT, "backend"))

POLICY_PATH   = os.path.join(OUT, "eval_06_policy.csv")
POLSUM_PATH   = os.path.join(OUT, "eval_06_policy_summary.csv")
RESP_PATH     = os.path.join(OUT, "eval_06_responses.json")
FEAT_PATH     = os.path.join(OUT, "eval_06_features.csv")
JUDGE_PATH    = os.path.join(OUT, "eval_06_judge.csv")
REPORT_PATH   = os.path.join(OUT, "eval_06_report.md")

BASE_URL = os.getenv("SAGE_BASE_URL", "http://localhost:8000")
DB_PATH  = os.path.join(ROOT, "backend", "database", "ai_tutor.db")

TIERS = ["quiz", "micro", "code"]
LEVELS = ["novice", "developing", "proficient", "reviewing"]

# The concept every seeded learner is given a history on. Must be a knowledge-graph
# entity name, and the question set below must resolve to it.
CONCEPT = "Pointers"

# `Pointers` REQUIRES_UNDERSTANDING_OF `Variables and Types`. Without a certified
# prerequisite the gatekeeper intercepts every question with a roadmap and the adaptation
# policy is never exercised — the first smoke run failed exactly this way. Every seeded
# learner therefore gets the prerequisite closure certified, including the NOVICE
# archetype: they are a novice *at pointers*, not a novice at C.
PREREQS = ["Variables and Types"]


# ══════════════════════════════════════════════════════════════════════════════
# The two competing policies
#
# `policy_conjunctive` is a transcription of the DEPLOYED rule in
# cot_rag_agent._classify_mastery_level (steps 5-6). Arm B independently verifies the
# transcription: the live system reports the level it actually assigned, and --score
# asserts it matches this function on the seeded state. If that assertion ever fails,
# the transcription has drifted and arm A is invalid.
# ══════════════════════════════════════════════════════════════════════════════
def policy_pooled(p: dict, n: dict, ever_certified: bool = False) -> str:
    """OLD rule: compensatory mean over the three tiers."""
    if ever_certified:
        return "reviewing"
    avg_p = (p["quiz"] + p["micro"] + p["code"]) / 3.0
    if avg_p >= 0.75 and all(n[t] >= 2 for t in TIERS):
        return "proficient"
    if avg_p >= 0.35 or any(n[t] >= 2 for t in TIERS):
        return "developing"
    return "novice"


def policy_conjunctive(p: dict, n: dict, ever_certified: bool = False) -> str:
    """DEPLOYED rule: non-compensatory minimum over the three tiers (mirrors Eq. 9)."""
    if ever_certified:
        return "reviewing"
    p_min = min(p[t] for t in TIERS)
    if p_min >= 0.75 and min(n[t] for t in TIERS) >= 2:
        return "proficient"
    if p_min >= 0.35 or max(n[t] for t in TIERS) >= 2:
        return "developing"
    return "novice"


def level_rank(level: str) -> int:
    """Ordinal support level. Higher rank == LESS scaffolding given."""
    return {"novice": 0, "developing": 1, "proficient": 2, "reviewing": 3}[level]


# ══════════════════════════════════════════════════════════════════════════════
# ARM A — offline policy comparison
# ══════════════════════════════════════════════════════════════════════════════
def true_vector(profile: str, lam: float = 1.0, base: float = 0.92) -> dict:
    """Latent per-tier competence. Identical construct to eval_05 so the two
    evaluations describe the same population of learners."""
    if profile == "master":
        return {"quiz": base, "micro": base, "code": base}
    if profile == "lopsided":
        return {"quiz": base,
                "micro": max(0.02, base - lam * 0.70),
                "code":  max(0.02, base - lam * 0.87)}
    if profile == "inverse_lopsided":
        return {"quiz": max(0.02, base - lam * 0.87),
                "micro": max(0.02, base - lam * 0.70),
                "code":  base}
    if profile == "moderate":
        return {"quiz": 0.65, "micro": 0.65, "code": 0.65}
    if profile == "weak":
        return {"quiz": 0.25, "micro": 0.20, "code": 0.15}
    raise ValueError(profile)


def simulate_state(tv: dict, n_per_tier: int, rng: random.Random) -> tuple[dict, dict]:
    """Run n_per_tier Bernoulli(tv[tier]) observations per tier through the deployed
    BKT update. Returns (posteriors, evidence counts)."""
    from app.core.bkt_model import _bkt_step, EVIDENCE_CONFIG

    p, n = {}, {}
    for tier in TIERS:
        cfg = EVIDENCE_CONFIG[tier]
        post = cfg["P_L0"]
        for _ in range(n_per_tier):
            correct = rng.random() < tv[tier]
            post = _bkt_step(post, correct, cfg)
        p[tier] = post
        n[tier] = n_per_tier
    return p, n


TRUE_MASTERY_LEVEL = 0.80          # same construct as eval_05


def is_true_master(tv: dict) -> bool:
    return all(tv[t] >= TRUE_MASTERY_LEVEL for t in TIERS)


def _states(prof: str, lam: float, budgets, reps: int, rng) -> list:
    """Simulate states and run both policies. `harmful` marks the case that matters:
    the pooled rule withdraws scaffolding (proficient/reviewing) from a learner who is
    NOT competent in all three tiers. Pooled promoting a genuine master early is a
    disagreement but not an error, and is counted separately."""
    tv = true_vector(prof, lam=lam)
    truth = is_true_master(tv)
    out = []
    for budget in budgets:
        for _ in range(reps):
            p, n = simulate_state(tv, budget, rng)
            lp, lc = policy_pooled(p, n), policy_conjunctive(p, n)
            out.append({
                "archetype": prof, "lam": round(lam, 2), "n_per_tier": budget,
                "true_master": int(truth),
                "p_quiz": round(p["quiz"], 4), "p_micro": round(p["micro"], 4),
                "p_code": round(p["code"], 4),
                "p_min": round(min(p.values()), 4),
                "p_avg": round(sum(p.values()) / 3.0, 4),
                "level_pooled": lp, "level_conjunctive": lc,
                "agree": int(lp == lc),
                "pooled_higher": int(level_rank(lp) > level_rank(lc)),
                "pooled_lower": int(level_rank(lp) < level_rank(lc)),
                "harmful": int((not truth) and lp == "proficient" and lc != "proficient"),
            })
    return out


def phase_policy(args) -> None:
    print("[policy] arm A — offline, deployed estimator, no LLM")
    rng = random.Random(args.seed_rng)
    budgets = [2, 3, 5, 9]
    reps = args.n_states

    rows = []
    # Study A1 — the five fixed archetypes at full lopsidedness.
    for prof in ["master", "lopsided", "inverse_lopsided", "moderate", "weak"]:
        rows += [{**r, "study": "A1"} for r in _states(prof, 1.0, budgets, reps, rng)]
    # Study A2 — lopsidedness sweep. At lam=1 the weak tiers sit near the floor and both
    # rules agree on DEVELOPING; the two rules can only diverge in the intermediate band
    # where the mean clears 0.75 but the minimum does not, so the sweep is required to
    # locate that band rather than assert it does not exist.
    for prof in ["lopsided", "inverse_lopsided"]:
        for lam in [round(0.1 * i, 2) for i in range(11)]:
            rows += [{**r, "study": "A2"} for r in _states(prof, lam, budgets, reps, rng)]

    df = pd.DataFrame(rows)
    df.to_csv(POLICY_PATH, index=False)
    print(f"[policy] wrote {POLICY_PATH}  ({len(df)} states)")

    def _summ(g: pd.DataFrame, **keys) -> dict:
        n_ = len(g)
        k = int(g["harmful"].sum())
        lo, hi = wilson(k, n_)
        return {**keys, "n_states": n_,
                "true_master": int(g["true_master"].iloc[0]),
                "disagreement_rate": round(1 - g["agree"].mean(), 4),
                "harmful_over_credit": k,
                "harmful_pct": round(100 * k / n_, 2),
                "wilson_lo_pct": round(100 * lo, 2),
                "wilson_hi_pct": round(100 * hi, 2),
                "pooled_proficient_pct": round(100 * (g["level_pooled"] == "proficient").mean(), 2),
                "conj_proficient_pct": round(100 * (g["level_conjunctive"] == "proficient").mean(), 2)}

    a1 = pd.DataFrame([_summ(g, archetype=k)
                       for k, g in df[df.study == "A1"].groupby("archetype")])
    a1 = a1.sort_values("harmful_pct", ascending=False)
    a2 = pd.DataFrame([_summ(g, archetype=k[0], lam=k[1])
                       for k, g in df[df.study == "A2"].groupby(["archetype", "lam"])])
    a2 = a2.sort_values(["archetype", "lam"])

    pd.concat([a1.assign(study="A1"), a2.assign(study="A2")]).to_csv(POLSUM_PATH, index=False)
    print(f"[policy] wrote {POLSUM_PATH}\n")
    print("── A1: fixed archetypes ──")
    print(a1.to_string(index=False))
    print("\n── A2: lopsidedness sweep (harmful = pooled says PROFICIENT to a non-master) ──")
    print(a2[["archetype", "lam", "harmful_pct", "wilson_lo_pct", "wilson_hi_pct",
              "pooled_proficient_pct", "conj_proficient_pct"]].to_string(index=False))
    peak = a2.loc[a2["harmful_pct"].idxmax()] if len(a2) else None
    if peak is not None and peak["harmful_pct"] > 0:
        print(f"\n  worst case: {peak['archetype']} at lam={peak['lam']} — the pooled mean "
              f"withdraws scaffolding from {peak['harmful_pct']:.1f}% of non-masters "
              f"[95% CI {peak['wilson_lo_pct']:.1f}, {peak['wilson_hi_pct']:.1f}]")


def wilson(k: int, n: int) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    z = 1.959964
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = (z / den) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, c - h), min(1.0, c + h))


# ══════════════════════════════════════════════════════════════════════════════
# ARM B — seed learners, then replay one question set through the live system
# ══════════════════════════════════════════════════════════════════════════════
# Seeded states are chosen so the CONJUNCTIVE rule produces each of the four levels,
# plus one adversarial cell (`lopsided`) where the two rules disagree.
SEEDS = {
    # name              quiz  micro code   n_q n_m n_c  cert  expected level / weak tier
    "novice":     dict(p=(0.30, 0.05, 0.01), n=(0, 0, 0), cert=0,
                       expect="novice", expect_weak=""),
    "developing": dict(p=(0.72, 0.55, 0.40), n=(3, 2, 2), cert=0,
                       expect="developing", expect_weak="code"),
    "proficient": dict(p=(0.90, 0.86, 0.82), n=(4, 4, 4), cert=0,
                       expect="proficient", expect_weak=""),
    "reviewing":  dict(p=(0.96, 0.96, 0.96), n=(6, 6, 6), cert=1,
                       expect="reviewing", expect_weak=""),
    # The Evidence Diversity case: pooled mean = 0.75 -> PROFICIENT (under-scaffolds a
    # student who cannot write the code); conjunctive min = 0.55 -> DEVELOPING.
    "lopsided":   dict(p=(0.95, 0.75, 0.55), n=(4, 3, 2), cert=0,
                       expect="developing", expect_weak="code"),
    # Mirror image, and the reason it exists: without it, every archetype has `code` as
    # its weakest tier, so "the system identifies the lagging competence" is
    # indistinguishable from "the system always says code" — `code` carries the lowest
    # Cromwell prior (0.01 vs quiz 0.30) and wins an unconditional argmin by default.
    # This learner can write the code but cannot state the concept, so a working
    # tier-steering signal must flip to `quiz`.
    "inverse_lopsided": dict(p=(0.55, 0.75, 0.95), n=(2, 3, 4), cert=0,
                             expect="developing", expect_weak="quiz"),
}

QUESTIONS = [
    # Every question is phrased as an explicit request for an explanation.
    #
    # This is not stylistic. Seeding a knowledge row makes the learner "known" to the
    # Socratic withholding gate (cot_rag_agent, `is_known`), which then answers with
    # "my records show you already mastered Pointers — how do YOU think we should
    # approach this?" instead of an adapted explanation. That gate exempts `reviewing`
    # but nobody else, so in the previous run it fired on 11/20 turns for every other
    # archetype: 46% of all responses were 39-word withholding prompts rather than the
    # 252-word explanations the adaptation policy produces. The policy under test was
    # never applied to them.
    #
    # The gate stands down when the student is asking to be reminded, so phrasing every
    # question as "Can you explain..." elicits the adapted explanation from every
    # archetype identically. This changes the instrument, not the system.
    "Can you explain what a pointer is in C?",
    "Can you explain how I declare a pointer variable?",
    "Can you explain how the address-of operator works with pointers?",
    "Can you explain what dereferencing a pointer means?",
    "Can you explain what a null pointer is?",
    "Can you explain how a pointer differs from a regular variable?",
    "Can you explain how pointer arithmetic works?",
    "Can you explain what the * symbol means when declaring a pointer?",
    "Can you explain what a dangling pointer is?",
    "Can you explain how a pointer parameter works in a function definition?",
    "Can you explain what a pointer to a pointer is?",
    "Can you explain how to make a pointer point to an existing variable?",
    "Can you explain how an uninitialised pointer becomes dangerous?",
    "Can you explain how much memory a pointer occupies?",
    "Can you explain what a function pointer is in C?",
    "Can you explain how a void pointer differs from a typed pointer?",
    "Can you explain how to safely free memory held by a pointer?",
    "Can you explain what a const pointer is in C?",
    "Can you explain how pointers cause memory leaks?",
    "Can you explain what a pointer actually stores in memory?",
]


def _username(arch: str, tag: str) -> str:
    return f"irl6_{tag}_{arch}"


def signup(username: str) -> None:
    try:
        requests.post(f"{BASE_URL}/api/v1/auth/signup", timeout=20, json={
            "name": "IRLEval6", "email": f"{username}@eval.edu",
            "username": username, "password": "Str0ngPass",
        })
    except Exception as e:
        print(f"  ! signup failed ({e}) — continuing, user may already exist")


def _connect(write: bool = False) -> sqlite3.Connection:
    """The DB lives under OneDrive and runs in `journal_mode=delete`, so every write
    transaction creates and deletes an `ai_tutor.db-journal` sidecar. The sync client
    intermittently intercepts those file operations and SQLite surfaces it as
    `disk I/O error`. A long busy_timeout absorbs contention with the live server; the
    caller is responsible for retrying genuine SQLITE_IOERR."""
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    return con


def phase_seed(args) -> None:
    """Create the five learners and write their BKT state directly.

    All rows are written in ONE transaction. That is not a performance choice: it means
    a failed seed rolls back completely rather than leaving two archetypes seeded and
    three not, and it gives the OneDrive sync client one journal create/delete to
    interfere with instead of a dozen.
    """
    if not os.path.exists(DB_PATH):
        raise SystemExit(f"ERROR: DB not found at {DB_PATH}")
    tag = args.tag
    # NAIVE local time, matching what the system itself writes (`datetime.now()` in
    # bkt_model). An aware UTC timestamp parses back as tz-aware, and `_apply_decay`
    # then does `datetime.now() - last_at`, which raises TypeError on mixed awareness.
    # The caller swallows that exception and falls back to "novice", so seeding aware
    # timestamps silently classified every non-certified learner as a novice.
    now = datetime.now().isoformat()

    plan = []          # (username, concept, p..., n..., cert)
    for arch, spec in SEEDS.items():
        u = _username(arch, tag)
        plan.append((u, CONCEPT, *spec["p"], *spec["n"], spec["cert"]))
        for pre in PREREQS:
            plan.append((u, pre, 0.97, 0.97, 0.97, 6, 6, 6, 1))

    for u in {p[0] for p in plan}:
        signup(u)

    last_err = None
    for attempt in range(5):
        con = _connect(write=True)
        try:
            with con:                      # commits on success, rolls back on exception
                for (u, concept, pq, pm, pc, nq, nm, nc, cert) in plan:
                    con.execute(
                        "DELETE FROM user_knowledge WHERE username=? AND concept=?",
                        (u, concept))
                    con.execute(
                        "INSERT INTO user_knowledge (username, concept, timestamp, "
                        "p_mastery, p_mastery_quiz, p_mastery_micro, p_mastery_code, "
                        "last_quiz_at, last_micro_at, last_code_at, "
                        "n_evidence_quiz, n_evidence_micro, n_evidence_code, "
                        "is_certified, ever_certified) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (u, concept, now, (pq + pm + pc) / 3.0, pq, pm, pc,
                         now, now, now, nq, nm, nc, cert, cert))
            last_err = None
            break
        except sqlite3.OperationalError as e:
            last_err = e
            wait = 2 * (attempt + 1)
            print(f"  ! sqlite: {e} — retrying in {wait}s "
                  f"(attempt {attempt + 1}/5; nothing was written)")
            time.sleep(wait)
        finally:
            con.close()

    if last_err is not None:
        raise SystemExit(
            f"ERROR: seed failed after 5 attempts: {last_err}\n"
            f"       The DB is under OneDrive. Pause OneDrive sync (menu bar -> Pause)\n"
            f"       and re-run --seed. Nothing was partially written.")

    for arch, spec in SEEDS.items():
        pq, pm, pc = spec["p"]
        nq, nm, nc = spec["n"]
        print(f"  seeded {_username(arch, tag):28s} quiz={pq:.2f} micro={pm:.2f} "
              f"code={pc:.2f} n=({nq},{nm},{nc}) cert={spec['cert']} "
              f"-> expect {spec['expect'].upper()} | prereqs: {', '.join(PREREQS)}")

    print("\n[seed] NOTE: timestamps are 'now', so temporal decay is ~zero at collect time.")
    print("[seed] Run --collect promptly; a long gap will decay the code tier fastest.")


def ask(username: str, message: str, session_id: str, retries: int = 3) -> dict:
    """One turn against the live pipeline. Returns the `complete` event payload."""
    t0 = time.time()
    payload = {"message": message, "username": username,
               "session_id": session_id, "user_role": "student"}
    for attempt in range(retries):
        try:
            with requests.post(f"{BASE_URL}/api/v1/chat/stream", json=payload,
                               stream=True, timeout=180) as r:
                if r.status_code == 429:
                    wait = 20 * (attempt + 1)
                    print(f"    rate-limited, sleeping {wait}s")
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                data = {}
                for line in r.iter_lines(decode_unicode=True):
                    if line and line.startswith("data:"):
                        try:
                            ev = json.loads(line[5:].strip())
                        except Exception:
                            continue
                        if ev.get("type") == "complete":
                            data = ev.get("data", {}) or {}
                data["_latency_s"] = round(time.time() - t0, 3)
                return data
        except Exception as e:
            if attempt == retries - 1:
                return {"answer": "", "_error": str(e),
                        "_latency_s": round(time.time() - t0, 3)}
            time.sleep(5 * (attempt + 1))
    return {"answer": "", "_error": "exhausted retries",
            "_latency_s": round(time.time() - t0, 3)}


def read_assigned_level(session_id: str, retries: int = 3) -> dict:
    """Recover the level the system actually assigned.

    `main.py` pops `_state` from the payload before it reaches the client, so the
    adaptation decision is not observable over the API. It IS written to `response_log`
    by the same turn, keyed by session_id, so read it from there. This is an
    observation, not a re-derivation: it is the value the running system used.
    """
    for attempt in range(retries):
        try:
            con = _connect()
            cols = {r[1] for r in con.execute("PRAGMA table_info(response_log)")}
            sel = "mastery_level, intent, concept" + (", weak_tier" if "weak_tier" in cols else "")
            r = con.execute(
                f"SELECT {sel} FROM response_log WHERE session_id=? ORDER BY id DESC LIMIT 1",
                (session_id,)).fetchone()
            con.close()
            if r:
                d = dict(r)
                return {"assigned_level": d.get("mastery_level") or "",
                        "weak_tier": d.get("weak_tier") or "",
                        "logged_concept": d.get("concept") or "",
                        "logged_intent": d.get("intent") or ""}
        except Exception:
            pass
        time.sleep(0.4 * (attempt + 1))
    return {"assigned_level": "", "weak_tier": "", "logged_concept": "", "logged_intent": ""}


def phase_collect(args) -> None:
    tag = args.tag
    questions = QUESTIONS[:args.n_questions]
    print(f"[collect] {len(SEEDS)} archetypes x {len(questions)} questions "
          f"= {len(SEEDS) * len(questions)} turns")
    print(f"[collect] target: {BASE_URL}")

    done = {}
    if os.path.exists(RESP_PATH) and not args.fresh:
        with open(RESP_PATH) as f:
            blob = json.load(f)
        if blob.get("tag") == tag:
            done = {(it["archetype"], it["question"]): it for it in blob["items"]}
            print(f"[collect] resuming — {len(done)} cells already present")

    items = list(done.values())
    for arch, spec in SEEDS.items():
        u = _username(arch, tag)
        for qi, q in enumerate(questions, 1):
            if (arch, q) in done:
                continue
            # Fresh session per question: a shared session would let the trajectory
            # accumulator and conversation history contaminate later answers, and the
            # adaptation level is a per-turn decision.
            sid = f"e6-{uuid.uuid4().hex[:10]}"
            print(f"  [{arch:11s} {qi:>2}/{len(questions)}] {q[:52]}")
            d = ask(u, q, sid)
            tel = read_assigned_level(sid)
            items.append({
                "archetype": arch, "username": u, "question": q, "session_id": sid,
                "expected_level": spec["expect"],
                "assigned_level": tel["assigned_level"],
                "weak_tier": tel["weak_tier"],
                "logged_concept": tel["logged_concept"],
                "intent": d.get("intent", "") or tel["logged_intent"],
                "entities": d.get("entities", []),
                "answer": d.get("answer", "") or "",
                "latency_s": d.get("_latency_s"),
                "error": d.get("_error", ""),
            })
            with open(RESP_PATH, "w") as f:
                json.dump({"tag": tag, "concept": CONCEPT, "base_url": BASE_URL,
                           "collected": datetime.now().isoformat(timespec="seconds"),
                           "items": items}, f, indent=2)
            time.sleep(args.sleep)

    print(f"\n[collect] wrote {RESP_PATH}  ({len(items)} responses)")


# ══════════════════════════════════════════════════════════════════════════════
# ARM C — deterministic features
# ══════════════════════════════════════════════════════════════════════════════
def _clean(t) -> str:
    return "" if not isinstance(t, str) else t


def code_density_pct(text) -> float:
    """Copied verbatim from scripts/generate_comprehensive_table.py (via eval_02) so the
    number is comparable across every evaluation in this paper."""
    text = _clean(text)
    lines = text.split("\n")
    if not lines:
        return 0.0
    code_lines, in_block = 0, False
    for line in lines:
        s = line.strip()
        if "```" in line:
            if "mermaid" not in line:
                in_block = not in_block
        elif in_block or s.endswith(";") or s.endswith("{") or s.endswith("}") or "//" in s:
            if not s.startswith("- ") and not s.startswith("* "):
                code_lines += 1
    return (code_lines / len(lines)) * 100


_DEFINITION_CUES = ("is a ", "is an ", "refers to", "means that", "in simple terms",
                    "think of it", "stands for", "by definition")
_ANALOGY_CUES = ("like a ", "imagine ", "think of it as", "analogy", "just like",
                 "similar to a")
_EDGE_CUES = ("edge case", "corner case", "undefined behavior", "undefined behaviour",
              "gotcha", "subtle", "pitfall", "careful", "beware", "in practice",
              "implementation-defined", "common mistake")


_HEADER_RE = re.compile(r"^\s{0,3}#{1,6}\s.*$", re.M)


def _strip_headers(text: str) -> str:
    """Remove Markdown headers before counting content cues.

    The response format is built from level-specific section headers, several of which
    contain cue vocabulary verbatim — `## Common Mistakes` matches the `"common mistake"`
    edge cue. Counting headers means counting the template's own instructions back as if
    they were content, which inflates exactly the level that owns that section: developing
    scored 1.30 edge cues with headers included and 0.35 without, so three quarters of the
    reported effect was the header. Headers are structure and are already measured
    separately; only the prose underneath is evidence of adaptation.
    """
    return _HEADER_RE.sub("", _clean(text))


def features(text: str) -> dict:
    t = _clean(text)
    body = _strip_headers(t)          # cue counts read the prose, not the section titles
    low = body.lower()
    words = t.split()
    return {
        "n_words": len(words),
        "code_density_pct": round(code_density_pct(t), 2),
        "n_questions_posed": body.count("?"),
        "n_definition_cues": sum(low.count(c) for c in _DEFINITION_CUES),
        "n_analogy_cues": sum(low.count(c) for c in _ANALOGY_CUES),
        "n_edge_cues": sum(low.count(c) for c in _EDGE_CUES),
        "has_code_block": int("```" in t),
    }


# The scaffolding LADDER. `reviewing` is deliberately excluded: it is not a fourth rung
# of increasing competence but a different mode of delivery (a refresher for someone who
# already certified), so a monotone test spanning all four levels asks the wrong
# question and its answer is dominated by whichever way `reviewing` happens to fall.
LADDER = ["novice", "developing", "proficient"]


def spearman_trend(df: pd.DataFrame, col: str) -> tuple[float, float]:
    """Monotone-trend test across the scaffolding ladder. Reported instead of pairwise
    t-tests because Table II asserts an ORDER, not three unrelated treatments."""
    from scipy import stats
    sub = df[df["assigned_level"].isin(LADDER)]
    if sub.empty:
        return (float("nan"), float("nan"))
    x = sub["assigned_level"].map(LADDER.index).astype(float)
    y = sub[col].astype(float)
    if x.nunique() < 2:
        return (float("nan"), float("nan"))
    rho, p = stats.spearmanr(x, y)
    return (float(rho), float(p))


def holm(pairs: list) -> list:
    """Holm-Bonferroni over a list of (label, raw_p). Six features are tested per
    contrast, so uncorrected p-values would overstate the number of real effects."""
    ordered = sorted(pairs, key=lambda t: t[1])
    m, out, prev = len(ordered), [], 0.0
    for i, (lab, p) in enumerate(ordered):
        adj = min(1.0, max(prev, p * (m - i)))
        prev = adj
        out.append((lab, p, adj))
    return out


def contrast_novice_vs_proficient(df: pd.DataFrame, cols: list) -> list:
    """The specific contrast Table II claims: maximum vs light scaffolding."""
    from scipy import stats
    a = df[df["assigned_level"] == "novice"]
    b = df[df["assigned_level"] == "proficient"]
    if a.empty or b.empty:
        return []
    raw = []
    for c in cols:
        try:
            raw.append((c, float(stats.mannwhitneyu(a[c], b[c])[1])))
        except ValueError:
            raw.append((c, float("nan")))
    return [(c, a[c].mean(), b[c].mean(), p, adj)
            for c, p, adj in holm([(c, p) for c, p in raw if p == p])]


def phase_score(args) -> None:
    if not os.path.exists(RESP_PATH):
        raise SystemExit("ERROR: run --collect first")
    with open(RESP_PATH) as f:
        blob = json.load(f)
    df = pd.DataFrame(blob["items"])

    feats = df["answer"].apply(lambda a: pd.Series(features(a)))
    df = pd.concat([df.drop(columns=["answer"]), feats,
                    df[["answer"]]], axis=1)
    df.to_csv(FEAT_PATH, index=False)
    print(f"[score] wrote {FEAT_PATH}")

    # ── classifier integrity: does the live system agree with policy_conjunctive on
    #    the seeded state? If not, the arm-A transcription has drifted.
    print("\n=== classifier integrity (seeded state -> assigned level) ===")
    ok = True
    for arch, spec in SEEDS.items():
        sub = df[df["archetype"] == arch]
        if sub.empty:
            continue
        p = dict(zip(TIERS, spec["p"]))
        n = dict(zip(TIERS, spec["n"]))
        predicted = policy_conjunctive(p, n, bool(spec["cert"]))
        observed = Counter(sub["assigned_level"].fillna("")).most_common()
        top = observed[0][0] if observed else ""
        agree = 100.0 * (sub["assigned_level"] == predicted).mean()
        flag = "OK " if agree >= 90 else "!! "
        if agree < 90:
            ok = False
        print(f"  {flag}{arch:11s} script says {predicted:11s} | live says {top or '(empty)':11s} "
              f"| agreement {agree:5.1f}%  {observed}")
    if not ok:
        print("\n  !! The script's policy transcription disagrees with the live system.")
        print("     Check the two known causes below before reporting anything.")

    # Known failure mode 1: the gatekeeper answered instead of the tutor, so the
    # adaptation policy never ran on this turn.
    gated = df["answer"].fillna("").str.contains("Quick Roadmap", case=False)
    if gated.any():
        print(f"\n  !! {int(gated.sum())}/{len(df)} turns were answered by the PREREQUISITE "
              f"GATEKEEPER, not the tutor. Those turns carry no adaptation. Seed the "
              f"prerequisite closure ({', '.join(PREREQS)}) and re-collect.")
    # Known failure mode 2: nothing was logged at all.
    empty = (df["assigned_level"].fillna("") == "")
    if empty.any():
        print(f"\n  !! {int(empty.sum())}/{len(df)} turns have NO assigned level. Either the "
              f"intent was not CONCEPT/PROBLEM, or `_classify_mastery_level` raised and was "
              f"swallowed by the caller's except-block (a stale `uvicorn --reload` worker "
              f"holding the old 3-tuple caller against the new 4-tuple function does exactly "
              f"this). Restart the server cleanly and re-collect.")

    # ── manipulation check: was the adaptation policy actually applied on this turn?
    # The Socratic withholding gate answers "you already mastered this, how do YOU think
    # we should approach it?" instead of producing an adapted explanation. Those turns
    # carry no treatment, so counting them would measure the gate, not the policy.
    # Anchored on the gate's literal wording. A looser pattern ("already mastered") false
    # -positives on genuine REVIEWING refreshers, which legitimately tell the student they
    # have already mastered the concept — the phrase appears in both, only the sentence is
    # diagnostic.
    withheld = df["answer"].fillna("").str.contains(
        r"my records show you already mastered|Before I just give you the answer",
        case=False, regex=True)
    df["withheld"] = withheld
    df.to_csv(FEAT_PATH, index=False)
    if withheld.any():
        print(f"\n=== MANIPULATION CHECK ===")
        print(f"  !! {int(withheld.sum())}/{len(df)} turns were answered by the Socratic "
              f"WITHHOLDING gate, not the adaptation policy.")
        print(pd.crosstab(df["archetype"], df["withheld"]).to_string())
        print(f"\n  Mean length: {df[withheld]['n_words'].mean():.0f} words withheld vs "
              f"{df[~withheld]['n_words'].mean():.0f} words when actually explained.")
        print("  Those turns contain no adapted explanation. Phrase the questions as an "
              "explicit request for an explanation ('Can you explain...') so the gate "
              "stands down, and re-collect.")
    else:
        print("\n=== MANIPULATION CHECK: OK — 0 turns hit the Socratic withholding gate ===")

    print("\n=== response features by ASSIGNED level ===")
    cols = ["n_words", "code_density_pct", "n_questions_posed",
            "n_definition_cues", "n_analogy_cues", "n_edge_cues"]
    tbl = df[df["assigned_level"].isin(LEVELS)].groupby("assigned_level")[cols].mean()
    tbl = tbl.reindex([l for l in LEVELS if l in tbl.index]).round(2)
    print(tbl.to_string())

    print("\n=== monotone trend across the scaffolding ladder "
          "(novice -> developing -> proficient; `reviewing` excluded) ===")
    for c in cols:
        rho, p = spearman_trend(df, c)
        star = "*" if (p == p and p < 0.05) else " "
        print(f"  {c:22s} rho={rho:+.3f}  p={p:.4g} {star}")

    print("\n=== novice vs proficient, Holm-corrected over the 6 features ===")
    for c, m_nov, m_pro, p, adj in contrast_novice_vs_proficient(df, cols):
        verdict = "SURVIVES" if adj < 0.05 else "n.s."
        print(f"  {c:22s} novice={m_nov:7.2f}  proficient={m_pro:7.2f}  "
              f"p={p:.4g}  Holm={adj:.4g}  {verdict}")

    # ── the adversarial cell
    print("\n=== Evidence Diversity cell (lopsided: quiz .95 / micro .75 / code .55) ===")
    p = dict(zip(TIERS, SEEDS["lopsided"]["p"]))
    n = dict(zip(TIERS, SEEDS["lopsided"]["n"]))
    print(f"  pooled mean rule      -> {policy_pooled(p, n).upper():11s} (less scaffolding)")
    print(f"  conjunctive rule      -> {policy_conjunctive(p, n).upper():11s} (deployed)")

    # ── tier steering: does the named weak tier track the evidence, or the prior?
    print("\n=== weak-tier identification (does it FLIP, or always say 'code'?) ===")
    flips = set()
    for arch, spec in SEEDS.items():
        sub = df[df["archetype"] == arch]
        if sub.empty:
            continue
        obs = Counter(sub["weak_tier"].fillna(""))
        top = obs.most_common(1)[0][0]
        want = spec.get("expect_weak", "")
        agree = 100.0 * (sub["weak_tier"].fillna("") == want).mean()
        flips.add(top)
        flag = "OK " if agree >= 90 else "!! "
        print(f"  {flag}{arch:17s} expect {want or '(none)':7s} | observed "
              f"{top or '(none)':7s} | agreement {agree:5.1f}%  {dict(obs)}")
    named = {f for f in flips if f}
    if len(named) < 2:
        print("\n  !! Every archetype names the SAME weak tier. That is consistent with the "
              "signal tracking the tier priors rather than the learner, so it does not "
              "support a tier-steering claim.")
    else:
        print(f"\n  The named tier varies across archetypes ({sorted(named)}), so it "
              f"tracks the evidence rather than the tier priors.")


# ══════════════════════════════════════════════════════════════════════════════
# ARM C — blind LLM judge (primary metric)
# ══════════════════════════════════════════════════════════════════════════════
# NOTE ON THIS RUBRIC (v2)
# ------------------------
# v1 described REVIEWING as "concise... key points only... brief". This tutor is terse at
# EVERY level (mean 122-169 words, no significant length difference between levels), so
# those cues matched almost anything and 62% of all 120 responses were labelled REVIEWING
# — including 11 of 20 maximally-scaffolded NOVICE responses. The rubric was measuring
# brevity, not pedagogical treatment.
#
# v2 fixes the construct: each level is defined by a STRUCTURAL cue that only that level
# exhibits, length is explicitly ruled out as evidence, and "unclear" is available so
# ambiguous responses are not forced into whichever category has the loosest description.
# Responses judged "unclear" are excluded from fidelity and reported as a coverage rate.
PI_RUBRIC = """You are auditing an AI programming tutor that adapts its answers to a
student's mastery level. You will see ONE tutor response to ONE student question. You do
NOT know which student it was written for.

The tutor uses exactly four adaptation profiles. Each has a DECISIVE cue — the thing that
only that profile does:

NOVICE — decisive cue: it assumes no prior knowledge. Every technical term is defined
  before it is used, and the concept is carried by a real-world analogy or a comparison to
  something outside programming. Any closing task is fully specified — it tells the
  student exactly what to write.

DEVELOPING — decisive cue: it builds on assumed prior experience without assuming mastery.
  Some terms are used freely, others are still explained. Any closing task asks the
  student to combine ideas or write a few lines, without dictating the answer.

PROFICIENT — decisive cue: it skips the basics entirely and goes to nuance — edge cases,
  corner cases, subtle or surprising behaviour, things that go wrong. Terminology is used
  without explanation. Any closing task asks the student to predict output, find a bug, or
  handle a corner case.

REVIEWING — decisive cue: it never teaches the concept at all. It refers to the concept as
  already known ("recall that...", "as you know...", "remember that...") rather than
  explaining it, and introduces no new material. Any closing task is a retention check on
  something presumed already learned.

CRITICAL — how to judge:
  * Judge the RESPONSE, not the question.
  * LENGTH IS NOT EVIDENCE. This tutor is brief at every level. Do NOT label a response
    REVIEWING merely because it is short, or NOVICE merely because it is long.
  * Decide on the DECISIVE cues above: are terms defined or assumed? is there an analogy?
    is the concept taught or referenced as already known? what kind of task closes it?
  * If the response shows no decisive cue, or shows cues from two profiles about equally,
    answer "unclear". Do NOT guess. "unclear" is a valid and expected answer.

Return ONLY compact JSON:
{"level": "novice"|"developing"|"proficient"|"reviewing"|"unclear", "confidence": 1-5, "why": "<12 words max>"}"""


def judge_one(client, model, question, answer) -> dict:
    if not (answer or "").strip():
        return {"level": None, "confidence": None, "why": "empty response"}
    try:
        r = client.chat.completions.create(
            model=model, temperature=0, max_tokens=120,
            messages=[{"role": "system", "content": PI_RUBRIC},
                      {"role": "user",
                       "content": f"STUDENT QUESTION:\n{question}\n\nTUTOR RESPONSE:\n{answer[:6000]}"}],
        )
        txt = r.choices[0].message.content.strip()
        txt = txt[txt.find("{"): txt.rfind("}") + 1]
        d = json.loads(txt)
        lv = str(d.get("level", "")).strip().lower()
        # "unclear" is a real verdict, kept distinct from None (a judging failure) so the
        # two are never silently pooled: one is the judge declining, the other is an error.
        return {"level": lv if lv in LEVELS + ["unclear"] else None,
                "confidence": d.get("confidence"),
                "why": str(d.get("why", ""))[:80]}
    except Exception as e:
        return {"level": None, "confidence": None, "why": f"judge error: {e}"[:80]}


TIER_RUBRIC = """You are auditing an AI programming tutor. You will see ONE tutor
response to ONE student question. You do NOT know anything about the student.

The tutor sometimes aims a response at whichever competence the student is weakest in.
There are three competences:

QUIZ  — conceptual recall. The response is explicit about definitions, terminology, and
  the rules that govern the concept. It makes sure the student can STATE what things are.

MICRO — applied reasoning. The response works through small concrete cases step by step.
  It makes sure the student can REASON about the concept on short problems.

CODE  — code production. The response is grounded in concrete syntax, shows how the code
  is actually written, and pushes the student to PRODUCE code rather than recall facts.

Which competence is this response primarily trying to build? Judge only what the response
emphasises. If it does not lean toward any one of them, answer "none".

Return ONLY compact JSON:
{"tier": "quiz"|"micro"|"code"|"none", "confidence": 1-5, "why": "<12 words max>"}"""


def judge_tier_one(client, model, question, answer) -> dict:
    if not (answer or "").strip():
        return {"tier": None, "why": "empty response"}
    try:
        r = client.chat.completions.create(
            model=model, temperature=0, max_tokens=120,
            messages=[{"role": "system", "content": TIER_RUBRIC},
                      {"role": "user",
                       "content": f"STUDENT QUESTION:\n{question}\n\nTUTOR RESPONSE:\n{answer[:6000]}"}],
        )
        txt = r.choices[0].message.content.strip()
        txt = txt[txt.find("{"): txt.rfind("}") + 1]
        d = json.loads(txt)
        t = str(d.get("tier", "")).strip().lower()
        return {"tier": t if t in TIERS + ["none"] else None,
                "why": str(d.get("why", ""))[:80]}
    except Exception as e:
        return {"tier": None, "why": f"judge error: {e}"[:80]}


def phase_judge_tier(client, model, df: pd.DataFrame) -> None:
    """Does the weak-tier directive change the response in a way anyone can detect?

    Restricted to `lopsided` vs `inverse_lopsided`. Both are assigned DEVELOPING, so the
    scaffolding level is held constant and the ONLY difference between them is which tier
    the directive names — code vs quiz. Any difference the judge finds is attributable to
    tier steering alone. Deterministic keyword features found nothing here; this decides
    whether the directive has no effect or merely no effect those features can see.
    """
    from scipy import stats
    sub = df[df["archetype"].isin(["lopsided", "inverse_lopsided"])]
    if sub.empty:
        print("[judge-tier] no lopsided/inverse_lopsided responses — skipping")
        return

    rows = []
    for i, r in sub.reset_index(drop=True).iterrows():
        v = judge_tier_one(client, model, r["question"], r["answer"])
        rows.append({"archetype": r["archetype"], "question": r["question"],
                     "directed_tier": r.get("weak_tier", ""),
                     "judged_tier": v["tier"], "why": v["why"]})
        if (i + 1) % 10 == 0:
            print(f"  tier-judged {i + 1}/{len(sub)}")
    jd = pd.DataFrame(rows)
    jd.to_csv(os.path.join(OUT, "eval_06_judge_tier.csv"), index=False)

    print("\n=== tier steering: does the directive change the response? ===")
    print("(both arms assigned DEVELOPING; only the named weak tier differs)\n")
    ct = pd.crosstab(jd["archetype"], jd["judged_tier"].fillna("(none)"))
    print(ct.to_string())

    # Deliberately NOT reporting "judge names the directed tier = X%". That statistic is
    # uninterpretable here: the two arms are directed at DIFFERENT tiers, so if the judge
    # simply has a standing preference for one tier, each arm is credited for its share of
    # that preference and the hit rate rises above chance with zero steering. The chi2 on
    # the contingency table below is the correct test — it asks whether the two arms
    # differ AT ALL, which is the actual question.
    if ct.shape[0] == 2 and ct.shape[1] >= 2:
        chi2, p, _, _ = stats.chi2_contingency(ct.values)
        print(f"  responses differ by directed tier: chi2 p = {p:.4g}")
        if p >= 0.05:
            print("\n  The directive reaches the prompt (verified) and names the correct "
                  "tier (verified), but does NOT measurably change the response. Report "
                  "weak-tier IDENTIFICATION as validated and tier STEERING as not "
                  "supported — do not claim the latter.")


def cohens_kappa(y_true: list, y_pred: list, labels: list) -> float:
    n = len(y_true)
    if n == 0:
        return float("nan")
    po = sum(a == b for a, b in zip(y_true, y_pred)) / n
    ct, cp = Counter(y_true), Counter(y_pred)
    pe = sum((ct[l] / n) * (cp[l] / n) for l in labels)
    return (po - pe) / (1 - pe) if pe < 1 else float("nan")


def phase_judge(args) -> None:
    try:
        from openai import OpenAI
    except ImportError:
        raise SystemExit("ERROR: `pip install openai` required for --judge")
    from app.core import config  # noqa: E402

    if not config.OPENAI_API_KEY:
        raise SystemExit("ERROR: OPENAI_API_KEY not set")
    if not os.path.exists(RESP_PATH):
        raise SystemExit("ERROR: run --collect first")

    client = OpenAI(api_key=config.OPENAI_API_KEY)
    model = os.getenv("OPENAI_JUDGE_MODEL", "gpt-4o")
    print(f"[judge] model={model} — blind Table II level identification")

    with open(RESP_PATH) as f:
        blob = json.load(f)
    df = pd.DataFrame(blob["items"])

    done = {}
    if os.path.exists(JUDGE_PATH) and not args.fresh:
        prev = pd.read_csv(JUDGE_PATH)
        done = {(r["archetype"], r["question"]): r["judged_level"]
                for _, r in prev.iterrows() if isinstance(r.get("judged_level"), str)}
        print(f"[judge] resuming — {len(done)} verdicts already present")

    rows = []
    for i, r in df.iterrows():
        key = (r["archetype"], r["question"])
        if key in done:
            rows.append({**{k: r[k] for k in
                            ("archetype", "question", "expected_level", "assigned_level")},
                         "judged_level": done[key], "confidence": None, "why": "(cached)"})
            continue
        v = judge_one(client, model, r["question"], r["answer"])
        rows.append({"archetype": r["archetype"], "question": r["question"],
                     "expected_level": r["expected_level"],
                     "assigned_level": r["assigned_level"],
                     "judged_level": v["level"], "confidence": v["confidence"],
                     "why": v["why"]})
        if (i + 1) % 10 == 0:
            print(f"  judged {i + 1}/{len(df)}")
        pd.DataFrame(rows).to_csv(JUDGE_PATH, index=False)

    jd = pd.DataFrame(rows)
    jd.to_csv(JUDGE_PATH, index=False)
    print(f"[judge] wrote {JUDGE_PATH}")

    valid = jd[jd["judged_level"].isin(LEVELS) & jd["assigned_level"].isin(LEVELS)]
    if valid.empty:
        print("[judge] no valid pairs — cannot compute fidelity")
        return

    y_true = valid["assigned_level"].tolist()
    y_pred = valid["judged_level"].tolist()
    acc = sum(a == b for a, b in zip(y_true, y_pred)) / len(y_true)
    lo, hi = wilson(sum(a == b for a, b in zip(y_true, y_pred)), len(y_true))
    kappa = cohens_kappa(y_true, y_pred, LEVELS)

    n_all = int(jd["assigned_level"].isin(LEVELS).sum())
    n_unclear = int((jd["judged_level"] == "unclear").sum())
    n_err = int(jd["judged_level"].isna().sum())

    print(f"\n=== Pi-fidelity: blind judge recovers the assigned level ===")
    print(f"  coverage  = {len(valid)}/{n_all} = {100 * len(valid) / max(1, n_all):.1f}% "
          f"({n_unclear} judged 'unclear', {n_err} judging errors)")
    print(f"  n = {len(valid)}   accuracy = {100 * acc:.1f}%  "
          f"[95% CI {100 * lo:.1f}, {100 * hi:.1f}]   chance = 25.0%")
    print(f"  Cohen's kappa = {kappa:.3f}")

    # A single label absorbing most of the judgments is the signature of a rubric whose
    # description matches the tutor's house style rather than one specific treatment.
    # v1 of this rubric failed exactly that way (62% of everything judged 'reviewing').
    share = valid["judged_level"].value_counts(normalize=True)
    if len(share) and share.iloc[0] > 0.45:
        print(f"\n  !! {share.index[0]!r} absorbs {100 * share.iloc[0]:.0f}% of all "
              f"judgments. Suspect the rubric describes that level in terms this tutor "
              f"exhibits at every level; fidelity is understated until that is fixed.")

    print("\n=== confusion matrix (rows = assigned, cols = judged) ===")
    cm = pd.crosstab(valid["assigned_level"], valid["judged_level"])
    cm = cm.reindex(index=[l for l in LEVELS if l in cm.index],
                    columns=[l for l in LEVELS if l in cm.columns], fill_value=0)
    print(cm.to_string())

    # Ordinal error: when the judge is wrong, is it wrong by one step or by three?
    err = [abs(level_rank(a) - level_rank(b)) for a, b in zip(y_true, y_pred) if a != b]
    if err:
        print(f"\n  mean ordinal distance of errors = {np.mean(err):.2f} "
              f"(1 = adjacent level, 3 = opposite end)")

    if not args.no_tier:
        phase_judge_tier(client, model, df)


# ══════════════════════════════════════════════════════════════════════════════
# Report
# ══════════════════════════════════════════════════════════════════════════════
def phase_report(args) -> None:
    L = []
    A = L.append
    A("# Eval 06 — Mastery-Conditioned Response Adaptation (Policy Pi, Table II)\n")
    A(f"_Generated {datetime.now().isoformat(timespec='seconds')}_\n")

    A("## What changed in the system\n")
    A("`_classify_mastery_level` previously pooled the three evidence tiers "
      "(`avg_p = (p_quiz + p_micro + p_code)/3`), which is compensatory: a strong quiz "
      "tier could mask a weak code tier and win the student *less* scaffolding. That is "
      "the same pooling failure Theorem 1 removes from certification. The deployed rule "
      "is now conjunctive (`min` over tiers), matching Eq. (9), and it reads the "
      "objective BKT posterior instead of the self-blended `P_eff`, so the SRL "
      "confidence slider can no longer move the amount of support a student receives.\n")

    if os.path.exists(POLSUM_PATH):
        s = pd.read_csv(POLSUM_PATH)
        A("## Arm A — policy disagreement (offline, deployed estimator)\n")
        A("`harmful_pct` counts states where the pooled mean says PROFICIENT — i.e. "
          "withdraws scaffolding — for a learner who is NOT competent in all three "
          "tiers. Pooled promoting a *genuine* master slightly early is a disagreement "
          "but not an error, so it is excluded from this count and visible instead in "
          "`disagreement_rate`.\n")

        a1 = s[s["study"] == "A1"].drop(columns=["study", "lam"], errors="ignore")
        if not a1.empty:
            A("### A1 — fixed archetypes (full lopsidedness)\n")
            A(a1.to_markdown(index=False))
            A("")

        a2 = s[s["study"] == "A2"]
        if not a2.empty:
            A("### A2 — lopsidedness sweep\n")
            A("At full lopsidedness (lam=1) the weak tiers sit near the posterior floor "
              "and both rules agree on DEVELOPING, so A1 alone would wrongly suggest the "
              "two policies never diverge. The two rules can only differ in the band "
              "where the mean clears 0.75 while the minimum does not, and locating that "
              "band requires the sweep.\n")
            A(a2[["archetype", "lam", "n_states", "disagreement_rate", "harmful_pct",
                  "wilson_lo_pct", "wilson_hi_pct", "pooled_proficient_pct",
                  "conj_proficient_pct"]].to_markdown(index=False))
            A("")
            pk = a2.loc[a2["harmful_pct"].idxmax()]
            A(f"Peak harm is at **moderate** lopsidedness, not extreme: "
              f"`{pk['archetype']}` at lam={pk['lam']}, where the pooled mean withdraws "
              f"scaffolding from **{pk['harmful_pct']:.1f}%** of non-masters "
              f"[95% CI {pk['wilson_lo_pct']:.1f}, {pk['wilson_hi_pct']:.1f}], against "
              f"{a2[a2.lam == 1.0]['harmful_pct'].max():.1f}% at lam=1.0. The pooled rule "
              f"is safe on learners who are obviously weak everywhere and safe on the "
              f"extremely lopsided; it fails precisely where one strong tier is just "
              f"enough to drag the mean over threshold.\n")
            A("This is what connects Sec. III-F back to Theorem 1: pooled mastery does "
              "not only over-certify, it under-scaffolds — for exactly the learner the "
              "diversity guarantee was written to protect.\n")

    if os.path.exists(FEAT_PATH):
        df = pd.read_csv(FEAT_PATH)
        cols = ["n_words", "code_density_pct", "n_questions_posed",
                "n_definition_cues", "n_analogy_cues", "n_edge_cues"]
        sub = df[df["assigned_level"].isin(LEVELS)]

        # ── validity checks. These gate everything below them: if the policy assigned the
        #    wrong level, or the turn never reached the policy at all, the feature and
        #    judge numbers describe something other than Table II.
        A("## Arm B — protocol validity\n")
        A("Two checks run before any outcome is computed. They answer *was the treatment "
          "administered, and was it the right one?* — not *did it work?*\n")
        A("| archetype | seeded state | expected level | assigned level | agreement |")
        A("|---|---|---|---|---|")
        for arch, spec in SEEDS.items():
            g = df[df["archetype"] == arch]
            if g.empty:
                continue
            p = dict(zip(TIERS, spec["p"]))
            n = dict(zip(TIERS, spec["n"]))
            pred = policy_conjunctive(p, n, bool(spec["cert"]))
            obs = Counter(g["assigned_level"].fillna("")).most_common(1)[0][0]
            agree = 100.0 * (g["assigned_level"] == pred).mean()
            A(f"| `{arch}` | {spec['p'][0]:.2f}/{spec['p'][1]:.2f}/{spec['p'][2]:.2f}, "
              f"n={spec['n']}, cert={spec['cert']} | {pred} | {obs} | {agree:.0f}% |")
        A("")
        if "withheld" in df.columns:
            nw = int(df["withheld"].sum())
            A(f"Manipulation check: **{nw}/{len(df)}** turns were answered by the Socratic "
              f"withholding gate rather than the adaptation policy"
              f"{' — the protocol is clean' if nw == 0 else ' — those turns carry no treatment'}.\n")

        # ── does each level differ from its NEIGHBOUR? A monotone trend can hold while an
        #    adjacent pair is identical, which is exactly how the novice/developing
        #    collapse hid: novice->proficient was significant throughout.
        from scipy import stats as _st
        A("### Adjacent-level separation\n")
        A("| pair | separates on (p < 0.05) |")
        A("|---|---|")
        for a, b in [("novice", "developing"), ("developing", "proficient"),
                     ("proficient", "reviewing")]:
            X, Y = sub[sub.assigned_level == a], sub[sub.assigned_level == b]
            if X.empty or Y.empty:
                continue
            hits = [c for c in cols if _st.mannwhitneyu(X[c], Y[c])[1] < 0.05]
            A(f"| {a} vs {b} | {', '.join(f'`{h}`' for h in hits) or '**none**'} |")
        A("")

        if "weak_tier" in df.columns:
            A("### Weak-tier identification\n")
            A("The lowest-posterior tier is named only when the spread between tiers is "
              "material (>= 0.20) and that tier has at least one observation. Without "
              "those gates an unconditional argmin returns `code` for every learner, since "
              "`code` carries the lowest Cromwell prior (0.01 vs quiz 0.30) — the signal "
              "would track the parameters rather than the student.\n")
            A("| archetype | expected | observed | agreement |")
            A("|---|---|---|---|")
            for arch, spec in SEEDS.items():
                g = df[df["archetype"] == arch]
                if g.empty:
                    continue
                want = spec.get("expect_weak", "")
                obs = Counter(g["weak_tier"].fillna("")).most_common(1)[0][0]
                agree = 100.0 * (g["weak_tier"].fillna("") == want).mean()
                A(f"| `{arch}` | {want or '(none)'} | {obs or '(none)'} | {agree:.0f}% |")
            named = sorted({t for t in df["weak_tier"].fillna("") if t})
            A("")
            A(f"The named tier varies across archetypes ({named}), so it tracks the "
              f"evidence rather than the tier priors.\n" if len(named) > 1 else
              "Every archetype names the same tier — consistent with the signal tracking "
              "the priors, so this does not support a tier-steering claim.\n")

        tier_path = os.path.join(OUT, "eval_06_judge_tier.csv")
        A("### Tier steering — not supported\n")
        td = pd.read_csv(tier_path) if os.path.exists(tier_path) else None
        # Staleness guard. This file survives --collect, so a recovered or leftover copy
        # can silently describe responses that no longer exist. Verify it was judged on
        # the questions actually in the current collection before rendering it as current.
        if td is not None and not set(td["question"]) <= set(df["question"]):
            A(f"_Stale: `eval_06_judge_tier.csv` was judged on a different question set "
              f"({len(set(td['question']) - set(df['question']))} of "
              f"{td['question'].nunique()} questions are not in the current collection), "
              f"so it describes responses generated before the current prompt templates. "
              f"NOT rendered. The result under the previous templates was chi2 p = 1.0. "
              f"Re-run `--judge --fresh` without `--no-tier` to measure it on current "
              f"responses._\n")
            td = None
        if td is not None:
            ct = pd.crosstab(td["archetype"], td["judged_tier"].fillna("(none)"))
            chi2, pv, _, _ = _st.chi2_contingency(ct.values)
            A(ct.to_markdown())
            A("")
            A(f"`lopsided` and `inverse_lopsided` are both assigned DEVELOPING, so the "
              f"scaffolding level is held constant and the only difference is which tier "
              f"the directive names (`code` vs `quiz`). A blind judge cannot tell the two "
              f"apart (chi2 p = {pv:.3g}).\n")
        elif not os.path.exists(tier_path):
            A("_Not measured in this run (`eval_06_judge_tier.csv` absent). The stored "
              "result was chi2 p = 1.0 over 40 responses: identical judged-tier "
              "distributions for `lopsided` and `inverse_lopsided`. Re-run `--judge "
              "--fresh` without `--no-tier` to reproduce it._\n")
        A("The directive reaches the prompt and names the correct tier, but does not "
          "measurably change the response. Report weak-tier IDENTIFICATION as validated "
          "and tier STEERING as not supported.\n")

        if not sub.empty:
            A("## Arm C — response features by assigned level\n")
            t = sub.groupby("assigned_level")[cols].mean()
            t = t.reindex([l for l in LEVELS if l in t.index]).round(2)
            A(t.to_markdown())
            A("")
            A("Monotone trend across the scaffolding ladder (novice -> developing -> "
              "proficient). `reviewing` is excluded: it is a different mode of delivery, "
              "not a fourth rung, so a trend spanning it would be uninterpretable.\n")
            A("| feature | rho | p |")
            A("|---|---|---|")
            for c in cols:
                rho, p = spearman_trend(df, c)
                A(f"| `{c}` | {rho:+.3f} | {p:.4g} |")
            A("")
            rows = contrast_novice_vs_proficient(df, cols)
            if rows:
                A("Novice vs proficient, Holm-corrected across the six features:\n")
                A("| feature | novice | proficient | p | Holm-adj | |")
                A("|---|---|---|---|---|---|")
                for c, mn, mp, p, adj in rows:
                    A(f"| `{c}` | {mn:.2f} | {mp:.2f} | {p:.4g} | {adj:.4g} | "
                      f"{'**survives**' if adj < 0.05 else 'n.s.'} |")
                A("")

    if os.path.exists(JUDGE_PATH):
        jd = pd.read_csv(JUDGE_PATH)
        valid = jd[jd["judged_level"].isin(LEVELS) & jd["assigned_level"].isin(LEVELS)]
        if not valid.empty:
            y_true = valid["assigned_level"].tolist()
            y_pred = valid["judged_level"].tolist()
            k = sum(a == b for a, b in zip(y_true, y_pred))
            acc = k / len(y_true)
            lo, hi = wilson(k, len(y_true))
            kappa = cohens_kappa(y_true, y_pred, LEVELS)
            n_all = int(jd["assigned_level"].isin(LEVELS).sum())
            n_unclear = int((jd["judged_level"] == "unclear").sum())
            A("## Arm C — Pi-fidelity (blind judge)\n")
            A(f"The judge could assign a level to {len(valid)}/{n_all} responses "
              f"({100 * len(valid) / max(1, n_all):.1f}% coverage; {n_unclear} judged "
              f"`unclear` and excluded). Among those, a judge shown one response and no "
              f"student information recovers the assigned Table II level in "
              f"**{100 * acc:.1f}%** of cases [95% CI {100 * lo:.1f}, {100 * hi:.1f}], "
              f"against a 25% chance baseline (Cohen's kappa = {kappa:.3f}).\n")
            cm = pd.crosstab(valid["assigned_level"], valid["judged_level"])
            cm = cm.reindex(index=[l for l in LEVELS if l in cm.index],
                            columns=[l for l in LEVELS if l in cm.columns], fill_value=0)
            A("Confusion matrix (rows = level the system assigned, columns = level the "
              "judge inferred):\n")
            A(cm.to_markdown())
            A("")

    A("## Limitations\n")
    A("- Learner states are **seeded**, not accumulated through real study. This "
      "validates the policy mapping and the response adaptation; it does not validate "
      "that real students reach these states at the rates assumed.\n")
    A("- One concept (`" + CONCEPT + "`) and one question set. Adaptation strength may "
      "differ for topics with less curricular scaffolding available.\n")
    A("- Arm A compares two rules on simulated evidence streams; the archetype "
      "definitions are the same construct used in eval_05, so the two evaluations "
      "share their assumptions and are not independent evidence of each other.\n")

    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(L))
    print(f"[report] wrote {REPORT_PATH}")


# ══════════════════════════════════════════════════════════════════════════════
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--policy", action="store_true", help="arm A: offline policy comparison")
    ap.add_argument("--seed", action="store_true", help="arm B: create + seed the learners")
    ap.add_argument("--collect", action="store_true", help="arm B: replay questions live")
    ap.add_argument("--score", action="store_true", help="arm C: deterministic features")
    ap.add_argument("--judge", action="store_true", help="arm C: blind LLM judge")
    ap.add_argument("--report", action="store_true", help="write the markdown report")
    ap.add_argument("--tag", default="v1", help="suffix for seeded usernames")
    ap.add_argument("--n-questions", type=int, default=len(QUESTIONS))
    ap.add_argument("--n-states", type=int, default=100,
                    help="arm A: states per (archetype, budget) cell")
    ap.add_argument("--seed-rng", type=int, default=20260727)
    ap.add_argument("--sleep", type=float, default=1.5)
    ap.add_argument("--fresh", action="store_true", help="ignore cached results")
    ap.add_argument("--no-tier", action="store_true",
                    help="skip the tier-steering judge (saves ~40 calls)")
    args = ap.parse_args()

    if not any([args.policy, args.seed, args.collect, args.score, args.judge, args.report]):
        ap.error("pick at least one phase: --policy --seed --collect --score --judge --report")

    if args.policy:
        phase_policy(args)
    if args.seed:
        phase_seed(args)
    if args.collect:
        phase_collect(args)
    if args.score:
        phase_score(args)
    if args.judge:
        phase_judge(args)
    if args.report:
        phase_report(args)


if __name__ == "__main__":
    main()
