"""Over-blocking guards: the cost side of access control.

A control layer that withholds more is trivially "safer" and pedagogically
useless. Each test here pins a case where the system must still answer:

  • a legitimate question whose best-matching material happens to be restricted
  • a question about an ENABLED topic whose wording brushes a disabled one
  • retrieval that must not be starved by what it is about to filter out
"""
import pytest

from app.agents.sentinel import _locks_topic
from app.core.cac_graph import permitted_chunks, OVERFETCH


# ── topic locks name a topic, not a substring of one ──────────────────────

@pytest.mark.parametrize("entity,topic", [
    ("int", "Pointers"),            # po-INT-ers
    ("C", "Control Flow"),
    ("C", "Functions"),
    ("C", "Structures"),
    ("C", "Memory Allocation"),
    ("a", "Arrays"),
    ("if", "File I/O"),
    ("or", "Pointers"),
])
def test_incidental_substrings_do_not_lock_a_topic(entity, topic):
    assert _locks_topic(entity, topic) is False, (
        f"'{entity}' would lock '{topic}', blocking legitimate questions about "
        f"an entirely different subject")


@pytest.mark.parametrize("entity,topic", [
    ("arrays", "Arrays"),
    ("Arrays", "arrays"),
    ("pointers", "Pointers"),
    ("file i/o", "File I/O"),
    ("memory allocation", "Memory Allocation"),
    ("dynamic memory allocation", "Memory Allocation"),
])
def test_real_topic_names_still_lock(entity, topic):
    assert _locks_topic(entity, topic) is True, (
        f"'{entity}' must still respect a lock on '{topic}' — under-blocking is "
        f"a security failure")


def test_empty_entity_locks_nothing():
    assert _locks_topic("", "Arrays") is False
    assert _locks_topic("arrays", "") is False


# ── retrieval is not starved by the filter ────────────────────────────────

def _chunks(n_teacher, n_student):
    out = []
    for i in range(n_teacher):
        out.append({"text": "key", "metadata": {"chunk_id": f"t{i}", "topic": "Arrays",
                                                "access_level": "teacher"}})
    for i in range(n_student):
        out.append({"text": "notes", "metadata": {"chunk_id": f"s{i}", "topic": "Arrays",
                                                  "access_level": "student"}})
    return out


def test_overfetch_multiplier_is_applied_by_every_retrieval_path():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "app" / "agents"
    for agent in ("socratic", "scaffolding", "reviewer"):
        src = (root / f"{agent}.py").read_text()
        assert "OVERFETCH" in src, (
            f"{agent}.py filters after retrieval without over-fetching, so "
            f"restricted chunks take slots the learner never gets back")


def test_overfetch_recovers_a_full_context():
    """Without the multiplier a learner keeps the leftovers; with it, a full set."""
    k = 8
    # Top-heavy with restricted material, as measured on the real exam keys.
    naive = permitted_chunks(_chunks(6, 2)[:k], "student")
    wide = permitted_chunks(_chunks(6, 2 + k * OVERFETCH)[:k * OVERFETCH], "student")[:k]
    assert len(naive) == 2
    assert len(wide) == k, "over-fetching must restore a full permitted context"


def test_students_still_get_nothing_restricted_after_overfetch():
    wide = permitted_chunks(_chunks(6, 20), "student")
    assert all(c["metadata"]["access_level"] != "teacher" for c in wide)


# ── grounding is preserved; over-blocking is fixed upstream of the prompt ──

def test_answers_stay_grounded_in_the_corpus():
    """Over-blocking is fixed by retrieving more, never by loosening grounding.

    Relaxing the grounding rule would "fix" a starved context by letting the tutor
    answer from the model's own knowledge of C — ending the retrieval guarantee the
    whole system rests on. In a RAG tutor an ungrounded answer is not a better
    answer, however good it reads, so the corpus restriction is load-bearing and
    stays exactly as it was. The cure lives upstream, in OVERFETCH.
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "app" / "agents"
           / "socratic.py").read_text()
    assert "STRICT LIMITATION" in src, "the corpus grounding rule must stay in the prompt"
    for escape_hatch in ("answer from your own knowledge",
                         "Never refuse a legitimate C",
                         "your general knowledge of C anyway"):
        assert escape_hatch not in src, (
            f"prompt permits ungrounded answers ({escape_hatch!r}) — that trades the "
            f"RAG guarantee for an over-blocking fix that OVERFETCH already provides")
