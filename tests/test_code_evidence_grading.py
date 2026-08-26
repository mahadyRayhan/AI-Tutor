"""
Micro/Code evidence tiers must GRADE the answer, not just look for punctuation.

Root cause: certification is conjunctive over three tiers (quiz, micro, code), but
only the quiz tier ever checked correctness. The other two scored on characters:

    # mastery exam, stage 2 (micro)
    has_code = any(c in q for c in [";", "{", "}", "=", "(", ")"])
    bkt.update(username, concept, has_code, evidence_type="micro")

    # mastery exam, stage 3 (code)
    has_code = any(c in q for c in [";", "{", "}"])

    # "Your Turn" challenge path — worse, the verdict was hardcoded
    _bkt_micro.update(username, challenge_topic, True, evidence_type="micro")

    # reviewer code tier — keyword-scanned the TUTOR'S OWN PROSE, not the code
    has_errors = any(kw in full_response.lower() for kw in _error_keywords)

So typing ";" cleared two of the three tiers required to certify, and a review that
merely cautioned "watch for an infinite loop if n grows" scored the student as
failing. All four sites now route through BaseAgent._llm_grade_code.

These tests fail against pre-fix code: a bare ";" returned is_correct True.

Run:  /opt/anaconda3/envs/agent/bin/python -m pytest tests/test_code_evidence_grading.py -q
"""
import asyncio
import logging

import pytest

from app.agents.base import BaseAgent


OK_JSON = '{"valid_c":true,"uses_concept":true,"status":"TRUE","feedback":"Nice."}'


class _StubLLM:
    """Records how many times the judge was actually invoked."""

    def __init__(self, reply=OK_JSON):
        self.reply = reply
        self.calls = 0

    def generate_response(self, prompt):
        self.calls += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class _Grader(BaseAgent):
    async def process(self, state):  # pragma: no cover - required by ABC
        yield {}


def _grade(code, concept="Variables", tier="micro", reply=OK_JSON):
    llm = _StubLLM(reply)
    grader = _Grader(llm, logging.getLogger("test"))
    result = asyncio.run(grader._llm_grade_code(code, concept, tier))
    return result, llm.calls


# ── the farming attack ────────────────────────────────────────────────────────
class TestTokenAnswersEarnNoEvidence:
    """Punctuation-only answers cleared micro+code before the fix."""

    @pytest.mark.parametrize("token", [";", "  ;  ", "{}", "=", "();", "int", ""])
    def test_token_answer_is_incorrect(self, token):
        result, _ = _grade(token)
        assert result["is_correct"] is False

    @pytest.mark.parametrize("token", [";", "{}", "="])
    def test_token_answer_short_circuits_before_the_llm(self, token):
        """Structural rejects must not spend an API call."""
        _, calls = _grade(token)
        assert calls == 0

    def test_prose_is_not_code(self):
        result, _ = _grade("I think you declare a variable with int")
        assert result["is_correct"] is False


# ── real answers still pass ───────────────────────────────────────────────────
class TestRealAnswersReachTheJudge:
    @pytest.mark.parametrize("code", [
        "int total_count = 5;",
        'int age = 25;\nprintf("Age: %d\\n", age);',
    ])
    def test_valid_code_is_graded_by_the_llm(self, code):
        result, calls = _grade(code)
        assert result["is_correct"] is True
        assert calls == 1


# ── the conjunction is enforced locally, not delegated to the judge ───────────
class TestConjunction:
    def test_wrong_concept_fails_even_when_status_says_true(self):
        """A judge that contradicts itself must not certify."""
        result, _ = _grade(
            "int x = 5;", concept="Pointers",
            reply='{"valid_c":true,"uses_concept":false,"status":"TRUE","feedback":"No pointer."}',
        )
        assert result["is_correct"] is False

    def test_invalid_c_fails_even_when_status_says_true(self):
        result, _ = _grade(
            "x = 5", reply='{"valid_c":false,"uses_concept":true,"status":"TRUE","feedback":"Not C."}',
        )
        assert result["is_correct"] is False


# ── outage must not penalise a real answer ────────────────────────────────────
class TestUngradable:
    def test_llm_failure_returns_none_not_false(self):
        """None tells the caller to record NO evidence.

        Recording False would punish a student for an API outage; recording True
        is the original bug. Skipping is the only honest option.
        """
        result, _ = _grade("int x = 5;", reply=RuntimeError("api down"))
        assert result["is_correct"] is None

    def test_malformed_judge_response_returns_none(self):
        result, _ = _grade("int x = 5;", reply="not json at all")
        assert result["is_correct"] is None


# ── evidence attribution ──────────────────────────────────────────────────────
# Second defect found by the same manual test: the grader passed the code, but the
# evidence was filed under "code submission" instead of the student's topic.
# `entities` is extracted from the submitted text, and on a code block that yields
# tokens like "printf"/"int"/"fork" — so real work landed on junk rows while the
# student's actual topic stayed at 0/3, making the Code tier (and therefore
# certification) unreachable via direct code submission.
class TestAttributionGuard:
    """`is_attributable_concept` decides what may hold mastery evidence."""

    @pytest.mark.parametrize("junk", [
        "printf", "int", "fork", "main", "code submission", "general",
        "unknown", "declare", "Int", "  ", "",
    ])
    def test_non_concepts_are_rejected(self, junk):
        from app.core.concept_canon import is_attributable_concept
        assert is_attributable_concept(junk) is False

    @pytest.mark.parametrize("concept", [
        "Strings", "Variables", "Pointers", "Control Flow", "Memory Allocation",
        "Variables and Types", "Loops", "malloc", "Linked List",
    ])
    def test_real_concepts_are_accepted(self, concept):
        from app.core.concept_canon import is_attributable_concept
        assert is_attributable_concept(concept) is True


class TestBktRefusesJunkWrites:
    """Defense in depth: no call path may create a junk row."""

    @pytest.mark.parametrize("junk", ["printf", "int", "code submission"])
    def test_update_refuses_non_concept(self, junk, tmp_path, monkeypatch):
        from app.core import bkt_model

        calls = []
        monkeypatch.setattr(bkt_model, "_read_row",
                            lambda *a, **k: calls.append(a) or None)
        result = bkt_model.update("someuser", junk, True, evidence_type="code")

        assert result == 0.0, "a junk write must be refused"
        assert calls == [], "refused write must not touch the database"

    def test_update_still_accepts_a_real_concept(self, monkeypatch):
        """The guard must not block legitimate writes."""
        from app.core.concept_canon import is_attributable_concept
        assert is_attributable_concept("Strings") is True


# ── judge-reply parsing ───────────────────────────────────────────────────────
# Third defect from the same manual test: a student pasted correct code repeatedly
# and the ledger stayed at "~2 more" forever. The judge reply was not parseable, so
# the grader returned None (= ungradable = record nothing) while the ledger still
# rendered — making a dropped write look like a submission that simply didn't count.
#
# `LLMInterface.generate_response` does NOT raise on provider failure; it returns a
# string starting "LLM_ERROR:". A JSON-only parser turned that into a silent drop.
class TestJudgeReplyParsing:
    GOOD_CODE = "#include <stdio.h>\nint main() { int car = 12; return 0; }"

    @pytest.mark.parametrize("reply,expected", [
        ("VALID_C: yes\nUSES_CONCEPT: yes\nFEEDBACK: Clean work.", True),
        ("VALID_C: no\nUSES_CONCEPT: yes\nFEEDBACK: Not valid C.", False),
        ("VALID_C: yes\nUSES_CONCEPT: no\nFEEDBACK: Wrong concept.", False),
        ("valid_c : Yes\nuses_concept:  YES\nfeedback: ok", True),   # sloppy spacing
        ('{"valid_c":true,"uses_concept":true,"feedback":"Nice"}', True),  # JSON fallback
    ])
    def test_parseable_replies_yield_a_verdict(self, reply, expected):
        result, _ = _grade(self.GOOD_CODE, "Variables", "code", reply=reply)
        assert result["is_correct"] is expected

    def test_provider_error_string_is_ungradable_not_a_failure(self):
        """generate_response returns this string rather than raising."""
        result, _ = _grade(self.GOOD_CODE, "Variables", "code",
                           reply="LLM_ERROR: Response blocked (Reason: SAFETY).")
        assert result["is_correct"] is None

    def test_unparseable_prose_is_ungradable(self):
        result, _ = _grade(self.GOOD_CODE, "Variables", "code",
                           reply="Yes, this looks like valid C to me.")
        assert result["is_correct"] is None

    def test_ungradable_feedback_tells_the_student_it_was_not_counted(self):
        """The silent-drop bug: the student must be told, or they re-paste forever."""
        result, _ = _grade(self.GOOD_CODE, "Variables", "code", reply="LLM_ERROR: down")
        assert "wasn't recorded" in result["feedback"] or "wasn" in result["feedback"]
