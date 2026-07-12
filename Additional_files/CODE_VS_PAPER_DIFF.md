# Code vs. Journal Difference Report

**Comparison:** current codebase vs. `Additional_files/SAGE_SRL_journal.pdf` (10 pages).

**Bottom line:** The journal, as written, is the **Contribution 1 paper**. It fully documents
the Factored Multi-Tier BKT + Evidence Diversity Guarantee and its supporting components.
Three large bodies of work now in the code are **absent** from the paper: Contribution 2
(Mastery-Conditioned Response Adaptation), Contribution 3 (SRL-BKT Calibration Loop), and
the entire Classroom-Video + Data-Collection infrastructure.

---

## 1. What the paper ALREADY documents (baseline — not a gap)

| Paper section | Component | In code? |
|---|---|---|
| §I–II | Evidence Diversity Problem/Guarantee | ✅ |
| §IV-B, Table II | Factored 3-tier BKT (quiz/micro/code), routing, correct-only transition | ✅ |
| §IV-C, Thm 2 | Conjunctive certification, n_min, hysteresis, false-cert bounds | ✅ |
| §IV-D, Eq 7–9 | GMM adaptive threshold calibration | ✅ (`threshold_calibrator.py`) |
| §IV-E, Eq 10 | Misconception EMA (λ=0.7, θ=0.25) | ✅ |
| §IV-E, Eq 11–12 | Forgetting decay + SM-2 coupling | ✅ |
| §IV-E | Teaching-style UCB1, affect, confidence (JOL) feeding SM-2 quality, goal alignment | ✅ |
| §III | IRL/SRL two-track architecture, security gate, perception layer | ✅ |
| §IV-F, Eq 13 | System utility metric J | ✅ (conceptual) |

**Supporting components are documented.** The gaps below are entirely new mechanisms and
subsystems.

---

## 2. NEW IN CODE — NOT IN THE PAPER

### A. Contribution 2 — Mastery-Conditioned Response Adaptation ❌ absent

The paper mentions "adapting how material is taught" and UCB1 style selection, but does **not**
document the discrete mastery-level classification that drives per-response adaptation.

| New in code | Where | Paper? |
|---|---|---|
| 4-level mastery classifier: novice / developing / proficient / reviewing | `cot_rag_agent.py::_classify_mastery_level` | ❌ |
| Entity→graph-concept resolution (exact → word-boundary → parent `INCLUDES`) | `cot_rag_agent.py` | ❌ |
| Prompt-level scaffolding adaptation per level | `socratic.py` (`_MASTERY_INSTRUCTIONS`) | ❌ |
| Response-format adaptation (2 vs 3 vs 5 sections by level) | `socratic.py` format builder | ❌ |
| Challenge-difficulty adaptation per level | `socratic.py` (`_MASTERY_CHALLENGES`) | ❌ |
| Gatekeeper + Socratic-withholding **bypass** for reviewing students | `cot_rag_agent.py` | ❌ |
| Uses **effective** (blended) mastery for classification | `cot_rag_agent.py` | ❌ |

**To add to paper:** a subsection defining the mastery→response mapping and where it sits in
the pedagogical model (§III-F / new methodology subsection). Frame honestly as standard-ITS
adaptive scaffolding that *consumes* the mastery signal (not a novel claim).

### B. Contribution 3 — SRL-BKT Calibration Loop ❌ fully absent

The paper lists exactly three student metacognitive signals (goal, confidence ratings,
prior-knowledge claims). The **downward-only self-assessment slider and per-user parameter
adaptation are not in the paper at all.** This is the biggest gap.

| New in code | Where | Paper? |
|---|---|---|
| Downward-only mastery self-assessment (dashboard sliders per tier) | `srl_calibration.py`, `student_dashboard.js` | ❌ |
| Layer 1 — score blending P_eff = α·P_BKT + (1−α)·P_self (α=0.6) | `bkt_model.py::get_effective_mastery` | ❌ |
| Layer 2 — per-user P_G adaptation after ≥3 consistent downward adjustments | `srl_calibration.py::_maybe_adapt_P_G` | ❌ |
| Direction EMA tracking + bounded P_G, downward-only guardrail | `srl_calibration.py` | ❌ |
| Certification always reads P_BKT (self-assessment never certifies) | design invariant | ❌ |
| `user_bkt_calibration` table + GET/POST mastery endpoints | `sqlite_db.py`, `main.py` | ❌ |
| Prediction-before-outcome logging (within-subject calibration accuracy) | `prediction_log`, `telemetry.py` | ❌ |

**To add to paper:** a full new methodology section (the "narrow novelty" — sustained
metacognitive disagreement adapts per-user emission parameters), positioned against Yudelson
(behavior-only) and Bull & Kay (display-only OLM), with the downward-only theorem-safety argument.

### C. Classroom Video System ❌ fully absent

The paper mentions only an "instructor classroom view" in the interface. The entire
interactive-video subsystem built this session is absent.

| New in code | Where | Paper? |
|---|---|---|
| Watch tracking: play/pause/seek/ended engagement events | `chat.js` classroom, `video_engagement` | ❌ |
| Coverage / completion % (genuine watch-time accumulation) | `video_coverage`, `telemetry.py` | ❌ |
| Attention proxy: tab-hidden-while-playing + mute detection | `chat.js`, `video_engagement` | ❌ |
| LLM-generated checkpoint MCQs from transcript segments | `video_service.py::generate_checkpoints` | ❌ |
| **Dynamic** checkpoint placement (N scales with length, one at end) | `video_service.py::_plan_checkpoint_times` | ❌ |
| Non-skippable enforcement (blocks on seek past a checkpoint) | `chat.js` `crEnforceCheckpoints` | ❌ |
| Checkpoint answers feed the BKT quiz tier | `main.py::answer_video_checkpoint` | ❌ |
| Pre-video intention + post-video reflection (SRL) | `chat.js`, `video_reflection` | ❌ |
| End-of-video "prelab" complex problems + "Solve in SAGE" guided handoff | `chat.js`, `prelab.json`, `main.py` | ❌ |
| Endpoints: `/checkpoints`, `/checkpoint/answer`, `/engagement`, `/coverage`, `/reflection`, `/prelab`, `/prelab-start` | `main.py` | ❌ |

**To add to paper:** either a dedicated "Classroom / video-based retrieval practice" section,
or fold the checkpoint-MCQ-as-evidence into the BKT evidence sources and the intention/
reflection into the SRL signals.

### D. Data Collection / Telemetry Infrastructure ❌ absent (eval is placeholder)

The paper's §V is "Placeholder for evaluation results." The full instrumentation for the
class study is new.

| New in code | Where | Paper? |
|---|---|---|
| Anonymized `study_id` linkage across all tables | `study_participants`, `telemetry.py` | ❌ |
| Append-only `event_log` (goal_set, quiz_skip, certification, withholding, gatekeeper, feedback, prelab_started, …) | `telemetry.py` | ❌ |
| Longitudinal tables: `bkt_history`, `evidence_log`, `prediction_log`, `turn_log`, `session_log`, `path_event`, `quiz_log`, `jol_log`, `calibration_log`, `response_log`, `behavior_log`, `affect_log`, `misconception_log` | `sqlite_db.py`, `telemetry.py` | ❌ |
| Per-turn metacognitive FSM state (Planning/Monitoring/Reflecting/Helplessness) capture | `turn_log` | ❌ |
| Ground-truth import (pre/post test + MAI), Bloom-tagged | `assessment`, `import_study_data.py` | ❌ |
| Backup + weekly integrity verification + pre-class dry-run | `scripts/backup_db.py`, `verify_telemetry.py`, `test_telemetry_dryrun.py` | ❌ |

**To add to paper:** the §V evaluation methodology (data collected, the class trial, the
proof-per-contribution mapping) — see `CLASS_DATA_COLLECTION_PLAN.md` and
`DATA_COLLECTION_INVENTORY.md`.

### E. Teacher Risk Matrix ❌ absent

| New in code | Where | Paper? |
|---|---|---|
| Frustration + low-mastery risk scoring/tiering (`affect_log` × mastery) | `main.py::get_risk_matrix` | ❌ |
| Teacher dashboard Risk Matrix view + push-challenge action | `teacher_dashboard.html` | ❌ |

### F. Minor code changes (not paper-level, noted for completeness)

- Intent-classifier off-topic fix (`fast_classifier.py`) — corrects OFF_TOPIC vs CONCEPT.
- Feature tests: `test_intent_classification.py`, `test_sentinel_off_topic.py`,
  `test_socratic_withholding.py` — validation, not paper content.
- Naming: "Agency-Transfer Mechanism (ATM)" → "Mastery-Conditioned Response Adaptation (MCRA)".

---

## 3. Priority for the paper

| Gap | Paper impact | Priority |
|---|---|---|
| **C3 — SRL-BKT Calibration Loop** | A load-bearing novel contribution, entirely missing | **Highest** |
| **§V Evaluation + telemetry** | The paper cannot be submitted with a placeholder eval | **Highest** |
| **C2 — Mastery-Conditioned Adaptation** | Shows the mastery signal is used; frame as standard ITS | Medium |
| **Classroom video / checkpoint evidence** | New evidence source + SRL retrieval practice | Medium |
| **Risk Matrix** | Supports the instructor-regulation + intervention story | Low |

**One-line summary:** the paper currently covers **Contribution 1 only**; the code now also
contains **Contribution 2, Contribution 3, the classroom-video subsystem, and the full
class-study telemetry** — none of which are yet written into the journal.
