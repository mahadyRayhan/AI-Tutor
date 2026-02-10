import json
import os
import uuid
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
ASSIGNMENTS_FILE = os.path.join(BASE_DIR, "database", "assignments.json")

class AssignmentManager:
    def __init__(self):
        if not os.path.exists(ASSIGNMENTS_FILE):
            with open(ASSIGNMENTS_FILE, 'w') as f: json.dump({}, f)

    def _load(self):
        try:
            # Check if file exists and has content
            if not os.path.exists(ASSIGNMENTS_FILE) or os.path.getsize(ASSIGNMENTS_FILE) == 0:
                self._save({}) # Reset to empty dict
                return {}
                
            with open(ASSIGNMENTS_FILE, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            print(f"⚠️ Warning: {ASSIGNMENTS_FILE} was corrupted. Resetting.")
            self._save({}) # Reset if corrupted
            return {}

    def _save(self, data):
        with open(ASSIGNMENTS_FILE, 'w') as f: json.dump(data, f, indent=2)

    def create_challenge(self, teacher, student, question):
        data = self._load()
        aid = str(uuid.uuid4())[:8]
        data[aid] = {
            "id": aid,
            "teacher": teacher,
            "student": student,
            "question": question,
            "status": "PENDING", # PENDING -> SUBMITTED -> GRADED
            "student_answer": None,
            "teacher_feedback": None,
            "timestamp": datetime.now().isoformat()
        }
        self._save(data)
        return aid

    def submit_answer(self, aid, answer):
        data = self._load()
        if aid in data:
            data[aid]["student_answer"] = answer
            data[aid]["status"] = "SUBMITTED"
            self._save(data)
            return True
        return False

    def grade_assignment(self, aid, feedback):
        data = self._load()
        if aid in data:
            data[aid]["teacher_feedback"] = feedback
            data[aid]["status"] = "GRADED"
            self._save(data)
            return True
        return False

    def get_by_student(self, student):
        data = self._load()
        return [v for k,v in data.items() if v['student'] == student]

    def get_pending_reviews(self, teacher):
        data = self._load()
        # Return items created by teacher that are SUBMITTED but not GRADED
        return [v for k,v in data.items() if v['teacher'] == teacher and v['status'] == "SUBMITTED"]

assignment_manager = AssignmentManager()