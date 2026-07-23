# backend/app/core/mcn_service.py
#
# Service layer for the Metacognitive Calibration Network (Phase 5).
# ─────────────────────────────────────────────────────────────────
# The ONE safe entry point the live system uses to obtain a calibration verdict.
# Everything here is defensive so enabling the feature can never break a request:
#
#   • flag-gated  — returns None unless config.MCN_ENABLED is true (default OFF).
#   • sufficiency — returns None unless enough signals were observed to act.
#   • fail-safe   — any exception is swallowed and returns None (tutor falls back to
#                   its existing behaviour).
#   • never certifies — this module only reports a metacognitive state; it does not
#                   touch BKT posteriors, effective mastery, or the mastery gate.
#
# Adaptation is delivered downstream by INJECTING a short instruction into the
# tutoring prompt (see socratic.py), NOT by mutating the mastery number — a deliberate
# choice that keeps the certified-mastery quantity exactly as BKT computes it.

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def is_enabled() -> bool:
    """True iff the MCN feature flag is on. Import config lazily so tests/tools that
    tweak the env var see the current value."""
    try:
        from app.core import config
        return bool(getattr(config, "MCN_ENABLED", False))
    except Exception:
        return False


def get_calibration(username: str, concept: str, log: bool = True) -> Optional[dict]:
    """
    Return the MCN calibration verdict for (username, concept), or None when the
    feature is off / inputs missing / signal insufficient / anything errors.

    A returned dict is the mcn_evidence.infer_calibration payload
    (map_C, label, confidence, map_K, n_signals, sufficient, explanation, ...).
    """
    if not is_enabled() or not username or not concept:
        return None
    try:
        from app.core import mcn_evidence
        verdict = mcn_evidence.infer_calibration(username, concept)
    except Exception as e:
        logger.warning(f"[MCN-SVC] inference failed for {username}/{concept}: {e}")
        return None

    if not verdict.get("sufficient"):
        return None

    if log:
        try:
            from app.core import telemetry
            telemetry.log_mcn_verdict(username, concept, verdict)
        except Exception as e:
            logger.debug(f"[MCN-SVC] telemetry skipped: {e}")
    return verdict


def prompt_directive(verdict: Optional[dict]) -> str:
    """
    Turn a verdict into a short instruction for the tutoring LLM. Empty string for
    None / calibrated (no intervention needed). Consumed by socratic.py.
    """
    if not verdict:
        return ""
    state = verdict.get("map_C")
    if state == "under":
        return (
            "\n\n**METACOGNITIVE CALIBRATION — UNDER-CONFIDENT:** This learner's "
            "performance and behaviour show they genuinely grasp this material, but "
            "they rate themselves LOWER than warranted. Briefly and specifically "
            "affirm their demonstrated competence (reference that they have answered "
            "or applied this correctly), build their confidence, and avoid "
            "re-explaining basics they already know."
        )
    if state == "over":
        return (
            "\n\n**METACOGNITIVE CALIBRATION — OVER-CONFIDENT:** This learner rates "
            "their grasp HIGHER than their demonstrated performance supports. Without "
            "discouraging them, weave in ONE pointed check question on a subtle or "
            "commonly-missed aspect so any gap surfaces safely before they move on. "
            "Stay encouraging."
        )
    return ""
