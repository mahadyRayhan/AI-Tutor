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

        OPEN QUESTION — what counts as "certified" here decides how often CAC
        fires, and the current answer is the strictest one available. Replay over
        real turns shows 3 of 630 learners hold a LIVE certification (conjunctive,
        θ=0.95 on all three tiers, n>=3 each), so for almost everyone this returns
        {Variables} and 91% of asks land beyond the frontier.

        That may be correct — a beginner asking about pointers arguably should get
        an orienting answer. But 91% is high enough that the alternatives deserve
        measuring before the term starts:

            ever_certified   sticky: once earned, the frontier never retreats
            P̃ >= 0.5         "enough to build on" rather than "mastered"
            any evidence     has touched the topic at all

        `scripts/cac_replay.py` can answer this empirically; the caller supplies
        the certified set, so no change here is needed to try one.
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

    ORIENT is the rung for a request that sits past what the learner has built up
    to. It answers — briefly — and spends the rest of its space on why the missing
    prerequisite is worth having. A full answer with a prerequisite note appended
    would not do this: nobody reads the note once the answer is already there, so
    the suggestion only lands if the answer is short enough to leave room for it.
    """
    NONE = 0        # refuse outright (reserved: hard blocks stay in the Sentinel)
    ORIENT = 1      # one or two lines, and why the missing prerequisite matters
    DIAGRAM = 2     # visual / memory model
    HINT = 3        # + a nudge toward the next step
    EXAMPLE = 4     # + an analogous worked example, different problem
    CODE = 5        # + code for the problem actually asked

    @property
    def label(self) -> str:
        return {0: "refuse", 1: "orienting answer", 2: "diagram",
                3: "hints", 4: "worked example", 5: "full code"}[int(self)]


# ── Applying a rung to a response ─────────────────────────────────────────
# The ladder is policy, so what each rung MEANS for a response lives here rather
# than in whichever agent happens to render it. An agent asks two questions —
# "may this section appear?" and "what must I tell the model?" — and both answers
# come from this module. A second agent (Scaffolding, the reviewer) wires up by
# calling the same two functions, not by reimplementing the ladder.

SECTION_MIN_RUNG: dict[str, Rung] = {
    # Prose that orients. Available at every rung above refusal.
    "Explanation":                 Rung.ORIENT,
    "Refresher":                   Rung.ORIENT,
    "Connection to Your Goal":     Rung.ORIENT,
    # Visual / structural models. Cheaper than prose for a loaded learner.
    "Visual Model":                Rung.DIAGRAM,
    "Use Cases":                   Rung.DIAGRAM,
    # Nudges toward the next step, and questions the learner answers themselves.
    "Common Mistakes":             Rung.HINT,
    "Your Turn! (Micro-Challenge)": Rung.HINT,
    "Your Turn: Apply the Fix":    Rung.HINT,
    "Challenge":                   Rung.HINT,
    "Quick Check":                 Rung.HINT,
    # Diagnostic branch (`_build_diagnostic_prompt`). The learner has a broken
    # artifact; these narrow attention to the defect without repairing it for
    # them, which is what HINT means. "How to Fix" stays at HINT because its own
    # instructions forbid a corrected program — it states the rule and stops.
    "What's Wrong":                Rung.HINT,
    "Why":                         Rung.ORIENT,
    "How to Fix":                  Rung.HINT,
    # Planning branches. Prose and diagrams that describe an approach without
    # writing it.
    "Strategy":                    Rung.ORIENT,
    "Architectural Overview":      Rung.ORIENT,
    "Why this approach works":     Rung.ORIENT,
    "Visual Logic":                Rung.DIAGRAM,
    "System Design (Diagram)":     Rung.DIAGRAM,
    "Implementation Plan":         Rung.HINT,
    "Implementation Phases":       Rung.HINT,
    "Guiding Question":            Rung.HINT,
    # Code the learner did not write.
    "Example":                     Rung.EXAMPLE,
    "Worked Example":              Rung.EXAMPLE,
    "Example from Class":          Rung.EXAMPLE,
    "Starter Skeleton":            Rung.EXAMPLE,
}


def redirect_preamble(asked: str, nearer: str) -> str:
    """Instruction for a turn that was redirected to a nearer prerequisite.

    The substitution is stated out loud on purpose. Quietly answering a different
    question than the one asked reads as the tutor having misunderstood, and the
    learner repeats themselves; naming the swap and its reason is what turns a
    restriction into teaching. This is also the text that discloses the edge —
    see `Decision.revealed_edge` and the E15 track.
    """
    return (
        "\n\n**REDIRECTED REQUEST (HARD CONSTRAINT):**\n"
        f"- The student asked about **{asked}**, but is not ready for it yet and "
        f"is showing signs of cognitive overload.\n"
        f"- Open by saying — warmly, in one sentence — that you are starting with "
        f"**{nearer}** because **{asked}** builds directly on it.\n"
        f"- Then teach **{nearer}**, and ONLY {nearer}.\n"
        f"- Do NOT teach {asked} in this response. Do not apologise, and do not "
        f"suggest they are incapable — this is sequencing, not judgement.\n"
        f"- Close by telling them {asked} is next once {nearer} is solid."
    )


def section_allowed(header: str, cap: Rung) -> bool:
    """May a section with this `## header` appear under `cap`?

    Unknown headers are ALLOWED. This layer only narrows, and a section nobody
    has classified is not thereby suspicious — failing closed here would mean a
    new heading silently disappears from responses, which is a far worse failure
    than a heading that escapes the cap until someone maps it.
    """
    required = SECTION_MIN_RUNG.get(header.strip())
    return True if required is None else cap >= required


def disclosure_directive(cap: Rung) -> str:
    """The instruction a prompt must carry to honour `cap`. Empty at CODE.

    Note DIAGRAM sits BELOW HINT on the ladder, so a mermaid diagram is permitted
    at HINT by construction. The thing withheld at HINT is C code, and the
    property test greps for exactly that — not for fenced blocks in general.
    """
    if cap >= Rung.CODE:
        return ""
    common = ("\n\n**DISCLOSURE LIMIT (HARD CONSTRAINT — overrides every other "
              "formatting instruction above):**\n")
    if cap == Rung.EXAMPLE:
        return common + (
            "- Do NOT write code that solves the student's actual problem.\n"
            "- An analogous example on a DIFFERENT problem is allowed.")
    if cap == Rung.HINT:
        return common + (
            "- Do NOT output C code in any form: no fenced code blocks, no "
            "snippets, no full statements, no function bodies.\n"
            "- Naming a function or keyword inline (e.g. `malloc`, `for`) is fine.\n"
            "- A Mermaid diagram is allowed; C code is not.\n"
            "- Guide with words and one concrete next step the student performs.")
    if cap == Rung.DIAGRAM:
        return common + (
            "- Do NOT output C code, and do NOT give step-by-step instructions.\n"
            "- Explain with a diagram and a description of the memory/control model.")
    if cap == Rung.ORIENT:
        return common + (
            "- Answer in AT MOST two sentences.\n"
            "- Spend the rest of the response on why the missing prerequisite is "
            "worth having first. No code, no diagram, no step-by-step.")
    return common + "- Do not answer this request."


# Phase 4 · prerequisite-edge thresholds.
#
# THETA_BASE mirrors bkt_model.THETA_DECERTIFY: what the CURRICULUM demands to
# keep an edge open, independent of any cognitive state. It is the floor, and
# `_signal_calibration` may only move away from it upward — the same sign
# constraint the rung ladder and the horizon obey, expressed for a continuous
# quantity instead of an enum.
#
# THETA_OVERCONFIDENT is where an overconfident learner's bar sits instead. The
# gap it opens (0.75 -> 0.90) is deliberately short of THETA_CERTIFY (0.95):
# the intent is "show me more before you build on this", not a re-certification.
THETA_BASE = 0.75
THETA_OVERCONFIDENT = 0.90

# A learner is treated as overconfident when more than half of what they got
# wrong, they got wrong while the model expected them to be right.
GAP_TIGHTEN = 0.5

# ...and only once there is enough to say so. Three misses is the same evidence
# floor bkt_model.N_MIN applies before certifying: one surprising miss is a bad
# day, and tightening a gate on it would punish the ordinary case.
GAP_MIN_WRONG = 3


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
    gap_n_wrong: int = 0                      # denominator behind gap_ratio
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

    `beyond_region` is an OBSERVATION, deliberately separate from the fields that
    restrict. A request past the frontier is worth recording — it is the A1
    signal — but on this course it is usually just curiosity, so by default it
    annotates and does not narrow. See `cac_region_gate`.

    `reasons` is not decoration: it is the audit record. Every entry names the
    signal, the graph level it acted on, and the effect — which is what STRIDE
    Repudiation asks for and what makes a decision reproducible after the fact.
    """
    rung_cap: Rung = Rung.CODE
    in_horizon: bool = True
    edge_ok: bool = True
    redirect_to: str | None = None
    beyond_region: bool = False
    # (prerequisite, asked_for) — the curriculum edge a redirect message discloses.
    # Explaining WHY a request was narrowed teaches the learner the shape of the
    # policy, so E15 measures it. Recorded at the moment of the reveal because
    # reconstructing it afterwards from prose is not reliable.
    revealed_edge: tuple[str, str] | None = None

    # Which threshold set decided this turn. Carried on the decision so the
    # audit row can name it: without the stamp, replaying an event after a
    # recalibration silently re-interprets it under thresholds that were not in
    # force when the learner was actually there.
    policy_version: int = 1

    # Phase 4 · the prerequisite bar this learner must clear on the edge they are
    # standing at. Starts at the curriculum's own THETA_DECERTIFY and may only be
    # raised: C tightens an edge, never loosens one below what the curriculum
    # already demands. Carried explicitly rather than recomputed downstream so
    # the number that gated a learner is in the audit row, not inferred from it.
    theta_edge: float = THETA_BASE
    reasons: list[str] = field(default_factory=list)

    @property
    def is_permissive(self) -> bool:
        """True when this decision changes nothing the learner would notice.

        `beyond_region` and `redirect_to` are excluded on purpose: a suggested
        prerequisite is information offered alongside a full answer, not a
        restriction, so a turn carrying one is still permissive.
        """
        return (self.rung_cap is Rung.CODE and self.in_horizon and self.edge_ok)

    def audit(self) -> dict:
        """Flat record for turn_log."""
        return {
            "rung_cap": int(self.rung_cap),
            "rung_label": self.rung_cap.label,
            "in_horizon": self.in_horizon,
            "edge_ok": self.edge_ok,
            "redirect_to": self.redirect_to,
            "beyond_region": self.beyond_region,
            "revealed_edge": list(self.revealed_edge) if self.revealed_edge else None,
            "policy_version": self.policy_version,
            "theta_edge": round(self.theta_edge, 3),
            "reasons": list(self.reasons),
        }


def _tighten(decision: Decision, *, rung: Rung | None = None,
             horizon: bool | None = None, edge: bool | None = None,
             theta: float | None = None,
             redirect: str | None = None, reason: str) -> None:
    """The ONLY way a signal may alter a decision, and it only ever narrows.

    Rung takes the minimum; the booleans can go True→False but never back; theta
    takes the MAXIMUM, because for a prerequisite bar "narrower" means higher.
    This is where the "C can only narrow" invariant is enforced structurally — a
    signal has no vocabulary for widening, so no future phase can accidentally
    add one without editing this function and noticing what it is doing.
    """
    changed = False
    if theta is not None and theta > decision.theta_edge:
        # max(), and never below THETA_BASE: an edge may be made harder than the
        # curriculum demands, never easier. Clamping here rather than trusting
        # callers keeps the floor a property of the mutator, so a future signal
        # passing a low theta cannot quietly open a gate.
        decision.theta_edge = max(THETA_BASE, theta)
        changed = True
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
    """Phase 2 · switch `cac_rung` — executive help-seeking caps the rung.

    `quality == "impulsive"` is computed in `learner_model._help_seeking` as
    "has given up immediately at least once AND skips more than 30% of the time".
    That is a statement about *executive control*, not about ability: the learner
    is reaching for the answer before attempting the problem.

    Capping at HINT is the pedagogically correct response and the security one at
    the same time. Code produced now answers the give-up rather than the question,
    and it is exactly the evidence a later certification cannot stand on — a
    correct submission copied from a worked example the learner requested instead
    of attempting. Withholding it is what stops hollow evidence being manufactured.

    Cold start is explicitly not suspicion: `quality` is None until there is
    enough history, and None takes this branch's early return.
    """
    if view.help_seeking_quality != "impulsive":
        return
    _tighten(
        decision, rung=Rung.HINT,
        reason="C/help-seeking: impulsive (immediate give-ups and a high skip "
               "rate) — capped at hints; code now would answer the give-up "
               "rather than the question",
    )


# Phase 3 · how far past the frontier a learner may reach, by cognitive load.
# Ordered high→low; the first threshold exceeded wins. Below the lowest, the
# horizon is unbounded — reaching ahead is what curiosity looks like, and the
# replay data says it is the common case, so this must not fire on ordinary use.
LOAD_HORIZON: tuple[tuple[float, int], ...] = ((0.7, 1), (0.5, 2))


def _active_horizon() -> tuple[tuple[float, int], ...]:
    """The thresholds currently in force, falling back to the declared default.

    Reads `cac_calibration.ACTIVE` — a module global refreshed at startup and
    after each promotion — rather than the database, so `decide()` keeps its
    documented purity and no learner ever waits on a policy lookup. An
    unconfigured process (tests, scripts, anything that never ran startup) sees
    None and behaves exactly as it did before calibration existed.
    """
    try:
        from app.core import cac_calibration
        a = cac_calibration.ACTIVE
        if a:
            return ((a["load_t1"], 1), (a["load_t2"], 2))
    except Exception:
        pass
    return LOAD_HORIZON


def _active_policy_version() -> int:
    """Version stamp for this decision. Same cached read as `_active_horizon`."""
    try:
        from app.core import cac_calibration
        return int((cac_calibration.ACTIVE or {}).get("version", 1))
    except Exception:
        return 1


def _horizon_limit(load: float) -> int | None:
    """Max hops past the frontier at this load. None = unbounded."""
    for threshold, d_max in _active_horizon():
        if load > threshold:
            return d_max
    return None


def _nearest_within(topo: Topology, frontier: set[str], target: str,
                    d_max: int, certified: set[str]) -> str | None:
    """The furthest ancestor of `target` the learner may still reach.

    Walks BACKWARD from the target, so the first hit is the closest step to what
    they actually asked for rather than the safest possible retreat. A learner
    asking about File I/O under a 1-hop limit should land on Strings — the next
    thing that gets them there — not be sent back to the frontier.
    """
    seen, layer = {target}, {target}
    while layer:
        nxt: set[str] = set()
        for n in layer:
            for p in topo.predecessors(n):
                if p in seen:
                    continue
                seen.add(p)
                d = topo.hops_from(frontier, p)
                if d is not None and d <= d_max and p not in certified:
                    return p
                nxt.add(p)
        layer = nxt
    return None


def _signal_cognitive_load(view: LearnerView, topo: Topology,
                           concepts: set[str], decision: Decision) -> None:
    """Phase 3 · switch `cac_horizon` — load contracts the traversal horizon.

    A loaded learner reaching several topics ahead is not curious, they are
    drowning. So load shortens the reach — and the outcome is a REDIRECT to the
    nearest thing they can actually absorb, never a refusal. A refusal that does
    not teach is a pedagogical failure, not a security success, and this signal
    has no vocabulary for one: it can clear `in_horizon` and name a nearer node,
    and that is all.

    E15 note — the redirect LEAKS. Saying "Strings first" reveals that File I/O
    depends on Strings, which is a fact about the policy the learner did not have
    a moment ago. The revealed edge is therefore named in the reason and carried
    on the decision, so policy leakage is measurable later without going back and
    re-instrumenting this path.
    """
    if view.cognitive_load is None:
        return                                  # cold start is not suspicion
    d_max = _horizon_limit(view.cognitive_load)
    if d_max is None:
        return

    certified = set(view.certified)
    frontier = topo.frontier(certified)
    if not frontier:
        return

    # The furthest concept asked about is the one that decides.
    worst, worst_d = None, -1
    for c in concepts:
        d = topo.hops_from(frontier, c)
        if d is not None and d > worst_d:
            worst, worst_d = c, d
    if worst is None or worst_d <= d_max:
        return

    nearer = _nearest_within(topo, frontier, worst, d_max, certified)
    if nearer is not None:
        decision.revealed_edge = (nearer, worst)
    _tighten(
        decision, horizon=False, redirect=nearer,
        reason=f"C/load {view.cognitive_load:.2f}: '{worst}' is {worst_d} hops "
               f"past the frontier, limit {d_max}"
               + (f"; redirect to '{nearer}' (reveals edge {nearer}->{worst})"
                  if nearer else "; no nearer node to offer"),
    )


def _signal_calibration(view: LearnerView, topo: Topology,
                        concepts: set[str], decision: Decision) -> None:
    """Phase 4 · switch `cac_edge` — overconfidence tightens a prerequisite edge.

    A learner who is wrong while the model expected them to be right does not
    know they are wrong, so they will not come back and repair it — they will
    build the next topic on the broken idea. That is the case worth spending a
    control on. A learner who is wrong and expected to be wrong is simply
    learning, and nothing here should touch them.

    The response is to raise the bar on the edge they are standing at, from the
    curriculum's THETA_BASE toward THETA_OVERCONFIDENT: show more before
    building on this. It is never a refusal, and never a reduction — `_tighten`
    takes the max and clamps at THETA_BASE, so this signal cannot open a gate
    the curriculum had closed.

    SOURCE NOTE. `view.gap_ratio` is fed from `learner_model._calibration`
    (model-side, `prediction_log.p_bkt_pred`) rather than `_slip_vs_gap`
    (self-reported `jol_log.confidence_1_5`). Self-report is both structurally
    starved here and declared by the subject of the policy: a learner who
    notices that admitting confidence tightens their gate stops admitting it.
    Behaviour the learner never sees is the sounder basis.
    """
    if view.gap_ratio is None:
        return                                  # cold start is not suspicion
    if view.gap_n_wrong < GAP_MIN_WRONG:
        return                                  # one bad day is not a pattern
    if view.gap_ratio <= GAP_TIGHTEN:
        return

    certified = set(view.certified)
    frontier = topo.frontier(certified)
    if not frontier:
        return

    # Only concepts standing directly on an edge out of the frontier are
    # affected. Tightening everything would make this a blanket restriction
    # rather than an edge threshold, and would be indistinguishable from the
    # region gate already in place.
    at_edge = sorted(c for c in concepts
                     if c not in certified
                     and topo.hops_from(frontier, c) in (0, 1))
    if not at_edge:
        return

    _tighten(
        decision, edge=False, theta=THETA_OVERCONFIDENT,
        reason=f"C/calibration {view.gap_ratio:.2f} over {view.gap_n_wrong} misses: "
               f"'{at_edge[0]}' needs P\u0303 \u2265 {THETA_OVERCONFIDENT:.2f} "
               f"(curriculum floor {THETA_BASE:.2f})",
    )


# Signal -> the switch that connects it. `decide()` consults this so a signal can
# be attributed in isolation; passing signals=None runs every implemented one,
# which is what offline replay wants when sweeping configurations by hand.
SIGNAL_SWITCHES = {
    "cac_rung": _signal_help_seeking,
    "cac_horizon": _signal_cognitive_load,
    "cac_edge": _signal_calibration,
}


# ══════════════════════════════════════════════════════════════════════════
# 5. The decision
# ══════════════════════════════════════════════════════════════════════════

def decide(view: LearnerView, query_concepts: Iterable[str],
           topo: Topology | None = None,
           signals: set[str] | None = None,
           region_gate: bool = False) -> Decision:
    """Evaluate CAC for one turn. Pure: no I/O, no model call, no mutation of `view`.

    The baseline is permissive and derived from K alone. Each signal may then
    narrow it. A request outside the authorized region gets a REDIRECT naming the
    missing prerequisite, never a bare refusal — a refusal that does not teach is
    a pedagogical failure, and the redirect is the only outcome that leaves the
    honest learner better off than before they asked.
    """
    topo = topo or CURRICULUM
    active = SIGNAL_SWITCHES.keys() if signals is None else signals
    decision = Decision(policy_version=_active_policy_version())

    concepts = {canonical_concept(c) for c in query_concepts if c}
    concepts = {c for c in concepts if c in topo.nodes}
    if not concepts:
        # Off-graph small talk, or a topic the coarse map does not name. CAC has
        # no opinion; other Sentinel layers still apply.
        return decision

    certified = set(view.certified)
    authorized = topo.authorized(certified)

    # ── K baseline: is the asked concept inside the authorized region? ──
    # Recorded either way. Whether it RESTRICTS is `region_gate`. When it does,
    # the rung is ORIENT: answer in a line or two and spend the response on why
    # the missing prerequisite matters. That is the pedagogical call — a full
    # answer carrying a prerequisite footnote teaches nobody, because the footnote
    # is never read.
    #
    # How OFTEN this fires is a separate question, and it is governed by how the
    # frontier is defined rather than by this rung. See `frontier()`.
    beyond = concepts - authorized
    if beyond:
        target = sorted(beyond)[0]
        missing = topo.missing_prerequisites(target, certified)
        hint = sorted(missing)[0] if missing else None
        decision.beyond_region = True
        if region_gate:
            _tighten(
                decision, redirect=hint, rung=Rung.ORIENT,
                reason=f"K/region: '{target}' is beyond the frontier"
                       + (f"; answer briefly and motivate '{hint}'" if hint
                          else "; answer briefly"),
            )
        else:
            decision.redirect_to = hint
            decision.reasons.append(
                f"K/region (advisory): '{target}' is beyond the frontier"
                + (f"; '{hint}' would help first" if hint else ""))

    # ── C signals: each may only narrow further, and each is switchable ──
    if "cac_rung" in active:
        _signal_help_seeking(view, decision)
    if "cac_horizon" in active:
        _signal_cognitive_load(view, topo, concepts, decision)
    if "cac_edge" in active:
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
    gap_n = 0
    quality = None
    rushing = False
    try:
        from app.core.learner_model import get_learner_profile
        p = get_learner_profile(username)
        cog, meta = p.get("cognitive", {}), p.get("metacognitive", {})
        load = (cog.get("cognitive_load") or {}).get("index")
        # Model-side calibration first, self-report only as a fallback. See
        # `_signal_calibration` for why: jol_log is structurally starved AND
        # declared by the subject of the policy, while p_bkt_pred is neither.
        _cal = cog.get("calibration") or {}
        gap = _cal.get("gap_ratio")
        gap_n = int(_cal.get("n_wrong") or 0)
        if gap is None:
            _sr = cog.get("error_slip_vs_gap") or {}
            gap = _sr.get("gap_ratio")
            gap_n = int(_sr.get("n_wrong") or 0)
        quality = (meta.get("help_seeking") or {}).get("quality")
        rushing = bool((meta.get("pacing") or {}).get("rushing"))
        grit = (meta.get("persistence") or {}).get("grit_index")
    except Exception as e:
        logger.warning(f"[cac] learner profile unavailable for {username}: {e}")

    return LearnerView(
        username=username, certified=frozenset(certified),
        cognitive_load=load, gap_ratio=gap, gap_n_wrong=gap_n,
        help_seeking_quality=quality,
        rushing=rushing, grit_index=grit,
    )


def active_signals() -> set[str]:
    """The signal switches currently connected."""
    from app.core import config
    return {name for name in SIGNAL_SWITCHES if config.feature_enabled(name)}


def enforcing() -> bool:
    """Whether a decision may CHANGE anything, as opposed to only being recorded.

    Computing and enforcing are separate switches on purpose. With `cac_enforce`
    off the layer runs in shadow: a full decision is formed and logged on every
    turn, and no learner is affected — so the policy can be measured against real
    traffic before it is trusted with any. Callers must consult this before acting
    on a Decision; `evaluate()` cannot enforce it for them because it does not
    know what the caller intends to do with the answer.
    """
    from app.core import config
    return config.feature_enabled("cac_graph") and config.feature_enabled("cac_enforce")


def evaluate(username: str, query_concepts: Iterable[str]) -> Decision:
    """Form a decision for one turn, honouring the live switch configuration.

    Always safe to call. Returns the permissive no-op when `cac_graph` is off, so
    a disconnected layer is provably inert rather than merely quiet. Note this
    RETURNS a decision regardless of `cac_enforce` — recording it is the point of
    shadow mode; see `enforcing()` for whether it may be acted on.
    """
    from app.core import config
    if not config.feature_enabled("cac_graph"):
        return Decision()
    try:
        return decide(build_view(username), query_concepts,
                      signals=active_signals(),
                      region_gate=config.feature_enabled("cac_region_gate"))
    except Exception as e:
        # Fail OPEN, deliberately. This layer only ever narrows, so a crash here
        # must not become a block: the Sentinel's hard blocks and the K-based
        # prerequisite gate are unaffected and still run.
        logger.error(f"[cac] decision failed for {username}, passing through: {e}")
        return Decision()
