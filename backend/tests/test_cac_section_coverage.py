"""Every disclosure surface must be governed by the rung cap.

Run: /opt/anaconda3/envs/agent/bin/python -m pytest backend/tests/test_cac_section_coverage.py -q

`section_allowed` fails OPEN on headers it does not recognise — deliberately, so
an unmapped section never blanks a response. The cost is that the filter's
coverage degrades silently as the prompts evolve: add a "## Starter Skeleton"
and it passes at every rung, with nothing to notice.

These tests convert that silent runtime fail-open into a loud test failure.
Found by simulation: three diagnostic headers and nine planning headers were
unmapped, and two of the four prompt builders never received the cap at all.
"""
import inspect
import re

import pytest

from app.agents import socratic as soc
from app.agents.socratic import SocraticTutorAgent as SocraticAgent
from app.core.cac_graph import SECTION_MIN_RUNG, Rung, section_allowed


_SRC = inspect.getsource(soc)
# A real section header is "## Name" followed by its placeholder — either a
# "[...]" description or a fenced block. Requiring the follower is what keeps
# prose mentions of "## " in comments and rules out of the scan.
_HEADERS = sorted({
    m.group(1).strip()
    for m in re.finditer(
        r'(?:^|")\s*##\s+([^\n\\"\[\]{}#]{2,45}?)\s*(?:\\n|\n)\s*(?:\[|```)',
        _SRC, re.MULTILINE)
    if not m.group(1).strip().startswith("Phase ")
})


def test_headers_were_actually_found():
    assert len(_HEADERS) > 10, f"header scan looks broken: {_HEADERS}"


def test_every_emitted_section_is_classified():
    """An unmapped header passes at EVERY rung, including NONE."""
    unmapped = [h for h in _HEADERS if h not in SECTION_MIN_RUNG]
    assert not unmapped, (
        "these sections are emitted but not in SECTION_MIN_RUNG, so the "
        f"disclosure cap does not govern them: {unmapped}")


def test_code_bearing_sections_are_blocked_at_hint():
    """The plan's property test, at the section level: no code under HINT."""
    for header in ("Example", "Worked Example", "Example from Class",
                   "Starter Skeleton"):
        assert header in SECTION_MIN_RUNG, f"{header} unmapped"
        assert not section_allowed(header, Rung.HINT), \
            f"'{header}' emits code the learner did not write; it must not survive HINT"


def test_diagram_survives_hint():
    """DIAGRAM < HINT, so a capped learner still gets the picture."""
    assert section_allowed("Visual Model", Rung.HINT)
    assert section_allowed("System Design (Diagram)", Rung.HINT)


def test_orienting_prose_survives_every_rung_above_refusal():
    for header, rung in SECTION_MIN_RUNG.items():
        if rung is Rung.ORIENT:
            assert section_allowed(header, Rung.ORIENT), header


# ── Every builder that can disclose must receive the cap ────────────────────

@pytest.mark.parametrize("builder", [
    "_build_concept_prompt",
    "_build_diagnostic_prompt",
    "_build_complex_plan_prompt",
])
def test_prompt_builder_accepts_the_cap(builder):
    """Regression: two of these silently ignored it.

    `_build_diagnostic_prompt` is the DEBUG path — a learner capped at HINT
    could reach full disclosure by pasting code and asking why it breaks.
    `_build_complex_plan_prompt` emits a "Starter Skeleton" of real C.
    """
    sig = inspect.signature(getattr(SocraticAgent, builder))
    assert "rung_cap" in sig.parameters, f"{builder} cannot see the cap"


@pytest.mark.parametrize("builder", [
    "_build_concept_prompt",
    "_build_diagnostic_prompt",
    "_build_complex_plan_prompt",
])
def test_prompt_builder_applies_the_cap(builder):
    """Accepting the parameter is not enough — it has to be used."""
    src = inspect.getsource(getattr(SocraticAgent, builder))
    assert "disclosure_directive" in src, f"{builder} takes rung_cap but ignores it"


def test_every_builder_call_site_passes_state_rung_cap():
    """The routing must hand the cap to whichever builder it picked."""
    src = inspect.getsource(SocraticAgent.process)
    for builder in ("_build_concept_prompt", "_build_diagnostic_prompt",
                    "_build_complex_plan_prompt"):
        i = src.find(builder + "(")
        if i == -1:
            continue
        # Generous window: the concept builder's call runs to nine arguments and
        # rung_cap sits near its end.
        call = src[i:i + 900]
        assert "rung_cap=state.rung_cap" in call, \
            f"{builder} is called without the turn's cap"
