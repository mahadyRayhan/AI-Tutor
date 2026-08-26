"""
Concept-canonicalization fix (dashboard "0 evidence despite certified quiz").

Root cause: the quiz/code-review paths wrote user_knowledge rows under non-canonical
concept names ("variables", "variable", "Variables and Types") while the dashboard
reads the canonical coarse topic ("Variables") by EXACT match — so the evidence was
invisible. bkt now canonicalizes concept names at every read/write boundary.

The integration test fails against pre-fix code: bkt.update("variables") created a
"variables" row and _read_row("Variables") returned None (a different row).

Run:  /opt/anaconda3/envs/agent/bin/python -m pytest tests/test_concept_canon.py -q
"""
import sqlite3
import pytest

import app.db.sqlite_db as sqlite_mod
from app.core.concept_canon import canonical_concept
from app.core import bkt_model


# ── unit: the canonicalizer ───────────────────────────────────────────────────
class TestCanonicalConcept:
    def test_case_and_plural_variants_fold_to_one(self):
        for v in ("variables", "variable", "Variables", "VARIABLES", "  variables  "):
            assert canonical_concept(v) == "Variables"

    def test_all_nine_coarse_topics_are_stable(self):
        for t in ("Variables", "Control Flow", "Functions", "Arrays", "Strings",
                  "Pointers", "Structures", "Memory Allocation", "File I/O"):
            assert canonical_concept(t) == t
            assert canonical_concept(t.lower()) == t

    def test_fine_nodes_and_aliases_left_unchanged(self):
        # Conservative scope: finer curriculum nodes are NOT folded into a coarse
        # bucket — that would rewrite how existing fine-grained rows (incl. the eval
        # cohorts) are read. They pass through untouched.
        assert canonical_concept("Variables and Types") == "Variables and Types"
        assert canonical_concept("loops") == "loops"
        assert canonical_concept("malloc") == "malloc"
        assert canonical_concept("struct") == "struct"

    def test_unknown_and_sentinel_concepts_unchanged(self):
        # Never guess/relabel unrelated names (protects graph node names).
        assert canonical_concept("quantum physics") == "quantum physics"
        assert canonical_concept("code submission") == "code submission"
        assert canonical_concept("") == ""


# ── integration: writes/reads converge on one canonical row ───────────────────
@pytest.fixture
def iso_db():
    orig = sqlite_mod.db.conn
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sqlite_mod.db.conn = conn
    sqlite_mod.db._init_tables()
    # threshold_calibrator owns tables outside _init_tables (calibration_buffer, …)
    from app.core.threshold_calibrator import calibrator
    calibrator._ensure_tables()
    try:
        yield sqlite_mod.db
    finally:
        conn.close()
        sqlite_mod.db.conn = orig


class TestEvidenceConverges:
    def test_variant_writes_land_on_canonical_row(self, iso_db):
        # Quiz path writes "variables"; a code review writes "Variable".
        bkt_model.update("stu", "variables", True, evidence_type="quiz")
        bkt_model.update("stu", "Variable", True, evidence_type="micro")

        # Exactly ONE row exists for this user in the variables family, named canonically.
        rows = iso_db.fetch_all(
            "SELECT concept, n_evidence_quiz, n_evidence_micro FROM user_knowledge "
            "WHERE username='stu'"
        )
        assert len(rows) == 1
        assert rows[0]["concept"] == "Variables"
        assert rows[0]["n_evidence_quiz"] == 1     # quiz evidence landed
        assert rows[0]["n_evidence_micro"] == 1    # micro evidence landed on the SAME row

    def test_dashboard_read_sees_quiz_evidence(self, iso_db):
        # Quiz stored under the raw lowercase name...
        bkt_model.update("stu", "variables", True, evidence_type="quiz")
        # ...the dashboard reads the canonical coarse topic and finds it.
        row = bkt_model._read_row("stu", "Variables")
        assert row is not None
        assert row["n_evidence_quiz"] == 1

    def test_fine_node_stays_separate_from_coarse(self, iso_db):
        # Conservative scope: a fine curriculum node is a DISTINCT row from the coarse
        # topic — only the case/plural variants of the coarse name merge.
        bkt_model.update("stu", "Variables and Types", True, evidence_type="quiz")
        bkt_model.update("stu", "variables", True, evidence_type="quiz")
        concepts = sorted(
            r["concept"] for r in
            iso_db.fetch_all("SELECT concept FROM user_knowledge WHERE username='stu'")
        )
        assert concepts == ["Variables", "Variables and Types"]
