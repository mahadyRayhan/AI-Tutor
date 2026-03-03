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
    Analyzes interaction patterns to detect Learning Preferences and Frustration
    using SOTA Transformer models.
    """
    def __init__(self, llm, logger):
        super().__init__(llm, logger)
        
        self.logger.info("🧠 Loading SOTA Profiling Models (DistilRoBERTa & DeBERTa)...")
        
        # 1. EMOTION DETECTION (Real-time)
        # Model: DistilRoBERTa (Fast, ~50ms)
        self.emotion_classifier = pipeline(
            "text-classification", 
            model="j-hartmann/emotion-english-distilroberta-base", 
            top_k=1
        )

        # 2. BEHAVIORAL PROFILING (Batch)
        # Model: DeBERTa v3 Small (Accurate, Zero-Shot)
        # Note: 'cross-encoder' implies NLI task which pipeline handles efficiently
        self.behavior_classifier = pipeline(
            "zero-shot-classification",
            model="cross-encoder/nli-deberta-v3-small"
        )
        
        # Define the Traits we want to detect via NLI
        self.learning_traits = [
            "visual learner",       # Prefers diagrams
            "verbal learner",       # Prefers text reading
            "practical coder",      # Wants code examples
            "impatient learner",    # Wants short answers
            "detail oriented"       # Wants long answers
        ]

    async def process(self, state: AgentState) -> AsyncGenerator[dict, None]:
        """
        Satisfies BaseAgent interface. 
        Profiler does not process the main chat stream directly.
        """
        return
        yield {}

    async def analyze_sentiment(self, user_id: str, last_message: str):
        """
        Real-time Emotion Check.
        Runs in ~50ms.
        """
        try:
            # Run model
            result = self.emotion_classifier(last_message)[0][0]
            label = result['label'] # e.g., 'anger', 'joy', 'neutral'
            score = result['score']

            # Log significant frustration
            if label in ['anger', 'disgust', 'sadness'] and score > 0.6:
                self.logger.warning(f"⚠️ Frustration Detected for {user_id}: {label} ({score:.2f})")
                return "FRUSTRATED"
            
            return "NEUTRAL"
        except Exception as e:
            self.logger.error(f"Sentiment Analysis Failed: {e}")
            return "NEUTRAL"

    async def update_learning_profile(self, user_id: str):
        self.logger.info(f"🧠 Running Deep Profiling for {user_id}...")
        
        # 1. Fetch recent history
        rows = db.fetch_all("""
            SELECT content FROM messages 
            WHERE username = ? AND role = 'user' 
            ORDER BY id DESC LIMIT 5
        """, (user_id,)) # Reduced to 5 so it reacts faster to recent mood swings
        
        messages = [r['content'].lower() for r in rows]
        if not messages: return

        # 2. HEURISTIC OVERRIDES (The "Demo Savior")
        # If the user explicitly says these words, don't rely on probability, just do it.
        is_explicitly_short = any(word in msg for msg in messages for word in ["too long", "shorter", "syntax only", "just the code", "brief"])
        is_explicitly_long = any(word in msg for msg in messages for word in ["explain in detail", "elaborate", "deep dive", "theory"])
        is_explicitly_visual = any(word in msg for msg in messages for word in ["picture", "diagram", "draw", "visual"])

        history_text = ". ".join(messages)
        profile = {"preferred_modality": "text", "attention_span": "normal"} # Defaults

        try:
            # 3. Run SOTA Zero-Shot Classification
            result = self.behavior_classifier(
                history_text, 
                candidate_labels=self.learning_traits,
                multi_label=True 
            )
            scores = {label: score for label, score in zip(result['labels'], result['scores'])}
            
            # 4. Blend Heuristics with ML Scores
            # Modality
            if is_explicitly_visual or (scores['visual learner'] > 0.4 and scores['visual learner'] > scores['verbal learner']):
                profile['preferred_modality'] = 'visual'
            elif scores['practical coder'] > 0.5:
                profile['preferred_modality'] = 'code_first'

            # Attention
            if is_explicitly_short or scores['impatient learner'] > 0.4:
                profile['attention_span'] = 'short'
            elif is_explicitly_long or scores['detail oriented'] > 0.4:
                profile['attention_span'] = 'long'

            # 5. Save to DB
            db.execute("UPDATE users SET learning_profile = ? WHERE username = ?", (json.dumps(profile), user_id))
            self.logger.info(f"✅ Profile Updated for {user_id}: {profile}")

        except Exception as e:
            self.logger.error(f"Profiling Failed: {e}")