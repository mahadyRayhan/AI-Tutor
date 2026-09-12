# backend/app/core/learner_model.py
#
# Unified Multidimensional Learner Model
# ──────────────────────────────────────
# Assembles a single learner profile across four dimensions — Cognitive,
# Metacognitive, Affective, Motivational — following the Azevedo learner-model
# taxonomy. Most variables are DERIVED from telemetry already collected
# (Tier 1); motivational variables come from self-report (Tier 2); confusion/
# boredom and comprehension/debugging come from Tier 3 signals.
#
# Design: pure read model. It never writes learner state — it reads the logs and
# the BKT state and returns a structured profile the pedagogical layer can use.

import json
import logging
from app.db.sqlite_db import db
from app.core.bkt_model import bkt, _read_row, EVIDENCE_CONFIG

logger = logging.getLogger(__name__)

# Recency window (most-recent N rows) used by the derivations
_N = 200
_SURRENDER = ("i don't know", "idk", "skip", "no idea", "i dont know", "no clue", "pass")


def _rows(sql, params):
    try:
        return db.fetch_all(sql, params) or []
    except Exception:
        return []


def _one(sql, params, default=0.0):
    try:
        r = db.fetch_one(sql, params)
        v = r[0] if r else None
        return v if v is not None else default
    except Exception:
        return default


def _clip01(x):
    return max(0.0, min(1.0, x))


# ── COGNITIVE ──────────────────────────────────────────────────────────────────

def _concept_mastery(username):
    """Per-concept composite mastery (BKT), plus a class-wide average."""
    rows = _rows("SELECT DISTINCT concept FROM user_knowledge WHERE username=?", (username,))
    out = {}
    for r in rows:
        c = r["concept"]
        out[c] = round(bkt.get_mastery(username, c), 3)
    avg = round(sum(out.values()) / len(out), 3) if out else 0.0
    return {"per_concept": out, "average": avg, "n_concepts": len(out)}


def _cognitive_load(username):
    """Composite load from code complexity, latency, and recent error rate (Tier 1)."""
    row = db.fetch_one(
        "SELECT AVG(c_code) cc, AVG(latency_ms) lat FROM "
        "(SELECT c_code, latency_ms FROM turn_log WHERE username=? ORDER BY id DESC LIMIT ?)",
        (username, _N))
    cc = (row["cc"] or 0.0) if row else 0.0
    lat = (row["lat"] or 0.0) if row else 0.0
    err = _one("SELECT AVG(1.0 - is_correct) FROM "
               "(SELECT is_correct FROM evidence_log WHERE username=? ORDER BY id DESC LIMIT ?)",
               (username, _N))
    # Normalize each component to [0,1] with soft caps, then weight.
    cc_n = _clip01(cc / 8.0)         # code-complexity proxy
    lat_n = _clip01(lat / 20000.0)  # 20s ceiling
    load = round(_clip01(0.4 * cc_n + 0.3 * lat_n + 0.3 * err), 3)
    return {"index": load, "avg_complexity": round(cc, 2),
            "avg_latency_ms": round(lat, 0), "recent_error_rate": round(err, 3)}


def _slip_vs_gap(username):
    """Classify incorrect answers: high confidence = knowledge gap, low = careless slip."""
    rows = _rows("SELECT confidence_1_5, is_correct FROM jol_log WHERE username=? "
                 "ORDER BY id DESC LIMIT ?", (username, _N))
    slips = gaps = wrong = 0
    for r in rows:
        if r["is_correct"] == 0:
            wrong += 1
            if (r["confidence_1_5"] or 3) >= 4:
                gaps += 1
            elif (r["confidence_1_5"] or 3) <= 2:
                slips += 1
    return {"n_wrong": wrong, "n_gap": gaps, "n_slip": slips,
            "gap_ratio": round(gaps / wrong, 3) if wrong else 0.0}


# A prediction this confident that still came back wrong is a calibration
# failure, not noise. 0.7 rather than 0.9: the interesting case is "the model
# thought this learner had it", and requiring near-certainty would discard most
# of the signal on a live BKT that rarely saturates.
P_CONFIDENT = 0.7


def _calibration(username):
    """Model-side overconfidence: wrong while the model expected right (Tier 1).

    The companion to `_slip_vs_gap`, and the one CAC should prefer. Both answer
    "was this learner wrong AND sure of themselves", but they source the
    confidence differently, and the difference matters twice over:

    1. COVERAGE. `_slip_vs_gap` reads `jol_log.confidence_1_5`, written only
       inside the pop-quiz path — 6 rows across 3 learners in the current
       corpus, against 152 predictions across 26. The self-report is not merely
       sparse, it is STRUCTURALLY sparse: the quiz trigger requires a <=4-word
       acknowledgement turn, which is 0.34% of student messages, so jol_log
       stays empty however much traffic arrives.

    2. MANIPULABILITY. `confidence_1_5` is declared by the subject of the
       policy. A learner who works out that reporting high confidence tightens
       their prerequisite gate simply stops reporting it, and the signal decays
       to noise exactly for the learners it was meant to catch. `p_bkt_pred` is
       computed from their behaviour and is never shown to them. An inferred
       attribute the subject controls is a weak basis for access control.
    """
    rows = _rows("SELECT p_bkt_pred, is_correct FROM prediction_log "
                 "WHERE username=? ORDER BY id DESC LIMIT ?", (username, _N))
    wrong = conf_wrong = 0
    for r in rows:
        if r["is_correct"] == 0:
            wrong += 1
            if (r["p_bkt_pred"] or 0.0) >= P_CONFIDENT:
                conf_wrong += 1
    return {"n": len(rows), "n_wrong": wrong, "n_confident_wrong": conf_wrong,
            "gap_ratio": round(conf_wrong / wrong, 3) if wrong else 0.0,
            "source": "model"}


def _automatization(username):
    """Negative latency trend on repeated quiz items = skill becoming automatic."""
    rows = _rows("SELECT time_to_answer_sec FROM quiz_log WHERE username=? AND is_correct=1 "
                 "AND time_to_answer_sec IS NOT NULL ORDER BY id ASC LIMIT ?", (username, _N))
    ts = [r["time_to_answer_sec"] for r in rows if r["time_to_answer_sec"]]
    if len(ts) < 4:
        return {"trend": 0.0, "n": len(ts), "status": "insufficient"}
    n = len(ts); xs = list(range(n))
    mx = sum(xs) / n; my = sum(ts) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ts))
    den = sum((x - mx) ** 2 for x in xs) or 1.0
    slope = num / den  # seconds per trial
    return {"trend": round(slope, 3), "n": n,
            "status": "automatizing" if slope < -0.05 else "stable"}


def _comprehension_tracing(username):
    """Tier 3: accuracy on video checkpoint (predict/understand) MCQs."""
    total = int(_one("SELECT COUNT(*) FROM video_mcq_response WHERE username=?", (username,)))
    correct = int(_one("SELECT COALESCE(SUM(is_correct),0) FROM video_mcq_response WHERE username=?", (username,)))
    return {"accuracy": round(correct / total, 3) if total else None, "n": total}


def _debugging_skill(username):
    """Tier 3: resolution of DEBUG turns — a DEBUG turn not followed by another
    DEBUG on the same concept counts as resolved."""
    rows = _rows("SELECT intent, topic FROM turn_log WHERE username=? ORDER BY id ASC LIMIT ?",
                 (username, _N))
    debug_idx = [i for i, r in enumerate(rows) if (r["intent"] or "") == "DEBUG"]
    if not debug_idx:
        return {"resolution_rate": None, "n_debug": 0}
    resolved = 0
    for i in debug_idx:
        nxt = rows[i + 1] if i + 1 < len(rows) else None
        if not nxt or (nxt["intent"] or "") != "DEBUG":
            resolved += 1
    return {"resolution_rate": round(resolved / len(debug_idx), 3), "n_debug": len(debug_idx)}


# ── METACOGNITIVE ───────────────────────────────────────────────────────────────

def _self_monitoring(username):
    """Calibration: (a) JOL confidence vs correctness; (b) slider self-assessment vs BKT."""
    rows = _rows("SELECT confidence_1_5, is_correct FROM jol_log WHERE username=? "
                 "ORDER BY id DESC LIMIT ?", (username, _N))
    # Brier-style calibration error between normalized confidence and outcome
    errs = []
    for r in rows:
        conf = ((r["confidence_1_5"] or 3) - 1) / 4.0  # → [0,1]
        errs.append((conf - (r["is_correct"] or 0)) ** 2)
    jol_cal = round(1.0 - (sum(errs) / len(errs)), 3) if errs else None  # 1 = perfectly calibrated
    n_slider = int(_one("SELECT COUNT(*) FROM calibration_log WHERE username=?", (username,)))
    return {"jol_calibration": jol_cal, "n_jol": len(rows), "n_slider_adjust": n_slider}


def _help_seeking(username):
    """Frequency + appropriateness of giving up / seeking help (Tier 1)."""
    n_skip = int(_one("SELECT COUNT(*) FROM event_log WHERE username=? AND event_type='quiz_skip'", (username,)))
    n_quiz = int(_one("SELECT COUNT(*) FROM quiz_log WHERE username=?", (username,)))
    # "Immediate give-up": a surrender with very short time-to-answer = poor help-seeking
    immediate = int(_one(
        "SELECT COUNT(*) FROM quiz_log WHERE username=? AND time_to_answer_sec IS NOT NULL "
        "AND time_to_answer_sec < 5 AND lower(student_answer) IN ({})".format(
            ",".join("?" * len(_SURRENDER))),
        (username, *_SURRENDER)))
    rate = round(n_skip / (n_quiz + n_skip), 3) if (n_quiz + n_skip) else 0.0
    return {"n_skip": n_skip, "skip_rate": rate, "n_immediate_giveup": immediate,
            "quality": "impulsive" if immediate > 0 and rate > 0.3 else "measured"}


def _persistence(username):
    """Retries before success vs give-ups (Tier 1 / motivational-adjacent)."""
    avg_attempts = _one("SELECT AVG(attempts) FROM video_mcq_response WHERE username=?", (username,))
    n_giveup = int(_one("SELECT COUNT(*) FROM event_log WHERE username=? AND event_type='quiz_skip'", (username,)))
    n_solve = int(_one("SELECT COUNT(*) FROM evidence_log WHERE username=? AND is_correct=1", (username,)))
    give_up_rate = round(n_giveup / (n_giveup + n_solve), 3) if (n_giveup + n_solve) else 0.0
    return {"avg_attempts": round(avg_attempts, 2), "give_up_rate": give_up_rate,
            "grit_index": round(_clip01(1.0 - give_up_rate), 3)}


def _pacing(username):
    """Time allocation: rushing = low latency on low-mastery work (Tier 1)."""
    row = db.fetch_one(
        "SELECT AVG(latency_ms) lat, AVG(delta_f) df FROM "
        "(SELECT latency_ms, delta_f FROM turn_log WHERE username=? ORDER BY id DESC LIMIT ?)",
        (username, _N))
    lat = (row["lat"] or 0.0) if row else 0.0
    mastery = _concept_mastery(username)["average"]
    # Rushing flag: fast responses despite low mastery
    rushing = (lat < 4000 and mastery < 0.4)
    return {"avg_latency_ms": round(lat, 0), "rushing": bool(rushing),
            "status": "rushing" if rushing else "deliberate"}


def _path_adherence(username):
    """Forethought: on-path vs deviation from the skill tree."""
    rows = _rows("SELECT deviation_type, COUNT(*) c FROM path_event WHERE username=? "
                 "GROUP BY deviation_type", (username,))
    dist = {r["deviation_type"]: r["c"] for r in rows}
    total = sum(dist.values())
    on_path = dist.get("on_path", 0)
    return {"distribution": dist, "adherence": round(on_path / total, 3) if total else None}


# ── AFFECTIVE ───────────────────────────────────────────────────────────────────

def _affect(username, profile):
    """Frustration + Tier 3 confusion/boredom tally + flow estimate."""
    tally = profile.get("emotion_counts", {})
    total = sum(tally.values()) or 1
    dist = {k: round(v / total, 3) for k, v in tally.items()}
    frustration = profile.get("frustration_level", "normal")
    delta_f = profile.get("delta_f", 0.0)
    return {"frustration_level": frustration, "delta_f": delta_f,
            "emotion_distribution": dist,
            "confusion_rate": dist.get("confusion", 0.0),
            "boredom_rate": dist.get("boredom", 0.0)}


def _flow(username):
    """Flow = challenge–skill match. Challenge from recent code complexity, skill from mastery."""
    cc = _one("SELECT AVG(c_code) FROM (SELECT c_code FROM turn_log WHERE username=? ORDER BY id DESC LIMIT 50)",
              (username,))
    challenge = _clip01(cc / 8.0)
    skill = _clip01(_concept_mastery(username)["average"] / 0.95)
    flow = round(1.0 - abs(challenge - skill), 3)  # 1 = well matched
    zone = "flow" if flow >= 0.7 else ("anxiety" if challenge > skill else "boredom")
    return {"flow_index": flow, "challenge": round(challenge, 3),
            "skill": round(skill, 3), "zone": zone}


# ── MOTIVATIONAL (Tier 2 self-report + derived) ─────────────────────────────────

def _self_report_dim(username, dimension):
    v = _one("SELECT AVG(score) FROM self_report WHERE username=? AND dimension=?",
             (username, dimension), default=None)
    n = int(_one("SELECT COUNT(*) FROM self_report WHERE username=? AND dimension=?",
                 (username, dimension)))
    return {"value": round(v, 3) if v is not None else None, "n_items": n}


def _engagement(username):
    """Time-on-task from session envelopes."""
    row = db.fetch_one(
        "SELECT COUNT(*) ns, COALESCE(SUM(turn_count),0) turns FROM session_log WHERE username=?",
        (username,))
    return {"n_sessions": (row["ns"] or 0) if row else 0,
            "total_turns": (row["turns"] or 0) if row else 0}


# ── ASSEMBLY ────────────────────────────────────────────────────────────────────

def get_learner_profile(username: str) -> dict:
    """Return the full multidimensional learner profile for a student."""
    prow = db.fetch_one("SELECT learning_profile FROM users WHERE username=?", (username,))
    profile = json.loads(prow["learning_profile"]) if prow and prow["learning_profile"] else {}

    # Misconceptions (existing EMA model)
    miscon = profile.get("misconceptions", [])
    active_miscon = [m for m in miscon if not m.get("resolved", True)]

    return {
        "username": username,
        "cognitive": {
            "concept_mastery": _concept_mastery(username),
            "active_misconceptions": [m.get("concept") for m in active_miscon],
            "cognitive_load": _cognitive_load(username),
            "error_slip_vs_gap": _slip_vs_gap(username),   # self-reported
            "calibration": _calibration(username),          # model-side; CAC prefers this
            "automatization": _automatization(username),
            "comprehension_tracing": _comprehension_tracing(username),
            "debugging_skill": _debugging_skill(username),
        },
        "metacognitive": {
            "self_monitoring": _self_monitoring(username),
            "help_seeking": _help_seeking(username),
            "persistence": _persistence(username),
            "pacing": _pacing(username),
            "path_adherence": _path_adherence(username),
        },
        "affective": {
            **_affect(username, profile),
            "flow": _flow(username),
        },
        "motivational": {
            "self_efficacy": _self_report_dim(username, "self_efficacy"),
            "interest": _self_report_dim(username, "interest"),
            "goal_orientation": _self_report_dim(username, "goal_orientation"),
            "engagement": _engagement(username),
        },
    }
