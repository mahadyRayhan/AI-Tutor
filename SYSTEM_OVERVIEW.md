# SAGE — System Overview

**S**elf-regulated **A**daptive **G**uidance **E**ngine — an adaptive intelligent tutoring
system for introductory C programming, deployed in CMP_SC 1050 (University of Missouri,
Fall 2026) as a flipped-classroom companion.

> This is the engineering + research reference for the whole system. It is the document
> code comments point at (`# See SYSTEM_OVERVIEW.md §3.10`). Where a number appears here it
> was read out of the deployed source, and the source file is named so it can be re-checked.
>
> **Companion documents:** [`README.md`](README.md) (setup + contribution summary),
> [`docs/MCN_EXPLAINER.md`](docs/MCN_EXPLAINER.md), [`docs/TEACHER_DASHBOARD.md`](docs/TEACHER_DASHBOARD.md),
> [`docs/IRL_EXTENSION_ROADMAP.md`](docs/IRL_EXTENSION_ROADMAP.md),
> [`Additional_files/DATA_COLLECTION_INVENTORY.md`](Additional_files/DATA_COLLECTION_INVENTORY.md).
>
> Last verified against the code: **2026-09-01**, branch `rayhan-tutor`.

---

## Table of Contents

1. [What SAGE is](#1-what-sage-is)
2. [Architecture — the IRL / SRL dual track](#2-architecture--the-irl--srl-dual-track)
3. [The learner model](#3-the-learner-model)
4. [The SRL layer](#4-the-srl-layer)
5. [The MCN — Metacognitive Calibration Network](#5-the-mcn--metacognitive-calibration-network)
6. [The IRL layer — instructor-regulated learning](#6-the-irl-layer--instructor-regulated-learning)
7. [Multi-agent pipeline and multi-step help](#7-multi-agent-pipeline-and-multi-step-help)
8. [The classroom — video-based flipped learning](#8-the-classroom--video-based-flipped-learning)
9. [Security](#9-security)
10. [Data model and telemetry](#10-data-model-and-telemetry)
11. [Evaluation suite](#11-evaluation-suite)
12. [Deployment and operations](#12-deployment-and-operations)
13. [Known gaps and open issues](#13-known-gaps-and-open-issues)
14. [File map](#14-file-map)
15. [References](#15-references)

---

## 1. What SAGE is

### 1.1 The problem it exists to solve

**The Evidence Diversity Problem.** A student can accumulate a large amount of evidence of
one *kind* — answering multiple-choice questions about pointers — and a conventional
knowledge-tracing model will certify mastery, because BKT treats all observations as
exchangeable draws from a single latent skill. That student cannot write a working pointer
program. The model has confidently certified an **illusion of competence**, and every
downstream decision (what to teach next, what to unlock, what to tell the instructor) is
now built on it.

SAGE's answer is to refuse to pool the evidence. Knowledge of a concept is tracked as three
independent posteriors mapped to Bloom levels, and certification is **conjunctive** — all
three must clear the bar independently, each with its own minimum evidence count. Lopsided
learners cannot certify. This is the system's load-bearing research claim.

### 1.2 What it is, concretely

- A FastAPI web application serving a streaming chat tutor, a student dashboard, a teacher
  dashboard, and a video classroom.
- A multi-agent pedagogical pipeline over an LLM (Gemini), grounded by RAG over course
  materials (ChromaDB) and a prerequisite knowledge graph (Neo4j).
- A learner model (multi-tier BKT + SM-2 + misconception EMA + a metacognitive Bayesian
  network) persisted in SQLite alongside 35 tables of research telemetry.
- Deployed at **https://mizzousage.net/** (AWS, Docker Compose, nginx + Let's Encrypt).

### 1.3 The two research tracks

| Track | Full name | Who regulates | Where it shows up |
|---|---|---|---|
| **IRL** | **Instructor-Regulated Learning** | The instructor sets boundary conditions | Topic locks, knowledge graph, video catalog, prelabs, assignments, teacher dashboard, safety gatekeeper |
| **SRL** | **Self-Regulated Learning** | The student regulates their own learning | Goal setting, JOL confidence, the downward mastery slider, MCN calibration, video intention/reflection, elective prelab practice |

The framing is that instruction should *cultivate* IRL first — instructors plan, monitor and
assess — and ultimately *serve* SRL, where learners take over those functions. The two
tracks are deliberately kept separate in the code: instructor constraints are stable and
change slowly; learner state changes every turn. Mixing them creates design conflicts and
makes neither independently testable.

The boundary is enforced per dashboard panel: triage / heatmap / gate / decertification /
model-health / security are IRL; metacognitive disagreement and the slider are SRL
([`docs/DASHBOARD_ROADMAP.md`](docs/DASHBOARD_ROADMAP.md)).

### 1.4 System utility function

Each component is a *local* optimiser of one term in a shared objective. Writing it down
states the target explicitly; it is **not** jointly optimised in the deployment (no
gradients are taken over it). It functions as the evaluation criterion for offline policy
assessment.

```
J  =  Σ_t  [ w₁·ΔK_t  +  w₂·R_t  −  w₃·F_t  −  w₄·C_t ]
```

| Symbol | Meaning | Range | Optimised by |
|---|---|---|---|
| `ΔK_t` | composite BKT mastery gain at step t (only the tier matching the evidence type moves) | ℝ | BKT mastery gate |
| `R_t` | SM-2 retention signal, 1 iff quality `q ≥ 3` | {0,1} | SM-2 scheduler |
| `F_t` | frustration cost, from `{none,low,medium,high,rage}` → `{0,.25,.50,.75,1.0}` | [0,1] | Affective profiler |
| `C_t` | instructional cost: direct answer 1, scaffolded hint 1.5, partial code 2 | {1,1.5,2} | Prerequisite gate, withholding |
| `w₁..w₄` | reference weights `(1.0, 0.5, 0.5, 0.2)` | ≥0 | — |

`C_t` scales with the transfer of cognitive labour from student to system: higher cost means
less productive struggle and lower expected learning yield per interaction.

---

## 2. Architecture — the IRL / SRL dual track

```
┌───────────────────────────────────────────┐   ┌────────────────────────────────────────────┐
│  IRL  (Instructor-Regulated)              │   │  SRL  (Self-Regulated Learning)            │
│                                           │   │                                            │
│  [A] INSTRUCTOR MODEL                     │   │  [B] PERCEPTION LAYER                      │
│      topic locks, catalog, assignments    │   │      contextualise → intent → NER → affect │
│  [C] DOMAIN MODEL                         │   │  [D] LEARNER MODEL                         │
│      Neo4j prereq graph + ChromaDB RAG    │   │      BKT ×3 · SM-2 · misconceptions · MCN  │
└──────────────────────┬────────────────────┘   └───────────────────────┬────────────────────┘
                       └──────────────┬──────────────────────────────────┘
                                      ▼
                        [E] SECURITY MODEL  (Sentinel, L0–L7)
                        σ = φ(S_cog · S_acad · S_spam) · α(G_t)
                                      ▼
                        [F] PEDAGOGICAL MODEL  (agent routing)
                                      ▼
                        [G] INTERFACE (SSE stream, dashboards, classroom)
                                      ▼
                        [H] PERSISTENCE (SQLite state + telemetry)
```

### 2.1 The request pipeline

Every chat turn runs through `ChainOfThoughtRAGAgent.run_stream()`
([`backend/app/agents/cot_rag_agent.py`](backend/app/agents/cot_rag_agent.py)):

| # | Stage | What happens | Code |
|---|---|---|---|
| 1 | **Session bootstrap** | identity from the signed cookie; session state loaded from SQLite | `auth.get_current_user` |
| 2 | **Contextualise** | LLM rewrites the query against history (coreference), then validates extracted entities against the Neo4j graph | `_contextualize_query` |
| 3 | **Intent classify** | rule cascade → MiniLM embedding fallback; `INTENT_CLASSIFIER_MODE=fast` by default | `fast_classifier.py` |
| 4 | **Sensory math** | `s_goal` (goal alignment), `c_code` (AST-ish complexity), `delta_f` (frustration delta), `m_state`, `n_strike` | `_calculate_goal_alignment`, `_calculate_code_complexity`, `_determine_m_state` |
| 5 | **Sentinel** | the L0–L7 gate stack (§9.2). Any block short-circuits the turn | `sentinel.py` |
| 6 | **Mastery classify** | novice / developing / proficient / reviewing, from *effective* mastery, conjunctively | `_classify_mastery_level` |
| 7 | **Prerequisite gate** | Neo4j edge check against the certified set `K_t` | `_check_gatekeeping` |
| 8 | **Route** | to Scaffolding / Examiner / Socratic / Reviewer | `run_stream` |
| 9 | **Generate** | mastery-adapted prompt + RAG context, streamed over SSE | `socratic.py` |
| 10 | **Update + log** | BKT update, SM-2, misconceptions, and ~10 telemetry writers | `bkt_model.py`, `telemetry.py` |

### 2.2 Storage layers

| Store | Path | Holds |
|---|---|---|
| **SQLite** | `backend/database/ai_tutor.db` | users, sessions, messages, BKT state, all 35 telemetry tables |
| **Neo4j** | bolt://localhost:7687 | concept nodes, `REQUIRES_UNDERSTANDING_OF` prerequisite edges, `INCLUDES` hierarchy, Q&A pairs |
| **ChromaDB** | `backend/database/chroma_db/` | vector store over course materials (RAG) |
| **Filesystem** | `backend/database/video/` | lecture `.mp4` files, Whisper transcript caches, `prelab.json` |

### 2.3 Models in use

| Role | Model | Set in |
|---|---|---|
| Generation (fast path) | `gemini-3.1-flash-lite` | `config.DEFAULT_GOOGLE_MODEL_ID` |
| Reasoning / CoT | `gemini-flash-latest` | `config.DEFAULT_REASONING_MODEL_ID` |
| Embeddings | `gemini-embedding-001` | `config.DEFAULT_GOOGLE_EMBEDDING_MODEL` |
| Local intent embeddings | `sentence-transformers/all-MiniLM-L6-v2` | `models/` cache |
| Transcription | OpenAI Whisper `base` (local, CPU) | `video_service._MODEL_SIZE` |
| TTS | `gemini-2.5-flash-preview-tts`, voice `Kore` | `config.DEFAULT_GOOGLE_TTS_MODEL` |
| Offline judging (eval only) | `gpt-4o` | `IRL_extension_script/_judge_client.py` |

---

## 3. The learner model

Six state variables per `(student, concept)`, assembled into a single profile by
[`learner_model.py`](backend/app/core/learner_model.py) along the Azevedo taxonomy —
cognitive, metacognitive, affective, motivational.

### 3.1 Multi-tier BKT — the core

[`backend/app/core/bkt_model.py`](backend/app/core/bkt_model.py)

Each `(student, concept)` carries **three independent posteriors** `P̃^(k) ∈ [0,1]`, one per
evidence tier, mapped to Bloom levels:

| Tier | Bloom level | Evidence source | `P_G` | `P_S` | `P_L0` | `P_T` | Ceiling `θ_max` | Column |
|---|---|---|---|---|---|---|---|---|
| **quiz** | declarative (remember/understand) | Examiner quizzes, video checkpoint MCQs | 0.20 | 0.10 | 0.30 | 0.09 | 0.60 | `p_mastery_quiz` |
| **micro** | procedural (apply) | micro-challenges | 0.10 | 0.15 | 0.05 | 0.09 | 0.25 | `p_mastery_micro` |
| **code** | applied (analyse/create) | code review submissions | 0.05 | 0.20 | 0.01 | 0.09 | 0.10 | `p_mastery_code` |

`P_G` = guess rate, `P_S` = slip rate, `P_L0` = prior (nonzero per Cromwell's rule),
`P_T` = learning rate. The displayed composite is `P^(k) = θ_max^(k) · P̃^(k)`, summing to a
0.95 ceiling.

**Why the parameters differ by tier.** Guessing a multiple-choice quiz item is easy (0.20);
guessing your way to working C code is nearly impossible (0.05). Conversely, slipping on
code you actually understand is common (0.20) — a typo, a missing semicolon — while slipping
on a recall question is rare (0.10). The priors follow: a student who has never touched the
concept is far more likely to know *about* it (0.30) than to be able to *write* it (0.01).

#### The update step

```python
# correct
p_obs = p_l·(1−P_S) + (1−p_l)·P_G
p_post = p_l·(1−P_S) / p_obs
return p_post + (1 − p_post)·P_T        # learning credit

# incorrect
p_obs = p_l·P_S + (1−p_l)·(1−P_G)
return p_l·P_S / p_obs                  # penalty only, NO learning credit
```

**Fix #1 — `P_T` applies to correct responses only.** In textbook BKT the learning
transition is applied unconditionally, which at low priors lets `P_T` dominate the
slip/guess penalty: a student answering wrong could see their posterior *rise*. Here a wrong
answer is pure penalty.

#### Minimum-evidence guarantee

Three consecutive correct answers from the cold prior:

| Tier | Trajectory | False-certification bound (3 lucky guesses) |
|---|---|---|
| quiz | 0.30 → 0.689 → 0.917 → 0.982 | `0.20³ = 0.80 %` |
| micro | 0.05 → 0.371 → 0.848 → 0.981 | `0.10³ = 0.10 %` |
| code | 0.01 → 0.217 → 0.833 → 0.989 | `0.05³ = 0.013 %` |

#### Conjunctive certification

```
Certify:    ∀k:  P̃^(k) ≥ THETA_CERTIFY (0.95)  ∧  n^(k)_evidence ≥ N_MIN (3)
Decertify:  ∃k:  P̃^(k) < THETA_DECERTIFY (0.75)          ← hysteresis band
```

Non-compensatory: strength on quiz cannot buy a pass on code. `n_evidence` is incremented
**only on correct answers**, so certification requires three genuine correct responses per
tier — nine in total, across three different evidence modalities.

`ever_certified` is set on first certification and **never reset**. The prerequisite gate
reads `ever_certified`, so forgetting decay can never re-lock a prerequisite the student
already earned (Fix #5).

`reconcile_certifications()` is a decertify-only sweep that applies the hysteresis rule
eagerly across all certified rows; the teacher Triage endpoint runs it before reading the
flag, so the dashboard never shows a stale certification.

#### Forgetting decay

```
P̃(t) = P̃(t₀)·e^(−λΔt) + P̃₀·(1 − e^(−λΔt))
```

Default half-lives: **quiz 14 days, micro 7 days, code 5 days** (`λ = ln2 / halflife`).
Applied material decays fastest — this matches the retention literature and is the
conservative direction for a mastery claim.

**SM-2 ↔ BKT coupling (Fix #6):** after each correct review `λ_new = λ_old / EF`, where `EF`
is the SM-2 ease factor (≥ 1.3). Every successful review extends the memory half-life, so
the decay rate is itself learned per student per concept (`decay_quiz_lam`, etc.).

> **Fixed 2026-08:** `_apply_decay` previously ended with `max(decayed, p0)`. That clamp
> never bound the intended case (a high posterior decaying down — the exponential already
> asymptotes to `p0` from above). It only ever fired when a posterior sat *below* the prior,
> i.e. immediately after a wrong answer, snapping it back up to `p0` regardless of elapsed
> time. Wrong answers were effectively erased on the student's very next interaction:
> `0.300 → 0.051` (wrong) then seconds later `0.051 → 0.300` (decay). Now clamped to the
> unit interval only. Regression tests: `tests/test_code_evidence_grading.py::TestDecayDoesNotEraseWrongAnswers`.

#### Effective vs. objective mastery

- `get_mastery()` — the **objective** BKT composite, decay-adjusted. Drives certification,
  the prerequisite gate, and the MCN's `K` prior.
- `get_effective_mastery()` — blends in the student's self-assessment (§4.2). Drives
  **display and response adaptation only**, never certification.

The separation is a hard invariant: a student can influence the *model* but never the *gate*.

### 3.2 Prerequisite-coupled priors ("head start")

[`backend/app/core/prereq_headstart.py`](backend/app/core/prereq_headstart.py)

A student who has *earned* mastery of Variables is not a total beginner at Loops. This
module gives a new topic a small, capped boost on its **starting prior** — never on the
finish line.

| Tier | Transfer coefficient `κ` | Default prior | Cap on seeded prior | Max head start |
|---|---|---|---|---|
| quiz | 0.15 | 0.30 | 0.50 | +0.20 |
| micro | 0.06 | 0.05 | 0.15 | +0.10 |
| code | 0.01 | 0.01 | 0.02 | +0.01 |

Near-transfer of declarative knowledge is real; writing working code does not transfer from
adjacent recall, so the code tier is left essentially at its cold-start prior.

**Seven invariants** (each is a test):

1. Only **certified** prerequisites propagate (`ever_certified = 1`). A head start
   contributes zero evidence, so it can never itself become a certified source — the boost
   cannot snowball down a prerequisite chain.
2. Boost is proportional to the prerequisite's effective mastery, capped per tier, far below
   `θ_cert = 0.95`.
3. Applied **once**, at the first genuine BKT touch of the new topic.
4. Real performance takes over — `_apply_decay` anchors to `P_L0`, not the seed, so a head
   start cannot hold a score up against real failure.
5. A head start can **never certify**: certification needs `n_evidence ≥ 3` real answers on
   the topic itself. The seed moves the starting line, not the finish.
6. Only doubt flows downhill: a downward self-assessment on a prerequisite lowers the head
   start it passes on, but only while the dependent has zero evidence of its own. Clawback
   is monotone — it can only reduce a neighbour's prior, never raise it.
7. The prerequisite gate reads certified mastery, never the head-started posterior, so a
   head start can never unlock content.

Every seed and clawback is written to `headstart_log`, so analysis can *prove* no head start
ever certified a topic.

### 3.3 Adaptive threshold calibration (GMM)

[`backend/app/core/threshold_calibrator.py`](backend/app/core/threshold_calibrator.py)

Literature defaults (`θ = 0.95`, Corbett & Anderson 1995) encode a principled starting point
but cannot know concept-specific difficulty or cohort characteristics. After enough
observations accumulate for a `(concept, tier)` pair, a two-component GMM is fitted to the
population of observed posteriors and blended with the prior:

```
θ_new       = (1 − w)·θ_prior + w·θ_GMM
w           = 1 − noise_level
noise_level = 1 / (1 + Fisher_ratio)
Fisher_ratio = (μ_mastered − μ_other)² / (σ²_mastered + σ²_other)
```

Well-separated clusters → `w → 1`, the data dominates. Overlapping clusters → `w → 0`, the
prior holds. **The prior is never discarded, only down-weighted as evidence accumulates.**

Schedule: first attempt at 50 observations, recalibrate every 50 more, sliding window of the
last 500 per `(tier, concept)`.

### 3.4 Misconception EMA

[`backend/app/core/user_knowledge_manager.py`](backend/app/core/user_knowledge_manager.py)

```
M_c(t+1) = λ_M · M_c(t) + (1 − λ_M) · 𝟙[confidence ≥ θ_conf ∧ incorrect]
λ_M = 0.7      θ_M = 0.25 → flagged as an active misconception
```

Tracked **separately from the posterior**. The signal is specifically the
*high-confidence wrong answer* — the classic misconception signature, distinct from a
knowledge gap (which is a low-confidence wrong answer). `misconception_log` records both
`store` and `resolve` actions, so learning-from-error is measurable as a resolution rate.

### 3.5 SM-2 spaced repetition

Standard SM-2 (`interval_days`, `ease_factor` default 2.5, `due_date`, `review_count`). The
quality score `q ∈ [0,5]` is derived from **both correctness and JOL confidence**:

| Outcome | Confidence | `q` |
|---|---|---|
| correct | ≥ 4 | 5 |
| correct | < 4 | 4 |
| wrong | ≥ 4 | 0 (high-confidence error — hardest reset) |
| wrong | ≤ 2 | 1 |
| wrong | 3 | 2 |

`q < 3` resets the interval to day 1. A confident wrong answer is punished harder than an
unconfident one — the calibration signal is baked into the schedule.

### 3.6 Concept canonicalisation

[`backend/app/core/concept_canon.py`](backend/app/core/concept_canon.py)

Nine coarse dashboard topics: **Variables, Control Flow, Functions, Arrays, Strings,
Pointers, Structures, Memory Allocation, File I/O**.

Different code paths historically wrote `user_knowledge` rows under inconsistent spellings
("variables" / "variable" / "Variables and Types"), scattering evidence across rows the
dashboard never read — the "quiz certified in chat but the dashboard says 0" bug.
`canonical_concept()` folds known case/plural/whitespace variants and finer sub-topics onto
the nine canonical names.

It is deliberately **conservative**: a concept whose normalised token set is not a known
variant is returned unchanged, never title-cased or guessed, so unrelated concepts and graph
node names are never merged by accident.

### 3.7 The unified multidimensional profile

[`learner_model.py`](backend/app/core/learner_model.py) is a **pure read model** — it never
writes learner state. It derives, over a 200-row recency window:

| Dimension | Derived variables |
|---|---|
| **Cognitive** | concept mastery, cognitive load, slip-vs-gap, automatisation, comprehension tracing, debugging skill |
| **Metacognitive** | self-monitoring, help-seeking, path adherence |
| **Affective** | frustration/affect state, flow |
| **Motivational** | self-efficacy, interest, goal orientation (Tier-2 self-report) |
| **Behavioural** | persistence, pacing, engagement |

Exposed at `GET /api/v1/learner-model/{username}`.

---

## 4. The SRL layer

### 4.1 Zimmerman's cycle, mapped to mechanisms and data

| SRL phase | Mechanism in SAGE | Where | Telemetry |
|---|---|---|---|
| **Forethought** | goal setting; skill-tree path; **pre-video intention prompt**; scaffolding plan step 0 | student dashboard, `chat.js:2131` | `user_goals`, `event_log(goal_set)`, `path_event`, `video_reflection(phase='intention')` |
| **Performance** | quizzes, micro-challenges, productive struggle, skips, help-seeking, checkpoint MCQs | chat, classroom | `evidence_log`, `quiz_log`, `behavior_log`, `video_mcq_response`, `event_log(quiz_skip)` |
| **Self-reflection** | JOL confidence ratings; **post-video reflection prompt**; dashboard self-assessment slider | Examiner, `chat.js:2156`, student dashboard | `jol_log`, `video_reflection(phase='reflection')`, `calibration_log` |
| **Elective transfer** | end-of-video prelab practice, offered but ungraded | `chat.js:2174` | `event_log(prelab_started)` |
| **Emotion regulation** | frustration tracking + reframe intervention | Profiler → Sentinel L1 | `affect_log` |
| **Learning from error** | misconception store → resolve | `user_knowledge_manager` | `misconception_log` |

### 4.2 The SRL-BKT calibration loop

[`backend/app/core/srl_calibration.py`](backend/app/core/srl_calibration.py)

The novel mechanism: **student metacognitive feedback tunes the model itself**, not just the
display.

**Layer 1 — score blending (immediate).**

```
P_eff = α·P_BKT + (1 − α)·P_self          α = 0.6
```

Drives display and response adaptation only. Never certification.

**Layer 2 — parameter adaptation (sustained).**

A direction EMA tracks whether the student is *consistently* disagreeing downward:

```
direction_ema ← λ_cal · direction + (1 − λ_cal) · direction_ema     λ_cal = 0.4
```

After `N_TRIGGER = 3` adjustments with `|direction_ema| > 0.3`, the per-user **guess rate
`P_G`** for that tier increases by `β = 0.10`, bounded:

| Tier | Default `P_G` | Max adapted `P_G` |
|---|---|---|
| quiz | 0.20 | 0.40 |
| micro | 0.10 | 0.25 |
| code | 0.05 | 0.15 |

Future correct answers on that tier then contribute less evidence — the model has *learned
that this student guesses well on this kind of item*. This is a genuinely individualised
emission parameter, which is what distinguishes it from a display-only open learner model.

**Guardrails.**

- **Downward-only slider.** "I know less than you think" is evidence; "trust me, I know it"
  is not. `record_self_assessment` raises `ValueError` if `p_self ≥ p_bkt_current`. This
  preserves the certification theorem — there is no path to self-certification.
- Certification always reads `P_BKT` (computed *with* the adapted parameters). The student
  influences the model, not the gate.
- Raising mastery happens only through evidence — the student must ask to be quizzed.

**Novelty position.** Yudelson et al. personalise BKT from behaviour only; Bull & Kay's
negotiated open learner models are display-only. This loop is the narrow intersection:
metacognitive disagreement that actually reparameterises the emission model, with a
guardrail that makes it non-gameable.

API: `GET /api/v1/mastery/{user}/{concept}`, `POST /api/v1/mastery/self-assess`.
Every slider move and every `P_G` adaptation is written to `calibration_log`.

### 4.3 Prediction logging (the C3 measurement)

`prediction_log` records both `p_bkt_pred` and `p_eff_pred` **before** the outcome is known,
then the outcome. This makes the central question answerable directly: does `P_eff` beat
`P_BKT` at predicting the next response after calibration? Scored with Brier score /
log-loss, within-subject.

### 4.4 Judgment of Learning (JOL)

The Examiner asks for a **1–5 confidence rating before showing the question**
([`examiner.py:131`](backend/app/agents/examiner.py#L131)), not after. Asking after
contaminates the rating with the felt difficulty of the item. Video checkpoint MCQs carry
the same field (`video_mcq_response.confidence_1_5`).

This is the raw material for calibration: signed error `= confidence − correctness` per
item, per concept, per student, over time.

---

## 5. The MCN — Metacognitive Calibration Network

[`backend/app/core/mcn.py`](backend/app/core/mcn.py) ·
[`mcn_cpts.py`](backend/app/core/mcn_cpts.py) ·
[`mcn_evidence.py`](backend/app/core/mcn_evidence.py) ·
[`mcn_service.py`](backend/app/core/mcn_service.py) ·
full derivation in [`docs/MCN_EXPLAINER.md`](docs/MCN_EXPLAINER.md)

### 5.1 What it is and why it isn't just another heuristic

A small **discrete Bayesian network** that infers a student's latent metacognitive
calibration state per concept by fusing signals the system already collects.

The modelling move that makes it a Bayesian network rather than a weighted score: **two
hidden variables are separated, and the self-report is modelled as jointly caused by both.**

```
    K = true KNOWLEDGE      (low | med | high)    ← soft prior from BKT
    C = CALIBRATION         (over | cal | under)  ← the SRL construct we want

DAG:
        K ──► P     performance   (quiz/micro/code correctness)
        K ──► B     behaviour     (help-seeking / fluency)
    (K,C) ──► S     self-report   (JOL confidence / dashboard slider)   ← the crux
    (K,C) ──► A     affect        (frustration level)          [optional]
```

Calibration is encoded as the **bias of the self-report relative to true knowledge**:

- `C = cal` → `S` tracks `K`
- `C = over` → `S` skews high regardless of `K`
- `C = under` → `S` skews low regardless of `K`

**Why this matters — "explaining away".** A student reports low confidence. A linear blend
of confidence and performance reads that as *weak*, and prescribes more practice. The
network, seeing strong performance evidence on `P` and `B` flowing from the same latent `K`,
instead concludes `K = high, C = under` — the student **knows it and doesn't know they know
it**. The correct intervention is the opposite one: affirm demonstrated competence, stop
re-explaining basics. No linear combination of the same inputs can make that distinction,
because it requires reasoning about a *common cause*.

### 5.2 Inference

Exact, by enumeration over the 3 × 3 = 9 latent `(K, C)` assignments. No solver, no numpy,
no external dependency. `mcn.py` is **pure** — it reads no database and touches no app
state.

Returns `map_C`, `map_K`, a confidence, `n_signals`, and the full posterior
`(p_over, p_cal, p_under)`.

### 5.3 Evidence adapters

[`mcn_evidence.py`](backend/app/core/mcn_evidence.py) is the only bridge to live data,
strictly read-only, every DB access guarded.

| Node | Source | Scope |
|---|---|---|
| `K` prior | `bkt_model.get_mastery()` — the **objective** composite | per concept |
| `S` self-report | most recent `jol_log.confidence_1_5`, else `user_bkt_calibration.self_assessment` averaged across tiers | per concept |
| `P` performance | `evidence_log.is_correct` over the last 5 rows | per concept |
| `B` behaviour | `behavior_log` — `hint_request`/`skip_challenge` → struggling; `copy_code`/long dwell → fluent, last 8 rows | user-recent |
| `A` affect | most recent `affect_log.frustration_level` | user-recent |

**Deliberate choice:** the `K` prior uses `get_mastery()`, **not** `get_effective_mastery()`.
The latter already blends in the student's self-report — and self-report is a *separate
evidence node* (`S`). Blending it into `K` would double-count it and destroy the very
independence the network relies on.

The `K` prior is a smooth Gaussian kernel over prototypes `low=0.15, med=0.50, high=0.85`
with `σ = 0.22`, so a borderline student contributes appropriately hedged prior mass rather
than falling into a hard bin.

**Carried caveat:** `B` and `A` are session/user-level, not per-concept, so they are treated
as weak corroborating evidence. The concept-specific weight rests on `K`, `S`, and `P`. This
limitation is stated in the paper.

### 5.4 CPTs — tunable without redeploy

Hand-elicited defaults are baked into `mcn.py`, so the network works cold with zero training
data. `mcn_cpts.py` externalises them to `mcn_cpts.json`:

- **Hot reload** keyed by file mtime — editing the JSON is picked up on the next inference,
  no restart. This is the operational constraint of a classroom deployment that cannot be
  taken down mid-study.
- **Any** load or validation error falls back to the baked-in default, so a malformed edit
  can never take the live SRL layer down.
- The same serialisation supports an offline refit of the CPTs from `jol_log` after the
  study.

### 5.5 Safety envelope

[`mcn_service.py`](backend/app/core/mcn_service.py) is the one sanctioned entry point:

| Guard | Behaviour |
|---|---|
| **Flag-gated** | returns `None` unless `MCN_ENABLED=true`. **Default OFF** — ships dark |
| **Sufficiency** | returns `None` unless `n_signals ≥ 2` (`MIN_SIGNALS_FOR_ACTION`) |
| **Fail-safe** | any exception is swallowed → `None` → tutor falls back to existing behaviour |
| **Never certifies** | does not touch BKT posteriors, effective mastery, or the mastery gate |

Adaptation is delivered by **injecting a short directive into the tutoring prompt**
(`prompt_directive()` → `socratic.py`), never by mutating a mastery number — a deliberate
choice that keeps the certified-mastery quantity exactly as BKT computes it.

- `under` → "affirm their demonstrated competence, avoid re-explaining basics they know"
- `over` → "weave in ONE pointed check question on a commonly-missed aspect, stay encouraging"
- `cal` → empty string, no intervention

Every verdict is appended to `mcn_log`. Endpoint: `GET /api/v1/mcn/calibration/{username}`.

---

## 6. The IRL layer — instructor-regulated learning

### 6.1 Instructor control surfaces

| Surface | What the instructor sets | Effect |
|---|---|---|
| **Topic locks** | enable/disable any of the nine topics | Sentinel L6 hard-blocks the topic; no BKT evidence can be collected for it |
| **Knowledge graph** | prerequisite edges, topic attributes | Immediately changes gatekeeper decisions on the next query — no redeploy, no retraining |
| **Video catalog** | chapters, planned vs. recorded videos, slide/section ranges | Drives the classroom UI and the flipped-classroom plan |
| **Prelabs** | per-video practice questions, one per line | Offered at end of video (§8.5) |
| **Assignments** | pushed challenges → `PENDING / SUBMITTED / GRADED` | Student dashboard challenge list |
| **Materials** | uploaded resources | RAG ingest + student download |
| **Users** | create, block, change role | Access control |

The point of the IRL layer is that **instructor modifications propagate immediately to the
Safety Gatekeeper** — real-time policy enforcement without redeploying an LLM or retraining
a classifier. That is what makes it a human-in-the-loop control rather than a config file.

### 6.2 Teacher dashboard

[`backend/app/templates/teacher_dashboard.html`](backend/app/templates/teacher_dashboard.html) ·
[`docs/TEACHER_DASHBOARD.md`](docs/TEACHER_DASHBOARD.md)

Six tabs, each answering one question:

| Tab | Question | Panels |
|---|---|---|
| **Now** | Who needs me today? | Struggling Students · Triage ("who needs me") · Roster |
| **Class** | Where does everyone stand? | Learning Path Deviation · Where the class stands · tier heatmap |
| **Next** | What should I teach and prepare? | What to prepare next · Did assignments land? · Ready for what's coming · Who's about to fade (decertification forecast) |
| **Content** | Lecture videos | Lecture Engagement · Upload a recording · Unassigned recordings · Course chapters · prelab editor |
| **Setup** | Course and users | Pending reviews · User management · Resource manager · Topic visibility |
| **System** | Is the model trustworthy? | Model Health (Brier + reliability curve) · Security Ledger |

**Design constraints enforced in the panels** (from `docs/DASHBOARD_ROADMAP.md`):

- **No composite score without decomposition.** The student modal shows three per-tier bars,
  coloured by the *weakest* tier. It previously showed a compensatory composite reading
  "59% — green" for a student at quiz 91% / code 1%. That is exactly the illusion of
  competence the whole system exists to prevent, reproduced in the UI.
- **Every mastery view decays.** Triage once read raw `p_mastery_*` while every other view
  decay-adjusted, producing a visible 0.98-vs-0.912 disagreement between two panels showing
  "the same" number.
- **No per-learner affect as fact.** Frustration surfaces as a cohort chip ("N% of active
  learners reading elevated"), explicitly labelled an estimate — never as a per-student
  column.
- **No leaderboard framing.** Triage-by-need is legitimate; ranking learners against each
  other is not.

**Engagement and analytics endpoints:**

| Endpoint | Answers |
|---|---|
| `/analytics/teacher/triage` | who needs intervention now, with reason |
| `/analytics/teacher/risk_matrix` | risk × topic grid |
| `/analytics/teacher/decert_forecast` | who is about to lose certification to decay |
| `/analytics/teacher/model_health` | Brier score + reliability curve — is the model calibrated? |
| `/analytics/teacher/security_ledger` | gated turns, bucketed (§9.6) |
| `/analytics/teacher/readiness`, `/prep_list` | what to teach next |
| `/analytics/frustration` | frustration over time |
| `/analytics/path-deviation` | where students leave the intended path |
| `/video/analytics/{filename}` | per-lecture engagement, checkpoint pass rates, drop-off |

### 6.3 Student dashboard

[`student_dashboard.html`](backend/app/templates/student_dashboard.html) — the SRL-facing
mirror: goal display and editing, learning path tree, challenges, mastery donut and radar,
the skill network graph, the **calibration card** (the downward slider), and a generated
"Personal Tutor Assessment" (current focus / strengths / needs work / recommended reading).

---

## 7. Multi-agent pipeline and multi-step help

### 7.1 The agents

| Agent | File | Trigger | Method | BKT evidence |
|---|---|---|---|---|
| **Orchestrator** | `cot_rag_agent.py` | every turn | intent → sensory math → gate → route | — |
| 🛡️ **Sentinel** | `sentinel.py` | every turn, first | L0–L7 gate stack | — |
| 🧐 **Examiner** | `examiner.py` | QUIZ intent | JOL-first, 2-tier grading | `quiz` → `P̃^(1)` |
| 🎓 **Socratic** | `socratic.py` | fallback / CONCEPT | CoT + hybrid RAG, mastery-adapted | — |
| 📝 **Reviewer** | `reviewer.py` | REVIEW intent (code detected) | sandwich feedback | `code` → `P̃^(3)` |
| 🏗️ **Scaffolding** | `scaffolding.py` | PROBLEM intent + complexity | step-by-step plan FSM | `micro` → `P̃^(2)` |
| 📊 **Profiler** | `profiler.py` | every turn | sentiment/frustration | — |

**Routing invariant:** no agent generates multi-tier evidence in a single interaction. One
interaction, one tier. This is what makes the three posteriors genuinely independent rather
than three views of the same observation stream.

### 7.2 Multi-step help — the escalation ladder

Help in SAGE is deliberately **staged**, so the student pays cognitive labour before the
system does. Ordered from least to most help given:

**Step 0 — the prerequisite gate.** Before anything else, `_check_gatekeeping` walks the
Neo4j `REQUIRES_UNDERSTANDING_OF` edges. If a prerequisite is not in the certified set `K_t`,
the tutor redirects to the prerequisite instead of answering. Because `K_t` is populated only
by *conjunctive* certification, a student who "mastered" arrays on quizzes alone cannot
unlock pointers — the strictness of the mastery criterion propagates through the entire
curriculum. Visited prereqs are tracked to prevent redirect loops.

**Step 1 — Socratic withholding.** On a first request for a direct answer, the tutor
withholds and asks the student to try. Logged as `event_log(withholding_fired)`. A second
request sets `bypassed_withholding` in session state and the answer is given — the gate
costs one turn of effort, it does not trap the student.

**Step 2 — the scaffolded plan.** For a `PROBLEM` intent that is complex (>8 words) or
explicitly a "write a program" request, the Scaffolding agent:

1. Opens with a **forethought question** — the SRL move: the student states their approach
   before any code exists.
2. Generates a ~5-step plan **in a background task** while the student is typing that answer,
   so the plan costs no visible latency.
3. Walks the student through one step at a time, validating each response before advancing.
4. Awards mastery on completion of the whole plan.

Note `COMPLEX_PROBLEM` deliberately routes to Socratic for an *unguided* plan instead — the
strongest students are not walked through steps.

**Step 3 — the stuck ladder inside a step.** If the student is stuck on a step, the agent
first offers a rephrasing of the question the student could ask, then a **partial** C
snippet for that step only — never the whole solution.

**Step 4 — the 3-step mastery exam.** The one path to certification on demand
(`_process_mastery_exam`):

```
Step 1 of 3 · Quiz            → graded by embedding similarity  → quiz tier
Step 2 of 3 · Micro-Challenge → graded: valid C AND uses the concept → micro tier
Step 3 of 3 · Code            → graded: valid C AND uses the concept → code tier
```

Three modalities in sequence, with an escape hatch at any stage. This is the conjunctive
certification rule made into an interaction.

**Cost accounting.** Each rung maps to the `C_t` term in `J`: direct answer 1.0, scaffolded
hint 1.5, partial code handout 2.0.

### 7.3 Mastery-conditioned response adaptation

The orchestrator classifies each request into one of four levels using **effective** mastery,
conjunctively (`_classify_mastery_level`), then adapts three things:

| Level | Scaffolding | Format | Challenge |
|---|---|---|---|
| **Novice** | maximum; analogies; define every term | full 5 sections | guided micro-challenge |
| **Developing** | moderate; build on basics | full sections | combine 2 concepts |
| **Proficient** | light; nuances and edge cases | 3 sections | advanced / find-the-bug |
| **Reviewing** | concise refresher | 2 sections | quick retention check |

**Reviewing** (i.e. `ever_certified = 1`) **bypasses both the prerequisite gate and Socratic
withholding.** Telling someone who has already demonstrated mastery to "try it first" is the
expertise-reversal effect — scaffolding that helps a novice actively harms an expert.

This is standard ITS practice (VanLehn's inner/outer loop) and is *not* claimed as novel. It
exists to demonstrate that the mastery signal is actually used for something.

Every adaptation decision is logged to `response_log` (`mastery_level`, `format_sections`,
`weak_tier`), so §C2 of the evaluation can show the adaptation shifting as the BKT
classification evolves.

### 7.4 Teaching style selection (UCB1)

Style `s ∈ {analogy, example, visual, Socratic, direct}`, reward `r_t ∈ {0,1}` from
thumbs-up/down:

```
score_s(t) = +∞                             if n_s = 0
             w_s/n_s + √(2·ln N / n_s)      otherwise
style*(t)  = argmax_s score_s(t)
```

`+∞` initialisation forces exploration of every arm before exploitation. Regret is
`O(√(|S|·N·ln N))` **under stationary rewards** — a student's style preferences may drift as
they gain comfort, a mild stationarity violation under which the formal bound does not
transfer unchanged. Sliding-window or discounted UCB would restore it; noted as future work.
State lives in `learning_profile.win_rates`; the chosen style is recorded in
`messages.style_used`.

---

## 8. The classroom — video-based flipped learning

The course runs flipped: students watch lecture recordings before lab. The classroom module
instruments that watching.

### 8.1 Ingest → transcription → checkpoints

```
instructor uploads (chunked)  →  video_catalog slot assigned
                              →  transcription ENQUEUED
                              →  Whisper `base` transcribes (one at a time)
                              →  transcript cached to disk
                              →  LLM generates checkpoint MCQs, cached in video_checkpoint
```

**Upload** is chunked (`POST /api/v1/video/upload-chunk`) so large recordings survive a
flaky connection. Failures now report the HTTP status code — a bare "Upload failed on part
1 of 60" was untraceable.

**The transcription queue** ([`video_service.py`](backend/app/services/video_service.py))
exists because an instructor may attach five videos in a minute and Whisper must handle all
of them, one at a time:

- `_transcribe_lock` — a `threading.Lock` around `model.transcribe()`. The shared Whisper
  model instance installs `kv_cache` hooks and is **not thread-safe**; concurrent calls
  produced `cannot reshape tensor of 0 elements`.
- `_job_queue` + a **single worker thread** — jobs are serialised rather than dropped or run
  in parallel.
- `_pending` / `_running` back `GET /api/v1/video/transcript-status/{filename}` so the
  dashboard can show queue position.
- Cache re-check **inside** the lock, so two requests racing on the same file don't both
  transcribe it.

Whisper shells out to **`ffmpeg`**, which must be present in the image
(`backend/Dockerfile`; added in `371eef3` after `FileNotFoundError: 'ffmpeg'` in production).

**Checkpoint placement** (`_plan_checkpoint_times`) is dynamic: the video is split into `N`
equal segments where `N = round(duration / 510s)` clamped to `[1, 8]`, and a checkpoint sits
at the **end** of each segment. Each MCQ is generated from *its own* segment of transcript,
not the whole video. The final checkpoint is nudged 2 s before the end so it fires reliably
during playback, before the `ended` event.

### 8.2 What is recorded while a student watches

| Table | Contents |
|---|---|
| `video_engagement` | every `play`, `pause`, `seek`, `ended`, `hidden`, `visible`, `mute` with position |
| `video_coverage` | accumulated `watched_sec`, `completion_pct`, **`hidden_sec`** (tab backgrounded), `completed` |
| `video_mcq_response` | checkpoint answers with confidence, time-to-answer, attempt count |
| `video_reflection` | pre-video intention and post-video reflection free text |

`hidden_sec` is the important one: it separates *elapsed* time from *attended* time. A
student with a video playing in a background tab looks identical to an engaged student on
completion percentage alone.

Checkpoint MCQs feed the **BKT quiz tier** (`POST /api/v1/video/checkpoint/answer` →
`bkt.update(..., evidence_type="quiz")`), so watching lectures builds real mastery evidence.

### 8.3 In-video tutoring

`POST /api/v1/video/chat` answers questions with context **bounded by what the student has
watched**:

```
context = transcript[0 … t_pause]        # never spoils content ahead
meta    = summary(full_transcript)       # high-level overview available at any point
```

**Bi-directional timestamp hints** (`find_timestamp_hints`): a question is matched against
transcript segments by keyword overlap (≥ 2 content words after stop-word removal, 30-second
grouping window), and the student is pointed either backward ("this was covered at 4:12") or
forward ("this comes up at 11:40").

Forward hints support *anticipatory attention* — knowing something relevant is coming primes
encoding. Backward hints support *retrieval practice* — re-encountering content processed
too shallowly. The threshold of two keywords is calibrated: one would fire on nearly every
question (terms like "variable" saturate any C transcript), three would suppress hints for
short specific questions.

### 8.4 Catalog and chapters

Before `video_chapter` / `video_catalog` existed, "videos" were whatever `.mp4` files
happened to be in the directory, ordered by filename and bucketed into topics by
keyword-matching the filename. Chapter order, slide ranges, and not-yet-recorded videos
could not be represented at all.

Now: chapters 0–14 as printed in the flipped-classroom plan, one `video_catalog` row per
**planned** video with `filename` NULL until a recording exists. A chapter can therefore
list what is still outstanding — the plan contains chapters 6–11 and 14 with nothing
recorded yet. A unique partial index enforces that a recording belongs to exactly one
catalog slot.

### 8.5 Prelabs

[`backend/database/video/prelab.json`](backend/database/video/prelab.json) — **not tracked by
git**; must be copied to the server separately.

Two kinds of entry:

- **Graded prelabs** under `_prelabs` (e.g. `prelab-2`, "Warehouse Inventory Report") — the
  real assignment, with title, `due_before`, concept list, full problem statement and sample
  outputs. Referenced from a video by `prelab_ref`, shown in full only where
  `show_graded: true`.
- **Per-video practice problems** — a list of `{concept, prompt}` objects mapped to the
  video that teaches that concept, tagged with the graded prelab's concept names.

**Student flow:** at the end of a video, after the reflection prompt, **one problem is chosen
at random** and offered (`crShowPrelabModal`, `chat.js:2174`). It is optional and ungraded —
which is precisely what makes electing it an SRL measurement (`event_log(prelab_started)`)
rather than a compliance measurement.

**Teacher flow:** the Content tab lists every video; clicking one opens a textarea of its
questions, **one per line**. `POST /api/v1/video/prelab` writes `prelab.json` via an atomic
`os.replace`, so a crash mid-write cannot corrupt the file.

> **Fixed 2026-08:** the modal rendered `[object Object]` because `/video/prelab` returns
> `{prompt, concept, samples}` objects while older entries are plain strings. Both shapes are
> now handled. This bug was fixed twice in the dead `video_chat.js` before it was noticed
> that students actually use the `cr*` implementation in `chat.js`.

---

## 9. Security

### 9.1 Authentication and authorization

**The reported finding (external security review, 2026-08):**

> "SAGE appears to use HTTP rather than HTTPS… There does not appear to be server-side
> authentication or authorization once a user is logged in. User credentials and account
> information are stored in the browser's local storage, which can be modified using the
> developer tools available in modern browsers. By modifying these values, I was able to
> access other students' accounts, as well as administrative functionality and the teacher's
> dashboard."

Two concrete holes, both confirmed in the code:

1. `verify_teacher` compared a client-supplied `X-User-Role` **header** against `"teacher"`.
   The frontend read that value from localStorage, so editing `c_tutor_user` to
   `{"role":"teacher"}` in devtools granted the teacher dashboard *and* satisfied the backend
   check.
2. Login issued **no credential at all**. ~13 endpoints took `{username}` straight from the
   URL path with no auth — reading another student's data required no tampering, just a
   different URL.

**The fix** ([`backend/app/core/auth.py`](backend/app/core/auth.py)):

| Control | Implementation |
|---|---|
| Session credential | signed **JWT**, `HS256`, 12 h TTL (`SAGE_SESSION_HOURS`) |
| Transport | **httpOnly cookie** `sage_session` — unreadable and unforgeable from JS, so XSS or devtools cannot lift or alter it |
| CSRF | `samesite=lax` blocks the cookie on cross-site POSTs |
| TLS | `secure` flag driven by `SAGE_COOKIE_SECURE` (setting it on plain HTTP makes the browser drop the cookie entirely, locking everyone out) |
| Identity | `get_current_user()` — **the only sanctioned source of identity**. Never take a username from a path param, query string, or body and treat it as authenticated |
| Role | `require_teacher()` reads the role from the **signed token**; a student cannot grant it to themselves |
| Object-level | `require_self_or_teacher(target, caller)` on every endpoint that still takes a `{username}` — the parameter selects *which* record, the token decides *whether that is allowed* |
| Secret | `SAGE_JWT_SECRET`. If unset a random key is generated per process — **fail-safe, not fail-open**: tokens stop validating on restart, logging everyone out. A warning is logged |

**The class of bug that was missed on the first pass.** The initial audit swept path
parameters and missed three history endpoints where the username arrives as a **query
parameter**:

```python
@app.get("/api/v1/history/sessions")
async def get_sessions(username: str,
                       _caller: dict = Depends(auth.get_current_user)):
    # AUTHZ: `username` arrives as a QUERY parameter here, not a path parameter.
    auth.require_self_or_teacher(username, _caller)
    return history_manager.get_user_sessions_list(username)
```

`tests/test_auth_authorization.py` now walks the **whole route table** with
`inspect.signature`, so any future endpoint taking a `username` in any position without a
guard fails a test rather than shipping.

**Two related holes closed at the same time:**

- `logout()` cleared localStorage but never told the server — the cookie stayed valid.
  It now calls `POST /api/v1/auth/logout`.
- `chat.js` routed the user from localStorage while the server gated on the cookie. When
  those disagreed, the page redirected in a loop — 100+ requests/second of `303 See Other`,
  enough to take the server down. The frontend now bootstraps identity from `/auth/me`.

**Account recovery** (`forgot-username`, `forgot-password`, `reset-password`): tokens are
stored **hashed** (`password_reset_token.token_hash`), 60-minute TTL, single-use. Every
response is the same generic string regardless of whether the account exists — no account
enumeration. Passwords are bcrypt with SHA-256 pre-normalisation to work around bcrypt's
72-byte input limit.

### 9.2 The Sentinel gate stack

[`backend/app/agents/sentinel.py`](backend/app/agents/sentinel.py) — runs before any
generation. Formally `σ(x) = φ(x)·α(x)` where `φ = S_cog · S_acad · S_spam` is a product of
hard binary predicates and `α` is a soft threshold on goal alignment. `φ = 0` collapses `σ`
to 0 regardless of `α`: **no alignment score can override a structural block.**

| Layer | Name | Blocks when | Block reason |
|---|---|---|---|
| **L0** | System tag routing | — (routing only; explicitly *never* a security bypass) | — |
| **L1a** | Harmful-code hard block | unambiguous malware/attack construction | `Harmful Code` |
| **L1b** | Cross-user privacy guard | query targets another user's data | `Cross-User Privacy` |
| **Rule 1** | `S_cog` — cognitive overload | `F_t = rage` **OR** (`ΔF > τ_ΔF` **AND** `C_code > τ_comp`) **OR** DPT | `Cognitive Overload` |
| **Rule 2** | `S_goal` — competence-weighted semantic radius | malicious intent **AND** `s_goal < adaptive_threshold` | `Goal-Bounded Security` |
| **Rule 3** | `S_acad` — academic integrity | in a quiz **AND** (intent is PROBLEM **OR** `m_state = Helplessness`) | `Academic Integrity` |
| **Rule 4** | `S_spam` — attention hijacking | off-topic **AND** `n_strike ≥ 3` | `Attention Hijacking` / `Off-Topic Warning` |
| **L6** | Teacher topic locks | entity matches a disabled topic | `Teacher Lock` |
| **L7** | Trajectory / crescendo net | session risk ≥ threshold, or transcript judge says block | `Trajectory Risk` / `AI Semantic Judge` |

**Rule 1 detail — the Disengagement Prediction Trigger.** Beyond rage and
frustration×complexity, DPT fires when the last *two* frustration deltas were both above
`τ_ΔF` — an escalating trajectory, caught before the student reaches rage. The intervention
differs by cause: DPT gets "let's zoom out — what feels most confusing?", complexity overload
gets "tell me in plain English what this code is *supposed* to do."

**Rule 2 detail — context-gated concepts.** `infinite loop`, `fork()`, `while(1)`, `for(;;)`
are legitimate C topics students must learn to recognise and debug. They are treated as
malicious **only when paired with harmful context in the same message** ("infinite loop to
crash the server"). Blocking the phrase alone would break "why does my code have an infinite
loop?" — a debugging student. Real attacks ("fork bomb") are still hard-blocked at L1a
regardless. Crucially, *not* blocking the benign single turn lets it reach L7, where the
whole crescendo is judged together.

### 9.3 Earned latitude — the adaptive security radius

The alignment bar a security-sensitive query must clear is **relaxed for learners who have
earned standing on the curriculum**:

```
τ_goal(|K|) = max( 0.60,  0.85 − 0.05·ln(1 + |K|) )
```

where `|K|` is the number of **certified** topics.

Three deliberate design choices:

1. **Keyed on the certified set `K`, not on activity.** Latitude must be bought with
   demonstrated mastery, never with mere engagement. Keying it on topics *touched* would let
   a learner widen their own security radius simply by asking questions — precisely the
   asserted-vs-earned confusion the learner model exists to remove.
2. **Logarithmic, not linear.** A linear slope of 0.05 would drive the threshold negative
   after 17 certified topics, and the curriculum has 26 — disabling the check entirely for
   the strongest students.
3. **Hard floor at 0.60.** However many topics are certified, the check never disappears.

This is the learner model used as a **security instrument**, which is the extension's
distinctive claim: mastery state is not only a pedagogical quantity but an authorization one.

### 9.4 Trajectory-level access control (multi-turn defense)

Per-message layers catch anything with a "tell" *this* turn. A crescendo attack has no tell
in any single turn — each looks benign, and only the trajectory is harmful.

Session risk is built from signals already computed (intent, `s_goal`, C-domain context,
off-topic strikes). The only new state is **two floats in `sessions.state`** — no new module:

| Parameter | Value | Meaning |
|---|---|---|
| `TRAJ_ACC_DECAY` | 0.8 | accumulator memory (decayed sum of per-turn risk) |
| `TRAJ_PEAK_DECAY` | 0.9 | peak cools over ~5–6 clean turns (usability guard) |
| `TRAJ_TAU_JUDGE` | 0.45 | escalate to the LLM transcript judge |
| `TRAJ_TAU_JUDGE_BUILD` | 0.05 | far lower bar when the turn asks to **construct** something |
| `TRAJ_TAU_BLOCK` | 0.85 | hard block |
| `TRAJ_POST_BLOCK_ACC` | 0.8 | accumulator floor after a security block |
| `TRAJ_POST_BLOCK_PEAK` | 0.7 | peak floor after a security block |

**The build-threshold split.** The trigger is supposed to be high-recall and the transcript
judge is supposed to supply the precision. At a single threshold the trigger was in fact
precision-tuned, which is how a slow-burn attack walked through it. Escalating costs one
judge call and **cannot itself block** — the judge still decides — so recall is cheap there
and false positives are bounded by judge accuracy. This fix moved containment 93% → 100%.

**Accumulator poisoning.** A per-message block returns *before* the L7 step, so the blocked
turn would otherwise leave session risk untouched — letting the very next turn ("show me the
code that does exactly that") slip through with a clean accumulator. Security-grade blocks
therefore **poison** the accumulator to a floor, so follow-ups are scrutinised.

**Ablation hooks.** `SAGE_TRAJ_TAU_JUDGE` and `SAGE_TRAJ_TAU_BLOCK` are env-overridable, so
setting both above 1.0 makes the layer unreachable (session risk is bounded by 1.0) while
leaving every per-message layer untouched. That isolates this layer's contribution to
containment — which layer-conditioned attribution in the audit cannot show on its own, since
it reports where blocks came from, not what would have happened without them.

### 9.5 Rate limiting and input caps

[`rate_limiter.py`](backend/app/core/rate_limiter.py) — hand-rolled sliding-window log, no
new dependency.

| Bucket | Limit |
|---|---|
| `chat` | 20 messages / minute / IP |
| `auth` | 10 login+signup / minute / IP |
| message size | 8000 characters |

Together these bound worst-case LLM cost per client. Separate scopes mean a burst of logins
does not consume the chat budget. Real client IP is read from `X-Forwarded-For` (leftmost),
because behind nginx/ALB `request.client.host` is the proxy and every user would share one
bucket.

**Known scope limit:** in-memory and per-process. Correct for the single instance we run; if
we ever run multiple replicas the counters must move to a shared store (Redis).

### 9.6 The Security Ledger

`GET /api/v1/analytics/teacher/security_ledger` — aggregate-first by design. **No learner is
named by default**: leading a dashboard with "who tried to jailbreak" is itself a harm.

The Sentinel blocks for ten distinct reasons and they are **not all security**. A student
stopped because they are overwhelmed needs a completely different response from one probing
for a jailbreak, so blocks are bucketed:

| Bucket | Reasons | Instructor reading |
|---|---|---|
| **security** (evasion) | Goal-Bounded Security, Harmful Code, Cross-User Privacy, AI Semantic Judge, Trajectory Risk, Tag Bypass Attempt, Security Policy | someone is probing the system |
| **cognitive** | Cognitive Overload | a wellbeing gate fired — rage, escalating frustration, or DPT. **Nothing adversarial** |
| **focus** | Off-Topic Warning, Attention Hijacking | attention drift |
| **integrity** | Academic Integrity | answer-seeking during a quiz |
| **policy** | Teacher Lock | your own curriculum lock fired |
| **pedagogical** | everything else | the tutor guiding instead of handing over answers |

The evasion bucket also carries `avg_risk` / `max_risk` from `turn_log.traj_risk`, and blocked
queries are categorised by technique with truncated, **unattributed** examples — so the
instructor sees the *kind* of attempt, not just a count, without a name attached.

### 9.7 Residual risks

| Risk | State |
|---|---|
| `SAGE_JWT_SECRET` unset in production | **Open.** Every restart logs all users out. Set it in the server `.env` |
| Checkpoint fail-open | **Open.** On a server error the client sets `checkpoints = []`, silently removing the quiz gate rather than failing closed |
| `mizzousage.net` sinkholed by Cisco Umbrella | Open on university-managed networks (`146.112.61.110`). Students on personal devices / cellular are unaffected |
| In-memory rate limiting | Accepted at current scale (single instance) |
| No audit trail for teacher data access | Open. A teacher reading a student record leaves no record |

---

## 10. Data model and telemetry

### 10.1 Design principles

- **Append-only.** Nothing in the telemetry tables is updated or deleted (`video_coverage` is
  the one accumulator, by necessity).
- **`study_id`-linked.** Every research table carries `study_id`, resolved through
  `study_participants (study_id, username, cohort, enrolled_at)`. Analysis works in
  pseudonymised space; the mapping table is the only place identity lives.
- **Raw before derived.** `evidence_log` stores raw correctness per tier, so **any**
  knowledge-tracing model can be re-simulated offline — including a flat single-tier BKT
  baseline, which is how the multi-tier claim is tested against its own null.
- **Prediction before outcome.** `prediction_log` writes the model's prediction *before* the
  observation, which is the only way a calibration claim is falsifiable.
- **Centralised.** Every write goes through [`telemetry.py`](backend/app/core/telemetry.py)
  (27 logger functions), never inline SQL scattered across handlers.

### 10.2 The 35 tables

**Application state (9)**

| Table | Holds |
|---|---|
| `users` | username, bcrypt hash, role, name, **email (required, unique)**, university, `is_blocked`, `learning_profile` JSON |
| `password_reset_token` | `token_hash` (never the token), TTL, `used_at` |
| `sessions` | chat rooms with a JSON `state` blob (active plan, quiz FSM, withholding flags) |
| `messages` | full chat log + `intent`, `topic`, `action_taken`, `style_used` |
| `assignments` | teacher-pushed challenges: `PENDING / SUBMITTED / GRADED` |
| `user_knowledge` | **the learner model** — see below |
| `user_goals` | the SRL goal statement |
| `user_feedback` | thumbs up/down + free text (the UCB1 reward signal) |
| `user_bkt_calibration` | per `(user, concept, tier)`: `self_assessment`, `n_adjustments`, `direction_ema`, `adapted_P_G` |

`user_knowledge` columns (the persisted learner state):

```
p_mastery_quiz / _micro / _code    the three posteriors
p_mastery                          composite (display only)
n_evidence_quiz / _micro / _code   correct-answer counts (certification gate)
is_certified / ever_certified      hysteresis flag / permanent earned flag
last_quiz_at / _micro_at / _code_at   per-tier timestamps for decay
decay_quiz_lam / _micro_lam / _code_lam   adaptive λ (SM-2 coupled)
hs_quiz / hs_micro / hs_code       unearned head-start portion (identifiable, clawback-able)
head_start_at
interval_days / ease_factor / due_date / review_count   SM-2 state
```

**Research telemetry (16)**

| Table | Contribution | Answers |
|---|---|---|
| `event_log` | all | append-only raw event stream — the safety net |
| `evidence_log` | C1 | raw per-tier correctness; enables offline re-simulation |
| `bkt_history` | C1 | per-tier posterior trajectory snapshots |
| `prediction_log` | C3 | `p_bkt_pred` and `p_eff_pred` logged **before** the outcome |
| `calibration_log` | C3 | every slider move + every `P_G` adaptation |
| `jol_log` | SRL | confidence 1–5 vs. actual correctness |
| `mcn_log` | SRL | MCN verdicts: `map_C`, `map_K`, posterior, evidence vector |
| `response_log` | C2 | mastery level + format sections + weak tier per AI turn |
| `behavior_log` | C2 | productive-struggle events |
| `affect_log` | SRL | `delta_f`, frustration level, `academic_emotion`, whether an intervention fired |
| `turn_log` | all | one row per turn with the **complete state vector** (see below) |
| `session_log` | engagement | session envelope: start, end, turn count, user agent |
| `path_event` | forethought | curriculum path adherence vs. deviation |
| `quiz_log` | psychometrics | item-level: question, answer, correctness, confidence, time-to-answer |
| `misconception_log` | learning-from-error | store / resolve with EMA value |
| `headstart_log` | C1 | every seed and clawback, with sources |

`turn_log` is the richest single table — `query_text`, `response_text`, `intent`, `entities`,
`topic`, `mastery_level`, `s_goal`, `c_code`, `m_state`, `delta_f`, `n_strike`, `n_sources`,
`latency_ms`, `was_blocked`, `block_reason`, and the trajectory triple `traj_risk` / `traj_acc` / `traj_peak`.

> **Fixed 2026-08:** `block_reason` was being discarded for most blocks, so the Security
> Ledger under-counted. Now `block_reason=((final_data.get("block_reason") or
> final_data.get("intent")) if _was_blocked else None)`.

**Classroom (7):** `video_checkpoint`, `video_engagement`, `video_coverage`,
`video_mcq_response`, `video_reflection`, `video_chapter`, `video_catalog` — see §8.2.

**Ground truth and linkage (3):** `study_participants`, `assessment` (pre/post C-test + MAI
survey, Bloom-tagged), `self_report` (self-efficacy, interest, goal orientation).

### 10.3 Linking to course outcomes

There is **no grade storage anywhere in SAGE** — `assignments.status` reaches `GRADED` but
carries no score column. Ground truth for any outcome analysis must come from outside.

The join key is **email**: it is required and enforced unique at signup
(`validate_email` → "Email is required."; `main.py:1078` calls `user_manager.email_taken`),
and Canvas exports email in its gradebook CSV. Join once on email, then work in `study_id`
space so the analysis set carries no identifiers.

Practical constraints, in order of urgency:

1. **Verify students used real emails** — a junk address breaks the join for that student and
   you will not find out until the semester ends.
2. **Snapshot the roster now.** Students drop, change emails, delete accounts. A CSV of
   `username, email, name, created_at` taken today is the linkage anchor.
3. **Confirm IRB coverage.** Joining behaviour to course grades is identifiable educational
   records (FERPA). If the protocol only covers system telemetry, the *linking* is what needs
   an amendment — and amendments take longer than data collection.
4. **Prefer exam score to composite grade** as the outcome. Prelab → lab grade is
   near-circular (the prelab gates the lab). SAGE never touches the exam, so a metacognitive
   measure from week 5 predicting week-9 exam performance is genuine transfer.

`scripts/export_research_data.py` emits the analysis-ready per-student, per-concept, per-tier
table.

---

## 11. Evaluation suite

[`IRL_extension_script/`](IRL_extension_script/) — reproduces every result in the paper.
Results in [`IRL_extension_results/`](IRL_extension_results/), phase reports `PHASE0`–`PHASE5`.

| # | Script | Result family | Metrics |
|---|---|---|---|
| 0 | `eval_00_data_audit` | data availability / staleness | what data exists, which stored results are stale |
| 1 | `eval_01_cedubench_ci` | C-EduBench quality | code density (**lower is better**), security compliance, curriculum compliance |
| 2 | `eval_02_sage_delta` | SAGE delta | pedagogy score, per-item deltas |
| 3 | `eval_03_winrate_conference_protocol` | win rate | debiased vs. faithful win rate |
| 4 | `eval_04_multiturn_jailbreak` | multi-turn enforcement | containment / ASR, escalation recall, false-block rate, defense depth, trajectory attribution |
| 5 | `eval_05_evidence_diversity` | **the Evidence Diversity Problem** | false certification of lopsided learners |
| 6 | `eval_06_mastery_adaptation` | mastery adaptation | Π-fidelity, level separation, weak-tier identification |
| 7 | `eval_07_judge_validation` | judge validation | human-rater agreement |

**Judging is a separate provider from generation.** Generation is Gemini
(`GOOGLE_API_KEY`); judging is `gpt-4o` (`OPENAI_API_KEY`). The server generating fine tells
you nothing about whether judging will work — Phase 1 hit exactly this, with collection
succeeding while every judge call returned `429 — no credits remaining`. `judge_model` is
recorded next to every verdict, because the judge is an instrument.

**Current headline numbers** (from `PHASE5_REPORT.md` — pull paper numbers from there, all
earlier figures carry supersession banners):

- Multi-turn containment, core: **100%** (3/3 runs)
- Held-out 25: **96%** mean, range [92–100] — nondeterministic; the single-run 100% was
  optimistic
- Slow-burn fix: 93% → 100%
- Trajectory ablation: null flipped 100% → 80%, but **McNemar p = 0.25 at n = 15** — the
  ablation is directionally right and statistically underpowered

**Unit tests** (`tests/`): `test_agent`, `test_evaluator`, `test_report`, `test_mcn`,
`test_concept_canon`, `test_user_manager`, `test_api_routes`, **`test_auth_authorization`**
(the route-table sweep), **`test_code_evidence_grading`** (including the decay regression).

---

## 12. Deployment and operations

### 12.1 Production

| Item | Value |
|---|---|
| URL | `https://mizzousage.net/` |
| Host | AWS Lightsail, `18.225.209.224`, user `ubuntu` |
| Runtime | Docker Compose (`backend` + `neo4j`) |
| TLS | nginx + Let's Encrypt |
| SSH key | `~/.ssh/CERI-AWS.pem` |

### 12.2 Deploy

```bash
# local — bundle (excludes venv, git, local vector DB)
zip -r tutor_v2.zip . -x "**/__pycache__/*" "**/venv/*" ".git/*" "database/chroma_db/*"
scp -i ~/.ssh/CERI-AWS.pem tutor_v2.zip ubuntu@18.225.209.224:~/

# remote
ssh -i ~/.ssh/CERI-AWS.pem ubuntu@18.225.209.224
cd ~/AI-Tutor && sudo docker compose down
cd ~ && mv AI-Tutor AI-Tutor_backup_$(date +%F_%H-%M)      # rollback point
mkdir ~/AI-Tutor && unzip -o tutor_v2.zip -d ~/AI-Tutor
cd ~/AI-Tutor && sudo chmod -R 777 backend/database/ logs/ resources/
sudo docker compose up --build -d
sudo docker compose logs -f backend
```

> **`--build` is mandatory.** `backend/app` is **not** bind-mounted — application code is
> baked into the image. Restarting without rebuilding runs the old code and looks exactly
> like "my change had no effect."
>
> **`prelab.json` is not in git.** Copy `backend/database/video/prelab.json` separately or
> the classroom loses every prelab.

### 12.3 Required environment

| Variable | Purpose | Consequence if unset |
|---|---|---|
| `GOOGLE_API_KEY` | generation, embeddings, TTS | system cannot respond |
| `OPENAI_API_KEY` | offline judging only | evals fail, runtime fine |
| `NEO4J_URI` / `_USER` / `_PASSWORD` | knowledge graph | prerequisite gate degrades |
| **`SAGE_JWT_SECRET`** | session signing | **ephemeral key per process — everyone logged out on restart** |
| `SAGE_COOKIE_SECURE` | `true` behind TLS | cookie sent over plain HTTP |
| `SAGE_SESSION_HOURS` | session TTL (default 12) | — |
| `MCN_ENABLED` | MCN master switch (default `false`) | MCN returns `None` everywhere |
| `SMTP_HOST` / `SMTP_FROM` / … | password-reset email | relay mode logs the body instead of sending |
| `RATE_LIMIT_CHAT_MAX` / `_AUTH_MAX` / `MAX_MESSAGE_CHARS` | cost + abuse control | defaults 20 / 10 / 8000 |

### 12.4 Operational scripts

| Script | Purpose | Cadence |
|---|---|---|
| `scripts/backup_db.py` | consistent DB snapshot with retention | **daily** |
| `scripts/verify_telemetry.py` | integrity check that every logger writes and links | **weekly, from week 1** |
| `scripts/import_study_data.py` | roster + pre/post + MAI import, keyed by `study_id` | per phase |
| `scripts/export_research_data.py` | analysis-ready per-student/concept/tier table | on demand |
| `scripts/reset_password.py` | admin password reset (`--list`, `--user`, `--password`, `--apply`) | as needed |
| `scripts/pregenerate_checkpoints.py` | build checkpoint MCQs ahead of class | after upload |
| `scripts/seed_video_catalog.py` | populate chapters/videos from the plan | once |
| `scripts/ingest_data.py` / `ingest_manual_graph.py` | build ChromaDB / Neo4j | on content change |
| `scripts/security_audit.py` | route-level auth sweep | before release |

### 12.5 Classroom protocol dependencies

Several contributions have **no data at all** unless the classroom protocol supplies it:

1. Mandate goal-setting in week 1.
2. Weekly 5-minute **Dashboard Reflection Days** — without these there is no active-calibrator
   group and Contribution 3 has no comparison.
3. Pop-quizzes count toward participation (drives JOL + evidence).
4. Lock curriculum week-by-week (generates bypass-attempt data).
5. Push challenges to at-risk students via the teacher dashboard Risk Matrix.
6. **Pre/post C-test + MAI survey** on day 1 and day 30 — all ground truth for growth claims.

---

## 13. Known gaps and open issues

Ordered by how much they block research output.

### Blocking

| Issue | Detail |
|---|---|
| **`video_reflection` = 0 rows** | The post-video reflection has *never* fired. The end-of-video sequence requires reaching the end, and most students watch < 20%. Every reflection-phase SRL analysis is currently impossible |
| **`prelab_started` = 5 events** | Elective-transfer analysis is unpowered. Same root cause |
| **No prior-ability covariate** | Without one, every outcome result reads as "good students do everything well." A 5-item prior-programming-experience survey is the single highest-value addition, and it has to happen **before** more data accrues |
| **Checkpoint fail-open** | On a server error the client sets `checkpoints = []`, silently removing the quiz gate. Fails open where it should fail closed |

### Correctness

| Issue | Detail |
|---|---|
| `topic='VARIABLES'` across the catalog | Every card in the video catalog is tagged with the same topic, so topic-level video analytics are meaningless |
| `TAU_COMP` comment mismatch | `config.py` sets 5; the `sentinel.py` comment says 3. The code uses 5 |
| Decertification forecast empty | The only certified learners have NULL `last_*_at`, so `_apply_decay` no-ops. The panel is correct and honestly empty; it populates with dated practice |
| `deviation_type` classification | `path_event` gate-pressure analysis needs this fixed before it means anything |

### Housekeeping

| Issue | Detail |
|---|---|
| `SAGE_JWT_SECRET` unset in production | See §9.7 |
| SMTP not configured on the server | Password-reset emails are logged, not sent |
| `prelab.json` untracked | Must be copied on every deploy |
| Uncommitted work | Deploy pending: `git add -A`, push, `docker compose up --build -d` |
| `video_chat.html` / `.js` / `.css` deleted | Confirmed unreferenced; students use the `cr*` implementation in `chat.js`. Deletion staged, not yet committed |
| README deployment section stale | Still references `SRL-script/`, which no longer exists |

### Research-design open questions

| Question | State |
|---|---|
| **D1 — cognitive-load guardrail** | §III-G of the paper claims Eq. (18) overrides Π. It does **not** — `_cognitive_load` feeds only the instructor dashboard. Recommendation: remove the claim rather than implement an untested behavioural path mid-study |
| Human rater for `eval_07` | Needs a real second person |
| MCN activation | Still `MCN_ENABLED=false` in production. It ships dark and has never run on live students |
| Dashboard-accuracy protocol | Show instructors triage, have them name top-3 + intervention, score against held-out ground truth. Stronger than a Likert usability item — protocol not yet designed |

---

## 14. File map

```
AI-Tutor/
├── SYSTEM_OVERVIEW.md              # this document
├── README.md                       # setup + contribution summary
├── ANSWER_GRADING_MECHANISM.md     # how answers are graded
│
├── backend/
│   ├── Dockerfile                  # includes ffmpeg (required by Whisper)
│   └── app/
│       ├── main.py                 # 4600 lines: 90 endpoints, streaming, dashboards
│       ├── dependencies.py
│       ├── agents/
│       │   ├── cot_rag_agent.py    # orchestrator — the request pipeline
│       │   ├── sentinel.py         # L0–L7 gate stack, trajectory defense
│       │   ├── examiner.py         # quizzes, JOL-first
│       │   ├── socratic.py         # tutoring, mastery-adapted prompts
│       │   ├── reviewer.py         # code review
│       │   ├── scaffolding.py      # multi-step plans
│       │   ├── profiler.py         # affect
│       │   ├── base.py · schema.py
│       ├── core/
│       │   ├── bkt_model.py        # multi-tier BKT, decay, certification
│       │   ├── srl_calibration.py  # the calibration loop
│       │   ├── mcn.py              # Bayesian network (pure)
│       │   ├── mcn_cpts.py         # CPT store, hot reload
│       │   ├── mcn_evidence.py     # live-data adapters
│       │   ├── mcn_service.py      # safety envelope
│       │   ├── prereq_headstart.py # prerequisite-coupled priors
│       │   ├── threshold_calibrator.py  # GMM threshold calibration
│       │   ├── learner_model.py    # unified multidimensional profile
│       │   ├── user_knowledge_manager.py  # SM-2, misconceptions
│       │   ├── concept_canon.py    # canonical topic names
│       │   ├── telemetry.py        # 27 logger functions
│       │   ├── auth.py             # JWT, cookies, authorization
│       │   ├── rate_limiter.py · email_service.py · validators.py
│       │   ├── fast_classifier.py · history_manager.py
│       │   ├── assignment_manager.py · settings_manager.py · config.py
│       ├── db/
│       │   ├── sqlite_db.py        # 35 tables + migrations
│       │   ├── graph_db.py         # Neo4j
│       │   ├── vector_store.py     # ChromaDB
│       │   └── llm_interface.py    # provider abstraction
│       ├── services/video_service.py   # Whisper queue, checkpoints, hints
│       ├── static/js/chat.js       # THE live frontend (chat + classroom)
│       ├── static/js/student_dashboard.js
│       └── templates/
│           ├── index.html · student_dashboard.html
│           ├── teacher_dashboard.html · reset_password.html
│   └── database/
│       ├── ai_tutor.db · chroma_db/
│       └── video/                  # .mp4, transcripts, prelab.json (untracked)
│
├── docs/                           # MCN explainer, dashboard, IRL roadmap, testing guides
├── Additional_files/               # papers (.tex/.pdf), data plans, syllabus, learner model
├── IRL_extension_script/           # eval_00 … eval_07
├── IRL_extension_results/          # PHASE0–PHASE5 reports + CSVs
├── scripts/                        # ingest, import, backup, verify, benchmarks
├── tests/                          # pytest suite
└── resources/                      # course materials + knowledge graph source
```

---

## 15. References

**Knowledge tracing and mastery**

1. Corbett, A. T., & Anderson, J. R. (1995). Knowledge tracing: Modeling the acquisition of procedural knowledge. *User Modeling and User-Adapted Interaction, 4*(4), 253–278.
2. Anderson, J. R., Corbett, A. T., Koedinger, K. R., & Pelletier, R. (1995). Cognitive tutors: Lessons learned. *Journal of the Learning Sciences, 4*(2), 167–207.
3. Bloom, B. S. (1968). Learning for mastery. *Evaluation Comment, 1*(2), 1–12.
4. Yudelson, M. V., Koedinger, K. R., & Gordon, G. J. (2013). Individualized Bayesian knowledge tracing models. *AIED 2013*.
5. Baker, R. S. J. d., Corbett, A. T., & Aleven, V. (2008). More accurate student modeling through contextual estimation of slip and guess probabilities. *ITS 2008*.

**Self-regulated learning and metacognition**

6. Zimmerman, B. J. (2000). Attaining self-regulation: A social cognitive perspective. In *Handbook of Self-Regulation* (pp. 13–39). Academic Press.
7. Pintrich, P. R. (2000). The role of goal orientation in self-regulated learning. In *Handbook of Self-Regulation* (pp. 451–502). Academic Press.
8. Azevedo, R., & Hadwin, A. F. (2005). Scaffolding self-regulated learning and metacognition. *Instructional Science, 33*, 367–379.
9. Bull, S., & Kay, J. (2007). Student models that invite the learner in: The SMILI open learner modelling framework. *IJAIED, 17*(2), 89–120.
10. Nelson, T. O., & Narens, L. (1990). Metamemory: A theoretical framework and new findings. *Psychology of Learning and Motivation, 26*, 125–173.
11. Aleven, V., McLaren, B., Roll, I., & Koedinger, K. (2006). Toward meta-cognitive tutoring: A model of help seeking with a Cognitive Tutor. *IJAIED, 16*(2), 101–128.
12. Schraw, G., & Dennison, R. S. (1994). Assessing metacognitive awareness. *Contemporary Educational Psychology, 19*(4), 460–475. *(MAI)*

**Learning science**

13. Sweller, J. (1988). Cognitive load during problem solving. *Cognitive Science, 12*(2), 257–285.
14. Kalyuga, S., Ayres, P., Chandler, P., & Sweller, J. (2003). The expertise reversal effect. *Educational Psychologist, 38*(1), 23–31.
15. Roediger, H. L., & Karpicke, J. D. (2006). Test-enhanced learning. *Psychological Science, 17*(3), 249–255.
16. Hattie, J., & Timperley, H. (2007). The power of feedback. *Review of Educational Research, 77*(1), 81–112.
17. Vygotsky, L. S. (1978). *Mind in Society.* Harvard University Press.
18. Wood, D., Bruner, J. S., & Ross, G. (1976). The role of tutoring in problem solving. *Journal of Child Psychology and Psychiatry, 17*(2), 89–100.
19. Collins, A., & Stevens, A. L. (1982). Goals and strategies of inquiry teachers. In *Advances in Instructional Psychology* (Vol. 2, pp. 65–119). Erlbaum.
20. D'Mello, S., & Graesser, A. (2012). Dynamics of affective states during complex learning. *Learning and Instruction, 22*(2), 145–157.
21. Paivio, A. (1991). Dual coding theory: Retrospect and current status. *Canadian Journal of Psychology, 45*(3), 255–287.
22. Pressley, M., Wood, E., Woloshyn, V. E., Martin, V., King, A., & Menke, D. (1992). Encouraging mindful use of prior knowledge. *Educational Psychologist, 27*(1), 91–109.
23. VanLehn, K. (2006). The behavior of tutoring systems. *IJAIED, 16*(3), 227–265.
24. Wozniak, P. A., & Gorzelanczyk, E. J. (1994). Optimization of repetition spacing in the practice of learning. *Acta Neurobiologiae Experimentalis, 54*, 59–62. *(SM-2)*

**Systems and methods**

25. Lewis, P., Perez, E., Piktus, A., et al. (2020). Retrieval-augmented generation for knowledge-intensive NLP tasks. *NeurIPS 2020*.
26. Liang, C., Wu, Z., Huang, W., & Giles, C. L. (2018). Recovering concept prerequisite relations from university course dependencies. *AAAI-18*.
27. Auer, P., Cesa-Bianchi, N., & Fischer, P. (2002). Finite-time analysis of the multiarmed bandit problem. *Machine Learning, 47*, 235–256. *(UCB1)*
28. Zaratiana, U., Tomeh, N., & Shi, C. (2023). GLiNER: Generalist model for named entity recognition. *arXiv:2311.08526*.
29. Pearl, J. (1988). *Probabilistic Reasoning in Intelligent Systems.* Morgan Kaufmann. *(explaining away)*
30. Radford, A., Kim, J. W., Xu, T., et al. (2023). Robust speech recognition via large-scale weak supervision. *ICML 2023*. *(Whisper)*
