# Evidence Diversity / Over-Crediting Resistance — Results

_Generated 2026-08-03T00:03:44 · 2000 learners/cell · 60 interactions · seed=7 · θ=0.95, N_min=3 · mix quiz/micro/code = 0.6/0.3/0.1_

Simulation against the **deployed** estimator (`bkt_model._bkt_step`, `EVIDENCE_CONFIG`) — not a re-implementation. All arms receive the identical evidence stream.

## The four arms (quantity, diversity, and an existing multi-dimensional model)

| Arm | Rule | Isolates |
|---|---|---|
| Canonical BKT | P̃ ≥ θ only, no evidence count | Corbett & Anderson (1995) baseline |
| Pooled + N≥9 | P̃ ≥ θ and 9 observations | adds **quantity** (scalar) |
| Conjunctive KT [24] | ∏ₖ P̃⁽ᵏ⁾ ≥ θ^K, per-tier, no evidence count | existing **multi-dimensional** model |
| Factored (ours) | ∀k: P̃⁽ᵏ⁾ ≥ θ and N⁽ᵏ⁾ ≥ 3 | non-compensatory **min** + per-tier evidence |

Two controls matter. **`Pooled + N≥9`** demands the same total evidence as the factored rule but cannot distinguish tiers — any gap over it is diversity, not volume. **`Conjunctive KT [24]`** is the multi-dimensional baseline a reviewer will ask for: it tracks a posterior *per tier* exactly as the factored model does, so it is not a scalar strawman. The two differ only in the certification rule — a product (AND-gate) versus a per-tier minimum plus evidence floor — so any gap between them isolates the two design choices that are actually ours: the non-compensatory `min` (the product is log-additive and lets a high quiz/micro posterior partially mask a sub-θ code posterior) and the per-tier count N_min. θ^K = θ³ is the product at the operating point where every tier sits exactly at θ, so the two rules coincide there and the threshold does not rig the comparison.

## Table 1 — Archetypes × arms (§IV-C headline)

| Archetype | truly a master? | Canonical | Pooled N≥9 | Conjunctive [24] | **Factored** |
|---|---|---:|---:|---:|---:|
| master | yes | 100.0% | 100.0% | 77.9% | **73.8%** |
| lopsided | no | 99.9% | 99.7% | 0.0% | **0.0%** |
| inverse_lopsided | no | 40.6% | 32.4% | 0.4% | **0.1%** |
| moderate | no | 100.0% | 99.9% | 45.0% | **35.6%** |
| weak | no | 48.3% | 40.0% | 0.1% | **0.1%** |

False certification of lopsided non-masters, factored arm: **0/2000**, Wilson 95% [0.00%, 0.19%]. Report the raw count.

## Conjunctive KT [24] — the multi-dimensional baseline (the reviewer's arm)

The arm that answers *"why isn't an existing multi-dimensional model a baseline?"*. It tracks a per-tier posterior identically to the factored model, so any difference is the certification rule alone — a product (AND-gate) versus a per-tier minimum plus evidence floor.

| Arm | Lopsided false-cert | True-master cert |
|---|---:|---:|
| Canonical / Pooled (scalar) | 1998/2000 (~100%) | 100% |
| Conjunctive KT [24] (∏P ≥ θ^K) | **0/2000** [0.00, 0.19%] | 77.9% |
| Factored (ours, min + N_min) | **0/2000** [0.00, 0.19%] | 73.8% |

**Read this honestly — the reviewer's arm changes the claim, and for the better.** The dominant effect is dimensionality, not our specific rule: both multi-dimensional arms drive lopsided false-certification from ~100% (scalar) to ≈0%, and the gap *between* conjunctive KT and the factored rule on this metric is 0/2000 vs 0/2000 — statistically indistinguishable. The factored rule's separable difference is that it is uniformly more **conservative**: it certifies true masters at 73.8% against conjunctive KT's 77.9%, i.e. it trades a few more safe deferrals of genuine masters for marginally lower false-certification, because the `min` refuses the log-additive compensation the product allows and N_min refuses certification on a tier with too little evidence.

The paper's claim must therefore be the precise one and **not** the loose "factoring beats pooling": a non-compensatory, evidence-counted certification rule (i) *eliminates* the Evidence Diversity Problem that scalar tracking fails outright, and (ii) against an existing multi-dimensional product-rule model, which also largely resists the EDP, is marginally and deliberately more conservative rather than dramatically more accurate. Claiming a large margin over [24] here would be unsupported.

One honest negative: this experiment does **not** separately isolate the contribution of the per-tier evidence floor N_min. It was hypothesised to bite under sparse applied evidence, but the code-share study below shows the two multi-dimensional arms coincide there too — because a lopsided learner with few applied items also has low applied *competence*, so the applied posterior is sub-θ and both the product and the minimum reject on the posterior alone, before N_min is ever pivotal. Isolating N_min would require a distinct probe — a learner with genuinely high applied competence but very few applied observations — which is not among the current archetypes. As it stands, the measurable difference between conjunctive KT and the factored rule is the conservatism on true masters, not the evidence floor.

## Theorem 1 — what actually realises it

Eq. (9) violations across all cells: **0**. This is an **implementation-fidelity unit test** — Eq. (9) *is* the certification rule, so the code obeying it is not empirical support. What empirically realises Theorem 1 is the false-certification count above (0/2000): lopsided evidence cannot certify.

## §IV-E — error classes, not error rates

The arms do not merely differ in accuracy; their errors fall in different classes. The factored model's errors land almost entirely on **true masters** and are **deferrals** (safe — ask for more evidence). The pooled arms' errors land on **non-masters** and are **false certifications** (unsafe — the gate opens on competence never demonstrated). See `eval_05_errors.csv`.

## Symmetry and boundary archetypes

- **inverse_lopsided** (strong code, weak recall): factored certifies 0.1%. The rule is symmetric — it is not merely anti-quiz-gaming.
- **moderate** (~0.65 everywhere): factored 35.6% vs canonical 100.0% — the boundary case where over-deferral would show up.

## Deferral is an assessment-design requirement, not an accuracy penalty

Deferral of true masters is **a function of the item mix**, not a property of the rule. `eval_05_mixcurve.csv`:

| code-item share | factored certifies true masters | defers | factored lopsided false-cert | conjunctive [24] lopsided false-cert |
|---:|---:|---:|---:|---:|
| 5% | 34.1% | 66.0% | 0.0% | 0.0% |
| 10% | 75.7% | 24.3% | 0.0% | 0.1% |
| 20% | 97.4% | 2.6% | 0.1% | 0.1% |
| 30% | 98.5% | 1.5% | 0.0% | 0.1% |
| 40% | 97.2% | 2.8% | 0.1% | 0.3% |
| 50% | 93.8% | 6.2% | 0.1% | 0.1% |

The last two columns were included to test whether **N_min** leaves a separable footprint. It does not: conjunctive KT and the factored rule track together at ≈0% across every code-item share. The reason is structural — a lopsided non-master with few applied items also has low applied competence, so the applied posterior never rises enough for the evidence count to become the deciding factor. This is a genuine null: on the current archetypes the factored rule's advantage over the [24] baseline is its conservatism on true masters, and N_min is not independently demonstrated. Report it as such rather than asserting the floor does work the data does not show.

Where the curriculum actually assesses all three tiers, true masters certify at the high end. The correct claim is therefore that conjunctive certification **imposes a requirement on assessment design** — a pedagogical implication — rather than costing accuracy.

## Baseline parameterisation (state this in the paper)

Pooled arms use evidence-weighted means over the item mix: P_G=0.155, P_S=0.125, P_L0=0.196, P_T=0.090. θ = 0.95 for all arms (Corbett & Anderson 1995).

## Caveats

- Simulated learners have **static** competence but all models apply P_T > 0, so posteriors drift upward over long streams. This is the known BKT pathology behind high certification on weak learners; `eval_05_robustness.csv` sweeps P_T ∈ {0, 0.03, 0.09, 0.15} rather than assuming the deployed value.
- Simulation validates the **mechanism**, not the parameter values.
- θ = 0.95 is externally sourced (Corbett & Anderson 1995) and, in the deployed code, a floor the calibrator may only raise — so these results are a lower bound on deployed resistance.