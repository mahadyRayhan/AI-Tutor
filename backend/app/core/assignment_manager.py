# backend/app/core/assignment_manager.py

import json
import uuid
import html
from datetime import datetime
from app.db.sqlite_db import db

class AssignmentManager:
    def create_challenge(self, teacher, student, question):
        aid = str(uuid.uuid4())[:8]
        safe_q = html.escape(question)
        
        db.execute("""
            INSERT INTO assignments (id, teacher_id, student_id, question, status, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (aid, teacher, student, safe_q, "PENDING", datetime.now()))
        return aid

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
            results.append({
                "id": r['id'],
                "teacher": r['teacher_id'],
                "student": r['student_id'],
                "question": r['question'],
                "student_answer": r['student_answer'],
                "teacher_feedback": r['teacher_feedback'],
                "status": r['status'],
                "timestamp": r['timestamp']
            })
        return results

assignment_manager = AssignmentManager()