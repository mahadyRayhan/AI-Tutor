# backend/app/db/sqlite_db.py

import sqlite3
import json
import os
from app.core import config
from datetime import datetime

# Define path relative to backend root
# BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
# DB_PATH = os.path.join(BASE_DIR, "database", "ai_tutor.db")
DB_PATH = os.path.join(config.DB_DIR, "ai_tutor.db")

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
                
            try:
                self.conn.execute("ALTER TABLE messages ADD COLUMN action_taken TEXT")
            except sqlite3.OperationalError:
                pass

            try:
                self.conn.execute("ALTER TABLE messages ADD COLUMN style_used TEXT")
            except sqlite3.OperationalError:
                pass

            # Reparameterization migration: rows seeded under the old zero-prior
            # scheme have p_mastery_micro=0.0 or p_mastery_code=0.0 but no
            # recorded interactions (any actual interaction would push them above 0.09).
            # Update those rows to the new Cromwell-safe priors.
            try:
                self.conn.execute(
                    "UPDATE user_knowledge SET p_mastery_micro=0.05 WHERE p_mastery_micro=0.0"
                )
                self.conn.execute(
                    "UPDATE user_knowledge SET p_mastery_code=0.01 WHERE p_mastery_code=0.0"
                )
            except Exception:
                pass

            # SM-2 spaced repetition + BKT columns on user_knowledge
            for col, definition in [
                ("interval_days",   "INTEGER DEFAULT 1"),
                ("ease_factor",     "REAL DEFAULT 2.5"),
                ("due_date",        "TIMESTAMP"),
                ("review_count",    "INTEGER DEFAULT 0"),
                ("p_mastery",       "REAL DEFAULT 0.3"),
                ("p_mastery_quiz",  "REAL DEFAULT 0.3"),
                ("p_mastery_micro", "REAL DEFAULT 0.05"),
                ("p_mastery_code",  "REAL DEFAULT 0.01"),
                # Hysteresis flag: 1 once all tiers cross THETA_CERTIFY (0.95),
                # 0 if any tier later drops below THETA_DECERTIFY (0.75).
                ("is_certified",    "INTEGER DEFAULT 0"),
                # Per-tier last-interaction timestamps for forgetting-augmented BKT decay.
                # NULL until the first interaction on that tier.
                ("last_quiz_at",    "TIMESTAMP"),
                ("last_micro_at",   "TIMESTAMP"),
                ("last_code_at",    "TIMESTAMP"),
                # Explicit evidence counts — certification requires n_evidence^(k) >= 3.
                # Incremented only on correct answers (Fix #3: n_min explicit gate).
                ("n_evidence_quiz",  "INTEGER DEFAULT 0"),
                ("n_evidence_micro", "INTEGER DEFAULT 0"),
                ("n_evidence_code",  "INTEGER DEFAULT 0"),
                # ever_certified: 1 once all tiers have been jointly certified at any point.
                # Never reset to 0. Prerequisite gate uses this (not current decayed P̃)
                # so forgetting-decay cannot re-lock earned prerequisites (Fix #5).
                ("ever_certified",   "INTEGER DEFAULT 0"),
                # Per-tier adaptive decay rates λ (day^-1).
                # Initialized from DECAY_RATES; each correct review divides by ease_factor
                # to slow forgetting — SM-2/BKT coupling (Fix #6).
                ("decay_quiz_lam",   "REAL DEFAULT 0.04951"),
                ("decay_micro_lam",  "REAL DEFAULT 0.09902"),
                ("decay_code_lam",   "REAL DEFAULT 0.13863"),
            ]:
                try:
                    self.conn.execute(f"ALTER TABLE user_knowledge ADD COLUMN {col} {definition}")
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