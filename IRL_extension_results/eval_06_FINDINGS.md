# Mastery-Conditioned Response Adaptation — Findings

**Does the tutor give different answers to students of different proficiency, and can that difference be detected by someone who does not know the student?**

Yes to both. Six synthetic learners, one 20-question set, 120 responses, one live pipeline.

| | |
|---|---|
| Headline | Π-fidelity **83.3%** [95% CI 75.7, 88.9], κ = 0.767, chance 25%, n = 120 |
| Policy assigns the correct level | **100%** (6/6 archetypes, 120/120 turns) |
| Protocol contamination | **0/120** |
| Weak-tier identification | **100%**, varies `code` ↔ `quiz` |
| Tier *steering* | **not supported** |

---

## 1. What was tested

Six learners were written directly into `user_knowledge`, differing only in their three-tier BKT state. Each was asked the same 20 questions about pointers, in a fresh session per question, through the live system.

| archetype | quiz / micro / code | n per tier | certified | level assigned |
|---|---|---|---|---|
| `novice` | 0.30 / 0.05 / 0.01 | 0,0,0 | no | novice |
| `developing` | 0.72 / 0.55 / 0.40 | 3,2,2 | no | developing |
| `proficient` | 0.90 / 0.86 / 0.82 | 4,4,4 | no | proficient |
| `reviewing` | 0.96 / 0.96 / 0.96 | 6,6,6 | **yes** | reviewing |
| `lopsided` | 0.95 / 0.75 / 0.55 | 4,3,2 | no | developing |
| `inverse_lopsided` | 0.55 / 0.75 / 0.95 | 2,3,4 | no | developing |

`lopsided` and `inverse_lopsided` exist to test the Evidence Diversity claim. Their *pooled mean* is 0.75, which the old compensatory rule scored as PROFICIENT — less scaffolding for a student who cannot write the code. The conjunctive rule (`min` across tiers, mirroring Eq. 9) correctly assigns DEVELOPING.

---

## 2. The same question, four ways

**"Can you explain what a pointer is in C?"**

**NOVICE** — 316 words. Analogy-led, defines every term.
> In C, a variable is like a labeled box where you store a piece of data… Think of it like a scavenger hunt: if a normal variable is the treasure, a pointer is a piece of paper with the exact GPS coordinates of where that treasure is hidden.

**DEVELOPING** — 363 words. No analogy; traces the mechanism; owns a *Common Mistakes* section.
> A pointer is a memory address stored in a variable, but the power of pointers lies in the interaction between the address-of operator (`&`) and the dereference operator (`*`)… you are instructing the CPU to look at the address stored in `ptr` and perform a read or write directly at that memory location.

**PROFICIENT** — 258 words. No definition at all; opens on failure modes.
> Since you are comfortable with basic pointer mechanics, you should be aware of the risks regarding pointer arithmetic and memory safety. Accessing memory via an uninitialized pointer… results in undefined behavior, which often manifests as intermittent segmentation faults or silent data corruption.

**REVIEWING** — 165 words. Teaches nothing new; reminds.
> Remember that a pointer is simply a variable that stores the memory address of another variable… A common "gotcha" that often fades is the difference between the pointer's address and the value it points to.

Section structure differs by level, which is what makes the difference robust rather than stylistic:

```
novice      Explanation · Use Cases · Visual Model · Example from Class · Your Turn!
developing  Explanation · Common Mistakes · Worked Example · Challenge
proficient  Explanation · Example · Challenge
reviewing   Refresher · Quick Check
```

---

## 3. Measured differences

Mean per response (n = 20 per level; 60 for developing, which pools three archetypes).

| feature | novice | developing | proficient | reviewing |
|---|---|---|---|---|
| words | 310.9 | 306.4 | 236.2 | 151.8 |
| code density % | 33.9 | 41.9 | 61.4 | 17.1 |
| questions posed | 0.25 | 0.23 | 1.35 | 1.15 |
| analogy cues | **1.55** | **0.08** | 0.05 | 0.20 |
| edge-case cues | 0.05 | 0.35 | 1.15 | 0.75 |
| definition cues | 1.15 | 0.80 | 0.85 | 0.40 |

### Every adjacent pair separates

Mann–Whitney p-values. This matters more than an overall trend: a monotone trend can hold while two neighbouring levels are identical.

| feature | novice vs developing | developing vs proficient | proficient vs reviewing |
|---|---|---|---|
| words | 0.498 | **3.7e-10** | **1.9e-07** |
| code density | **1.0e-04** | **1.9e-09** | **6.3e-07** |
| questions posed | 0.938 | **1.4e-10** | 0.154 |
| analogy cues | **8.3e-12** | 0.635 | 0.164 |
| edge cues | **0.018** | **4.9e-04** | 0.291 |
| definition cues | 0.267 | 0.638 | **0.041** |

Each transition is carried by a *different* mechanism, which is the sign of four genuine treatments rather than one dial:

- **novice → developing**: the analogy disappears (1.55 → 0.08) and is replaced by a mechanism trace
- **developing → proficient**: length drops, code rises, interrogation begins (0.23 → 1.35 questions)
- **proficient → reviewing**: code collapses (61% → 17%) because reviewing teaches nothing new

### Ladder trend (novice → developing → proficient)

`reviewing` is excluded — it is a different delivery mode, not a fourth rung, so a trend spanning it would be uninterpretable.

| feature | Spearman ρ | p |
|---|---|---|
| code density | +0.686 | 3.4e-15 |
| analogy cues | −0.619 | 6.4e-12 |
| words | −0.549 | 3.4e-09 |
| questions posed | +0.539 | 7.3e-09 |
| edge cues | +0.461 | 1.4e-06 |
| definition cues | −0.060 | 0.553 (n.s.) |

Novice vs proficient, Holm-corrected across the six features: five of six survive (`n_words` Holm p = 2.3e-06; `n_analogy_cues` 4.8e-06; `code_density_pct` 4.8e-06; `n_questions_posed` 5.9e-06; `n_edge_cues` 6.5e-05). Only `n_definition_cues` does not.

---

## 4. The blind judge — the load-bearing result

The feature counts above are **partly circular**: the prompt template instructs "use one real-world analogy" at novice and "no analogies" at developing, and the analogy counter then reflects that. They measure template compliance.

The blind judge breaks the circularity. It is shown one response, told nothing about the student, given no information about section headers, and asked which of four *pedagogical treatments* it was written for.

**Π-fidelity = 83.3%** [75.7, 88.9], κ = 0.767, n = 120, chance 25%. Coverage 120/120 (no response was declined as unclear).

```
                        judged
assigned      novice  developing  proficient  reviewing   recall
novice            20           0           0          0     100%
developing         3          42          15          0      70%
proficient         1           0          19          0      95%
reviewing          1           0           0         19      95%
```

Judged-label distribution is balanced (25 / 42 / 34 / 19) — no single label absorbs the responses. 18 of 20 errors are between adjacent levels; mean ordinal distance 1.15.

### Where the errors concentrate

| archetype | judge accuracy |
|---|---|
| novice | 100% |
| proficient | 95% |
| reviewing | 95% |
| lopsided | 85% |
| developing | 70% |
| **inverse_lopsided** | **55%** |

`inverse_lopsided` (weak recall, strong code) is read as PROFICIENT 8 times in 20. A code-strong learner receives a code-heavy answer, which reads as advanced. **The developing/proficient boundary is where the policy is least sharp**, and it fails in a specific, explainable direction.

---

## 5. Evidence diversity in the adaptation policy

Offline, using the deployed BKT estimator: simulated evidence streams for five archetypes across four evidence budgets and eleven lopsidedness levels (10,800 states, no LLM).

*Harmful over-crediting* = the pooled mean assigns PROFICIENT — withdrawing scaffolding — to a learner not competent in all three tiers. Pooled promoting a genuine master slightly early is counted separately as benign.

| archetype (λ = 1.0) | harmful % | pooled → proficient | conjunctive → proficient |
|---|---|---|---|
| moderate | 10.5 | 29.3% | 18.8% |
| master | 0.0 | 86.3% | 74.5% |
| lopsided | 0.0 | 0.0% | 0.0% |
| inverse_lopsided | 0.0 | 0.0% | 0.0% |
| weak | 0.0 | 0.0% | 0.0% |

At full lopsidedness both rules agree, because the weak tiers sit near the posterior floor. The two rules can only diverge where the *mean* clears 0.75 while the *minimum* does not — which requires the sweep to locate:

| archetype | λ | harmful % | 95% CI |
|---|---|---|---|
| inverse_lopsided | 0.2 | **19.0** | [15.5, 23.1] |
| inverse_lopsided | 0.3 | 18.5 | [15.0, 22.6] |
| lopsided | 0.3 | 17.5 | [14.1, 21.5] |
| both | 1.0 | 0.0 | [0.0, 0.95] |

**Peak harm is at moderate, not extreme, lopsidedness.** Pooled mastery is safe on learners who are uniformly weak and safe on the extremely lopsided; it fails precisely where one strong tier is just enough to drag the mean over threshold.

This connects §III-F back to Theorem 1: pooled mastery does not only over-certify, it **under-scaffolds** — for exactly the learner the diversity guarantee was written to protect.

---

## 6. Weak-tier identification — validated

The lowest-posterior tier is named only when the spread between tiers is ≥ 0.20 and that tier has ≥ 1 observation.

| archetype | expected | observed | agreement |
|---|---|---|---|
| novice | (none) | (none) | 100% |
| developing | code | code | 100% |
| proficient | (none) | (none) | 100% |
| reviewing | (none) | (none) | 100% |
| lopsided | code | code | 100% |
| **inverse_lopsided** | **quiz** | **quiz** | 100% |

The gates are load-bearing. Without them an unconditional argmin returns `code` for every learner: `code` carries the lowest Cromwell prior (0.01 vs quiz 0.30), so an under-evidenced tier is weakest by construction. Measured median spread is 0.03 for a balanced learner versus 0.96 for a genuinely lopsided one. Ungated, the signal would track the model's parameters rather than the student.

The named tier **varies with the evidence** (`code` for quiz-strong learners, `quiz` for code-strong learners) and is **withheld when unsupported** (novice has no evidence; proficient is balanced).

---

## 7. Tier steering — NOT supported

Identification works. Steering does not.

`lopsided` and `inverse_lopsided` are both assigned DEVELOPING, so scaffolding level is held constant and the only difference is which tier the directive names. Any difference is attributable to steering alone.

Deterministic features: no separation on any of six (p = 0.16 to 0.95), with effect sizes near zero (code density 16.17 vs 15.62). A blind tier judge over 40 responses returned **identical** distributions for the two arms, χ² p = 1.0.

The directive was verified to reach the prompt with the correct tier named. The model receives it and does not act on it.

**Report weak-tier identification as validated and tier steering as not supported.** Do not report the "judge names the directed tier = 50%" statistic — the two arms are directed at different tiers, so a judge with a standing preference credits each arm for its share of that preference, and the rate exceeds chance with zero steering.

> Caveat: this was measured under the previous prompt templates, before the four-branch rewrite. It has not been re-measured on current responses.

---

## 8. Validity checks

Both ran before any outcome was computed. They ask *was the treatment administered, and was it the right one?* — not *did it work?*

**Classifier integrity — 100%.** For each archetype, the level assigned by the live system matches an independent transcription of the deployed rule, on all 20 turns. If this had failed, every number above would describe something other than Table II.

**Manipulation check — 0/120 contaminated.** The Socratic withholding gate answers "my records show you already mastered this — how do *you* think we should approach it?" instead of producing an adapted explanation. Seeding a knowledge row makes every non-`reviewing` learner "known" to that gate. In an earlier collection it fired on **55 of 120 turns** (39-word withholding prompts versus 252-word explanations), and those turns carried no treatment at all. Phrasing every question as an explicit request for an explanation stands the gate down. The current collection is clean.

---

## 9. Defects found and fixed

These were all discovered by the evaluation and are worth a sentence in the paper — particularly the first, which explains why Table II could not have been validated before.

1. **Adaptation was inert in production.** Entity resolution took the first fuzzy match in arbitrary Neo4j order, so "pointer" resolved to the section `Memory and Pointers`, which carries no BKT row; the parent lookup reached the graph root, and *every student was classified NOVICE*. 8 of 14 common concepts failed this way (`pointer`, `array`, `string`, `function`, `struct`, `scope`, `debugging`, `file`) — each resolving to the section heading containing its name. Now resolved by scoring all candidates. This also explains the NaN `mastery_level` in the earlier C-EduBench replay.

2. **The adaptation policy pooled the evidence tiers.** `avg_p = (p_quiz + p_micro + p_code)/3` is compensatory — the exact failure Theorem 1 removes from certification, reintroduced inside §III-F. Now conjunctive.

3. **The SRL confidence slider moved IRL scaffolding.** The classifier read `get_effective_mastery` (`P_eff = α·P_BKT + (1−α)·P_self`). Now reads the objective posterior.

4. **NOVICE and DEVELOPING were the same treatment.** The format template had three branches for four levels; the two shared one, producing identical section lists differing only in the closing header name. They were statistically indistinguishable on code density (p = 0.85), analogies (p = 0.72) and edge cases (p = 0.79). Four branches now, each owning at least one section no other level emits.

5. **Prose could not override structure.** The analogy instruction was hardcoded into the shared Explanation string, so `developing` produced analogies regardless of what `_MASTERY_INSTRUCTIONS` said. Level definitions were also written as a sliding scale ("maximum/moderate/light scaffolding") with DEVELOPING defined by negation — a model cannot act on a quantity it cannot observe. Each level now states a distinct *job*, what it may assume, and what it must not do.

---

## 10. Limitations

1. **Learner states are seeded, not accumulated.** This validates that the system responds correctly to a given proficiency. It does not validate that real students reach these states, or at what rates.
2. **One concept, one question set.** All 20 questions concern pointers. Adaptation may differ for topics with less curricular material available.
3. **Feature differences partly reflect template compliance.** The prompt instructs the behaviours being counted. The blind judge, not the feature table, carries the pedagogical claim.
4. **`developing` pools three archetypes** (n = 60 vs 20), so its variance reflects three seeded states. They are statistically indistinguishable from one another, which is itself the tier-steering null result.
5. **Arm A shares its archetype construct with eval_05**, so the two evaluations rest on the same assumptions and are not independent evidence of one another.
6. **Single LLM judge, no human validation** of the judging rubric.

---

## Provenance

| file | contents |
|---|---|
| `eval_06_policy.csv` | Arm A, 10,800 simulated states, both policies |
| `eval_06_policy_summary.csv` | Arm A summary, A1 archetypes + A2 sweep |
| `eval_06_responses.json` | all 120 responses with assigned level and weak tier |
| `eval_06_features.csv` | per-response deterministic features |
| `eval_06_judge.csv` | blind level-judge verdicts |
| `eval_06_judge_tier.csv` | blind tier-judge verdicts (previous templates) |
| `eval_06_report.md` | auto-generated report |

Reproduce: `--policy`, then `--seed`, `--collect --fresh`, `--score`, `--judge --fresh`, `--report`.
