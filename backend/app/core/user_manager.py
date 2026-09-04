# # backend/app/core/user_manager.py

# from datetime import datetime
# from typing import Optional, Dict, List
# import logging
from passlib.context import CryptContext
# from app.db.sqlite_db import db # Import our new DB logic

# logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# class UserManager:
#     def __init__(self):
#         # Create default admin if not exists
#         self._ensure_admin_exists()

#     def _ensure_admin_exists(self):
#         try:
#             hashed = pwd_context.hash("admin123")
#             # INSERT OR IGNORE means "do nothing if teacher1 exists"
#             db.execute("""
#                 INSERT OR IGNORE INTO users (username, password_hash, role, name, email, created_at)
#                 VALUES (?, ?, ?, ?, ?, ?)
#             """, ("teacher1", hashed, "teacher", "Default Teacher", "teacher@edu.com", datetime.now()))
#         except Exception as e:
#             print(f"Error creating default admin: {e}")

#     def authenticate(self, username, password) -> Optional[Dict]:
#         row = db.fetch_one("SELECT * FROM users WHERE username = ?", (username,))
        
#         if not row: return None
#         if row['is_blocked']: raise Exception("Account Blocked")

#         if pwd_context.verify(password, row['password_hash']):
#             return {
#                 "username": row['username'],
#                 "role": row['role'],
#                 "name": row['name']
#             }
#         return None

#     def create_user(self, username, password, role="student", **kwargs) -> bool:
#         # Check if exists
#         exists = db.fetch_one("SELECT 1 FROM users WHERE username = ?", (username,))
#         if exists: return False
        
#         hashed_pw = pwd_context.hash(password)
        
#         try:
#             db.execute("""
#                 INSERT INTO users (username, password_hash, role, name, email, university, created_at)
#                 VALUES (?, ?, ?, ?, ?, ?, ?)
#             """, (username, hashed_pw, role, kwargs.get("name"), kwargs.get("email"), kwargs.get("university"), datetime.now()))
#             return True
#         except Exception as e:
#             print(f"Signup error: {e}")
#             return False

#     def get_all_users(self, page: int = 1, page_size: int = 10) -> List[Dict]:
#         """
#         Fetches a specific page of users.
#         """
#         offset = (page - 1) * page_size
        
#         # Use parameterized query for safety, though integers are generally safe
#         rows = db.fetch_all(f"""
#             SELECT username, role, name, email, university, is_blocked 
#             FROM users
#             ORDER BY created_at DESC
#             LIMIT ? OFFSET ?
#         """, (page_size, offset))
        
#         return [dict(row) for row in rows]

#     def get_total_user_count(self) -> int:
#         """
#         Returns total number of users for pagination calculation.
#         """
#         row = db.fetch_one("SELECT count(*) as cnt FROM users")
#         return row['cnt'] if row else 0

#     def update_user_status(self, username: str, role: str = None, blocked: bool = None):
#         if role:
#             db.execute("UPDATE users SET role = ? WHERE username = ?", (role, username))
#         if blocked is not None:
#             db.execute("UPDATE users SET is_blocked = ? WHERE username = ?", (blocked, username))
#         return True

# user_manager = UserManager()

# backend/app/core/user_manager.py

from datetime import datetime
from typing import Optional, Dict, List
import hashlib
from passlib.context import CryptContext
from app.db.sqlite_db import db


# Proper bcrypt context
pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto"
)


def _normalize_password(password: str) -> str:
    """
    Pre-hash password with SHA256 to avoid bcrypt's 72-byte limit.
    This preserves security and prevents runtime crashes.
    """
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


class UserManager:
    def __init__(self):
        self._ensure_admin_exists()

    def _ensure_admin_exists(self):
        """Guarantee the documented default teacher login (teacher1 / admin123) works.

        Creates the row if missing, and *self-heals* a stale hash. Rows created before
        passwords were SHA-256 normalized (see _normalize_password) carry a bcrypt hash
        that no longer matches the current verify path — and the old `INSERT OR IGNORE`
        seed never updated them, so the default login silently broke. We now reset the
        hash (and unblock) whenever the default password fails to verify.
        """
        try:
            default_pw = _normalize_password("admin123")
            row = db.fetch_one(
                "SELECT password_hash, is_blocked FROM users WHERE username = ?",
                ("teacher1",)
            )

            if row is None:
                db.execute("""
                    INSERT INTO users
                    (username, password_hash, role, name, email, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    "teacher1",
                    pwd_context.hash(default_pw),
                    "teacher",
                    "Default Teacher",
                    "teacher@edu.com",
                    datetime.now()
                ))
                return

            # Row exists: repair it if the documented password no longer verifies,
            # or if the account got left in a blocked state.
            try:
                verifies = pwd_context.verify(default_pw, row["password_hash"])
            except Exception:
                verifies = False

            if not verifies or row["is_blocked"]:
                db.execute(
                    "UPDATE users SET password_hash = ?, is_blocked = 0 WHERE username = ?",
                    (pwd_context.hash(default_pw), "teacher1")
                )
                print("[user_manager] Reset default teacher login 'teacher1' to 'admin123'.")
        except Exception as e:
            print(f"Error ensuring default admin: {e}")

    def authenticate(self, username, password) -> Optional[Dict]:
        row = db.fetch_one(
            "SELECT * FROM users WHERE username = ?",
            (username,)
        )

        if not row:
            return None

        if row['is_blocked']:
            raise Exception("Account Blocked")

        if pwd_context.verify(
            _normalize_password(password),
            row['password_hash']
        ):
            return {
                "username": row['username'],
                "role": row['role'],
                "name": row['name']
            }

        return None

    def email_taken(self, email: str) -> bool:
        """True if an account already uses this email (case-insensitive)."""
        if not email:
            return False
        row = db.fetch_one(
            "SELECT 1 FROM users WHERE LOWER(email) = LOWER(?)",
            (email.strip(),)
        )
        return row is not None

    def create_user(self, username, password, role="student", **kwargs) -> bool:
        exists = db.fetch_one(
            "SELECT 1 FROM users WHERE username = ?",
            (username,)
        )

        if exists:
            return False

        hashed_pw = pwd_context.hash(_normalize_password(password))

        try:
            db.execute("""
                INSERT INTO users 
                (username, password_hash, role, name, email, university, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                username,
                hashed_pw,
                role,
                kwargs.get("name"),
                kwargs.get("email"),
                kwargs.get("university"),
                datetime.now()
            ))
            return True

        except Exception as e:
            print(f"Signup error: {e}")
            return False

    def find_by_email(self, email: str) -> Optional[Dict]:
        """Look up an account by email (case-insensitive). None if no match."""
        if not email or not email.strip():
            return None
        row = db.fetch_one(
            "SELECT username, role, name, email, is_blocked FROM users "
            "WHERE LOWER(email) = LOWER(?)", (email.strip(),))
        return dict(row) if row else None

    def set_password(self, username: str, new_password: str) -> bool:
        """Replace a user's password. Uses the same normalization as signup —
        SHA-256 then bcrypt — so the hash is what authenticate() expects."""
        try:
            db.execute("UPDATE users SET password_hash = ? WHERE username = ?",
                       (pwd_context.hash(_normalize_password(new_password)), username))
            return True
        except Exception as e:
            logger.error(f"set_password failed for {username}: {e}")
            return False

    def get_all_users(self, page: int = 1, page_size: int = 10) -> List[Dict]:
        offset = (page - 1) * page_size

        rows = db.fetch_all("""
            SELECT username, role, name, email, university, is_blocked
            FROM users
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """, (page_size, offset))

        return [dict(row) for row in rows]

    def get_total_user_count(self) -> int:
        row = db.fetch_one("SELECT count(*) as cnt FROM users")
        return row['cnt'] if row else 0

    def update_user_status(self, username: str, role: str = None, blocked: bool = None):
        if role:
            db.execute(
                "UPDATE users SET role = ? WHERE username = ?",
                (role, username)
            )

        if blocked is not None:
            db.execute(
                "UPDATE users SET is_blocked = ? WHERE username = ?",
                (blocked, username)
            )

        return True


user_manager = UserManager()