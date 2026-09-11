"""Calibration guards for the CAC cognitive-load thresholds.

Run: /opt/anaconda3/envs/agent/bin/python -m pytest backend/tests/test_cac_calibration.py -q

These tests are about the GUARDS, not the arithmetic. The percentile is easy and
uninteresting; what matters is that a population cannot push the thresholds
somewhere that disables the mechanism, and that the decision path keeps working
when calibration is absent, stale, or broken.
"""
import pytest

from app.core import cac_calibration as cal
from app.core import cac_graph


# ── Percentile ──────────────────────────────────────────────────────────────

def test_percentile_interpolates():
    v = [0.0, 0.1, 0.2, 0.3, 0.4]
    assert cal._percentile(v, 0) == pytest.approx(0.0)
    assert cal._percentile(v, 100) == pytest.approx(0.4)
    assert cal._percentile(v, 50) == pytest.approx(0.2)


def test_percentile_empty_is_zero_not_a_crash():
    assert cal._percentile([], 90) == 0.0


# ── Guard: absolute band ────────────────────────────────────────────────────

def test_clamp_bounds_both_directions():
    assert cal._clamp(0.99) == cal.CLAMP_HI
    assert cal._clamp(0.001) == cal.CLAMP_LO
    assert cal._clamp(0.3) == 0.3


def test_clamp_prevents_disabling_the_mechanism():
    """A poisoned population pushing load to 1.0 still cannot exceed CLAMP_HI.

    This is the backstop: if every other guard is wrong, the threshold cannot
    be driven past a value that no learner reaches, which is the state that
    turns the horizon signal into a no-op.
    """
    assert cal._clamp(_percentile_of_poisoned()) <= cal.CLAMP_HI < 1.0


def _percentile_of_poisoned():
    return cal._percentile([1.0] * 500, cal.PCT_T1)


# ── Guard: rate limit ───────────────────────────────────────────────────────

def test_step_limited_when_target_is_far():
    assert cal._limit_step(0.50, 0.90) == pytest.approx(0.50 + cal.MAX_STEP)
    assert cal._limit_step(0.50, 0.10) == pytest.approx(0.50 - cal.MAX_STEP)


def test_step_exact_when_target_is_near():
    assert cal._limit_step(0.50, 0.52) == pytest.approx(0.52)


def test_sustained_attack_is_a_staircase_not_a_jump():
    """Walking the line from 0.50 to CLAMP_HI takes many runs, each recorded."""
    x, runs = 0.50, 0
    while x < cal.CLAMP_HI - 1e-9 and runs < 100:
        x = cal._limit_step(x, cal.CLAMP_HI)
        runs += 1
    assert runs >= 2, "rate limit must not be bypassable in one run"


# ── The decision path without calibration ───────────────────────────────────

def test_horizon_falls_back_to_declared_default(monkeypatch):
    """No ACTIVE (tests, scripts, pre-startup) behaves as before this existed."""
    monkeypatch.setattr(cal, "ACTIVE", None)
    assert cac_graph._active_horizon() == cac_graph.LOAD_HORIZON
    assert cac_graph._active_policy_version() == 1


def test_horizon_follows_the_active_version(monkeypatch):
    monkeypatch.setattr(cal, "ACTIVE", {"version": 7, "load_t1": 0.30,
                                        "load_t2": 0.20, "calibrated": True})
    assert cac_graph._horizon_limit(0.35) == 1
    assert cac_graph._horizon_limit(0.25) == 2
    assert cac_graph._horizon_limit(0.10) is None
    assert cac_graph._active_policy_version() == 7


def test_calibrated_thresholds_make_the_signal_reachable(monkeypatch):
    """The whole point: under v1 the observed population maximum fires nothing.

    Max observed load in the corpus is 0.415 against a 0.5 floor, so every
    learner sat below the threshold and the horizon signal was dead. A
    population-derived threshold has to change that.
    """
    monkeypatch.setattr(cal, "ACTIVE", None)
    assert cac_graph._horizon_limit(0.415) is None, "v1 is unreachable by design"
    monkeypatch.setattr(cal, "ACTIVE", {"version": 2, "load_t1": 0.20,
                                        "load_t2": 0.06, "calibrated": True})
    assert cac_graph._horizon_limit(0.415) == 1


def test_audit_record_carries_the_policy_version(monkeypatch):
    monkeypatch.setattr(cal, "ACTIVE", {"version": 4, "load_t1": 0.3,
                                        "load_t2": 0.2, "calibrated": True})
    d = cac_graph.decide(cac_graph.LearnerView(username="u", certified=frozenset()),
                         ["loops"])
    assert d.audit()["policy_version"] == 4


def test_decide_does_no_io(monkeypatch):
    """`decide()` must not touch the database, however thresholds are sourced."""
    import app.db.sqlite_db as sq

    def _boom(*a, **k):
        raise AssertionError("decide() performed database I/O")

    monkeypatch.setattr(cal, "ACTIVE", {"version": 3, "load_t1": 0.3,
                                        "load_t2": 0.2, "calibrated": True})
    monkeypatch.setattr(sq.db, "fetch_one", _boom)
    monkeypatch.setattr(sq.db, "fetch_all", _boom)
    monkeypatch.setattr(sq.db, "execute", _boom)
    cac_graph.decide(cac_graph.LearnerView(username="u", certified=frozenset()),
                     ["loops"])


# ── Trigger ─────────────────────────────────────────────────────────────────

def test_note_turn_does_not_run_every_turn(monkeypatch):
    calls = []
    monkeypatch.setattr(cal, "recalibrate", lambda: calls.append(1))
    monkeypatch.setattr(cal, "_turns_since_run", 0)
    monkeypatch.setattr(cal, "RECALIBRATE_EVERY", 50)
    for _ in range(49):
        cal.note_turn()
    assert calls == [], "calibration ran inside the request path"


def test_identical_candidate_does_not_churn_the_history(monkeypatch, tmp_path):
    """A candidate equal to the active thresholds must NOT promote a version.

    Stored thresholds are rounded to 4dp. Comparing an unrounded candidate
    against them let sub-0.0001 drift append version after version with
    identical values — harmless to policy, but it buries the signal in the one
    record whose job is to make a slow threshold walk visible.
    """
    recorded = []
    monkeypatch.setattr(cal, "ACTIVE", {"version": 9, "load_t1": 0.2510,
                                        "load_t2": 0.1500, "calibrated": True})
    monkeypatch.setattr(cal, "_population",
                        lambda: [(f"u{i}", 0.251) for i in range(cal.MIN_STUDENTS + 5)])
    monkeypatch.setattr(cal, "_record",
                        lambda status, reason, **kw: recorded.append((status, reason)) or 99)

    class _DB:
        def fetch_one(self, *a, **k): return {"c": 1000}
        def execute(self, *a, **k): raise AssertionError("promoted a no-change version")
    monkeypatch.setattr("app.db.sqlite_db.db", _DB())

    r = cal.recalibrate()
    assert r["promoted"] is False
    assert recorded and recorded[0][0] == "rejected"


# ── Guard: the band is anchored to measured data, not to a guess ────────────

def test_band_falls_back_to_absolutes_without_a_baseline(monkeypatch):
    monkeypatch.setattr(cal, "_baseline", lambda: None)
    assert cal._band() == (cal.CLAMP_LO, cal.CLAMP_HI)


def test_band_tightens_to_the_honest_baseline(monkeypatch):
    monkeypatch.setattr(cal, "_baseline", lambda: (0.20, 0.14))
    lo, hi = cal._band()
    assert hi == pytest.approx(0.20 * cal.CEILING_FACTOR)
    assert lo == pytest.approx(max(cal.CLAMP_LO, 0.14 * cal.FLOOR_FACTOR))
    assert hi < cal.CLAMP_HI, "anchor must bind tighter than the absolute band"


def test_attack_ceiling_stays_below_a_realistic_population_max(monkeypatch):
    """The point of the anchor.

    The original 0.5 threshold was dead because it sat above the observed
    population maximum of 0.415. A fixed CLAMP_HI of 0.60 let a patient attacker
    walk the threshold back into exactly that dead zone — bounding the movement
    while not bounding the consequence. Anchored to an honest baseline of ~0.235,
    the reachable ceiling is ~0.352, which still contracts the loaded tail.
    """
    monkeypatch.setattr(cal, "_baseline", lambda: (0.235, 0.179))
    _, hi = cal._band()
    assert hi < 0.415, "a successful attack must not disable the mechanism"


def test_baseline_is_the_first_calibration_not_the_current_one(monkeypatch):
    """A ceiling that drifts with the value it bounds does not bound anything."""
    seen = {}

    class _DB:
        def fetch_one(self, q, *a, **k):
            seen["q"] = q
            return {"load_t1": 0.2, "load_t2": 0.1}
    monkeypatch.setattr("app.db.sqlite_db.db", _DB())
    cal._baseline()
    assert "ORDER BY version ASC" in seen["q"], "must read the EARLIEST version"
    assert "calibrated=1" in seen["q"], "the seeded default is not a baseline"
