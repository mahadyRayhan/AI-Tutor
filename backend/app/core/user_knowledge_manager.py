# backend/app/core/user_knowledge_manager.py
import json
import os
from typing import List, Set

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
KNOWLEDGE_FILE = os.path.join(BASE_DIR, "database", "user_knowledge.json")

class UserKnowledgeManager:
    def __init__(self):
        self.file_path = KNOWLEDGE_FILE
        self._ensure_file_exists()

    def _ensure_file_exists(self):
        if not os.path.exists(self.file_path):
            with open(self.file_path, 'w') as f:
                json.dump({}, f)

    def _load_db(self) -> dict:
        try:
            with open(self.file_path, 'r') as f:
                return json.load(f)
        except:
            return {}

    def _save_db(self, data: dict):
        with open(self.file_path, 'w') as f:
            json.dump(data, f, indent=2)

    def get_known_concepts(self, username: str) -> List[str]:
        data = self._load_db()
        return data.get(username, [])

    def mark_concept_as_known(self, username: str, concept: str):
        """Adds a concept to the user's known list."""
        data = self._load_db()
        user_knowledge = set(data.get(username, []))
        
        # Normalize string (simple lowercase check)
        user_knowledge.add(concept)
        
        data[username] = list(user_knowledge)
        self._save_db(data)

    def has_mastered(self, username: str, concept: str) -> bool:
        known = self.get_known_concepts(username)
        # Simple fuzzy match
        return any(concept.lower() in k.lower() for k in known)

knowledge_manager = UserKnowledgeManager()