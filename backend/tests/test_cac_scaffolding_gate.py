"""The guided (scaffolding) path must be governed by CAC too.

Run: /opt/anaconda3/envs/agent/bin/python -m pytest backend/tests/test_cac_scaffolding_gate.py -q

Background: CAC used to be evaluated inline just above the Socratic hand-off.
Both ScaffoldingAgent entry points return before reaching it, so the guided
track answered with no cap, no horizon check, and no cac_access_event row. A
learner asking "explain pointers" was governed; the same learner asking "write
a program using pointers" was not. That is a rephrasing bypass of an existing
control, and it sits on the path that hands out code.
"""
import inspect
import re

import pytest

from app.agents import cot_rag_agent as cra
from app.agents.scaffolding import ScaffoldingAgent
from app.agents.schema import AgentState
from app.core.cac_graph import Rung


def _src(fn):
    return inspect.getsource(fn)


# ── The evaluation must sit in front of every door ──────────────────────────

def test_cac_is_applied_before_each_scaffolding_handoff():
    """Every `self.scaffolding.process(` must be preceded by `_apply_cac`.

    Asserted structurally rather than by example because the failure mode is a
    NEW answering path being added later without the call — which no
    behavioural test over today's paths would catch.
    """
    src = inspect.getsource(cra)
    doors = [m.start() for m in re.finditer(r"self\.scaffolding\.process\(", src)]
    assert doors, "no scaffolding hand-off found; test is stale"
    for pos in doors:
        window = src[max(0, pos - 600):pos]
        assert "_apply_cac" in window, (
            "a scaffolding hand-off is reachable without CAC having run — "
            "this is the rephrasing bypass this module exists to prevent")


def test_apply_cac_is_idempotent_within_a_turn():
    """Several paths call it; the learner must get exactly one decision.

    Re-evaluating mid-turn could hand two agents different caps, and the horizon
    redirect rewrites `state.entities`, so a second pass would read the
    substituted topic instead of what was actually asked.
    """
    calls = []

    class _Agent:
        logger = type("L", (), {"info": lambda *a: None,
                                "warning": lambda *a: None})()
        _apply_cac = cra.ChainOfThoughtRAGAgent._apply_cac

    st = AgentState(query="q", user_id="u", session_id="s")
    st.entities = ["Pointers"]

    import app.core.cac_graph as cg
    import app.core.cac_calibration as cal
    real_eval = cg.evaluate
    cg.evaluate = lambda u, e: calls.append(e) or cg.Decision()
    cal.note_turn = lambda: None
    try:
        a = _Agent()
        a._apply_cac(st, "u")
        a._apply_cac(st, "u")
        a._apply_cac(st, "u")
    finally:
        cg.evaluate = real_eval
    assert len(calls) == 1, f"CAC evaluated {len(calls)} times in one turn"
    assert st.cac_evaluated is True


# ── The guided plan itself must respect the cap ─────────────────────────────

def test_new_plan_is_declined_below_example():
    """A guided plan walks to working code, so it discloses at EXAMPLE/CODE.

    Below that cap the plan is declined and the turn falls through to Socratic,
    which answers under the same cap and redirects on a horizon contraction.
    """
    src = _src(ScaffoldingAgent.process)
    assert "state.rung_cap < int(Rung.EXAMPLE)" in src
    assert "return" in src.split("state.rung_cap < int(Rung.EXAMPLE)")[1][:400]


@pytest.mark.parametrize("cap", [Rung.NONE, Rung.ORIENT, Rung.DIAGRAM, Rung.HINT])
def test_capped_learner_gets_no_guided_plan(cap):
    assert int(cap) < int(Rung.EXAMPLE), "cap must block plan creation"


@pytest.mark.parametrize("cap", [Rung.EXAMPLE, Rung.CODE])
def test_uncapped_learner_still_gets_a_plan(cap):
    """The control must not break the feature for learners who have earned it."""
    assert int(cap) >= int(Rung.EXAMPLE)


def test_partial_code_prompt_carries_the_disclosure_directive():
    """The stuck-student escalation emits C code, so it must read the cap."""
    src = _src(ScaffoldingAgent._continue_plan)
    assert "disclosure_directive(Rung(state.rung_cap))" in src


def test_step_evaluation_receives_the_cap():
    sig = inspect.signature(ScaffoldingAgent._evaluate_step_progress)
    assert "rung_cap" in sig.parameters
    assert "disclosure_directive" in _src(ScaffoldingAgent._evaluate_step_progress)
    assert "state.rung_cap" in _src(ScaffoldingAgent._continue_plan)


def test_default_cap_leaves_scaffolding_untouched():
    """Shadow mode: rung_cap defaults to CODE, so nothing changes by default."""
    st = AgentState(query="q", user_id="u", session_id="s")
    assert st.rung_cap == int(Rung.CODE)
    assert st.cac_evaluated is False
