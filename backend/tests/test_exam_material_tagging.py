"""Every file under resources/exams must be indexed as teacher-only.

The evaluation plan needs material that a student must never receive: without it,
a containment result is measured against an empty set. What makes these files
restricted is a filename rule (`_determine_metadata` tags anything named exam /
solution / quiz), which is easy to defeat by accident — `Lab3_answers.md` would
be indexed as student-readable. These tests pin the tagging and the filter.

The Chroma checks skip when no index is present, so a fresh clone still passes.
"""
import os

import pytest

from app.core import config
from app.core.cac_graph import permitted_chunks
from app.processing.data_processing import _determine_metadata

EXAM_DIR = str(config.EXAMS_PATH)


def _exam_filenames():
    if not os.path.isdir(EXAM_DIR):
        return []
    return sorted(f for f in os.listdir(EXAM_DIR)
                  if f.lower().endswith((".md", ".txt", ".c")))


@pytest.mark.parametrize("filename", _exam_filenames())
def test_every_exam_file_is_tagged_teacher(filename):
    assert _determine_metadata(filename)["access_level"] == "teacher", (
        f"{filename} would be indexed as student-readable. Exam material must be "
        f"named so it contains 'exam', 'solution' or 'quiz'.")


def test_exams_directory_is_ingested_on_a_full_rebuild():
    assert config.EXAMS_PATH in config.RESOURCE_PATHS, (
        "resources/exams is not in RESOURCE_PATHS, so a rebuilt index would "
        "silently lose the teacher-only material")


def _teacher_chunks():
    try:
        import chromadb
        col = chromadb.PersistentClient(path=config.DEFAULT_VECTOR_DB_PATH) \
            .get_or_create_collection("ai_tutor_collection")
        got = col.get(include=["metadatas"])
    except Exception:
        return None
    metas = got.get("metadatas") or []
    if not metas:
        return None
    return metas


def test_indexed_exam_chunks_are_teacher_only():
    metas = _teacher_chunks()
    if metas is None:
        pytest.skip("no populated vector index in this environment")
    exam_docs = set(_exam_filenames())
    indexed = [m for m in metas if m.get("document_name") in exam_docs]
    if not indexed:
        pytest.skip("exam material not ingested here — run scripts/ingest_exam_solutions.py")
    mislabelled = [m["document_name"] for m in indexed
                   if m.get("access_level") != "teacher"]
    assert not mislabelled, f"indexed as student-readable: {sorted(set(mislabelled))}"


def test_students_receive_none_of_it():
    metas = _teacher_chunks()
    if metas is None:
        pytest.skip("no populated vector index in this environment")
    chunks = [{"text": "...", "metadata": m} for m in metas
              if m.get("access_level") == "teacher"]
    if not chunks:
        pytest.skip("no teacher-only chunks indexed here")
    assert permitted_chunks(chunks, "student") == []
    assert len(permitted_chunks(chunks, "teacher")) == len(chunks)
