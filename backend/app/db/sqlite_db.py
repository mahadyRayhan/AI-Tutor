# backend/app/db/sqlite_db.py

import sqlite3
import json
import os
from datetime import datetime

# Define path relative to backend root
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
DB_PATH = os.path.join(BASE_DIR, "database", "ai_tutor.db")

class SQLiteDB:
    def __init__(self):
        # Ensure database directory exists
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        
        # Connect
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row # Access columns by name
        self._init_tables()

    def _init_tables(self):
        with self.conn:
            # 1. Users Table
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    password_hash TEXT,
                    role TEXT,
                    name TEXT,
                    email TEXT,
                    university TEXT,
                    is_blocked BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP,
                    learning_profile JSON -- <--- NEW: Stores { "frustration_threshold": "low", "preferred_style": "visual" }
                )
            """)
            
            # 2. Sessions Table (Chat Rooms)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    username TEXT,
                    title TEXT,
                    state TEXT, -- JSON string for active_plan/quiz
                    created_at TIMESTAMP,
                    deleted BOOLEAN DEFAULT 0,
                    FOREIGN KEY(username) REFERENCES users(username)
                )
            """)

            # 3. Messages Table (Chat Logs)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    username TEXT, -- Redundant but useful for analytics queries
                    role TEXT, -- 'user' or 'bot'
                    content TEXT,
                    sources TEXT, -- JSON string
                    intent TEXT,
                    topic TEXT,
                    timestamp TIMESTAMP,
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id)
                )
            """)

            # 4. Assignments Table (Challenges)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS assignments (
                    id TEXT PRIMARY KEY,
                    teacher_id TEXT,
                    student_id TEXT,
                    question TEXT,
                    student_answer TEXT,
                    teacher_feedback TEXT,
                    status TEXT, -- PENDING, SUBMITTED, GRADED
                    timestamp TIMESTAMP
                )
            """)
            
            # 5. User Knowledge (XP/Mastery)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS user_knowledge (
                    username TEXT,
                    concept TEXT,
                    timestamp TIMESTAMP,
                    PRIMARY KEY (username, concept)
                )
            """)
            
            # 6. User Goals
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS user_goals (
                    username TEXT PRIMARY KEY,
                    goal_text TEXT,
                    updated_at TIMESTAMP
                )
            """)

            # 7. User Feedback
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS user_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT,
                    session_id TEXT,
                    original_query TEXT,
                    feedback_type TEXT,
                    feedback_text TEXT, -- NEW COLUMN
                    timestamp TIMESTAMP,
                    FOREIGN KEY(username) REFERENCES users(username),
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id)
                )
            """)
            # Safe catch to add the column to existing databases without breaking
            import sqlite3
            try:
                self.conn.execute("ALTER TABLE user_feedback ADD COLUMN feedback_text TEXT")
            except sqlite3.OperationalError:
                pass # Column already exists

    def execute(self, query, params=()):
        """Execute a write operation"""
        with self.conn:
            return self.conn.execute(query, params)

    def fetch_one(self, query, params=()):
        """Get a single row"""
        cursor = self.conn.execute(query, params)
        return cursor.fetchone()

    def fetch_all(self, query, params=()):
        """Get all rows"""
        cursor = self.conn.execute(query, params)
        return cursor.fetchall()

# Singleton Instance
db = SQLiteDB()