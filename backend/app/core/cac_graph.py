"""Cognitive Access Control over the curriculum graph.

WHAT THIS IS
────────────
Mastery (K) is a *state* — a property of a topic — so it lives on the NODES of the
curriculum DAG and answers "what may be opened". That is ABAC, and it already
works.

Metacognition (C) is *regulation* — a property of how the learner moves — so it
lives on the EDGES and on the TRAVERSAL, and answers "how it is crossed, how far,
and how much is shown". That is CAC, and this module is where it happens.

"Dynamic DAG" therefore does not mean time-varying node labels. It means:

    static topology      the instructor's map           never moves
    slow node dynamics   BKT mastery / decay            days
    fast edge dynamics   metacognitive regulation       minutes

Three timescales, three owners. See docs/CAC_DYNAMIC_DAG_PLAN.md and the visual
walkthrough in docs/dynamic-dag.html.

THE ONE INVARIANT
─────────────────
**C can only narrow.** Nothing metacognitive ever opens a node the curriculum
keeps closed, loosens an edge below the curriculum's own threshold, or raises a
disclosure rung. This is not checked after the fact — `decide()` is *built* so it
cannot be violated: the decision starts from the K-derived baseline and every
signal may only tighten it. `_tighten()` is the sole mutator and it takes the
minimum. A signal that tried to widen would have no way to express it.

That structure is what makes the property test in tests/test_cac_graph.py a
statement about the code rather than about the thresholds of the day.

WHY IT IS A PURE FUNCTION
─────────────────────────
`decide()` touches no database, calls no model, and writes nothing. All I/O
happens in `build_view()`. Purity is what buys three things the evaluation plan
needs and could not otherwise have:

  • ablation      — flip `cac_graph` off and the decision is provably a no-op
  • replay        — run the policy over historical turn_log rows offline (E14)
  • property tests — quantify over random graphs and random learner states (E8)

PHASE 0 SCOPE
─────────────
Topology, learner view, the Decision object, the graph helpers, and the tightening
skeleton — with every signal threshold still neutral. `decide()` currently returns
the permissive baseline for everyone; Phases 1–6 each switch on one signal by
filling in one `_signal_*` function. The plumbing is what is being landed here, so
that each later phase is a small, separately ablatable change.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Iterable

from app.core.concept_canon import canonical_concept

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# 1. Topology — the instructor's map
# ══════════════════════════════════════════════════════════════════════════

# The nine coarse topics the dashboard, the skill network and the mastery store
# all agree on. Kept here because CAC is now the component with the strongest
# claim to own the curriculum's shape; main.py imports these rather than keeping
# a second copy that could drift.
SKILL_TOPICS: tuple[str, ...] = (
    "Variables", "Control Flow", "Functions", "Arrays", "Strings",
    "Pointers", "Structures", "Memory Allocation", "File I/O",
)

# (prerequisite, dependent). Fixed curriculum knowledge for the coarse graph.
# Neo4j holds a finer entity-level structure used for retrieval and head-start
# seeding; for ACCESS decisions the coarse list is the authority, so this module
# deliberately takes no graph-database dependency — it must work in tests, in
# offline replay, and when Neo4j is down.
CANONICAL_PREREQ_EDGES: tuple[tuple[str, str], ...] = (
    ("Variables", "Control Flow"),
    ("Variables", "Pointers"),
    ("Control Flow", "Functions"),
    ("Control Flow", "Arrays"),
    ("Arrays", "Strings"),
    ("Arrays", "Structures"),
    ("Pointers", "Memory Allocation"),
    ("Strings", "File I/O"),
)


class Topology:
    """An immutable prerequisite DAG with the lookups the policy needs."""

    def __init__(self, nodes: Iterable[str], edges: Iterable[tuple[str, str]]):
        self.nodes: tuple[str, ...] = tuple(nodes)
        self.edges: tuple[tuple[str, str], ...] = tuple(edges)
        self._preds: dict[str, set[str]] = {n: set() for n in self.nodes}
        self._succs: dict[str, set[str]] = {n: set() for n in self.nodes}
        for prereq, dependent in self.edges:
            if prereq not in self._preds or dependent not in self._preds:
                # A malformed edge must not silently create a phantom node that
                # nothing can ever certify — that would lock its dependents forever.
                logger.warning(f"[cac] edge references unknown node: {prereq} -> {dependent}")
                continue
            self._preds[dependent].add(prereq)
            self._succs[prereq].add(dependent)

    def predecessors(self, node: str) -> set[str]:
        return set(self._preds.get(node, ()))

    def successors(self, node: str) -> set[str]:
        return set(self._succs.get(node, ()))

    def frontier(self, certified: set[str]) -> set[str]:
        """Unlocked but not yet mastered — every prerequisite currently certified.

        This set is the Zone of Proximal Development and the authorization
        boundary at the same time; that they coincide is the conceptual claim the
        whole layer rests on.
        """
        return {n for n in self.nodes
                if n not in certified and self._preds[n] <= certified}

    def authorized(self, certified: set[str]) -> set[str]:
        """The region a learner may legitimately work in: mastered plus frontier."""
        return set(certified) | self.frontier(certified)

    def hops_from(self, sources: set[str], target: str) -> int | None:
        """Forward edge distance from the nearest source to `target`.

        None when unreachable. Used for the horizon: how many prerequisite steps
        beyond the frontier a request sits.
        """
        if target in sources:
            return 0
        seen, layer, depth = set(sources), set(sources), 0
        while layer:
            depth += 1
            nxt: set[str] = set()
            for n in layer:
                for s in self._succs.get(n, ()):
                    if s in seen:
                        continue
                    if s == target:
                        return depth
                    seen.add(s)
                    nxt.add(s)
            layer = nxt
        return None

    def missing_prerequisites(self, node: str, certified: set[str]) -> set[str]:
        """Direct prerequisites of `node` that are not certified — what a redirect names."""
        return self._preds.get(node, set()) - certified


CURRICULUM = Topology(SKILL_TOPICS, CANONICAL_PREREQ_EDGES)


# ══════════════════════════════════════════════════════════════════════════
# 2. The disclosure ladder
# ══════════════════════════════════════════════════════════════════════════

class Rung(IntEnum):
    """How much of an answer may be shown, ordered least to most revealing.

    CAC's output is a rung on this ladder, not a boolean. A tutor's real decision
    was never "may they see it" but "how much of it" — which makes this a
    declassification policy rather than an access policy, and makes an attack
    measurable as *moving the rung* rather than only as defeating a block.
    """
    NONE = 0        # refuse outright (reserved: hard blocks stay in the Sentinel)
    DIAGRAM = 1     # visual / memory model only
    HINT = 2        # + a nudge toward the next step
    EXAMPLE = 3     # + an analogous worked example, different problem
    CODE = 4        # + code for the problem actually asked

    @property
    def label(self) -> str:
        return {0: "refuse", 1: "diagram only", 2: "hints",
                3: "worked example", 4: "full code"}[int(self)]


# ══════════════════════════════════════════════════════════════════════════
# 3. Learner view — the only thing decide() is allowed to read
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class LearnerView:
    """A materialised snapshot: certified set (K) plus the metacognitive signals (C).

    Built once per turn by `build_view()`, which does all the I/O. Frozen so a
    policy bug cannot express itself as a mutation of the learner's state.

    Signal fields are Optional and default to the neutral value. A learner with no
    history must be treated as unremarkable, never as suspicious — cold start is
    the common case at the beginning of a term, and a policy that tightened on
    missing data would hit every student in week one.
    """
    username: str
    certified: frozenset[str] = frozenset()

    # C signals, each None when there is not yet enough evidence to compute it.
    cognitive_load: float | None = None       # 0..1, higher = more loaded
    gap_ratio: float | None = None            # 0..1, wrong-and-confident share
    help_seeking_quality: str | None = None   # "impulsive" | "measured"
    rushing: bool = False                     # fast answers on low mastery
    grit_index: float | None = None           # 0..1, higher = more persistent

    def is_cold(self) -> bool:
        """True when almost nothing is known — used to justify the neutral path."""
        return (self.cognitive_load is None and self.gap_ratio is None
                and self.help_seeking_quality is None)


# ══════════════════════════════════════════════════════════════════════════
# 4. Decision
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class Decision:
    """What CAC concluded, and why.

    `reasons` is not decoration: it is the audit record. Every entry names the
    signal, the graph level it acted on, and the effect — which is what STRIDE
    Repudiation asks for and what makes a decision reproducible after the fact.
    """
    rung_cap: Rung = Rung.CODE
    in_horizon: bool = True
    edge_ok: bool = True
    redirect_to: str | None = None
    reasons: list[str] = field(default_factory=list)

    @property
    def is_permissive(self) -> bool:
        """True when this decision changes nothing — the ablation-off baseline."""
        return (self.rung_cap is Rung.CODE and self.in_horizon
                and self.edge_ok and self.redirect_to is None)

    def audit(self) -> dict:
        """Flat record for turn_log."""
        return {
            "rung_cap": int(self.rung_cap),
            "rung_label": self.rung_cap.label,
            "in_horizon": self.in_horizon,
            "edge_ok": self.edge_ok,
            "redirect_to": self.redirect_to,
            "reasons": list(self.reasons),
        }


def _tighten(decision: Decision, *, rung: Rung | None = None,
             horizon: bool | None = None, edge: bool | None = None,
             redirect: str | None = None, reason: str) -> None:
    """The ONLY way a signal may alter a decision, and it only ever narrows.

    Rung takes the minimum; the booleans can go True→False but never back. This
    is where the "C can only narrow" invariant is enforced structurally — a signal
    has no vocabulary for widening, so no future phase can accidentally add one
    without editing this function and noticing what it is doing.
    """
    changed = False
    if rung is not None and rung < decision.rung_cap:
        decision.rung_cap = rung
        changed = True
    if horizon is False and decision.in_horizon:
        decision.in_horizon = False
        changed = True
    if edge is False and decision.edge_ok:
        decision.edge_ok = False
        changed = True
    if redirect is not None and decision.redirect_to is None:
        decision.redirect_to = redirect
        changed = True
    if changed:
        decision.reasons.append(reason)


# ── Signal hooks. Phase 0 lands them neutral; one phase fills in each. ──────

def _signal_help_seeking(view: LearnerView, decision: Decision) -> None:
    """Phase 2 — executive help-seeking caps the disclosure rung."""
    return


def _signal_cognitive_load(view: LearnerView, topo: Topology,
                           concepts: set[str], decision: Decision) -> None:
    """Phase 3 — load contracts the traversal horizon."""
    return


def _signal_calibration(view: LearnerView, topo: Topology,
                        concepts: set[str], decision: Decision) -> None:
    """Phase 4 — overconfidence tightens the prerequisite edge."""
    return


# ══════════════════════════════════════════════════════════════════════════
# 5. The decision
# ══════════════════════════════════════════════════════════════════════════

def decide(view: LearnerView, query_concepts: Iterable[str],
           topo: Topology | None = None) -> Decision:
    """Evaluate CAC for one turn. Pure: no I/O, no model call, no mutation of `view`.

    The baseline is permissive and derived from K alone. Each signal may then
    narrow it. A request outside the authorized region gets a REDIRECT naming the
    missing prerequisite, never a bare refusal — a refusal that does not teach is
    a pedagogical failure, and the redirect is the only outcome that leaves the
    honest learner better off than before they asked.
    """
    topo = topo or CURRICULUM
    decision = Decision()

    concepts = {canonical_concept(c) for c in query_concepts if c}
    concepts = {c for c in concepts if c in topo.nodes}
    if not concepts:
        # Off-graph small talk, or a topic the coarse map does not name. CAC has
        # no opinion; other Sentinel layers still apply.
        return decision

    certified = set(view.certified)
    authorized = topo.authorized(certified)

    # ── K baseline: is the asked concept even in the authorized region? ──
    beyond = concepts - authorized
    if beyond:
        target = sorted(beyond)[0]
        missing = topo.missing_prerequisites(target, certified)
        _tighten(
            decision, redirect=sorted(missing)[0] if missing else None,
            rung=Rung.HINT,
            reason=f"K/region: '{target}' is beyond the authorized region"
                   + (f"; missing prerequisite '{sorted(missing)[0]}'" if missing else ""),
        )

    # ── C signals: each may only narrow further ──
    _signal_help_seeking(view, decision)
    _signal_cognitive_load(view, topo, concepts, decision)
    _signal_calibration(view, topo, concepts, decision)

    return decision


NO_OP = Decision()


# ══════════════════════════════════════════════════════════════════════════
# 6. I/O boundary — everything impure lives below this line
# ══════════════════════════════════════════════════════════════════════════

def build_view(username: str) -> LearnerView:
    """Materialise one learner's K and C state. The only DB access in the module.

    Degrades to the neutral view on any failure. A learner-model outage must not
    change access decisions: failing toward "unremarkable learner" keeps the
    baseline K policy intact while silently disabling only the C narrowing, which
    is the safe direction for a layer that can only restrict.
    """
    from app.core import bkt_model

    certified: set[str] = set()
    for topic in SKILL_TOPICS:
        try:
            if bkt_model.is_current_certified(username, topic):
                certified.add(topic)
        except Exception as e:
            logger.debug(f"[cac] certification read failed for {topic}: {e}")

    load = gap = grit = None
    quality = None
    rushing = False
    try:
        from app.core.learner_model import get_learner_profile
        p = get_learner_profile(username)
        cog, meta = p.get("cognitive", {}), p.get("metacognitive", {})
        load = (cog.get("cognitive_load") or {}).get("index")
        gap = (cog.get("error_slip_vs_gap") or {}).get("gap_ratio")
        quality = (meta.get("help_seeking") or {}).get("quality")
        rushing = bool((meta.get("pacing") or {}).get("rushing"))
        grit = (meta.get("persistence") or {}).get("grit_index")
    except Exception as e:
        logger.warning(f"[cac] learner profile unavailable for {username}: {e}")

    return LearnerView(
        username=username, certified=frozenset(certified),
        cognitive_load=load, gap_ratio=gap, help_seeking_quality=quality,
        rushing=rushing, grit_index=grit,
    )


def evaluate(username: str, query_concepts: Iterable[str]) -> Decision:
    """Convenience wrapper for callers in the request path.

    Returns the permissive no-op when the `cac_graph` ablation switch is off, so a
    disabled layer is provably inert rather than merely quiet.
    """
    from app.core import config
    if not config.feature_enabled("cac_graph"):
        return Decision()
    try:
        return decide(build_view(username), query_concepts)
    except Exception as e:
        # Fail OPEN, deliberately. This layer only ever narrows, so a crash here
        # must not become a block: the Sentinel's hard blocks and the K-based
        # prerequisite gate are unaffected and still run.
        logger.error(f"[cac] decision failed for {username}, passing through: {e}")
        return Decision()
