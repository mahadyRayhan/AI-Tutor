# backend/app/core/user_manager.py
import csv
import os
from typing import Optional, Dict

# Path to your CSV
# Using relative path logic to find the database folder
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
CSV_PATH = os.path.join(BASE_DIR, "database", "users.csv")

class UserManager:
    def __init__(self):
        self.db_path = CSV_PATH

    def _read_users(self) -> Dict[str, Dict]:
        """
        Reads CSV and returns a dictionary of users.
        FUTURE UPGRADE: Replace this function with MongoDB calls.
        """
        users = {}
        if not os.path.exists(self.db_path):
            return {}

        with open(self.db_path, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Store by username for fast lookup
                users[row['username']] = {
                    "password": row['password'], # In production, verify hashes here!
                    "role": row['role']
                }
        return users

    def authenticate(self, username, password) -> Optional[Dict[str, str]]:
        """
        Returns user info if credentials match, else None.
        """
        users = self._read_users()
        user = users.get(username)
        
        if user and user['password'] == password:
            return {
                "username": username,
                "role": user['role']
            }
        return None

user_manager = UserManager()