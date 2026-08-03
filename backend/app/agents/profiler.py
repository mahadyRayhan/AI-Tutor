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

        # Tier 3: confusion / boredom academic-emotion heuristics
        self._confusion_patterns = [re.compile(p, re.IGNORECASE) for p in (
            r"\bconfus", r"\bi (?:don'?t|do not) (?:get|understand)\b", r"\bnot sure\b",
            r"\bwhat do you mean\b", r"\bhow does .* work\b", r"\blost\b", r"\bunclear\b",
            r"\bmakes no sense\b", r"\bhuh\b", r"\bwhy (?:is|does|doesn'?t)\b",
        )]
        self._boredom_patterns = [re.compile(p, re.IGNORECASE) for p in (
            r"\bbor(?:ed|ing)\b", r"\btoo easy\b", r"\bi (?:already )?know (?:this|that)\b",
            r"\bskip\b", r"\bnext\b", r"\bmove on\b", r"\bcan we (?:hurry|speed)\b",
            r"\bthis is (?:easy|basic)\b",
        )]

    def _detect_academic_emotion(self, text: str) -> str | None:
        """Cheap keyword detection for confusion / boredom (Tier 3)."""
        for p in self._confusion_patterns:
            if p.search(text):
                return "confusion"
        for p in self._boredom_patterns:
            if p.search(text):
                return "boredom"
        return None

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
        Updates the user's frustration level and delta (ΔF) dynamically in SQLite.
        """
        try:
            fast_signal = self._fast_evaluate(last_message)
            
            if fast_signal.is_keep_going:
                return "continue"
                
            frustration_level = "normal"
            detected_emotion = "neutral"
            f_score = 0.2 # Base baseline for normal

            # Tier 3: academic emotion (confusion / boredom / frustration / flow)
            academic_emotion = self._detect_academic_emotion(last_message) or "neutral"

            if fast_signal.is_rage:
                self.logger.warning(f"⚠️ [Tier 1] RAGE Detected for {user_id}: {fast_signal.negative_hits}")
                frustration_level = "rage"
                detected_emotion = "anger"
                academic_emotion = "frustration"
                f_score = 1.0 # Max frustration
            else:
                result = self.emotion_classifier(last_message)[0][0]
                detected_emotion = result['label']
                score = result['score']

                if detected_emotion in ['anger', 'disgust', 'sadness', 'fear'] and score > 0.6:
                    self.logger.warning(f"⚠️ [Tier 2] Academic Frustration Detected for {user_id}: {detected_emotion} ({score:.2f})")
                    frustration_level = "high"
                    academic_emotion = "frustration"
                    f_score = 0.7 # High frustration
                elif detected_emotion == 'joy' and score > 0.7:
                    frustration_level = "delighted"
                    academic_emotion = "flow"
                    f_score = 0.0 # Zero frustration (flow state)
                elif detected_emotion == 'surprise' and score > 0.6 and academic_emotion == "neutral":
                    academic_emotion = "confusion"

            # ---------------------------------------------------------
            # UPDATE SQLITE STATE & CALCULATE ΔF
            # ---------------------------------------------------------
            row = db.fetch_one("SELECT learning_profile FROM users WHERE username = ?", (user_id,))
            profile = json.loads(row['learning_profile']) if row and row['learning_profile'] else {}

            # Retrieve or initialize the rolling history of frustration scores
            f_history = profile.get("frustration_history", [0.2])
            
            # Calculate ΔF = F_t - F_{t-1}
            f_t_minus_1 = f_history[-1] if f_history else 0.2
            delta_f = f_score - f_t_minus_1

            # Update rolling history (keep last 3 states)
            f_history.append(f_score)
            if len(f_history) > 3:
                f_history.pop(0)

            # Update the profile JSON
            profile["frustration_level"] = frustration_level
            profile["frustration_history"] = f_history
            profile["delta_f"] = delta_f
            profile["academic_emotion"] = academic_emotion

            # Tier 3: running tally of academic emotions (for the affective learner dimension)
            tally = profile.get("emotion_counts", {})
            tally[academic_emotion] = tally.get(academic_emotion, 0) + 1
            profile["emotion_counts"] = tally

            # Initialize Strike Counter if it doesn't exist (For Sentinel)
            if "off_topic_strikes" not in profile:
                profile["off_topic_strikes"] = 0

            db.execute("UPDATE users SET learning_profile = ? WHERE username = ?", (json.dumps(profile), user_id))

            # Expose the academic emotion so the caller can log it into affect_log
            self._last_academic_emotion = academic_emotion

            print(f"\n🧠 [PROFILER] Emotion: {detected_emotion.upper()} | Level: {frustration_level.upper()} | F_t: {f_score} | ΔF: {delta_f:.2f}\n")
            return frustration_level
            
        except Exception as e:
            self.logger.error(f"Sentiment Analysis Failed: {e}")
            return "normal"