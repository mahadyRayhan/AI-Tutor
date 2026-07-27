# Evidence Diversity / Over-Crediting Resistance — Results

_Generated 2026-07-27T01:44:51 · 2000 learners/cell · 60 interactions · seed=7 · θ=0.95, N_min=3 · mix quiz/micro/code = 0.6/0.3/0.1_

Simulation against the **deployed** estimator (`bkt_model._bkt_step`, `EVIDENCE_CONFIG`) — not a re-implementation. All arms receive the identical evidence stream.

## The three arms (quantity vs diversity)

| Arm | Rule | Isolates |
|---|---|---|
| Canonical BKT | P̃ ≥ θ only, no evidence count | Corbett & Anderson (1995) baseline |
| Pooled + N≥9 | P̃ ≥ θ and 9 observations | adds **quantity** |
| Factored (ours) | ∀k: P̃⁽ᵏ⁾ ≥ θ and N⁽ᵏ⁾ ≥ 3 | adds **diversity** |

`Pooled + N≥9` is the control that matters: it demands the same total evidence as the factored rule but cannot distinguish tiers. Any gap between it and the factored arm is attributable to diversity, not volume.

## Table 1 — Archetypes × arms (§IV-C headline)

| Archetype | truly a master? | Canonical | Pooled N≥9 | **Factored** |
|---|---|---:|---:|---:|
| master | yes | 100.0% | 100.0% | **73.8%** |
| lopsided | no | 99.9% | 99.8% | **0.0%** |
| inverse_lopsided | no | 39.5% | 31.2% | **0.1%** |
| moderate | no | 100.0% | 100.0% | **35.4%** |
| weak | no | 47.6% | 38.5% | **0.0%** |

False certification of lopsided non-masters, factored arm: **0/2000**, Wilson 95% [0.00%, 0.19%]. Report the raw count.

## Theorem 1 — what actually realises it

Eq. (9) violations across all cells: **0**. This is an **implementation-fidelity unit test** — Eq. (9) *is* the certification rule, so the code obeying it is not empirical support. What empirically realises Theorem 1 is the false-certification count above (0/2000): lopsided evidence cannot certify.

## §IV-E — error classes, not error rates

The arms do not merely differ in accuracy; their errors fall in different classes. The factored model's errors land almost entirely on **true masters** and are **deferrals** (safe — ask for more evidence). The pooled arms' errors land on **non-masters** and are **false certifications** (unsafe — the gate opens on competence never demonstrated). See `eval_05_errors.csv`.

## Symmetry and boundary archetypes

- **inverse_lopsided** (strong code, weak recall): factored certifies 0.1%. The rule is symmetric — it is not merely anti-quiz-gaming.
- **moderate** (~0.65 everywhere): factored 35.4% vs canonical 100.0% — the boundary case where over-deferral would show up.

## Deferral is an assessment-design requirement, not an accuracy penalty

Deferral of true masters is **a function of the item mix**, not a property of the rule. `eval_05_mixcurve.csv`:

| code-item share | factored certifies true masters | defers |
|---:|---:|---:|
| 5% | 34.1% | 66.0% |
| 10% | 75.7% | 24.3% |
| 20% | 97.4% | 2.6% |
| 30% | 98.5% | 1.5% |
| 40% | 97.2% | 2.8% |
| 50% | 93.8% | 6.2% |

Where the curriculum actually assesses all three tiers, true masters certify at the high end. The correct claim is therefore that conjunctive certification **imposes a requirement on assessment design** — a pedagogical implication — rather than costing accuracy.

## Baseline parameterisation (state this in the paper)

Pooled arms use evidence-weighted means over the item mix: P_G=0.155, P_S=0.125, P_L0=0.196, P_T=0.090. θ = 0.95 for all arms (Corbett & Anderson 1995).

## Caveats

- Simulated learners have **static** competence but all models apply P_T > 0, so posteriors drift upward over long streams. This is the known BKT pathology behind high certification on weak learners; `eval_05_robustness.csv` sweeps P_T ∈ {0, 0.03, 0.09, 0.15} rather than assuming the deployed value.
- Simulation validates the **mechanism**, not the parameter values.
- θ = 0.95 is externally sourced (Corbett & Anderson 1995) and, in the deployed code, a floor the calibrator may only raise — so these results are a lower bound on deployed resistance.