# Learner Model & SRL — Code vs. Azevedo Reference Document

Comparison of the **current SAGE learner model + SRL features (code)** against
`Learner Model & Bayesian Knowledge Tracing Document for SAGE Project Azevedo 07082026.docx`.

The Azevedo document is an **aspirational taxonomy** — the full space of variables a rich
programming ITS *could* model (6 categories + a 4-layer BKT extension). It is not a spec of
what SAGE implements. This report maps each Azevedo variable to the actual code.

Legend: ✅ implemented · ⚠️ partial / proxy only · ❌ not modeled · ➕ **code exceeds the document**

---

## 1. Programming Knowledge Variables

| Azevedo variable | Code status | Where / notes |
|---|---|---|
| Concept mastery levels | ➕ **exceeds** | Not one `P(mastery)` per concept but a **factored 3-tier BKT** (quiz/micro/code) with **conjunctive certification** + Evidence Diversity Guarantee. The doc describes single-stream BKT; code is strictly richer. |
| Conceptual misconceptions | ✅ | EMA misconception tracker `M_c` (λ=0.7, θ=0.25), store/resolve, high-confidence-error trigger. |
| Prerequisite knowledge status | ✅ | Graph `REQUIRES_UNDERSTANDING_OF` gate on `ever_certified`. |
| Knowledge decay / retention | ✅ | Forgetting decay `P̃(t)=P̃₀e^(−λΔt)+…`, SM-2 coupling; per-tier half-lives 14/7/5 days. |
| Syntax knowledge | ❌ | No syntax-specific latent (e.g., "omits semicolons"). Folded into general correctness. |

## 2. Programming Skills Variables

| Azevedo variable | Code status | Where / notes |
|---|---|---|
| Code-writing proficiency | ✅ | Applied tier `P̃^(3)` via Code Reviewer evidence. |
| Code comprehension / tracing | ⚠️ | Checkpoint "predict-output" MCQs exist, but routed as generic quiz evidence — not a separate *tracing skill* variable. |
| Debugging skill | ⚠️ | DEBUG intent is handled pedagogically, but there is **no debugging-skill latent** (attempts/time-to-fix not modeled as a tracked ability). |
| Error pattern history | ⚠️ | Misconceptions capture *some* patterns; no systematic error-type taxonomy (off-by-one, runtime vs logic). |
| Problem decomposition ability | ❌ | Scaffolding decomposes *for* the student; their own decomposition skill is not measured. |
| Testing / validation skill | ❌ | Not tracked (no detection of edge-case testing behavior). |

## 3. Cognitive Process Variables

| Azevedo variable | Code status | Where / notes |
|---|---|---|
| Response latency patterns | ✅ | Dwell time (`behavior_log`), quiz time-to-answer (`quiz_log`), per-turn latency (`turn_log`). |
| Attention / engagement | ✅ | Video engagement + **tab-hidden-while-playing** + mute proxy + coverage; chat idle via `n_strike`, `session_log`. |
| Cognitive load estimate | ⚠️ | Code-complexity `c_code` + a two-tier `S_cog` overload rule (rage/DPT). Discrete gate, not a continuous load estimate. |
| Error type classification (slip vs gap) | ⚠️ | BKT slip `p_S` models slips; high-confidence error → misconception distinguishes gap from slip. Not a full cognitive-origin classifier. |

## 4. Affective Variables

| Azevedo variable | Code status | Where / notes |
|---|---|---|
| Frustration level | ✅ | `F_t`, ΔF trajectory, rage detection, `affect_log`. |
| Confidence / anxiety | ✅ | JOL confidence 1–5 before quizzes (`jol_log`). |
| Emotional trajectory | ✅ | Per-turn ΔF in `affect_log`; intervention-fired flag. |
| Boredom / flow | ❌ | No flow (challenge-skill balance) or boredom estimate. Only the frustration dimension. |
| Confusion / curiosity | ❌ | Not separately modeled (doc lists these; code has frustration only). |

## 5. Metacognitive Variables  *(Azevedo's core focus)*

| Azevedo variable | Code status | Where / notes |
|---|---|---|
| Self-monitoring accuracy | ➕ **exceeds** | Two calibration signals: JOL confidence-vs-actual (`jol_log`) **and** the **SRL-BKT Calibration Loop** — self-assessment vs BKT that *adapts the model* (`calibration_log`, `prediction_log`). The doc only asks to *track* calibration; code *acts on* it. |
| Self-regulation of pacing | ⚠️ | Per-turn **metacognitive FSM** `m_state ∈ {Planning, Monitoring, Reflecting, Helplessness}` + `path_event` deviation + `session_log`. Discrete-state proxy, not a probabilistic pacing model. |
| Reflection / review behavior | ⚠️ | Post-video reflection + pre-video intention captured; but no tracking of *reviewing feedback / past errors*. |
| Help-seeking behavior | ⚠️ | Hint requests + `quiz_skip` (surrender) logged; not modeled as *appropriateness* (immediate vs after genuine attempt). |
| Strategy use | ❌ | No detection of print-debugging vs trial-and-error vs diagram strategies. |

## 6. Motivational Variables  *(weakest coverage)*

| Azevedo variable | Code status | Where / notes |
|---|---|---|
| Engagement / time-on-task | ✅ | `session_log`, video coverage, active-time endpoint. |
| Goal orientation | ⚠️ | Goal *setting* captured (`goal_set`); mastery- vs performance-orientation **not classified**. |
| Persistence / grit | ⚠️ | Retry counts (MCQ attempts, challenge retries), give-up (`quiz_skip`) — signals exist but no persistence latent. |
| Self-efficacy | ❌ | Not modeled (no self-efficacy estimate or survey instrument wired in). |
| Interest / value perception | ❌ | Not modeled. |

---

## 7. The 4-Layer BKT Extension (doc §5–9) vs. code

| Layer | Doc's target | Code reality |
|---|---|---|
| **Cognitive** | Per-KC mastery, misconceptions, skill | ➕ **exceeds** — factored multi-tier + conjunctive guarantee + adaptive GMM thresholds |
| **Metacognitive** | Probabilistic latent Planning/Monitoring/Reflection via (D)BN | ⚠️ **discrete FSM only** (`m_state`) + calibration loop; no Bayesian network over latent metacognitive states |
| **Affective** | Frustration, confusion, boredom, curiosity, flow | ⚠️ **frustration only** (1 of 5 dimensions) |
| **Motivational** | Self-efficacy, interest, persistence, goal orientation | ❌ **largely absent** |

---

## 8. Where the CODE GOES BEYOND the document ➕

The document is a generic field taxonomy and mentions BKT *extensions* (Contextual BKT, DBN,
PFA, DKT) only by name. The code contributes specific mechanisms **not in the document**:

1. **Factored multi-tier BKT + conjunctive certification** with the *Evidence Diversity
   Guarantee* (Theorem 2) — a concrete, proven architecture, not a generic "extension."
2. **SRL-BKT Calibration Loop** — student self-assessment (downward-only) that adapts per-user
   emission parameters `p_G`. The doc treats self-monitoring as something to *measure*; the
   code turns it into a *control signal on the model itself*.
3. **Adaptive GMM certification thresholds** (per concept/tier).
4. **Checkpoint MCQs as declarative evidence** feeding BKT from video comprehension.
5. **Per-turn metacognitive FSM** as an online, logged label (rare in real datasets).

---

## 9. The GAPS (in the document, not in the code)

Ordered by how central they are to Azevedo's SRL/metacognition emphasis:

| Priority | Gap | Category |
|---|---|---|
| High | **Motivational layer** — self-efficacy, interest, goal orientation, persistence as latents | Motivational |
| High | **Probabilistic metacognitive model** — latent Planning/Monitoring/Reflection (DBN), beyond the discrete FSM | Metacognitive |
| Medium | **Affective breadth** — confusion, boredom, curiosity, flow (only frustration today) | Affective |
| Medium | **Strategy use & help-seeking appropriateness** | Metacognitive |
| Medium | **Programming-skill latents** — debugging skill, decomposition, testing, error-pattern history | Skills |
| Low | Syntax-knowledge latent; continuous cognitive-load estimate | Knowledge / Cognitive |

---

## 10. One-paragraph summary

SAGE's learner model is **deep on the cognitive axis and, on one metacognitive dimension
(calibration), actually ahead of the document** — it not only tracks self-monitoring accuracy
but feeds it back to adapt the model. It has solid **affective (frustration)** and
**engagement** coverage, now strengthened by the video-attention and behavioral telemetry. It
is **thin on the motivational layer** (self-efficacy, interest, goal orientation, persistence
are essentially unmodeled) and represents **metacognition as a discrete FSM + calibration
loop rather than the probabilistic latent network** Azevedo envisions. The largest, most
publishable gaps to close for an Azevedo-aligned model are (1) a motivational sub-model and
(2) a probabilistic metacognitive layer over the signals SAGE already logs
(`m_state`, `path_event`, `jol_log`, help/skip events).
