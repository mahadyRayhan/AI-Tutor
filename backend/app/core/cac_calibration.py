"""Empirical calibration of the CAC cognitive-load thresholds.

WHY THIS EXISTS
---------------
`LOAD_HORIZON`'s original thresholds (0.7 -> 1 hop, 0.5 -> 2 hops) were chosen
because `cognitive_load` is scaled 0..1 and 0.5 reads like "half loaded". The
population says otherwise: across 630 accounts the p99 load is 0.197 and the
maximum is 0.415, so neither threshold was reachable by anyone and the horizon
signal could never fire. A number that no learner can cross is not a policy.

So the thresholds should come from the population. The rest of this module is
about doing that WITHOUT handing users a lever on the policy itself.

THE ATTACK THIS IS BUILT AGAINST
--------------------------------
A percentile threshold makes one learner's outcome depend on what other accounts
did. Flooding the population with high-load traffic drags the cut point upward
until a genuinely overloaded learner looks ordinary and stops being contracted;
flooding with low-load traffic drags it down until everyone is contracted.

Note what does and does not break. `_signal_cognitive_load` still only ever calls
`_tighten()`, so `M(C,K,S) subset-of M(C,K)` holds however the threshold moves —
cognitive state cannot widen a decision. What poisoning buys is that the
narrowing simply STOPS FIRING, driving M(C,K,S) toward equality with M(C,K).
The invariant survives and the mechanism becomes a no-op, which is harder to
notice than a violation would be: every audit row reads "no contraction", which
is exactly what a compliant system also looks like.

The guards below bound that. None of them make poisoning impossible; together
they make it slow, bounded, and legible in the version history.

WHAT IS STORED, AND WHY VERSIONS
--------------------------------
Thresholds are never mutated in place. Each calibration run appends a row to
`cac_policy_version`, and every `cac_access_event` carries the version that
produced it. A silently-moving threshold would make the audit log stop being
evidence: replaying a week-old event against today's constants reports a
decision the system never actually made. With the stamp, replay reads the
thresholds off the row and reconstructs the decision that really happened.

Rejected runs are recorded too, and consume a version number. "We looked and
declined to move" is a finding; leaving it out would make the history look like
nothing happened.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ── Guards ──────────────────────────────────────────────────────────────────
# Each one closes a specific route from "attacker generates traffic" to
# "threshold moves somewhere useful to them".

# Eligibility. Load is an average over a learner's last 20 turns; below this
# much history the average is noise, and a percentile over noise is not a
# policy. The current corpus has 17 accounts with >=10 turns, so this gate is
# what keeps today's thin data from being promoted into policy.
MIN_STUDENTS = 50
MIN_TURNS_PER_STUDENT = 10

# One vote per student. The percentile runs over per-USER load values, never
# over per-turn rows: with per-turn aggregation a single high-volume account
# dominates the distribution by itself, which is the cheapest possible attack.
# (Enforced structurally by _population(), not by a flag.)

# Absolute band. The outer backstop that holds when every other guard is wrong.
CLAMP_LO, CLAMP_HI = 0.15, 0.60

# ...but the absolute band alone is NOT enough, and the reason is the whole point
# of this module. 0.60 was picked the same intuitive way 0.5 originally was, and
# a patient attacker reaches it in ~10 runs. At 0.60 the threshold sits above
# anything the real population produces (observed max 0.415), so a "bounded"
# attack still ends with the mechanism dead — the exact failure this module was
# written to repair.
#
# So the OPERATIVE band is anchored to the first data-backed calibration, which
# is measured rather than guessed. An attacker may move the threshold at most
# CEILING_FACTOR above the honest baseline and at most FLOOR_FACTOR below it,
# whatever that baseline turns out to be on the real population. The anchor is
# read from the earliest calibrated version and never recomputed, so later
# poisoned runs cannot move the thing that bounds them.
CEILING_FACTOR = 1.5
FLOOR_FACTOR = 0.5

# Rate limit. A sustained attack can still walk the line, but only this far per
# run, which turns a jump into a staircase that is visible in the history.
MAX_STEP = 0.05

# Anomaly hold. A candidate this far out is more likely a data incident than a
# population shift, so it is refused outright rather than absorbed a step at a
# time.
ANOMALY_FACTOR = 2.0

# Where the cut points sit in the population. Phrased as the policy question
# ("what fraction should be contracted?") rather than as a magnitude, because
# the magnitude is an artifact of three arbitrary divisors inside
# `learner_model._cognitive_load` and means nothing on its own.
PCT_T1 = 98.0    # top ~2%  -> horizon 1 hop
PCT_T2 = 90.0    # top ~10% -> horizon 2 hops

# Seeded v1. Unchanged from the original constants so that landing this module
# alters no decision until real data promotes a successor.
DEFAULT_T1, DEFAULT_T2 = 0.7, 0.5

# How often the background job runs, in CAC-evaluated turns.
RECALIBRATE_EVERY = int(os.getenv("SAGE_CAC_RECALIBRATE_EVERY", "100"))

# Accounts generated by the evaluation and red-team harnesses. These behave
# nothing like students by construction — the irleval_* accounts exist to be
# blocked — so letting them into the percentile would be calibrating the policy
# against our own test fixtures. The primary filter is users.role='student';
# this list removes harness accounts that are registered as students anyway.
EXCLUDED_PREFIXES = tuple(
    p for p in os.getenv(
        "SAGE_CAC_CALIB_EXCLUDE",
        "irleval_,irl6_,trk,trickle_,gate,qa_,aud_,sec_,cur_",
    ).split(",") if p
)

# ── Active policy cache ─────────────────────────────────────────────────────
# `cac_graph.decide()` is documented as pure and must stay that way, so it reads
# this module global rather than the database. It is populated at startup and
# after each promotion. Left as None — in tests, or before startup — callers
# fall back to cac_graph.LOAD_HORIZON, so an unconfigured process behaves
# exactly as it did before this module existed.
ACTIVE: dict | None = None

_lock = threading.Lock()
_turns_since_run = 0
_running = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Reading the active version ──────────────────────────────────────────────

def refresh() -> dict | None:
    """Reload the active policy row into `ACTIVE`. Safe to call anywhere."""
    global ACTIVE
    try:
        from app.db.sqlite_db import db
        row = db.fetch_one(
            "SELECT version, load_t1, load_t2, calibrated FROM cac_policy_version "
            "WHERE status='active' ORDER BY version DESC LIMIT 1")
        if row is None:
            ACTIVE = seed_default()
        else:
            ACTIVE = {"version": int(row["version"]),
                      "load_t1": float(row["load_t1"]),
                      "load_t2": float(row["load_t2"]),
                      "calibrated": bool(row["calibrated"])}
    except Exception as e:
        logger.warning(f"[cac-calib] refresh failed, keeping defaults: {e}")
    return ACTIVE


def active() -> dict:
    """The active thresholds, loading them once if startup has not run yet."""
    if ACTIVE is None:
        refresh()
    return ACTIVE or {"version": 1, "load_t1": DEFAULT_T1,
                      "load_t2": DEFAULT_T2, "calibrated": False}


def active_version() -> int:
    """Version stamp for the audit row. Never raises — this is observability."""
    try:
        return int((ACTIVE or {}).get("version", 1))
    except Exception:
        return 1


def seed_default() -> dict:
    """Write v1 (the declared, uncalibrated default) if no version exists."""
    from app.db.sqlite_db import db
    db.execute(
        "INSERT INTO cac_policy_version "
        "(created_at, load_t1, load_t2, n_students, n_turns, calibrated, status, reason) "
        "VALUES (?, ?, ?, 0, 0, 0, 'active', ?)",
        (_now(), DEFAULT_T1, DEFAULT_T2,
         "seeded default; declared, not measured"))
    row = db.fetch_one("SELECT version FROM cac_policy_version "
                       "WHERE status='active' ORDER BY version DESC LIMIT 1")
    return {"version": int(row["version"]) if row else 1,
            "load_t1": DEFAULT_T1, "load_t2": DEFAULT_T2, "calibrated": False}


# ── Building a candidate from the population ────────────────────────────────

def _population() -> list[tuple[str, float]]:
    """Per-student load values eligible to set policy.

    ONE VALUE PER STUDENT. This is the structural form of the "one vote each"
    guard: a percentile over per-turn rows would let a single high-volume
    account set the cut point on its own, and that is the cheapest attack on
    this whole design. Aggregating first makes an attacker pay for an account
    per vote instead of a request per vote.

    Load is read through `learner_model._cognitive_load` rather than
    reimplemented here. Calibration must measure exactly what enforcement
    measures; a second copy of the formula would drift from the first and the
    thresholds would quietly stop describing the thing they gate.
    """
    from app.db.sqlite_db import db
    from app.core.learner_model import _cognitive_load

    rows = db.fetch_all(
        "SELECT t.username AS u, COUNT(*) AS n FROM turn_log t "
        "JOIN users s ON s.username = t.username AND s.role = 'student' "
        "GROUP BY t.username HAVING n >= ?", (MIN_TURNS_PER_STUDENT,))

    out = []
    for r in rows:
        u = r["u"]
        if any(u.startswith(p) for p in EXCLUDED_PREFIXES):
            continue
        try:
            out.append((u, float(_cognitive_load(u)["index"])))
        except Exception as e:
            logger.debug(f"[cac-calib] load unavailable for {u}: {e}")
    return out


def _percentile(values: list[float], p: float) -> float:
    """Linear-interpolated percentile. `values` need not be sorted."""
    v = sorted(values)
    if not v:
        return 0.0
    k = (len(v) - 1) * p / 100.0
    f = int(k)
    if f + 1 >= len(v):
        return v[-1]
    return v[f] + (v[f + 1] - v[f]) * (k - f)


def _baseline() -> tuple[float, float] | None:
    """Thresholds of the FIRST data-backed calibration, or None if there is none.

    Deliberately the earliest calibrated version rather than the current one:
    the current one may already have been walked by a slow attack, and a ceiling
    that drifts with the thing it is bounding bounds nothing.
    """
    try:
        from app.db.sqlite_db import db
        row = db.fetch_one(
            "SELECT load_t1, load_t2 FROM cac_policy_version "
            "WHERE calibrated=1 AND status IN ('active','superseded') "
            "ORDER BY version ASC LIMIT 1")
        return (float(row["load_t1"]), float(row["load_t2"])) if row else None
    except Exception:
        return None


def _band() -> tuple[float, float]:
    """The (lo, hi) thresholds may occupy: absolute band, tightened by the anchor."""
    lo, hi = CLAMP_LO, CLAMP_HI
    base = _baseline()
    if base:
        hi = min(hi, base[0] * CEILING_FACTOR)
        lo = max(lo, base[1] * FLOOR_FACTOR)
        if lo > hi:                      # degenerate anchor; absolute band wins
            lo, hi = CLAMP_LO, CLAMP_HI
    return lo, hi


def _clamp(x: float, band: tuple[float, float] | None = None) -> float:
    lo, hi = band or (CLAMP_LO, CLAMP_HI)
    return max(lo, min(hi, x))


def _limit_step(current: float, target: float) -> float:
    """Move `current` toward `target` by at most MAX_STEP."""
    delta = target - current
    if abs(delta) <= MAX_STEP:
        return target
    return current + (MAX_STEP if delta > 0 else -MAX_STEP)


def _record(status: str, reason: str, *, t1: float, t2: float,
            n_students: int, n_turns: int, calibrated: bool) -> int:
    """Append a version row and return its number. Rejections land here too."""
    from app.db.sqlite_db import db
    cur = db.execute(
        "INSERT INTO cac_policy_version "
        "(created_at, load_t1, load_t2, n_students, n_turns, calibrated, status, reason) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (_now(), round(t1, 4), round(t2, 4), n_students, n_turns,
         1 if calibrated else 0, status, reason))
    return int(cur.lastrowid)


def recalibrate() -> dict:
    """One calibration run. Promotes a new version, or records why it did not.

    Always writes a row. Returns the outcome as a dict for the caller to log.
    Never raises: a calibration fault must leave the previous version in force,
    which is the safe direction for a layer that can only narrow.
    """
    from app.db.sqlite_db import db

    try:
        cur = active()
        pop = _population()
        n = len(pop)

        # ── Guard 1: eligibility ────────────────────────────────────────────
        if n < MIN_STUDENTS:
            reason = (f"insufficient basis: {n} students with "
                      f">={MIN_TURNS_PER_STUDENT} turns, need {MIN_STUDENTS}")
            _record("rejected", reason, t1=cur["load_t1"], t2=cur["load_t2"],
                    n_students=n, n_turns=0, calibrated=False)
            return {"promoted": False, "reason": reason, "n_students": n}

        values = [v for _, v in pop]
        n_turns = int(db.fetch_one(
            "SELECT COUNT(*) c FROM turn_log")["c"] or 0)

        raw_t1 = _percentile(values, PCT_T1)
        raw_t2 = _percentile(values, PCT_T2)

        # ── Guard 2: band, anchored to the first honest calibration ─────────
        band = _band()
        t1, t2 = _clamp(raw_t1, band), _clamp(raw_t2, band)
        # Ordering is part of the meaning: more load must never buy a LONGER
        # horizon. A degenerate population can invert the percentiles after
        # clamping, and the collapse to a single cut point is the honest
        # reading of that.
        if t1 < t2:
            t1 = t2

        # ── Guard 3: anomaly hold ───────────────────────────────────────────
        jump = max(abs(t1 - cur["load_t1"]), abs(t2 - cur["load_t2"]))
        first_real = not cur.get("calibrated")
        if jump > MAX_STEP * ANOMALY_FACTOR and not first_real:
            reason = (f"anomalous move {jump:.3f} > "
                      f"{MAX_STEP * ANOMALY_FACTOR:.3f}; holding v{cur['version']} "
                      f"(candidate {raw_t1:.3f}/{raw_t2:.3f})")
            _record("rejected", reason, t1=t1, t2=t2, n_students=n,
                    n_turns=n_turns, calibrated=True)
            logger.warning(f"[cac-calib] {reason}")
            return {"promoted": False, "reason": reason, "n_students": n}

        # ── Guard 4: rate limit ─────────────────────────────────────────────
        # Skipped on the FIRST data-backed promotion: v1 was declared rather
        # than measured, so there is no continuity worth protecting, and
        # stepping 0.05 at a time from a number nobody could reach would leave
        # the mechanism dead for another dozen runs. This exemption fires once
        # in the lifetime of the deployment, so it is not a standing route.
        if not first_real:
            t1 = _limit_step(cur["load_t1"], t1)
            t2 = _limit_step(cur["load_t2"], t2)

        # Compare at STORED precision. Stored values are rounded to 4dp, so an
        # unrounded comparison lets sub-0.0001 drift promote a version identical
        # to its predecessor — churn that would pollute the one record meant to
        # make a slow attack legible.
        t1, t2 = round(t1, 4), round(t2, 4)
        if (abs(t1 - round(cur["load_t1"], 4)) < 1e-9
                and abs(t2 - round(cur["load_t2"], 4)) < 1e-9):
            reason = f"no change from v{cur['version']}"
            _record("rejected", reason, t1=t1, t2=t2, n_students=n,
                    n_turns=n_turns, calibrated=True)
            return {"promoted": False, "reason": reason, "n_students": n}

        # ── Promote ─────────────────────────────────────────────────────────
        reason = (f"p{PCT_T1:g}/p{PCT_T2:g} over {n} students "
                  f"(raw {raw_t1:.3f}/{raw_t2:.3f})")
        db.execute("UPDATE cac_policy_version SET status='superseded' "
                   "WHERE status='active'")
        ver = _record("active", reason, t1=t1, t2=t2, n_students=n,
                      n_turns=n_turns, calibrated=True)
        refresh()
        logger.info(f"[cac-calib] promoted v{ver}: "
                    f"{t1:.3f}/{t2:.3f} — {reason}")
        return {"promoted": True, "version": ver, "load_t1": t1,
                "load_t2": t2, "reason": reason, "n_students": n}

    except Exception as e:
        logger.warning(f"[cac-calib] recalibration failed: {e}")
        return {"promoted": False, "reason": f"error: {e}"}


# ── Trigger ─────────────────────────────────────────────────────────────────

def note_turn() -> None:
    """Count one CAC-evaluated turn; run calibration off-thread when due.

    The work never touches the request path. A percentile over every eligible
    student is far too slow to sit in front of a learner waiting for an answer,
    and it is not urgent: thresholds that are 100 turns stale are fine, whereas
    a turn that is two seconds slow is not.
    """
    global _turns_since_run, _running
    with _lock:
        _turns_since_run += 1
        if _turns_since_run < RECALIBRATE_EVERY or _running:
            return
        _turns_since_run = 0
        _running = True

    def _run():
        global _running
        try:
            recalibrate()
        finally:
            with _lock:
                _running = False

    threading.Thread(target=_run, name="cac-calibration", daemon=True).start()


def history(limit: int = 20) -> list[dict]:
    """Recent calibration runs, promoted and rejected alike."""
    from app.db.sqlite_db import db
    rows = db.fetch_all(
        "SELECT version, created_at, load_t1, load_t2, n_students, n_turns, "
        "       calibrated, status, reason FROM cac_policy_version "
        "ORDER BY version DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]


def thresholds_for(version: int) -> tuple[float, float] | None:
    """Thresholds a given version used — the replay entry point.

    Replay must reconstruct the decision that was actually made, which means
    reading the policy off the event's own row instead of whatever is active
    now. Without this the audit log would silently re-interpret itself every
    time the thresholds moved.
    """
    from app.db.sqlite_db import db
    row = db.fetch_one("SELECT load_t1, load_t2 FROM cac_policy_version "
                       "WHERE version=?", (version,))
    return (float(row["load_t1"]), float(row["load_t2"])) if row else None
