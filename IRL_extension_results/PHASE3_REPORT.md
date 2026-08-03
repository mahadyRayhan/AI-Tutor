# Phase 3 — write the missing sections

_Run 2026-08-01. Index: `docs/IRL_EXTENSION_ROADMAP.md`._
_Judged figures updated 2026-08-01 for the gpt-4o re-judge — see `PHASE1B_GPT4O_REJUDGE.md`._

Phase 3 was scoped as: write §IV-H, §IV-I and §V, and clear all placeholders — on the
assumption that Phase 0's audit had unblocked it.

It had not. §IV-H and §IV-I cannot be written: **the class trial has not run.** §V is
written and included below. **Option A is chosen** — cut both sections and reframe.

`Additional_files/IRL_extension.tex` was located after the analysis and read, but **not
modified**. Every change below is given with its line number for you to apply.

---

## 0. Status

| Item | State |
|---|---|
| §V Discussion | ✅ drafted in full (§5 below) — pedagogy figure updated to gpt-4o |
| Future Work replacement text | ✅ drafted (§6 below) |
| Abstract — reframe for Option A | ✅ drafted (§4 below) |
| Cut list for §IV-H / §IV-I | ✅ exact lines (§3) |
| Placeholder resolution | ✅ all 6 located and resolved (§3) |
| `Section ??` cross-references | ✅ **already fixed** — no undefined refs in the `.tex` |
| Applying any of it to the `.tex` | ❌ **not done — you asked me not to** |

---

## 1. The trial has not run

Checked every telemetry table, not the four the Phase 0 audit sampled.

| Table | Rows | Meaning |
|---|---:|---|
| `assessment` (pre/post C-test, MAI survey) | **0** | no learning-gain or survey data at all |
| `event_log: decertification` | **0** | §IV-H's entire subject never occurred |
| `event_log: certification` | 2 | both `hs_test_user` / concept `HS_Variables` — a fixture |
| `calibration_log` | 2 | |
| `jol_log` | 1 | |
| `quiz_log` | 1 | |
| `behavior_log` | 12 | |
| `study_participants` | 327 | auto-created per account; 321 synthetic |

Of the 6 participants unmatched by synthetic patterns, two are fixtures
(`__mcn_p6_user__`, `hs_test_user`), one is `anonymous`, three are single-turn `f202_*`
fragments from one day. **Real participants: effectively zero.**

`Additional_files/CLASS_DATA_COLLECTION_PLAN.md` corroborates: written throughout in
planning tense, readiness checkboxes unticked. It is a pre-trial instrumentation plan.

Writing §IV-H or §IV-I would mean inventing participant counts and session findings for a
study that has not happened, under an IRB number that is itself a placeholder. Not a
drafting shortcut — fabricated human-subjects results.

---

## 2. Good news: both sections are already empty

Lines 1075–1081 of the `.tex`:

```latex
\subsection{Beta Testing result}
}

\color{blue}{
\subsection{Usability Testing result}
}
```

They are bare headings with no content. **Cutting them is a seven-line deletion**, not a
rewrite. The work in Option A is not the cut — it is the abstract and intro claims that
promise those sections.

---

## 3. Exact edit list

### 3a. Delete the two sections

| Lines | Action |
|---|---|
| **1075–1081** | delete both `\subsection` stubs and the stray `\color{blue}{ }` wrapper |

Check the brace balance around 1073–1075 when you cut — the stubs sit inside a
`\color{blue}{...}` group that opens earlier.

### 3b. Placeholders — all six

| Line | Placeholder | Resolution under Option A |
|---|---|---|
| 193 (×2) | usability *N*, instructor *N*, six-week beta *N = 10* | **rewrite the paragraph** — it promises a three-part evaluation; only part one and the benchmark survive. Text in §4 below. |
| 745 | usability *N*, IRB # | **delete** — describes a usability protocol that was not run |
| 747 | six-week beta *N = 10* | **delete** — describes the beta deployment |
| 749 | `[PLACEHOLDER: $S$]` seeds | **fill: 2000** learners per cell, seed 7 (`eval_05_report.md`) |
| 797 | "Security compliance unchanged at [PLACEHOLDER…]" | **fill: 10/10 (100%)**, up from 9/10 in the conference system (`eval_02_delta.csv`) |

Only line 749 and 797 are fillable with data. The rest describe modalities that did not
happen and must be removed, not filled.

### 3c. `Section ??` — already resolved

The verification record flagged undefined cross-references from the PDF. In the current
`.tex` there are **55 labels defined, 39 distinct refs, zero undefined**. Nothing to do.

---

## 4. ⚠️ The abstract is wrong independently of Option A

Two separate problems.

**(a) It promises evaluation modalities that do not exist.** Current text:

> "We evaluate SAGE in an exemplar programming course scenario, using simulated students, a
> custom educational benchmark, **in-person usability sessions, and a six-week trial**."

Both must go under Option A.

**(b) Three numbers no longer match the current results.** The abstract states 1999, 1998,
and "none". Against `eval_05_report.md` (2026-07-30, 2000 learners/cell, seed 7):

| Abstract claims | Current result | |
|---|---|---|
| canonical marks **1999** of 2000 | 99.9% = **1998** of 2000 | ✗ |
| pooled N≥9 changes little, at **1998** | 99.8% = **1996** of 2000 | ✗ |
| "Our rule marks **none** of them" | **1 of 2000** (0.1%) | ✗ |

The third is the one that matters: the factored rule false-certifies **one** lopsided
learner in two thousand, and `eval_05_report.md` explicitly says *"Report the raw count."*
Claiming zero is a stronger claim than the evidence supports, and it is the claim a
reviewer would check first because it is the paper's headline. **Fix this even if you
abandon Option A.**

Surviving abstract claims, verified: curriculum compliance 80% → 100% ✅ (identical under
both judges); Π-fidelity 83.3% ✅ (eval_06, gpt-4o, not re-run in Phase 1b — its responses
were already `current`).

**Judged figures used in §V below are gpt-4o**, the publish judge. `gemini-flash-latest`
results are retained in `PHASE1_REPORT.md` for comparison; they differ in level but not in
direction, and the two judges agree exactly on curriculum compliance and on containment.

### Replacement abstract — final two sentences onward

> We evaluate SAGE in an exemplar programming course scenario using simulated learners, a
> custom educational benchmark, and an adversarial red-team audit. Experimental results
> show that the standard approach marks 1998 of 2000 learners who are strong on facts but
> weak in hands-on work as having learned the topic. Demanding more evidence without
> separating the kinds changes almost nothing, at 1996. Our rule marks one. Curriculum
> compliance also rises from 80\% to 100\% over the conference system. The same judgment
> also changes how the tutor teaches: shown only a response, an independent reviewer could
> identify which kind of learner it was written for in 83.3\% of cases.

### Replacement for line 193 (intro evaluation roadmap)

> Our evaluation proceeds in three parts. We first isolate the learner model in a
> controlled simulation harness, measuring how often a concept would have been certified
> from a single evidence type and how the certification rule behaves as its parameters are
> varied. We then replay a fixed educational benchmark through the deployed system to
> measure the instructional consequences of mastery-conditioned adaptation against the
> conference configuration. Finally, we subject the trajectory-level access control layer
> to a stratified multi-turn red-team audit spanning eight crescendo strategies, and report
> which enforcement layer accounts for each interception. Throughout, we retain the
> benchmark comparison against prompt-based LLM baselines. We do not report classroom
> deployment: the system has not yet been evaluated with learners, and we identify this as
> the principal limitation of the present work.

That last sentence is doing real work. Stating the limitation in the introduction is what
separates a scoped systems paper from one that looks like it is hiding a missing section.

---

## 5. §V — Discussion  ✅

### V. DISCUSSION

**A. What the evidence supports**

The central claim of this extension is that a learner model which pools heterogeneous
evidence into a scalar admits a substitution attack, and that factoring the model by
evidence type and certifying conjunctively removes it. In simulation, the conjunctive rule
false-certifies one lopsided learner in two thousand, while the canonical pooled rule
certifies 1998 of the same 2000 and a pooled rule demanding the same total evidence volume
certifies 1996. The gap between the last two is the control that matters: it isolates
evidence *diversity* from evidence *quantity*, and shows that demanding more of the same
kind of evidence does not address the substitution. Under parameter misspecification the
conjunctive rule's false-certification count remains at or below one in two thousand across
all four perturbation regimes, so the result is a property of the decision rule rather than
of a fortunate parameter choice.

The second result concerns the trajectory defense, and is more nuanced. Pooled across all
turns, the session risk score does not separate attack conversations from benign ones
(AUC 0.491). Reported alone, that would suggest the layer does nothing. Conditioning on
which layer produced each block shows why the pooled statistic is the wrong summary: of the
twenty-five blocks observed, the seven produced by the escalated transcript judge all
occurred at risk between 0.50 and 0.80, while all eighteen produced by per-message layers
occurred at exactly 0.000. Per-message layers fire on the first turn, where no trajectory
has accumulated and the score is definitionally zero. Pooling the two populations averages
a mechanism against cases it was never intended to handle.

This supports a narrower claim than "the risk score detects attacks", and we make only the
narrower one. The accumulator is a high-recall trigger whose function is to route a
conversation to a transcript-fed judge; the judge is the classifier. Both benign sessions
that crossed the escalation threshold were cleared by the judge rather than blocked, which
is what this division of labour predicts: escalation costs latency, not usability. A
defense-in-depth argument requires showing layers are complementary rather than redundant,
and the layer-conditioned risk distribution is that evidence.

**B. What the evidence does not support**

Three limits deserve explicit statement.

First, the hard-block threshold never fired. The highest session risk observed in
twenty-three sessions was 0.797 against a threshold of 0.85, so every
trajectory-attributable block came through the escalation path rather than from the score
alone. We report this threshold as an unexercised upper safety stop, not as a validated
mechanism. A threshold no observed trajectory reaches is a parameter awaiting
justification, and we prefer to say so than present it as load-bearing.

Second, the improvement in code density on the benchmark replay is largely an artifact of
prerequisite gating rather than of less directive teaching. Because the replay runs on an
account with no certified prerequisites, the gate returns a prerequisite roadmap for
twenty-three of fifty items, and a roadmap contains no code by construction. Restricted to
responses in which the tutor actually taught, code density falls by 1.27 points, which is
not distinguishable from noise at this sample size. The pedagogy score behaves differently
under the same decomposition, gaining 2.22 points on taught responses alone, and we
therefore treat pedagogy rather than density as the quality result. We report the
decomposition rather than the pooled figure because the pooled figure would credit the
response policy with an effect produced by the curriculum gate.

Third, and most importantly, the system has not been evaluated with learners. Every result
here comes from simulation, from replay against a fixed benchmark, or from adversarial
probing. The retention and decertification behaviour implied by the decay model, the
instructional value of mastery-conditioned adaptation, and the usability cost of the
security layers are all unevaluated in a classroom. Nothing here establishes that the
system teaches better; it establishes that the model has a property the pooled alternative
lacks, and that the safety layers behave as specified under attack.

**C. Design implications**

Two implications generalise beyond this system.

The first concerns evaluation of adaptive tutors. Mastery-conditioned adaptation is
typically validated by showing responses differ across levels. That is a weaker test than
it appears, because the prompt instructs the very behaviour being counted; template
compliance is close to circular. A blind judge asked to recover the assigned level from
response text breaks the circularity, and serves as a manipulation check on whether the
treatment was administered at all — a question that should precede any question about
whether it worked.

The second concerns non-compensatory decision rules in learner models. The substitution
addressed here is not specific to programming or to three tiers. Wherever a system
aggregates evidence of differing cost and guessability into a single scalar and thresholds
it, the cheap evidence can purchase the credential. Conjunctive certification is one
remedy; the general principle is that aggregation should be non-compensatory whenever the
aggregated quantities are not substitutable in the construct being measured. Declarative
recall does not substitute for the ability to write working code, and a model that lets it
is mis-specified regardless of how well it fits.

**D. Threats to validity**

The evaluation judges are large language models, which introduces two risks: judges may
prefer responses stylistically similar to their own outputs, and judge behaviour may drift
between model versions. We mitigate the first by scoring both systems with the same judge
in the same batch, so the reported quantity is a paired difference rather than an absolute
score, and the second by recording the judge model alongside every verdict and
content-hashing each verdict against the response it scored, so a stale verdict cannot
silently enter a table. We report one measured instance. Delivery verdicts initially differed across
judge models by twenty percentage points; the cause was a defect in our audit harness
rather than judge unreliability — the rubric declared that the reply under review came from
the conversation's final payload turn, while every turn was in fact submitted, so early
conceptual answers were assessed as though they were the harmful culmination. After
correcting the prompt to state each turn's true position, two judge models from different
families agree on the containment figure. We did not validate verdicts against human
raters, which remains the principal unaddressed threat.

The red-team suite comprises fifteen attack sessions across eight crescendo strategies.
This suffices to demonstrate the layers are complementary and to locate a failure mode —
the single containment failure was a six-turn slow burn — but confidence intervals are wide
and per-strategy cells frequently contain one session. The suite tests the attacks we
designed; it cannot speak to attacks we did not anticipate.

**E. Future work**

The immediate priority is classroom deployment, which would supply the retention,
decertification and usability evidence this paper lacks, and would allow the calibration
claim to be tested against more than a handful of paired predictions. Two extensions would
strengthen the security argument: an ablation with the trajectory layer disabled, isolating
its contribution to containment rather than to escalation alone, and a human-validated
subsample of judge verdicts. Finally, per-concept threshold calibration currently only
tightens certification; whether it should be permitted to relax under instructor authority
is an open question with obvious governance implications.

---

## 6. Future Work — absorbing the cut sections

If §VI Future Work is separate from §V-E, this paragraph absorbs the cut material.
Otherwise §V-E above already covers it and this is redundant.

> The evaluation reported here is deliberately scoped to what can be established without
> learners: the properties of the decision rule, the instructional consequences of
> adaptation on a fixed benchmark, and the behaviour of the enforcement layers under
> adversarial probing. Three questions require classroom deployment and are the subject of
> ongoing work. First, whether conjunctive certification's deferral of genuine masters —
> which simulation places at roughly one in four under the deployed evidence mix — is
> experienced as appropriate rigour or as an obstacle. Second, whether the decay model's
> retention intervals correspond to actual forgetting, which can only be observed over the
> weeks on which forgetting operates. Third, whether the over-refusal rate measured against
> a constructed benign suite predicts over-refusal on authentic coursework vocabulary. The
> telemetry instrumentation required for all three is deployed and verified; what remains
> is the cohort.

The last sentence is worth keeping — it converts "we didn't do the study" into "the
apparatus is ready", which is both true and a materially better position to be reviewed in.

---

## 7. What remains

| | Owner |
|---|---|
| Apply §3 edits to the `.tex` (delete 1075–1081, six placeholders) | you |
| Paste §V and the revised abstract | you |
| **Fix the three stale abstract numbers** — do this regardless | you |
| Phase 2's 12 correctness fixes | you (`docs/PHASE2_CHECKLIST.md`) |
| Decisions D1, D2 | you |
| gpt-4o re-judge (D4) | you, then me |

Note the ordering: Phase 2's §III-G rewrite and this phase's abstract rewrite both touch
claims about what the certification rule does. Do the §III-G fix first so the abstract and
§III-G tell the same story.
