# Class Trial Data Collection Plan (One-Shot)

**Goal:** Instrument SAGE so that a single semester-long class trial produces a dataset
sufficient to prove all three core contributions *and* the SRL framing — with no
recoverable-data gaps and no silent logging failures.

**Guiding principle:** With one chance, the failure mode that kills the paper is not
"we forgot a nice-to-have metric" — it is **(a) a logger that silently stops writing,
(b) overwritten state we needed as a time-series, or (c) logs we cannot link back to a
student's pre/post test.** This plan is built around eliminating those three.

---

## 0. Non-Negotiable Foundations (build these FIRST)

These five are the difference between "we have a dataset" and "we lost the semester."

### 0.1 Stable anonymized Study ID (the #1 most-forgotten thing)
Every log row, every test, every survey must carry the **same** `study_id`.
- Create a `study_participants` table mapping `username` → `study_id` (random, e.g. `S-0473`).
- Keep the real-name ↔ study_id mapping in a **separate, access-controlled file** (IRB).
- Pre-test, post-test, and MAI surveys are keyed by `study_id`, never by name.
- **Without this linkage, no within-subject analysis is possible** — you cannot connect
  "this student calibrated 4 times" to "this student's post-test went up."

### 0.2 Append-only raw event log (the safety net)
One table, `event_log`, that captures **every** meaningful event as an immutable row:
`(seq INTEGER PK AUTOINCREMENT, study_id, session_id, event_type, payload JSON, ts_utc)`.
- Never UPDATE, never DELETE. Monotonic `seq` gives global ordering.
- Even if a specialized table has a bug, the raw stream lets you reconstruct the analysis.
- This is your insurance policy. Write to it from a single helper called everywhere.

### 0.3 Raw evidence stream (enables offline re-simulation of ANY model)
Log every BKT evidence item **before** it is consumed:
`(study_id, concept, tier, is_correct, question_id, ts_utc)`.
- This lets you re-run a **flat/single-tier BKT offline** for the Contribution-1 baseline
  comparison — without it, you cannot answer the reviewer's "what if you used standard BKT?"
- It also future-proofs against any parameter change mid-semester.

### 0.4 Daily off-machine DB backup
A single corrupted SQLite file = total loss. Automated daily snapshot copied off the host
(cron + timestamped copy to cloud/OneDrive). Keep ≥14 daily backups.

### 0.5 Weekly data-integrity verification script
Run every week from week 1. It must assert: each table is growing, every active student
has rows in every expected table, no NULLs in key columns, every log row has a valid
`study_id`. **Catch a broken logger in week 1, not week 12.**

---

## 1. Data Infrastructure — New Tables

Existing tables we keep: `user_knowledge` (latest BKT state), `user_bkt_calibration`
(slider state), `user_goals`, `sessions`, `messages`, `users.learning_profile` (JSON).

New tables to add:

| Table | Purpose | Key columns |
|---|---|---|
| `study_participants` | Anonymized linkage | study_id, username, cohort, enrolled_at |
| `event_log` | Append-only raw stream (0.2) | seq, study_id, session_id, event_type, payload, ts_utc |
| `evidence_log` | Raw BKT evidence (0.3) | study_id, concept, tier, is_correct, question_id, ts_utc |
| `bkt_history` | Per-tier posterior **trajectory** | study_id, concept, tier, p_tilde, n_evidence, is_certified, ever_certified, trigger, ts_utc |
| `prediction_log` | Model-prediction-before-outcome | study_id, concept, tier, p_bkt_pred, p_eff_pred, then is_correct, ts_utc |
| `calibration_log` | Every slider move + P_G adaptation | study_id, concept, tier, p_bkt_at_time, p_self, delta, direction_ema, adapted_pg_old, adapted_pg_new, ts_utc |
| `jol_log` | Judgment-of-Learning calibration | study_id, concept, confidence_1_5, actual_score, quiz_id, ts_utc |
| `response_log` | Every AI turn + adaptation used | study_id, session_id, concept, intent, mastery_level, format_sections, ts_utc |
| `behavior_log` | Productive-struggle telemetry | study_id, session_id, event (copy_code/dwell/hint/skip), value, message_id, ts_utc |
| `affect_log` | Frustration trajectory | study_id, session_id, delta_f, frustration_level, intervention_fired, ts_utc |
| `assessment` | Pre/post test + MAI ground truth | study_id, instrument (pretest/posttest/mai_pre/mai_post), item_id, bloom_tier, concept, score, ts_utc |

> Design rule: history tables are **append-only snapshots**, not updates. `user_knowledge`
> stays as the "current state" cache; `bkt_history` is the time-series you actually analyze.

---

## 2. What Each Contribution Needs (proof → data → table)

### Contribution 1 — Factored Multi-Tier BKT + Conjunctive Certification

**Claim:** 3 independent tiers + conjunctive certification predicts true learning better
than a flat BKT, and prevents the "illusion of competence."

| Proof element | Data required | Source table |
|---|---|---|
| Per-tier learning curves | P̃ trajectory per (student, concept, tier) | `bkt_history` |
| Conjunctive vs single-tier-high | certification flags + per-tier finals | `bkt_history`, `user_knowledge` |
| Flat-BKT baseline comparison | raw evidence to re-simulate offline | `evidence_log` |
| "Illusion of competence" (high quiz / low code → bad exam) | per-tier P × Bloom-tagged exam items | `bkt_history` × `assessment` |
| Forgetting-decay claim | inter-session gaps + decayed-P before/after | `event_log` (session times) × `bkt_history` |
| Ground truth | pre/post test, items tagged by Bloom tier **and** concept | `assessment` |

**Base-rate risk:** few students may fully conjunctively-certify in one term. **Mitigation:**
make the *partial-pattern* analysis (high-quiz/low-code → lower exam) the **primary**
result; treat full certification as secondary. The raw `evidence_log` + `bkt_history`
guarantee you can run this regardless of how many certify.

### Contribution 2 — Mastery-Conditioned Response Adaptation

**Claim:** adapting responses on the multi-tier, SRL-calibrated signal improves scaffolding
effectiveness and enforces productive struggle.

| Proof element | Data required | Source table |
|---|---|---|
| Adaptation actually happened | mastery_level + sections used per AI turn | `response_log` |
| Productive struggle ↑ | copy-code clicks, dwell/latency, hint use, skips over time | `behavior_log` |
| Behavior shifts with mastery transition | novice→developing→proficient timing × behavior | `bkt_history` × `behavior_log` |
| Fewer evasions over time | off-topic strikes, bypass attempts trend | `event_log` |

**Telemetry gap to close (currently NOT logged):**
- **Copy-Code clicks** — button exists in `chat.js` but the click only copies; add a POST.
- **Dwell time / response latency** — time from AI message render → student's next keystroke/send.
- **Hint requests / micro-challenge skip vs attempt** — partially in `skipped_challenges`; make it an event.

### Contribution 3 — SRL-BKT Calibration Loop

**Claim:** downward-only slider tuning of P_G yields a more accurate individualized model
than behavioral data alone.

| Proof element | Data required | Source table |
|---|---|---|
| Calibration activity | every slider move, direction, P_G adaptation | `calibration_log` |
| **Accuracy gain (within-subject, dodges self-selection)** | predicted P_BKT vs P_eff logged **before** each later outcome, then outcome | `prediction_log` |
| Group A vs B (active vs passive) | ≥3 downward-adjusters vs non-adjusters | `calibration_log` |
| MAI growth, not absolute | MAI day1 + day30, ANCOVA on day-1 baseline | `assessment` |
| Metacognitive baseline | JOL confidence vs actual score | `jol_log` |

**The methodological fix you MUST bake in now:** the Group-A-vs-B comparison has a
**self-selection confound** (calibrators are already more conscientious). Two defenses,
both requiring data captured *now*:
1. **Within-subject prediction accuracy** (`prediction_log`): for the *same* student, does
   P_eff predict their next quiz outcome better than P_BKT (lower Brier/log-loss) after they
   calibrate? This is causal-flavored and immune to self-selection.
2. **ANCOVA with Day-1 MAI as covariate** and report **growth** (Δ MAI), plus honest N for
   Group A. If Action 2 (Reflection Days) doesn't run, Group A may be too small — so Action 2
   is mandatory, not optional.

---

## 3. SRL Framing Data (cross-cutting, Zimmerman 3-phase)

| SRL phase | Signal | Table | Status |
|---|---|---|---|
| Forethought | goal-set event + text; path adherence (asked concept vs skill-tree position) | `event_log`, `user_goals` | goal ✓ / adherence ❌ add |
| Performance | quiz attempts, skipped/cleared challenges, evidence stream | `evidence_log`, `behavior_log` | partial |
| Self-reflection | JOL confidence; slider usage | `jol_log`, `calibration_log` | JOL ✓ / slider ✓ |
| Emotional regulation | ΔF trajectory, rage events, recovery after empathetic turn | `affect_log` | computed live, **not persisted** — add |

**Recovery metric (nice, cheap, persuasive):** after an empathetic/cognitive-reframe
response fires, did the student's ΔF drop on the next turn? Needs `affect_log` with the
`intervention_fired` flag — captured per turn.

---

## 4. Ground Truth & Surveys (Action 6 — the spine of every claim)

- **Pre-test (Day 1)** and **Post-test (Day 30)**: parallel forms, every item tagged with
  `bloom_tier ∈ {declarative, procedural, applied}` **and** `concept`. The Bloom tags are
  what let you align exam items to the 3 BKT tiers (Contribution 1).
- **MAI (Metacognitive Awareness Inventory)** Day 1 and Day 30 — baseline + growth.
- All keyed by `study_id`. **If the test/survey isn't linkable to logs, the within-subject
  analyses are impossible.** This is the single most important non-software task.

---

## 5. Classroom Protocol Dependencies (data won't exist without these)

These Actions from the deck are **data prerequisites**, not just pedagogy:

| Action | Generates | If skipped… |
|---|---|---|
| 1. Mandate goal-setting wk1 | Forethought baseline | no path-deviation data |
| 2. Weekly Reflection Days | slider/calibration data | **Contribution 3 has no Group A** |
| 3. Pop-quizzes = participation grade | JOL + evidence stream | sparse BKT evidence |
| 4. Lock curriculum weekly | bypass-attempt data | no gating/evasion signal |
| 5. Push challenges to at-risk | code-tier evidence + intervention effect | thin code-tier, weak Contribution 1 |
| 6. Pre/post test + MAI | **all ground truth** | **no claim is provable** |

Action 2 and Action 6 are load-bearing. Treat them as mandatory.

---

## 6. Implementation Task List (prioritized)

**Tier A — must exist before day 1 (data is unrecoverable otherwise):**
1. `study_participants` + Study-ID injection into every log call
2. `event_log` append-only helper (single `log_event(study_id, type, payload)` used everywhere)
3. `evidence_log` (hook into `bkt.update`)
4. `bkt_history` snapshot (hook into `bkt.update`, after-state)
5. `prediction_log` (log P_BKT & P_eff at evidence time, before grading the outcome)
6. `calibration_log` (hook into `record_self_assessment`)
7. `assessment` import path for pre/post/MAI
8. Daily backup cron + weekly verification script

**Tier B — needed for Contribution 2 and richer SRL:**
9. `behavior_log` + frontend hooks: copy-code click, dwell/latency, hint, skip
10. `response_log` (hook into socratic/orchestrator: mastery_level + sections)
11. `affect_log` (persist ΔF + intervention_fired per turn)
12. `jol_log` (hook into examiner confidence flow)

**Tier C — strengthens, not blocking:**
13. Path-adherence flag (asked concept vs skill-tree position)
14. Session metadata (start/end/duration/device)

---

## 7. Pre-Class Verification Checklist (dry run with 2–3 fake students)

Before real students arrive, run a scripted end-to-end session and confirm **every** table
gets a row:

- [ ] Fake student gets a `study_id`; it appears in every log table
- [ ] Ask a concept → `response_log` row with mastery_level + sections
- [ ] Answer a quiz → `evidence_log`, `bkt_history`, `prediction_log`, `jol_log` rows
- [ ] Move a dashboard slider → `calibration_log` row; 3× → P_G adaptation logged
- [ ] Copy a code block → `behavior_log` row; pause before typing → dwell captured
- [ ] Trigger frustration → `affect_log` row with intervention flag
- [ ] Import a fake pre-test → `assessment` rows linked by `study_id`
- [ ] `event_log.seq` is monotonic and gap-free
- [ ] Daily backup file appears; weekly verifier reports all-green
- [ ] **Export script** produces a single linked CSV/parquet per student across all tables

If any box fails in the dry run, it would have failed silently for 12 weeks.

---

## 8. Top Risks → Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Silent logger failure | Lose weeks of data | Weekly verifier from wk 1; `event_log` safety net |
| Overwritten BKT state | No trajectories (kills C1) | `bkt_history` append-only snapshots |
| Can't link logs ↔ tests | No within-subject (kills C3) | `study_id` everywhere, enforced |
| Self-selection in C3 | Reviewer rejects | `prediction_log` within-subject + Day-1 MAI ANCOVA |
| Few full certifications | Weak C1 | Partial-pattern as primary; raw `evidence_log` |
| Model/params changed mid-term | Inconsistent series | `evidence_log` re-simulation + version stamp on events |
| DB corruption | Total loss | Daily off-machine backups |
| Copy-code/dwell not built | C2 unprovable | Tier-B frontend hooks before launch |

---

## 9. One-Line Summary per Contribution

- **C1 (Multi-Tier BKT):** `evidence_log` + `bkt_history` + Bloom-tagged `assessment` →
  per-tier curves & illusion-of-competence, with offline flat-BKT baseline.
- **C2 (Response Adaptation):** `response_log` + `behavior_log` → productive-struggle metrics
  shift as BKT classification evolves.
- **C3 (Calibration Loop):** `calibration_log` + `prediction_log` + MAI growth →
  within-subject accuracy gain that survives the self-selection critique.
