# Paper–Implementation Gap Analysis
## SAGE-SRL Journal Draft vs. Deployed Codebase

This document maps every discrepancy between the current draft (`AI-Tutor-journal.pdf`) and
the actual backend implementation. For each gap it explains why the component matters to the
paper's argument and exactly where in the draft it should be added or corrected.

---

## Part 1 — Missing Sections (Implemented but Not Described)

---

### Gap 1 — Adaptive Threshold Recalibration is Absent

**Severity: High — this is a novel technical contribution.**

#### What is implemented
`backend/app/core/threshold_calibrator.py` (378 lines) is a fully working adaptive calibration
system. Every time the BKT model produces a posterior, it is logged to a sliding-window buffer
per `(concept, tier)` pair. Once 50 observations accumulate, and every 50 observations after
that, the calibrator fits a two-component Gaussian Mixture Model via Expectation-Maximization
(pure NumPy, no ML library), measures cluster separation with Fisher's discriminant ratio, and
blends the GMM-derived threshold with the literature prior using a noise-weighted shrinkage formula:

```
θ_new  =  (1 − w) · θ_prior  +  w · θ_GMM

w            =  1 − noise_level
noise_level  =  1 / (1 + Fisher_ratio)
Fisher_ratio =  (μ_mastered − μ_other)² / (σ_mastered² + σ_other²)
θ_GMM        =  μ_mastered − Z₀.₁₀ · σ_mastered     (10th-percentile of mastered cluster)
```

When clusters are well-separated (Fisher → ∞), w → 1 and the data-driven estimate takes over.
When clusters overlap heavily (Fisher ≈ 0), w → 0 and the system falls back to the literature
prior. The prior is never discarded; it is down-weighted as evidence grows.

#### Why it matters to the paper
The paper's Equation (6) shows fixed mastery thresholds (0.57, 0.24, 0.09) but the actual
`is_mastered()` call in `bkt_model.py` uses `calibrator.get_threshold(concept, tier)` — a
live, concept-specific value that changes over the deployment lifetime. A reader who implements
Equation (6) from the paper will get a different system than what is described. More importantly,
the calibration mechanism is what makes the system *deployable without pre-labelled mastery
data*: the priors are good enough to start, and the system improves as it observes real students.
That is an argument no competing approach makes.

#### Where to integrate
**New subsection: Section IV-D.5 — Adaptive Threshold Recalibration**

Place it after the Teaching-style selection paragraph and before the Security gate paragraph.
The subsection should:

1. State that θ^(k)_mastery in Equation (6) are initialised from the literature-backed priors
   but are not fixed — they are the output of the calibrator.
2. Present the shrinkage formula as a new numbered equation (call it Eq. 9 or renumber
   the existing ones).
3. Define the Fisher ratio and noise level as sub-expressions.
4. Explain the sliding-window buffer (N = 500 most recent observations per concept/tier) and
   the recalibration trigger (every 50 new observations once 50 accumulate).
5. Add one sentence on the fallback: "When fewer than 50 observations are available for a
   concept–tier pair, the system uses the prior; the prior is therefore never a hard lower
   bound — it is the warm-start value before data arrive."
6. Note the known limitation: Gaussian components are misspecified for bounded BKT posteriors;
   a Beta Mixture Model is the theoretically correct replacement and is planned before the
   journal submission.

Add a row for this mechanism to Table I under a label such as "Population-adaptive thresholds."

---

### Gap 2 — UCB1 Selection Formula is Overclaimed

**Severity: High — the paper claims the system does something the code does not do.**

#### What is implemented vs. what is claimed
The paper (Section IV-D, Teaching-style selection) states:

> "The system chooses among teaching styles with UCB1, treating thumbs-up feedback as reward
> and balancing exploration against exploitation; the standard sublinear-regret guarantee holds
> under approximate stationarity."

The code does two separate things:

- **Win-rate update** (`main.py`, lines 1213–1235): when a student submits thumbs up or
  thumbs down, the last bot message's `style_used` field is retrieved and the style's
  `wins` and `total` counters in the user's profile are incremented. This part is real.

- **Style selection** (`cot_rag_agent.py`, lines 614–623): the agent selects style based
  entirely on the classified *intent* label — `REVIEW` → code review, `PROBLEM/DEBUG` →
  Socratic, everything else → concept explanation. There is no UCB1 score computed and no
  exploration-exploitation tradeoff in the selection step. The win-rate counters that are
  updated by thumbs feedback are never read back to influence which style is chosen next.

The UCB1 selection formula,

```
UCB1_score(i) = wins_i / total_i  +  sqrt( 2 · ln(N) / total_i )
```

where N = sum of all total_i, does not exist anywhere in the codebase.

#### Why it matters
Claiming UCB1 and citing its regret bound while only tracking win-rates is a factual error that
a reviewer will catch. The fix is either to implement the selection or to accurately describe
what the code does.

**Option A (preferred):** Implement UCB1 selection before submission. In `cot_rag_agent.py`,
after intent classification, compute UCB1 scores over the available styles from the user's
profile and select the highest-scoring style that is compatible with the detected intent. An
unplayed style (total = 0) gets score +∞, which is the standard Auer et al. (2002) cold-start
convention that the SYSTEM_OVERVIEW already describes.

**Option B (fallback if not implementing):** Replace the claim with an accurate description:

> "Thumbs-up and thumbs-down feedback is recorded as per-style win and total counts in the
> learner state; selection currently follows intent classification, and UCB1-based selection
> over compatible styles is planned as a direct extension."

#### Where to integrate
Rewrite the Teaching-style selection paragraph in **Section IV-D** with whichever option is
chosen. If Option A, add a new equation showing the UCB1 score and one sentence naming the
cold-start convention. If Option B, remove the regret-bound claim entirely and do not cite
Auer et al. for selection.

---

## Part 2 — Math and Text Discrepancies

---

### Gap 3 — Misconception Resolution Claim is Numerically Wrong

**Severity: Medium — the text states an incorrect behavioural claim.**

#### What the paper says
Section IV-D (Misconception tracking) states:

> "The parameters are set so a confident error flags immediately and two consecutive correct
> responses are required to clear it."

#### What the code actually does
- First high-confidence error: `m_score = (1 − 0.7) × 1.0 = 0.30`
- Flag threshold: `THETA_M = 0.25`
- After one correct answer: `0.30 × 0.7 = 0.21`
- Is `0.21 < 0.25`? **Yes.** The misconception resolves after ONE correct answer, not two.

The "two consecutive" claim was accurate in an earlier version of the plan where THETA_M was
set lower (around 0.22). When THETA_M was raised to 0.25, the text was not updated to match.

The two-consecutive-correct property *does* hold for a reinforced misconception (two or more
high-confidence errors in a row), because the score grows to 0.51 and requires more decay:
`0.51 → 0.357 → 0.25 → 0.175` (three correct answers). But the paper implies it is always
two, which is only true for the reinforced case.

#### Fix
Replace:

> "two consecutive correct responses are required to clear it"

With:

> "a single correct response resolves a fresh misconception (score decays from 0.30 to 0.21,
> below the 0.25 flag threshold); a misconception reinforced by multiple high-confidence errors
> accumulates a higher score and requires proportionally more correct responses to clear —
> correctly capturing the persistence of deeply held incorrect beliefs"

This single sentence is more accurate and actually strengthens the pedagogical argument.

---

### Gap 4 — Equation (6) Presents Thresholds as Fixed Constants

**Severity: Medium — creates a contradiction with the calibrator described in Gap 1.**

#### What the paper shows
Equation (6) writes out `(θ^(1)_mastery, θ^(2)_mastery, θ^(3)_mastery) = (0.57, 0.24, 0.09)`
as concrete numbers and the surrounding text does not indicate they change.

At the very end of Section IV-C there is one sentence: "Thresholds are initialized from
literature-backed defaults and recalibrated as population data accumulates, with a fallback to
the prior when the data are too sparse to fit reliably."

This sentence is easy to overlook and appears after the proof rather than with the equation.

#### Fix
Immediately after writing out the numeric triple in Equation (6), add:

> "These values are initialization defaults; at runtime each threshold is replaced by the
> output of the adaptive calibrator described in Section IV-D.5 once sufficient evidence has
> accumulated. The Evidence Diversity Guarantee (Theorem 1) depends only on
> θ^(k)_mastery > p^(k)_0, a property that holds for both the initialization defaults and any
> calibrated value the system produces, since the calibrator clamps its output strictly below
> the BKT ceiling."

This ties Equation (6) cleanly to the calibrator and shows the guarantee is robust to
recalibration.

---

## Part 3 — Structural Issues (Broken References and Placeholders)

---

### Gap 5 — Broken Cross-References

| Location | Problem | Fix |
|---|---|---|
| Page 2, "Theorem ??" | The theorem is stated and proved on page 5 as Theorem 1 but the forward reference on page 2 has an unresolved label | Replace "Theorem ??" with "Theorem 1" |
| Page 4, "Fig. ??" | The system architecture figure is referenced in the System Overview section but no figure exists | Create the two-track architecture diagram (IRL track / SRL track / security gate) or insert a placeholder with a clear label for the camera-ready version |
| Related Work, two "[?]" citations | Evidence-centred design and mastery-learning citations are missing | Fill with Mislevy (1994) for ECD and Bloom (1968) / Guskey (2010) for mastery learning |

---

### Gap 6 — Empty or Placeholder Sections

| Section | Status | Minimum needed |
|---|---|---|
| Abstract | Empty | ~150 words: problem (EDP), approach (factored BKT), guarantee (Theorem 1), and one headline result |
| Index Terms | Empty | 5–7 terms: Bayesian Knowledge Tracing, Intelligent Tutoring Systems, Evidence-Centered Design, Mastery Learning, Spaced Repetition, Self-Regulated Learning, Bloom's Taxonomy |
| Section V — Evaluation | Placeholder | Either simulation results (synthetic learners) or pilot study; at minimum, a table showing what proportion of mastery decisions would have been wrong under single-stream BKT vs. factored BKT |
| Section VI — Conclusion | Placeholder | 3–4 paragraphs: what was built, the guarantee, limitations (GMM misspecification, no UCB1 selection, no agency transfer), and future work |
| Section IV — "Placeholder for SAGE-SRL methodology" | Hanging line at end of section | Delete this line — it is a LaTeX artifact |

---

## Part 4 — Components Correctly Described (for Reference)

These match code and need no changes.

| Component | Paper location | Code location | Status |
|---|---|---|---|
| BKT correct-answer posterior (Eq. 3) | IV-B | `bkt_model.py:_bkt_step()` | ✅ Exact |
| BKT incorrect-answer posterior (Eq. 4) | IV-B | `bkt_model.py:_bkt_step()` | ✅ Exact |
| Learning transition + ceiling (Eq. 5) | IV-B | `bkt_model.py:_bkt_step()` | ✅ Exact |
| Table II parameters (all 15 values) | IV-B | `bkt_model.py:EVIDENCE_CONFIG` | ✅ Exact |
| Evidence routing function δ^(k) (Eq. 2) | IV-B | `bkt_model.py:update()` | ✅ Exact |
| Misconception EMA formula (Eq. 7) | IV-D | `user_knowledge_manager.py:store_misconception()` | ✅ Exact |
| Misconception decay on correct answer | IV-D | `user_knowledge_manager.py:resolve_misconception()` | ✅ Exact |
| SM-2 quality score q ∈ [0,5] | IV-D | `user_knowledge_manager.py:update_sm2()` | ✅ Correct |
| Security gate σ = φ · α | IV-D | `agents/sentinel.py` | ✅ Correct |
| Goal alignment Gt = cos_sim(h(xt), h(g0)) | IV-D | `agents/sentinel.py` | ✅ Correct |
| J utility metric (Eq. 8), non-optimized | IV-E | Distributed across components | ✅ Correctly qualified |
| IRL/SRL narrative framing | Intro, Sec II-D, Sec III | Architecture split | ✅ Strongly supported |
| SRL-on-behalf-of-student disclaimer | Sec II-D | Confirmed by codebase | ✅ Accurate |
| Evidence Diversity Guarantee (Theorem 1) | IV-C | Follows from architecture | ✅ Proof is valid |

---

## Summary Checklist

### Must fix before any submission
- [ ] **Gap 3**: Correct "two consecutive" → accurate single/reinforced description
- [ ] **Gap 4**: Add calibrator forward-reference next to Eq. (6)
- [ ] **Gap 5**: Replace all "Theorem ??" with "Theorem 1"
- [ ] **Gap 2**: Either implement UCB1 selection or retract the claim

### Should add before submission
- [ ] **Gap 1**: Add Section IV-D.5 on Adaptive Threshold Recalibration with the shrinkage formula
- [ ] **Gap 6**: Fill Abstract, Index Terms
- [ ] **Gap 5**: Fill missing "[?]" citations

### Required for camera-ready
- [ ] **Gap 5**: System architecture figure
- [ ] **Gap 6**: Section V (Evaluation) and Section VI (Conclusion)
- [ ] **Gap 6**: Delete the hanging "Placeholder for SAGE-SRL methodology" line
