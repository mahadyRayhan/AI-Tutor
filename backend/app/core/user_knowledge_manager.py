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
    
    def set_goal(self, username: str, goal: str):
        data = self._load_db()
        # Initialize user dict if new
        if username not in data: 
            # Note: Changing structure slightly. 
            # Old: data[username] = ["concept1"]
            # New: data[username] = {"known": ["concept1"], "goal": "Master Loops"}
            # We need to migrate gracefully.
            data[username] = {"known": [], "goal": goal}
        elif isinstance(data[username], list):
            # Migration logic for existing users
            data[username] = {"known": data[username], "goal": goal}
        else:
            data[username]["goal"] = goal
            
        self._save_db(data)

    def get_goal(self, username: str) -> str:
        data = self._load_db()
        user_data = data.get(username, {})
        if isinstance(user_data, list): return None # Legacy format
        return user_data.get("goal")

    # UPDATE get/mark methods to handle the new dict structure
    def get_known_concepts(self, username: str) -> List[str]:
        data = self._load_db()
        user_data = data.get(username, [])
        if isinstance(user_data, list): return user_data
        return user_data.get("known", [])

    def mark_concept_as_known(self, username: str, concept: str):
        data = self._load_db()
        user_data = data.get(username, {"known": [], "goal": None})
        
        # Handle legacy list format
        if isinstance(user_data, list): 
            user_data = {"known": user_data, "goal": None}
            
        known_set = set(user_data["known"])
        known_set.add(concept)
        user_data["known"] = list(known_set)
        
        data[username] = user_data
        self._save_db(data)

knowledge_manager = UserKnowledgeManager()