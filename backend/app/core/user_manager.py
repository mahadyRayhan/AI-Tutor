# backend/app/core/user_manager.py

from datetime import datetime
from typing import Optional, Dict, List
from passlib.context import CryptContext
from app.db.sqlite_db import db # Import our new DB logic

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

class UserManager:
    def __init__(self):
        # Create default admin if not exists
        self._ensure_admin_exists()

    def _ensure_admin_exists(self):
        try:
            hashed = pwd_context.hash("admin123")
            # INSERT OR IGNORE means "do nothing if teacher1 exists"
            db.execute("""
                INSERT OR IGNORE INTO users (username, password_hash, role, name, email, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, ("teacher1", hashed, "teacher", "Default Teacher", "teacher@edu.com", datetime.now()))
        except Exception as e:
            print(f"Error creating default admin: {e}")

    def authenticate(self, username, password) -> Optional[Dict]:
        row = db.fetch_one("SELECT * FROM users WHERE username = ?", (username,))
        
        if not row: return None
        if row['is_blocked']: raise Exception("Account Blocked")

        if pwd_context.verify(password, row['password_hash']):
            return {
                "username": row['username'],
                "role": row['role'],
                "name": row['name']
            }
        return None

    def create_user(self, username, password, role="student", **kwargs) -> bool:
        # Check if exists
        exists = db.fetch_one("SELECT 1 FROM users WHERE username = ?", (username,))
        if exists: return False
        
        hashed_pw = pwd_context.hash(password)
        
        try:
            db.execute("""
                INSERT INTO users (username, password_hash, role, name, email, university, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (username, hashed_pw, role, kwargs.get("name"), kwargs.get("email"), kwargs.get("university"), datetime.now()))
            return True
        except Exception as e:
            print(f"Signup error: {e}")
            return False

    def get_all_users(self) -> List[Dict]:
        rows = db.fetch_all("SELECT username, role, name, email, university, is_blocked FROM users")
        return [dict(row) for row in rows]

    def update_user_status(self, username: str, role: str = None, blocked: bool = None):
        if role:
            db.execute("UPDATE users SET role = ? WHERE username = ?", (role, username))
        if blocked is not None:
            db.execute("UPDATE users SET is_blocked = ? WHERE username = ?", (blocked, username))
        return True

user_manager = UserManager()