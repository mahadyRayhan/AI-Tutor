# SAGE-SRL — Complete Data Collection Inventory

Every data stream the system records during the class trial, followed by a focused
synthesis through three lenses: **Learner Model**, **BKT Calibration**, and **SRL**.

All telemetry is anonymized and linked by a single `study_id`.

---

## PART 1 — Complete Data Inventory

### A. Dedicated Telemetry Tables (the research dataset)

| # | Table | What each row captures |
|---|-------|------------------------|
| 1 | `study_participants` | study_id ↔ username ↔ cohort (the anonymized linkage key) |
| 2 | `event_log` | Append-only raw stream of discrete events (safety net) |
| 3 | `evidence_log` | Every BKT evidence item: concept, tier, correct/incorrect, question_id |
| 4 | `bkt_history` | Per-tier posterior trajectory: p_tilde, n_evidence, certification flags, trigger |
| 5 | `prediction_log` | P_BKT and P_eff logged **before** each outcome + the outcome |
| 6 | `calibration_log` | Every slider move: p_bkt, p_self, delta, direction EMA, P_G old→new |
| 7 | `jol_log` | Judgment of Learning: confidence (1–5) vs actual correctness |
| 8 | `response_log` | Per turn: intent, concept, mastery level applied |
| 9 | `behavior_log` | copy_code clicks, dwell time, thumbs_down clicks |
| 10 | `affect_log` | Per turn: delta_f, frustration level, intervention fired |
| 11 | `assessment` | Pre/post C-test + MAI survey items (Bloom-tagged) |
| 12 | `session_log` | Session start/end/duration/turn-count |
| 13 | `turn_log` | Full conversation + state vector (m_state, s_goal, c_code, delta_f, n_strike), latency, was_blocked |
| 14 | `path_event` | Curriculum path deviation: on_path / skip_ahead / revisit / off_path |
| 15 | `quiz_log` | Item-level: question, student answer, correct answer, correctness, confidence, time-to-answer |
| 16 | `misconception_log` | Misconception form → decay → resolve (EMA value) |

**`event_log` sub-types:** `goal_set`, `quiz_skip`, `self_assessment`, `certification`,
`decertification`, `withholding_fired`, `gatekeeper_block`, `feedback`

### B. Operational Tables (system state, also analyzable)

| Table | What it holds |
|-------|---------------|
| `user_knowledge` | **Current** BKT state per concept: p_mastery_quiz/micro/code, n_evidence_*, decay_*_lam, ease_factor, is_certified, ever_certified, last_*_at, SM-2 (interval, review_count, due_date) |
| `users.learning_profile` (JSON) | frustration_level, frustration_history, off_topic_strikes, skipped_challenges, style_win_rates (UCB1), misconceptions (EMA) |
| `user_bkt_calibration` | Current calibration state: self_assessment, n_adjustments, direction_ema, adapted_P_G |
| `messages` | Full chat log: role, content, topic, intent, action_taken, style_used |
| `sessions` | session_id, title, state (active quiz/plan), created_at |
| `user_goals` | Current goal text + updated_at |
| `user_feedback` | 👍/👎 + written reason |
| `assignments` | Teacher-pushed challenges + student submissions + grades |
| `calibration_buffer`, `threshold_history` | GMM adaptive certification-threshold calibrator |

---

## PART 2 — Key Points Through Three Lenses

### LENS 1 — The Learner Model (what the system knows about each student)

SAGE maintains a rich, multi-dimensional, **open** learner model. Per student, per concept:

**Cognitive (what they know):**
- Three independent BKT posteriors — **quiz** (declarative), **micro** (procedural),
  **code** (applied) — mapped to Bloom's taxonomy.
- **Conjunctive certification**: mastery requires ALL three tiers ≥ 0.95 with ≥ 3
  evidence items each. Prevents the "illusion of competence."
- **Forgetting decay** with SM-2 coupling: mastery erodes over gaps; successful reviews
  slow the decay (ease_factor).
- **Misconceptions**: EMA-tracked; a high-confidence wrong answer raises m_score,
  correct answers decay it — the model tracks *specific errors*, not just "wrong."

**Metacognitive (what they think they know):**
- **Self-assessment** via downward-only slider → `user_bkt_calibration`.
- **JOL** confidence ratings before quizzes → calibration accuracy (perceived vs actual).

**Affective (how they feel):**
- Frustration level + trajectory (delta_f), rage detection, intervention history.

**Preference (how they learn best):**
- UCB1 style bandit: analogy / technical / visual win-rates from 👍/👎.

**Derived classification:** novice → developing → proficient → reviewing, which drives
response adaptation.

> **Key point:** This is an **Open Learner Model** — the student can *see and negotiate*
> their own model (via the dashboard sliders), which is what makes the SRL loop possible.
> The data streams that expose it: `bkt_history` (trajectory), `user_knowledge` (state),
> `misconception_log`, `affect_log`, `response_log` (classification applied).

---

### LENS 2 — BKT Calibration (the human-in-the-loop calibration loop)

The novel mechanism: student metacognitive feedback tunes the BKT model itself.

**Two layers:**
1. **Score Blending (immediate):** `P_eff = 0.6·P_BKT + 0.4·P_self`. The student's
   downward self-assessment shifts the *displayed / adaptation* mastery — never
   certification.
2. **Parameter Adaptation (sustained):** after ≥ 3 consistent downward adjustments on a
   tier, the per-user **guess rate P_G** increases (bounded). The model learns "this
   student guesses well on quizzes" → future correct answers count for less.

**Guardrails (what makes it defensible):**
- **Downward-only** — a student can say "I know less," never "trust me, I know it."
  Preserves the certification theorem (can't self-certify).
- **Certification always reads P_BKT** (with adapted params) — the student influences the
  *model*, never the *gate*.
- Raising mastery happens **only through evidence** (ask to be quizzed).

**Data captured for the proof:**
- `calibration_log` — every slider move, direction EMA, P_G old→new.
- `prediction_log` — **P_BKT vs P_eff logged before each later outcome**. This is the
  within-subject test: after a student calibrates, does P_eff predict their next quiz
  better than P_BKT (lower Brier/log-loss)? This is the evidence that survives the
  self-selection critique.

> **Key point:** Novelty vs. Yudelson (2013) personalized BKT: parameters here are driven
> by **metacognitive feedback**, not behavior alone. Novelty vs. Bull & Kay negotiated
> OLM: the negotiation actually **rewrites the emission parameters**, not just the display.
> The defensible sliver: *sustained metacognitive disagreement adapts per-user guess/slip.*

---

### LENS 3 — SRL (self-regulated learning signals, Zimmerman's cycle)

Every phase of the SRL cycle is instrumented with real behavioral data:

| SRL Phase | Mechanism in SAGE | Data stream |
|-----------|-------------------|-------------|
| **Forethought** (plan) | Goal setting, AI skill-tree, path adherence | `event_log(goal_set)`, `path_event`, `user_goals` |
| **Performance** (monitor) | Quiz attempts, productive struggle, skips | `evidence_log`, `quiz_log`, `behavior_log`, `event_log(quiz_skip)` |
| **Self-reflection** (evaluate) | JOL confidence, dashboard self-assessment | `jol_log`, `calibration_log` |
| **Emotional regulation** | Frustration tracking + empathetic reframe | `affect_log` |
| **Learning from error** | Misconception form → resolve | `misconception_log` |

**The three highest-value SRL signals (rare in real classroom data):**
1. **Per-turn metacognitive state (m_state FSM)** in `turn_log` —
   Planning / Monitoring / Reflecting / Helplessness on every single turn. Very few
   datasets have a per-turn metacognitive label.
2. **Calibration accuracy** — JOL confidence vs actual (`jol_log`) AND slider vs BKT
   (`calibration_log`): two independent measures of metacognitive accuracy.
3. **Skip vs attempt** (`event_log(quiz_skip)`) — distinguishes avoidance/help-seeking
   from honest effort, a direct self-regulation behavior.

**The ground-truth spine:** MAI (Metacognitive Awareness Inventory) at Day 1 and Day 30,
plus pre/post C-test — lets you show that students who engaged the SRL features
(especially active calibrators) showed measurable **growth** in metacognition.

> **Key point:** SRL is no longer "in the backfoot." The calibration loop makes student
> self-regulation **load-bearing** — it changes the model — and every phase of the cycle
> produces analyzable data linked to MAI growth.

---

## One-Sentence Summary

The system collects a fully-linked, per-student longitudinal record spanning **what they
know** (multi-tier BKT trajectories), **what they think they know** (JOL + calibration),
**how they feel** (affect), and **how they self-regulate** (goals, path, skips,
misconceptions) — anchored to pre/post MAI + C-test ground truth — enabling proof of all
three contributions and a genuine SRL framing.
