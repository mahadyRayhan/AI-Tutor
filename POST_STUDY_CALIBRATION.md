# Post-Study Calibration Guide
### What to Do After the 10-User, 4–6 Week Pilot Study

---

## Overview

This document describes the analysis pipeline to run after the pilot study concludes. The goals are:

1. **Calibrate** `θ_goal` using labeled interaction data
2. **Validate** `θ_mastery^(k)` against an external assessment
3. **Audit** misconception flags for correctness
4. **Inspect** BKT trajectories for gross parameter miscalibration
5. **Produce** the calibration and validation numbers needed for the journal paper

The study should produce approximately 250–500 labeled interactions across 10 users over 4–6 weeks. This is not enough for full EM re-estimation of BKT parameters, but it is enough for the steps below.

---

## Phase 0 — Before the Study Ends (Week 5–6)

### 0.1 Design the standardized assessment

Prepare a written C programming test that covers every concept tracked in the knowledge graph. The test must have three question types per concept, each targeting a distinct cognitive level:

| Type | Bloom's Level | Example for concept "Pointers" |
|---|---|---|
| Declarative (D) | Remember / Understand | "What does the `*` operator do when used in a declaration?" |
| Procedural (P) | Apply | "Write a function that swaps two integers using pointers." |
| Applied (A) | Analyze / Evaluate | "Identify the bug in this pointer-manipulation code snippet." |

Score each answer as 0 (wrong) or 1 (correct). Keep it separate from the tutoring system — students should complete it without AI assistance. Administer it in the final week before system access ends.

### 0.2 Label goal-alignment interactions

For each user, randomly sample 25–30 of their interactions from the logs. Present these to the user (or to yourself as domain expert) and label each as:

- `1` — this interaction is relevant to the student's stated learning goal
- `0` — this interaction is off-topic or misaligned with the goal

Target: ~250 total labeled pairs (10 users × 25 labels). These are used to calibrate `θ_goal`.

---

## Phase 1 — Data Extraction

### 1.1 Export interaction logs from SQLite

```sql
-- All BKT states per user per concept
SELECT username, concept, p_mastery_quiz, p_mastery_micro,
       p_mastery_code, p_mastery, timestamp
FROM user_knowledge
ORDER BY username, concept, timestamp;

-- SM-2 schedule state
SELECT username, concept, interval_days, ease_factor,
       due_date, review_count
FROM user_knowledge
WHERE review_count > 0;
```

### 1.2 Export learning profiles from user JSON

For each user, extract `learning_profile` JSON from the `users` table and parse:
- `misconceptions` list (concept, m_score, resolved, detected_at)
- `win_rates` per style (for UCB1 preference data)
- `frustration_level` history

### 1.3 Export goal alignment scores

Pull the `G_t` scores logged per interaction (stored in the audit log or interaction history). You need: `(username, interaction_id, G_t_score, human_label)` for the calibration step.

---

## Phase 2 — θ_goal Calibration

**Goal:** Find the threshold `θ_goal` that maximizes F1 on the labeled interaction pairs.

**Procedure:**

1. Build a dataset: `[(G_t_score, human_label), ...]` for all ~250 labeled pairs.
2. Sweep candidate thresholds from 0.30 to 0.90 in steps of 0.01.
3. For each candidate `θ`:
   - Predict `ŷ = 1` if `G_t ≥ θ`, else `0`
   - Compute precision, recall, F1 against the human labels
4. Select `θ_goal* = argmax_θ F1(θ)`.
5. Report: precision, recall, and F1 at `θ_goal*`, plus the distribution of G_t scores.

**Code skeleton (Python):**

```python
import numpy as np

scores = [...]   # G_t float values
labels = [...]   # 0 or 1 human labels

thresholds = np.arange(0.30, 0.91, 0.01)
best_f1, best_theta = 0, None

for theta in thresholds:
    preds = [1 if s >= theta else 0 for s in scores]
    tp = sum(p == 1 and l == 1 for p, l in zip(preds, labels))
    fp = sum(p == 1 and l == 0 for p, l in zip(preds, labels))
    fn = sum(p == 0 and l == 1 for p, l in zip(preds, labels))
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall    = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
    if f1 > best_f1:
        best_f1, best_theta = f1, theta

print(f"θ_goal* = {best_theta:.2f}, F1 = {best_f1:.3f}")
```

**Update in code:** Set the new `θ_goal` value in the security model configuration.

**Report in paper:** "θ_goal was calibrated on N={total} human-labeled interactions (10 users). The F1-maximizing threshold was θ_goal = {value}, achieving precision = {P}, recall = {R}, F1 = {F1}."

---

## Phase 3 — θ_mastery Validation

**Goal:** Check whether the system's mastery declarations match external assessment performance.

### 3.1 Build the comparison table

For each (user, concept) pair where the system declared mastery (`m_c = 1`) before the assessment, record the student's scores on the D, P, and A questions for that concept in the assessment.

| user | concept | mastery_declared | D_score | P_score | A_score | all_correct |
|---|---|---|---|---|---|---|
| u1 | Pointers | 1 | 1 | 1 | 0 | 0 |
| u1 | Variables | 1 | 1 | 1 | 1 | 1 |
| ... | | | | | | |

### 3.2 Compute precision and recall

- **Precision:** Of all (user, concept) pairs where `m_c = 1`, what fraction had `all_correct = 1` on the assessment?
- **Recall:** Of all (user, concept) pairs where the student scored `all_correct = 1` on the assessment, what fraction had `m_c = 1` in the system?

**Acceptable targets for a pilot study:**
- Precision ≥ 0.75 (the system doesn't over-declare mastery)
- Recall ≥ 0.60 (the system catches most genuine mastery; lower recall is expected given short study duration)

### 3.3 Decision: adjust thresholds?

| Result | Action |
|---|---|
| Precision < 0.65 | Raise `θ_mastery^(k)` by 5% (thresholds too permissive) |
| Recall < 0.40 | Lower `θ_mastery^(k)` by 5% (thresholds too strict) |
| Both in acceptable range | Keep current values; report as validated |

Do not adjust thresholds independently for each tier without enough per-tier data — with 10 users you likely won't have enough to distinguish tier-specific miscalibration. Adjust all thresholds proportionally.

---

## Phase 4 — Misconception Flag Audit

**Goal:** Verify that flagged misconceptions correspond to real, identifiable misconceptions.

### 4.1 Extract the flag list

For each user, retrieve all entries from the `misconceptions` list where `m_score >= 0.25` at any point during the study, including entries that were later resolved.

### 4.2 Expert review

For each flagged (user, concept) pair:
1. Read the 3–5 interactions that triggered the flag (look at `student_answer` and `correct_answer` fields)
2. Judge: is the student's incorrect response systematic and consistent (genuine misconception), or random error?
3. Label: `true_positive` or `false_positive`

Compute: **false positive rate** = false_positives / total_flags.

**Acceptable target:** False positive rate < 25%.

### 4.3 Check resolution behavior

For misconceptions that the system resolved (m_score dropped below `θ_M`), check whether the student continued to answer that concept correctly in later interactions. If resolved misconceptions recur frequently (> 40% of cases), `λ = 0.7` is too low — the EMA decays too fast. In that case, raise λ to 0.8 and document the adjustment.

---

## Phase 5 — BKT Trajectory Inspection

**Goal:** Visually verify that BKT posteriors behave plausibly. This is not parameter calibration — it is sanity checking.

### 5.1 Plot per-user, per-concept BKT curves

For each user and concept with at least 5 interactions, plot `P_t^(k)` over time for each of the three tiers (quiz, micro, code). Expected patterns:

- **Correct streak:** curve rises toward ceiling monotonically
- **Incorrect streak:** curve decays toward prior
- **Mixed evidence:** curve shows noise around a slowly rising trend
- **No micro/code interactions:** `P_t^(2)` and `P_t^(3)` should stay flat at prior (0.00)

### 5.2 Red flags to look for

| Pattern | Possible problem | Action |
|---|---|---|
| Curve reaches ceiling in 1–2 interactions and never comes down | p_G too high (too much credit for guessing) | Note for EM re-estimation; do not manually adjust yet |
| Curve never rises despite many correct answers | p_T too low, or p_S too high | Note for EM re-estimation |
| Curve oscillates wildly | Evidence routing bug | Investigate in code |
| `P_t^(2)` or `P_t^(3)` rises despite no micro/code interactions | Evidence routing bug | Fix in code immediately |

Only fix the last category (routing bugs) immediately. The others are parameter initialization issues to address when you have enough data for EM.

---

## Phase 6 — UCB1 Style Preference Report

This is not calibration — it is descriptive reporting.

For each user, report the win rate `w_s / n_s` for each teaching style. Compute:
- Most preferred style per user
- Most preferred style across all users (aggregate win rate)
- Whether UCB1 converged: for each user, how many trials before one style dominated?

This gives you the qualitative story for the UCB1 section of the paper and evidence that the bandit is working.

---

## Phase 7 — Paper Reporting Template

After completing Phases 2–6, you have everything needed for the validation section of the paper. Use this structure:

### Calibration results paragraph:

> "We conducted a 4–6 week pilot deployment with N=10 undergraduate students (M interactions per user = {X}, SD = {Y}). Following the study, we calibrated θ_goal using {N_labels} human-labeled interaction pairs; the F1-maximizing threshold was θ_goal = {value} (precision = {P}, recall = {R}, F1 = {F1} on a held-out 20% split). Mastery thresholds θ_mastery^(k) were validated against a standardized 3-tier C programming assessment administered in the final study week: mastery declarations achieved precision = {P_m} and recall = {R_m} against external criterion performance. Misconception flags were audited by two domain experts; the false positive rate was {FP}%. BKT parameter trajectories were consistent with expected monotone convergence; no systematic routing errors were detected. Full EM re-estimation of BKT parameters is planned once the deployment accumulates ≥200 interactions per concept per evidence tier (estimated at N≈{estimate} students over one full semester)."

### Limitations paragraph:

> "The pilot cohort (N=10) is insufficient for statistically significant parameter estimation. The BKT parameters reported in Table 1 are initialization defaults calibrated against prior BKT literature [29]; the pilot study provides trajectory validation rather than parameter calibration. The weighting hyperparameters w₁…w₄ = (1.0, 0.5, 0.5, 0.2) are heuristically set and have not been optimized; offline policy evaluation is planned as future work."

---

## Appendix: What Needs More Data Before It Can Be Calibrated

| Parameter | What's needed | When feasible |
|---|---|---|
| BKT `p_T`, `p_S`, `p_G` per tier | ≥200 interactions per concept per tier | After ~1 full semester, ≥50 students |
| `w₁, w₂, w₃, w₄` | Controlled A/B experiment with multiple weight configurations | Future work; requires IRB-approved experimental design |
| SM-2 `EF_init`, `EF_min` | Longitudinal retention data over ≥3 months | After second semester deployment |
| `p_T^(k)` tier separation | Large enough sample to fit independent EM per tier | Same as BKT above |
