# Eval 06 — Mastery-Conditioned Response Adaptation (Policy Pi, Table II)

_Generated 2026-07-28T02:23:47_

## What changed in the system

`_classify_mastery_level` previously pooled the three evidence tiers (`avg_p = (p_quiz + p_micro + p_code)/3`), which is compensatory: a strong quiz tier could mask a weak code tier and win the student *less* scaffolding. That is the same pooling failure Theorem 1 removes from certification. The deployed rule is now conjunctive (`min` over tiers), matching Eq. (9), and it reads the objective BKT posterior instead of the self-blended `P_eff`, so the SRL confidence slider can no longer move the amount of support a student receives.

## Arm A — policy disagreement (offline, deployed estimator)

`harmful_pct` counts states where the pooled mean says PROFICIENT — i.e. withdraws scaffolding — for a learner who is NOT competent in all three tiers. Pooled promoting a *genuine* master slightly early is a disagreement but not an error, so it is excluded from this count and visible instead in `disagreement_rate`.

### A1 — fixed archetypes (full lopsidedness)

| archetype        |   n_states |   true_master |   disagreement_rate |   harmful_over_credit |   harmful_pct |   wilson_lo_pct |   wilson_hi_pct |   pooled_proficient_pct |   conj_proficient_pct |
|:-----------------|-----------:|--------------:|--------------------:|----------------------:|--------------:|----------------:|----------------:|------------------------:|----------------------:|
| moderate         |        400 |             0 |              0.105  |                    42 |          10.5 |            7.86 |           13.89 |                   29.25 |                 18.75 |
| inverse_lopsided |        400 |             0 |              0      |                     0 |           0   |            0    |            0.95 |                    0    |                  0    |
| lopsided         |        400 |             0 |              0      |                     0 |           0   |            0    |            0.95 |                    0    |                  0    |
| master           |        400 |             1 |              0.1175 |                     0 |           0   |            0    |            0.95 |                   86.25 |                 74.5  |
| weak             |        400 |             0 |              0      |                     0 |           0   |            0    |            0.95 |                    0    |                  0    |

### A2 — lopsidedness sweep

At full lopsidedness (lam=1) the weak tiers sit near the posterior floor and both rules agree on DEVELOPING, so A1 alone would wrongly suggest the two policies never diverge. The two rules can only differ in the band where the mean clears 0.75 while the minimum does not, and locating that band requires the sweep.

| archetype        |   lam |   n_states |   disagreement_rate |   harmful_pct |   wilson_lo_pct |   wilson_hi_pct |   pooled_proficient_pct |   conj_proficient_pct |
|:-----------------|------:|-----------:|--------------------:|--------------:|----------------:|----------------:|------------------------:|----------------------:|
| inverse_lopsided |   0   |        400 |              0.1175 |          0    |            0    |            0.95 |                   84.5  |                 72.75 |
| inverse_lopsided |   0.1 |        400 |              0.1625 |          0    |            0    |            0.95 |                   76.5  |                 60.25 |
| inverse_lopsided |   0.2 |        400 |              0.19   |         19    |           15.46 |           23.13 |                   64.25 |                 45.25 |
| inverse_lopsided |   0.3 |        400 |              0.185  |         18.5  |           15    |           22.6  |                   47    |                 28.5  |
| inverse_lopsided |   0.4 |        400 |              0.1775 |         17.75 |           14.32 |           21.8  |                   35.75 |                 18    |
| inverse_lopsided |   0.5 |        400 |              0.1125 |         11.25 |            8.51 |           14.72 |                   19.5  |                  8.25 |
| inverse_lopsided |   0.6 |        400 |              0.085  |          8.5  |            6.15 |           11.64 |                   11.25 |                  2.75 |
| inverse_lopsided |   0.7 |        400 |              0.0275 |          2.75 |            1.54 |            4.86 |                    3.75 |                  1    |
| inverse_lopsided |   0.8 |        400 |              0.005  |          0.5  |            0.14 |            1.8  |                    0.75 |                  0.25 |
| inverse_lopsided |   0.9 |        400 |              0.0025 |          0.25 |            0.04 |            1.4  |                    0.25 |                  0    |
| inverse_lopsided |   1   |        400 |              0      |          0    |            0    |            0.95 |                    0    |                  0    |
| lopsided         |   0   |        400 |              0.0925 |          0    |            0    |            0.95 |                   86    |                 76.75 |
| lopsided         |   0.1 |        400 |              0.1475 |          0    |            0    |            0.95 |                   76.5  |                 61.75 |
| lopsided         |   0.2 |        400 |              0.145  |         14.5  |           11.39 |           18.29 |                   66    |                 51.5  |
| lopsided         |   0.3 |        400 |              0.175  |         17.5  |           14.09 |           21.53 |                   55    |                 37.5  |
| lopsided         |   0.4 |        400 |              0.12   |         12    |            9.17 |           15.55 |                   40.25 |                 28.25 |
| lopsided         |   0.5 |        400 |              0.14   |         14    |           10.94 |           17.74 |                   29.75 |                 15.75 |
| lopsided         |   0.6 |        400 |              0.0625 |          6.25 |            4.27 |            9.06 |                   15.5  |                  9.25 |
| lopsided         |   0.7 |        400 |              0.055  |          5.5  |            3.66 |            8.19 |                    7.75 |                  2.25 |
| lopsided         |   0.8 |        400 |              0.01   |          1    |            0.39 |            2.54 |                    1.25 |                  0.25 |
| lopsided         |   0.9 |        400 |              0.0025 |          0.25 |            0.04 |            1.4  |                    0.25 |                  0    |
| lopsided         |   1   |        400 |              0      |          0    |            0    |            0.95 |                    0    |                  0    |

Peak harm is at **moderate** lopsidedness, not extreme: `inverse_lopsided` at lam=0.2, where the pooled mean withdraws scaffolding from **19.0%** of non-masters [95% CI 15.5, 23.1], against 0.0% at lam=1.0. The pooled rule is safe on learners who are obviously weak everywhere and safe on the extremely lopsided; it fails precisely where one strong tier is just enough to drag the mean over threshold.

This is what connects Sec. III-F back to Theorem 1: pooled mastery does not only over-certify, it under-scaffolds — for exactly the learner the diversity guarantee was written to protect.

## Arm B — protocol validity

Two checks run before any outcome is computed. They answer *was the treatment administered, and was it the right one?* — not *did it work?*

| archetype | seeded state | expected level | assigned level | agreement |
|---|---|---|---|---|
| `novice` | 0.30/0.05/0.01, n=(0, 0, 0), cert=0 | novice | novice | 100% |
| `developing` | 0.72/0.55/0.40, n=(3, 2, 2), cert=0 | developing | developing | 100% |
| `proficient` | 0.90/0.86/0.82, n=(4, 4, 4), cert=0 | proficient | proficient | 100% |
| `reviewing` | 0.96/0.96/0.96, n=(6, 6, 6), cert=1 | reviewing | reviewing | 100% |
| `lopsided` | 0.95/0.75/0.55, n=(4, 3, 2), cert=0 | developing | developing | 100% |
| `inverse_lopsided` | 0.55/0.75/0.95, n=(2, 3, 4), cert=0 | developing | developing | 100% |

Manipulation check: **0/120** turns were answered by the Socratic withholding gate rather than the adaptation policy — the protocol is clean.

### Adjacent-level separation

| pair | separates on (p < 0.05) |
|---|---|
| novice vs developing | `code_density_pct`, `n_analogy_cues`, `n_edge_cues` |
| developing vs proficient | `n_words`, `code_density_pct`, `n_questions_posed`, `n_edge_cues` |
| proficient vs reviewing | `n_words`, `code_density_pct`, `n_definition_cues` |

### Weak-tier identification

The lowest-posterior tier is named only when the spread between tiers is material (>= 0.20) and that tier has at least one observation. Without those gates an unconditional argmin returns `code` for every learner, since `code` carries the lowest Cromwell prior (0.01 vs quiz 0.30) — the signal would track the parameters rather than the student.

| archetype | expected | observed | agreement |
|---|---|---|---|
| `novice` | (none) | (none) | 100% |
| `developing` | code | code | 100% |
| `proficient` | (none) | (none) | 100% |
| `reviewing` | (none) | (none) | 100% |
| `lopsided` | code | code | 100% |
| `inverse_lopsided` | quiz | quiz | 100% |

The named tier varies across archetypes (['code', 'quiz']), so it tracks the evidence rather than the tier priors.

### Tier steering — not supported

_Stale: `eval_06_judge_tier.csv` was judged on a different question set (20 of 20 questions are not in the current collection), so it describes responses generated before the current prompt templates. NOT rendered. The result under the previous templates was chi2 p = 1.0. Re-run `--judge --fresh` without `--no-tier` to measure it on current responses._

The directive reaches the prompt and names the correct tier, but does not measurably change the response. Report weak-tier IDENTIFICATION as validated and tier STEERING as not supported.

## Arm C — response features by assigned level

| assigned_level   |   n_words |   code_density_pct |   n_questions_posed |   n_definition_cues |   n_analogy_cues |   n_edge_cues |
|:-----------------|----------:|-------------------:|--------------------:|--------------------:|-----------------:|--------------:|
| novice           |    310.85 |              33.89 |                0.25 |                1.15 |             1.55 |          0.05 |
| developing       |    306.42 |              41.94 |                0.23 |                0.8  |             0.08 |          0.35 |
| proficient       |    236.15 |              61.43 |                1.35 |                0.85 |             0.05 |          1.15 |
| reviewing        |    151.75 |              17.09 |                1.15 |                0.4  |             0.2  |          0.75 |

Monotone trend across the scaffolding ladder (novice -> developing -> proficient). `reviewing` is excluded: it is a different mode of delivery, not a fourth rung, so a trend spanning it would be uninterpretable.

| feature | rho | p |
|---|---|---|
| `n_words` | -0.549 | 3.356e-09 |
| `code_density_pct` | +0.686 | 3.442e-15 |
| `n_questions_posed` | +0.539 | 7.317e-09 |
| `n_definition_cues` | -0.060 | 0.5525 |
| `n_analogy_cues` | -0.619 | 6.388e-12 |
| `n_edge_cues` | +0.461 | 1.362e-06 |

Novice vs proficient, Holm-corrected across the six features:

| feature | novice | proficient | p | Holm-adj | |
|---|---|---|---|---|---|
| `n_words` | 310.85 | 236.15 | 3.9e-07 | 2.34e-06 | **survives** |
| `n_analogy_cues` | 1.55 | 0.05 | 9.603e-07 | 4.802e-06 | **survives** |
| `code_density_pct` | 33.89 | 61.43 | 1.101e-06 | 4.802e-06 | **survives** |
| `n_questions_posed` | 0.25 | 1.35 | 1.969e-06 | 5.907e-06 | **survives** |
| `n_edge_cues` | 0.05 | 1.15 | 3.245e-05 | 6.491e-05 | **survives** |
| `n_definition_cues` | 1.15 | 0.85 | 0.5032 | 0.5032 | n.s. |

## Arm C — Pi-fidelity (blind judge)

The judge could assign a level to 120/120 responses (100.0% coverage; 0 judged `unclear` and excluded). Among those, a judge shown one response and no student information recovers the assigned Table II level in **83.3%** of cases [95% CI 75.7, 88.9], against a 25% chance baseline (Cohen's kappa = 0.767).

Confusion matrix (rows = level the system assigned, columns = level the judge inferred):

| assigned_level   |   novice |   developing |   proficient |   reviewing |
|:-----------------|---------:|-------------:|-------------:|------------:|
| novice           |       20 |            0 |            0 |           0 |
| developing       |        3 |           42 |           15 |           0 |
| proficient       |        1 |            0 |           19 |           0 |
| reviewing        |        1 |            0 |            0 |          19 |

## Limitations

- Learner states are **seeded**, not accumulated through real study. This validates the policy mapping and the response adaptation; it does not validate that real students reach these states at the rates assumed.

- One concept (`Pointers`) and one question set. Adaptation strength may differ for topics with less curricular scaffolding available.

- Arm A compares two rules on simulated evidence streams; the archetype definitions are the same construct used in eval_05, so the two evaluations share their assumptions and are not independent evidence of each other.
