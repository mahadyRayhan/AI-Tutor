# backend/app/core/user_knowledge_manager.py

from datetime import datetime
from typing import List
from app.db.sqlite_db import db

class UserKnowledgeManager:
    def get_known_concepts(self, username: str) -> List[str]:
        """Returns list of concepts the user has mastered."""
        rows = db.fetch_all("SELECT concept FROM user_knowledge WHERE username = ?", (username,))
        return [r['concept'] for r in rows]

    def mark_concept_as_known(self, username: str, concept: str):
        """Adds a concept to the DB (Ignores duplicates)."""
        if not concept:
            return
            
        concept_clean = concept.strip()
        
        # If it has a question mark, or is longer than 3 words, it's a sentence, not a concept.
        if "?" in concept_clean or len(concept_clean.split()) > 3:
            print(f"🛡️ [DB Guard] Refused to save junk concept: '{concept_clean}'")
            return

        db.execute("""
            INSERT OR IGNORE INTO user_knowledge (username, concept, timestamp)
            VALUES (?, ?, ?)
        """, (username, concept_clean, datetime.now()))

    def has_mastered(self, username: str, concept: str) -> bool:
        """Checks if a concept is known (Fuzzy match)."""
        # Get all known concepts
        known = self.get_known_concepts(username)
        # Check for partial match (e.g. "Arrays" matches "Array Declaration")
        concept_lower = concept.lower()
        return any(concept_lower in k.lower() or k.lower() in concept_lower for k in known)
    
    def set_goal(self, username: str, goal: str):
        """Upsert user goal."""
        # Check if exists
        exists = db.fetch_one("SELECT 1 FROM user_goals WHERE username = ?", (username,))
        if exists:
            db.execute("UPDATE user_goals SET goal_text = ?, updated_at = ? WHERE username = ?", (goal, datetime.now(), username))
        else:
            db.execute("INSERT INTO user_goals (username, goal_text, updated_at) VALUES (?, ?, ?)", (username, goal, datetime.now()))

    def get_goal(self, username: str) -> str:
        row = db.fetch_one("SELECT goal_text FROM user_goals WHERE username = ?", (username,))
        return row['goal_text'] if row else None

knowledge_manager = UserKnowledgeManager()