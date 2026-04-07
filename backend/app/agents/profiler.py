# backend/app/agents/profiler.py

import json
import asyncio
from typing import AsyncGenerator
from transformers import pipeline
from app.agents.base import BaseAgent
from app.agents.schema import AgentState
from app.db.sqlite_db import db

class ProfilerAgent(BaseAgent):
    """
    The 'Psychologist' Agent.
    Analyzes interaction patterns to detect in-the-moment Frustration.
    (Static learning preferences are now handled explicitly by the UI).
    """
    def __init__(self, llm, logger):
        super().__init__(llm, logger)
        
        self.logger.info("🧠 Loading Emotion Profiling Model (DistilRoBERTa)...")
        
        # EMOTION DETECTION (Real-time)
        # Model: DistilRoBERTa (Super Fast, ~50ms)
        self.emotion_classifier = pipeline(
            "text-classification", 
            model="j-hartmann/emotion-english-distilroberta-base", 
            top_k=1
        )

    async def process(self, state: AgentState) -> AsyncGenerator[dict, None]:
        """Satisfies BaseAgent interface."""
        return
        yield {}

    async def analyze_sentiment(self, user_id: str, last_message: str):
        """
        Real-time Emotion Check. Updates the user's frustration level dynamically.
        """
        try:
            # 1. Run extremely fast emotion model
            result = self.emotion_classifier(last_message)[0][0]
            label = result['label'] # e.g., 'anger', 'joy', 'neutral', 'sadness'
            score = result['score']

            # 2. Check for high frustration
            frustration_level = "normal"
            if label in ['anger', 'disgust', 'sadness'] and score > 0.6:
                self.logger.warning(f"⚠️ Frustration Detected for {user_id}: {label} ({score:.2f})")
                frustration_level = "high"

            # 3. Fetch current frustration state only (avoid read-modify-write race with preferences save)
            row = db.fetch_one("SELECT json_extract(learning_profile, '$.frustration_level') as frustration_level FROM users WHERE username = ?", (user_id,))
            current_frustration = row['frustration_level'] if row else None

            # 4. Update ONLY the frustration field atomically using json_set
            if current_frustration != frustration_level:
                db.execute(
                    "UPDATE users SET learning_profile = json_set(COALESCE(learning_profile, '{}'), '$.frustration_level', ?) WHERE username = ?",
                    (frustration_level, user_id)
                )

            return frustration_level
            
        except Exception as e:
            self.logger.error(f"Sentiment Analysis Failed: {e}")
            return "normal"