"""Certification rule (c): the three correct answers must be three DIFFERENT items.

Answering one quiz question right three times, or pasting one line of C three
times, is one piece of knowledge however many times it is submitted. These tests
pin that, plus the two compatibility rules that keep the change from re-locking
learners whose evidence predates item ids.
"""
import pytest

from app.core import bkt_model
from app.core.bkt_model import bkt, item_key, n_distinct_evidence, D_MIN, N_MIN
from app.db.sqlite_db import db


@pytest.fixture
def user():
    name = "variety_probe"
    for table, col in (("user_knowledge", "username"), ("evidence_log", "username")):
        db.execute(f"DELETE FROM {table} WHERE {col}=?", (name,))
    yield name
    for table, col in (("user_knowledge", "username"), ("evidence_log", "username")):
        db.execute(f"DELETE FROM {table} WHERE {col}=?", (name,))


def _drill(user, concept, tier, item, times):
    for _ in range(times):
        bkt.update(user, concept, True, evidence_type=tier, item_id=item_key(item))


# ── item_key ──────────────────────────────────────────────────────────────

def test_item_key_ignores_whitespace_and_case():
    assert item_key("int x = 1;") == item_key("  INT   x = 1;  ")


def test_item_key_separates_different_items():
    assert item_key("int x = 1;") != item_key("int y = 2;")


def test_item_key_of_nothing_is_none():
    assert item_key(None) is None and item_key("   ") is None


# ── the rule ──────────────────────────────────────────────────────────────

def test_repeating_one_item_does_not_build_variety(user):
    _drill(user, "Arrays", "micro", "int a[3];", 5)
    assert n_distinct_evidence(user, "Arrays", "micro", 5) == 1, \
        "five submissions of one line are one item"


def test_different_items_each_count(user):
    for line in ("int a[3];", "a[0] = 1;", "printf(\"%d\", a[0]);"):
        _drill(user, "Arrays", "micro", line, 1)
    assert n_distinct_evidence(user, "Arrays", "micro", 3) == D_MIN


def test_repetition_cannot_certify(user):
    """Same item over and over: P̃ and n_min are satisfied, variety is not."""
    for tier in ("quiz", "micro", "code"):
        _drill(user, "Arrays", tier, "the one answer", 12)
    row = bkt_model._read_row(user, "Arrays")
    assert (row["n_evidence_quiz"] or 0) >= N_MIN, "the old counter is satisfied"
    assert bkt.is_mastered(user, "Arrays") is False, \
        "one item repeated must not certify a topic"


def test_legacy_rows_without_item_ids_still_count(user):
    """Evidence logged before item ids existed keeps its full weight."""
    for _ in range(3):
        bkt.update(user, "Arrays", True, evidence_type="quiz")     # no item_id
    assert n_distinct_evidence(user, "Arrays", "quiz", 3) == 3


def test_no_evidence_rows_falls_back_to_the_counter(user):
    """A telemetry outage must not become a silent certification freeze."""
    assert n_distinct_evidence(user, "Nothing Logged", "quiz", 7) == 7


# ── telling the learner ───────────────────────────────────────────────────

def test_repeat_item_is_detected(user):
    from app.core.bkt_model import is_repeat_item
    key = item_key("int a[3];")
    assert is_repeat_item(user, "Arrays", "micro", key) is False, "first time is not a repeat"
    bkt.update(user, "Arrays", True, evidence_type="micro", item_id=key)
    assert is_repeat_item(user, "Arrays", "micro", key) is True
    assert is_repeat_item(user, "Arrays", "code", key) is False, "tiers are counted apart"
    assert is_repeat_item(user, "Arrays", "micro", item_key("a[0] = 1;")) is False


def test_wrong_answers_are_not_precedent(user):
    from app.core.bkt_model import is_repeat_item
    key = item_key("int a[3];")
    bkt.update(user, "Arrays", False, evidence_type="micro", item_id=key)
    assert is_repeat_item(user, "Arrays", "micro", key) is False


def test_repeat_note_is_wired_into_the_exam():
    """The message exists and says what to do about it."""
    from app.agents.cot_rag_agent import REPEAT_ITEM_NOTE
    assert "different" in REPEAT_ITEM_NOTE.lower()
    src = (__import__("pathlib").Path(__file__).resolve().parents[1]
           / "app" / "agents" / "cot_rag_agent.py").read_text()
    assert src.count("REPEAT_ITEM_NOTE if _repeat") == 2, \
        "both the micro and the code stage must say it"
