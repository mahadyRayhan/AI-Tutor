# backend/app/core/bkt_model.py
# Tiered Bayesian Knowledge Tracing — three evidence tiers per Bloom's taxonomy:
#
#   Tier 1 — Declarative  (quiz):        ceiling 0.60 — can student recall facts?
#   Tier 2 — Procedural   (micro):       ceiling 0.25 — can student write code?
#   Tier 3 — Applied      (code review): ceiling 0.10 — can student reason about code?
#
# Composite P(L) = p_quiz + p_micro + p_code
# Mastery threshold = 0.95 = sum of all ceilings
# → student CANNOT reach mastery without evidence from all three tiers
#
# Parameters (literature defaults — calibrate from June student data):
#   P(G)  probability of guessing correctly without knowing
#   P(S)  probability of slipping despite knowing
#   P(T)  probability of learning per attempt (transition)
#   P(L0) prior probability of knowing before any evidence

import logging
from datetime import datetime
from app.db.sqlite_db import db

logger = logging.getLogger(__name__)

EVIDENCE_CONFIG = {
    "quiz": {
        "P_G": 0.20, "P_S": 0.10, "P_L0": 0.30, "P_T": 0.09,
        "ceiling": 0.60, "col": "p_mastery_quiz"
    },
    "micro": {
        "P_G": 0.10, "P_S": 0.15, "P_L0": 0.00, "P_T": 0.09,
        "ceiling": 0.25, "col": "p_mastery_micro"
    },
    "code": {
        "P_G": 0.05, "P_S": 0.20, "P_L0": 0.00, "P_T": 0.09,
        "ceiling": 0.10, "col": "p_mastery_code"
    },
}

MASTERY_THRESHOLD = 0.95  # = 0.60 + 0.25 + 0.10 — used for dashboard display only

# Per-tier decision thresholds: set at ~95% of each ceiling so mastery uses
# a strict ≥ check that doesn't require exact numerical ceiling equality.
# θ_mastery^(k) < θ_max^(k) for all k — decouples evidence contribution cap
# from evidence sufficiency criterion (see SYSTEM_OVERVIEW §3.4.1).
MASTERY_THRESHOLDS = {
    "quiz":  0.57,   # 95% of ceiling 0.60 — declarative sufficiency threshold
    "micro": 0.24,   # 96% of ceiling 0.25 — procedural sufficiency threshold
    "code":  0.09,   # 90% of ceiling 0.10 — applied sufficiency threshold
}


def _bkt_step(p_l: float, is_correct: bool, cfg: dict) -> float:
    """One BKT update step for a single evidence tier."""
    P_G, P_S, P_T = cfg["P_G"], cfg["P_S"], cfg["P_T"]

    if is_correct:
        p_obs = p_l * (1 - P_S) + (1 - p_l) * P_G
        p_l_given_obs = (p_l * (1 - P_S)) / p_obs if p_obs > 0 else p_l
    else:
        p_obs = p_l * P_S + (1 - p_l) * (1 - P_G)
        p_l_given_obs = (p_l * P_S) / p_obs if p_obs > 0 else p_l

    p_l_next = p_l_given_obs + (1 - p_l_given_obs) * P_T
    return round(min(p_l_next, cfg["ceiling"]), 6)


def update(username: str, concept: str, is_correct: bool, evidence_type: str = "quiz") -> float:
    """
    Updates one evidence tier for a concept and returns the new composite P(L).
    Creates the row if it does not yet exist.
    """
    if evidence_type not in EVIDENCE_CONFIG:
        logger.warning(f"[BKT] Unknown evidence_type '{evidence_type}', defaulting to quiz")
        evidence_type = "quiz"

    cfg = EVIDENCE_CONFIG[evidence_type]
    col = cfg["col"]

    row = db.fetch_one(
        f"SELECT p_mastery_quiz, p_mastery_micro, p_mastery_code FROM user_knowledge WHERE username=? AND concept=?",
        (username, concept)
    )

    if row is None:
        # Insert row with priors
        db.execute(
            """INSERT OR IGNORE INTO user_knowledge
               (username, concept, timestamp, p_mastery_quiz, p_mastery_micro, p_mastery_code, p_mastery)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (username, concept, datetime.now(),
             EVIDENCE_CONFIG["quiz"]["P_L0"],
             EVIDENCE_CONFIG["micro"]["P_L0"],
             EVIDENCE_CONFIG["code"]["P_L0"],
             EVIDENCE_CONFIG["quiz"]["P_L0"])
        )
        row = db.fetch_one(
            "SELECT p_mastery_quiz, p_mastery_micro, p_mastery_code FROM user_knowledge WHERE username=? AND concept=?",
            (username, concept)
        )

    p_current = row[col] if row[col] is not None else cfg["P_L0"]
    p_new = _bkt_step(p_current, is_correct, cfg)

    # Recompute composite from all three columns
    quiz_val  = row["p_mastery_quiz"]  if row["p_mastery_quiz"]  is not None else EVIDENCE_CONFIG["quiz"]["P_L0"]
    micro_val = row["p_mastery_micro"] if row["p_mastery_micro"] is not None else 0.0
    code_val  = row["p_mastery_code"]  if row["p_mastery_code"]  is not None else 0.0

    # Replace the updated tier value in the composite sum
    tier_values = {"quiz": quiz_val, "micro": micro_val, "code": code_val}
    tier_values[evidence_type] = p_new
    composite = round(sum(tier_values.values()), 6)

    db.execute(
        f"UPDATE user_knowledge SET {col}=?, p_mastery=? WHERE username=? AND concept=?",
        (p_new, composite, username, concept)
    )

    symbol = "✅" if is_correct else "❌"
    logger.info(
        f"📐 [BKT/{evidence_type.upper()}] '{concept}' {username}: "
        f"{p_current:.3f}→{p_new:.3f} {symbol} | composite={composite:.3f}"
    )
    return composite


def get_mastery(username: str, concept: str) -> float:
    """Returns current composite P(L). Returns prior if not yet tracked."""
    row = db.fetch_one(
        "SELECT p_mastery FROM user_knowledge WHERE username=? AND concept=?",
        (username, concept)
    )
    return row["p_mastery"] if row and row["p_mastery"] is not None else EVIDENCE_CONFIG["quiz"]["P_L0"]


def is_mastered(username: str, concept: str) -> bool:
    """True when all three subskill posteriors meet their individual mastery thresholds.

    Uses conjunctive check P_t^(k) >= theta_mastery^(k) for each tier rather than
    composite >= 0.95, so mastery does not require exact numerical ceiling equality.
    """
    row = db.fetch_one(
        "SELECT p_mastery_quiz, p_mastery_micro, p_mastery_code FROM user_knowledge WHERE username=? AND concept=?",
        (username, concept)
    )
    if not row:
        return False
    return (
        (row["p_mastery_quiz"]  or 0.0) >= MASTERY_THRESHOLDS["quiz"]  and
        (row["p_mastery_micro"] or 0.0) >= MASTERY_THRESHOLDS["micro"] and
        (row["p_mastery_code"]  or 0.0) >= MASTERY_THRESHOLDS["code"]
    )


bkt = type("BKTModel", (), {
    "update": staticmethod(update),
    "get_mastery": staticmethod(get_mastery),
    "is_mastered": staticmethod(is_mastered),
})()
