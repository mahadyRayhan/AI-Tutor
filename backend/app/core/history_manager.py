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
                # Safety fallback: If AI sends None, use defaults
                safe_intent = intent or "UNKNOWN"
                safe_topic = topic or "General"
                
                db.conn.execute("""
                    UPDATE messages 
                    SET intent = ?, topic = ? 
                    WHERE id = ?
                """, (safe_intent, safe_topic, message_id))
                db.conn.commit()
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
        # 1. Ensure Session Exists
        sess = db.fetch_one("SELECT 1 FROM sessions WHERE session_id = ?", (session_id,))
        if not sess:
            self.create_session(username) 

        # 2. Update Title based on first USER message (ignore bot greeting)
        if role == "user":
            user_count = db.fetch_one(
                "SELECT count(*) as c FROM messages WHERE session_id = ? AND role = 'user'",
                (session_id,)
            )
            if user_count['c'] == 0:
                new_title = content[:40] + "..."
                db.execute("UPDATE sessions SET title = ? WHERE session_id = ?", (new_title, session_id))

        # 3. Insert Message with DEFAULTS
        sources_json = json.dumps(sources) if sources else None
        
        # --- ROOT CAUSE FIX IS HERE ---
        # We explicitly insert 'General' for topic and 'PROCESSING' for intent.
        # This prevents NULLs even if the analytics step crashes later.
        default_topic = "General"
        default_intent = "PROCESSING" if role == 'user' else "RESPONSE"

        cursor = db.conn.execute("""
            INSERT INTO messages (session_id, username, role, content, sources, timestamp, intent, topic)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            session_id, 
            username, 
            role, 
            content, 
            sources_json, 
            datetime.now(), 
            default_intent, # <--- No more NULL
            default_topic   # <--- No more NULL
        ))
        db.conn.commit()
        
        return cursor.lastrowid

    def get_user_sessions_list(self, username: str) -> List[Dict]:
        rows = db.fetch_all("""
            SELECT s.session_id, s.title, s.created_at 
            FROM sessions s
            WHERE s.username = ? AND s.deleted = 0
            AND EXISTS (
                SELECT 1 FROM messages m 
                WHERE m.session_id = s.session_id 
                AND m.role = 'user' 
                AND m.content != '[INIT_SESSION]'
            )
            ORDER BY s.created_at DESC
        """, (username,))
        
        return [{"id": r['session_id'], "title": r['title'], "date": r['created_at']} for r in rows]

    def log_feedback(self, username: str, session_id: str, original_query: str, feedback_type: str, feedback_text: str = None):
        try:
            db.conn.execute("""
                INSERT INTO user_feedback (username, session_id, original_query, feedback_type, feedback_text, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (username, session_id, original_query, feedback_type, feedback_text, datetime.now()))
            db.conn.commit()
        except Exception as e:
            print(f"Failed to log feedback to SQL: {e}")
    
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

    def calculate_active_time(self, username: str, days: int) -> str:
        """
        Calculates total active time in minutes for the last N days.
        Heuristic: Sum of time deltas between messages where gap < 20 mins.
        """
        from datetime import timedelta
        
        # 1. Calculate cutoff date
        cutoff_date = datetime.now() - timedelta(days=days)
        
        # 2. Fetch timestamps only (Fast query)
        rows = db.fetch_all("""
            SELECT timestamp FROM messages 
            WHERE username = ? AND timestamp > ?
            ORDER BY timestamp ASC
        """, (username, cutoff_date))
        
        if not rows or len(rows) < 2:
            return "0m"

        total_seconds = 0
        SESSION_THRESHOLD = 20 * 60 # 20 minutes in seconds

        timestamps = []
        for r in rows:
            try:
                # Handle ISO format string from DB
                if isinstance(r['timestamp'], str):
                    timestamps.append(datetime.fromisoformat(r['timestamp']))
                else:
                    timestamps.append(r['timestamp'])
            except: continue

        # 3. Sum Deltas
        for i in range(1, len(timestamps)):
            delta = (timestamps[i] - timestamps[i-1]).total_seconds()
            
            # Only count if the gap is reasonable (active session)
            # If they reply 5 hours later, don't count those 5 hours.
            if delta < SESSION_THRESHOLD:
                total_seconds += delta
            else:
                # Add a minimal baseline for the new session start (e.g. 1 min reading time)
                total_seconds += 60 

        # 4. Format Output
        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) // 60)
        
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"

history_manager = HistoryManager()