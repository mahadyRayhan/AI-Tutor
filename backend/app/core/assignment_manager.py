# backend/app/core/assignment_manager.py

import json
import uuid
import html
from datetime import datetime
from app.db.sqlite_db import db

class AssignmentManager:
    _cols_ready = False

    def _ensure_columns(self):
        """Lazily add the material-delivery columns so no migration script is needed.
        A material assignment carries a resource/uploaded file instead of a submit-answer
        question; older challenge rows keep working with these left NULL."""
        if self._cols_ready:
            return
        try:
            have = {r['name'] for r in db.fetch_all("PRAGMA table_info(assignments)")}
            for col in ("kind", "material", "material_kind", "topic"):
                if col not in have:
                    db.execute(f"ALTER TABLE assignments ADD COLUMN {col} TEXT")
        except Exception:
            pass
        self._cols_ready = True

    def create_challenge(self, teacher, student, question):
        self._ensure_columns()
        aid = str(uuid.uuid4())[:8]
        safe_q = html.escape(question)

        db.execute("""
            INSERT INTO assignments (id, teacher_id, student_id, question, status, timestamp, kind)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (aid, teacher, student, safe_q, "PENDING", datetime.now(), "challenge"))
        return aid

    def create_material_assignment(self, teacher, student, topic, material, material_kind, note=None):
        """Assign reading / code / an uploaded file to a student. Renders as a resource
        card (view + mark-as-done) student-side, not a submit-answer challenge."""
        self._ensure_columns()
        aid = str(uuid.uuid4())[:8]
        safe_note = html.escape(note or "")
        db.execute("""
            INSERT INTO assignments (id, teacher_id, student_id, question, status, timestamp,
                                     kind, material, material_kind, topic)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (aid, teacher, student, safe_note, "ASSIGNED", datetime.now(),
              "material", material, material_kind, topic))
        return aid

    def mark_material_done(self, aid):
        db.execute("UPDATE assignments SET status='DONE' WHERE id=?", (aid,))
        return True

    def submit_answer(self, aid, answer):
        safe_ans = html.escape(answer)
        db.execute("""
            UPDATE assignments 
            SET student_answer = ?, status = 'SUBMITTED' 
            WHERE id = ?
        """, (safe_ans, aid))
        return True

    def grade_assignment(self, aid, feedback):
        safe_feed = html.escape(feedback)
        db.execute("""
            UPDATE assignments 
            SET teacher_feedback = ?, status = 'GRADED' 
            WHERE id = ?
        """, (safe_feed, aid))
        return True

    def get_by_student(self, student):
        rows = db.fetch_all("SELECT * FROM assignments WHERE student_id = ?", (student,))
        return self._format_rows(rows)

    def get_pending_reviews(self, teacher):
        rows = db.fetch_all("SELECT * FROM assignments WHERE teacher_id = ? AND status = 'SUBMITTED'", (teacher,))
        return self._format_rows(rows)

    def _format_rows(self, rows):
        # Convert sqlite rows to list of dicts for API
        results = []
        for r in rows:
            keys = r.keys()
            results.append({
                "id": r['id'],
                "teacher": r['teacher_id'],
                "student": r['student_id'],
                "question": r['question'],
                "student_answer": r['student_answer'],
                "teacher_feedback": r['teacher_feedback'],
                "status": r['status'],
                "timestamp": r['timestamp'],
                "kind": (r['kind'] if 'kind' in keys else None) or "challenge",
                "material": r['material'] if 'material' in keys else None,
                "material_kind": r['material_kind'] if 'material_kind' in keys else None,
                "topic": r['topic'] if 'topic' in keys else None,
            })
        return results

assignment_manager = AssignmentManager()