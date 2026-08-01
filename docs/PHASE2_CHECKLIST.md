# Phase 2 — paper-correctness pass

Make Section III describe the system that is actually deployed. Source of findings:
`docs/IRL_PAPER_CODE_VERIFICATION.md`, plus three items Phase 1 added.

**There is no LaTeX source in this repo** — only `Additional_files/IRL_Extension.pdf`.
So each item below carries the exact replacement text to paste, and the code location it
was verified against. Every constant here was re-read from the deployed code on
2026-07-31, *after* the Phase 0/1 changes.

Nothing in Tier 1–2 is a bug in the system. The learner model verified exact — Eqs. 12–17,
all 21 Table II values, Corollary 1 numerics. These are prose-vs-code drift.

---

## Status board

| # | Item | Kind | Needs | Done |
|---|---|---|---|:--:|
| 1 | §III-G partition rule | prose | — | ☐ |
| 2 | Eq. (9) λ_peak | prose | — | ☐ |
| 3 | §III-F1 α | prose | — | ☐ |
| 4 | Cognitive-load guardrail | **decision** | your call | ☐ |
| 5 | Eq. (19) confidence conjunct | prose | — | ☐ |
| 6 | Eq. (16) θ_cert | prose | — | ☐ |
| 7 | REVIEWING ↔ K | **decision** | your call | ☐ |
| 8 | §III-F drift fixed points | prose | — | ☐ |
| 9 | Table III DEVELOPING | prose | — | ☐ |
| 10 | Theorem 1(i) → corollary | prose | — | ☐ |
| 11 | τ_block never fires | prose | — | ☐ |
| 12 | §IV result wording | prose | — | ☐ |
| — | *item 7 of the old plan* | **GAP** | content lost | ☐ |

> **Recorded gap.** The earlier plan referenced "item 7, all three parts" as a prerequisite
> for drafting §IV-H. That plan was never written to a file and its content did not survive
> compaction. It is not in the verification record. Either supply it, or treat §IV-H as
> blocked for the separate reason below.
>
> §IV-H is blocked regardless: Phase 0 found **zero certification events, zero
> decertification events**, and 4 of 1854 turns from non-synthetic accounts. Retention and
> decertification over a six-week beta cannot be written from this machine. The open
> question is still *where is the beta data* — a deployed instance to merge, or a study
> not yet run.

---

## Tier 1 — blocking

### 1. §III-G partition rule  ← do this first

**Verified:** `cot_rag_agent.py::_classify_mastery_level`, Step 5.

The paper says the three pre-certification levels partition the **composite estimate of
Eq. (15)** at 0.35 and 0.75. The deployed rule partitions `min_k P̃⁽ᵏ⁾` and adds evidence
conditions:

```python
p_min = min(p_q, p_m, p_c)          # conjunctive: no tier compensates another
n_all = min(n_q, n_m, n_c); n_any = max(n_q, n_m, n_c)
if p_min >= 0.75 and n_all >= 2: return "proficient"
if p_min >= 0.35 or  n_any >= 2:  return "developing"
return "novice"
```

Eq. (15) is a weighted sum — compensatory by construction. Partitioning it applies the
exact pooling that the Evidence Diversity Problem exists to eliminate, *inside the section
that exists to eliminate it*.

Re-derived 2026-07-31 by executing both rules against the deployed `EVIDENCE_CONFIG`
ceilings (quiz 0.60, micro 0.25, code 0.10):

| archetype | P̄ (Eq. 15) | min P̃ | paper's rule | deployed | |
|---|---:|---:|---|---|---|
| novice | 0.194 | 0.01 | NOVICE | NOVICE | |
| developing | 0.598 | 0.40 | DEVELOPING | DEVELOPING | |
| proficient | 0.835 | 0.82 | PROFICIENT | PROFICIENT | |
| **lopsided** (0.95/0.75/0.55) | **0.812** | **0.55** | **PROFICIENT** | **DEVELOPING** | ← |
| inverse_lopsided | 0.613 | 0.55 | DEVELOPING | DEVELOPING | |

A quiz-strong, code-weak learner is handed *reduced* scaffolding under the rule as printed —
the single archetype the Evidence Diversity Problem is named after.

Third, smaller divergence: the composite's attainable range is [0, 0.95], so a cutoff
printed as 0.75 actually sits at **78.9%** of range. If the composite formulation were kept
the cutoffs would need restating as fractions of Σθ_max, which is a further argument for
switching the prose to the minimum.

**Replacement text:**

> Below certification, the policy level is determined by the same conjunctive principle
> that governs certification itself. Let `P̃_min = min_k P̃⁽ᵏ⁾` be the weakest decayed
> per-tier posterior and let `n⁽ᵏ⁾` denote the evidence count in tier `k`. A topic is
> assigned PROFICIENT when `P̃_min ≥ 0.75` and every tier carries at least two
> observations; DEVELOPING when `P̃_min ≥ 0.35`, or when any single tier has accumulated at
> least two observations; and NOVICE otherwise. The minimum is used rather than the
> composite of Eq. (15) deliberately: a weighted sum is compensatory, so partitioning it
> would allow strength in a cheap tier to purchase reduced scaffolding for a learner who
> is weak in an expensive one — the same substitution that Theorem 1 rules out for
> certification. Adaptation is therefore non-compensatory for the same reason
> certification is.

The last two sentences are the point. This item *strengthens* the paper: the conjunctive
adaptation rule is a second instance of your own thesis, currently presented as a
violation of it.

---

### 2. Eq. (9) — λ_peak

**Verified:** `sentinel.py:30-31`.

```python
TRAJ_ACC_DECAY  = 0.8   # Eq. (10) ✅ matches paper
TRAJ_PEAK_DECAY = 0.9   # Eq. (9)  ✗ paper says 0.8
```

The paper prints λ = 0.8 and says it is shared with Eq. (10). There are two constants.

**Replacement:** print Eq. (9) with `λ_peak = 0.9` and Eq. (10) with `λ_acc = 0.8`, and
carry the code's own rationale — the peak term cools over roughly five to six clean turns,
which is a usability guard: it stops a single spike from holding a learner under suspicion
for the rest of a session.

---

### 3. §III-F1 — α

**Verified:** `sentinel.py:44-46, 433-441`. **The code was already changed in Phase 0c**;
only the prose is outstanding.

```python
GOAL_TAU_BASE  = 0.85
GOAL_TAU_ALPHA = 0.05
GOAL_TAU_FLOOR = 0.60
adaptive_threshold = max(GOAL_TAU_FLOOR,
                         GOAL_TAU_BASE - GOAL_TAU_ALPHA * math.log(1 + n_cert))
```

**Replacement text:**

> The alignment bar a security-sensitive query must clear is relaxed for learners with
> earned standing on the curriculum: `τ = max(τ_floor, τ_base − α·ln(1 + |K|))`, with
> `τ_base = 0.85`, `α = 0.05`, and `τ_floor = 0.60`. The relation is keyed on the certified
> set `K` rather than on activity, so latitude is bought with demonstrated mastery and
> never with engagement. It is logarithmic, and floored, because a linear slope of 0.05
> would drive the threshold negative after 17 certified topics of the 26 in the curriculum,
> disabling the check entirely.

Keep the final clause — justifying a constant by its failure mode is the standard you set
in §III-K, and reviewers asked about hand-picked values.

---

### 4. Cognitive-load guardrail — DECISION REQUIRED

**Verified:** `_cognitive_load` has exactly two references in the entire backend —
its definition at `learner_model.py:59`, and one call at `learner_model.py:260` that feeds
the instructor dashboard snapshot. **Nothing in the response path reads it.**

§III-G claims: *"Π is overridden by a cognitive-load guardrail. When the instantaneous load
estimate of Eq. (18) is elevated, the policy is suppressed toward plain-language
explanation irrespective of the learner's mastery level."*

That override does not exist. Eq. (18)'s formula is correct and verified; only its claimed
use is fictional.

**Recommendation: remove the claim.** Implementing it means adding an untested behavioural
path to the response pipeline and then needing an evaluation to show it does something —
new work, right before submission, for a mechanism no result depends on. Removal is one
sentence and costs nothing, because Eq. (18) still earns its place as an instructor-facing
diagnostic.

If removing: keep Eq. (18), and describe it as feeding the instructor dashboard rather
than as gating Π.

☐ **Your call. I have not changed code for this item.**

---

## Tier 2 — correctness, cheap

### 5. Eq. (19) — confidence conjunct, and θ_conf

**Verified:** the conjunct is real but it lives at the *call site*, not in the EMA.
`user_knowledge_manager.py:268` applies the indicator as `1.0` unconditionally;
`examiner.py:372` decides whether to call it at all:

```python
if confidence_score >= 4:          # self-reported 1–5 Likert  → θ_conf = 4
    knowledge_manager.store_misconception(...)
```

So the deployed update is `M_c ← λ_M M_c + (1−λ_M)·1[conf ≥ 4 ∧ e = 0]`, with
`λ_M = 0.7` ✅ and `θ_M = 0.25` ✅ (both verified in `user_knowledge_manager.py:14-15`).

**Replacement:** print the indicator with the confidence conjunct and state θ_conf = 4 on
the five-point self-report scale. As written the equation describes a general error-rate
tracker; as built it tracks *confidently wrong* answers, which is the stronger construct —
it is a misconception detector, not an error counter. Say so.

---

### 6. Eq. (16) — θ_cert is calibrated, not constant

**Verified:** `bkt_model.py:405-407` calls `calibrator.get_threshold(concept, tier)`, not
the literal `THETA_CERTIFY`. The calibrator floors at `max(THRESHOLD_PRIORS[tier], 0.75)`
with all priors 0.95, so the effective threshold is **≥ 0.95** and can only tighten.

Theorem 1 is unaffected — it quantifies over all parameter assignments — and the direction
is conservative.

**Replacement:** write `θ⁽ᵏ⁾_cert ≥ 0.95, per-concept calibrated` rather than
`θ_cert = 0.95`. One symbol change, removes a discrepancy a reviewer would find by grepping.

---

### 7. REVIEWING ↔ K — DECISION REQUIRED

**Verified:** `cot_rag_agent.py:1149-1151`.

```python
# Step 4: ever_certified takes priority → reviewing
if row["ever_certified"]:
    return ("reviewing", ...)
```

`ever_certified` is sticky — set once, never reset (`bkt_model.py:372`, deliberately:
*"decay cannot re-lock earned prerequisites"*). `is_certified` is the flag that tracks K.

The paper says REVIEWING is entered *"exactly when t is certified (t ∈ K)"*, and §III-H
says K is non-monotone — a certified topic left unpracticed decays below θ_dec and is
decertified.

**Both cannot be true.** As deployed, a decertified topic leaves K but keeps REVIEWING
treatment — the system stops teaching a learner whose mastery has demonstrably lapsed.
Worse for the paper: if nothing behavioural changes on decertification, then §III-H's decay
mechanism has **no consequence for adaptation at all**, which undercuts the section.

**Recommendation: split the two uses of the flag.** They are already separate code paths.

- Prerequisite gating keeps `ever_certified` — the original rationale is sound, decay
  should not re-lock a prerequisite and trap the learner.
- The REVIEWING *response level* keys on `is_certified` — so decay has a visible
  pedagogical consequence, which is what §III-H claims.

That is a two-line change in `_classify_mastery_level` and it makes the paper's own decay
story true. The cheaper alternative is to restate the prose as "entered when t has ever
been certified" and accept that decertification has no adaptive effect.

☐ **Your call. I have not changed code for this item.**

---

### 8. §III-F drift fixed points

**Verified:** reproduced exactly — 0.102857 / 0.108000 / 0.114000.

The paper's numbers are right, but they are fixed points of the map with `p_T` applied
**unconditionally**. The deployed map (Eq. 14) credits transition on correct answers only,
and contracts to 0 — which is what Theorem 1(ii) rests on.

**Replacement:** add one clause — *"under the unfactored update in which p_T is applied
irrespective of outcome"*. Without it a reviewer checks the claim against Eq. (14), gets 0,
and concludes the paper contradicts itself.

---

### 9. Table III — DEVELOPING

**Verified:** `socratic.py:459-491`, four structurally distinct branches.

Table III currently reads *"Moderate; builds on prior known concepts"* — the vague relative
phrasing that let DEVELOPING collapse into NOVICE before the four-branch fix.

**Replacement:** the deployed template is specific and more defensible:

| | deployed behaviour |
|---|---|
| Analogies | none — analogy is NOVICE-only |
| Explanation | step-by-step mechanism trace |
| Dedicated section | **Common Mistakes** |
| Worked example | yes |
| Challenge type | multi-concept synthesis ✅ already correct |

Sections by level, for the table: NOVICE `Explanation · Use Cases · Visual Model · Example
from Class · Your Turn!`; DEVELOPING `Explanation · Common Mistakes · Worked Example ·
Challenge`; PROFICIENT `Explanation · Example · Challenge`; REVIEWING `Refresher · Quick
Check`. Every level owns at least one section no other level emits — that is the design
rule that makes Π-fidelity measurable, and it is worth one sentence.

---

### 10. Theorem 1(i) → corollary

Theorem 1(i) holds **by construction** of the `N_min` conjunct in Eq. (16) — it is
definitional, not a result. Presenting it as a theorem clause invites a reviewer to ask
what was proved. Demote to a corollary or a remark immediately after Eq. (16). Theorem
1(ii), which rests on the asymmetry of Eq. (14), is the real result and stays.

---

## Tier 3 — added by Phase 1

### 11. τ_block never fires

**Measured:** across 23 sessions and 96 turns, the highest session risk observed anywhere
is **0.797**. τ_block = 0.85 was reached **zero** times. Every trajectory-attributable
block came through the judge escalation path at τ_judge = 0.45.

Do not present τ_block as a validated mechanism. Either describe it as an unexercised upper
safety stop, or lower it. With reviewers already questioning hand-picked constants, a
threshold that never fires while being presented as load-bearing is a liability.

Source: `IRL_extension_results/eval_04_report.md` Table 6, `eval_04_threshold_sweep.csv`.

### 12. §IV result wording

- **Code density** — report the decomposition, not the pooled −6.39. 89% of it is the
  prerequisite gate returning roadmaps with zero code; among taught responses the drop is
  1.27 and indistinguishable from noise at n=27.
- **Pedagogy** — report both +2.28 pooled and **+1.96 restricted to taught responses**.
  The contrast is a strength: the same stratification that dissolves code density leaves
  pedagogy standing.
- **Judge** — every judged number currently comes from `gemini-flash-latest`. Either re-run
  on gpt-4o before submission or name the judge explicitly.
- **Containment** — 93% (14/15) is the current figure; do not present it as an improvement
  over the earlier 80%, which came from a different judge.

Full detail: `IRL_extension_results/PHASE1_REPORT.md`.

---

## Suggested order

1. **#1 §III-G** — highest risk, and it improves the paper
2. **#4 and #7 decisions** — they gate what gets written; make them before drafting
3. **#2, #3, #5, #6, #8, #10** — mechanical prose fixes, roughly an hour together
4. **#9 Table III** — needs the section list above
5. **#11, #12** — §IV/§V wording, after the Phase 1 numbers are settled
6. **§IV-H** — blocked pending the beta-data answer; do not draft around it
