# backend/app/core/settings_manager.py
import json
import os
from typing import Dict

SETTINGS_FILE = "topic_settings.json"

# Default state: All topics enabled
DEFAULT_TOPICS = [
    "Variables", "Control Flow", "Functions", "Arrays", "Strings", "Pointers", "Structures", "General"
]

class SettingsManager:
    def __init__(self):
        self.file_path = SETTINGS_FILE
        self._ensure_file_exists()

    def _ensure_file_exists(self):
        if not os.path.exists(self.file_path):
            # Create default settings
            settings = {topic: True for topic in DEFAULT_TOPICS}
            self.save_settings(settings)

    def get_settings(self) -> Dict[str, bool]:
        try:
            with open(self.file_path, 'r') as f:
                return json.load(f)
        except:
            return {topic: True for topic in DEFAULT_TOPICS}

    def save_settings(self, settings: Dict[str, bool]):
        with open(self.file_path, 'w') as f:
            json.dump(settings, f, indent=4)

    def update_topic(self, topic: str, is_enabled: bool):
        settings = self.get_settings()
        settings[topic] = is_enabled
        self.save_settings(settings)

settings_manager = SettingsManager()