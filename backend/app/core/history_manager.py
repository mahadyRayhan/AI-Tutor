# backend/app/core/history_manager.py
import json
import os
import uuid
from datetime import datetime
from typing import List, Dict, Optional

# Paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
ANALYTICS_FILE = os.path.join(BASE_DIR, "database", "chat_history.json")
SESSIONS_FILE = os.path.join(BASE_DIR, "database", "user_sessions.json")

class HistoryManager:
    def __init__(self):
        self._ensure_files_exist()

    def _ensure_files_exist(self):
        for path in [ANALYTICS_FILE, SESSIONS_FILE]:
            if not os.path.exists(path):
                with open(path, 'w') as f:
                    json.dump({}, f) if path == SESSIONS_FILE else json.dump([], f)

    # --- ANALYTICS LOGGING (For Teacher Dashboard) ---
    def log_interaction(self, username: str, query: str, intent: str, response: str, topic: str, metadata: dict = None):
        """Logs flat interaction for teacher analytics."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "username": username,
            "query": query,
            "intent": intent,
            "topic": topic,
            "metadata": metadata or {},
            "response_snippet": response[:200]
        }
        try:
            with open(ANALYTICS_FILE, 'r') as f:
                history = json.load(f)
            history.append(entry)
            with open(ANALYTICS_FILE, 'w') as f:
                json.dump(history, f, indent=2)
        except Exception as e:
            print(f"Analytics Log Error: {e}")

    # --- SESSION MANAGEMENT (For Student UI) ---
    def _load_sessions(self) -> Dict:
        try:
            with open(SESSIONS_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}

    def _save_sessions(self, data: Dict):
        with open(SESSIONS_FILE, 'w') as f:
            json.dump(data, f, indent=2)

    def create_session(self, username: str, title: str = "New Chat") -> str:
        data = self._load_sessions()
        session_id = str(uuid.uuid4())
        
        if username not in data:
            data[username] = {}
            
        data[username][session_id] = {
            "id": session_id,
            "title": title,
            "timestamp": datetime.now().isoformat(),
            "deleted": False,  # <--- NEW FLAG
            "messages": []
        }
        self._save_sessions(data)
        return session_id

    def add_message(self, username: str, session_id: str, role: str, content: str, sources: list = None):
        data = self._load_sessions()
        
        # Create session if it doesn't exist (failsafe)
        if username not in data: data[username] = {}
        if session_id not in data[username]:
            self.create_session(username) # This creates a fresh ID, handle mapping in caller or force creation
            # If session_id was passed but not found, we effectively start a new one with that ID
            data[username][session_id] = {
                "id": session_id,
                "title": content[:30] + "...", # Generate title from first msg
                "timestamp": datetime.now().isoformat(),
                "messages": []
            }

        # Update Title if it's the first user message and title is generic
        if role == "user" and len(data[username][session_id]["messages"]) == 0:
             data[username][session_id]["title"] = content[:40] + ("..." if len(content) > 40 else "")

        msg_entry = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        }
        if sources:
            msg_entry["sources"] = sources

        data[username][session_id]["messages"].append(msg_entry)
        data[username][session_id]["timestamp"] = datetime.now().isoformat() # Update last modified
        
        self._save_sessions(data)

    def get_user_sessions_list(self, username: str) -> List[Dict]:
        """Returns ACTIVE sessions only."""
        data = self._load_sessions()
        user_data = data.get(username, {})
        
        sessions = []
        for s in user_data.values():
            # Filter out deleted sessions
            if not s.get("deleted", False): 
                sessions.append(s)
        
        sessions.sort(key=lambda x: x['timestamp'], reverse=True)
        return [{"id": s["id"], "title": s["title"], "date": s["timestamp"]} for s in sessions]

    def get_session_details(self, username: str, session_id: str) -> Dict:
        """Returns full message history for a session."""
        data = self._load_sessions()
        return data.get(username, {}).get(session_id, None)
    
    def delete_session(self, username: str, session_id: str) -> bool:
        """Soft deletes a session."""
        data = self._load_sessions()
        if username in data and session_id in data[username]:
            data[username][session_id]["deleted"] = True
            self._save_sessions(data)
            return True
        return False
        
    def get_student_history(self, username: str) -> List[Dict]:
        """Existing method for analytics compatibility."""
        try:
            with open(ANALYTICS_FILE, 'r') as f:
                history = json.load(f)
            return [h for h in history if h['username'] == username]
        except:
            return []

history_manager = HistoryManager()