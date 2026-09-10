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
    SECTION_MIN_RUNG, disclosure_directive, section_allowed,
    LOAD_HORIZON, _horizon_limit, redirect_preamble,
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
    assert d.beyond_region is False
    assert d.reasons == []


def test_mastered_request_is_permissive_in_phase_0():
    view = LearnerView("u", frozenset({"Variables"}))
    assert decide(view, ["Variables"]).is_permissive


def test_beyond_frontier_caps_at_an_orienting_answer():
    """The pedagogical call: answer briefly, spend the response on the prerequisite.

    A full answer with a prerequisite note appended does not work — the note is
    never read once the answer is already there. Capping at ORIENT is what makes
    the suggestion the point rather than a footnote.
    """
    view = LearnerView("u", frozenset({"Variables"}))
    d = decide(view, ["Strings"], region_gate=True)
    assert d.beyond_region is True
    assert d.redirect_to == "Arrays"
    assert d.rung_cap is Rung.ORIENT
    assert not d.is_permissive
    assert "motivate 'Arrays'" in d.reasons[0]


def test_orient_is_below_every_teaching_rung():
    """ORIENT must be less revealing than a diagram, a hint or an example."""
    assert Rung.NONE < Rung.ORIENT < Rung.DIAGRAM < Rung.HINT < Rung.EXAMPLE < Rung.CODE
    assert Rung.ORIENT.label == "orienting answer"


def test_advisory_mode_stays_available_for_comparison():
    """The un-gated reading is kept so the paper can report both."""
    view = LearnerView("u", frozenset({"Variables"}))
    d = decide(view, ["Strings"], region_gate=False)
    assert d.beyond_region is True
    assert d.redirect_to == "Arrays"
    assert d.rung_cap is Rung.CODE
    assert "advisory" in d.reasons[0]


def test_region_gate_only_ever_tightens():
    """Turning the gate on must never widen relative to advisory mode."""
    view = LearnerView("u", frozenset({"Variables"}))
    for concept in SKILL_TOPICS:
        loose = decide(view, [concept])
        strict = decide(view, [concept], region_gate=True)
        assert strict.rung_cap <= loose.rung_cap
        assert strict.beyond_region == loose.beyond_region


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
    d = decide(LearnerView("u", frozenset({"Variables"})), ["Strings"],
               region_gate=True)
    a = d.audit()
    assert a["rung_label"] == "orienting answer"
    assert set(a) == {"rung_cap", "rung_label", "in_horizon", "edge_ok",
                      "redirect_to", "beyond_region", "revealed_edge", "reasons"}
    assert a["reasons"], "an audit record with no reason explains nothing"


def test_no_op_decision_is_permissive():
    assert Decision().is_permissive


# ══════════════════════════════════════════════════════════════════════════
# Phase 1 — observation only
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def probe_user():
    from app.db.sqlite_db import db
    name = "cac_test_probe_user"
    db.execute("DELETE FROM cac_access_event WHERE username=?", (name,))
    yield name
    db.execute("DELETE FROM cac_access_event WHERE username=?", (name,))


def test_every_turn_is_logged_not_only_probes(probe_user):
    """A probe COUNT without a turn count is not a rate, and a rate is what the
    A1 signal actually needs."""
    from app.core import telemetry
    view = LearnerView(probe_user, frozenset({"Variables", "Control Flow"}))
    for concept in ("Arrays", "Strings", "Arrays"):
        telemetry.log_cac_access(probe_user, "s1", concept,
                                 decide(view, [concept]))
    stats = telemetry.cac_probe_stats(probe_user, "s1")
    assert stats == {"turns": 3, "probes": 1, "probe_rate": round(1 / 3, 3)}


def test_probe_stats_scope_to_a_session(probe_user):
    from app.core import telemetry
    view = LearnerView(probe_user, frozenset({"Variables"}))
    telemetry.log_cac_access(probe_user, "s1", "Strings", decide(view, ["Strings"]))
    telemetry.log_cac_access(probe_user, "s2", "Strings", decide(view, ["Strings"]))
    assert telemetry.cac_probe_stats(probe_user, "s1")["turns"] == 1
    assert telemetry.cac_probe_stats(probe_user)["turns"] == 2


def test_access_row_stamps_the_ablation_config(probe_user):
    """A decision must stay attributable to the configuration that produced it."""
    import json
    from app.core import telemetry
    from app.db.sqlite_db import db
    view = LearnerView(probe_user, frozenset({"Variables"}))
    telemetry.log_cac_access(probe_user, "s1", "Strings", decide(view, ["Strings"]))
    row = db.fetch_one("SELECT ablation_config, reasons, redirect_to "
                       "FROM cac_access_event WHERE username=?", (probe_user,))
    assert "cac_graph" in json.loads(row["ablation_config"])
    assert json.loads(row["reasons"]), "a logged probe with no reason explains nothing"
    assert row["redirect_to"] == "Arrays"


def test_logging_never_raises_on_a_bad_write(probe_user):
    """Observability must not cost a learner their turn."""
    from app.core import telemetry

    class Exploding:
        def audit(self):
            raise RuntimeError("boom")

    telemetry.log_cac_access(probe_user, "s1", "Arrays", Exploding())  # must not raise
    assert telemetry.cac_probe_stats(probe_user)["turns"] == 0


def test_probe_stats_on_unknown_user_is_zero():
    from app.core import telemetry
    assert telemetry.cac_probe_stats("nobody_at_all")["turns"] == 0


# ══════════════════════════════════════════════════════════════════════════
# Phase 2 — help-seeking caps the rung
#
# The plan states the acceptance criterion as a property, not a judgement:
# "for any query, the response under cap=HINT contains no fenced code block."
# Two halves are testable without a model in the loop — that the signal fires
# and only ever narrows, and that the prompt carries an instruction which makes
# code impossible. The third half (the model obeys) belongs to the E4 corpus run.
# ══════════════════════════════════════════════════════════════════════════

def _impulsive(certified=frozenset(SKILL_TOPICS)):
    """A fully-certified learner, so nothing BUT help-seeking can narrow."""
    return LearnerView("u", certified, help_seeking_quality="impulsive")


def _measured(certified=frozenset(SKILL_TOPICS)):
    return LearnerView("u", certified, help_seeking_quality="measured")


def test_impulsive_help_seeking_caps_at_hint():
    d = decide(_impulsive(), ["Arrays"])
    assert d.rung_cap is Rung.HINT
    assert not d.is_permissive
    assert any("help-seeking" in r for r in d.reasons), "a cap with no reason is unauditable"


def test_measured_help_seeking_does_not_cap():
    assert decide(_measured(), ["Arrays"]).rung_cap is Rung.CODE


def test_cold_start_is_not_suspicion():
    """quality is None until there is history; None must never narrow."""
    d = decide(LearnerView("u", frozenset(SKILL_TOPICS)), ["Arrays"])
    assert d.rung_cap is Rung.CODE and d.is_permissive


def test_help_seeking_signal_is_switchable():
    """Ablating cac_rung disconnects this signal and nothing else."""
    d = decide(_impulsive(), ["Arrays"], signals=set())
    assert d.rung_cap is Rung.CODE


def test_help_seeking_only_ever_narrows():
    """Over random graphs and K: adding the signal never raises the rung."""
    for seed in SEEDS:
        rng = random.Random(seed)
        topo = random_dag(rng)
        nodes = sorted(topo.nodes)
        k = frozenset(rng.sample(nodes, rng.randint(0, len(nodes))))
        ask = [rng.choice(nodes)]
        base = decide(LearnerView("u", k), ask, topo=topo)
        with_signal = decide(
            LearnerView("u", k, help_seeking_quality="impulsive"), ask, topo=topo)
        assert with_signal.rung_cap <= base.rung_cap, f"widened at seed {seed}"


# ── The consumer side: the cap must be expressible in a prompt ──────────────

def test_hint_directive_forbids_c_code():
    text = disclosure_directive(Rung.HINT)
    assert "Do NOT output C code" in text
    assert "fenced code blocks" in text


def test_code_rung_adds_no_directive():
    """At CODE the layer is inert — no instruction, so no behaviour change."""
    assert disclosure_directive(Rung.CODE) == ""


def test_directive_exists_for_every_rung_below_code():
    for rung in Rung:
        text = disclosure_directive(rung)
        if rung is Rung.CODE:
            assert text == ""
        else:
            assert text.strip(), f"{rung.name} has no directive"


def test_code_bearing_sections_are_withheld_at_hint():
    """The sections that emit code the learner did not write are EXAMPLE-rung."""
    for header in ("Example", "Worked Example", "Example from Class"):
        assert not section_allowed(header, Rung.HINT)
        assert section_allowed(header, Rung.CODE)


def test_diagram_survives_hint():
    """DIAGRAM sits BELOW HINT, so a diagram is permitted at HINT by construction.

    This is why the property is 'no C code', not 'no fenced block' — a Mermaid
    block is fenced and is deliberately still allowed here.
    """
    assert section_allowed("Visual Model", Rung.HINT)


def test_unknown_section_is_allowed():
    """Fail OPEN on an unclassified header: this layer only narrows."""
    assert section_allowed("Some New Heading", Rung.ORIENT)


def test_section_permission_is_monotone_in_the_rung():
    """A section allowed at a low rung is allowed at every higher one."""
    for header in SECTION_MIN_RUNG:
        allowed = [r for r in Rung if section_allowed(header, r)]
        assert allowed == sorted(allowed), header
        assert Rung.CODE in allowed, f"{header} unreachable even at full disclosure"


# ══════════════════════════════════════════════════════════════════════════
# Phase 3 — cognitive load contracts the horizon
#
# Plan acceptance: "a depth-2 query under high load returns the depth-1
# prerequisite, and the audit row names the revealed edge."
#
# The curriculum chain used below is Variables → Control Flow → Arrays →
# Strings → File I/O, so with {Variables, Control Flow} certified the frontier
# holds Arrays and the distances are Arrays 0 · Strings 1 · File I/O 2.
# ══════════════════════════════════════════════════════════════════════════

READY = frozenset({"Variables", "Control Flow"})


def _loaded(load, certified=READY):
    return LearnerView("u", certified, cognitive_load=load)


def test_horizon_limit_thresholds():
    assert _horizon_limit(0.85) == 1        # > 0.7
    assert _horizon_limit(0.60) == 2        # > 0.5
    assert _horizon_limit(0.50) is None     # boundary is exclusive
    assert _horizon_limit(0.10) is None


def test_high_load_redirects_two_hops_to_one_hop():
    """The plan's stated acceptance criterion, on the real curriculum."""
    d = decide(_loaded(0.85), ["File I/O"])
    assert d.in_horizon is False
    assert d.redirect_to == "Strings"       # depth-1, not all the way back
    assert d.revealed_edge == ("Strings", "File I/O")


def test_moderate_load_allows_two_hops():
    """Two hops is within the 0.5–0.7 limit, so the horizon must not contract.

    `redirect_to` is deliberately NOT asserted here: the Phase 0 region advisory
    already names a suggested prerequisite for any beyond-frontier ask, and that
    is information offered alongside a full answer, not a restriction. What marks
    a horizon contraction is `in_horizon` falling and an edge being revealed.
    """
    d = decide(_loaded(0.60), ["File I/O"])
    assert d.in_horizon is True
    assert d.revealed_edge is None


def test_low_load_never_contracts_the_horizon():
    """Reaching ahead is what curiosity looks like; it must not fire on it."""
    d = decide(_loaded(0.10), ["File I/O"])
    assert d.in_horizon is True and d.revealed_edge is None


def test_frontier_request_survives_even_at_maximum_load():
    """Load restricts REACH, not the thing they are actually ready for."""
    d = decide(_loaded(1.0), ["Arrays"])
    assert d.in_horizon is True and d.redirect_to is None


def test_horizon_cold_start_is_not_suspicion():
    assert decide(LearnerView("u", READY), ["File I/O"]).in_horizon is True


def test_horizon_signal_is_switchable():
    """Ablating cac_horizon disconnects the contraction, not the region advisory."""
    d = decide(_loaded(0.95), ["File I/O"], signals=set())
    assert d.in_horizon is True
    assert d.revealed_edge is None


def test_redirect_never_becomes_a_refusal():
    """A refusal that does not teach is a pedagogical failure, not a win."""
    d = decide(_loaded(0.95), ["File I/O"])
    assert d.rung_cap > Rung.NONE, "horizon must never refuse outright"
    assert d.redirect_to, "a contracted horizon must still name something to teach"


def test_revealed_edge_is_recorded_for_e15():
    """Policy leakage has to be measurable without re-instrumenting this path."""
    d = decide(_loaded(0.85), ["File I/O"])
    assert d.audit()["revealed_edge"] == ["Strings", "File I/O"]
    assert any("reveals edge" in r for r in d.reasons)


def test_no_redirect_means_no_revealed_edge():
    assert decide(_loaded(0.1), ["File I/O"]).audit()["revealed_edge"] is None


def test_redirect_preamble_names_both_topics():
    text = redirect_preamble("File I/O", "Strings")
    assert "File I/O" in text and "Strings" in text
    assert "Do NOT teach File I/O" in text


@pytest.mark.parametrize("seed", SEEDS)
def test_load_only_ever_narrows(seed):
    """Over random DAGs: adding load never widens the horizon or raises the rung."""
    rng = random.Random(seed)
    topo = random_dag(rng)
    nodes = sorted(topo.nodes)
    k = frozenset(rng.sample(nodes, rng.randint(0, len(nodes))))
    ask = [rng.choice(nodes)]
    base = decide(LearnerView("u", k), ask, topo=topo)
    loaded = decide(LearnerView("u", k, cognitive_load=rng.choice([0.55, 0.75, 0.99])),
                    ask, topo=topo)
    assert loaded.in_horizon <= base.in_horizon, f"widened horizon at seed {seed}"
    assert loaded.rung_cap <= base.rung_cap, f"raised rung at seed {seed}"


@pytest.mark.parametrize("seed", SEEDS)
def test_redirect_target_is_always_reachable(seed):
    """Never redirect a learner to something they also cannot reach.

    A redirect that lands outside the horizon would send them in a circle, so the
    target must sit within d_max and must not be something already certified.
    """
    rng = random.Random(seed)
    topo = random_dag(rng)
    nodes = sorted(topo.nodes)
    k = frozenset(rng.sample(nodes, rng.randint(0, len(nodes))))
    load = rng.choice([0.75, 0.95])
    d = decide(LearnerView("u", k, cognitive_load=load), [rng.choice(nodes)], topo=topo)
    if d.redirect_to and not d.beyond_region:
        frontier = topo.frontier(set(k))
        hops = topo.hops_from(frontier, d.redirect_to)
        assert hops is not None and hops <= _horizon_limit(load), \
            f"redirect out of horizon at seed {seed}"
        assert d.redirect_to not in k, f"redirected to an earned topic at seed {seed}"


# ══════════════════════════════════════════════════════════════════════════
# Phase 3 audit — region and horizon are separate findings
#
# Before Phase 3, `in_region` was inferred from "did CAC say anything at all",
# which was correct only while the region check was the sole signal that could
# speak. These tests pin the two apart so a later signal cannot silently pool
# them again under a column named for one of them.
# ══════════════════════════════════════════════════════════════════════════

def _row(username):
    from app.db.sqlite_db import db
    return db.fetch_one(
        "SELECT in_region, in_horizon, revealed_edge, redirect_to, reasons "
        "FROM cac_access_event WHERE username=? ORDER BY id DESC LIMIT 1",
        (username,))


def test_audit_row_records_the_revealed_edge_structurally(probe_user):
    """E15 must be queryable without parsing prose out of the reason text."""
    from app.core import telemetry
    import json as _json
    view = LearnerView(probe_user, READY, cognitive_load=0.85)
    telemetry.log_cac_access(probe_user, "s1", "File I/O", decide(view, ["File I/O"]))
    row = _row(probe_user)
    assert _json.loads(row["revealed_edge"]) == ["Strings", "File I/O"]
    assert row["in_horizon"] == 0


def test_horizon_contraction_is_not_recorded_as_a_region_probe(probe_user):
    """An overloaded learner INSIDE their region must not read as an over-reach.

    This is the regression the column split exists to prevent: pooled, an
    overloaded honest student would inflate the A1 adversary signal.
    """
    from app.core import telemetry
    # Certified through Strings, so File I/O is ON the frontier — in region —
    # but two hops of load-limited reach away is not the issue here: the point
    # is that a horizon finding must leave in_region alone.
    view = LearnerView(probe_user, frozenset({"Variables", "Control Flow",
                                              "Arrays", "Strings"}),
                       cognitive_load=0.85)
    d = decide(view, ["File I/O"])
    telemetry.log_cac_access(probe_user, "s2", "File I/O", d)
    row = _row(probe_user)
    assert row["in_region"] == 1, "an in-region turn was logged as a probe"


def test_region_probe_still_records_as_a_probe(probe_user):
    from app.core import telemetry
    view = LearnerView(probe_user, frozenset({"Variables"}))
    telemetry.log_cac_access(probe_user, "s3", "Strings", decide(view, ["Strings"]))
    assert _row(probe_user)["in_region"] == 0


def test_clean_turn_records_as_in_region_and_in_horizon(probe_user):
    from app.core import telemetry
    view = LearnerView(probe_user, READY)
    telemetry.log_cac_access(probe_user, "s4", "Arrays", decide(view, ["Arrays"]))
    row = _row(probe_user)
    assert row["in_region"] == 1 and row["in_horizon"] == 1
    assert row["revealed_edge"] is None


def test_probe_rate_counts_region_only(probe_user):
    """An overloaded but in-region learner must not raise the A1 probe rate."""
    from app.core import telemetry
    k = frozenset({"Variables", "Control Flow", "Arrays", "Strings"})
    for _ in range(3):
        telemetry.log_cac_access(
            probe_user, "s5", "File I/O",
            decide(LearnerView(probe_user, k, cognitive_load=0.85), ["File I/O"]))
    stats = telemetry.cac_probe_stats(probe_user, "s5")
    assert stats["turns"] == 3
    assert stats["probes"] == 0, "load contractions leaked into the probe count"
