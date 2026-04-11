# backend/app/agents/profiler.py

import json
import re
import asyncio
from typing import AsyncGenerator, Dict, List, Pattern
from dataclasses import dataclass, asdict
from transformers import pipeline

from app.agents.base import BaseAgent
from app.agents.schema import AgentState
from app.db.sqlite_db import db

@dataclass(frozen=True)
class PromptSignal:
    text: str
    is_rage: bool         # Explicit anger/profanity
    is_keep_going: bool   # User wants continuation
    negative_hits: List[str]

class ProfilerAgent(BaseAgent):
    """
    The 'Psychologist' Agent.
    Implements the SAGE Affective SRL Processes (Emotion & Feeling Regulation).
    Uses a Hybrid approach: Fast Heuristics (Claude-style) + Deep Semantics (RoBERTa).
    """
    def __init__(self, llm, logger):
        super().__init__(llm, logger)
        
        self.logger.info("🧠 Loading Emotion Profiling Model (DistilRoBERTa)...")
        self.emotion_classifier = pipeline(
            "text-classification", 
            model="j-hartmann/emotion-english-distilroberta-base", 
            top_k=1
        )

        # --- CLAUDE-STYLE FAST HEURISTICS ---
        negative_phrases = [
            r"wtf", r"wth", r"ffs", r"omfg", r"shitty", r"dumbass", 
            r"horrible", r"awful", r"pissed off", r"piece of shit", 
            r"what the fuck", r"fucking broken", r"fuck you", 
            r"screw this", r"so frustrating", r"this sucks", r"damn it",
            r"i hate this", r"this is stupid", r"garbage"
        ]

        self._negative_patterns: Dict[str, Pattern[str]] = {
            phrase: re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE)
            for phrase in negative_phrases
        }
        
        self._continue_exact = re.compile(r"^\s*continue\s*$", re.IGNORECASE)
        self._keep_going_patterns: Dict[str, Pattern[str]] = {
            "keep going": re.compile(r"\bkeep going\b", re.IGNORECASE),
            "go on": re.compile(r"\bgo on\b", re.IGNORECASE),
        }

    async def process(self, state: AgentState) -> AsyncGenerator[dict, None]:
        """Satisfies BaseAgent interface."""
        yield {}

    def _fast_evaluate(self, text: str) -> PromptSignal:
        """Tier 1: Claude-style Regex Evaluation"""
        negative_hits = []
        is_keep_going = False

        for label, pattern in self._negative_patterns.items():
            if pattern.search(text):
                negative_hits.append(label)

        if self._continue_exact.search(text):
            is_keep_going = True
        for label, pattern in self._keep_going_patterns.items():
            if pattern.search(text):
                is_keep_going = True

        return PromptSignal(
            text=text,
            is_rage=bool(negative_hits),
            is_keep_going=is_keep_going,
            negative_hits=negative_hits
        )

    async def analyze_sentiment(self, user_id: str, last_message: str):
        """
        Real-time Emotion Check mapping to SAGE Academic Emotions.
        Updates the user's frustration level dynamically in SQLite.
        """
        try:
            # ---------------------------------------------------------
            # TIER 1: Fast Heuristic Check (Claude Leak approach)
            # ---------------------------------------------------------
            fast_signal = self._fast_evaluate(last_message)
            
            if fast_signal.is_keep_going:
                return "continue"
                
            frustration_level = "normal"
            detected_emotion = "neutral"

            if fast_signal.is_rage:
                self.logger.warning(f"⚠️ [Tier 1] RAGE Detected for {user_id}: {fast_signal.negative_hits}")
                frustration_level = "rage"
                detected_emotion = "anger"
            else:
                # ---------------------------------------------------------
                # TIER 2: Deep Semantic Check (For polite/academic frustration)
                # ---------------------------------------------------------
                result = self.emotion_classifier(last_message)[0][0]
                detected_emotion = result['label'] # 'anger', 'joy', 'neutral', 'sadness', 'fear'
                score = result['score']

                # Map ML output to SAGE Core Academic Emotions
                if detected_emotion in ['anger', 'disgust', 'sadness', 'fear'] and score > 0.6:
                    self.logger.warning(f"⚠️ [Tier 2] Academic Frustration Detected for {user_id}: {detected_emotion} ({score:.2f})")
                    frustration_level = "high"
                elif detected_emotion == 'joy' and score > 0.7:
                    frustration_level = "delighted" # Maps to SAGE "Delight/Flow state"

            # ---------------------------------------------------------
            # UPDATE SQLITE STATE
            # ---------------------------------------------------------
            row = db.fetch_one("SELECT learning_profile FROM users WHERE username = ?", (user_id,))
            profile = json.loads(row['learning_profile']) if row and row['learning_profile'] else {}

            # Only write to DB if the state actually changed (saves DB calls)
            if profile.get("frustration_level") != frustration_level:
                profile["frustration_level"] = frustration_level
                db.execute("UPDATE users SET learning_profile = ? WHERE username = ?", (json.dumps(profile), user_id))

            print(f"\n🧠 [PROFILER] User: {user_id} | Emotion: {detected_emotion.upper()} | Frustration Level: {frustration_level.upper()}\n")
            return frustration_level
            
        except Exception as e:
            self.logger.error(f"Sentiment Analysis Failed: {e}")
            return "normal"