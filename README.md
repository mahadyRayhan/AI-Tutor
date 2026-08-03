# SAGE-SRL — An Adaptive Intelligent Tutoring System for C Programming

SAGE-SRL is a multi-agent Intelligent Tutoring System (ITS) that helps students master C
programming. It combines Large Language Models with Retrieval-Augmented Generation (RAG), a
reparameterized multi-tier Bayesian Knowledge Tracing (BKT) engine, and a novel
**human-in-the-loop calibration loop** that lets students negotiate their own mastery model —
making Self-Regulated Learning (SRL) load-bearing rather than incidental.

---

## Table of Contents

1. [Research Contributions](#1-research-contributions)
2. [System Architecture](#2-system-architecture)
3. [Code Components](#3-code-components)
4. [The Learner Model (Multi-Tier BKT)](#4-the-learner-model-multi-tier-bkt)
5. [SRL-BKT Calibration Loop](#5-srl-bkt-calibration-loop)
6. [Mastery-Conditioned Response Adaptation](#6-mastery-conditioned-response-adaptation)
7. [SRL Framing](#7-srl-framing)
8. [Data Collection & Telemetry](#8-data-collection--telemetry)
9. [Evaluation Plan](#9-evaluation-plan)
10. [Project Structure](#10-project-structure)
11. [Setup & Running](#11-setup--running)
12. [Deployment Guide](#12-deployment-guide)

---

## 1. Research Contributions

| # | Contribution | Status | Role |
|---|--------------|--------|------|
| **1** | **Factored Multi-Tier BKT + Conjunctive Certification** | Novel | Core; carries the paper |
| **2** | **Mastery-Conditioned Response Adaptation** | Standard ITS | Consumes the mastery signal (not claimed as novel) |
| **3** | **SRL-BKT Calibration Loop** | Novel (narrow) | Makes SRL load-bearing |

- **C1** — Three independent evidence tiers mapped to Bloom's taxonomy (quiz/micro/code),
  online BKT updates with mastery ceilings, and a **conjunctive certification rule** that
  requires diverse evidence, preventing the "illusion of competence."
- **C2** — The tutor adapts scaffolding, format, and challenge difficulty to the student's
  mastery level. This is established ITS practice (VanLehn's inner/outer loop); it
  *demonstrates the mastery signal is used*, not a novelty.
- **C3** — Students contest their mastery via a **downward-only slider**. Sustained
  metacognitive disagreement adapts the per-user BKT emission parameters (guess/slip),
  producing an increasingly accurate, individualized learner model. Novel vs. Yudelson
  (behavior-only personalized BKT) and Bull & Kay (display-only negotiated OLM).

---

## 2. System Architecture

```
                         ┌─────────────────────────────────────────┐
   Student / Teacher  →  │  FastAPI  (backend/app/main.py)          │
        (Web UI)         │  Streaming chat, dashboards, analytics   │
                         └──────────────────┬──────────────────────┘
                                            │
                         ┌──────────────────▼──────────────────────┐
                         │  ChainOfThoughtRAGAgent (orchestrator)   │
                         │  run_stream(): intent → sensory math →   │
                         │  mastery classify → gate → route         │
                         └──────────────────┬──────────────────────┘
             ┌───────────────┬──────────────┼──────────────┬───────────────┐
             ▼               ▼              ▼              ▼               ▼
        Sentinel        Examiner       Socratic       Reviewer       Scaffolding
        (safety)        (quizzes)      (tutoring)     (code)         (planning)
             │               │              │              │               │
             └───────────────┴──────────────┴──────────────┴───────────────┘
                                            │
        ┌───────────────────┬───────────────┼───────────────────┬──────────────┐
        ▼                   ▼               ▼                   ▼              ▼
   BKT Engine         SRL Calibration   Neo4j Graph        ChromaDB       SQLite
   (bkt_model.py)     (srl_calibration) (prereqs/Q&A)      (RAG docs)     (state+telemetry)
```

**Storage layers:**
- **SQLite** (`ai_tutor.db`) — users, sessions, messages, BKT state, and all telemetry.
- **Neo4j** — knowledge graph (concept prerequisites, `INCLUDES` hierarchy, Q&A pairs).
- **ChromaDB** — vector store for RAG over course materials.

---

## 3. Code Components

### Agents (`backend/app/agents/`)

| Agent | File | Role |
|-------|------|------|
| Orchestrator | `cot_rag_agent.py` | Runs the pipeline: intent → sensory math → mastery classification → gatekeeper → routing. Also hosts Socratic withholding and Mastery-Conditioned Adaptation. |
| 🛡️ Sentinel | `sentinel.py` | Safety filter: security risks, off-topic strikes, cognitive-overload, academic integrity, topic locks. |
| 🧐 Examiner | `examiner.py` | Quizzes with confidence (JOL) ratings; grades answers; drives BKT quiz-tier updates. |
| 🎓 Socratic Tutor | `socratic.py` | Standard RAG tutoring; builds mastery-adapted prompts & response formats. |
| 📝 Code Reviewer | `reviewer.py` | Reviews submitted C code; drives BKT code-tier updates. |
| 🏗️ Scaffolding | `scaffolding.py` | Forethought/planning support for problem solving. |
| 📊 Profiler | `profiler.py` | Sentiment/frustration analysis feeding the affective state. |

### Core modules (`backend/app/core/`)

| Module | Responsibility |
|--------|----------------|
| `bkt_model.py` | Reparameterized multi-tier BKT: update, decay, conjunctive certification, effective-mastery blending. |
| `srl_calibration.py` | SRL-BKT Calibration Loop: score blending + per-user P_G adaptation. |
| `telemetry.py` | Centralized, append-only, study_id-linked logging (all research data). |
| `user_knowledge_manager.py` | SM-2 spaced repetition, known-concepts, misconception EMA. |
| `threshold_calibrator.py` | GMM-based adaptive certification thresholds. |
| `fast_classifier.py` | Embedding + rule intent classification and entity extraction. |
| `history_manager.py` | Session/message state and interaction logging. |
| `assignment_manager.py` | Teacher-pushed challenges and submissions. |
| `settings_manager.py` | Teacher topic locks. |

### Data layer (`backend/app/db/`)

| Module | Responsibility |
|--------|----------------|
| `sqlite_db.py` | Schema + connection (state and all telemetry tables). |
| `graph_db.py` | Neo4j interface (prerequisites, Q&A). |
| `vector_store.py` | ChromaDB interface (RAG retrieval). |
| `llm_interface.py` | LLM abstraction (streaming, embeddings). |

---

## 4. The Learner Model (Multi-Tier BKT)

Each `(student, concept)` tracks three independent BKT posteriors mapped to Bloom's taxonomy:

| Tier | Evidence source | P_G | P_S | P_L0 | Ceiling |
|------|-----------------|-----|-----|------|---------|
| **quiz** (declarative) | Examiner quizzes | 0.20 | 0.10 | 0.30 | 0.60 |
| **micro** (procedural) | Micro-challenges | 0.10 | 0.15 | 0.05 | 0.25 |
| **code** (applied) | Code review | 0.05 | 0.20 | 0.01 | 0.10 |

- **Conjunctive certification:** ALL tiers ≥ `THETA_CERTIFY` (0.95) with ≥ `N_MIN` (3)
  evidence each. Decertify if any tier drops below `THETA_DECERTIFY` (0.75) — hysteresis.
- **Forgetting decay:** `P̃(t) = P̃(t₀)·e^(−λΔt) + P̃₀·(1−e^(−λΔt))`, coupled to SM-2 ease
  factor (successful reviews slow forgetting).
- **`ever_certified`** is never reset — the prerequisite gate uses it so decay can't
  re-lock earned prerequisites.
- **Misconceptions** are tracked with an EMA (`LAMBDA_M`), separate from the posterior.

---

## 5. SRL-BKT Calibration Loop

The novel mechanism: student metacognitive feedback tunes the model itself.

**Layer 1 — Score Blending (immediate):**
```
P_eff = α·P_BKT + (1−α)·P_self          (α = 0.6)
```
Drives display and response adaptation only — **never certification**.

**Layer 2 — Parameter Adaptation (sustained):**
After ≥ 3 consistent downward adjustments on a tier (tracked via a direction EMA), the
per-user **guess rate P_G** increases within bounds. Future correct answers on that tier
contribute less → the model "learns this student guesses well."

**Guardrails:**
- **Downward-only slider** — "I know less" is evidence; "trust me, I know it" is not.
  Preserves the certification theorem (no self-certification).
- **Certification always reads P_BKT** (with adapted parameters). The student influences
  the *model*, not the *gate*.
- Raising mastery happens **only through evidence** (ask to be quizzed).

Implemented in `srl_calibration.py`; dashboard sliders in `student_dashboard.js`; API
endpoints `GET /api/v1/mastery/{user}/{concept}` and `POST /api/v1/mastery/self-assess`.

---

## 6. Mastery-Conditioned Response Adaptation

The orchestrator classifies each request into **novice / developing / proficient /
reviewing** (using *effective* mastery), then adapts:

| Level | Scaffolding | Format | Challenge |
|-------|-------------|--------|-----------|
| Novice | Maximum, analogies, define every term | Full 5 sections | Guided micro-challenge |
| Developing | Moderate, build on basics | Full sections | Combine 2 concepts |
| Proficient | Light, nuances/edge cases | 3 sections | Advanced / find bugs |
| Reviewing | Concise refresher | 2 sections | Quick retention check |

Reviewing students (ever-certified) **bypass** the prerequisite gate and Socratic
withholding — they get a refresher, not "try first."

---

## 7. SRL Framing (Zimmerman's cycle)

| SRL Phase | Mechanism | Data stream |
|-----------|-----------|-------------|
| Forethought | Goal setting, skill-tree path | `event_log(goal_set)`, `path_event` |
| Performance | Quizzes, productive struggle, skips | `evidence_log`, `quiz_log`, `behavior_log`, `event_log(quiz_skip)` |
| Self-reflection | JOL confidence, dashboard self-assessment | `jol_log`, `calibration_log` |
| Emotional regulation | Frustration tracking + reframe | `affect_log` |
| Learning from error | Misconception form → resolve | `misconception_log` |

---

## 8. Data Collection & Telemetry

All logging is centralized in `telemetry.py`, append-only, and linked by an anonymized
`study_id`. **16 dedicated tables** plus 8 `event_log` sub-types. See
[`Additional_files/DATA_COLLECTION_INVENTORY.md`](Additional_files/DATA_COLLECTION_INVENTORY.md)
and [`Additional_files/CLASS_DATA_COLLECTION_PLAN.md`](Additional_files/CLASS_DATA_COLLECTION_PLAN.md)
for full detail.

Key tables: `evidence_log`, `bkt_history`, `prediction_log`, `calibration_log`, `jol_log`,
`response_log`, `behavior_log`, `affect_log`, `turn_log`, `path_event`, `quiz_log`,
`misconception_log`, `session_log`, `assessment`, `study_participants`, `event_log`.

**Operational scripts (`scripts/`):**
- `import_study_data.py` — roster + pre/post/MAI import (keyed by study_id).
- `backup_db.py` — daily consistent DB snapshot (retention).
- `verify_telemetry.py` — weekly integrity check (run from week 1).

---

## 9. Evaluation Plan

### A. Offline / scripted tests (`SRL-script/`)
| Test | Proves |
|------|--------|
| `test_intent_classification.py` | Intent routing accuracy |
| `test_sentinel_off_topic.py` | Off-topic strike + security blocking |
| `test_socratic_withholding.py` | "Try first" withholding logic |
| `test_telemetry_dryrun.py` | **Every logger writes + links** (pre-class safety) |
| `eval_1_1_mastery_adaptation.py` | Mastery-conditioned adaptation quality (LLM-judge) |

### B. Class trial (the primary study)
Proof mapping per contribution:

- **C1 (Multi-Tier BKT):** per-tier learning curves (`bkt_history`) vs Bloom-tagged post-
  test (`assessment`); "illusion of competence" (high-quiz/low-code → poor exam); offline
  flat-BKT baseline via raw `evidence_log`.
- **C2 (Response Adaptation):** productive-struggle metrics (`behavior_log`) shift as BKT
  classification evolves (`response_log`).
- **C3 (Calibration Loop):** within-subject accuracy — does `P_eff` beat `P_BKT` predicting
  next outcome after calibration (`prediction_log`, Brier/log-loss)? Plus MAI **growth**
  (Day 1 → Day 30) with Day-1 baseline as covariate to control self-selection.

### C. Classroom protocol dependencies (mandatory for data)
1. Mandate goal-setting in week 1.
2. Weekly 5-minute **Dashboard Reflection Days** (generates calibration data — without it,
   Contribution 3 has no active-calibrator group).
3. Pop-quizzes count toward participation (drives JOL + evidence).
4. Lock curriculum week-by-week (bypass-attempt data).
5. Push challenges to at-risk students via the **Risk Matrix** (teacher dashboard).
6. **Pre/post C-test + MAI survey** on Day 1 and Day 30 (all ground truth).

---

## 10. Project Structure

```
AI-Tutor/
├── backend/
│   └── app/
│       ├── main.py                 # FastAPI app: endpoints, streaming, dashboards
│       ├── agents/                 # Multi-agent pipeline
│       │   ├── cot_rag_agent.py    #   Orchestrator
│       │   ├── sentinel.py         #   Safety filter
│       │   ├── examiner.py         #   Quizzes + JOL
│       │   ├── socratic.py         #   Tutoring + adaptation
│       │   ├── reviewer.py         #   Code review
│       │   ├── scaffolding.py      #   Planning
│       │   └── profiler.py         #   Affect
│       ├── core/                   # BKT, calibration, telemetry, managers
│       ├── db/                     # SQLite / Neo4j / Chroma / LLM interfaces
│       ├── static/ + templates/    # Web UI (chat, dashboards)
│       └── database/               # ai_tutor.db, chroma_db
├── scripts/                        # ingest, import, backup, verify, benchmarks
├── SRL-script/                     # feature tests + eval scripts
├── Additional_files/               # design docs, data plans, paper material
├── resources/                      # course materials + knowledge graph
├── requirements.txt
└── docker-compose.yml
```

---

## 11. Setup & Running

### Prerequisites
- Python 3.11+, Neo4j, and the Python deps in `requirements.txt`.
- A `.env` with LLM API keys and Neo4j credentials.

### Install
```bash
pip install -r requirements.txt
```

### Ingest knowledge base
```bash
python scripts/ingest_data.py          # builds ChromaDB vector store
python scripts/ingest_manual_graph.py  # builds Neo4j knowledge graph
```

### Run the backend
```bash
cd backend
uvicorn app.main:app --reload --port 8000
```

### Pre-class data checklist
```bash
python scripts/import_study_data.py roster roster.csv      # register students
cd backend && python ../SRL-script/test_telemetry_dryrun.py # verify loggers (must be green)
# schedule: daily  → scripts/backup_db.py
#           weekly  → scripts/verify_telemetry.py
```

---

## 12. Deployment Guide

Deploying updates to the AWS production server.

**Prerequisites:** SSH key `~/.ssh/CERI-AWS.pem`, host `18.225.209.224`, user `ubuntu`.

### 1. Local — bundle source (excludes venv, git, local vector DB)
```bash
zip -r tutor_v2.zip . -x "**/__pycache__/*" "**/venv/*" ".git/*" "database/chroma_db/*"
scp -i ~/.ssh/CERI-AWS.pem tutor_v2.zip ubuntu@18.225.209.224:~/
```

### 2. Remote — deploy
```bash
ssh -i ~/.ssh/CERI-AWS.pem ubuntu@18.225.209.224
cd ~/AI-Tutor && sudo docker-compose down
cd ~ && mv AI-Tutor AI-Tutor_backup_$(date +%F_%H-%M)      # backup for rollback
mkdir ~/AI-Tutor && unzip -o tutor_v2.zip -d ~/AI-Tutor
cd ~/AI-Tutor && sudo chmod -R 777 backend/database/ logs/ resources/
sudo docker-compose up --build -d
sudo docker-compose logs -f backend
```

**Troubleshooting:**
- **Rollback:** stop containers, delete failed `AI-Tutor`, rename latest `AI-Tutor_backup_...` back.
- **Disk space:** `sudo docker system prune -f`.
- **Vector DB:** `database/chroma_db/` is excluded from the zip; persist via Docker volume or re-ingest.

---

## Related Documents

- [`Additional_files/DATA_COLLECTION_INVENTORY.md`](Additional_files/DATA_COLLECTION_INVENTORY.md) — full data inventory + learner-model/BKT/SRL synthesis
- [`Additional_files/CLASS_DATA_COLLECTION_PLAN.md`](Additional_files/CLASS_DATA_COLLECTION_PLAN.md) — one-shot data-collection plan + safeguards
- [`Additional_files/srl-bkt-calibration-loop.md`](Additional_files/srl-bkt-calibration-loop.md) — calibration-loop design
