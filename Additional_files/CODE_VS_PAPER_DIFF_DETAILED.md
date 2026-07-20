# Code vs. Journal — Detailed Formal Specification of the Gaps

Companion to `CODE_VS_PAPER_DIFF.md`. This document formalizes every mechanism that exists
in the code but is **absent from `SAGE_SRL_journal.pdf`**, with the exact equations, decision
rules, invariants, and reasoning — written in the paper's notation so it can be lifted
directly into the manuscript.

Notation follows the journal: `P̃^(k) ∈ [0,1]` is the unconstrained BKT posterior for tier
`k ∈ {1,2,3} = {quiz(declarative), micro(procedural), code(applied)}`; `θ_max^(k) =
(0.60, 0.25, 0.10)` are the display ceilings; `n^(k)` is the correct-evidence count;
`θ_certify = 0.95`, `n_min = 3`. All constants below are the actual values in the code.

---

## PART A — Contribution 2: Mastery-Conditioned Response Adaptation (MCRA)

### A.1 Effective mastery (the quantity classified)

MCRA does not classify on the raw posterior but on the **SRL-blended effective posterior**
(defined fully in Part B):

```
P̃_eff^(k) = α · P̃^(k) + (1 − α) · P_self^(k)     if a self-assessment exists,
P̃_eff^(k) = P̃^(k)                                  otherwise,          α = 0.6.        (A1)
```

### A.2 Mastery-level classifier

Let `resolve(E)` map the turn's extracted entities `E` to a knowledge-graph concept `c` by
the three-stage resolution (exact case-insensitive match → word-boundary prefix match with
min length 3 → parent lookup along `INCLUDES` edges). Given `c`, define the unweighted mean
effective mastery

```
P̄_eff(c) = (1/3) Σ_{k=1}^{3} P̃_eff^(k)(c).                                            (A2)
```

The level function `L(c) ∈ {novice, developing, proficient, reviewing}` is

```
              ⎧ reviewing    if ever_certified(c) = 1                                   (A3)
              ⎪ proficient   if P̄_eff ≥ 0.75  ∧  n^(k) ≥ 2  ∀k
   L(c)  =    ⎨ developing   if P̄_eff ≥ 0.35  ∨  ∃k : n^(k) ≥ 2
              ⎪ novice       otherwise
              ⎩ novice       if no concept resolves / no prior evidence.
```

Note the deliberate asymmetry: `proficient` requires a **conjunctive** evidence floor
(`∀k`), mirroring the certification rule, whereas `developing` uses a **disjunctive** trigger
(`∃k`) so that a single sustained interaction is enough to leave the novice regime.
`reviewing` keys on `ever_certified` (a latch that decay never resets), so a student who has
proven mastery is never sent back to heavy scaffolding by forgetting.

### A.3 Adaptation operator

`L(c)` drives a deterministic policy `Π(L)` over three response dimensions:

| `L` | scaffolding depth `d` | format sections `σ` | challenge type `χ` |
|-----|----------------------|---------------------|--------------------|
| novice | maximal (analogies, define every term) | 5 (Explanation, Use-Cases, Goal, Visual, Example) | guided micro-challenge (1 line) |
| developing | moderate (build on basics) | 5 | combine two concepts |
| proficient | light (edge cases only) | 3 (Explanation, Example, Challenge) | advanced / find-the-bug |
| reviewing | none (concise refresher) | 2 (Refresher, Quick-Check) | retention recall |

Two **gate bypasses** are conditioned on `L`:

```
   reviewing ⇒ prerequisite gate φ is skipped        (earned-mastery bypass)             (A4)
   reviewing ⇒ Socratic withholding is skipped        (refresher, not "try first")        (A5)
```

### A.4 Reasoning

This is the *expertise-reversal effect* (Kalyuga): scaffolding that helps novices harms
experts, whose limited working memory is taxed by redundant guidance. `Π` reduces `d` and `σ`
monotonically in `L`, keeping instruction inside each learner's zone of proximal development.
The mechanism is standard ITS (VanLehn inner loop) and is framed as *consuming* the mastery
signal, not as a novel contribution — its role in the paper is to demonstrate the mastery
signal is actionable.

---

## PART B — Contribution 3: SRL-BKT Calibration Loop  (the load-bearing novelty)

The learner may contest the model's estimate through a **downward-only** dashboard slider.
Two coupled adaptation layers result: an immediate score blend and a slow per-user
parameter adaptation.

### B.1 Self-assessment signal

A student self-assessment on tier `k` of concept `c` is a value `P_self^(k) ∈ [0,1]`
admitted only under the **downward constraint**

```
   P_self^(k) < P̃^(k)     (else the assessment is rejected).                            (B1)
```

Epistemic justification: "I guessed, I don't really know this" is *evidence of non-mastery*;
"trust me, I know it" is a *claim*, not evidence. Raising mastery is therefore reserved for
the evidence path (Part A/§IV of the paper): a student who wants a higher estimate must
elicit and pass evidence (e.g., request a quiz).

### B.2 Layer 1 — score blending (immediate)

```
   P̃_eff^(k) = α · P̃^(k) + (1 − α) · P_self^(k),      α = 0.6.                          (B2)
```

`P̃_eff` is a convex combination, so under (B1) it satisfies `P_self^(k) ≤ P̃_eff^(k) < P̃^(k)`:
the blend can only **lower** the working estimate. `P̃_eff` feeds display, the MCRA classifier
(A1–A3), and the prerequisite-radius computation — **never** certification. `α = 0.6` places
majority trust in behavioural evidence while granting the learner a bounded 40% voice;
`α` is fixed (a sensitivity sweep over `α ∈ {0.5,…,0.8}` is the planned robustness check).

### B.3 Calibration state and the direction signal

Each `(u,c,k)` maintains an adjustment count `N_adj` and a direction EMA `e`. On each
admitted assessment,

```
   Δ = P_self^(k) − P̃^(k)               (< 0 by B1)                                     (B3)
   d = clip( Δ / max(|Δ|, 0.01), −1, +1 )      (= −1 for any genuine downward move)      (B4)
   e ← λ·d + (1 − λ)·e,                 λ = 0.4.                                          (B5)
   N_adj ← N_adj + 1.
```

Because every downward move gives `d = −1`, the EMA (B5) converges geometrically to `−1`
under sustained disagreement; a lone protest decays as the student later agrees (no new
downward moves ⇒ `e` is not reinforced). `e` thus measures *sustained* disagreement, not a
one-off.

### B.4 Layer 2 — per-user emission-parameter adaptation (slow)

When disagreement is both **sustained** and **consistent**,

```
   if  N_adj ≥ N_trig  ∧  e < −θ_dir       (N_trig = 3,  θ_dir = 0.3):                   (B6)
        p_G^(k) ← min( p_G^(k) + β·|e|,  p_G,max^(k) ),      β = 0.10.                    (B7)
```

Bounds and defaults (per tier):

```
   p_G,default = (0.20, 0.10, 0.05),   p_G,max = (0.40, 0.25, 0.15).                     (B8)
```

The `min(·, p_G,max)` in (B7) makes adaptation **monotone non-decreasing up to a hard cap**;
a saturation guard halts adaptation once `p_G^(k)` reaches `p_G,max^(k)`, so repeated triggers
can never drive `p_G → 1`. The caps are chosen strictly below the BKT non-degeneracy boundary
`p_S^(k) + p_G,max^(k) < 1` for every tier (quiz 0.50, micro 0.40, code 0.35), so a correct
answer always remains diagnostic — even fully saturated, a correct quiz answer is ≈60%
informative.

`N_trig = 3` matches the system's `n_min = 3` evidence rule — three observations is the
minimum to separate a pattern from noise. Each qualifying adjustment raises the guess
parameter by `β|e| ≈ 0.10` (since `|e| → 1`), saturating at `p_G,max`.

### B.5 Effect on the learner model

Raising `p_G^(k)` reshapes the tier's Bayesian update (paper Eq. 3). A correct response
yields a smaller posterior increment because a larger share of "correct" is attributed to
guessing:

```
   P'(P̃; p_G) = P̃(1−p_S) / [ P̃(1−p_S) + (1−P̃)·p_G ]      is strictly decreasing in p_G.  (B9)
```

So for a student the model has learned to be a strong guesser, each correct quiz answer
counts for *less*, and more evidence is required before `P̃^(1)` reaches `θ_certify`. The
calibration loop personalizes the *emission* model (guess/slip), not merely the estimate —
this is the distinction from prior work.

### B.6 Safety: the loop cannot manufacture mastery

**Proposition B.1 (No self-certification; bounded, non-degenerate adaptation).** For any
admitted self-assessment sequence,
(i) `P̃_eff^(k) < P̃^(k)` (B2 under B1);
(ii) the certification predicate `m_c = 1 ⇔ ∀k: P̃^(k) ≥ θ_certify ∧ n^(k) ≥ n_min` reads the
raw posterior `P̃^(k)`, on which `P_self` has no influence;
(iii) Layer 2 is **monotone non-decreasing in `p_G^(k)` up to the hard cap `p_G,max^(k)`**
(B7–B8), and by (B9) the certification posterior is monotone non-increasing in `p_G^(k)`;
(iv) the cap satisfies `p_S^(k) + p_G,max^(k) < 1`, so the emission model is **never
degenerate** and a correct response remains strictly informative.
Hence no student action can raise `m_c`; student input can only *delay* certification by a
**bounded** amount (the effect saturates at `p_G,max`). The Evidence Diversity Guarantee
(paper Thm 2) is preserved verbatim. ∎

This is why the slider is downward-only: it dissolves the theorem-safety problem by
construction rather than by parameter tuning.

### B.7 Convergence intuition (for the synthetic study)

Model a learner with a *true* guess rate `g* > p_G,default`. Behaviour-only BKT over-credits
lucky guesses, inflating `P̃`. Sustained downward self-assessment drives `e → −1`, and (B7)
increments `p_G^(k)` toward `g*` (clamped by `p_G,max`), reducing over-credit. Convergence is
in the number of *qualifying adjustments*, `⌈(g* − p_G,default)/β⌉` in the noiseless limit.
**Validity caveat:** the simulated self-assessment must be *independently* noisy /
miscalibrated (Dunning–Kruger); generating it from `g*` would be circular.

### B.8 Evaluation (within-subject, defeats self-selection)

Comparing self-selected calibrators to non-calibrators is confounded. The `prediction_log`
records, *before each outcome* `x`, both `P̃^(1)` and `P̃_eff^(1)`. Define per-student Brier
scores

```
   B_bkt  = mean_t ( P̃^(1)_t     − x_t )²,
   B_eff  = mean_t ( P̃_eff^(1)_t − x_t )².                                              (B10)
```

The within-subject claim is `B_eff < B_bkt` **after** a student begins calibrating — a
paired test on the same learner, immune to who chose to calibrate. Group MAI growth
(Day-30 − Day-1) with Day-1 MAI as an ANCOVA covariate is the secondary, between-subject check.

### B.9 Positioning

- vs. **Yudelson (2013)** individualized BKT: parameters there are fit from *behaviour* alone;
  here `p_G` adapts from *metacognitive feedback*.
- vs. **Bull & Kay** negotiated Open Learner Model: the negotiation there changes the
  *display*; here it rewrites the *emission parameters* that govern future inference.
- The defensible sliver: *sustained metacognitive disagreement adapts the per-user guess/slip
  parameters, downward-only, without touching the certification gate.*

---

## PART C — Classroom Video Subsystem

### C.1 Dynamic checkpoint placement

For a video of transcript duration `D` (seconds), the number of comprehension checkpoints is

```
   N = clip( round(D / τ), 1, 8 ),         τ = 510 s (≈ 8.5 min target spacing).         (C1)
   t_i = D · i / N,   i = 1,…,N;   t_N ← min(t_N, D − 2).                                 (C2)
```

Checkpoints are the segment *end-points* of an `N`-way equipartition, so the last always
lands at the video's end. Example `D = 17 min`: `N = round(1020/510) = 2`, `t = (8.5, 17)`
min — one mid-lecture, one terminal. MCQ `q_i` is LLM-generated from the transcript window
`[t_{i−1}, t_i)` (its own segment), cached once per `(video, t_i)`.

### C.2 Non-skippable enforcement invariant

Let `A ⊆ {t_1,…,t_N}` be the answered set and `p` the playback position. On every `timeupdate`
and `seeking` event the controller enforces

```
   if ∃ i : t_i ≤ p ∧ t_i ∉ A   then  pause and present q_{i*} (i* = min such i);         (C3)
   resume ⇒ correct answer on q_{i*} ⇒ A ← A ∪ {t_{i*}}.
```

**Invariant (C4).** At any *playing* position `p`, every checkpoint `t_i ≤ p` has been
answered correctly: `∀ t_i ≤ p, t_i ∈ A`. Seeking forward past an unanswered `t_i` (the
"scrub to the end" case) triggers (C3) and is thus non-bypassable. A wrong answer clears the
selection and re-arms submission; only a correct answer advances `A`.

### C.3 Checkpoint answers as declarative evidence

A correct/incorrect answer on `q_i` (tagged with concept `c_i`) is routed as a **quiz-tier
(declarative)** observation into the same factored BKT:

```
   (x, e) = (1[answer correct], quiz)  →  bkt.update(u, c_i, x, quiz).                    (C5)
```

Video comprehension therefore contributes to mastery under the identical routing (Eq. 2),
update (Eq. 3–5), and certification (Eq. 6) machinery — checkpoints are retrieval-practice
evidence, not a separate scoring track. A confidence rating (JOL) is collected before each
answer and feeds the SM-2 quality score exactly as in-chat quizzes do.

### C.4 Engagement, coverage, and attention

Genuine watch time excludes seek jumps by accumulating only small forward deltas:

```
   W = Σ_t Δp_t · 1[ 0 < Δp_t < 1.5 s ∧ playing ],   Δp_t = p_t − p_{t−1}.               (C6)
   completion  ρ = min(W/D, 1);   completed ⇔ ρ ≥ 0.90.                                  (C7)
```

Inattention is proxied by time spent hidden while playing (Page-Visibility API) plus mute
state:

```
   H = Σ intervals where (document.hidden ∧ playing);   inattention ≈ H / D.             (C8)
```

`H > 0` with high `ρ` flags "running in the background." All of play/pause/seek/ended/
tab-hidden/tab-visible/mute/unmute are logged with position.

### C.5 SRL wrappers and transfer

- **Pre-video intention** (Forethought): a goal prompt gates the first play; response logged.
- **Post-video reflection** (Self-reflection): a takeaway prompt at end-of-video.
- **Prelab transfer**: after the terminal checkpoint, one of the video's authored complex
  problems (`prelab.json`) is offered; "Solve in SAGE" hands the problem into the chat's
  guided complex-problem mode (`intent = PROBLEM` → Scaffolding agent) and logs a
  `prelab_started` event — a measurable *transfer* from passive viewing to active solving.

---

## PART D — Class-Study Telemetry (formal data model)

Every logged row carries an anonymized `study_id` (a stable pseudonym per user), making the
dataset a single relational graph keyed on `study_id`.

### D.1 Raw event algebra

`event_log` is an append-only stream `(seq, study_id, session_id, type, payload, t)` with a
monotone `seq`. Event types now emitted:

```
   type ∈ { goal_set, quiz_skip, self_assessment, certification, decertification,
            withholding_fired, gatekeeper_block, feedback, prelab_started,
            video_checkpoint_answer }.
```

### D.2 Longitudinal tables (append-only)

| table | grain | key columns |
|-------|-------|-------------|
| `evidence_log` | one BKT observation | concept, tier, correct, question_id |
| `bkt_history` | one posterior snapshot | tier, P̃, n, certified flags, trigger |
| `prediction_log` | one pre-outcome prediction | P̃_bkt, P̃_eff, then outcome |
| `calibration_log` | one slider move | P̃_bkt, P_self, Δ, e, p_G(old→new) |
| `jol_log` | one confidence rating | confidence 1–5, correctness |
| `quiz_log` | one item | question, answer, correct, confidence, latency |
| `turn_log` | one chat turn | intent, mastery level, s_goal, c_code, **m_state (FSM)**, ΔF, n_strike, latency |
| `response_log`, `behavior_log`, `affect_log`, `session_log`, `path_event`, `misconception_log`, `video_*` | as named | — |
| `assessment` | one test/MAI item | instrument, Bloom tier, concept, score |

The per-turn metacognitive FSM state `m_state ∈ {Planning, Monitoring, Reflecting,
Helplessness}` is a rare longitudinal SRL label; `prediction_log` is the artifact enabling
(B10).

### D.3 Contribution-to-data map (for §V)

```
   C1 (multi-tier BKT)   ← evidence_log, bkt_history, assessment (Bloom-tagged),
                            offline flat-BKT re-simulation from evidence_log
   C2 (MCRA)             ← response_log × behavior_log (productive-struggle shift)
   C3 (calibration loop) ← calibration_log, prediction_log (B10), MAI growth
```

---

## PART E — Instructor Risk Matrix (intervention support)

Per student over a recent window, from `affect_log` (frustration) and `user_knowledge`
(mastery):

```
   mastery_norm = min( P̄ / 0.95, 1 ),      P̄ = mean composite mastery over touched concepts.
   F = min( 50,  15·R + 120·max(0, μ_ΔF) + 30·max(0, max ΔF) )      (R = rage count)      (E1)
   M = (1 − mastery_norm) · 50      (if any concept touched, else 0)                       (E2)
   risk = F + M ∈ [0,100].                                                                 (E3)

   tier = Critical   if (R > 0 ∨ μ_ΔF > 0.1) ∧ mastery_norm < 0.35
        = High       elif risk ≥ 50
        = Watch      elif risk ≥ 30
        = OK         else.                                                                 (E4)
```

The **Critical** predicate is the explicit *high-frustration ∧ low-mastery* intersection the
instructor acts on (pushes a targeted challenge). This closes the instructor-regulation loop:
intervention (challenge push) → measurable downstream effect on `affect_log`/`behavior_log`.

---

## Summary of new formal objects (none currently in the paper)

| # | Object | Eq. |
|---|--------|-----|
| 1 | Effective mastery blend | A1 / B2 |
| 2 | Mastery-level classifier `L` | A3 |
| 3 | Adaptation policy `Π(L)` + gate bypasses | A4–A5 |
| 4 | Downward-only self-assessment constraint | B1 |
| 5 | Direction signal + EMA | B3–B5 |
| 6 | Per-user `p_G` adaptation rule + bounds | B6–B8 |
| 7 | Monotone effect of `p_G` on the update | B9 |
| 8 | **Proposition B.1 (No self-certification)** | B6 |
| 9 | Within-subject Brier evaluation | B10 |
| 10 | Dynamic checkpoint placement | C1–C2 |
| 11 | Non-skippable enforcement invariant | C3–C4 |
| 12 | Checkpoint-as-evidence routing | C5 |
| 13 | Coverage / completion / attention | C6–C8 |
| 14 | Telemetry event algebra + contribution map | D.1–D.3 |
| 15 | Risk scoring function | E1–E4 |
