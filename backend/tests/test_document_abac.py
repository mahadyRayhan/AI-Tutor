"""Document-level ABAC: teacher-only material and locked topics, on EVERY path.

The filter used to live inside SocraticTutorAgent alone, so "explain arrays" was
governed and "write a program using arrays" was not — the same rephrasing bypass
that CAC's complete-mediation fix closed for the disclosure cap. These tests pin
the shared implementation and that each retrieval path calls it.
"""
import pathlib

import pytest

from app.core.cac_graph import permitted_chunks


def _chunk(cid, topic="Arrays", access="student"):
    return {"text": cid, "metadata": {"chunk_id": cid, "topic": topic,
                                      "access_level": access}}


def test_students_never_see_teacher_material():
    out = permitted_chunks([_chunk("a"), _chunk("solution", access="teacher")], "student")
    assert [c["metadata"]["chunk_id"] for c in out] == ["a"]


def test_teachers_do():
    out = permitted_chunks([_chunk("solution", access="teacher")], "teacher")
    assert len(out) == 1


def test_locked_topics_are_dropped(monkeypatch):
    from app.core import settings_manager
    monkeypatch.setattr(settings_manager.settings_manager, "get_settings",
                        lambda: {"Arrays": False, "Strings": True})
    out = permitted_chunks([_chunk("a", topic="Arrays"), _chunk("s", topic="Strings")],
                           "student")
    assert [c["metadata"]["chunk_id"] for c in out] == ["s"]


def test_unknown_topic_is_allowed(monkeypatch):
    from app.core import settings_manager
    monkeypatch.setattr(settings_manager.settings_manager, "get_settings",
                        lambda: {"Arrays": False})
    assert len(permitted_chunks([_chunk("g", topic="General")], "student")) == 1


def test_missing_metadata_does_not_crash():
    assert permitted_chunks([{"text": "x"}], "student") == [{"text": "x"}]
    assert permitted_chunks(None, "student") == []


@pytest.mark.parametrize("agent", ["socratic", "scaffolding", "reviewer"])
def test_every_retrieval_path_applies_it(agent):
    src = (pathlib.Path(__file__).resolve().parents[1] / "app" / "agents"
           / f"{agent}.py").read_text()
    assert "permitted_chunks" in src, (
        f"{agent}.py retrieves documents without the document ABAC — a student "
        "can reach teacher-only material by routing to this agent instead")


def test_admins_see_teacher_material_too():
    assert len(permitted_chunks([_chunk("s", access="teacher")], "admin")) == 1


def test_missing_role_is_treated_as_student():
    assert permitted_chunks([_chunk("s", access="teacher")], None) == []
