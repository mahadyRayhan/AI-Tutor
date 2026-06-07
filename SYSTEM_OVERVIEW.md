# An Adaptive AI Tutoring System for Introductory C Programming
### Competency-Aware Mastery Learning via Factored Multi-Skill Bayesian Knowledge Tracing

---

## Abstract

Intelligent tutoring systems commonly model concept mastery as a single latent variable, inferring proficiency from a single stream of evidence — typically quiz responses or problem attempts. This design creates a systematic failure mode: a student who can recall a definition but cannot write correct code, or who can write code but cannot reason about bugs, appears proficient under the model's criterion, while genuine incompetence across cognitive dimensions goes undetected. We call this the **Evidence Diversity Problem**. Premature mastery declarations resulting from this problem lead to advancement without true competency, compounding learning debt across prerequisite chains.

We present an adaptive intelligent tutoring system for undergraduate C programming that resolves the Evidence Diversity Problem at the learner model level. Concept mastery is modeled as a factored latent state spanning three Bloom's cognitive levels — declarative (can the student recall?), procedural (can the student write code?), and applied (can the student reason about code?) — each tracked by an independent Bayesian Knowledge Tracing hidden Markov model. A binary evidence-routing function ensures that each observation updates only the tracker corresponding to its cognitive type. Mastery requires the conjunctive satisfaction of per-tier sufficiency thresholds across all three dimensions simultaneously. This structural constraint makes it mathematically impossible for a student to satisfy the mastery criterion from any single evidence stream, regardless of performance.

The multi-dimensional learner model is embedded within a broader adaptive architecture comprising spaced repetition scheduling, multi-armed bandit teaching style selection, EMA-based misconception tracking, affective state monitoring, and a two-stage instructional security gate that maintains instructor authority over curriculum boundaries. Each component is grounded in learning science literature and connected to a formal global optimization objective. Hyperparameter choices are derived from behavioral requirements and literature-informed priors, with a calibration protocol defined for pilot study refinement.

---

## 1. The Core Research Problem

### 1.1 The Evidence Diversity Problem

Consider three students who have all studied the concept of **pointers** in C:

**Student A** can answer the question: *"What does the `*` operator do in a variable declaration?"* correctly and confidently. They score 9/10 on a declarative quiz. When asked to write a function that swaps two integers using pointers, they cannot produce working code. When shown a snippet with a dangling pointer, they cannot identify the bug.

**Student B** can write the swap function without difficulty. Their quiz score is 6/10 — they confuse some syntax — but they can produce procedurally correct code when given a specification. However, they cannot reason about what happens to memory when a pointer is freed and then dereferenced.

**Student C** can do all three: recall definitions, write code, and identify subtle memory errors in unfamiliar code. They have demonstrated competency across all cognitive dimensions.

Under a standard single-variable Bayesian Knowledge Tracing model [7], all three students may receive similar mastery probability estimates if their overall correctness rate on quiz responses is similar. The model cannot distinguish between them because it maintains one latent state per concept and updates it uniformly regardless of whether the evidence came from a recall question or a code-writing task.

**Definition (Evidence Diversity Problem).** Let `C` be a concept and `E = {e^(1), ..., e^(K)}` be a set of `K` evidence types, each targeting a distinct cognitive level. A mastery inference system suffers from the Evidence Diversity Problem if it infers `m_C = 1` from a strict subset `E' ⊂ E` of evidence types — i.e., it is possible for the system to declare mastery while the student has never produced evidence of type `e^(k)` for some `k ∈ E \ E'`.

The practical consequence is premature advancement: a student is routed to a concept whose prerequisite they appear to have mastered, but whose mastery was inferred from incomplete evidence. Because later concepts build on the full competency stack of the prerequisite — not just its declarative facet — the student's knowledge debt compounds silently.

This problem is not a corner case. In introductory C programming, declarative evidence (quiz responses) is collected far more frequently than procedural evidence (code submissions) and applied evidence (code review tasks), because quizzes are easier to generate and grade automatically. Without architectural enforcement, systems default to quiz-heavy evidence even when other evidence types are available.

### 1.2 Why Existing Approaches Do Not Solve This

**Standard BKT** (Corbett & Anderson, 1994 [7]) models one binary latent state `z_c ∈ {0, 1}` per concept, with a single emission model for all evidence types. A quiz response and a code submission produce updates of the same mathematical form to the same posterior. There is no mechanism to distinguish what cognitive process generated the observation.

**Deep Knowledge Tracing** (Piech et al., 2015 [31]) replaces the Markov model with an LSTM that captures long-range dependencies across interactions. It improves predictive accuracy but does not disaggregate mastery by cognitive level — the LSTM hidden state has no interpretable decomposition by evidence type, and there is no evidence routing mechanism.

**DKVMN** (Zhang et al., 2017 [32]) introduces dynamic key-value memory to model multiple skills simultaneously. The key matrix encodes concept relationships; the value matrix tracks per-concept mastery. However, mastery of each concept is still a scalar state, and the routing of evidence to specific cognitive trackers is not enforced — the model learns associations between evidence and skills from data without structural routing constraints.

**Cognitive Diagnostic Models** (Tatsuoka, 1983 [33]) use a Q-matrix to map items to required latent attributes. A student must demonstrate all required attributes to answer an item correctly. This is structurally similar to our conjunctive mastery rule. However, CDMs are static: the Q-matrix is designed by domain experts, is not updated from interaction data, and does not incorporate the temporal dynamics of learning (no Markov structure, no learning rate).

**Multidimensional IRT** (Reckase, 2009 [34]) models multiple latent ability dimensions simultaneously but is designed for static assessment, not adaptive real-time tutoring. It has no mechanism for online posterior updating as new evidence arrives.

The gap in the existing landscape is a system that is simultaneously: (1) multi-dimensional (disaggregated by cognitive level), (2) dynamic (Bayesian posterior updates in real time), (3) evidence-routed (each observation updates only the matching tracker), and (4) conjunctive (mastery requires multi-dimensional sufficiency). The proposed system fills this gap.

| Model | Multi-dim | Dynamic | Evidence routing | Conjunctive mastery |
|---|---|---|---|---|
| BKT [7] | ✗ | ✓ | ✗ | N/A |
| DKT [31] | ✗ | ✓ | ✗ | ✗ |
| DKVMN [32] | Partial | ✓ | ✗ | ✗ |
| CDM / DINA [33] | ✓ | ✗ | Expert Q-matrix | ✓ |
| MIRT [34] | ✓ | ✗ | ✗ | ✗ |
| **This system** | **✓** | **✓** | **✓** | **✓** |

### 1.3 Research Question

> *How can mastery inference in an intelligent tutoring system be structured so that concept progression requires evidence-specific demonstration of declarative, procedural, and applied competency — making it structurally impossible to satisfy the mastery criterion from any single cognitive evidence stream?*

The answer is a **factored multi-skill BKT model with evidence-type routing and conjunctive mastery** — the primary technical contribution of this work.

---

## 2. System Overview and Contribution Map

### 2.1 Primary Contribution: Factored Multi-Skill BKT

The primary contribution is the learner model design. For each concept `c`, three independent latent binary subskill states are maintained — one per Bloom's cognitive level [8]:

```
z_c = (z_c^(1), z_c^(2), z_c^(3)),   z_c^(k) ∈ {0, 1}
```

Each subskill is tracked by an independent BKT hidden Markov model with tier-specific parameters. A binary evidence-routing function `δ^(k)(e_t) = 𝟏[k corresponds to e_t]` ensures that each interaction updates exactly one tracker. Mastery requires all three posteriors to meet their individual sufficiency thresholds simultaneously. This directly resolves the Evidence Diversity Problem: no single evidence stream can satisfy the conjunctive criterion.

### 2.2 Supporting Contributions

The remaining components of the system make the learner model deployable in a real classroom at scale:

| Component | Role relative to core contribution |
|---|---|
| IRL/SRL dual-track architecture | Enables real-time instructor authority over curriculum boundaries without per-student reconfiguration |
| Two-stage security gate `σ = φ · α` | Enforces structural prerequisites and pedagogical alignment before routing to the mastery model |
| **Adaptive threshold calibration (GMM)** | **Makes mastery thresholds data-driven: warm-starts from literature priors, recalibrates every N interactions using population posterior distributions** |
| UCB1 teaching style selection | Personalizes *how* content is delivered while the mastery model governs *what* counts as demonstrated competency |
| SM-2 spaced repetition | Maintains retention of previously mastered concepts across sessions |
| EMA misconception tracking | Identifies and monitors systematic high-confidence errors that the BKT model alone cannot detect |
| Affective profiler | Prevents frustration-driven disengagement from corrupting evidence quality |

None of these components independently address the Evidence Diversity Problem, but all are necessary for the learner model to function reliably in a real deployment environment.

### 2.3 System Utility Function

The individual components act as local optimizers of a shared utility metric — the explicit theoretical target that makes the learning-theoretic intent of each design decision legible. Formally:

```
J  =  Σ_t  [ w₁ · ΔK_t  +  w₂ · R_t  −  w₃ · F_t  −  w₄ · C_t ]
```

where:

- `ΔK_t = Σ_{k=1}^{3} [P_t^(k) − P_{t−1}^(k)]`: composite BKT mastery gain at step `t`. Only the tier `k(e_t)` corresponding to the current evidence type is updated; the remaining two posteriors are unchanged. For a correct observation `ΔK_t ≥ 0`; for an incorrect one `ΔK_t < 0`.
- `R_t ∈ {0, 1}`: SM-2 retention signal. `R_t = 1` if the SM-2 quality score `q ≥ 3` (the algorithm's pass threshold); `R_t = 0` if `q < 3`, which resets the review interval to day 1. The quality score `q ∈ [0, 5]` is derived from both correctness and student-reported Judgment of Learning.
- `F_t ∈ [0, 1]`: frustration cost. The affective profiler outputs a categorical label in `{none, low, medium, high, rage}`, mapped uniformly to `{0.00, 0.25, 0.50, 0.75, 1.00}` for inclusion in `J`.
- `C_t ∈ {1, 1.5, 2}`: instructional cost. A standard direct response costs `C_t = 1`; a scaffolded hint costs `C_t = 1.5`; a full partial-code handout costs `C_t = 2`. The scaling reflects the transfer of cognitive labor from student to system — higher `C_t` means less productive struggle and lower expected learning yield per interaction.
- `w₁, w₂, w₃, w₄ ≥ 0`: weighting hyperparameters. Initial reference values: `(w₁, w₂, w₃, w₄) = (1.0, 0.5, 0.5, 0.2)`. Mastery gain anchors the scale at 1.0; retention and frustration suppression are symmetric at 0.5; instructional cost is a weak regularizer at 0.2.

Each component optimizes a specific term in `J`:

| Component | Term optimized | Mechanism |
|---|---|---|
| BKT mastery gate | `w₁ · ΔK_t` | Routes interactions that generate tier-specific mastery evidence |
| SM-2 scheduler | `w₂ · R_t` | Schedules reviews at intervals that maximize long-term retention |
| Affective profiler | `−w₃ · F_t` | Triggers tone softening and pacing to suppress frustration cost |
| Prerequisite gate | `−w₄ · C_t` | Prevents cognitively overloaded interactions that inflate cost |
| UCB1 style selector | `w₁ · ΔK_t` (indirect) | Selects the teaching style that empirically maximizes engagement and mastery uptake |

**Important:** `J` is not jointly optimized in the current deployment — no gradients are computed over the full expression, and each component acts as a local optimizer of one term. `J` functions as a **system utility metric**: a precise statement of what the system is trying to achieve, used as the evaluation criterion for offline policy assessment and future reinforcement-learning-based optimization. Writing it down defines the target; optimizing it jointly is future work.

### 2.4 Architecture Overview

The system is organized around two parallel tracks — **IRL** (Instructor-Regulated Learning) and **SRL** (Self-Regulated Learning) — that converge at a two-stage security gate before routing to the pedagogical engine:

```
┌───────────────────────────────────────────┐   ┌────────────────────────────────────────────┐
│  IRL  (Instructor-Regulated)              │   │  SRL  (Self-Regulated Learning)            │
│                                           │   │                                            │
│  [A] INSTRUCTOR MODEL                     │   │  [B] PERCEPTION LAYER                      │
│  [C] DOMAIN MODEL                         │   │  [D] LEARNER MODEL                         │
└──────────────────────┬────────────────────┘   └───────────────────────┬────────────────────┘
                       └──────────────┬──────────────────────────────────┘
                                      ▼
                              [E] SECURITY MODEL
                              σ = φ(S_cog · S_acad · S_spam) · α(G_t)
                                      ▼
                          [F] PEDAGOGICAL MODEL (SRL Engine)
                                      ▼
                              [G] INTERFACE  →  [H] PERSISTENCE
```

The separation of IRL and SRL is deliberate. Instructor constraints ([A], [C]) are stable and change slowly — they define the boundary conditions of the learning environment. Student state ([B], [D]) changes rapidly with each interaction. Mixing them in a single model creates design conflicts; separating them makes each layer independently testable and maintainable.

---

## 3. Mathematical Formulation

This section consolidates the complete formal notation of the system for reference during paper writing.

### 3.1 Learner State Representation

For each student `u` and concept `c`, the learner state is a six-tuple:

```
S_{u,c}(t)  =  ( P_t^(1), P_t^(2), P_t^(3),  M_c(t),  F_t,  G_t )
```

where:
- `P_t^(k) ∈ [0, θ_max^(k)]` — BKT constrained belief-state estimate for subskill `k` (lower bound is 0, not `p_0^(k)`, because incorrect evidence reduces the estimate below the initialization prior)
- `M_c(t) ∈ [0, 1]` — EMA misconception score for concept `c`
- `F_t ∈ [0, 1]` — frustration level from affective profiler
- `G_t ∈ [0, 1]` — goal alignment score

The composite mastery score is:

```
S_c(t)  =  Σ_{k=1}^{3} P_t^(k)   ∈  [0,  Σ_k θ_max^(k)]  =  [0, 0.95]
```

`S_c(t)` is not a probability — it is a bounded composite scalar for dashboard display. It is notated `S` (score) rather than `P` (probability) to signal that it does not satisfy probability axioms: summing posteriors does not correspond to union probability unless the events are mutually exclusive, which they are not.

### 3.2 Subskill Definitions and Parameters

Three independent BKT HMMs, each corresponding to a Bloom's cognitive level [8]:

| Subskill `k` | Cognitive level | Evidence type | Prior `p_0^(k)` | Learning `p_T^(k)` | Slip `p_S^(k)` | Guess `p_G^(k)` | Ceiling `θ_max^(k)` |
|---|---|---|---|---|---|---|---|
| 1 | Declarative (Remember / Understand) | Quiz | 0.30 | 0.09 | 0.10 | 0.20 | 0.60 |
| 2 | Procedural (Apply) | Micro-challenge | 0.00 | 0.09 | 0.15 | 0.10 | 0.25 |
| 3 | Applied (Analyze / Evaluate) | Code review | 0.00 | 0.09 | 0.20 | 0.05 | 0.10 |

**Parameter grounding:**
- `p_0^(1) = 0.30`: students entering an introductory C course have some prior exposure to programming concepts; the non-zero prior encodes this background. Procedural and applied priors are zero, as these require deliberate practice to develop.
- `p_T^(k) = 0.09` across all tiers: initialization consistent with Baker et al.'s (2008) analysis of BKT parameters in introductory CS domains, where learning rates in the range 0.05–0.15 were found, with lower values appropriate for skills requiring substantial deliberate practice [29].
- `p_G^(1) = 0.20`: reflects susceptibility to correct guessing on declarative recall tasks (multiple-choice / short-answer format).
- `p_G^(3) = 0.05`: reflects the low probability of accidentally producing correct reasoning about code without genuine understanding.
- `p_S^(3) = 0.20`: reflects sensitivity to syntactic and execution errors on applied tasks even when underlying knowledge is present.
- Ceiling proportions (60% / 25% / 10%): follow Evidence-Centered Design principles [28] — evidence types mapping to lower Bloom's levels and collected more frequently contribute proportionally larger ceilings to the composite score.

### 3.3 Evidence-Type Routing

Each student interaction produces a tuple `(x_t, e_t)` where `x_t ∈ {0, 1}` is correctness and `e_t ∈ {quiz, micro, code}` is the evidence type. A binary selector routes each observation to exactly one subskill tracker:

```
δ^(k)(e_t)  =  𝟏[k corresponds to e_t]
```

Specifically: `δ^(1)(quiz) = 1`, `δ^(2)(micro) = 1`, `δ^(3)(code) = 1`, all others zero. When `δ^(k)(e_t) = 0`, tracker `k` is frozen: `P_{t+1}^(k) = P_t^(k)`.

This is the mathematical core of the Evidence Diversity resolution. Quiz evidence physically cannot update the procedural or applied trackers. Mastery of the procedural tracker is structurally unreachable from quiz evidence alone.

### 3.4 BKT Posterior Update

Let `P_t^(k) = P(z_c^(k) = 1 | x_{1:t}, e_{1:t})` be the marginal posterior for subskill `k` after `t` observations. When `δ^(k)(e_t) = 1`, the standard BKT update applies:

**Correct observation** (`x_t = 1`):
```
P(z^(k) = 1 | x_t = 1)  =  [P_t^(k) · (1 − p_S^(k))]
                             ─────────────────────────────────────────────────────
                             [P_t^(k) · (1 − p_S^(k))  +  (1 − P_t^(k)) · p_G^(k)]
```

**Incorrect observation** (`x_t = 0`):
```
P(z^(k) = 1 | x_t = 0)  =  [P_t^(k) · p_S^(k)]
                             ─────────────────────────────────────────────────────
                             [P_t^(k) · p_S^(k)  +  (1 − P_t^(k)) · (1 − p_G^(k))]
```

**Learning transition** (applied after the observation update):
```
P_{t+1}^(k)  =  min(  P(z^(k) = 1 | x_t)  +  [1 − P(z^(k) = 1 | x_t)] · p_T^(k),   θ_max^(k)  )
```

The ceiling `θ_max^(k)` is enforced by hard clipping at the update step, so `P_t^(k) ∈ [0, θ_max^(k)]` at all times. The lower bound is 0, not `p_0^(k)`: after an incorrect observation the BKT numerator shrinks, and the estimate can fall well below the initialization prior — for example, a single incorrect answer from `p_0^(1) = 0.30` reduces the declarative estimate to approximately 0.05.

**Probabilistic semantics.** Hard clipping at `θ_max^(k) < 1` departs from pure Bayesian inference — no well-defined likelihood function produces a natural ceiling at an arbitrary value below 1. The clipped quantity `P_t^(k)` is therefore more precisely a **constrained belief-state estimate** than a formal Bayesian posterior. This is a deliberate design choice grounded in Evidence-Centered Design principles [28]: the ceiling imposes a structural evidence-contribution cap so that no single evidence stream can dominate the composite mastery score, at the cost of strict Bayesian interpretation. Throughout this paper, `P_t^(k)` should be read as a bounded mastery estimate — it tracks the direction and magnitude of evidence correctly, but its absolute value is not interpretable as a probability under a formally specified generative model.

**Independence assumption.** The three subskills are modeled as conditionally independent: `P(z_c^(1), z_c^(2), z_c^(3)) = ∏_k P(z_c^(k))`. This is a tractability assumption, not a psychological claim. Declarative knowledge plausibly facilitates procedural performance, which in turn supports applied reasoning — dependencies well-documented in cognitive science (Anderson, 1983; Bloom, 1956 [8]). Modeling these dependencies explicitly would require a hierarchical or coupled latent-state model and substantially larger datasets for EM re-estimation. The independent-factor formulation follows the same tractability rationale as classical BKT [7]. The key distinction is between *conditional independence in state estimation* and *cognitive independence in reality* — the former is a computational modeling choice, the latter is a psychological claim. This system makes only the former. Relaxing this assumption using hierarchical Bayes or a dependency-augmented graphical model is a planned direction for future work.

**Parameter estimation.** The values in Section 3.2 are initialization defaults. Once sufficient interaction logs accumulate (approximately ≥200 interactions per concept per evidence type), parameters are re-estimated via Expectation-Maximization — specifically, the Baum-Welch algorithm applied to each subskill HMM independently. The initialization values then serve as priors for EM rather than fixed constants.

### 3.5 Mastery Decision Rule

The mastery decision decouples the evidence contribution cap (`θ_max^(k)`) from the evidence sufficiency threshold (`θ_mastery^(k)`):

```
m_c  =  𝟏[ ∀k : P_t^(k) ≥ θ_mastery^(k) ]
```

| Subskill `k` | Evidence ceiling `θ_max^(k)` | Mastery threshold `θ_mastery^(k)` | Ratio |
|---|---|---|---|
| 1 — Quiz | 0.60 | 0.57 | 95% |
| 2 — Micro | 0.25 | 0.24 | 96% |
| 3 — Code | 0.10 | 0.09 | 90% |

**Threshold grounding.** `θ_mastery^(k)` is set strictly below `θ_max^(k)` because the ceiling is enforced by hard clipping — `P_t^(k)` can reach `θ_max^(k)` exactly. Setting `θ_mastery^(k) = θ_max^(k)` would couple the mastery decision to the ceiling cap, collapsing two conceptually distinct constructs (evidence contribution limit and evidence sufficiency criterion) to the same number. The 5% buffer decouples them: a student at `P_t^(k) = θ_mastery^(k)` has demonstrated sufficient posterior confidence for progression but has not necessarily exhausted all discriminative evidence of type `k`. Any value in the range `(θ_max^(k) · 0.85, θ_max^(k))` preserves the behavioral semantics; the specific ratios are calibrated heuristics within this range.

**Structural implication.** A student who performs perfectly on quizzes accumulates `P_t^(1) → θ_max^(1) = 0.60`, which satisfies `P_t^(1) ≥ 0.57`. But `P_t^(2)` and `P_t^(3)` remain at their priors (0.00) — far below `θ_mastery^(2) = 0.24` and `θ_mastery^(3) = 0.09`. Therefore `m_c = 0` regardless of quiz performance. This property follows directly from `δ^(k)(e_t)`: since quiz evidence sets `δ^(2)(quiz) = δ^(3)(quiz) = 0`, the procedural and applied trackers receive no updates from quiz interactions. Quiz-only mastery is **structurally unreachable** — not a policy rule, but a mathematical consequence of the evidence routing function and the conjunctive criterion.

---

**Theorem (Evidence Diversity Guarantee).** Under the factored BKT model with evidence-routing function `δ^(k)(e_t)` and conjunctive mastery criterion, for any student interaction history `(x_{1:t}, e_{1:t})`:

> `m_c = 1` implies `∀k ∈ {1, 2, 3}: ∃τ ≤ t` such that `e_τ = e^(k)`.

That is, mastery cannot be declared unless at least one observation of **each** evidence type has been received.

**Proof.** Suppose `m_c = 1`, i.e., `P_t^(k) ≥ θ_mastery^(k)` for all `k`. Assume for contradiction that for some subskill `k'`, no observation of type `e^(k')` was ever received. Then `δ^(k')(e_τ) = 0` for all `τ ≤ t`. By the frozen-tracker rule, `P_τ^(k') = P_0^(k') = p_0^(k')` for all `τ`. For tiers `k' ∈ {2, 3}`, `p_0^(k') = 0.00 < θ_mastery^(k')`, so `P_t^(k') < θ_mastery^(k')` — contradiction. For tier `k' = 1`, `p_0^(1) = 0.30 < 0.57 = θ_mastery^(1)` — contradiction. Therefore at least one observation of each type must exist in the history. □

**Remark.** The theorem holds for any choice of `θ_mastery^(k) > p_0^(k)` for `k ∈ {2, 3}` and `θ_mastery^(1) > p_0^(1)`. The specific threshold values in Section 3.5 satisfy these inequalities by construction; they are not required for the guarantee to hold.

---

### 3.6 EMA Misconception State Variable

For concept `c`, the misconception score is an exponentially weighted state variable:

```
M_c(t+1)  =  λ · M_c(t)  +  (1−λ) · 𝟏[ conf_t ≥ θ_conf  ∧  x_t = 0 ]
```

with parameters `λ = 0.7` (persistence) and `θ_conf = 4` (top 40% of confidence on a 0–5 scale). A misconception is flagged when `M_c(t) ≥ θ_M = 0.25`. On a correct response, no increment is applied and the score decays:

```
M_c(t+1)  =  λ · M_c(t)
```

**Behavioral derivation.** After a single high-confidence error: `M_c = (1−λ) = 0.30` → flagged immediately. After one correct response: `0.30 × 0.7 = 0.21` → still flagged. After two correct responses: `0.21 × 0.7 = 0.147 < 0.25` → resolved. Two consecutive correct answers are required for resolution.

**Parameter grounding: `θ_M = 0.25`.** The threshold is uniquely constrained by two behavioral requirements given `λ`:
1. *Immediate detection*: `(1 − λ) · 1 > θ_M` → `θ_M < 0.30`
2. *Two-answer recovery*: `λ · (1 − λ) < θ_M` → `θ_M > 0.21`

Valid range: `(0.21, 0.30)`. The value `0.25` is the approximate midpoint, providing equal margin from both boundary conditions.

**Parameter grounding: `λ = 0.70`.** Selected so that the effective half-life of the misconception score under sustained correct responses is exactly 2 interactions: `λ^k < 0.5` first holds at `k = ⌈log(0.5)/log(0.7)⌉ = 2`. Two consecutive correct responses halve any accumulated score — consistent with evidence that misconceptions in science and programming require multiple corrective exposures before they are suppressed (Chi et al., 1994 [30]).

**Confidence threshold grounding: `θ_conf = 4`.** Targets the top 40% of expressed confidence on a six-point scale (0–5), consistent with Likert-scale conventions in educational measurement. Nelson & Narens (1990) distinguish between low-confidence errors (noise or genuine uncertainty) and high-confidence errors (indicative of stable misconceptions) [12]. A threshold of ≤ 3 would generate excessive false positives; a threshold of 5 would be too restrictive as students rarely express maximum confidence on novel material.

### 3.7 Spaced Repetition Schedule (SM-2)

The inter-repetition interval is updated after each review:

```
I(1) = 1d,    I(2) = 6d,    I(n) = round( I(n−1) · EF )   for  n ≥ 3

EF'  =  max( 1.3,   EF + 0.1 − (5−q) · (0.08 + (5−q) · 0.02) )

q  ∈ [0, 5]:
    5 = perfect recall (correct + high confidence)
    4 = correct with hesitation
    3 = correct but difficult  (pass threshold — interval extends)
    2 = wrong but close
    1 = wrong, low confidence
    0 = misconception (correct + wrong + high confidence error)
```

The SM-2 quality score `q` maps to the J-function retention signal: `R_t = 1` iff `q ≥ 3`.

### 3.8 Teaching Style Selection (UCB1)

Define:
- `r_t ∈ {0, 1}`: reward — 1 if thumbs-up, 0 if thumbs-down or no response
- `w_s`: cumulative wins for style `s ∈ S = {analogy, example, visual, Socratic, direct}`
- `n_s`: total presentations of style `s`
- `N = Σ_s n_s`: total interactions

Selection rule:
```
score_s(t)  =  +∞                              if  n_s = 0
               w_s / n_s + √(2 · ln N / n_s)   otherwise

style*(t)  =  argmax_{s ∈ S}  score_s(t)
```

The `+∞` initialization ensures all arms are explored before exploitation begins — the standard UCB1 cold-start convention (Auer et al., 2002 [13]). With `r_t ∈ {0, 1}` bounded, the expected cumulative regret satisfies:

```
E[R_N]  =  O( √(|S| · N · ln N) )
```

This sublinear regret guarantee holds under **stationary reward distributions** — i.e., when `μ_s = E[r_t | style = s]` does not change over time. In the tutoring setting, a student's style preferences may evolve as they become more comfortable with the material, representing a mild violation of stationarity. Under non-stationarity, the formal regret bound does not transfer unchanged. UCB1 is used here as a principled and empirically effective policy under approximate stationarity; algorithms with explicit non-stationarity handling (e.g., sliding-window UCB or discounted UCB) would provide formal guarantees in the fully non-stationary case and represent a natural extension.

### 3.9 Security Gate Decomposition

The security gate `σ(x_t) ∈ {0, 1}` decomposes into two stages:

**Stage 1 — Structural Feasibility Gate (hard binary):**
```
φ(x_t)  =  S_cog(x_t) · S_acad(x_t) · S_spam(x_t)

S_cog(x_t)   =  𝟏[ c(x_t) ∈ T_enabled  ∧  ∀r ∈ prereqs(c(x_t)): r ∈ K_t ]
S_acad(x_t)  =  𝟏[ ¬∃p ∈ Φ_blocked: match(x_t, p) ]
S_spam(x_t)  =  𝟏[ N_t < N_max ]
```

**Stage 2 — Pedagogical Alignment Check (soft threshold):**
```
G_t     =  ( cos_sim( h(x_t), h(g_0) ) + 1 ) / 2   ∈  [0, 1]
α(x_t)  =  𝟏[ G_t ≥ θ_goal ]
```

where `h(·)` produces L2-normalized sentence embeddings. Raw cosine similarity is in `[-1, 1]`; the linear rescaling `(cos_sim + 1) / 2` maps this to `[0, 1]` without distorting the ordinal ranking, guaranteeing the stated range. The threshold `θ_goal` is calibrated to maximize F1 on human-labeled aligned vs. misaligned interactions and is interpreted on the rescaled [0, 1] scale.

**Composite gate:**

```
σ(x_t)  =  φ(x_t) · α(x_t)   ∈  {0, 1}
```

`φ(x_t) = 0` collapses `σ` to 0 regardless of `α`, preserving the fail-safe property: no alignment score can override a structural block.

---

### 3.10 Adaptive Threshold Calibration (Empirical-Bayes-Inspired Shrinkage, GMM)

**Motivation.** Mastery thresholds `θ_mastery^(k)` are initialized from literature-backed defaults (Section 3.5). These defaults encode a principled starting point but cannot account for concept-specific difficulty, cohort characteristics, or deployment context. Rather than treating them as fixed design choices, the system recalibrates them automatically as population-level interaction data accumulates — starting from the prior and converging toward an empirically grounded value.

**Observation buffer.** Every BKT posterior update appends `P_t^(k)` to a per-(concept, tier) sliding window buffer of the last `N = 500` observations (population-level, not per-student). The buffer is purely population data — it aggregates posteriors across all students for that concept.

**Recalibration trigger.** The first calibration fires when the buffer reaches `N_min = 50` observations. Subsequent recalibrations fire every additional `N_step = 50` observations. The trigger is incremental, not epoch-based, so the threshold adapts continuously rather than waiting for a semester boundary.

**Step 1 — Fit a two-component GMM via EM.**

Model the buffer as a mixture of two Gaussians — a mastered cluster and a not-yet-mastered cluster:

```
p(x)  =  π₁ · N(x; μ₁, σ₁²)  +  π₀ · N(x; μ₀, σ₀²)
```

Parameters `(μ₁, σ₁, π₁, μ₀, σ₀, π₀)` are estimated via Expectation-Maximization with `n_init = 5` random restarts. The mastered cluster is identified as the component with the higher mean: `μ_mastered = max(μ₁, μ₀)`.

**Gaussianity caveat.** BKT belief-state estimates are bounded in `[0, θ_max^(k)]`, not on the real line, so the Gaussian mixture is a misspecified distributional model: Gaussians have infinite support and will assign non-trivial probability mass outside the valid range, particularly for tiers with tight ceilings (e.g., code tier ceiling 0.10). Deriving a 10th-percentile threshold from a misspecified Gaussian on bounded data introduces bias whose direction and magnitude depend on how concentrated the empirical distribution is near the boundary. The theoretically correct replacement is a **Beta Mixture Model (BMM)**: Beta distributions are strictly bounded on `[0, 1]` (the calibration data can be linearly rescaled to this range per tier), naturally model bimodal bounded data, and have a well-defined EM algorithm. BMM implementation is planned as a direct replacement of the current Gaussian EM. In the interim, the Fisher-ratio noise gate limits the damage: when the Gaussian fit is poorly resolved (low cluster separation), the blending weight `w → 0` and the system falls back to the literature prior. This fallback is triggered precisely in the cases where Gaussian misspecification is most severe — sparse or boundary-concentrated data — providing a principled safeguard until BMM is deployed.

**Step 2 — Measure cluster separation (noise level).**

Fisher's discriminant ratio quantifies how cleanly the two clusters separate in the posterior space:

```
Fisher_ratio  =  (μ_mastered − μ_other)²  /  (σ_mastered² + σ_other²)

noise_level   =  1 / (1 + Fisher_ratio)   ∈  (0, 1)
```

`noise_level → 0`: clusters are well-separated; the data is trustworthy.
`noise_level → 1`: clusters heavily overlap; the data is too noisy to calibrate reliably.

**Step 3 — Derive θ_GMM from the mastered cluster.**

The calibrated threshold is the **10th percentile** of the mastered cluster distribution — the value above which 90% of students in the mastered cluster fall:

```
θ_GMM  =  μ_mastered  −  z_{0.10} · σ_mastered
         =  μ_mastered  −  1.282 · σ_mastered
```

This means: a student whose BKT posterior equals `θ_GMM` is at the boundary where 90% of the population's mastered-cluster students would be considered more confident. `θ_GMM` is clamped to `(0, θ_max^(k) · 0.99)`.

**Step 4 — Bayesian blending with the literature prior.**

The new threshold is a noise-weighted blend of the literature prior and the GMM estimate:

```
w      =  1 − noise_level              ∈  [0, 1]
θ_new  =  (1 − w) · θ_prior  +  w · θ_GMM
```

| Regime | noise_level | w | Result |
|---|---|---|---|
| Cold start (< N_min observations) | — | 0 | `θ_new = θ_prior` (literature default) |
| High noise (clusters overlap) | → 1 | → 0 | Trusts literature prior |
| Low noise (clean separation) | → 0 | → 1 | Trusts empirical GMM estimate |
| Asymptotic (large N, clear clusters) | ≈ 0 | ≈ 1 | Fully data-driven |

The prior is never discarded — it is down-weighted as evidence quality improves. This prevents pathological behavior when data is sparse or noisy.

**Three-level adaptation summary.**

| Level | What adapts | Mechanism | Timescale |
|---|---|---|---|
| Per-student state | `P_t^(k)`, SM-2 ease factor, UCB1 win rates, `M_c(t)` | BKT update, SM-2 formula, bandit, EMA | Every interaction |
| Threshold calibration | `θ_mastery^(k)` per concept per tier | GMM + Bayesian blending | Every N_step = 50 new observations |
| Model parameters | `p_T^(k)`, `p_G^(k)`, `p_S^(k)` | Baum-Welch EM on interaction logs | After ≥ 200 interactions per tier per concept |

**Implementation.** The calibrator runs in the same synchronous request path as the BKT update. The GMM EM runs in pure NumPy with no external ML dependency. For `n = 500` observations and 200 EM iterations, wall-clock time is under 5 ms on a CPU, adding negligible latency to the response path. The calibration buffer and threshold history are persisted in SQLite (`calibration_buffer`, `threshold_history` tables) for auditability and cross-session persistence.

---

### 3.11 Hyperparameter Classification

All hyperparameters in the system fall into one of four categories. This classification determines how each value is defended and how it can be updated post-deployment.

| Parameter | Value | Category | Grounding |
|---|---|---|---|
| **BKT structure** | | | |
| Evidence routing `δ^(k)(e_t)` | — | Structural invariant | Follows from problem definition; independent of parameter values |
| Conjunctive mastery rule | — | Structural invariant | Follows from problem definition; independent of parameter values |
| `K_t = {c : m_c = 1}` | — | Structural invariant | Connects SRL mastery to IRL prerequisite enforcement |
| **BKT emission parameters** | | | |
| Prior `p_0^(1)` (quiz) | 0.30 | Domain reasoning | Prior programming exposure for entering C students |
| Prior `p_0^(2,3)` (micro, code) | 0.00 | Domain reasoning | Procedural / applied skills require deliberate practice |
| Learning rate `p_T^(k)` | 0.09 | Literature default | Baker et al. (2008): 0.05–0.15 range for intro CS [29] |
| Slip `p_S^(1,2,3)` | 0.10, 0.15, 0.20 | Literature default + domain | Higher slip for higher Bloom's level; Baker et al. (2008) |
| Guess `p_G^(1,2,3)` | 0.20, 0.10, 0.05 | Literature default + domain | Higher guess for recall tasks; lower for applied reasoning |
| **Ceiling and threshold values** | | | |
| Ceilings `θ_max^(k)` | 0.60, 0.25, 0.10 | ECD design choice | Proportional to relative collection frequency by Bloom's level [28] |
| Initial thresholds `θ_mastery^(k)` | 0.57, 0.24, 0.09 | Warm-start prior | ~95% of ceiling; numerical stability buffer; updated by GMM calibrator |
| **Calibration parameters** | | | |
| Buffer window `N` | 500 | Engineering choice | Balances recency and stability of GMM fit |
| Recalibration trigger `N_min / N_step` | 50 / 50 | Engineering choice | Minimum for stable GMM; incremental rather than epoch-based |
| GMM percentile | 10th | Design choice | 90% of mastered cluster above threshold; conservative but achievable |
| **EMA misconception** | | | |
| Persistence `λ` | 0.70 | Behaviorally derived | Half-life = 2 correct responses: `⌈log(0.5)/log(0.7)⌉ = 2` |
| Flag threshold `θ_M` | 0.25 | Behaviorally derived | Midpoint of valid range (0.21, 0.30) uniquely determined by λ |
| Confidence threshold `θ_conf` | 4 | Domain convention | Top 40% of Likert scale; Nelson & Narens (1990) [12] |
| **Security gate** | | | |
| Goal alignment `θ_goal` | Calibrated | Post-deployment | F1 sweep on human-labeled interaction pairs |
| **Objective function** | | | |
| Weights `(w₁, w₂, w₃, w₄)` | (1.0, 0.5, 0.5, 0.2) | Heuristic priority | Not jointly optimized; reference values for initial deployment |
| **SM-2** | | | |
| `EF_init`, `EF_min` | 2.5, 1.3 | Prior work defaults | Wozniak (1990) empirically derived values [10] |

The only category that requires full defense against "why this specific value?" is **ECD design choices** (ceilings). The defense is: (1) the structural invariant holds regardless of specific ceiling values; (2) the proportionality principle is grounded in ECD [28]; (3) the specific values are warm-start priors updated by the GMM calibrator.

---

## 4. System Components with Literature Backing

---

### 4.1 [A] Instructor Model — IRL Layer

**What it does:** Stores and enforces instructor-set rules: which topics are unlocked, assignment lifecycle, classroom video availability, and user access control.

**Formal representation:**
- Topic access: `t_i ∈ enabled_topics ⊆ T`
- Assignment lifecycle: `state ∈ {PENDING, SUBMITTED, GRADED}`

**Why it exists:** Intelligent tutoring systems historically modeled only the student. The need for an explicit instructor model was recognized early — Anderson et al. (1995) noted that Cognitive Tutors required teacher oversight to be effective in real classrooms [1]. The concept of Instructor-Regulated Learning (IRL) as a complement to SRL formalizes that the instructor sets the outer constraints within which student self-regulation operates [2].

**Connection to core contribution:** The instructor model controls which concepts are eligible for evidence collection. If an instructor disables "Pointers," no quiz, micro-challenge, or code-review evidence can be collected for that concept — the BKT trackers for Pointers receive no updates. This makes the factored BKT's evidence routing dependent on instructor-regulated access, not just student behavior.

**Literature:**
- [1] Anderson, J. R., Corbett, A. T., Koedinger, K. R., & Pelletier, R. (1995). Cognitive tutors: Lessons learned. *Journal of the Learning Sciences, 4*(2), 167–207.
- [2] Pintrich, P. R. (2000). The role of goal orientation in self-regulated learning. In *Handbook of Self-Regulation* (pp. 451–502). Academic Press.

---

### 4.2 [B] Perception Layer — SRL Layer

**What it does:** A four-stage pipeline that processes every student message before routing it to the pedagogical engine:

1. **Query Contextualizer** — an LLM rewrites ambiguous queries using conversation history (coreference resolution), then validates entities against the Neo4j knowledge graph
2. **Intent Classifier** — a deterministic rule cascade (keyword matching) followed by a MiniLM embedding fallback: `intent* = argmax_k [sim(q, iₖ)]`
3. **Named Entity Recognizer** — GLiNER [3] combined with a domain-specific C programming vocabulary (`C_CONCEPT_TERMS`) to extract concept entities
4. **Affective Profiler** — regex rules for explicit frustration signals, backed by a DistilRoBERTa sentiment model: `F_t ∈ {none, low, medium, high, rage}` → mapped to `[0, 0.25, 0.50, 0.75, 1.00]`

**Why it exists:** The Perception Layer implements the need to detect "affective-cognitive" states — D'Mello & Graesser (2012) showed that learning is not purely cognitive, and a system that ignores emotional state will systematically fail frustrated students [4]. The two-stage intent classifier (rule → embedding) balances precision on common cases with robustness on novel phrasing.

**Connection to core contribution:** The NER stage determines which concept `c` an interaction relates to, and the intent classifier determines which BKT evidence tier it belongs to (quiz → declarative, code attempt → procedural, code review → applied). This is the upstream computation that feeds the evidence routing function `δ^(k)(e_t)`.

**Literature:**
- [3] Zaratiana, U., Tomeh, N., & Shi, C. (2023). GLiNER: Generalist model for named entity recognition using bidirectional transformer. *arXiv:2311.08526*.
- [4] D'Mello, S., & Graesser, A. (2012). Dynamics of affective states during complex learning. *Learning and Instruction, 22*(2), 145–157.

---

### 4.3 [C] Domain Model — IRL Layer

**What it does:** Stores the C programming domain as a knowledge graph (Neo4j) with prerequisite edges (`REQUIRES_UNDERSTANDING_OF`), and a vector store (ChromaDB) for semantic retrieval of course materials.

**Core operations:**
- **Prerequisite check:** `∃(c → r) ∈ E ∧ r ∉ K_known → gate fires`
- **Semantic retrieval:** `sim = cosine(embed(q), embed(chunkᵢ))`, retrieve `top-k = argmax_i [sim(q, chunkᵢ)]`

**Why it exists:** Liang et al. (2018) showed that automatically recovering prerequisite concept relations significantly improves the coherence of adaptive learning paths [5]. The knowledge graph enforces hard prerequisite constraints (no pointer content until arrays are mastered); the vector store retrieves the most contextually relevant course material (RAG grounding, Lewis et al., 2020 [6]).

**Connection to core contribution:** The prerequisite check in `S_cog` verifies that the mastery set `K_t` satisfies all required antecedents. Crucially, `K_t` is populated only when `m_c = 1` — the conjunctive mastery rule. A student who "mastered" arrays via quiz evidence only will not have arrays in `K_t` (since the procedural and applied trackers are below threshold), and therefore cannot unlock pointer content. The prerequisite gate propagates the strictness of the factored mastery criterion through the entire curriculum.

**Literature:**
- [5] Liang, C., Wu, Z., Huang, W., & Giles, C. L. (2018). Recovering concept prerequisite relations from university course dependencies. *AAAI-18*.
- [6] Lewis, P., Perez, E., Piktus, A., et al. (2020). Retrieval-augmented generation for knowledge-intensive NLP tasks. *NeurIPS 2020*.

---

### 4.4 [D] Learner Model — SRL Layer

This is the most theoretically rich component and the primary site of the Evidence Diversity resolution. It maintains six state variables updated with each interaction.

---

#### 4.4.1 K_t — Mastery State (Factored Multi-Skill BKT)

The full mathematical formulation is given in Section 3.1–3.5 above. See that section for all equations, parameter tables, independence assumption justification, and the derivation of the structural impossibility property.

**Why this formulation over alternatives?** Standard BKT [7] uses a single latent state per concept and cannot distinguish between a student who recalls a definition and one who writes correct code — both produce the same posterior update to the same variable. DKT [31] and DKVMN [32] improve multi-skill accuracy but do not enforce evidence routing or provide interpretable per-cognitive-level posteriors. Cognitive Diagnostic Models [33] require a static expert-designed Q-matrix and do not update online. The factored three-tracker BKT fills the gap: it is multi-dimensional, dynamic, evidence-routed, conjunctive, and interpretable.

**Literature:**
- [7] Corbett, A. T., & Anderson, J. R. (1994). Knowledge tracing: Modeling the acquisition of procedural knowledge. *User Modeling and User-Adapted Interaction, 4*(4), 253–278.
- [8] Bloom, B. S. (1956). *Taxonomy of educational objectives: Cognitive domain.* Longmans, Green.
- [28] Mislevy, R. J., Steinberg, L. S., & Almond, R. G. (2003). On the structure of educational assessments. *Measurement: Interdisciplinary Research and Perspectives, 1*(1), 3–62.
- [29] Baker, R. S., Corbett, A. T., & Aleven, V. (2008). More accurate student modeling through contextual estimation of slip and guess probabilities in Bayesian knowledge tracing. *ITS 2008*, pp. 406–415.
- [31] Piech, C., Bassen, J., Huang, J., Ganguli, S., Sahami, M., Guibas, L., & Paepcke, A. (2015). Deep knowledge tracing. *NeurIPS 2015*.
- [32] Zhang, J., Shi, X., King, I., & Yeung, D. Y. (2017). Dynamic key-value memory networks for knowledge tracing. *WWW 2017*.
- [33] Tatsuoka, K. K. (1983). Rule space: An approach for dealing with misconceptions based on item response theory. *Journal of Educational Measurement, 20*(4), 345–354.
- [34] Reckase, M. D. (2009). *Multidimensional item response theory.* Springer.

---

#### 4.4.2 SM-2 — Spaced Repetition Schedule

**What it does:** Schedules when each concept should next be reviewed. Full algorithm in Section 3.7.

**Why it exists:** The forgetting curve, documented by Ebbinghaus (1885), shows that memory decays exponentially without reinforcement [9]. Spaced repetition counteracts this by scheduling reviews at increasing intervals. The SM-2 algorithm (Wozniak, 1990 [10]) adapts the interval based on recall quality `q`. Cepeda et al. (2006) confirmed in a meta-analysis of 317 experiments that distributed practice substantially outperforms massed practice for long-term retention [11].

The quality score `q` is derived from both correctness and self-reported confidence (Judgment of Learning, JoL), following the metacognitive monitoring framework of Nelson & Narens (1990) [12]. Using JoL ensures that a correct answer with low confidence is treated differently from a correct answer with high confidence — the former suggests fragile knowledge that should be reviewed sooner.

**Connection to core contribution:** SM-2 operates on the mastered concept set `K_t`. Because `K_t` is populated only by the conjunctive mastery rule, spaced repetition is only scheduled for concepts where all three cognitive dimensions have been demonstrated — never for quiz-only "mastery." This ensures that review scheduling reinforces genuine multi-dimensional competency, not declarative recall alone.

**Literature:**
- [9] Ebbinghaus, H. (1885). *Über das Gedächtnis.* Duncker & Humblot.
- [10] Wozniak, P. A. (1990). *Optimization of learning.* SuperMemo World.
- [11] Cepeda, N. J., Pashler, H., Vul, E., Wixted, J. T., & Rohrer, D. (2006). Distributed practice in verbal recall tasks. *Psychological Bulletin, 132*(3), 354–380.
- [12] Nelson, T. O., & Narens, L. (1990). Metamemory: A theoretical framework and new findings. *Psychology of Learning and Motivation, 26*, 125–173.

---

#### 4.4.3 C_s — Teaching Style Selection (UCB1 Multi-Armed Bandit)

**What it does:** Selects the teaching style `s* ∈ S = {analogy, example, visual, Socratic, direct}` that maximizes cumulative student engagement. Full algorithm in Section 3.8.

**Why it exists:** Students differ substantially in how they receive new information, and a static style assignment fails to adapt to individual preference. Treating style selection as a multi-armed bandit problem — with exploration to discover good styles and exploitation of identified preferences — is motivated by Auer et al. (2002) [13]. Clement et al. (2015) demonstrated that bandit algorithms outperform fixed-curriculum approaches in ITS by adapting to learner responses in real time [14].

The reward signal `r_t ∈ {0, 1}` is explicit (thumbs up/down) rather than inferred from response time or quiz performance, making the update semantically clean: `r_t = 1` means "the student explicitly indicated this response was helpful." Unlike engagement-proxy signals (dwell time, response length), this is semantically unambiguous.

**Distinction from prior work:** Clement et al. (2015) [14] applied bandit algorithms to *content sequencing* — which topic to present next. This system applies UCB1 to *teaching style* — how content is delivered — while the factored BKT determines what constitutes demonstrated competency. The two are orthogonal: style selection personalizes delivery; the mastery model enforces cognitive standards.

**Literature:**
- [13] Auer, P., Cesa-Bianchi, N., & Fischer, P. (2002). Finite-time analysis of the multiarmed bandit problem. *Machine Learning, 47*(2-3), 235–256.
- [14] Clement, B., Roy, D., Oudeyer, P. Y., & Lopes, M. (2015). Multi-armed bandits for intelligent tutoring systems. *Journal of Educational Data Mining, 7*(2), 20–48.

---

#### 4.4.4 M_t — Misconception Tracking

**What it does:** Maintains a continuous EMA misconception score per concept. Full formulation in Section 3.6.

**Why it exists:** Confrey (1990) established that misconceptions are not random errors — they are systematic, stable beliefs that resist correction because the student is confident in them [15]. Ohlsson (1994) formalized constraint-based student modeling around the detection of such incorrect but stable knowledge states [16]. The EMA formulation converts the binary flag-or-not decision into a continuous state variable, enabling the system to distinguish between a student who made one overconfident mistake and one who has demonstrated a persistent pattern of high-confidence errors — and to calibrate intervention intensity accordingly.

**Relationship to BKT:** The BKT model records the probability of knowing. The misconception tracker records the probability of stably not-knowing despite high confidence. A student whose `P_t^(1)` is rising (learning detected by BKT) but whose `M_c(t) ≥ θ_M` (misconception still active) is a student who is getting some answers right but still harboring a systematic incorrect belief — a pattern the BKT posterior alone cannot represent.

**Literature:**
- [15] Confrey, J. (1990). A review of the research on student conceptions in mathematics, science, and programming. *Review of Research in Education, 16*, 3–56.
- [16] Ohlsson, S. (1994). Constraint-based student modeling. In *Student Modeling: The Key to Individualized Knowledge-Based Instruction* (pp. 167–189). Springer.
- [30] Chi, M. T. H., de Leeuw, N., Chiu, M. H., & LaVancher, C. (1994). Eliciting self-explanations improves understanding. *Cognitive Science, 18*(3), 439–477.

---

#### 4.4.5 F_t, G_t, N_t — Affective, Goal, and Off-Topic State

- **F_t** (Affective state): Passed from the Perception Layer. Used downstream to soften tone, offer encouragement, and avoid increasing cognitive load when a student is frustrated. Grounded in Picard's (1997) affective computing framework [17] and D'Mello & Graesser's (2012) finding that frustration is the dominant negative affect during complex learning [4].
- **G_t** (Goal alignment score): `G_t = (cos_sim(h(x_t), h(g_0)) + 1) / 2 ∈ [0, 1]`. Raw cosine similarity lies in `[-1, 1]`; the linear rescaling maps it to `[0, 1]` without distorting ordinal rank. Measures how well the current interaction aligns with the student's stated learning goal. Grounded in Zimmermann's (2000) SRL theory, which positions goal alignment as central to self-regulation [18].
- **N_t** (Off-topic strike counter): `N_{t+1} = N_t + 1` if `OFF_TOPIC`. Feeds into the Security Model's spam gate.

**Literature:**
- [17] Picard, R. W. (1997). *Affective computing.* MIT Press.
- [18] Zimmermann, B. J. (2000). Attaining self-regulation: A social cognitive perspective. In *Handbook of Self-Regulation* (pp. 13–39). Academic Press.

---

### 4.5 [E] Security Model

**What it does:** Acts as a synchronous, two-stage binary gate — every interaction must pass all structural constraints and a pedagogical alignment check before routing to the pedagogical engine. Full formulation in Section 3.9.

**Why this decomposition?** The three signals in `φ` share the same semantic role: each is a hard binary predicate representing a necessary condition for a response to be structurally permissible. The product-of-indicators within `φ` is equivalent to their logical conjunction — a fail-safe gate where any single violated constraint is sufficient for rejection. All three are binary by nature (not thresholded continuous signals), making the product semantics exact.

`G_t` is semantically distinct — a continuous cosine similarity score thresholded at a calibrated `θ_goal`. Grouping it with the hard binary signals in `φ` would conflate two different mathematical objects. Separating it as `α(x_t)` makes the distinction explicit: `φ = 0` means "structurally impermissible"; `α = 0` means "pedagogically misaligned." The two failure modes have different recovery implications — a `φ`-blocked interaction requires a policy-level fix (unlock the topic, address the integrity concern); an `α`-blocked interaction requires a pedagogical redirect (help the student reconnect with their goal).

Crucially, `φ(x_t) = 0` collapses `σ` to 0 regardless of `α`, preserving the fail-safe property: no alignment score can override a structural block.

**Asynchronous semantic monitor (non-blocking):**
```
f_sem : X → {flag, pass}     (async, latency-tolerant; output → audit log only)
```

`f_sem` runs a heavier LLM-based semantic analysis after the synchronous gate decision and writes its output to an instructor-visible audit log. It does not modify `σ` and introduces no latency to the student-facing response path.

| Signal | Type | Condition for block | Basis |
|---|---|---|---|
| `S_cog` | Hard (φ) | Topic not enabled or prerequisite unmet | Sweller (1988) [19]; Liang et al. (2018) [5] |
| `S_acad` | Hard (φ) | Academic integrity pattern matched | Course policy; Φ_blocked |
| `S_spam` | Hard (φ) | Off-topic strike count ≥ N_max | Abuse prevention |
| `α` | Soft (threshold) | G_t < θ_goal | Pintrich (2000) [2]; Zimmermann (2000) [18] |
| `f_sem` | Async monitor | — (non-blocking) | Audit and instructor review |

**Why this exists:** Cognitive Load Theory (Sweller, 1988) establishes that presenting material beyond a student's current competence actively impairs learning [19] — `S_cog` implements this as a structural prerequisite gate. Goal alignment (`α`) is grounded in Pintrich's (2000) finding that interactions misaligned with a student's stated learning goal disrupt self-regulated learning processes [2].

**Literature:**
- [2] Pintrich, P. R. (2000). The role of goal orientation in self-regulated learning. In *Handbook of Self-Regulation* (pp. 451–502). Academic Press.
- [5] Liang, C., Wu, Z., Huang, W., & Giles, C. L. (2018). Recovering concept prerequisite relations from university course dependencies. *AAAI-18*.
- [18] Zimmermann, B. J. (2000). Attaining self-regulation: A social cognitive perspective. In *Handbook of Self-Regulation* (pp. 13–39). Academic Press.
- [19] Sweller, J. (1988). Cognitive load during problem solving: Effects on learning. *Cognitive Science, 12*(2), 257–285.

---

### 4.6 [F] Pedagogical Model — SRL Engine

**What it does:** Routes each interaction to the appropriate pedagogical agent, applies UCB1 style selection, and updates the Learner Model after each interaction.

#### Agents

| Agent | Trigger | Method | BKT Evidence |
|---|---|---|---|
| Scaffolding | PROBLEM intent | SRL FSM, Remediation Ladder | — |
| Examiner | QUIZ intent | 2-tier grading + JoL | `evidence_type = quiz` → `P_t^(1)` (ceiling 0.60) |
| Code Reviewer | REVIEW intent | Sandwich method | `evidence_type = code` → `P_t^(3)` (ceiling 0.10) |
| Micro-Challenge | CONCEPT (post-teach) | Code attempt detection | `evidence_type = micro` → `P_t^(2)` (ceiling 0.25) |
| Prereq Gate | CONCEPT / PROBLEM | Neo4j edge check ← [C] | — |
| Socratic Agent | Fallback | CoT + Hybrid RAG | — |

**Agent-to-tier mapping.** Each pedagogical agent produces evidence of a specific type, which is then routed to the corresponding BKT tracker. This is the operational realization of evidence routing: the Examiner agent generates quiz evidence (`δ^(1) = 1`); the Micro-Challenge agent generates procedural evidence (`δ^(2) = 1`); the Code Reviewer generates applied evidence (`δ^(3) = 1`). No agent generates multi-tier evidence in a single interaction, preserving the routing invariant.

**Why the Scaffolding Agent?** Vygotsky's (1978) Zone of Proximal Development defines the gap between what a student can do alone and what they can do with guidance [20]. Wood, Bruner & Ross (1976) operationalized this as scaffolding — support that is gradually removed as competence increases [21]. The SRL Finite State Machine tracks whether a student is in the early, middle, or late stages of problem-solving and adjusts support accordingly.

**Why the Examiner Agent?** The Testing Effect (Roediger & Karpicke, 2006) shows that retrieving information from memory strengthens retention far more than re-reading [22]. The Examiner provides regular retrieval practice, with the JoL confidence rating used to calibrate both the SM-2 quality score and the BKT update.

**Why the Sandwich Method (Code Reviewer)?** Hattie & Timperley (2007) identify feedback that addresses the task (not the person) as the most effective form for learning [23]. The sandwich method (positive → constructive → positive) delivers corrective feedback without triggering defensive behavior.

**Why the Socratic Agent?** Collins & Stevens (1982) formalized Socratic inquiry as a teaching strategy that forces students to articulate and defend their understanding rather than passively receive information [24]. The agent uses Chain-of-Thought prompting to guide LLM responses toward asking clarifying questions.

**Literature:**
- [20] Vygotsky, L. S. (1978). *Mind in society.* Harvard University Press.
- [21] Wood, D., Bruner, J. S., & Ross, G. (1976). The role of tutoring in problem solving. *Journal of Child Psychology and Psychiatry, 17*(2), 89–100.
- [22] Roediger, H. L., & Karpicke, J. D. (2006). Test-enhanced learning. *Psychological Science, 17*(3), 249–255.
- [23] Hattie, J., & Timperley, H. (2007). The power of feedback. *Review of Educational Research, 77*(1), 81–112.
- [24] Collins, A., & Stevens, A. L. (1982). Goals and strategies of inquiry teachers. In *Advances in Instructional Psychology* (Vol. 2, pp. 65–119). Erlbaum.

---

### 4.7 [G] Interface

**What it does:** Delivers responses via Server-Sent Events (SSE) for real-time token streaming, renders Mermaid diagrams for visual learners, and provides a Classroom module with:
- `context = transcript[t=0 … t_pause]` — answers restricted to watched content
- `meta = summary(full_transcript)` — high-level video overview at any timestamp
- Bi-directional timestamp hints: `argmax_seg [keywords ∩ q] ≥ 2` for both past and future segments

**Why bi-directional hints?** Forward hints support **anticipatory attention** — students know something relevant is coming, which primes encoding; backward hints support **retrieval practice** — the student is directed to re-encounter content they may have processed too shallowly the first time. Both effects are consistent with the elaborative interrogation literature (Pressley et al., 1992) [25].

The keyword match threshold of **two or more** (after stop-word removal) balances precision and recall: a single keyword match would trigger hints on nearly every question (common domain terms like "variable" or "pointer" appear throughout any C programming lecture transcript), while a threshold of three would suppress hints for short, specific questions. The **30-second context window** for grouping adjacent transcript segments is calibrated to typical lecture pacing — most individual explanatory units take 20–40 seconds.

**Why Mermaid diagrams?** Paivio's (1991) Dual Coding Theory proposes that information encoded in both verbal and visual form is retained more robustly than information encoded in only one modality [26]. Generating diagrams alongside text explanations creates dual-coded representations for complex C programming concepts.

**Literature:**
- [25] Pressley, M., Wood, E., Woloshyn, V. E., Martin, V., King, A., & Menke, D. (1992). Encouraging mindful use of prior knowledge. *Educational Psychologist, 27*(1), 91–109.
- [26] Paivio, A. (1991). Dual coding theory: Retrospect and current status. *Canadian Journal of Psychology, 45*(3), 255–287.

---

### 4.8 [H] Persistence

**What it does:** Stores all learner state that must survive across sessions:

```
user_knowledge:
  p_mastery_quiz  ∈ [0, 0.60]        ← P_t^(1): declarative posterior
  p_mastery_micro ∈ [0, 0.25]        ← P_t^(2): procedural posterior
  p_mastery_code  ∈ [0, 0.10]        ← P_t^(3): applied posterior
  p_mastery       = Σ tiers ∈ [0, 0.95]  ← composite (dashboard display only)
  interval_days · ease_factor · due_date · review_count   ← SM-2 state

learning_profile (JSON):
  win_rates: {style: {wins, total}}   ← UCB1 state
  misconceptions: [M_c(t), …]        ← EMA misconception scores
  frustration_level · skipped_challenges
```

**Why it exists:** Adaptive tutoring that resets at each session cannot accumulate the longitudinal evidence needed to distinguish a student who consistently struggles with pointers from one who is having a bad day. Mastery learning (Bloom, 1968) requires tracking competency over time and gating progression on demonstrated mastery — not on time spent [27]. Persistent storage of all three BKT tier posteriors is especially critical: the evidence diversity resolution only works if the system remembers that a student is strong on quizzes but weak on code reviews across sessions, not just within a single interaction.

**Literature:**
- [27] Bloom, B. S. (1968). Learning for mastery. *Evaluation Comment, 1*(2), 1–12.

---

## 5. Novelty and Contributions

### 5.1 Primary Contribution: Factored Multi-Skill BKT with Evidence Routing and Conjunctive Mastery

**What is new:** Standard BKT represents concept mastery as a single latent binary variable and updates it with any student response regardless of its cognitive type. This system replaces the scalar latent state with a factored vector `z_c = (z_c^(1), z_c^(2), z_c^(3))`, one component per Bloom's cognitive level, each tracked by an independent HMM. A binary evidence-routing function `δ^(k)(e_t)` ensures that each observation updates exactly one tracker. The mastery decision `m_c = 𝟏[∀k: P_t^(k) ≥ θ_mastery^(k)]` requires the conjunctive satisfaction of per-tier sufficiency thresholds.

**What this provides:**
1. **Structural evidence specificity:** Quiz evidence updates only the declarative tracker. It is mathematically impossible for quiz performance to advance the procedural or applied posteriors. The impossibility is not a policy rule — it is a direct consequence of the routing function `δ^(k)(e_t)`.
2. **Closed-form interpretability:** The mastery criterion has an exact algebraic expression. Each posterior `P_t^(k)` has a direct cognitive interpretation (probability of declarative / procedural / applied knowledge). There is no neural approximation.
3. **Resolution of the Evidence Diversity Problem:** Student A (quiz-proficient, code-deficient), Student B (code-proficient, reasoning-deficient), and Student C (fully competent) receive distinct learner model representations. Only Student C satisfies `m_c = 1`.

**What this is not:** This is an extension of standard BKT in the latent state space, not a deep learning model. It trades the richer representation capacity of DKT / DKVMN for interpretability, evidence-routing enforceability, and tractable per-tier parameter estimation.

### 5.2 Empirical-Bayes-Inspired Shrinkage Threshold Calibration

**What is new:** Existing BKT-based ITS systems treat mastery thresholds as fixed design parameters set by the system designer. This system introduces a population-level recalibration mechanism: a shrinkage estimator that blends a literature-backed prior with an empirically derived GMM estimate, weighted by measured cluster quality.

After every `N_step = 50` new observations per (concept, tier), a two-component Gaussian Mixture Model is fitted to the buffer of BKT posteriors. The mastery threshold is updated as a noise-weighted blend of the literature prior and the 10th percentile of the mastered cluster:

```
θ_new  =  (1 − w) · θ_prior  +  w · (μ_mastered − 1.282 · σ_mastered)
w      =  1 − 1 / (1 + Fisher_ratio)
```

This gives the system **self-awareness about its own calibration quality**: when the two clusters in the posterior distribution are well-separated (Fisher ratio high), the empirical estimate dominates; when they overlap (noisy data), the system conservatively falls back toward the literature prior.

**What this provides:** The mastery threshold is no longer a static design choice — it is a population-level estimate that evolves as evidence accumulates. The literature-backed prior ensures the system works from the first interaction; the GMM mechanism provides a data-informed correction as more students interact with each concept. The Gaussian mixture is treated as a pragmatic heuristic for bounded data rather than a formally derived Bayesian estimator: a reviewer may reasonably question Gaussianity, bimodality, or identifiability assumptions. The Fisher-ratio blending weight provides a principled fallback: when those assumptions are poorly met (low cluster separation), the calibrator conservatively down-weights the GMM estimate and defers to the literature prior.

### 5.3 UCB1 Pedagogical Style Selection with Live Student Feedback

While bandit algorithms have been applied to content sequencing in ITS (Clement et al., 2015 [14]), applying UCB1 to **teaching style** (how content is delivered) rather than **content selection** (what is delivered) is less explored. The feedback signal here is explicit (thumbs up/down) rather than inferred from response time or quiz performance, making the update semantically clean. The sublinear regret guarantee of UCB1 applies directly since `r_t ∈ {0, 1}` is Bernoulli bounded in [0, 1].

### 5.4 IRL/SRL Dual-Track Architecture

Most ITS architectures treat the instructor as a one-time configuration step. This architecture maintains IRL and SRL as parallel, continuously active tracks that converge at the Security Model. Instructor topic locks propagate in real time — if a teacher disables "Pointers" mid-semester, the gate fires immediately for all students without requiring a system restart or per-student reconfiguration. The prerequisite gate propagates the strictness of the factored mastery criterion: a concept can only enter `K_t` after satisfying `m_c = 1`, and subsequent concepts that require it are blocked until then.

### 5.5 Security as a Two-Stage Interpretable Gate

Safety filtering in LLM-based systems is typically implemented as a single opaque classifier or a fixed blocklist. This system decomposes the gate into `σ = φ · α` — a structural feasibility gate (hard binary conjunctions of policy predicates) and a pedagogical alignment check (thresholded continuous similarity score). Each signal has an explicit semantic meaning, the reason for any block is attributable to a specific named signal, and the two stages have distinct recovery implications. The asynchronous semantic monitor `f_sem` runs out-of-band without blocking the student-facing response path.

### 5.6 Bi-Directional Video Transcript Timestamp Hints

Video-integrated chat tutoring typically answers based on watched content or defers to future content. This system introduces **bi-directional hints** — suggesting both future segments ("keep watching, this is covered at 3:00") and past segments ("you may want to revisit 0:56") based on keyword matching between the student's question and the full transcript. This transforms the video player from a passive viewing tool into an active navigation aid.

### 5.7 Cross-Modal Classroom-to-Chat Bridge

When a student receives a concept explanation in the chat interface and there is a relevant lecture video in the classroom, the system appends a contextual video suggestion. This bridges two interaction modes that are typically siloed, allowing the system to reinforce text-based explanations with video-based demonstrations — directly supporting Dual Coding Theory [26].

---

## 6. Summary Table

| Component | Technique | Primary purpose | Key literature |
|---|---|---|---|
| [A] Instructor Model | IRL constraints, assignment lifecycle | Instructor authority | Anderson et al. (1995); Pintrich (2000) |
| [B] Perception Layer | Intent classification, NER, affective profiling | Evidence type identification + frustration detection | Zaratiana et al. (2023); D'Mello & Graesser (2012) |
| [C] Domain Model | Knowledge graph, RAG, prerequisite chains | Prerequisite enforcement; course-grounded content | Liang et al. (2018); Lewis et al. (2020) |
| **[D] K_t — Factored BKT** | **Tiered BKT, evidence routing, conjunctive mastery** | **PRIMARY: resolve Evidence Diversity Problem** | **Corbett & Anderson (1994); Bloom (1956); Baker et al. (2008)** |
| [D] SM-2 | Spaced repetition | Retention scheduling | Ebbinghaus (1885); Wozniak (1990); Cepeda et al. (2006) |
| [D] C_s — UCB1 | Multi-armed bandit style selection | Personalized delivery | Auer et al. (2002); Clement et al. (2015) |
| [D] M_t — EMA Misconceptions | EMA state variable | Persistent error detection | Confrey (1990); Ohlsson (1994) |
| [D] F_t — Affective | Categorical + continuous frustration | Frustration suppression | Picard (1997); D'Mello & Graesser (2012) |
| [E] Security Model | σ = φ · α gate | Structural safety + goal alignment | Sweller (1988); Pintrich (2000) |
| [F] Scaffolding | ZPD, SRL FSM | Problem-solving support | Vygotsky (1978); Wood et al. (1976) |
| [F] Examiner | Testing effect, JoL, BKT update | Declarative evidence collection | Roediger & Karpicke (2006) |
| [F] Code Reviewer | Sandwich feedback, BKT update | Applied evidence collection | Hattie & Timperley (2007) |
| [F] Micro-Challenge | Code attempt, BKT update | Procedural evidence collection | — |
| [F] Socratic Agent | Inquiry teaching, CoT | Scaffolded understanding | Collins & Stevens (1982) |
| [G] Mermaid / Visual | Dual coding | Visual encoding | Paivio (1991) |
| [G] Timestamp hints | Elaborative interrogation, retrieval practice | Video navigation | Pressley et al. (1992) |
| [H] Persistence | Mastery learning, longitudinal tracking | Cross-session learner state | Bloom (1968) |

---

## 7. References

[1] Anderson, J. R., Corbett, A. T., Koedinger, K. R., & Pelletier, R. (1995). Cognitive tutors: Lessons learned. *Journal of the Learning Sciences, 4*(2), 167–207.

[2] Pintrich, P. R. (2000). The role of goal orientation in self-regulated learning. In *Handbook of Self-Regulation* (pp. 451–502). Academic Press.

[3] Zaratiana, U., Tomeh, N., & Shi, C. (2023). GLiNER: Generalist model for named entity recognition using bidirectional transformer. *arXiv:2311.08526*.

[4] D'Mello, S., & Graesser, A. (2012). Dynamics of affective states during complex learning. *Learning and Instruction, 22*(2), 145–157.

[5] Liang, C., Wu, Z., Huang, W., & Giles, C. L. (2018). Recovering concept prerequisite relations from university course dependencies. *Proceedings of AAAI-18*.

[6] Lewis, P., Perez, E., Piktus, A., et al. (2020). Retrieval-augmented generation for knowledge-intensive NLP tasks. *NeurIPS 2020*.

[7] Corbett, A. T., & Anderson, J. R. (1994). Knowledge tracing: Modeling the acquisition of procedural knowledge. *User Modeling and User-Adapted Interaction, 4*(4), 253–278.

[8] Bloom, B. S. (1956). *Taxonomy of educational objectives: The classification of educational goals. Handbook I: Cognitive domain.* Longmans, Green.

[9] Ebbinghaus, H. (1885). *Über das Gedächtnis.* Duncker & Humblot.

[10] Wozniak, P. A. (1990). *Optimization of learning.* SuperMemo World.

[11] Cepeda, N. J., Pashler, H., Vul, E., Wixted, J. T., & Rohrer, D. (2006). Distributed practice in verbal recall tasks: A review and quantitative synthesis. *Psychological Bulletin, 132*(3), 354–380.

[12] Nelson, T. O., & Narens, L. (1990). Metamemory: A theoretical framework and new findings. *Psychology of Learning and Motivation, 26*, 125–173.

[13] Auer, P., Cesa-Bianchi, N., & Fischer, P. (2002). Finite-time analysis of the multiarmed bandit problem. *Machine Learning, 47*(2-3), 235–256.

[14] Clement, B., Roy, D., Oudeyer, P. Y., & Lopes, M. (2015). Multi-armed bandits for intelligent tutoring systems. *Journal of Educational Data Mining, 7*(2), 20–48.

[15] Confrey, J. (1990). A review of the research on student conceptions in mathematics, science, and programming. *Review of Research in Education, 16*, 3–56.

[16] Ohlsson, S. (1994). Constraint-based student modeling. In *Student Modeling: The Key to Individualized Knowledge-Based Instruction* (pp. 167–189). Springer.

[17] Picard, R. W. (1997). *Affective computing.* MIT Press.

[18] Zimmermann, B. J. (2000). Attaining self-regulation: A social cognitive perspective. In *Handbook of Self-Regulation* (pp. 13–39). Academic Press.

[19] Sweller, J. (1988). Cognitive load during problem solving: Effects on learning. *Cognitive Science, 12*(2), 257–285.

[20] Vygotsky, L. S. (1978). *Mind in society: The development of higher psychological processes.* Harvard University Press.

[21] Wood, D., Bruner, J. S., & Ross, G. (1976). The role of tutoring in problem solving. *Journal of Child Psychology and Psychiatry, 17*(2), 89–100.

[22] Roediger, H. L., & Karpicke, J. D. (2006). Test-enhanced learning: Taking memory tests improves long-term retention. *Psychological Science, 17*(3), 249–255.

[23] Hattie, J., & Timperley, H. (2007). The power of feedback. *Review of Educational Research, 77*(1), 81–112.

[24] Collins, A., & Stevens, A. L. (1982). Goals and strategies of inquiry teachers. In *Advances in Instructional Psychology* (Vol. 2, pp. 65–119). Erlbaum.

[25] Pressley, M., Wood, E., Woloshyn, V. E., Martin, V., King, A., & Menke, D. (1992). Encouraging mindful use of prior knowledge. *Educational Psychologist, 27*(1), 91–109.

[26] Paivio, A. (1991). Dual coding theory: Retrospect and current status. *Canadian Journal of Psychology, 45*(3), 255–287.

[27] Bloom, B. S. (1968). Learning for mastery. *Evaluation Comment, 1*(2), 1–12.

[28] Mislevy, R. J., Steinberg, L. S., & Almond, R. G. (2003). On the structure of educational assessments. *Measurement: Interdisciplinary Research and Perspectives, 1*(1), 3–62.

[29] Baker, R. S., Corbett, A. T., & Aleven, V. (2008). More accurate student modeling through contextual estimation of slip and guess probabilities in Bayesian knowledge tracing. In *Proceedings of the 9th International Conference on Intelligent Tutoring Systems (ITS 2008)*, pp. 406–415.

[30] Chi, M. T. H., de Leeuw, N., Chiu, M. H., & LaVancher, C. (1994). Eliciting self-explanations improves understanding. *Cognitive Science, 18*(3), 439–477.

[31] Piech, C., Bassen, J., Huang, J., Ganguli, S., Sahami, M., Guibas, L., & Paepcke, A. (2015). Deep knowledge tracing. *Advances in Neural Information Processing Systems (NeurIPS) 28*.

[32] Zhang, J., Shi, X., King, I., & Yeung, D. Y. (2017). Dynamic key-value memory networks for knowledge tracing. In *Proceedings of the 26th International Conference on World Wide Web (WWW 2017)*, pp. 765–774.

[33] Tatsuoka, K. K. (1983). Rule space: An approach for dealing with misconceptions based on item response theory. *Journal of Educational Measurement, 20*(4), 345–354.

[34] Reckase, M. D. (2009). *Multidimensional item response theory.* Springer.
