"""Phase 4 — overconfidence on a topic tightens the topics after it.

Run: /opt/anaconda3/envs/agent/bin/python -m pytest backend/tests/test_cac_calibration_signal.py -q

The rule, in the project's own words: if a learner is overconfident in Control
Flow, every topic after Control Flow is affected — and a topic on a different
branch, such as Pointers, is not.

The map used here:
    Variables -> Control Flow -> Arrays -> Strings -> File I/O
                              -> Functions   Arrays -> Structures
    Variables -> Pointers -> Memory Allocation
"""
import random

import pytest

from app.core.cac_graph import (
    CURRICULUM, Decision, LearnerView, decide, _tighten,
    THETA_BASE, THETA_OVERCONFIDENT, GAP_TIGHTEN, GAP_MIN_WRONG,
)

EARNED = frozenset({"Variables", "Control Flow"})


def _ask(topic, over=frozenset({"Control Flow"}), earned=EARNED):
    v = LearnerView("u", earned, overconfident_topics=frozenset(over))
    return decide(v, [topic], signals={"cac_edge"})


# ── It follows the branch ───────────────────────────────────────────────────

@pytest.mark.parametrize("topic", ["Arrays", "Functions", "Strings",
                                   "Structures", "File I/O"])
def test_topics_after_the_overconfident_topic_are_tightened(topic):
    d = _ask(topic)
    assert d.edge_ok is False
    assert d.theta_edge == THETA_OVERCONFIDENT


@pytest.mark.parametrize("topic", ["Pointers", "Memory Allocation"])
def test_other_branch_is_untouched(topic):
    """Overconfident in Control Flow must not reach the Pointers branch."""
    d = _ask(topic)
    assert d.edge_ok is True
    assert d.theta_edge == THETA_BASE


def test_the_topic_itself_is_not_tightened():
    """They have Control Flow; the risk is what they build on it."""
    assert _ask("Control Flow").edge_ok is True


def test_flow_reaches_every_depth():
    """Not just the next topic: File I/O is four steps after Control Flow."""
    assert "File I/O" in CURRICULUM.descendants("Control Flow")
    assert _ask("File I/O").edge_ok is False


def test_already_earned_downstream_topic_is_not_tightened():
    earned = EARNED | {"Arrays"}
    assert _ask("Arrays", earned=earned).edge_ok is True
    assert _ask("Strings", earned=earned).edge_ok is False


def test_two_overconfident_topics_cover_both_branches():
    over = {"Control Flow", "Pointers"}
    assert _ask("Arrays", over=over).edge_ok is False
    assert _ask("Memory Allocation", over=over).edge_ok is False


def test_no_overconfidence_changes_nothing():
    for t in CURRICULUM.nodes:
        d = _ask(t, over=frozenset())
        assert d.edge_ok is True and d.theta_edge == THETA_BASE


def test_descendants_are_exactly_the_branch():
    assert CURRICULUM.descendants("Control Flow") == {
        "Arrays", "Functions", "Strings", "Structures", "File I/O"}
    assert CURRICULUM.descendants("Pointers") == {"Memory Allocation"}


# ── The invariant: it can only raise a bar, never lower one ─────────────────

def test_theta_floor_holds_over_random_inputs():
    rng = random.Random(1789)
    nodes = sorted(CURRICULUM.nodes)
    for _ in range(2000):
        over = frozenset(rng.sample(nodes, rng.randint(0, 3)))
        earned = frozenset(rng.sample(nodes, rng.randint(0, 4)))
        d = _ask(rng.choice(nodes), over=over, earned=earned)
        assert d.theta_edge >= THETA_BASE


def test_tighten_cannot_lower_theta_even_when_asked():
    d = Decision()
    _tighten(d, theta=0.10, reason="hostile")
    assert d.theta_edge == THETA_BASE
    _tighten(d, theta=THETA_OVERCONFIDENT, reason="raise")
    _tighten(d, theta=THETA_BASE, reason="walk it back")
    assert d.theta_edge == THETA_OVERCONFIDENT


def test_theta_stays_below_certification():
    assert THETA_BASE < THETA_OVERCONFIDENT < 0.95


# ── Audit and switch ────────────────────────────────────────────────────────

def test_reason_names_the_source_topic():
    r = " ".join(_ask("Arrays").reasons)
    assert "C/calibration" in r and "Control Flow" in r
    assert f"{THETA_OVERCONFIDENT:.2f}" in r


def test_theta_is_in_the_audit_record():
    a = _ask("Arrays").audit()
    assert a["theta_edge"] == THETA_OVERCONFIDENT and a["edge_ok"] is False


def test_signal_is_switchable_off():
    v = LearnerView("u", EARNED, overconfident_topics=frozenset({"Control Flow"}))
    d = decide(v, ["Arrays"], signals=set())
    assert d.edge_ok is True and d.theta_edge == THETA_BASE


# ── Measuring overconfidence per topic ──────────────────────────────────────

def _profile(by_topic):
    return {"cognitive": {"calibration": {"gap_ratio": 0.0, "n_wrong": 0,
                                          "by_topic": by_topic}},
            "metacognitive": {}}


@pytest.fixture
def view_from(monkeypatch):
    import app.core.cac_graph as cg
    monkeypatch.setattr(cg, "SKILL_TOPICS", [])

    def build(by_topic):
        monkeypatch.setattr("app.core.learner_model.get_learner_profile",
                            lambda u: _profile(by_topic))
        return cg.build_view("u")
    return build


def test_overconfidence_is_measured_per_topic(view_from):
    """3/3 confident misses on Control Flow must not be diluted by Arrays."""
    v = view_from({"Control Flow": {"n_wrong": 3, "n_confident_wrong": 3},
                   "Arrays":       {"n_wrong": 7, "n_confident_wrong": 0}})
    assert v.overconfident_topics == frozenset({"Control Flow"})


def test_one_bad_day_does_not_count(view_from):
    for n in range(1, GAP_MIN_WRONG):
        v = view_from({"Control Flow": {"n_wrong": n, "n_confident_wrong": n}})
        assert v.overconfident_topics == frozenset(), f"flagged on {n} misses"
    v = view_from({"Control Flow": {"n_wrong": GAP_MIN_WRONG,
                                    "n_confident_wrong": GAP_MIN_WRONG}})
    assert v.overconfident_topics == frozenset({"Control Flow"})


def test_at_threshold_does_not_count(view_from):
    """More than half, not half."""
    v = view_from({"Control Flow": {"n_wrong": 4, "n_confident_wrong": 2}})
    assert GAP_TIGHTEN == 0.5 and v.overconfident_topics == frozenset()


def test_raw_topic_names_are_pooled(view_from):
    """'variables' and 'Variable' are the same topic on the map."""
    v = view_from({"variables": {"n_wrong": 2, "n_confident_wrong": 2},
                   "Variable":  {"n_wrong": 1, "n_confident_wrong": 1}})
    assert v.overconfident_topics == frozenset({"Variables"})


def test_topics_not_on_the_map_are_ignored(view_from):
    v = view_from({"HS_Loops": {"n_wrong": 9, "n_confident_wrong": 9}})
    assert v.overconfident_topics == frozenset()


@pytest.mark.parametrize("topic", ["Arrays", "Strings", "File I/O"])
def test_the_topic_to_prove_is_the_overconfident_one(topic):
    """Asking anything after Control Flow means proving CONTROL FLOW.

    Not the asked topic's direct prerequisite: for Strings that would be Arrays,
    which the learner may be perfectly sound on.
    """
    assert _ask(topic).theta_topics == ("Control Flow",)


def test_other_branch_has_nothing_to_prove():
    assert _ask("Memory Allocation").theta_topics == ()


def test_topics_to_prove_only_accumulate():
    d = Decision()
    _tighten(d, theta_topics={"Control Flow"}, reason="a")
    _tighten(d, theta_topics={"Pointers"}, reason="b")
    assert d.theta_topics == ("Control Flow", "Pointers")


def test_raised_bar_is_eighty():
    assert THETA_OVERCONFIDENT == 0.80
