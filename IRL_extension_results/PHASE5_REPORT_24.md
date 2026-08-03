# Phase 5 (addendum) — Factored certification vs. an existing multi-dimensional model [24]

_Run 2026-08-03. Simulation only (`eval_05_evidence_diversity.py`, deployed `_bkt_step`,
2000 learners/cell, 60 interactions, θ=0.95, N_min=3, mix quiz/micro/code = 0.6/0.3/0.1,
seed=7). No server, no judge. Numbers are reproducible — archetype seeding was switched
from Python's per-process `hash()` to CRC32 so figures are stable across runs._

Added in response to the review comment: *both comparison arms in the evidence-diversity
study are single-scalar, so the experiment never faces an existing multi-dimensional model;
conjunctive knowledge tracing [24] and Bayesian-network skill topologies [25] are in related
work — why is neither an arm?* This addendum adds **conjunctive KT [24]** as a fourth arm.
It shares per-tier posteriors with the factored model, so any difference is the
certification rule alone: a product (AND-gate) `∏ₖ P̃(k) ≥ θ^K` versus the factored
per-tier minimum with an evidence floor `∀k: P̃(k) ≥ θ ∧ N(k) ≥ N_min`. The product
threshold `θ^K = 0.95³ = 0.857` is the value at which every tier sits exactly at θ, so the
two rules coincide at that operating point and the threshold does not rig the comparison.

---

## The security axis

The security-relevant failure is a **false certification**: the mastery gate opens for a
learner who has not demonstrated competence across all three tiers — under-prepared learners
let past a boundary. A **deferral** (withholding certification from a genuine master) is the
safe error: recoverable with more evidence. "More secure" therefore means **lower
false-certification**, and — more strongly — a false-certification mode that is *closed by
construction* rather than merely rare.

---

## Table 1 — Certification rate by archetype (raw numbers)

2000 learners/cell. For non-master archetypes **lower is better** (any certification is a
false certification). For `master`, higher is better.

| Archetype | should certify? | Canonical (scalar) | Pooled N≥9 (scalar) | Conjunctive KT [24] (multi-dim) | **Factored (ours)** |
|---|---|---:|---:|---:|---:|
| master | **yes** | 100.0% | 100.0% | 77.9% | **73.8%** |
| lopsided (the EDP case) | no | 99.9% | 99.7% | 0.0% | **0.0%** |
| inverse_lopsided | no | 40.6% | 32.4% | 0.4% | **0.1%** |
| moderate (mediocre everywhere) | no | 100.0% | 99.9% | 45.0% | **35.6%** |
| weak | no | 48.3% | 40.0% | 0.1% | **0.1%** |

## Table 2 — The headline security metric: false-certification of lopsided non-masters

| Arm | False-certifications | Wilson 95% |
|---|---:|---|
| Canonical BKT (scalar) | 1998/2000 (99.9%) | [99.6%, 99.97%] |
| Pooled + N≥9 (scalar) | 1994/2000 (99.7%) | [99.3%, 99.9%] |
| Conjunctive KT [24] (multi-dim) | **0/2000 (0.0%)** | [0.00%, 0.19%] |
| **Factored (ours)** (multi-dim) | **0/2000 (0.0%)** | [0.00%, 0.19%] |

## Table 3 — Where the two multi-dimensional rules diverge: the boundary learner

The `moderate` archetype (mediocre ~0.65 on every tier) is the only case where the two
multi-dimensional rules separate materially. Both false-certify it (BKT posterior drift over
60 items), but the factored rule certifies **187 fewer** of 2000.

| Arm | Moderate false-cert | rate |
|---|---:|---:|
| Conjunctive KT [24] | 899/2000 | 45.0% |
| **Factored (ours)** | **712/2000** | **35.6%** |
| **difference** | **−187** | **−9.4 pts** |

---

## What the numbers say

**1. Against a single-axis (scalar) mastery model, both multi-dimensional rules are
transformative — and almost identical to each other.** The scalar arms false-certify
lopsided non-masters at ~100% (1994–1998/2000). Conjunctive KT [24] and the factored rule
both drive this to **0/2000**. On the pure Evidence Diversity Problem the two
multi-dimensional models are, for practical purposes, **tied**. The dominant effect is
dimensionality itself, which [24] shares.

**2. Where the two diverge, the factored rule is the more conservative — i.e. the more
secure.** It is never worse than [24] on false-certification on any archetype, and on the
**boundary** learner it false-certifies **187 fewer / 2000 (35.6% vs 45.0%, a 9.4-point
reduction)**. This is the compensation surface opening: the product rule lets three middling,
drifting posteriors *multiply* past threshold, whereas the `min` requires each tier to clear
0.95 independently.

**3. The security advantage is structural, not just a stricter dial.** Part of the 9.4-point
gap is that the factored rule sits at a more conservative operating point, and [24] could
raise its product threshold to match the *empirical* false-certification rate. What retuning
**cannot** fix is compensation itself: at any threshold, the product remains log-additive, so
a strong pair of tiers can always offset a weak third. The `min` rule forbids this **by
construction, at every threshold** — this is Theorem 1. That guarantee is the part of "more
secure" a reviewer cannot retune away.

**4. The cost, disclosed.** The factored rule is uniformly stricter, so it certifies genuine
masters ~4 points less often (73.8% vs 77.9%). In security terms this is a lower false-accept
rate bought with a higher false-reject (deferral) rate — and deferral is the safe,
recoverable error.

---

## The claim to make in the paper (security-axis framing)

> On the evidence-diversity axis, the factored conjunctive rule and an existing
> multi-dimensional model — conjunctive knowledge tracing [24] — are near-identical, and both
> eliminate the false-certification that single-scalar mastery tracking commits at ~100%
> (both reach 0/2000 on the lopsided case). The factored rule's distinction is on the
> **security axis**: its per-tier minimum is *non-compensatory by construction*, so no
> combination of strong tiers can certify a learner deficient in another — at any threshold —
> a guarantee the product-rule model of [24] cannot provide, since its certification is
> log-additive. Empirically this yields **equal-or-lower false-certification than [24] on
> every learner archetype**, and 9.4 points lower on the boundary (uniformly mediocre) case,
> at the cost of certifying true masters ~4 points less often. The factored rule is therefore
> not more *accurate* than [24] on the diversity problem — both solve it — but it is more
> *conservative*, and provably closes a compensation surface [24] leaves open.

**Do claim:** near-parity with [24] against the diversity problem; a structural
non-compensation guarantee [24] lacks; equal-or-lower false-certification on every archetype;
a more conservative/secure operating point (187 fewer false-certs on the boundary case).

**Do not claim:** a large empirical margin over [24] on the lopsided EDP case — there the two
are tied at 0/2000.

---

## Honest negative (carried from the main Phase 5 report)

This experiment does **not** independently isolate the per-tier evidence floor `N_min`. It
was expected to bite under sparse applied evidence, but a lopsided learner with few code
items also has low code *competence*, so the applied posterior is sub-θ and both rules reject
on the posterior before `N_min` is pivotal (see `eval_05_report.md`, code-share study — the
factored and conjunctive columns coincide at ≈0% across all code shares). Isolating `N_min`
would need a distinct probe: a learner with genuinely high applied competence but very few
applied observations. Not built. The demonstrated advantage over [24] is the non-compensatory
`min`, not the evidence floor.

---

## Reproduction

```
python IRL_extension_script/eval_05_evidence_diversity.py
```

The `ConjunctiveModel` arm and its analysis are in `eval_05_evidence_diversity.py`; full
per-archetype output in `IRL_extension_results/eval_05_report.md`; raw per-cell data in
`eval_05_archetypes.csv`, `eval_05_sweep.csv`, `eval_05_mixcurve.csv`. Bayesian-network skill
topology [25] was scoped as a second arm and not built; see the main report for why [24] is
the higher-value comparison. Figures are reproducible (CRC32 archetype seeding, fixed
`--seed`); expect ±1–2 false-certs of run-to-run noise on the near-zero cells.
