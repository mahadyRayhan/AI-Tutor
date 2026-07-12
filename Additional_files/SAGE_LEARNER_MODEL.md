# SAGE — Unified Multidimensional Learner Model

This document describes SAGE's learner model **after** the Tier 1–3 additions that align it
to the Azevedo learner-model taxonomy. It covers the four dimensions, how each variable is
produced, the math behind the derived variables, and how the model is consumed.

Implementation: `backend/app/core/learner_model.py` — a **pure read model** exposed at
`GET /api/v1/learner-model/{username}`. It never writes learner state; it assembles a profile
by reading the BKT state and the telemetry logs.

---

## 1. Architecture

```
   ┌──────────────────────────────────────────────────────────────────────────┐
   │  STUDENT SIGNALS  (chat turns · quizzes · code · video · dashboard · survey)│
   └──────────────────────────────────┬───────────────────────────────────────┘
                                       │  logged by telemetry.py (study_id-linked)
              ┌────────────────────────┼────────────────────────┐
              ▼                        ▼                         ▼
      evidence/jol/quiz         turn/affect/session        calibration/self_report
        _log, bkt_history        path_event, video_*         behavior_log, event_log
              │                        │                         │
              └────────────────────────┴────────────┬────────────┘
                                                     │  derivations (Tier 1–3)
                                                     ▼
   ┌──────────────────────────────────────────────────────────────────────────┐
   │                    UNIFIED LEARNER MODEL  (learner_model.py)               │
   │                                                                            │
   │  COGNITIVE            METACOGNITIVE          AFFECTIVE        MOTIVATIONAL  │
   │  • concept mastery    • self-monitoring      • frustration    • self-effic.│
   │    (factored BKT)       (JOL + slider cal.)  • confusion (T3) • interest   │
   │  • misconceptions     • help-seeking (T1)    • boredom  (T3)  • goal orient│
   │  • cognitive load(T1) • persistence  (T1)    • flow      (T1) • persistence│
   │  • slip vs gap  (T1)  • pacing       (T1)    • emotional      • engagement │
   │  • automatization(T1) • path adherence         trajectory                  │
   │  • comprehension(T3)                                                       │
   │  • debugging    (T3)                                                       │
   └──────────────────────────────────┬───────────────────────────────────────┘
                                       │  get_learner_profile(username)
                                       ▼
   ┌──────────────────────────────────────────────────────────────────────────┐
   │  PEDAGOGICAL USE                                                           │
   │  • Mastery-Conditioned Response Adaptation (novice→reviewing)              │
   │  • Prerequisite gate + Socratic withholding                               │
   │  • Affect-aware intervention (frustration / confusion / boredom)          │
   │  • Instructor Risk Matrix (frustration × low mastery)                      │
   │  • Study analytics (per-contribution proof map)                           │
   └──────────────────────────────────────────────────────────────────────────┘
```

**Provenance legend:** *(no tag)* = already in the model · **(T1)** derived from existing
telemetry · **(T2)** motivational self-report survey · **(T3)** new light instrumentation.

---

## 2. Cognitive dimension

| Variable | How produced | Formula / rule |
|---|---|---|
| Concept mastery | factored multi-tier BKT | `P^(k)=θ_max^(k)·P̃^(k)`, composite `S=Σ_k θ_max^(k)P̃^(k)` |
| Misconceptions | EMA model (existing) | `M_c(t+1)=λM_c+(1−λ)·1[conf≥θ ∧ wrong]`, active if `M_c≥0.25` |
| **Cognitive load (T1)** | code complexity + latency + errors | `L = clip(0.4·ĉ + 0.3·ℓ̂ + 0.3·e)`, `ĉ=c_code/8`, `ℓ̂=lat/20s`, `e`=recent error rate |
| **Slip vs. gap (T1)** | JOL confidence × correctness | wrong ∧ conf≥4 ⇒ *gap*; wrong ∧ conf≤2 ⇒ *slip*; `gap_ratio=gaps/wrong` |
| **Automatization (T1)** | latency trend on repeat items | OLS slope of `time_to_answer` over trials; `<−0.05 s/trial` ⇒ automatizing |
| **Comprehension/tracing (T3)** | video checkpoint MCQ accuracy | `Σ correct / N` over `video_mcq_response` |
| **Debugging skill (T3)** | DEBUG-turn resolution | a DEBUG turn not followed by another DEBUG on-topic ⇒ resolved; rate = resolved/DEBUG |

## 3. Metacognitive dimension

| Variable | How produced | Formula / rule |
|---|---|---|
| **Self-monitoring (calibration)** | JOL + slider (existing + T1) | `jol_cal = 1 − mean_t (conf_t − x_t)²` (conf∈[0,1]); plus slider-adjustment count |
| **Help-seeking (T1)** | skips + timing | `skip_rate = skips/(skips+quizzes)`; surrender with `t<5s` ⇒ *impulsive* |
| **Persistence / grit (T1)** | retries vs give-ups | `give_up_rate = skips/(skips+solves)`; `grit = 1 − give_up_rate` |
| **Pacing (T1)** | latency vs mastery | *rushing* if `avg_latency<4s ∧ avg_mastery<0.4` |
| **Path adherence** | skill-tree deviation | `adherence = on_path/total` over `path_event` |

## 4. Affective dimension

| Variable | How produced | Formula / rule |
|---|---|---|
| Frustration + ΔF | profiler (existing) | RoBERTa + rage heuristics → `F_t`, `ΔF=F_t−F_{t−1}` |
| **Confusion (T3)** | profiler heuristics | keyword patterns / RoBERTa `surprise` → tally |
| **Boredom (T3)** | profiler heuristics | "too easy / already know / skip" patterns → tally |
| **Flow (T1)** | challenge–skill match | `flow = 1 − |challenge − skill|`, `challenge=c_code/8`, `skill=mastery/0.95` |
| Emotional trajectory | affect_log time-series | per-turn `ΔF` + `academic_emotion` |

The profiler now emits an `academic_emotion ∈ {frustration, confusion, boredom, flow,
neutral}` per turn, tallied in the profile and logged to `affect_log.academic_emotion`.

## 5. Motivational dimension  (Tier 2 — the newly filled layer)

| Variable | How produced | Source |
|---|---|---|
| **Self-efficacy (T2)** | Likert survey (se1–se3) | `self_report` table |
| **Interest / task value (T2)** | Likert survey (in1–in2) | `self_report` table |
| **Goal orientation (T2)** | Likert survey (go1–go2), high=mastery | `self_report` table |
| Persistence | derived (see §3) | telemetry |
| Engagement / time-on-task | session envelopes | `session_log` |

Survey items (Likert 1–5) and CSV import are documented in `scripts/import_study_data.py`
(`self_report` mode) and can also be submitted in-app via `POST /api/v1/self-report`.

---

## 6. The perception → inference → adaptation cycle

Following Azevedo's ITS cycle, each learner action drives:

1. **Observe** — the turn is logged (evidence, latency, affect, code, help/skip).
2. **Infer** — `get_learner_profile()` recomputes the four dimensions: BKT updates cognitive
   mastery; Tier-1 derivations recompute load/pacing/help-seeking/persistence/flow; the
   profiler updates affect; self-report supplies motivation.
3. **Adapt** — the pedagogical layer selects an action from the profile:

| Profile condition | Adaptation |
|---|---|
| low mastery ∧ high motivation | worked example → guided practice (Scaffolding) |
| high frustration ∧ repeated errors | targeted hint / reflective pause (affect gate) |
| high mastery ∧ low challenge (boredom / flow<0.7, challenge<skill) | advance / enrichment |
| poor self-monitoring (low `jol_cal`) | prediction prompts, confidence checks |
| impulsive help-seeking | withhold answer, prompt an attempt first |

---

## 7. Coverage vs. the Azevedo taxonomy (after Tier 1–3)

| Layer | Before | After |
|---|---|---|
| Cognitive | strong | **stronger** (+load, slip/gap, automatization, tracing, debugging) |
| Metacognitive | calibration only | **+help-seeking, persistence, pacing, path adherence** |
| Affective | frustration only | **+confusion, boredom, flow, emotion trajectory** |
| Motivational | ~absent | **self-efficacy, interest, goal orientation, engagement** |

Approximate variable coverage rises from **~38% → ~70%** of the document's taxonomy, with the
remaining gaps (syntax-knowledge latent, problem decomposition, testing skill, strategy-use
detection, and a full probabilistic metacognitive DBN) left as scoped future work.

---

## 8. What was added in code (this change)

| File | Change |
|---|---|
| `core/learner_model.py` | **new** — unified profile + all Tier-1/T3 derivations |
| `core/telemetry.py` | `log_self_report`, `academic_emotion` on `log_affect` |
| `agents/profiler.py` | confusion/boredom detection, `academic_emotion`, emotion tally |
| `agents/cot_rag_agent.py` | passes `academic_emotion` into `affect_log` |
| `db/sqlite_db.py` | `self_report` table, `affect_log.academic_emotion` column |
| `main.py` | `GET /api/v1/learner-model/{username}`, `POST /api/v1/self-report` |
| `scripts/import_study_data.py` | `self_report` import + survey items |

Everything is study_id-linked, so every learner-model dimension is also a longitudinal
research variable for the class trial.
