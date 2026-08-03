# SRL-BKT Calibration Loop — Design Document

## Problem Statement

The current SAGE-SRL system treats BKT as a one-way measurement tool: the system observes student behavior, computes mastery, and makes decisions. The student has no agency over how their mastery is assessed. This violates the core SRL principle (Zimmerman, 2002) that learners should be able to **monitor, evaluate, and adjust** their own learning.

Two specific gaps:

1. **No learner control over assessment** — A student who knows they guessed correctly on a quiz cannot tell the system "I don't actually know this." BKT credits the correct answer equally regardless.

2. **Fixed BKT parameters** — P_G=0.20 for quiz is identical for all students. A student who consistently guesses well on multiple-choice questions has the same guess parameter as one who never guesses. The model treats all students identically.

---

## Two-Layer Solution

### Layer 1: Score Blending (Immediate Effect)

When a student adjusts their mastery on the dashboard:

```
P_effective(k) = alpha * P_BKT(k) + (1-alpha) * P_self(k)
```

Where:
- k = tier (quiz, micro, code)
- P_BKT(k) = BKT-computed mastery after decay
- P_self(k) = student's self-assessed mastery (0.0-1.0 from dashboard slider)
- alpha = 0.6 (trust BKT 60%, student 40%)

**Effect:** Immediately shifts the system's understanding. Used for ATM classification, certification decisions, and prerequisite gate evaluation.

**Why alpha = 0.6:** Metacognitive calibration research (Dunning-Kruger, 1999; Winne, 1996) shows students systematically over- or under-estimate their knowledge. Trusting BKT slightly more (60%) provides a safety net while still giving the student meaningful control. A sensitivity analysis across alpha in {0.5, 0.6, 0.7, 0.8} validates robustness.

### Layer 2: Parameter Adaptation (Long-Term Model Change)

When the student **consistently** adjusts a specific tier in the same direction (3+ times), the BKT parameters for that user+tier adapt:

```
Student keeps lowering quiz mastery (3+ adjustments, same direction):
  -> System infers: student guesses well on quizzes
  -> Increase P_G_quiz for this user (e.g., 0.20 -> 0.25)
  -> Future correct quiz answers contribute LESS to mastery
  -> Student needs stronger quiz evidence to certify

Student keeps raising code mastery (3+ adjustments, same direction):
  -> System infers: student's code skills are better than measured
  -> Decrease P_S_code for this user (e.g., 0.20 -> 0.15)
  -> Future correct code answers contribute MORE to mastery
```

**Why N_trigger = 3:** Consistent with the system's N_MIN=3 evidence threshold for certification and the P_G^3 bound from the 3-question verification flow. Baker et al. (2008) use similar small observation windows for contextual BKT parameter estimation. Three is the minimum sample to distinguish a pattern from noise.

---

## Existing System Components to Leverage

### Already Implemented:
- **Confidence JOLs** (`examiner.py`): Student rates confidence 1-5 before quiz questions. High-confidence errors trigger misconception detection. This is per-question SRL — the dashboard extends it to per-concept SRL.
- **SM-2/BKT coupling** (`bkt_model.py:204-211`): ease_factor already modifies decay rate lambda. This proves per-user BKT parameter adaptation is architecturally feasible.
- **Threshold Calibrator** (`threshold_calibrator.py`): GMM-based adaptive certification thresholds. The calibration infrastructure (buffer table, recalibration logic) can be reused.
- **Open Learner Model**: Analytics endpoints already expose mastery data. The dashboard extension adds interactivity.

### New Components Needed:
1. Database table: `user_bkt_calibration`
2. API endpoints: GET mastery detail + POST self-assessment
3. BKT model: per-user parameter lookup + blending logic
4. ATM integration: use effective mastery instead of raw BKT
5. Frontend: slider UI on mastery dashboard (existing page)

---

## Database Schema

### New Table: `user_bkt_calibration`

```sql
CREATE TABLE IF NOT EXISTS user_bkt_calibration (
    username        TEXT    NOT NULL,
    concept         TEXT    NOT NULL,
    tier            TEXT    NOT NULL,  -- 'quiz', 'micro', 'code'

    -- Student's self-assessment (Layer 1)
    self_assessment  REAL   DEFAULT NULL,  -- P_self (0.0-1.0), NULL = no assessment
    last_adjusted_at TIMESTAMP DEFAULT NULL,

    -- Calibration tracking (Layer 2)
    n_adjustments    INTEGER DEFAULT 0,    -- total adjustment count for this tier
    direction_ema    REAL    DEFAULT 0.0,  -- running EMA of (P_self - P_BKT), range [-1, +1]

    -- Adapted BKT parameters (NULL = use global defaults)
    adapted_P_G      REAL   DEFAULT NULL,  -- personalized guess probability
    adapted_P_S      REAL   DEFAULT NULL,  -- personalized slip probability

    -- Metadata
    last_adapted_at  TIMESTAMP DEFAULT NULL,  -- when parameters were last adapted

    PRIMARY KEY (username, concept, tier)
);
```

**Size:** At most 21 concepts x 3 tiers x N_users = 63 rows per user. Negligible.

---

## Parameter Adaptation Math

### Direction EMA Update

On each dashboard adjustment:

```python
LAMBDA_CAL = 0.4  # EMA smoothing (same family as misconception LAMBDA_M=0.7)

# Current adjustment direction
delta = P_self_new - P_BKT_current  # positive = student thinks higher

# Normalize to [-1, +1]
direction = max(-1.0, min(1.0, delta / max(abs(delta), 0.01)))

# Update EMA
direction_ema = LAMBDA_CAL * direction + (1 - LAMBDA_CAL) * direction_ema_prev
```

### Parameter Adaptation Trigger

After each adjustment, check if adaptation should fire:

```python
N_TRIGGER = 3           # minimum adjustments before adapting
DIRECTION_THRESHOLD = 0.3  # minimum consistent direction signal
BETA = 0.10             # adaptation step size

if n_adjustments >= N_TRIGGER and abs(direction_ema) > DIRECTION_THRESHOLD:

    if direction_ema < -DIRECTION_THRESHOLD:
        # Student consistently says "I know LESS than BKT thinks"
        # -> Student guesses well -> Increase P_G
        P_G_new = min(P_G_current + BETA * abs(direction_ema), P_G_MAX[tier])

    elif direction_ema > DIRECTION_THRESHOLD:
        # Student consistently says "I know MORE than BKT thinks"
        # -> Student slips less -> Decrease P_S
        P_S_new = max(P_S_current - BETA * abs(direction_ema), P_S_MIN[tier])
```

### Parameter Bounds (Safety Rails)

| Parameter | Tier | Default | Min | Max |
|-----------|------|---------|-----|-----|
| P_G | quiz | 0.20 | 0.05 | 0.40 |
| P_G | micro | 0.10 | 0.02 | 0.25 |
| P_G | code | 0.05 | 0.01 | 0.15 |
| P_S | quiz | 0.10 | 0.02 | 0.25 |
| P_S | micro | 0.15 | 0.05 | 0.30 |
| P_S | code | 0.20 | 0.05 | 0.35 |

Bounds prevent degenerate behavior (e.g., P_G=0.5 would make quiz evidence meaningless).

---

## Implementation Changes

### File 1: `backend/app/db/sqlite_db.py`

Add `CREATE TABLE IF NOT EXISTS user_bkt_calibration` to the schema initialization block. Add index on `(username, concept)`.

### File 2: `backend/app/core/bkt_model.py`

#### Change A: `_get_user_params(username, concept, tier)` — New helper function

```python
def _get_user_params(username: str, concept: str, tier: str) -> dict:
    """Return BKT parameters for this user+concept+tier.
    Uses adapted values if they exist, otherwise global defaults."""
    row = db.fetch_one(
        "SELECT adapted_P_G, adapted_P_S FROM user_bkt_calibration "
        "WHERE username=? AND concept=? AND tier=?",
        (username, concept, tier)
    )
    cfg = dict(EVIDENCE_CONFIG[tier])  # copy defaults
    if row:
        if row["adapted_P_G"] is not None:
            cfg["P_G"] = row["adapted_P_G"]
        if row["adapted_P_S"] is not None:
            cfg["P_S"] = row["adapted_P_S"]
    return cfg
```

#### Change B: `update()` — Use per-user parameters

In the `update()` function, replace:
```python
cfg = EVIDENCE_CONFIG[evidence_type]
```
With:
```python
cfg = _get_user_params(username, concept, evidence_type)
```

This is a 1-line change that makes the entire BKT pipeline per-user aware.

#### Change C: `get_effective_mastery()` — New function (Layer 1 blending)

```python
ALPHA = 0.6  # BKT trust weight

def get_effective_mastery(username: str, concept: str) -> dict:
    """Return blended mastery combining BKT posterior with student self-assessment.
    Returns dict with per-tier effective P values and composite score."""
    row = _read_row(username, concept)
    if not row:
        return {"quiz": 0.0, "micro": 0.0, "code": 0.0, "composite": 0.0, "source": "no_data"}

    result = {}
    for tier in ["quiz", "micro", "code"]:
        p_bkt = _apply_decay(
            row[EVIDENCE_CONFIG[tier]["col"]] or EVIDENCE_CONFIG[tier]["P_L0"],
            tier, _parse_ts(row[_TS_COL[tier]]), lam=row[_LAM_COL[tier]]
        )
        # Check for self-assessment
        cal_row = db.fetch_one(
            "SELECT self_assessment FROM user_bkt_calibration "
            "WHERE username=? AND concept=? AND tier=?",
            (username, concept, tier)
        )
        if cal_row and cal_row["self_assessment"] is not None:
            p_self = cal_row["self_assessment"]
            p_eff = ALPHA * p_bkt + (1 - ALPHA) * p_self
        else:
            p_eff = p_bkt

        result[tier] = round(p_eff, 6)

    result["composite"] = round(
        result["quiz"] * 0.60 + result["micro"] * 0.25 + result["code"] * 0.10, 4
    )
    result["source"] = "blended"
    return result
```

#### Change D: Add to `bkt` namespace

```python
bkt = type("BKTModel", (), {
    "update":                staticmethod(update),
    "get_mastery":           staticmethod(get_mastery),
    "get_effective_mastery": staticmethod(get_effective_mastery),  # NEW
    "is_mastered":           staticmethod(is_mastered),
})()
```

### File 3: `backend/app/core/srl_calibration.py` — New file

Core calibration logic — keeps bkt_model.py clean.

```python
"""
SRL-BKT Calibration Loop
Processes student self-assessment from dashboard and adapts BKT parameters.
"""

ALPHA = 0.6             # BKT trust weight (Layer 1)
LAMBDA_CAL = 0.4        # EMA smoothing for direction tracking
N_TRIGGER = 3           # minimum adjustments before parameter adaptation
DIRECTION_THRESHOLD = 0.3
BETA = 0.10             # adaptation step size

P_G_BOUNDS = {
    "quiz":  (0.05, 0.40),
    "micro": (0.02, 0.25),
    "code":  (0.01, 0.15),
}
P_S_BOUNDS = {
    "quiz":  (0.02, 0.25),
    "micro": (0.05, 0.30),
    "code":  (0.05, 0.35),
}

def record_self_assessment(username, concept, tier, p_self, p_bkt_current):
    """
    Called when student adjusts mastery on dashboard.
    Layer 1: Stores self-assessment (immediate blending effect).
    Layer 2: Tracks direction, triggers parameter adaptation if consistent.
    Returns dict with adaptation status.
    """
    # ... (read existing row, update EMA, check trigger, adapt P_G/P_S)

def get_adapted_params(username, concept, tier):
    """Return adapted P_G/P_S or None if not adapted."""
    # ... (read from user_bkt_calibration)
```

### File 4: `backend/app/main.py` — New API endpoints

#### GET `/api/v1/mastery/{username}/{concept}`

Returns detailed mastery breakdown with both BKT and self-assessment values:

```json
{
  "concept": "Arrays",
  "tiers": {
    "quiz": {
      "p_bkt": 0.82,
      "p_self": 0.60,
      "p_effective": 0.73,
      "n_evidence": 5,
      "adapted_P_G": 0.25,
      "adapted_P_S": null
    },
    "micro": { ... },
    "code": { ... }
  },
  "composite_effective": 0.65,
  "is_certified": false,
  "ever_certified": false,
  "calibration_status": "adapted_quiz_P_G"
}
```

#### POST `/api/v1/mastery/{username}/{concept}/self-assess`

Request body:
```json
{
  "tier": "quiz",
  "self_assessment": 0.60
}
```

Response:
```json
{
  "tier": "quiz",
  "p_bkt": 0.82,
  "p_self": 0.60,
  "p_effective": 0.73,
  "n_adjustments": 3,
  "parameter_adapted": true,
  "adaptation_detail": "P_G for quiz increased from 0.20 to 0.25 based on your consistent feedback",
  "message": "Your self-assessment has been recorded. Based on your feedback pattern, the system now accounts for your quiz-guessing tendency."
}
```

### File 5: `backend/app/agents/cot_rag_agent.py`

#### Change: `_classify_mastery_level()` uses effective mastery

Replace `_read_row()` + manual decay computation with `get_effective_mastery()`:

```python
from app.core.bkt_model import bkt

mastery = bkt.get_effective_mastery(username, resolved_concept)
avg_p = (mastery["quiz"] + mastery["micro"] + mastery["code"]) / 3.0
```

This automatically incorporates the student's self-assessment into the ATM classification.

---

## How the Four Mechanisms Interact

| Mechanism | What Adapts | Timescale | Trigger |
|-----------|------------|-----------|---------|
| **Forgetting decay** (existing) | P_BKT decays -> P_self gains relative weight | Days/weeks | Time passing without interaction |
| **SM-2 ease factor** (existing) | lambda decreases -> slower forgetting | After successful reviews | Correct answers on review |
| **Score blending** (Layer 1, new) | P_effective = alpha*P_BKT + (1-alpha)*P_self | Immediate | Dashboard slider adjustment |
| **Parameter adaptation** (Layer 2, new) | P_G/P_S personalized per user+tier | After 3+ consistent adjustments | Repeated dashboard adjustments in same direction |

All four converge naturally: the system becomes more aligned with each individual student over time through multiple independent paths.

---

## Impact on the Paper

### New Contribution (Contribution 3)

**SRL-BKT Calibration Loop** — A closed-loop mechanism where student self-assessment directly influences BKT parameters. Unlike standard personalized BKT (Yudelson et al., 2013) which adapts parameters from behavioral data alone, SAGE-SRL adapts parameters from metacognitive feedback. This bridges SRL theory and knowledge tracing in a novel way.

| System | BKT Personalization | Student Feedback -> Model? |
|--------|--------------------|-----------------------|
| Standard BKT | None (fixed parameters) | No |
| Personalized BKT (Yudelson 2013) | Per-student P_L0 from data | No (data-driven only) |
| Deep Knowledge Tracing | Neural, implicit | No |
| ALEKS | Knowledge spaces | Partial (re-assessment) |
| **SAGE-SRL** | **Per-user P_G/P_S from calibration** | **Yes (student metacognitive feedback drives adaptation)** |

### New Evaluation Studies

#### Synthetic Study: Calibration Convergence

Simulate 1000 learners with known "true" P_G values (some are good guessers, some aren't). Show that:
- Without calibration loop: BKT over-credits good guessers (false mastery)
- With calibration loop: adapted P_G converges toward true P_G after 3-5 self-assessments
- Alpha sensitivity: show robustness across alpha = {0.5, 0.6, 0.7, 0.8}

#### Software-Defined Test: Dashboard Self-Assessment Quality

Scripted scenarios through the running system:
- Student A: always agrees with BKT -> no parameter change
- Student B: consistently lowers quiz mastery -> P_G adapts upward
- Student C: consistently raises code mastery -> P_S adapts downward
- Verify: Student B needs more quiz evidence to certify; Student C needs less code evidence

### Updated Paper Section V Structure

```
V. PERFORMANCE EVALUATION

  A. Experimental Setup
  B. Mastery Signal Validation (Studies 1.1-1.3) — existing
  C. SRL Components (Studies 1.4-1.7) — existing
  D. System Behavior (Evals 2.1-2.4)
     - 2.1: Mastery-Aware Adaptation (ATM) — existing
     - 2.5: SRL-BKT Calibration Loop (NEW)
  E. Calibration Convergence (NEW)
     - Synthetic: P_G convergence under self-assessment
     - Alpha sensitivity analysis
  F. Comparative Evaluation — existing
  G. Ablation Analysis — add calibration loop ablation variant
```

---

## Implementation Order

1. **Database schema** — Add `user_bkt_calibration` table
2. **`srl_calibration.py`** — Core calibration logic (record, EMA, adapt)
3. **`bkt_model.py`** — `_get_user_params()` + `get_effective_mastery()` + update() change
4. **`main.py`** — GET/POST mastery endpoints
5. **`cot_rag_agent.py`** — ATM uses effective mastery
6. **Synthetic eval** — Calibration convergence study
7. **Software-defined eval** — Dashboard self-assessment scenarios

Steps 1-5 are implementation. Steps 6-7 are evaluation.

---

## Certification Transparency

When parameter adaptation fires, the student is notified:

> "Based on your feedback, I've adjusted how I evaluate your quiz performance for Arrays.
> Since you've indicated your quiz scores overestimate your understanding, the system now
> requires stronger quiz evidence for mastery certification. This means your quiz results
> are weighted more carefully — matching your own self-awareness."

This transparency:
- Reinforces SRL metacognitive skills (the student sees their self-regulation having real impact)
- Prevents frustration ("why is it harder to certify now?")
- Aligns with Open Learner Model principles (Bull & Kay, 2010)
