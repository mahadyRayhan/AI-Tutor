# backend/app/core/mcn_evidence.py
#
# Evidence adapters for the Metacognitive Calibration Network (Phase 3).
# ─────────────────────────────────────────────────────────────────────
# Turns signals the system ALREADY logs into the BN's evidence vector + a soft K
# prior. This is the only bridge between the pure engine (mcn.py) and live data;
# it is strictly READ-ONLY and every DB access is guarded so a live request can
# never break because a table is empty or a value is odd.
#
# Signal → node mapping
# ─────────────────────
#   K prior  ← bkt_model.get_mastery()  (OBJECTIVE composite, decayed). We use the
#              raw BKT estimate, NOT get_effective_mastery(), because the latter
#              already blends in the student's self-report — and self-report is a
#              SEPARATE evidence node (S). Blending it into K would double-count it.
#   S (self-report) ← jol_log.confidence_1_5 (most recent), else user_bkt_calibration
#              self_assessment averaged across tiers.        [concept-scoped]
#   P (performance) ← evidence_log.is_correct over a recent window.  [concept-scoped]
#   B (behaviour)   ← behavior_log events (hint_request/skip_challenge = struggle;
#              copy_code/long-dwell = fluent).       [user-recent, NOT concept-scoped]
#   A (affect)      ← affect_log.frustration_level (most recent).
#              [user-recent, NOT concept-scoped]
#
# Caveat carried into the paper: B and A are session/user-level, not per-concept, so
# they are treated as weak, corroborating evidence — the concept-specific weight
# rests on K, S, and P.

from __future__ import annotations

import logging
import math
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# How many recent rows define "recent" for performance / behaviour windows.
PERF_WINDOW = 5
BEHAV_WINDOW = 8

# Minimum observed leaves for a calibration verdict to be considered trustworthy
# (below this, the posterior is basically the prior and callers should not act).
MIN_SIGNALS_FOR_ACTION = 2

# Soft K-prior kernel: prototypes for low/med/high knowledge on a [0,1] scale.
_K_PROTOTYPES = {"low": 0.15, "med": 0.50, "high": 0.85}
_K_SIGMA = 0.22


# ─────────────────────────────────────────────────────────────────────────────
# K prior from BKT
# ─────────────────────────────────────────────────────────────────────────────

def _scalar_to_k_prior(m: float) -> Dict[str, float]:
    """
    Map an objective mastery scalar m ∈ [0,1] to a soft distribution over K using a
    Gaussian kernel around low/med/high prototypes. Smooth (no hard bins), so a
    borderline student contributes appropriately-hedged prior mass.
    """
    m = max(0.0, min(1.0, m))
    w = {
        s: math.exp(-((m - proto) ** 2) / (2 * _K_SIGMA ** 2))
        for s, proto in _K_PROTOTYPES.items()
    }
    total = sum(w.values())
    return {s: v / total for s, v in w.items()}


def k_prior_from_bkt(username: str, concept: str) -> Optional[Dict[str, float]]:
    """
    Soft K prior from the OBJECTIVE BKT composite (decayed), normalised by the
    composite ceiling. Returns None when the student has no BKT row for the concept
    (so the engine falls back to its cold-start prior_K).
    """
    try:
        from app.core import bkt_model
        row = bkt_model._read_row(username, concept)
        if not row:
            return None
        composite = bkt_model.get_mastery(username, concept)      # ∈ [0, 0.95]
        m = composite / bkt_model.MASTERY_THRESHOLD               # → [0, 1]
        return _scalar_to_k_prior(m)
    except Exception as e:
        logger.warning(f"[MCN-EV] k_prior failed for {username}/{concept}: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Observable leaves
# ─────────────────────────────────────────────────────────────────────────────

def _self_report_state(username: str, concept: str) -> Optional[str]:
    """S from the most recent JoL confidence (1–5); fall back to calibration slider."""
    from app.db.sqlite_db import db
    try:
        row = db.fetch_one(
            "SELECT confidence_1_5 FROM jol_log WHERE username=? AND concept=? "
            "ORDER BY ts_utc DESC, id DESC LIMIT 1",
            (username, concept),
        )
        if row and row["confidence_1_5"] is not None:
            c = int(row["confidence_1_5"])
            if c <= 2:
                return "low"
            if c == 3:
                return "med"
            return "high"
    except Exception as e:
        logger.warning(f"[MCN-EV] jol read failed: {e}")

    # Fallback: mean self_assessment across tiers from the calibration table.
    try:
        rows = db.fetch_all(
            "SELECT self_assessment FROM user_bkt_calibration "
            "WHERE username=? AND concept=? AND self_assessment IS NOT NULL",
            (username, concept),
        )
        vals = [r["self_assessment"] for r in rows if r["self_assessment"] is not None]
        if vals:
            avg = sum(vals) / len(vals)
            if avg < 0.34:
                return "low"
            if avg < 0.67:
                return "med"
            return "high"
    except Exception as e:
        logger.warning(f"[MCN-EV] calibration read failed: {e}")
    return None


def _performance_state(username: str, concept: str) -> Optional[str]:
    """P from the fraction correct over the recent evidence window for this concept."""
    from app.db.sqlite_db import db
    try:
        rows = db.fetch_all(
            "SELECT is_correct FROM evidence_log WHERE username=? AND concept=? "
            "ORDER BY ts_utc DESC, id DESC LIMIT ?",
            (username, concept, PERF_WINDOW),
        )
        vals = [r["is_correct"] for r in rows if r["is_correct"] is not None]
        if not vals:
            return None
        frac = sum(vals) / len(vals)
        if frac < 0.34:
            return "poor"
        if frac < 0.67:
            return "mixed"
        return "good"
    except Exception as e:
        logger.warning(f"[MCN-EV] evidence read failed: {e}")
        return None


def _behavior_state(username: str) -> Optional[str]:
    """
    B from recent behaviour events (user-recent, not concept-scoped).
    hint_request / skip_challenge → struggle; copy_code / long dwell → fluent.
    """
    from app.db.sqlite_db import db
    try:
        rows = db.fetch_all(
            "SELECT event, value FROM behavior_log WHERE username=? "
            "ORDER BY ts_utc DESC, id DESC LIMIT ?",
            (username, BEHAV_WINDOW),
        )
        if not rows:
            return None
        struggle = fluent = 0
        for r in rows:
            ev = (r["event"] or "").lower()
            if ev in ("hint_request", "skip_challenge"):
                struggle += 1
            elif ev == "thumbs_down_click":
                struggle += 1              # dissatisfaction — a weak struggle signal
            elif ev == "copy_code":
                fluent += 1
            elif ev == "dwell":
                try:
                    if float(r["value"]) >= 20.0:   # ≥20s dwell reads as engaged
                        fluent += 1
                except (TypeError, ValueError):
                    pass
        if struggle >= 2 and struggle > fluent:
            return "struggling"
        if fluent >= 2 and fluent > struggle:
            return "fluent"
        return "normal"
    except Exception as e:
        logger.warning(f"[MCN-EV] behavior read failed: {e}")
        return None


def _affect_state(username: str) -> Optional[str]:
    """A from the most recent frustration_level (user-recent, not concept-scoped)."""
    from app.db.sqlite_db import db
    try:
        row = db.fetch_one(
            "SELECT frustration_level FROM affect_log WHERE username=? "
            "ORDER BY ts_utc DESC, id DESC LIMIT 1",
            (username,),
        )
        if not row or not row["frustration_level"]:
            return None
        f = row["frustration_level"].lower()
        if f in ("high", "rage"):
            return "frustrated"
        if f in ("delighted", "flow"):
            return "engaged"
        if f in ("normal", "neutral", "calm"):
            return "neutral"
        return None
    except Exception as e:
        logger.warning(f"[MCN-EV] affect read failed: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Assembly + convenience inference
# ─────────────────────────────────────────────────────────────────────────────

def gather_evidence(username: str, concept: str) -> Tuple[Dict[str, str], Optional[Dict[str, float]]]:
    """
    Assemble the observed-evidence dict and the soft K prior for (user, concept).
    Missing signals are simply omitted (the BN marginalises them out).
    """
    evidence: Dict[str, str] = {}
    for node, val in (
        ("S", _self_report_state(username, concept)),
        ("P", _performance_state(username, concept)),
        ("B", _behavior_state(username)),
        ("A", _affect_state(username)),
    ):
        if val is not None:
            evidence[node] = val
    k_prior = k_prior_from_bkt(username, concept)
    return evidence, k_prior


def infer_calibration(username: str, concept: str) -> dict:
    """
    Full pipeline: gather live evidence → run the network (current CPTs) → return a
    JSON-safe verdict with the evidence used, a sufficiency flag, and a plain-language
    explanation. This is what the SRL integration (Phase 5/6) calls.
    """
    from app.core import mcn
    from app.core.mcn_cpts import get_network

    evidence, k_prior = gather_evidence(username, concept)
    result = mcn.infer(evidence, get_network(), k_prior=k_prior)

    n_signals = len(evidence)
    out = result.as_dict()
    out.update({
        "username": username,
        "concept": concept,
        "n_signals": n_signals,
        "sufficient": n_signals >= MIN_SIGNALS_FOR_ACTION,
        "k_prior_from_bkt": k_prior is not None,
        "explanation": _explain(result, n_signals),
    })
    return out


def _explain(result, n_signals: int) -> str:
    """A short, student-neutral account of the verdict for logs / dashboard."""
    if n_signals < MIN_SIGNALS_FOR_ACTION:
        return ("Not enough signal yet to judge calibration — "
                "showing the prior estimate.")
    c = result.map_C
    conf = result.confidence
    k = result.map_K
    if c == "under":
        return (f"Performance/behaviour suggest {k} knowledge, but self-report is "
                f"lower — likely under-confident (p={conf:.2f}). Reassure and surface "
                f"their own evidence.")
    if c == "over":
        return (f"Self-report is high but performance/behaviour suggest {k} "
                f"knowledge — likely over-confident (p={conf:.2f}). Offer a quick "
                f"self-check before advancing.")
    return (f"Self-report is consistent with {k} knowledge — well-calibrated "
            f"(p={conf:.2f}).")
