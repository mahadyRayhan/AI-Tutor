# backend/app/core/bkt_model.py
#
# Reparameterized Tiered Bayesian Knowledge Tracing with Forgetting Decay
# ─────────────────────────────────────────────────────────────────────────
# Each tier tracks a proper BKT posterior P̃^(k) ∈ [0, 1] — no ceiling clip.
# The displayed/composite score scales by ceiling: P^(k) = θ_max^(k) · P̃^(k).
#
# Tiers (Bloom's taxonomy):
#   Tier 1 — Declarative  (quiz)        θ_max = 0.60
#   Tier 2 — Procedural   (micro)       θ_max = 0.25
#   Tier 3 — Applied      (code review) θ_max = 0.10
#
# Unified mastery criterion (Corbett & Anderson, 1995):
#   Certify:   ALL P̃^(k) ≥ THETA_CERTIFY = 0.95  AND  n^(k)_evidence ≥ N_MIN
#   Decertify: ANY P̃^(k) < THETA_DECERTIFY = 0.75  (hysteresis)
#
# Minimum-evidence guarantee — P_T applied on correct responses only (Fix #1):
#   quiz:  n_min = 3  (P̃: 0.30 → 0.689 → 0.917 → 0.982)
#   micro: n_min = 3  (P̃: 0.05 → 0.371 → 0.848 → 0.981)
#   code:  n_min = 3  (P̃: 0.01 → 0.217 → 0.833 → 0.989)
#
# False-certification bounds (non-master at guess rate P_G, 3 correct guesses):
#   quiz: P_G^3 = 0.20^3 = 0.80%   micro: 0.10^3 = 0.10%   code: 0.05^3 = 0.013%
#   Wrong answers only penalise: P_T no longer rescues incorrect responses.
#
# Forgetting (SM-2 ↔ BKT coupling, Fix #6):
#   P̃(t) = P̃(t₀)·e^(−λΔt) + P̃₀·(1−e^(−λΔt))
#   After each correct review: λ_new = λ_old / EF  (EF = SM-2 ease factor ≥ 1.3)
#   → each successful review extends the memory half-life.
#
# Parameters:
#   P_G  — probability of correct response without knowledge (guess rate)
#   P_S  — probability of incorrect response despite knowledge (slip rate)
#   P_T  — probability of learning per CORRECT attempt (learning rate)
#   P_L0 — prior probability of knowing before any evidence (nonzero per Cromwell's rule)

import math
import logging
from datetime import datetime
from app.db.sqlite_db import db
from app.core.threshold_calibrator import calibrator, THRESHOLD_PRIORS

logger = logging.getLogger(__name__)

# ── Evidence-tier configuration ───────────────────────────────────────────────
EVIDENCE_CONFIG = {
    "quiz": {
        "P_G": 0.20, "P_S": 0.10, "P_L0": 0.30, "P_T": 0.09,
        "ceiling": 0.60, "col": "p_mastery_quiz"
    },
    "micro": {
        "P_G": 0.10, "P_S": 0.15, "P_L0": 0.05, "P_T": 0.09,
        "ceiling": 0.25, "col": "p_mastery_micro"
    },
    "code": {
        "P_G": 0.05, "P_S": 0.20, "P_L0": 0.01, "P_T": 0.09,
        "ceiling": 0.10, "col": "p_mastery_code"
    },
}

MASTERY_THRESHOLD  = 0.95   # composite display ceiling (Σ θ_max = 0.95)
THETA_CERTIFY      = 0.95   # enter mastered state
THETA_DECERTIFY    = 0.75   # exit mastered state (hysteresis)
N_MIN              = 3      # minimum correct-answer evidence per tier for certification

# Default decay rates λ (day⁻¹): half-lives 14 / 7 / 5 days
DECAY_RATES = {
    "quiz":  math.log(2) / 14,   # ≈ 0.04951
    "micro": math.log(2) / 7,    # ≈ 0.09902
    "code":  math.log(2) / 5,    # ≈ 0.13863
}

# Column maps
_TS_COL  = {"quiz": "last_quiz_at",    "micro": "last_micro_at",   "code": "last_code_at"}
_N_COL   = {"quiz": "n_evidence_quiz", "micro": "n_evidence_micro","code": "n_evidence_code"}
_LAM_COL = {"quiz": "decay_quiz_lam",  "micro": "decay_micro_lam", "code": "decay_code_lam"}


def _parse_ts(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _bkt_step(p_l: float, is_correct: bool, cfg: dict) -> float:
    """
    One BKT update step — returns P̃ ∈ [0, 1], unconstrained proper posterior.

    Fix #1: P_T (learning transition) is applied ONLY after correct responses.
    On incorrect responses the posterior is penalised but receives no learning
    credit, preventing P_T from dominating the slip/guess penalty at low priors.
    """
    P_G, P_S, P_T = cfg["P_G"], cfg["P_S"], cfg["P_T"]

    if is_correct:
        p_obs = p_l * (1 - P_S) + (1 - p_l) * P_G
        p_l_given_obs = (p_l * (1 - P_S)) / p_obs if p_obs > 0 else p_l
        return round(p_l_given_obs + (1 - p_l_given_obs) * P_T, 6)
    else:
        p_obs = p_l * P_S + (1 - p_l) * (1 - P_G)
        p_l_given_obs = (p_l * P_S) / p_obs if p_obs > 0 else p_l
        return round(p_l_given_obs, 6)   # penalty only; no learning credit on error


def _apply_decay(p_tilde: float, tier: str, last_at: datetime | None,
                 lam: float | None = None) -> float:
    """
    Exponential forgetting:
        P̃(t) = P̃(t₀)·e^(−λΔt) + P̃₀·(1−e^(−λΔt))

    Uses stored per-concept λ if provided (Fix #6 adaptive decay), else DECAY_RATES default.
    Asymptotes to the tier Cromwell prior P̃₀ — never collapses to 0.
    """
    if last_at is None:
        return p_tilde
    delta_days = (datetime.now() - last_at).total_seconds() / 86400.0
    if delta_days <= 0:
        return p_tilde
    lam_eff = lam if lam is not None else DECAY_RATES[tier]
    p0 = EVIDENCE_CONFIG[tier]["P_L0"]
    decayed = p_tilde * math.exp(-lam_eff * delta_days) + p0 * (1.0 - math.exp(-lam_eff * delta_days))
    return round(max(decayed, p0), 6)


def _read_row(username: str, concept: str):
    """Fetch full BKT state row for (username, concept)."""
    return db.fetch_one(
        "SELECT p_mastery_quiz, p_mastery_micro, p_mastery_code, "
        "last_quiz_at, last_micro_at, last_code_at, "
        "n_evidence_quiz, n_evidence_micro, n_evidence_code, "
        "decay_quiz_lam, decay_micro_lam, decay_code_lam, "
        "ease_factor, is_certified, ever_certified "
        "FROM user_knowledge WHERE username=? AND concept=?",
        (username, concept)
    )


def update(username: str, concept: str, is_correct: bool, evidence_type: str = "quiz") -> float:
    """
    Updates one evidence tier and returns the new composite display score.
    Applies forgetting decay before the BKT step (Fix #6).
    On correct answers: increments n_evidence counter (Fix #3), adapts λ (Fix #6).
    """
    if evidence_type not in EVIDENCE_CONFIG:
        logger.warning(f"[BKT] Unknown evidence_type '{evidence_type}', defaulting to quiz")
        evidence_type = "quiz"

    cfg    = EVIDENCE_CONFIG[evidence_type]
    col    = cfg["col"]
    ts_col = _TS_COL[evidence_type]
    n_col  = _N_COL[evidence_type]
    lam_col = _LAM_COL[evidence_type]

    row = _read_row(username, concept)
    if row is None:
        db.execute(
            """INSERT OR IGNORE INTO user_knowledge
               (username, concept, timestamp,
                p_mastery_quiz, p_mastery_micro, p_mastery_code, p_mastery)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (username, concept, datetime.now(),
             EVIDENCE_CONFIG["quiz"]["P_L0"],
             EVIDENCE_CONFIG["micro"]["P_L0"],
             EVIDENCE_CONFIG["code"]["P_L0"],
             EVIDENCE_CONFIG["quiz"]["P_L0"] * EVIDENCE_CONFIG["quiz"]["ceiling"])
        )
        row = _read_row(username, concept)

    # --- Decay-adjusted current posterior ---
    stored_lam = row[lam_col] if row[lam_col] else DECAY_RATES[evidence_type]
    p_raw     = row[col] if row[col] is not None else cfg["P_L0"]
    p_current = _apply_decay(p_raw, evidence_type, _parse_ts(row[ts_col]), lam=stored_lam)

    # --- BKT step (Fix #1: P_T only on correct) ---
    p_new = _bkt_step(p_current, is_correct, cfg)

    # --- Composite ---
    quiz_val  = row["p_mastery_quiz"]  if row["p_mastery_quiz"]  is not None else EVIDENCE_CONFIG["quiz"]["P_L0"]
    micro_val = row["p_mastery_micro"] if row["p_mastery_micro"] is not None else EVIDENCE_CONFIG["micro"]["P_L0"]
    code_val  = row["p_mastery_code"]  if row["p_mastery_code"]  is not None else EVIDENCE_CONFIG["code"]["P_L0"]
    tier_values = {"quiz": quiz_val, "micro": micro_val, "code": code_val}
    tier_values[evidence_type] = p_new
    composite = round(
        tier_values["quiz"]  * EVIDENCE_CONFIG["quiz"]["ceiling"]  +
        tier_values["micro"] * EVIDENCE_CONFIG["micro"]["ceiling"] +
        tier_values["code"]  * EVIDENCE_CONFIG["code"]["ceiling"],
        6
    )

    # --- Persist: always update P̃, composite, timestamp ---
    db.execute(
        f"UPDATE user_knowledge SET {col}=?, p_mastery=?, {ts_col}=? WHERE username=? AND concept=?",
        (p_new, composite, datetime.now(), username, concept)
    )

    # --- On correct: increment evidence counter (Fix #3) + slow decay rate (Fix #6) ---
    if is_correct:
        n_old    = (row[n_col] or 0)
        ease     = max(row["ease_factor"] or 2.5, 1.3)        # SM-2 ease factor
        lam_new  = round(stored_lam / ease, 8)                 # slower forgetting after review
        db.execute(
            f"UPDATE user_knowledge SET {n_col}=?, {lam_col}=? WHERE username=? AND concept=?",
            (n_old + 1, lam_new, username, concept)
        )

    symbol = "✅" if is_correct else "❌"
    logger.info(
        f"📐 [BKT/{evidence_type.upper()}] '{concept}' {username}: "
        f"P̃ {p_raw:.3f}→(decay)→{p_current:.3f}→{p_new:.3f} {symbol} | "
        f"n={row[n_col] or 0}{'→'+str((row[n_col] or 0)+1) if is_correct else ''} | "
        f"composite={composite:.3f}"
    )

    calibrator.record_posterior(concept, evidence_type, p_new)
    return composite


def get_mastery(username: str, concept: str) -> float:
    """Returns composite display score ∈ [0, 0.95] with forgetting decay applied."""
    row = _read_row(username, concept)
    if not row:
        return EVIDENCE_CONFIG["quiz"]["P_L0"] * EVIDENCE_CONFIG["quiz"]["ceiling"]

    q = _apply_decay(row["p_mastery_quiz"]  or EVIDENCE_CONFIG["quiz"]["P_L0"],
                     "quiz",  _parse_ts(row["last_quiz_at"]),  lam=row["decay_quiz_lam"])
    m = _apply_decay(row["p_mastery_micro"] or EVIDENCE_CONFIG["micro"]["P_L0"],
                     "micro", _parse_ts(row["last_micro_at"]), lam=row["decay_micro_lam"])
    c = _apply_decay(row["p_mastery_code"]  or EVIDENCE_CONFIG["code"]["P_L0"],
                     "code",  _parse_ts(row["last_code_at"]),  lam=row["decay_code_lam"])

    return round(
        q * EVIDENCE_CONFIG["quiz"]["ceiling"]  +
        m * EVIDENCE_CONFIG["micro"]["ceiling"] +
        c * EVIDENCE_CONFIG["code"]["ceiling"],
        6
    )


def is_mastered(username: str, concept: str) -> bool:
    """
    Conjunctive mastery gate with Schmitt-trigger hysteresis and explicit n_min gate.

    Certification requires:
        (a) ALL P̃^(k) ≥ θ^(k)_certify   (default THETA_CERTIFY = 0.95)
        (b) ALL n^(k)_evidence ≥ N_MIN = 3   (Fix #3: no lucky-guess certification)
    Decertification: ANY decayed P̃^(k) < THETA_DECERTIFY (0.75)

    On first certification, ever_certified is set to 1 and never reset (Fix #5):
    the prerequisite gate uses ever_certified so decay cannot re-lock earned
    prerequisites.
    """
    row = _read_row(username, concept)
    if not row:
        return False

    q = _apply_decay(row["p_mastery_quiz"]  or 0.0, "quiz",  _parse_ts(row["last_quiz_at"]),  lam=row["decay_quiz_lam"])
    m = _apply_decay(row["p_mastery_micro"] or 0.0, "micro", _parse_ts(row["last_micro_at"]), lam=row["decay_micro_lam"])
    c = _apply_decay(row["p_mastery_code"]  or 0.0, "code",  _parse_ts(row["last_code_at"]),  lam=row["decay_code_lam"])
    was_certified = bool(row["is_certified"])

    if was_certified:
        still_mastered = (q >= THETA_DECERTIFY and m >= THETA_DECERTIFY and c >= THETA_DECERTIFY)
        if not still_mastered:
            db.execute(
                "UPDATE user_knowledge SET is_certified=0 WHERE username=? AND concept=?",
                (username, concept)
            )
        return still_mastered

    # --- Not yet certified: check both P̃ threshold AND evidence count ---
    n_q = row["n_evidence_quiz"]  or 0
    n_m = row["n_evidence_micro"] or 0
    n_c = row["n_evidence_code"]  or 0

    θ_q = calibrator.get_threshold(concept, "quiz")
    θ_m = calibrator.get_threshold(concept, "micro")
    θ_c = calibrator.get_threshold(concept, "code")

    newly_mastered = (q >= θ_q and m >= θ_m and c >= θ_c and
                      n_q >= N_MIN and n_m >= N_MIN and n_c >= N_MIN)

    if newly_mastered:
        db.execute(
            "UPDATE user_knowledge SET is_certified=1, ever_certified=1 WHERE username=? AND concept=?",
            (username, concept)
        )
    return newly_mastered


bkt = type("BKTModel", (), {
    "update":      staticmethod(update),
    "get_mastery": staticmethod(get_mastery),
    "is_mastered": staticmethod(is_mastered),
})()
