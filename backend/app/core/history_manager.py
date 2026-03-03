# backend/app/core/history_manager.py

import json
import uuid
from datetime import datetime
from typing import List, Dict
from app.db.sqlite_db import db

class HistoryManager:
    # --- ANALYTICS LOGGING ---
    def log_interaction(self, message_id: int, intent: str, topic: str):
        if message_id:
            try:
                db.conn.execute("""
                    UPDATE messages 
                    SET intent = ?, topic = ? 
                    WHERE id = ?
                """, (intent, topic, message_id))
                db.conn.commit() # <--- CRITICAL FIX: Save the update
            except Exception as e:
                print(f"Analytics Log Error: {e}")

    # --- SESSION MANAGEMENT ---
    def create_session(self, username: str, title: str = "New Chat") -> str:
        session_id = str(uuid.uuid4())
        db.execute("""
            INSERT INTO sessions (session_id, username, title, state, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (session_id, username, title, "{}", datetime.now()))
        return session_id

    def add_message(self, username: str, session_id: str, role: str, content: str, sources: list = None) -> int:
        """
        Adds a message and RETURNS the database row ID.
        """
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

        # 3. Insert Message and Return ID
        sources_json = json.dumps(sources) if sources else None
        
        # We use the connection directly here to ensure we get the cursor for lastrowid
        cursor = db.conn.execute("""
            INSERT INTO messages (session_id, username, role, content, sources, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (session_id, username, role, content, sources_json, datetime.now()))
        db.conn.commit()
        
        return cursor.lastrowid

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
        """
        Used by the Analytics Dashboard.
        We fetch the USER messages because we are now tagging the user's input 
        with the intent (e.g. "User asked a PROBLEM").
        """
        rows = db.fetch_all("""
            SELECT 
                content AS query, 
                intent, 
                topic, 
                timestamp 
            FROM messages 
            WHERE username = ? AND role = 'user'
            ORDER BY timestamp ASC
        """, (username,))
        return [dict(r) for r in rows]

history_manager = HistoryManager()