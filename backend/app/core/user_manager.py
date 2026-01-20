# backend/app/core/user_manager.py
import json
import os
from typing import Optional, Dict, List

# Using relative path logic
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
DB_PATH = os.path.join(BASE_DIR, "database", "users.json")

class UserManager:
    def __init__(self):
        self.db_path = DB_PATH
        self._ensure_db_exists()

    def _ensure_db_exists(self):
        if not os.path.exists(self.db_path):
            # Create default admin if file doesn't exist
            default_data = {
                "teacher1": {
                    "password": "admin123",
                    "role": "teacher",
                    "blocked": False,
                    "name": "Default Teacher",
                    "email": "teacher@university.edu"
                }
            }
            self._save_db(default_data)

    def _load_db(self) -> Dict:
        try:
            with open(self.db_path, 'r') as f:
                return json.load(f)
        except:
            return {}

    def _save_db(self, data: Dict):
        with open(self.db_path, 'w') as f:
            json.dump(data, f, indent=4)

    def authenticate(self, username, password) -> Optional[Dict]:
        """Returns user info if credentials match and NOT blocked."""
        users = self._load_db()
        user = users.get(username)
        
        if not user:
            return None
            
        if user.get('password') == password:
            if user.get('blocked', False):
                raise Exception("Account Blocked")
            
            return {
                "username": username,
                "role": user.get('role', 'student'),
                "name": user.get('name', username)
            }
        return None

    def create_user(self, username, password, role="student", **kwargs) -> bool:
        """Creates a new user. Returns False if username exists."""
        users = self._load_db()
        if username in users:
            return False
        
        users[username] = {
            "password": password,
            "role": role,
            "blocked": False,
            "name": kwargs.get("name", ""),
            "email": kwargs.get("email", ""),
            "university": kwargs.get("university", ""),
            "department": kwargs.get("department", ""),
            "interest": kwargs.get("interest", "")
        }
        self._save_db(users)
        return True

    def get_all_users(self) -> List[Dict]:
        """Returns list of all users for Admin Panel."""
        users = self._load_db()
        result = []
        for uname, data in users.items():
            result.append({
                "username": uname,
                "role": data.get("role", "student"),
                "blocked": data.get("blocked", False),
                "name": data.get("name", ""),
                "email": data.get("email", ""),
                "university": data.get("university", "")
            })
        return result

    def update_user_status(self, username: str, role: str = None, blocked: bool = None):
        """Admin function to change role or block status."""
        users = self._load_db()
        if username in users:
            if role:
                users[username]['role'] = role
            if blocked is not None:
                users[username]['blocked'] = blocked
            self._save_db(users)
            return True
        return False

user_manager = UserManager()