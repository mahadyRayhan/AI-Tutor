# backend/app/core/history_manager.py
import json
import os
from datetime import datetime
from typing import List, Dict

# Using relative path logic
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
HISTORY_FILE = os.path.join(BASE_DIR, "database", "chat_history.json")

class HistoryManager:
    def __init__(self):
        self.file_path = HISTORY_FILE
        self._ensure_file_exists()

    def _ensure_file_exists(self):
        if not os.path.exists(self.file_path):
            with open(self.file_path, 'w') as f:
                json.dump([], f)

    def log_interaction(self, username: str, query: str, intent: str, response: str, topic: str, metadata: dict = None):
        """
        Saves a chat interaction with Research Metadata.
        """
        entry = {
            "timestamp": datetime.now().isoformat(),
            "username": username,
            "query": query,
            "intent": intent,
            "topic": topic,
            # CAUSAL DATA POINTS
            "metadata": metadata or {}, 
            "response_snippet": response[:200]
        }
        
        try:
            with open(self.file_path, 'r') as f:
                history = json.load(f)
            history.append(entry)
            with open(self.file_path, 'w') as f:
                json.dump(history, f, indent=2)
        except Exception as e:
            print(f"Error logging history: {e}")

    def get_student_history(self, username: str) -> List[Dict]:
        """Get all logs for a specific student."""
        try:
            with open(self.file_path, 'r') as f:
                history = json.load(f)
            return [h for h in history if h['username'] == username]
        except:
            return []

history_manager = HistoryManager()