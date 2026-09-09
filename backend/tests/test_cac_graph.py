"""Phase 0 tests for Cognitive Access Control over the curriculum graph.

Two kinds of test, and the distinction matters for the paper:

  • EXAMPLES pin down behaviour on the real nine-topic curriculum.
  • PROPERTIES quantify over randomly generated DAGs and learner states. The
    "C can only narrow" claim is universally quantified, so an example-based test
    would not be evidence for it — it has to be sampled.

Run:  python -m pytest tests/test_cac_graph.py -q
"""
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.cac_graph import (  # noqa: E402
    CANONICAL_PREREQ_EDGES, CURRICULUM, SKILL_TOPICS,
    Decision, LearnerView, Rung, Topology, decide,
)

SEEDS = range(200)


# ══════════════════════════════════════════════════════════════════════════
# Random DAG generation
# ══════════════════════════════════════════════════════════════════════════

def random_dag(rng: random.Random) -> Topology:
    """A random DAG. Edges always run low→high index, so acyclicity is structural."""
    n = rng.randint(2, 12)
    nodes = [f"N{i}" for i in range(n)]
    density = rng.choice([0.15, 0.3, 0.5])
    edges = [(nodes[i], nodes[j])
             for i in range(n) for j in range(i + 1, n) if rng.random() < density]
    return Topology(nodes, edges)


def random_view(rng: random.Random, topo: Topology, *, neutral: bool) -> LearnerView:
    certified = frozenset(n for n in topo.nodes if rng.random() < 0.4)
    if neutral:
        return LearnerView(username="u", certified=certified)
    return LearnerView(
        username="u", certified=certified,
        cognitive_load=rng.choice([None, 0.0, 0.3, 0.6, 0.85, 1.0]),
        gap_ratio=rng.choice([None, 0.0, 0.25, 0.7, 1.0]),
        help_seeking_quality=rng.choice([None, "measured", "impulsive"]),
        rushing=rng.choice([True, False]),
        grit_index=rng.choice([None, 0.1, 0.9]),
    )


# ══════════════════════════════════════════════════════════════════════════
# Topology
# ══════════════════════════════════════════════════════════════════════════

def test_curriculum_matches_canonical_shape():
    assert len(SKILL_TOPICS) == 9
    assert CURRICULUM.predecessors("Arrays") == {"Control Flow"}
    assert CURRICULUM.predecessors("Variables") == set()
    assert CURRICULUM.successors("Arrays") == {"Strings", "Structures"}


def test_every_edge_references_real_nodes():
    """A phantom node would lock its dependents forever with nothing able to certify it."""
    for prereq, dependent in CANONICAL_PREREQ_EDGES:
        assert prereq in SKILL_TOPICS, prereq
        assert dependent in SKILL_TOPICS, dependent


def test_frontier_is_the_unlocked_not_yet_mastered_set():
    frontier = CURRICULUM.frontier({"Variables"})
    assert frontier == {"Control Flow", "Pointers"}
    assert "Arrays" not in frontier      # Control Flow not certified yet
    assert "Variables" not in frontier   # already mastered, not on the frontier


def test_frontier_of_nothing_is_the_roots():
    assert CURRICULUM.frontier(set()) == {"Variables"}


def test_hops_measures_distance_beyond_the_frontier():
    certified = {"Variables", "Control Flow"}
    frontier = CURRICULUM.frontier(certified)
    assert CURRICULUM.hops_from(frontier, "Arrays") == 0      # on the frontier
    assert CURRICULUM.hops_from(frontier, "Strings") == 1     # one step past
    assert CURRICULUM.hops_from(frontier, "File I/O") == 2    # two steps past


def test_missing_prerequisites_names_what_a_redirect_should_say():
    assert CURRICULUM.missing_prerequisites("Arrays", {"Variables"}) == {"Control Flow"}
    assert CURRICULUM.missing_prerequisites("Arrays", {"Control Flow"}) == set()


@pytest.mark.parametrize("seed", SEEDS)
def test_frontier_and_certified_never_overlap(seed):
    rng = random.Random(seed)
    topo = random_dag(rng)
    certified = {n for n in topo.nodes if rng.random() < 0.5}
    assert topo.frontier(certified) & certified == set()


# ══════════════════════════════════════════════════════════════════════════
# THE INVARIANT — C can only narrow
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("seed", SEEDS)
def test_signals_never_widen_a_decision(seed):
    """For the same learner, adding metacognitive signal can only tighten.

    The central claim of the design. Compare a neutral view (K only) against the
    same certified set carrying arbitrary C, and assert the C decision is
    everywhere at least as restrictive.
    """
    rng = random.Random(seed)
    topo = random_dag(rng)
    base_view = random_view(rng, topo, neutral=True)
    with_c = LearnerView(
        username=base_view.username, certified=base_view.certified,
        cognitive_load=rng.choice([None, 0.2, 0.9]),
        gap_ratio=rng.choice([None, 0.1, 0.8]),
        help_seeking_quality=rng.choice([None, "measured", "impulsive"]),
        rushing=rng.choice([True, False]),
        grit_index=rng.choice([None, 0.2, 0.95]),
    )
    query = [rng.choice(topo.nodes)]

    base = decide(base_view, query, topo)
    cd = decide(with_c, query, topo)

    assert cd.rung_cap <= base.rung_cap, "C raised the disclosure rung"
    assert cd.in_horizon <= base.in_horizon, "C widened the horizon"
    assert cd.edge_ok <= base.edge_ok, "C loosened an edge"


@pytest.mark.parametrize("seed", SEEDS)
def test_certifying_more_never_restricts(seed):
    """Learning something must never make a learner worse off.

    The mirror of the invariant above, and the answer to "are you punishing
    competence?" — a superset of K is never a tighter decision.
    """
    rng = random.Random(seed)
    topo = random_dag(rng)
    certified = {n for n in topo.nodes if rng.random() < 0.3}
    extra = set(rng.sample(list(topo.nodes), k=min(2, len(topo.nodes))))
    query = [rng.choice(topo.nodes)]

    lo = decide(LearnerView("u", frozenset(certified)), query, topo)
    hi = decide(LearnerView("u", frozenset(certified | extra)), query, topo)

    assert hi.rung_cap >= lo.rung_cap
    assert hi.in_horizon >= lo.in_horizon
    assert hi.edge_ok >= lo.edge_ok


@pytest.mark.parametrize("seed", SEEDS)
def test_decide_never_mutates_the_view(seed):
    """decide() is pure — replay and ablation both depend on it."""
    rng = random.Random(seed)
    topo = random_dag(rng)
    view = random_view(rng, topo, neutral=False)
    before = (view.certified, view.cognitive_load, view.gap_ratio,
              view.help_seeking_quality, view.rushing, view.grit_index)
    decide(view, [rng.choice(topo.nodes)], topo)
    after = (view.certified, view.cognitive_load, view.gap_ratio,
             view.help_seeking_quality, view.rushing, view.grit_index)
    assert before == after


@pytest.mark.parametrize("seed", SEEDS)
def test_decide_is_deterministic(seed):
    """Same input, same decision — STRIDE Repudiation / E9 reproducibility."""
    rng = random.Random(seed)
    topo = random_dag(rng)
    view = random_view(rng, topo, neutral=False)
    query = [rng.choice(topo.nodes)]
    assert decide(view, query, topo).audit() == decide(view, query, topo).audit()


# ══════════════════════════════════════════════════════════════════════════
# Baseline behaviour (Phase 0: signals neutral)
# ══════════════════════════════════════════════════════════════════════════

def test_frontier_request_is_fully_permissive():
    view = LearnerView("u", frozenset({"Variables", "Control Flow"}))
    d = decide(view, ["Arrays"])
    assert d.is_permissive
    assert d.rung_cap is Rung.CODE
    assert d.reasons == []


def test_mastered_request_is_permissive_in_phase_0():
    view = LearnerView("u", frozenset({"Variables"}))
    assert decide(view, ["Variables"]).is_permissive


def test_beyond_region_redirects_and_names_the_prerequisite():
    """A refusal that does not teach is a failure — the redirect must be actionable."""
    view = LearnerView("u", frozenset({"Variables"}))
    d = decide(view, ["Strings"])
    assert not d.is_permissive
    assert d.redirect_to == "Arrays"
    assert d.rung_cap is Rung.HINT
    assert "beyond the authorized region" in d.reasons[0]


def test_unknown_concept_yields_no_opinion():
    """Off-graph chatter is not CAC's business; other Sentinel layers still run."""
    view = LearnerView("u", frozenset())
    assert decide(view, ["quantum chromodynamics"]).is_permissive
    assert decide(view, []).is_permissive


def test_cold_learner_is_not_treated_as_suspicious():
    """Week one is the common case; a policy that tightened on missing data
    would restrict the entire class on day one."""
    view = LearnerView("u", frozenset({"Variables"}))
    assert view.is_cold()
    assert decide(view, ["Control Flow"]).is_permissive


def test_rungs_are_ordered_and_labelled():
    assert Rung.DIAGRAM < Rung.HINT < Rung.EXAMPLE < Rung.CODE
    assert Rung.HINT.label == "hints"
    assert min(Rung.CODE, Rung.HINT) is Rung.HINT


def test_audit_record_is_flat_and_complete():
    d = decide(LearnerView("u", frozenset({"Variables"})), ["Strings"])
    a = d.audit()
    assert set(a) == {"rung_cap", "rung_label", "in_horizon",
                      "edge_ok", "redirect_to", "reasons"}
    assert a["rung_label"] == "hints"
    assert a["reasons"], "an audit record with no reason explains nothing"


def test_no_op_decision_is_permissive():
    assert Decision().is_permissive
