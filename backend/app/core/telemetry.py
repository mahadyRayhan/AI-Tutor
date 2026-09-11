# backend/app/core/telemetry.py
#
# Class Trial Telemetry — centralized, append-only logging.
# ─────────────────────────────────────────────────────────
# One module that every hook calls. Depends ONLY on `db` (no agent/model
# imports) to stay free of circular dependencies.
#
# Design rules:
#   • Every log carries a stable `study_id` (resolved/auto-created from username).
#   • All writes are append-only (INSERT). Never UPDATE/DELETE here.
#   • Every logger is wrapped in try/except — telemetry must NEVER crash the app.
#   • event_log is the raw safety net; specialized tables are for convenient analysis.

import json
import logging
import random
import string
from datetime import datetime, timezone

from app.db.sqlite_db import db

logger = logging.getLogger(__name__)

# In-process cache so we don't hit the DB for every single log call
_STUDY_ID_CACHE: dict[str, str] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gen_study_id() -> str:
    suffix = "".join(random.choices(string.digits, k=4))
    return f"S-{suffix}"


def get_study_id(username: str) -> str:
    """Resolve a stable anonymized study_id for a username, creating one if needed.
    Auto-registration guarantees no log row is ever orphaned without an ID."""
    if not username:
        return "S-UNKNOWN"
    if username in _STUDY_ID_CACHE:
        return _STUDY_ID_CACHE[username]

    try:
        row = db.fetch_one(
            "SELECT study_id FROM study_participants WHERE username = ?", (username,)
        )
        if row and row["study_id"]:
            _STUDY_ID_CACHE[username] = row["study_id"]
            return row["study_id"]

        # Auto-create a unique study_id
        for _ in range(10):
            sid = _gen_study_id()
            exists = db.fetch_one(
                "SELECT 1 FROM study_participants WHERE study_id = ?", (sid,)
            )
            if not exists:
                break
        db.execute(
            "INSERT OR IGNORE INTO study_participants (study_id, username, cohort, enrolled_at) "
            "VALUES (?, ?, ?, ?)",
            (sid, username, "default", _now()),
        )
        _STUDY_ID_CACHE[username] = sid
        return sid
    except Exception as e:
        logger.warning(f"[telemetry] get_study_id failed for {username}: {e}")
        return "S-ERROR"


def register_participant(username: str, cohort: str = "default") -> str:
    """Explicitly enroll a participant (e.g., from a roster import). Idempotent."""
    sid = get_study_id(username)
    try:
        db.execute(
            "UPDATE study_participants SET cohort = ? WHERE username = ?",
            (cohort, username),
        )
    except Exception as e:
        logger.warning(f"[telemetry] register_participant failed: {e}")
    return sid


# ── Raw event stream (safety net) ──────────────────────────────────────────────

def log_event(username: str, event_type: str, payload: dict | None = None,
              session_id: str = None) -> None:
    try:
        sid = get_study_id(username)
        db.execute(
            "INSERT INTO event_log (study_id, username, session_id, event_type, payload, ts_utc) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sid, username, session_id, event_type,
             json.dumps(payload or {}), _now()),
        )
    except Exception as e:
        logger.warning(f"[telemetry] log_event failed: {e}")


# ── C1: BKT evidence + trajectory + prediction ─────────────────────────────────

def log_evidence(username: str, concept: str, tier: str, is_correct: bool,
                 question_id: str = None) -> None:
    try:
        sid = get_study_id(username)
        db.execute(
            "INSERT INTO evidence_log (study_id, username, concept, tier, is_correct, question_id, ts_utc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sid, username, concept, tier, 1 if is_correct else 0, question_id, _now()),
        )
    except Exception as e:
        logger.warning(f"[telemetry] log_evidence failed: {e}")


def log_bkt_snapshot(username: str, concept: str, tier: str, p_tilde: float,
                     n_evidence: int, is_certified: int, ever_certified: int,
                     trigger: str) -> None:
    try:
        sid = get_study_id(username)
        db.execute(
            "INSERT INTO bkt_history "
            "(study_id, username, concept, tier, p_tilde, n_evidence, is_certified, ever_certified, trigger, ts_utc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, username, concept, tier, p_tilde, n_evidence,
             is_certified, ever_certified, trigger, _now()),
        )
    except Exception as e:
        logger.warning(f"[telemetry] log_bkt_snapshot failed: {e}")


def log_prediction(username: str, concept: str, tier: str, p_bkt_pred: float,
                   p_eff_pred: float, is_correct: bool) -> None:
    """Prediction (pre-update posterior) paired with the actual outcome.
    Enables within-subject Brier/log-loss: does P_eff beat P_bkt after calibration?"""
    try:
        sid = get_study_id(username)
        db.execute(
            "INSERT INTO prediction_log "
            "(study_id, username, concept, tier, p_bkt_pred, p_eff_pred, is_correct, ts_utc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, username, concept, tier, p_bkt_pred, p_eff_pred,
             1 if is_correct else 0, _now()),
        )
    except Exception as e:
        logger.warning(f"[telemetry] log_prediction failed: {e}")


# ── C3: calibration ────────────────────────────────────────────────────────────

def log_calibration(username: str, concept: str, tier: str, p_bkt_at_time: float,
                    p_self: float, delta: float, direction_ema: float,
                    adapted_pg_old: float | None, adapted_pg_new: float | None) -> None:
    try:
        sid = get_study_id(username)
        db.execute(
            "INSERT INTO calibration_log "
            "(study_id, username, concept, tier, p_bkt_at_time, p_self, delta, direction_ema, "
            " adapted_pg_old, adapted_pg_new, ts_utc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, username, concept, tier, p_bkt_at_time, p_self, delta, direction_ema,
             adapted_pg_old, adapted_pg_new, _now()),
        )
    except Exception as e:
        logger.warning(f"[telemetry] log_calibration failed: {e}")


# ── SRL: Metacognitive Calibration Network verdicts ─────────────────────────────

def log_mcn_verdict(username: str, concept: str, verdict: dict) -> None:
    """Persist one MCN calibration inference for offline evaluation / CPT refit."""
    try:
        import json as _json
        sid = get_study_id(username)
        post = verdict.get("posterior_C", {}) or {}
        db.execute(
            "INSERT INTO mcn_log "
            "(study_id, username, concept, map_C, map_K, confidence, n_signals, "
            " p_over, p_cal, p_under, evidence_json, ts_utc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, username, concept,
             verdict.get("map_C"), verdict.get("map_K"),
             verdict.get("confidence"), verdict.get("n_signals"),
             post.get("over"), post.get("cal"), post.get("under"),
             _json.dumps(verdict.get("evidence_used", {})), _now()),
        )
    except Exception as e:
        logger.warning(f"[telemetry] log_mcn_verdict failed: {e}")


# ── SRL: JOL ───────────────────────────────────────────────────────────────────

def log_jol(username: str, concept: str, confidence_1_5: int, is_correct: bool,
            quiz_id: str = None) -> None:
    try:
        sid = get_study_id(username)
        db.execute(
            "INSERT INTO jol_log (study_id, username, concept, confidence_1_5, is_correct, quiz_id, ts_utc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sid, username, concept, confidence_1_5, 1 if is_correct else 0, quiz_id, _now()),
        )
    except Exception as e:
        logger.warning(f"[telemetry] log_jol failed: {e}")


# ── C2: response adaptation + behavior ─────────────────────────────────────────

def log_response(username: str, session_id: str, concept: str, intent: str,
                 mastery_level: str, format_sections: str = "",
                 weak_tier: str = "") -> None:
    try:
        sid = get_study_id(username)
        db.execute(
            "INSERT INTO response_log "
            "(study_id, username, session_id, concept, intent, mastery_level, "
            "format_sections, weak_tier, ts_utc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, username, session_id, concept, intent, mastery_level,
             format_sections, weak_tier, _now()),
        )
    except Exception as e:
        logger.warning(f"[telemetry] log_response failed: {e}")


def log_behavior(username: str, session_id: str, event: str, value=None,
                 message_id: str = None) -> None:
    try:
        sid = get_study_id(username)
        db.execute(
            "INSERT INTO behavior_log (study_id, username, session_id, event, value, message_id, ts_utc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sid, username, session_id, event, str(value) if value is not None else None,
             message_id, _now()),
        )
    except Exception as e:
        logger.warning(f"[telemetry] log_behavior failed: {e}")


# ── SRL: affect ────────────────────────────────────────────────────────────────

def log_affect(username: str, session_id: str, delta_f: float,
               frustration_level: str, intervention_fired: bool,
               academic_emotion: str = None) -> None:
    try:
        sid = get_study_id(username)
        db.execute(
            "INSERT INTO affect_log "
            "(study_id, username, session_id, delta_f, frustration_level, intervention_fired, "
            " academic_emotion, ts_utc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, username, session_id, delta_f, frustration_level,
             1 if intervention_fired else 0, academic_emotion, _now()),
        )
    except Exception as e:
        logger.warning(f"[telemetry] log_affect failed: {e}")


# ── Engagement: session envelope ───────────────────────────────────────────────

def touch_session(username: str, session_id: str, user_agent: str = None) -> None:
    """Upsert session envelope. First call sets started_at; every call advances
    ended_at and increments turn_count. Duration = ended_at - started_at."""
    if not session_id:
        return
    try:
        sid = get_study_id(username)
        now = _now()
        existing = db.fetch_one(
            "SELECT turn_count FROM session_log WHERE session_id = ?", (session_id,)
        )
        if existing:
            db.execute(
                "UPDATE session_log SET ended_at = ?, turn_count = turn_count + 1 "
                "WHERE session_id = ?",
                (now, session_id),
            )
        else:
            db.execute(
                "INSERT INTO session_log "
                "(session_id, study_id, username, started_at, ended_at, turn_count, user_agent) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, sid, username, now, now, 1, user_agent),
            )
    except Exception as e:
        logger.warning(f"[telemetry] touch_session failed: {e}")


# ── Full conversational record ─────────────────────────────────────────────────

def log_turn(username: str, session_id: str, turn_index: int, query_text: str,
             response_text: str, intent: str, entities, topic: str,
             mastery_level: str, s_goal: float, c_code: int, m_state: str,
             delta_f: float, n_strike: int, n_sources: int, latency_ms: int,
             was_blocked: bool, block_reason: str = None,
             traj_risk: float = None, traj_acc: float = None,
             traj_peak: float = None) -> None:
    """One row per conversational turn with the full sensory/state vector.
    The richest single artifact — enables re-classification and new angles later."""
    try:
        sid = get_study_id(username)
        ent = json.dumps(entities) if isinstance(entities, (list, dict)) else str(entities)
        db.execute(
            "INSERT INTO turn_log "
            "(study_id, username, session_id, turn_index, query_text, response_text, "
            " intent, entities, topic, mastery_level, s_goal, c_code, m_state, delta_f, "
            " n_strike, n_sources, latency_ms, was_blocked, block_reason, "
            " traj_risk, traj_acc, traj_peak, ts_utc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, username, session_id, turn_index, query_text, response_text,
             intent, ent, topic, mastery_level, s_goal, c_code, m_state, delta_f,
             n_strike, n_sources, latency_ms, 1 if was_blocked else 0, block_reason,
             traj_risk, traj_acc, traj_peak, _now()),
        )
    except Exception as e:
        logger.warning(f"[telemetry] log_turn failed: {e}")


# ── Forethought: curriculum path deviation ─────────────────────────────────────

def log_path_event(username: str, concept_asked: str, expected_concept: str,
                   deviation_type: str, goal: str = None) -> None:
    """deviation_type ∈ {on_path, skip_ahead, revisit, off_path, unknown}."""
    try:
        sid = get_study_id(username)
        db.execute(
            "INSERT INTO path_event "
            "(study_id, username, concept_asked, expected_concept, deviation_type, goal, ts_utc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sid, username, concept_asked, expected_concept, deviation_type, goal, _now()),
        )
    except Exception as e:
        logger.warning(f"[telemetry] log_path_event failed: {e}")


def log_cac_access(username: str, session_id: str, concept_asked: str,
                   decision, deviation_type: str = None) -> None:
    """Record one Cognitive Access Control decision.

    Called on every turn CAC has an opinion about, including the in-region ones.
    Logging only the probes would make the probe RATE uncomputable — a count of
    out-of-region asks means nothing without the count of turns that were fine.

    `decision` is a cac_graph.Decision. Never raises: this is observability, and
    an analytics failure must not cost the learner their turn.
    """
    try:
        from app.core import config
        sid = get_study_id(username)
        a = decision.audit()
        # `in_region` is read from beyond_region, NOT inferred from "did CAC say
        # anything". The inferred form was correct only while the region check was
        # the sole thing that could speak; from Phase 3 a horizon contraction also
        # produces reasons, and pooling the two would have silently mixed "reached
        # past the frontier" with "was overloaded" under a column named for the
        # first. Region and horizon are stored separately because they are separate
        # findings, and E14 reads one of them.
        edge = a.get("revealed_edge")
        db.execute(
            "INSERT INTO cac_access_event "
            "(study_id, username, session_id, concept_asked, in_region, in_horizon, "
            " revealed_edge, redirect_to, rung_cap, deviation_type, reasons, "
            " ablation_config, policy_version, ts_utc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, username, session_id, concept_asked,
             0 if a.get("beyond_region") else 1,
             1 if a.get("in_horizon", True) else 0,
             json.dumps(edge) if edge else None,
             a["redirect_to"], a["rung_cap"], deviation_type,
             json.dumps(a["reasons"]), json.dumps(config.active_ablation_config()),
             # The load thresholds are calibrated from the population and move
             # between versions, so the decision is only reconstructable if the
             # row names the version that produced it. Replay reads this, not
             # the live constants.
             a.get("policy_version"),
             _now()),
        )
    except Exception as e:
        logger.warning(f"[telemetry] log_cac_access failed: {e}")


def cac_probe_stats(username: str, session_id: str = None) -> dict:
    """Out-of-region reach attempts, for the A1 adversary and the E7 detector.

    Scoped to one session when `session_id` is given, else the whole account.
    Returns a rate as well as a count: a student who asks a hundred questions and
    over-reaches twice is not the same as one who over-reaches on both of their
    only two turns, and a bare count cannot tell them apart.

    Counts REGION probes only. A horizon contraction is a statement about the
    learner's load, not about them reaching past what they have earned, so it must
    not inflate an adversary signal — an overloaded honest student is not an A1.
    """
    try:
        where = "username=?"
        params = [username]
        if session_id:
            where += " AND session_id=?"
            params.append(session_id)
        row = db.fetch_one(
            f"SELECT COUNT(*) AS n, "
            f"       COALESCE(SUM(CASE WHEN in_region=0 THEN 1 ELSE 0 END), 0) AS probes "
            f"FROM cac_access_event WHERE {where}", tuple(params))
        n = int(row["n"] or 0) if row else 0
        probes = int(row["probes"] or 0) if row else 0
        return {"turns": n, "probes": probes,
                "probe_rate": round(probes / n, 3) if n else 0.0}
    except Exception as e:
        logger.warning(f"[telemetry] cac_probe_stats failed: {e}")
        return {"turns": 0, "probes": 0, "probe_rate": 0.0}


def classify_path_deviation(path: list, concept_asked: str) -> tuple:
    """Given a learning path (list of {concept,status}) and the asked concept,
    return (deviation_type, expected_concept). Pure function, no DB."""
    if not path:
        return "unknown", None
    expected = next((n.get("concept") for n in path if n.get("status") == "next"), None)
    asked_l = (concept_asked or "").lower().strip()
    for node in path:
        c = (node.get("concept") or "").lower().strip()
        if c and (c == asked_l or c in asked_l or asked_l in c):
            status = node.get("status")
            if status == "next":
                return "on_path", expected
            if status == "locked":
                return "skip_ahead", expected
            if status == "mastered":
                return "revisit", expected
    return "off_path", expected


# ── Item-level quiz record ─────────────────────────────────────────────────────

def log_quiz_item(username: str, session_id: str, concept: str, question_text: str,
                  correct_answer: str, student_answer: str, is_correct: bool,
                  confidence_1_5: int, time_to_answer_sec: float,
                  is_verification: bool, verification_q_num: int) -> None:
    """Full item-level quiz record for psychometric analysis."""
    try:
        sid = get_study_id(username)
        db.execute(
            "INSERT INTO quiz_log "
            "(study_id, username, session_id, concept, question_text, correct_answer, "
            " student_answer, is_correct, confidence_1_5, time_to_answer_sec, "
            " is_verification, verification_q_num, ts_utc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, username, session_id, concept, question_text, correct_answer,
             student_answer, 1 if is_correct else 0, confidence_1_5, time_to_answer_sec,
             1 if is_verification else 0, verification_q_num, _now()),
        )
    except Exception as e:
        logger.warning(f"[telemetry] log_quiz_item failed: {e}")


# ── Learning-from-error: misconceptions ────────────────────────────────────────

def log_misconception(username: str, concept: str, action: str,
                      detail: str = "", ema_value: float = None) -> None:
    """action ∈ {stored, resolved}. Tracks the learning-from-error trajectory."""
    try:
        sid = get_study_id(username)
        db.execute(
            "INSERT INTO misconception_log "
            "(study_id, username, concept, action, detail, ema_value, ts_utc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sid, username, concept, action, detail, ema_value, _now()),
        )
    except Exception as e:
        logger.warning(f"[telemetry] log_misconception failed: {e}")


# ── Prerequisite-coupled priors ("head start") ─────────────────────────────────

def log_headstart(username: str, concept: str, action: str,
                  hs: dict, seeds: dict, sources: list) -> None:
    """action ∈ {seed, clawback}. Records every head-start seed/clawback so analysis
    can prove a head start never certified a topic (it carries zero evidence)."""
    try:
        import json as _json
        sid = get_study_id(username)
        db.execute(
            "INSERT INTO headstart_log "
            "(study_id, username, concept, action, hs_quiz, hs_micro, hs_code, "
            " seed_quiz, seed_micro, seed_code, sources, ts_utc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, username, concept, action,
             hs.get("quiz"), hs.get("micro"), hs.get("code"),
             seeds.get("quiz"), seeds.get("micro"), seeds.get("code"),
             _json.dumps(sources), _now()),
        )
    except Exception as e:
        logger.warning(f"[telemetry] log_headstart failed: {e}")


# ── Classroom video: engagement, coverage, MCQ, reflection ─────────────────────

def log_video_engagement(username: str, video_filename: str, event: str,
                         position_sec: float = None, detail: str = None) -> None:
    """event ∈ {play, pause, seek, ended, tab_hidden, tab_visible, mute, unmute}."""
    try:
        sid = get_study_id(username)
        db.execute(
            "INSERT INTO video_engagement "
            "(study_id, username, video_filename, event, position_sec, detail, ts_utc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sid, username, video_filename, event, position_sec, detail, _now()),
        )
    except Exception as e:
        logger.warning(f"[telemetry] log_video_engagement failed: {e}")


def update_video_coverage(username: str, video_filename: str, duration_sec: float,
                          watched_sec: float, hidden_sec: float) -> None:
    """Upsert per-user coverage summary. completion_pct/completed derived here."""
    try:
        sid = get_study_id(username)
        pct = round(min(watched_sec / duration_sec, 1.0) * 100, 1) if duration_sec else 0.0
        completed = 1 if pct >= 90 else 0
        db.execute(
            "INSERT INTO video_coverage "
            "(study_id, username, video_filename, duration_sec, watched_sec, "
            " completion_pct, hidden_sec, completed, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(username, video_filename) DO UPDATE SET "
            "  duration_sec=excluded.duration_sec, watched_sec=excluded.watched_sec, "
            "  completion_pct=excluded.completion_pct, hidden_sec=excluded.hidden_sec, "
            "  completed=excluded.completed, updated_at=excluded.updated_at",
            (sid, username, video_filename, duration_sec, round(watched_sec, 1),
             pct, round(hidden_sec, 1), completed, _now()),
        )
    except Exception as e:
        logger.warning(f"[telemetry] update_video_coverage failed: {e}")


def log_video_mcq(username: str, video_filename: str, checkpoint_time: float,
                  concept: str, selected_index: int, is_correct: bool,
                  confidence_1_5: int, time_to_answer_sec: float, attempts: int) -> None:
    try:
        sid = get_study_id(username)
        db.execute(
            "INSERT INTO video_mcq_response "
            "(study_id, username, video_filename, checkpoint_time, concept, selected_index, "
            " is_correct, confidence_1_5, time_to_answer_sec, attempts, ts_utc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, username, video_filename, checkpoint_time, concept, selected_index,
             1 if is_correct else 0, confidence_1_5, time_to_answer_sec, attempts, _now()),
        )
    except Exception as e:
        logger.warning(f"[telemetry] log_video_mcq failed: {e}")


def log_video_reflection(username: str, video_filename: str, phase: str,
                         prompt: str, response: str) -> None:
    """phase ∈ {intention (pre), reflection (post)}."""
    try:
        sid = get_study_id(username)
        db.execute(
            "INSERT INTO video_reflection "
            "(study_id, username, video_filename, phase, prompt, response, ts_utc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sid, username, video_filename, phase, prompt, response, _now()),
        )
    except Exception as e:
        logger.warning(f"[telemetry] log_video_reflection failed: {e}")


# ── Motivational self-report (Tier 2) ──────────────────────────────────────────

def log_self_report(username: str, dimension: str, item_id: str, score: float) -> None:
    """dimension ∈ {self_efficacy, interest, goal_orientation}. score is a Likert value."""
    try:
        sid = get_study_id(username)
        db.execute(
            "INSERT INTO self_report (study_id, username, dimension, item_id, score, ts_utc) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sid, username, dimension, item_id, score, _now()),
        )
    except Exception as e:
        logger.warning(f"[telemetry] log_self_report failed: {e}")


# ── Ground truth: assessment import ────────────────────────────────────────────

def log_assessment(study_id: str, instrument: str, item_id: str, bloom_tier: str,
                   concept: str, score: float) -> None:
    """Import a pre/post test or MAI item. Keyed directly by study_id (from roster)."""
    try:
        db.execute(
            "INSERT INTO assessment (study_id, instrument, item_id, bloom_tier, concept, score, ts_utc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (study_id, instrument, item_id, bloom_tier, concept, score, _now()),
        )
    except Exception as e:
        logger.warning(f"[telemetry] log_assessment failed: {e}")
