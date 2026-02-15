# backend/app/core/assignment_manager.py

import json
import os
import uuid
import html  # <--- 1. ADD THIS IMPORT
from datetime import datetime

# Adjust path logic as needed for your project structure
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
ASSIGNMENTS_FILE = os.path.join(BASE_DIR, "database", "assignments.json")

class AssignmentManager:
    def __init__(self):
        if not os.path.exists(ASSIGNMENTS_FILE):
            with open(ASSIGNMENTS_FILE, 'w') as f: json.dump({}, f)

    def _load(self):
        try:
            if not os.path.exists(ASSIGNMENTS_FILE) or os.path.getsize(ASSIGNMENTS_FILE) == 0:
                self._save({})
                return {}
            with open(ASSIGNMENTS_FILE, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            self._save({})
            return {}

    def _save(self, data):
        with open(ASSIGNMENTS_FILE, 'w') as f: json.dump(data, f, indent=2)

    def create_challenge(self, teacher, student, question):
        data = self._load()
        aid = str(uuid.uuid4())[:8]
        
        # 2. SANITIZE THE QUESTION
        safe_question = html.escape(question) # <--- SECURITY FIX
        
        data[aid] = {
            "id": aid,
            "teacher": teacher,
            "student": student,
            "question": safe_question, # Store the safe version
            "status": "PENDING", 
            "student_answer": None,
            "teacher_feedback": None,
            "timestamp": datetime.now().isoformat()
        }
        self._save(data)
        return aid

    def submit_answer(self, aid, answer):
        data = self._load()
        if aid in data:
            # 3. SANITIZE THE STUDENT ANSWER
            safe_answer = html.escape(answer) # <--- SECURITY FIX
            
            data[aid]["student_answer"] = safe_answer
            data[aid]["status"] = "SUBMITTED"
            self._save(data)
            return True
        return False

    def grade_assignment(self, aid, feedback):
        data = self._load()
        if aid in data:
            # 4. SANITIZE THE TEACHER FEEDBACK
            safe_feedback = html.escape(feedback) # <--- SECURITY FIX
            
            data[aid]["teacher_feedback"] = safe_feedback
            data[aid]["status"] = "GRADED"
            self._save(data)
            return True
        return False

    def get_by_student(self, student):
        data = self._load()
        return [v for k,v in data.items() if v['student'] == student]

    def get_pending_reviews(self, teacher):
        data = self._load()
        return [v for k,v in data.items() if v['teacher'] == teacher and v['status'] == "SUBMITTED"]

assignment_manager = AssignmentManager()