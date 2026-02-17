# # backend/app/core/history_manager.py
# import json
# import os
# import uuid
# from datetime import datetime
# from typing import List, Dict, Optional

# # Paths
# BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
# ANALYTICS_FILE = os.path.join(BASE_DIR, "database", "chat_history.json")
# SESSIONS_FILE = os.path.join(BASE_DIR, "database", "user_sessions.json")

# class HistoryManager:
#     def __init__(self):
#         self._ensure_files_exist()

#     def _ensure_files_exist(self):
#         for path in [ANALYTICS_FILE, SESSIONS_FILE]:
#             if not os.path.exists(path):
#                 with open(path, 'w') as f:
#                     json.dump({}, f) if path == SESSIONS_FILE else json.dump([], f)

#     # --- ANALYTICS LOGGING (For Teacher Dashboard) ---
#     def log_interaction(self, username: str, query: str, intent: str, response: str, topic: str, metadata: dict = None):
#         """Logs flat interaction for teacher analytics."""
#         entry = {
#             "timestamp": datetime.now().isoformat(),
#             "username": username,
#             "query": query,
#             "intent": intent,
#             "topic": topic,
#             "metadata": metadata or {},
#             "response_snippet": response[:200]
#         }
#         try:
#             with open(ANALYTICS_FILE, 'r') as f:
#                 history = json.load(f)
#             history.append(entry)
#             with open(ANALYTICS_FILE, 'w') as f:
#                 json.dump(history, f, indent=2)
#         except Exception as e:
#             print(f"Analytics Log Error: {e}")

#     # --- SESSION MANAGEMENT (For Student UI) ---
#     def _load_sessions(self) -> Dict:
#         try:
#             with open(SESSIONS_FILE, 'r') as f:
#                 return json.load(f)
#         except:
#             return {}

#     def _save_sessions(self, data: Dict):
#         with open(SESSIONS_FILE, 'w') as f:
#             json.dump(data, f, indent=2)

#     def create_session(self, username: str, title: str = "New Chat") -> str:
#         data = self._load_sessions()
#         session_id = str(uuid.uuid4())
        
#         if username not in data:
#             data[username] = {}
            
#         data[username][session_id] = {
#             "id": session_id,
#             "title": title,
#             "timestamp": datetime.now().isoformat(),
#             "deleted": False,  # <--- NEW FLAG
#             "messages": []
#         }
#         self._save_sessions(data)
#         return session_id

#     def add_message(self, username: str, session_id: str, role: str, content: str, sources: list = None):
#         data = self._load_sessions()
        
#         # Create session if it doesn't exist (failsafe)
#         if username not in data: data[username] = {}
#         if session_id not in data[username]:
#             self.create_session(username) # This creates a fresh ID, handle mapping in caller or force creation
#             # If session_id was passed but not found, we effectively start a new one with that ID
#             data[username][session_id] = {
#                 "id": session_id,
#                 "title": content[:30] + "...", # Generate title from first msg
#                 "timestamp": datetime.now().isoformat(),
#                 "messages": []
#             }

#         # Update Title if it's the first user message and title is generic
#         if role == "user" and len(data[username][session_id]["messages"]) == 0:
#              data[username][session_id]["title"] = content[:40] + ("..." if len(content) > 40 else "")

#         msg_entry = {
#             "role": role,
#             "content": content,
#             "timestamp": datetime.now().isoformat()
#         }
#         if sources:
#             msg_entry["sources"] = sources

#         data[username][session_id]["messages"].append(msg_entry)
#         data[username][session_id]["timestamp"] = datetime.now().isoformat() # Update last modified
        
#         self._save_sessions(data)

#     def get_user_sessions_list(self, username: str) -> List[Dict]:
#         """Returns ACTIVE sessions only."""
#         data = self._load_sessions()
#         user_data = data.get(username, {})
        
#         sessions = []
#         for s in user_data.values():
#             # Filter out deleted sessions
#             if not s.get("deleted", False): 
#                 sessions.append(s)
        
#         sessions.sort(key=lambda x: x['timestamp'], reverse=True)
#         return [{"id": s["id"], "title": s["title"], "date": s["timestamp"]} for s in sessions]

#     def update_session_state(self, username: str, session_id: str, state_data: Dict):
#         """Saves temporary state (like active quiz) to the session."""
#         data = self._load_sessions()
#         if username in data and session_id in data[username]:
#             # Merge new state with existing
#             current_state = data[username][session_id].get("state", {})
#             current_state.update(state_data)
#             data[username][session_id]["state"] = current_state
#             self._save_sessions(data)

#     def get_session_state(self, username: str, session_id: str) -> Dict:
#         data = self._load_sessions()
#         return data.get(username, {}).get(session_id, {}).get("state", {})
    
#     def get_session_details(self, username: str, session_id: str) -> Dict:
#         """Returns full message history for a session."""
#         data = self._load_sessions()
#         return data.get(username, {}).get(session_id, None)
    
#     def delete_session(self, username: str, session_id: str) -> bool:
#         """Soft deletes a session."""
#         data = self._load_sessions()
#         if username in data and session_id in data[username]:
#             data[username][session_id]["deleted"] = True
#             self._save_sessions(data)
#             return True
#         return False
        
#     def get_student_history(self, username: str) -> List[Dict]:
#         """Existing method for analytics compatibility."""
#         try:
#             with open(ANALYTICS_FILE, 'r') as f:
#                 history = json.load(f)
#             return [h for h in history if h['username'] == username]
#         except:
#             return []

# history_manager = HistoryManager()

# backend/app/core/history_manager.py

import json
import uuid
from datetime import datetime
from typing import List, Dict
from app.db.sqlite_db import db

class HistoryManager:
    # --- ANALYTICS LOGGING ---
    def log_interaction(self, username: str, query: str, intent: str, response: str, topic: str, metadata: dict = None):
        """
        Updates the most recent message with metadata for analytics.
        In the SQL version, we assume the 'bot' message was just added, 
        and we update it with the classified Intent and Topic.
        """
        # Find the latest bot message for this user to tag it
        # (This is a heuristic, but sufficient for this architecture)
        last_msg = db.fetch_one("""
            SELECT id FROM messages 
            WHERE username = ? AND role = 'bot' 
            ORDER BY id DESC LIMIT 1
        """, (username,))
        
        if last_msg:
            db.execute("""
                UPDATE messages 
                SET intent = ?, topic = ? 
                WHERE id = ?
            """, (intent, topic, last_msg['id']))

    # --- SESSION MANAGEMENT ---
    def create_session(self, username: str, title: str = "New Chat") -> str:
        session_id = str(uuid.uuid4())
        db.execute("""
            INSERT INTO sessions (session_id, username, title, state, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (session_id, username, title, "{}", datetime.now()))
        return session_id

    def add_message(self, username: str, session_id: str, role: str, content: str, sources: list = None):
        # 1. Ensure Session Exists (If manually passed ID)
        sess = db.fetch_one("SELECT 1 FROM sessions WHERE session_id = ?", (session_id,))
        if not sess:
            self.create_session(username) # Logic creates entry with that ID if we forced it

        # 2. Update Title if it's the first user message
        if role == "user":
            count = db.fetch_one("SELECT count(*) as c FROM messages WHERE session_id = ?", (session_id,))
            if count['c'] == 0:
                new_title = content[:40] + "..."
                db.execute("UPDATE sessions SET title = ? WHERE session_id = ?", (new_title, session_id))

        # 3. Insert Message
        sources_json = json.dumps(sources) if sources else None
        db.execute("""
            INSERT INTO messages (session_id, username, role, content, sources, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (session_id, username, role, content, sources_json, datetime.now()))

    def get_user_sessions_list(self, username: str) -> List[Dict]:
        rows = db.fetch_all("""
            SELECT session_id, title, created_at 
            FROM sessions 
            WHERE username = ? AND deleted = 0 
            ORDER BY created_at DESC
        """, (username,))
        
        return [{"id": r['session_id'], "title": r['title'], "date": r['created_at']} for r in rows]

    def get_session_details(self, username: str, session_id: str) -> Dict:
        # 1. Verify ownership
        sess = db.fetch_one("SELECT * FROM sessions WHERE session_id = ? AND username = ?", (session_id, username))
        if not sess: return None
        
        # 2. Get Messages
        msgs = db.fetch_all("SELECT role, content, sources, timestamp FROM messages WHERE session_id = ? ORDER BY id ASC", (session_id,))
        
        formatted_msgs = []
        for m in msgs:
            formatted_msgs.append({
                "role": m['role'],
                "content": m['content'],
                "sources": json.loads(m['sources']) if m['sources'] else [],
                "timestamp": m['timestamp']
            })
            
        return {
            "id": session_id,
            "title": sess['title'],
            "state": json.loads(sess['state']) if sess['state'] else {},
            "messages": formatted_msgs
        }

    def update_session_state(self, username: str, session_id: str, state_update: Dict):
        # Get current state
        row = db.fetch_one("SELECT state FROM sessions WHERE session_id = ?", (session_id,))
        if row:
            current_state = json.loads(row['state']) if row['state'] else {}
            current_state.update(state_update)
            db.execute("UPDATE sessions SET state = ? WHERE session_id = ?", (json.dumps(current_state), session_id))

    def get_session_state(self, username: str, session_id: str) -> Dict:
        row = db.fetch_one("SELECT state FROM sessions WHERE session_id = ?", (session_id,))
        if row and row['state']:
            return json.loads(row['state'])
        return {}

    def delete_session(self, username: str, session_id: str) -> bool:
        db.execute("UPDATE sessions SET deleted = 1 WHERE session_id = ? AND username = ?", (session_id, username))
        return True
        
    def get_student_history(self, username: str) -> List[Dict]:
        # Needed for Analytics Dashboard
        rows = db.fetch_all("""
            SELECT content as query, intent, topic, timestamp 
            FROM messages 
            WHERE username = ? AND role = 'user'
            ORDER BY timestamp ASC
        """, (username,))
        return [dict(r) for r in rows]

history_manager = HistoryManager()