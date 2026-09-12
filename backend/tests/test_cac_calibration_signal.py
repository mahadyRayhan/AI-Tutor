"""Phase 4 — overconfidence tightens a prerequisite edge.

Run: /opt/anaconda3/envs/agent/bin/python -m pytest backend/tests/test_cac_calibration_signal.py -q

The learner this exists for is the one who is wrong while the MODEL expected
them to be right. They do not know they are wrong, so they will not repair it —
they will build the next topic on the broken idea. A learner who is wrong and
expected to be wrong is simply learning, and nothing here may touch them.
"""
import random

import pytest

from app.core.cac_graph import (
    CURRICULUM, Decision, LearnerView, Rung, decide, _tighten,
    THETA_BASE, THETA_OVERCONFIDENT, GAP_TIGHTEN, GAP_MIN_WRONG,
)


def _view(gap, n_wrong, certified=frozenset({"Variables"})):
    return LearnerView("u", certified, gap_ratio=gap, gap_n_wrong=n_wrong)


# "Control Flow" sits ON the frontier from Variables; "Arrays" is one hop
# past it. Both are inside the edge window this signal acts on.
def _ask(gap, n_wrong, concept="Control Flow", **kw):
    return decide(_view(gap, n_wrong, **kw), [concept], signals={"cac_edge"})


# ── The invariant: C may raise an edge, never lower it ──────────────────────

def test_theta_floor_holds_over_random_inputs():
    """Property: theta_edge >= THETA_BASE for ANY signal input.

    The plan states this as Phase 4's invariant. Asserted over random values
    rather than chosen ones because the failure that matters is a future signal
    passing something small, not today's constants being right.
    """
    rng = random.Random(1789)
    for _ in range(2000):
        d = _ask(rng.random(), rng.randint(0, 40))
        assert d.theta_edge >= THETA_BASE


def test_tighten_cannot_lower_theta_even_when_asked():
    """The floor is a property of the mutator, not of its callers."""
    d = Decision()
    _tighten(d, theta=0.10, reason="hostile")
    assert d.theta_edge == THETA_BASE
    _tighten(d, theta=THETA_OVERCONFIDENT, reason="raise")
    assert d.theta_edge == THETA_OVERCONFIDENT
    _tighten(d, theta=THETA_BASE, reason="try to walk it back")
    assert d.theta_edge == THETA_OVERCONFIDENT, "theta must take the maximum"


def test_theta_stays_below_certification():
    """'Show me more before building on this', not a re-certification."""
    assert THETA_BASE < THETA_OVERCONFIDENT < 0.95


# ── Who it fires on ─────────────────────────────────────────────────────────

def test_overconfident_learner_is_tightened():
    d = _ask(0.8, 5)
    assert d.edge_ok is False
    assert d.theta_edge == THETA_OVERCONFIDENT
    assert any("C/calibration" in r for r in d.reasons)


def test_well_calibrated_learner_is_untouched():
    """Wrong but expected to be wrong: that is learning, not a risk."""
    d = _ask(0.0, 8)
    assert d.edge_ok is True
    assert d.theta_edge == THETA_BASE
    assert not any("C/calibration" in r for r in d.reasons)


def test_cold_start_is_not_suspicion():
    d = decide(LearnerView("u", frozenset({"Variables"})), ["Control Flow"],
               signals={"cac_edge"})
    assert d.edge_ok is True
    assert d.theta_edge == THETA_BASE


def test_one_bad_day_does_not_tighten_a_gate():
    """Below the evidence floor, a high ratio means nothing."""
    for n in range(GAP_MIN_WRONG):
        d = _ask(1.0, n)
        assert d.edge_ok is True, f"tightened on only {n} misses"
    assert _ask(1.0, GAP_MIN_WRONG).edge_ok is False


@pytest.mark.parametrize("gap", [0.0, 0.25, GAP_TIGHTEN])
def test_at_or_below_threshold_does_not_fire(gap):
    assert _ask(gap, 10).edge_ok is True


@pytest.mark.parametrize("gap", [GAP_TIGHTEN + 0.01, 0.75, 1.0])
def test_above_threshold_fires(gap):
    assert _ask(gap, 10).edge_ok is False


# ── Where it fires ──────────────────────────────────────────────────────────

def test_only_concepts_at_the_edge_are_affected():
    """A blanket tightening would be the region gate again, not an edge bar."""
    far = [c for c in CURRICULUM.nodes
           if CURRICULUM.hops_from(CURRICULUM.frontier({"Variables"}), c) not in (0, 1, None)]
    if not far:
        pytest.skip("curriculum too shallow for a far concept")
    d = _ask(0.9, 10, concept=far[0])
    assert d.edge_ok is True, "a distant concept must not be edge-tightened"


def test_already_certified_concept_is_not_tightened():
    d = decide(_view(0.9, 10, certified=frozenset({"Variables"})),
               ["Variables"], signals={"cac_edge"})
    assert d.edge_ok is True


# ── Switchability and audit ─────────────────────────────────────────────────

def test_signal_is_switchable_off():
    d = decide(_view(0.9, 10), ["Control Flow"], signals=set())
    assert d.edge_ok is True
    assert d.theta_edge == THETA_BASE


def test_theta_is_in_the_audit_record():
    a = _ask(0.9, 10).audit()
    assert a["theta_edge"] == THETA_OVERCONFIDENT
    assert a["edge_ok"] is False


def test_reason_names_signal_level_and_effect():
    """The audit record must be reproducible, not decorative."""
    r = " ".join(_ask(0.9, 7).reasons)
    assert "C/calibration" in r and "0.90" in r and "0.75" in r


# ── Source preference ───────────────────────────────────────────────────────

def test_build_view_prefers_model_side_calibration(monkeypatch):
    """Self-report is starved AND declarable by the learner; model-side is not."""
    import app.core.cac_graph as cg
    monkeypatch.setattr(cg, "SKILL_TOPICS", [])
    monkeypatch.setattr(
        "app.core.learner_model.get_learner_profile",
        lambda u: {"cognitive": {"calibration": {"gap_ratio": 0.9, "n_wrong": 7},
                                 "error_slip_vs_gap": {"gap_ratio": 0.0, "n_wrong": 1}},
                   "metacognitive": {}})
    v = cg.build_view("u")
    assert v.gap_ratio == 0.9 and v.gap_n_wrong == 7


def test_build_view_falls_back_to_self_report(monkeypatch):
    import app.core.cac_graph as cg
    monkeypatch.setattr(cg, "SKILL_TOPICS", [])
    monkeypatch.setattr(
        "app.core.learner_model.get_learner_profile",
        lambda u: {"cognitive": {"error_slip_vs_gap": {"gap_ratio": 0.6, "n_wrong": 4}},
                   "metacognitive": {}})
    v = cg.build_view("u")
    assert v.gap_ratio == 0.6 and v.gap_n_wrong == 4
