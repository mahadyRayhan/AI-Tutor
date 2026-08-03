#!/usr/bin/env python
"""
IRL Extension — Eval 05: Evidence Diversity Problem / over-crediting resistance.

WHAT THIS TESTS
───────────────
  §III-D  factored knowledge tracing — three tiers (declarative / procedural / applied)
  §III-E  conjunctive certification, Eq. (9):
              t ∈ K  iff  ∀k: P̃⁽ᵏ⁾ ≥ θ_cert   ∧   ∀k: N⁽ᵏ⁾ ≥ N_min
  Thm. 1  Evidence Diversity Guarantee
  §IV-C   "Empirically realizes Theorem 1"

The Evidence Diversity Problem: a learner who recites definitions but cannot write
working code is indistinguishable, under a single pooled mastery scalar, from one who
can do both. Pooling lets cheap, easily-guessed evidence (quiz, P_G = 0.20) substitute
for expensive evidence (code, P_G = 0.05).

FOUR ARMS — QUANTITY, DIVERSITY, AND AN EXISTING MULTI-DIMENSIONAL MODEL
───────────────────────────────────────────────────────────────────────
The obvious reviewer objection is that the factored rule demands 9 observations
(3 tiers × N_min) while a naive baseline demands fewer — so the comparison would test
evidence QUANTITY, not DIVERSITY. A second, sharper objection (reviewer, [24]/[25]):
the two comparison arms are both single-scalar, so the experiment never faces an
existing MULTI-DIMENSIONAL model. Four arms address both:

  1. CANONICAL BKT      P̃ ≥ θ only.  No evidence-count rule at all.
                        This is Corbett & Anderson (1995) as actually specified:
                        mastery is declared purely on the posterior crossing 0.95.
  2. POOLED + N≥9       P̃ ≥ θ AND 9 corroborating observations.
                        Same total evidence as the factored rule, still no tier
                        structure. This arm CONTROLS FOR QUANTITY.
  3. CONJUNCTIVE KT [24]  ∏k P̃⁽ᵏ⁾ ≥ θ^K.  Per-tier posteriors like the factored
                        model, product (AND-gate) certification, no evidence count.
                        The MULTI-DIMENSIONAL baseline — not a scalar strawman.
  4. FACTORED (ours)    ∀k: P̃⁽ᵏ⁾ ≥ θ AND ∀k: N⁽ᵏ⁾ ≥ 3.

  arm 1 → 2  isolates the effect of demanding more evidence.
  arm 2 → 3  isolates the effect of multi-dimensionality (the big effect).
  arm 3 → 4  isolates the non-compensatory `min` and the per-tier floor N_min —
             the two choices that are actually ours.

FINDING: arms 1–2 (scalar) over-certify lopsided learners at ≈100%; arms 3–4 (both
multi-dimensional) at ≈0%. So the dominant effect is dimensionality, shared by [24].
The factored rule's separable difference from [24] is marginal on false-certification
and shows up as uniform conservatism on true masters; N_min is not independently
isolated by the current archetypes. State this precisely — the claim is a
non-compensatory, evidence-counted rule improving on BOTH scalar tracking AND an
existing product-rule model, not the loose "factoring beats pooling".

θ_cert = 0.95 HAS EXTERNAL PROVENANCE
─────────────────────────────────────
Unlike most constants in this system, 0.95 is not hand-picked: it is the canonical BKT
mastery criterion from Corbett & Anderson (1995) and remains the widely adopted
threshold. Cite it. (Note for docs/HANDPICKED_VALUES.md, which currently lists it as
hand-set.)

In the deployed code, 0.95 is additionally a FLOOR: `THRESHOLD_PRIORS` is 0.95 for all
tiers, `get_threshold()` returns that prior when uncalibrated, and `recalibrate()`
floors at the prior — the calibrator can only tighten. Simulating at 0.95 is therefore
the most permissive deployed configuration, making these results a LOWER BOUND.

RUNS AGAINST THE DEPLOYED ESTIMATOR
───────────────────────────────────
`_bkt_step` and `EVIDENCE_CONFIG` are imported from backend/app/core/bkt_model.py, not
re-implemented. No database, no server, no API — `_bkt_step` is a pure function.

WHAT THE "THEOREM CHECK" IS AND IS NOT
──────────────────────────────────────
Verifying that no certification violates Eq. (9) is an IMPLEMENTATION-FIDELITY unit
test — Eq. (9) is the rule, so the code obeying it is not empirical support. What
empirically realises Theorem 1 is the false-certification COUNT on lopsided learners
(reported as raw k/n with a Wilson interval, not as a bare percentage).

A NOTE ON P_T AND THE STATIC-COMPETENCE GENERATOR
─────────────────────────────────────────────────
Simulated learners have FIXED latent competence — they do not learn during the run.
Every model still applies P_T > 0 (a learning-transition prior) on correct answers, so
all four arms drift upward over a long stream. That drift is model-side optimism, and
it is the known BKT pathology behind high certification rates on weak learners. Because
the result is sensitive to it, P_T is swept explicitly (Study C) rather than assumed.

USAGE
─────
    python IRL_extension_script/eval_05_evidence_diversity.py
    python IRL_extension_script/eval_05_evidence_diversity.py --n 4000 --seed 11
    python IRL_extension_script/eval_05_evidence_diversity.py --mix 0.4,0.3,0.3

OUTPUTS (IRL_extension_results/)
    eval_05_archetypes.csv    four arms × five archetypes   (§IV-C headline)
    eval_05_sweep.csv         certification vs lopsidedness, all four arms
    eval_05_errors.csv        error-class decomposition      (§IV-E)
    eval_05_robustness.csv    guess/slip AND P_T sweeps      (§IV-G)
    eval_05_budget.csv        evidence-budget control
    eval_05_mixcurve.csv      deferral vs code-item share    (assessment-design result)
    eval_05_report.md         write-up
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import random
import sys
import zlib
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
OUT = os.path.join(ROOT, "IRL_extension_results")
os.makedirs(OUT, exist_ok=True)
sys.path.insert(0, os.path.join(ROOT, "backend"))

from app.core.bkt_model import (  # noqa: E402  — the REAL deployed estimator
    _bkt_step, EVIDENCE_CONFIG, THETA_CERTIFY, N_MIN,
)

TIERS = ["quiz", "micro", "code"]          # declarative / procedural / applied
TRUE_MASTERY_LEVEL = 0.80                  # construct definition of "genuine mastery"
ARMS = ["canonical", "pooled_n9", "conjunctive", "factored"]
ARM_LABEL = {"canonical":   "Canonical BKT (P≥θ only)",
             "pooled_n9":   "Pooled + N≥9 (quantity-matched)",
             "conjunctive": "Conjunctive KT [24] (∏P≥θ^K)",
             "factored":    "Factored conjunctive (ours)"}


def wilson(k: int, n: int) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    z = 1.959964
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = (z / den) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, c - h), min(1.0, c + h))


# ── learner archetypes ────────────────────────────────────────────────────────
def true_vector(profile: str, lam: float = 1.0, base: float = 0.92) -> dict:
    """Latent per-tier true competence. lam = lopsidedness ∈ [0,1]."""
    if profile == "master":            # genuinely competent, balanced
        return {"quiz": base, "micro": base, "code": base}
    if profile == "lopsided":          # strong recall, weak application (the EDP case)
        return {"quiz": base,
                "micro": max(0.02, base - lam * 0.70),
                "code":  max(0.02, base - lam * 0.87)}
    if profile == "inverse_lopsided":  # strong application, weak recall — symmetry check
        return {"quiz": max(0.02, base - lam * 0.87),
                "micro": max(0.02, base - lam * 0.70),
                "code":  base}
    if profile == "moderate":          # boundary case: mediocre everywhere
        return {"quiz": 0.65, "micro": 0.65, "code": 0.65}
    if profile == "weak":              # uniformly not ready
        return {"quiz": 0.25, "micro": 0.20, "code": 0.15}
    raise ValueError(profile)


def is_true_master(tv: dict) -> bool:
    return all(tv[k] >= TRUE_MASTERY_LEVEL for k in TIERS)


def _pseed(prof: str) -> int:
    """Deterministic per-archetype seed offset. Python's built-in hash() is salted per
    process (PYTHONHASHSEED), which made archetype numbers non-reproducible run to run —
    the paper figures were bouncing (lopsided false-cert 0/1/2/4 across runs). CRC32 is
    stable across processes."""
    return zlib.crc32(prof.encode()) % 1000


# ── the four competing certifiers ────────────────────────────────────────────
class FactoredModel:
    """Deployed model: one posterior per tier, conjunctive certification (Eq. 9)."""

    def __init__(self, theta: float, n_min: int, p_t: float | None = None):
        self.theta, self.n_min = theta, n_min
        self.cfg = {}
        for k in TIERS:
            c = dict(EVIDENCE_CONFIG[k])
            if p_t is not None:
                c["P_T"] = p_t
            self.cfg[k] = c
        self.p = {k: EVIDENCE_CONFIG[k]["P_L0"] for k in TIERS}
        self.n = {k: 0 for k in TIERS}

    def observe(self, tier, correct):
        self.p[tier] = _bkt_step(self.p[tier], correct, self.cfg[tier])
        if correct:
            self.n[tier] += 1

    def certified(self):
        return (all(self.p[k] >= self.theta for k in TIERS) and
                all(self.n[k] >= self.n_min for k in TIERS))


class ConjunctiveModel:
    """Conjunctive knowledge tracing [24] — the multi-dimensional BASELINE.

    Tracks one posterior per tier, exactly like the factored model, so it is not a scalar
    strawman. The two arms differ only in the certification rule, which is precisely the
    reviewer's question:

        Conjunctive [24]:  ∏_k P̃(k) ≥ θ^K      — AND-gate / product rule, no evidence count
        Factored (ours):   min_k P̃(k) ≥ θ  AND  N(k) ≥ N_min   — non-compensatory + counted

    The product is only PARTIALLY non-compensatory (it is log-additive): a very high quiz
    and micro posterior can pull the product over threshold while the code posterior sits
    below θ — the exact compensation the `min` rule forbids. Any gap between this arm and
    the factored arm therefore isolates the two design choices that are actually ours:
    the non-compensatory minimum, and the per-tier evidence floor N_min.

    Threshold θ^K is the product value when every tier sits exactly at θ, so the two rules
    coincide at that symmetric operating point — the comparison is not rigged by the
    threshold. No per-tier N_min is imposed, because that guard is part of the proposed
    rule, not of the [24] baseline; adding it would make this the factored rule.
    """

    def __init__(self, theta: float, p_t: float | None = None):
        self.theta_conj = theta ** len(TIERS)
        self.cfg = {}
        for k in TIERS:
            c = dict(EVIDENCE_CONFIG[k])
            if p_t is not None:
                c["P_T"] = p_t
            self.cfg[k] = c
        self.p = {k: EVIDENCE_CONFIG[k]["P_L0"] for k in TIERS}
        self.n = {k: 0 for k in TIERS}

    def observe(self, tier, correct):
        self.p[tier] = _bkt_step(self.p[tier], correct, self.cfg[tier])
        if correct:
            self.n[tier] += 1

    def certified(self):
        prod = 1.0
        for k in TIERS:
            prod *= self.p[k]
        return prod >= self.theta_conj


class PooledModel:
    """Single-posterior BKT over all evidence, with a configurable evidence floor.

    n_min_total = 0  -> CANONICAL BKT (Corbett & Anderson 1995): mastery declared purely
                        on the posterior crossing θ, no evidence-count rule.
    n_min_total = 9  -> quantity-matched control: same total evidence as the factored
                        rule, still no tier structure.

    Guess/slip/prior are the evidence-weighted pooled means of the tier parameters, so
    the baseline is not handicapped — it simply cannot tell a quiz answer from a code
    submission.
    """

    def __init__(self, theta, n_min_total, mix, p_t: float | None = None):
        self.theta, self.n_min_total = theta, n_min_total
        self.cfg = {
            "P_G":  sum(mix[k] * EVIDENCE_CONFIG[k]["P_G"] for k in TIERS),
            "P_S":  sum(mix[k] * EVIDENCE_CONFIG[k]["P_S"] for k in TIERS),
            "P_T":  p_t if p_t is not None else
                    sum(mix[k] * EVIDENCE_CONFIG[k]["P_T"] for k in TIERS),
            "P_L0": sum(mix[k] * EVIDENCE_CONFIG[k]["P_L0"] for k in TIERS),
        }
        self.p = self.cfg["P_L0"]
        self.n = 0

    def observe(self, tier, correct):
        self.p = _bkt_step(self.p, correct, self.cfg)
        if correct:
            self.n += 1

    def certified(self):
        return self.p >= self.theta and self.n >= self.n_min_total


# ── one simulated learner, all four arms on the SAME stream ──────────────────
def run_learner(tv, mix, n_ops, theta, n_min, jitter, rng, p_t=None) -> dict:
    models = {
        "canonical":   PooledModel(theta, 0, mix, p_t),
        "pooled_n9":   PooledModel(theta, n_min * len(TIERS), mix, p_t),
        "conjunctive": ConjunctiveModel(theta, p_t),
        "factored":    FactoredModel(theta, n_min, p_t),
    }
    tier_pool = [t for t in TIERS for _ in range(max(1, int(round(mix[t] * 100))))]
    at = {a: None for a in ARMS}
    viol = 0

    for step in range(1, n_ops + 1):
        tier = rng.choice(tier_pool)
        c = EVIDENCE_CONFIG[tier]
        # generator guess/slip may be perturbed away from the models' (mismatch)
        p_g = min(0.95, max(0.0, c["P_G"] * (1 + rng.uniform(-jitter, jitter))))
        p_s = min(0.95, max(0.0, c["P_S"] * (1 + rng.uniform(-jitter, jitter))))
        correct = rng.random() < (tv[tier] * (1 - p_s) + (1 - tv[tier]) * p_g)

        for a, mdl in models.items():
            mdl.observe(tier, correct)
            if at[a] is None and mdl.certified():
                at[a] = step
        f = models["factored"]
        if at["factored"] == step:      # implementation-fidelity check on Eq. (9)
            if any(f.n[k] < n_min for k in TIERS) or any(f.p[k] < theta for k in TIERS):
                viol += 1

    truth = is_true_master(tv)
    out = {"true_master": truth, "theorem_violations": viol}
    for a in ARMS:
        out[f"{a}_cert"] = at[a] is not None
        out[f"{a}_at"] = at[a]
        out[f"{a}_false_cert"] = (at[a] is not None) and not truth
        out[f"{a}_deferred"] = (at[a] is None) and truth
    return out


def cohort(profile, lam, n, mix, n_ops, theta, n_min, jitter, seed, p_t=None) -> dict:
    rng = random.Random(seed)
    tv = true_vector(profile, lam)
    rows = [run_learner(tv, mix, n_ops, theta, n_min, jitter, rng, p_t) for _ in range(n)]
    n_true = sum(r["true_master"] for r in rows)
    n_false = len(rows) - n_true
    res = {"profile": profile, "lambda": round(lam, 3), "n": n,
           "true_master": bool(n_true), "theorem_violations": sum(r["theorem_violations"] for r in rows)}
    for a in ARMS:
        k_cert = sum(r[f"{a}_cert"] for r in rows)
        k_false = sum(r[f"{a}_false_cert"] for r in rows)
        k_def = sum(r[f"{a}_deferred"] for r in rows)
        ats = [r[f"{a}_at"] for r in rows if r[f"{a}_at"]]
        res[f"{a}_cert_rate"] = round(k_cert / len(rows), 4)
        res[f"{a}_false_k"] = k_false
        res[f"{a}_false_n"] = n_false
        res[f"{a}_false_rate"] = round(k_false / n_false, 4) if n_false else None
        res[f"{a}_defer_k"] = k_def
        res[f"{a}_defer_n"] = n_true
        res[f"{a}_defer_rate"] = round(k_def / n_true, 4) if n_true else None
        res[f"{a}_mean_ops"] = round(sum(ats) / len(ats), 1) if ats else None
    return res


def write_csv(path, rows):
    if not rows:
        return
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--ops", type=int, default=60)
    ap.add_argument("--mix", default="0.6,0.3,0.1")
    ap.add_argument("--theta", type=float, default=THETA_CERTIFY)
    ap.add_argument("--n-min", type=int, default=N_MIN)
    ap.add_argument("--jitter", type=float, default=0.0)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    parts = [float(x) for x in args.mix.split(",")]
    mix = dict(zip(TIERS, [p / sum(parts) for p in parts]))
    C = dict(mix=mix, n_ops=args.ops, theta=args.theta, n_min=args.n_min,
             jitter=args.jitter)

    print("=" * 92)
    print("IRL Eval 05 — Evidence Diversity: quantity vs diversity decomposition")
    print(f"estimator=REAL bkt_model._bkt_step · θ={args.theta} (Corbett & Anderson 1995)"
          f" · N_min={args.n_min}")
    print(f"mix={ {k: round(v,2) for k,v in mix.items()} } · {args.n}/cell · "
          f"{args.ops} ops · seed={args.seed}")
    print("=" * 92)

    # ── Study A (§IV-C): archetypes × four arms ──────────────────────────────
    print("\n[A] Archetypes × four arms — certification rate")
    print(f"  {'archetype':<20}{'truth':>8}{'canonical':>11}{'pooled_n9':>11}{'conjunctive':>12}{'factored':>10}")
    arche = []
    for prof in ("master", "lopsided", "inverse_lopsided", "moderate", "weak"):
        r = cohort(prof, 1.0, args.n, seed=args.seed + _pseed(prof), **C)
        arche.append(r)
        print(f"  {prof:<20}{('MASTER' if r['true_master'] else 'not'):>8}"
              f"{r['canonical_cert_rate']:>11.3f}{r['pooled_n9_cert_rate']:>11.3f}"
              f"{r['conjunctive_cert_rate']:>12.3f}{r['factored_cert_rate']:>10.3f}")
    write_csv(os.path.join(OUT, "eval_05_archetypes.csv"), arche)

    lop = next(r for r in arche if r["profile"] == "lopsided")
    print(f"\n  false certification on lopsided non-masters (raw k/n, Wilson 95%):")
    for a in ARMS:
        k, n = lop[f"{a}_false_k"], lop[f"{a}_false_n"]
        lo, hi = wilson(k, n)
        print(f"    {ARM_LABEL[a]:<34} {k:>5}/{n}  [{100*lo:.2f}%, {100*hi:.2f}%]")

    # ── Study B: lopsidedness sweep ───────────────────────────────────────────
    print("\n[B] Certification vs lopsidedness (lopsided profile)")
    sweep = []
    print(f"  {'lam':>5}{'truth':>8}{'canonical':>11}{'pooled_n9':>11}{'conjunctive':>12}{'factored':>10}")
    for i in range(11):
        lam = i / 10
        r = cohort("lopsided", lam, args.n, seed=args.seed + i, **C)
        sweep.append(r)
        print(f"  {lam:>5.1f}{('MASTER' if r['true_master'] else 'not'):>8}"
              f"{r['canonical_cert_rate']:>11.3f}{r['pooled_n9_cert_rate']:>11.3f}"
              f"{r['conjunctive_cert_rate']:>12.3f}{r['factored_cert_rate']:>10.3f}")
    write_csv(os.path.join(OUT, "eval_05_sweep.csv"), sweep)

    # ── Study C (§IV-G): robustness — guess/slip AND P_T ──────────────────────
    print("\n[C] Robustness — guess/slip jitter, then P_T sweep")
    robust = []
    for j in (0.0, 0.15, 0.30, 0.45):
        for prof in ("lopsided", "master"):
            r = cohort(prof, 1.0, args.n, **{**C, "jitter": j},
                       seed=args.seed + int(j * 100))
            r["sweep"] = "guess_slip"; r["param"] = j
            robust.append(r)
    print(f"  {'P_T':>6}{'archetype':>12}{'canonical':>11}{'pooled_n9':>11}{'conjunctive':>12}{'factored':>10}")
    for p_t in (0.0, 0.03, 0.09, 0.15):
        for prof in ("lopsided", "weak", "master"):
            r = cohort(prof, 1.0, args.n, seed=args.seed + int(p_t * 1000), p_t=p_t, **C)
            r["sweep"] = "P_T"; r["param"] = p_t
            robust.append(r)
            print(f"  {p_t:>6.2f}{prof:>12}{r['canonical_cert_rate']:>11.3f}"
                  f"{r['pooled_n9_cert_rate']:>11.3f}{r['conjunctive_cert_rate']:>12.3f}"
                  f"{r['factored_cert_rate']:>10.3f}")
    write_csv(os.path.join(OUT, "eval_05_robustness.csv"), robust)

    # ── Study D: evidence budget ──────────────────────────────────────────────
    print("\n[D] Evidence budget — is the effect the rule, or saturation?")
    budgets = []
    print(f"  {'ops':>5}{'canon_lop':>11}{'n9_lop':>9}{'fac_lop':>9}{'fac_defer_master':>18}")
    for ops in (15, 30, 60, 120):
        l = cohort("lopsided", 1.0, args.n, **{**C, "n_ops": ops}, seed=args.seed + ops)
        m = cohort("master", 1.0, args.n, **{**C, "n_ops": ops}, seed=args.seed + ops + 1)
        row = {"ops": ops,
               "canonical_lopsided": l["canonical_cert_rate"],
               "pooled_n9_lopsided": l["pooled_n9_cert_rate"],
               "factored_lopsided": l["factored_cert_rate"],
               "factored_master_defer": m["factored_defer_rate"]}
        budgets.append(row)
        print(f"  {ops:>5}{row['canonical_lopsided']:>11.3f}{row['pooled_n9_lopsided']:>9.3f}"
              f"{row['factored_lopsided']:>9.3f}{row['factored_master_defer']:>18.3f}")
    write_csv(os.path.join(OUT, "eval_05_budget.csv"), budgets)

    # ── Study E: deferral vs code-item share (assessment-design result) ───────
    print("\n[E] Deferral of TRUE masters vs share of code items in the curriculum")
    mixcurve = []
    print(f"  {'code share':>11}{'fac_master_cert':>17}{'fac_lop_false':>15}{'conj_lop_false':>16}")
    for code_share in (0.05, 0.10, 0.20, 0.30, 0.40, 0.50):
        rest = 1 - code_share
        m = {"quiz": rest * 0.667, "micro": rest * 0.333, "code": code_share}
        ms = cohort("master", 1.0, args.n, **{**C, "mix": m}, seed=args.seed + int(code_share * 100))
        lp = cohort("lopsided", 1.0, args.n, **{**C, "mix": m}, seed=args.seed + int(code_share * 100) + 1)
        row = {"code_share": code_share,
               "factored_master_cert": ms["factored_cert_rate"],
               "factored_master_defer": ms["factored_defer_rate"],
               "factored_lopsided_false": lp["factored_false_rate"],
               "conjunctive_lopsided_false": lp["conjunctive_false_rate"],
               "pooled_n9_lopsided_false": lp["pooled_n9_false_rate"]}
        mixcurve.append(row)
        print(f"  {code_share:>11.2f}{row['factored_master_cert']:>17.3f}"
              f"{(row['factored_lopsided_false'] or 0):>15.3f}"
              f"{(row['conjunctive_lopsided_false'] or 0):>16.3f}")
    write_csv(os.path.join(OUT, "eval_05_mixcurve.csv"), mixcurve)

    # ── Study F (§IV-E): error-class decomposition ────────────────────────────
    errors = []
    for prof in ("master", "lopsided", "inverse_lopsided", "moderate", "weak"):
        errors.append(cohort(prof, 1.0, args.n, seed=args.seed + 500 + _pseed(prof) % 100, **C))
    write_csv(os.path.join(OUT, "eval_05_errors.csv"), errors)

    viol = sum(r["theorem_violations"] for r in arche + sweep + robust + errors)
    print(f"\n[fidelity] Eq. (9) violations: {viol} (unit test — the code obeying its own "
          f"rule is implementation fidelity, NOT empirical support for Theorem 1)")

    # ── report ────────────────────────────────────────────────────────────────
    inv = next(r for r in arche if r["profile"] == "inverse_lopsided")
    mod = next(r for r in arche if r["profile"] == "moderate")
    mas = next(r for r in arche if r["profile"] == "master")
    wk = next(r for r in arche if r["profile"] == "weak")
    k, nn = lop["factored_false_k"], lop["factored_false_n"]
    lo, hi = wilson(k, nn)

    R = ["# Evidence Diversity / Over-Crediting Resistance — Results", "",
         f"_Generated {datetime.now().isoformat(timespec='seconds')} · {args.n} learners/cell "
         f"· {args.ops} interactions · seed={args.seed} · θ={args.theta}, N_min={args.n_min} "
         f"· mix quiz/micro/code = {'/'.join(str(round(mix[k],2)) for k in TIERS)}_", "",
         "Simulation against the **deployed** estimator (`bkt_model._bkt_step`, "
         "`EVIDENCE_CONFIG`) — not a re-implementation. All arms receive the identical "
         "evidence stream.", "",
         "## The four arms (quantity, diversity, and an existing multi-dimensional model)", "",
         "| Arm | Rule | Isolates |",
         "|---|---|---|",
         "| Canonical BKT | P̃ ≥ θ only, no evidence count | Corbett & Anderson (1995) baseline |",
         "| Pooled + N≥9 | P̃ ≥ θ and 9 observations | adds **quantity** (scalar) |",
         "| Conjunctive KT [24] | ∏ₖ P̃⁽ᵏ⁾ ≥ θ^K, per-tier, no evidence count | existing **multi-dimensional** model |",
         "| Factored (ours) | ∀k: P̃⁽ᵏ⁾ ≥ θ and N⁽ᵏ⁾ ≥ 3 | non-compensatory **min** + per-tier evidence |", "",
         "Two controls matter. **`Pooled + N≥9`** demands the same total evidence as the "
         "factored rule but cannot distinguish tiers — any gap over it is diversity, not "
         "volume. **`Conjunctive KT [24]`** is the multi-dimensional baseline a reviewer "
         "will ask for: it tracks a posterior *per tier* exactly as the factored model "
         "does, so it is not a scalar strawman. The two differ only in the certification "
         "rule — a product (AND-gate) versus a per-tier minimum plus evidence floor — so "
         "any gap between them isolates the two design choices that are actually ours: the "
         "non-compensatory `min` (the product is log-additive and lets a high quiz/micro "
         "posterior partially mask a sub-θ code posterior) and the per-tier count N_min. "
         "θ^K = θ³ is the product at the operating point where every tier sits exactly at "
         "θ, so the two rules coincide there and the threshold does not rig the comparison.",
         "",
         "## Table 1 — Archetypes × arms (§IV-C headline)", "",
         "| Archetype | truly a master? | Canonical | Pooled N≥9 | Conjunctive [24] | **Factored** |",
         "|---|---|---:|---:|---:|---:|"]
    for r in arche:
        R.append(f"| {r['profile']} | {'yes' if r['true_master'] else 'no'} | "
                 f"{r['canonical_cert_rate']:.1%} | {r['pooled_n9_cert_rate']:.1%} | "
                 f"{r['conjunctive_cert_rate']:.1%} | "
                 f"**{r['factored_cert_rate']:.1%}** |")
    ck, cnn = lop["conjunctive_false_k"], lop["conjunctive_false_n"]
    clo, chi = wilson(ck, cnn)
    mas_row = next(r for r in arche if r["profile"] == "master")
    conj_mas, fac_mas = mas_row["conjunctive_cert_rate"], mas_row["factored_cert_rate"]
    scalar_fail = max(lop["canonical_false_k"], lop["pooled_n9_false_k"])
    R += ["",
          f"False certification of lopsided non-masters, factored arm: "
          f"**{k}/{nn}**, Wilson 95% [{100*lo:.2f}%, {100*hi:.2f}%]. Report the raw count.", "",
          "## Conjunctive KT [24] — the multi-dimensional baseline (the reviewer's arm)", "",
          "The arm that answers *\"why isn't an existing multi-dimensional model a "
          "baseline?\"*. It tracks a per-tier posterior identically to the factored model, "
          "so any difference is the certification rule alone — a product (AND-gate) versus "
          "a per-tier minimum plus evidence floor.", "",
          "| Arm | Lopsided false-cert | True-master cert |",
          "|---|---:|---:|",
          f"| Canonical / Pooled (scalar) | {scalar_fail}/{cnn} (~{100*scalar_fail/cnn:.0f}%) | 100% |",
          f"| Conjunctive KT [24] (∏P ≥ θ^K) | **{ck}/{cnn}** [{100*clo:.2f}, {100*chi:.2f}%] | {conj_mas:.1%} |",
          f"| Factored (ours, min + N_min) | **{k}/{nn}** [{100*lo:.2f}, {100*hi:.2f}%] | {fac_mas:.1%} |", "",
          "**Read this honestly — the reviewer's arm changes the claim, and for the "
          "better.** The dominant effect is dimensionality, not our specific rule: both "
          f"multi-dimensional arms drive lopsided false-certification from ~{100*scalar_fail/cnn:.0f}% "
          f"(scalar) to ≈0%, and the gap *between* conjunctive KT and the factored rule on "
          f"this metric is {ck}/{cnn} vs {k}/{cnn} — statistically indistinguishable. The "
          "factored rule's separable difference is that it is uniformly more "
          f"**conservative**: it certifies true masters at {fac_mas:.1%} against conjunctive "
          f"KT's {conj_mas:.1%}, i.e. it trades a few more safe deferrals of genuine masters "
          "for marginally lower false-certification, because the `min` refuses the "
          "log-additive compensation the product allows and N_min refuses certification on "
          "a tier with too little evidence.", "",
          "The paper's claim must therefore be the precise one and **not** the loose "
          "\"factoring beats pooling\": a non-compensatory, evidence-counted certification "
          "rule (i) *eliminates* the Evidence Diversity Problem that scalar tracking fails "
          "outright, and (ii) against an existing multi-dimensional product-rule model, "
          "which also largely resists the EDP, is marginally and deliberately more "
          "conservative rather than dramatically more accurate. Claiming a large margin "
          "over [24] here would be unsupported.", "",
          "One honest negative: this experiment does **not** separately isolate the "
          "contribution of the per-tier evidence floor N_min. It was hypothesised to bite "
          "under sparse applied evidence, but the code-share study below shows the two "
          "multi-dimensional arms coincide there too — because a lopsided learner with few "
          "applied items also has low applied *competence*, so the applied posterior is "
          "sub-θ and both the product and the minimum reject on the posterior alone, before "
          "N_min is ever pivotal. Isolating N_min would require a distinct probe — a learner "
          "with genuinely high applied competence but very few applied observations — which "
          "is not among the current archetypes. As it stands, the measurable difference "
          "between conjunctive KT and the factored rule is the conservatism on true masters, "
          "not the evidence floor.", "",
          "## Theorem 1 — what actually realises it", "",
          f"Eq. (9) violations across all cells: **{viol}**. This is an "
          "**implementation-fidelity unit test** — Eq. (9) *is* the certification rule, so "
          "the code obeying it is not empirical support. What empirically realises "
          f"Theorem 1 is the false-certification count above ({k}/{nn}): lopsided evidence "
          "cannot certify.", "",
          "## §IV-E — error classes, not error rates", "",
          "The arms do not merely differ in accuracy; their errors fall in different "
          "classes. The factored model's errors land almost entirely on **true masters** "
          "and are **deferrals** (safe — ask for more evidence). The pooled arms' errors "
          "land on **non-masters** and are **false certifications** (unsafe — the gate "
          "opens on competence never demonstrated). See `eval_05_errors.csv`.", "",
          "## Symmetry and boundary archetypes", "",
          f"- **inverse_lopsided** (strong code, weak recall): factored certifies "
          f"{inv['factored_cert_rate']:.1%}. The rule is symmetric — it is not merely "
          "anti-quiz-gaming.",
          f"- **moderate** (~0.65 everywhere): factored {mod['factored_cert_rate']:.1%} vs "
          f"canonical {mod['canonical_cert_rate']:.1%} — the boundary case where "
          "over-deferral would show up.", "",
          "## Deferral is an assessment-design requirement, not an accuracy penalty", "",
          "Deferral of true masters is **a function of the item mix**, not a property of "
          "the rule. `eval_05_mixcurve.csv`:", "",
          "| code-item share | factored certifies true masters | defers | "
          "factored lopsided false-cert | conjunctive [24] lopsided false-cert |",
          "|---:|---:|---:|---:|---:|"]
    for r in mixcurve:
        ff = r['factored_lopsided_false'] or 0.0
        cf = r['conjunctive_lopsided_false'] or 0.0
        R.append(f"| {r['code_share']:.0%} | {r['factored_master_cert']:.1%} | "
                 f"{r['factored_master_defer']:.1%} | {ff:.1%} | {cf:.1%} |")
    R += ["",
          "The last two columns were included to test whether **N_min** leaves a separable "
          "footprint. It does not: conjunctive KT and the factored rule track together at "
          "≈0% across every code-item share. The reason is structural — a lopsided "
          "non-master with few applied items also has low applied competence, so the applied "
          "posterior never rises enough for the evidence count to become the deciding "
          "factor. This is a genuine null: on the current archetypes the factored rule's "
          "advantage over the [24] baseline is its conservatism on true masters, and N_min "
          "is not independently demonstrated. Report it as such rather than asserting the "
          "floor does work the data does not show."]
    R += ["",
          "Where the curriculum actually assesses all three tiers, true masters certify at "
          "the high end. The correct claim is therefore that conjunctive certification "
          "**imposes a requirement on assessment design** — a pedagogical implication — "
          "rather than costing accuracy.", "",
          "## Baseline parameterisation (state this in the paper)", "",
          "Pooled arms use evidence-weighted means over the item mix: "
          f"P_G={sum(mix[k]*EVIDENCE_CONFIG[k]['P_G'] for k in TIERS):.3f}, "
          f"P_S={sum(mix[k]*EVIDENCE_CONFIG[k]['P_S'] for k in TIERS):.3f}, "
          f"P_L0={sum(mix[k]*EVIDENCE_CONFIG[k]['P_L0'] for k in TIERS):.3f}, "
          f"P_T={sum(mix[k]*EVIDENCE_CONFIG[k]['P_T'] for k in TIERS):.3f}. "
          "θ = 0.95 for all arms (Corbett & Anderson 1995).", "",
          "## Caveats", "",
          "- Simulated learners have **static** competence but all models apply P_T > 0, "
          "so posteriors drift upward over long streams. This is the known BKT pathology "
          "behind high certification on weak learners; `eval_05_robustness.csv` sweeps "
          "P_T ∈ {0, 0.03, 0.09, 0.15} rather than assuming the deployed value.",
          "- Simulation validates the **mechanism**, not the parameter values.",
          "- θ = 0.95 is externally sourced (Corbett & Anderson 1995) and, in the deployed "
          "code, a floor the calibrator may only raise — so these results are a lower "
          "bound on deployed resistance."]
    with open(os.path.join(OUT, "eval_05_report.md"), "w") as f:
        f.write("\n".join(R))
    print("\nWrote eval_05_archetypes/sweep/robustness/budget/mixcurve/errors.csv + report.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
