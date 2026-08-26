# backend/app/core/srl_calibration.py
#
# SRL-BKT Calibration Loop
# ─────────────────────────
# Layer 1 — Score Blending:  P_eff = α·P_BKT + (1−α)·P_self
# Layer 2 — Parameter Adaptation: sustained metacognitive disagreement
#           adjusts per-user P_G (guess rate) for the disagreed tier.
#
# Design constraints:
#   • Slider is DOWNWARD-ONLY — students can lower, never raise mastery.
#   • P_eff drives display and response adaptation ONLY, never certification.
#   • Certification always reads P_BKT (computed with adapted parameters).

import logging
from datetime import datetime, timezone
from app.db.sqlite_db import db

logger = logging.getLogger(__name__)

ALPHA = 0.6             # BKT trust weight (Layer 1)
LAMBDA_CAL = 0.4        # EMA smoothing for direction tracking
N_TRIGGER = 3           # minimum adjustments before parameter adaptation
DIRECTION_THRESHOLD = 0.3
BETA = 0.10             # adaptation step size

# P_G can only increase (student says "I guessed"); bounded per tier
P_G_MAX = {"quiz": 0.40, "micro": 0.25, "code": 0.15}

# Global defaults (mirrored from bkt_model.EVIDENCE_CONFIG)
P_G_DEFAULT = {"quiz": 0.20, "micro": 0.10, "code": 0.05}

VALID_TIERS = {"quiz", "micro", "code"}


def record_self_assessment(
    username: str,
    concept: str,
    tier: str,
    p_self: float,
    p_bkt_current: float,
) -> dict:
    """
    Record a student's downward self-assessment from the dashboard.

    Returns dict with blending result and adaptation status.
    Raises ValueError if p_self >= p_bkt_current (upward not allowed).
    """
    if tier not in VALID_TIERS:
        raise ValueError(f"Invalid tier: {tier}")
    if not (0.0 <= p_self <= 1.0):
        raise ValueError(f"p_self must be in [0, 1], got {p_self}")
    if p_self >= p_bkt_current:
        raise ValueError(
            f"Self-assessment ({p_self:.3f}) must be lower than "
            f"BKT estimate ({p_bkt_current:.3f}). Upward adjustment not allowed."
        )

    from app.core.concept_canon import canonical_concept
    concept = canonical_concept(concept)
    now = datetime.now(timezone.utc).isoformat()

    existing = db.fetch_one(
        "SELECT n_adjustments, direction_ema, adapted_P_G "
        "FROM user_bkt_calibration WHERE username=? AND concept=? AND tier=?",
        (username, concept, tier),
    )

    delta = p_self - p_bkt_current  # always negative (downward-only)
    direction = max(-1.0, min(1.0, delta / max(abs(delta), 0.01)))

    if existing:
        n_adj = existing["n_adjustments"] + 1
        old_ema = existing["direction_ema"]
        new_ema = LAMBDA_CAL * direction + (1 - LAMBDA_CAL) * old_ema
        current_P_G = existing["adapted_P_G"]

        db.execute(
            "UPDATE user_bkt_calibration SET "
            "self_assessment=?, last_adjusted_at=?, n_adjustments=?, direction_ema=? "
            "WHERE username=? AND concept=? AND tier=?",
            (p_self, now, n_adj, new_ema, username, concept, tier),
        )
    else:
        n_adj = 1
        new_ema = LAMBDA_CAL * direction
        current_P_G = None

        db.execute(
            "INSERT INTO user_bkt_calibration "
            "(username, concept, tier, self_assessment, last_adjusted_at, "
            " n_adjustments, direction_ema) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (username, concept, tier, p_self, now, n_adj, new_ema),
        )

    p_eff = ALPHA * p_bkt_current + (1 - ALPHA) * p_self

    pg_old = current_P_G if current_P_G is not None else P_G_DEFAULT[tier]
    adaptation = _maybe_adapt_P_G(
        username, concept, tier, n_adj, new_ema, current_P_G
    )
    pg_new = adaptation.get("new_P_G", pg_old) if adaptation["adapted"] else pg_old

    logger.info(
        f"[SRL-CAL] {username}/{concept}/{tier}: "
        f"P_BKT={p_bkt_current:.3f} P_self={p_self:.3f} P_eff={p_eff:.3f} "
        f"n_adj={n_adj} ema={new_ema:.3f} adapted={adaptation['adapted']}"
    )

    # --- Telemetry: calibration event ---
    try:
        from app.core import telemetry
        telemetry.log_calibration(
            username, concept, tier, round(p_bkt_current, 6), round(p_self, 6),
            round(delta, 6), round(new_ema, 6), round(pg_old, 6), round(pg_new, 6),
        )
        telemetry.log_event(username, "self_assessment", {
            "concept": concept, "tier": tier, "p_self": p_self,
            "p_bkt": p_bkt_current, "adapted": adaptation["adapted"],
        })
    except Exception as e:
        logger.warning(f"[SRL-CAL] telemetry hook failed: {e}")

    # --- Rule 6: doubt flows downhill. Lowering a prerequisite's effective mastery
    # reduces the head start it passes to zero-evidence dependents (never raises it). ---
    try:
        from app.core import prereq_headstart
        prereq_headstart.reduce_on_prereq_doubt(username, concept)
    except Exception as e:
        logger.warning(f"[SRL-CAL] head-start clawback skipped: {e}")

    return {
        "tier": tier,
        "p_bkt": round(p_bkt_current, 4),
        "p_self": round(p_self, 4),
        "p_effective": round(p_eff, 4),
        "n_adjustments": n_adj,
        "parameter_adapted": adaptation["adapted"],
        "adapted_P_G": adaptation.get("new_P_G"),
        "detail": adaptation.get("detail", ""),
    }


def _maybe_adapt_P_G(
    username: str,
    concept: str,
    tier: str,
    n_adj: int,
    direction_ema: float,
    current_adapted_P_G: float | None,
) -> dict:
    """
    Layer 2: If student has consistently lowered this tier (N_TRIGGER+ times,
    direction EMA below threshold), increase P_G for this user+concept+tier.
    """
    if n_adj < N_TRIGGER:
        return {"adapted": False, "detail": f"Need {N_TRIGGER - n_adj} more adjustments"}

    # direction_ema is negative for downward adjustments
    if direction_ema > -DIRECTION_THRESHOLD:
        return {"adapted": False, "detail": "Direction signal not strong enough"}

    base_P_G = current_adapted_P_G if current_adapted_P_G is not None else P_G_DEFAULT[tier]
    step = BETA * abs(direction_ema)
    new_P_G = min(base_P_G + step, P_G_MAX[tier])

    if new_P_G <= base_P_G + 0.001:
        return {"adapted": False, "detail": "P_G already at maximum for this tier"}

    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "UPDATE user_bkt_calibration SET adapted_P_G=?, last_adapted_at=? "
        "WHERE username=? AND concept=? AND tier=?",
        (round(new_P_G, 4), now, username, concept, tier),
    )

    detail = (
        f"P_G for {tier} increased from {base_P_G:.3f} to {new_P_G:.3f}. "
        f"Future correct {tier} answers contribute less to mastery."
    )
    logger.info(f"[SRL-CAL] Parameter adapted: {username}/{concept}/{tier} — {detail}")

    return {"adapted": True, "new_P_G": round(new_P_G, 4), "detail": detail}


def get_self_assessment(username: str, concept: str, tier: str) -> dict | None:
    """Read stored self-assessment and calibration state for one tier."""
    row = db.fetch_one(
        "SELECT self_assessment, n_adjustments, direction_ema, adapted_P_G, "
        "last_adjusted_at, last_adapted_at "
        "FROM user_bkt_calibration WHERE username=? AND concept=? AND tier=?",
        (username, concept, tier),
    )
    if not row:
        return None
    return {
        "self_assessment": row["self_assessment"],
        "n_adjustments": row["n_adjustments"],
        "direction_ema": row["direction_ema"],
        "adapted_P_G": row["adapted_P_G"],
        "last_adjusted_at": row["last_adjusted_at"],
        "last_adapted_at": row["last_adapted_at"],
    }


def get_adapted_P_G(username: str, concept: str, tier: str) -> float | None:
    """Return adapted P_G for this user+concept+tier, or None if not adapted."""
    row = db.fetch_one(
        "SELECT adapted_P_G FROM user_bkt_calibration "
        "WHERE username=? AND concept=? AND tier=?",
        (username, concept, tier),
    )
    if row and row["adapted_P_G"] is not None:
        return row["adapted_P_G"]
    return None
