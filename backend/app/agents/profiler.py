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
        """
        Batch Profiling using Zero-Shot Classification.
        Runs in background (~2s).
        """
        self.logger.info(f"🧠 Running Deep Profiling for {user_id}...")
        
        # 1. Fetch recent history
        rows = db.fetch_all("""
            SELECT content FROM messages 
            WHERE username = ? AND role = 'user' 
            ORDER BY id DESC LIMIT 15
        """, (user_id,))
        
        messages = [r['content'] for r in rows]
        if len(messages) < 3: return # Need data

        # Concatenate into one context string
        history_text = ". ".join(messages)

        # 2. Run SOTA Zero-Shot Classification
        try:
            result = self.behavior_classifier(
                history_text, 
                candidate_labels=self.learning_traits,
                multi_label=True 
            )
            
            # 3. Interpret Probabilities
            profile = {}
            scores = {label: score for label, score in zip(result['labels'], result['scores'])}
            
            # Logic: Modality
            if scores['visual learner'] > 0.4 and scores['visual learner'] > scores['verbal learner']:
                profile['preferred_modality'] = 'visual'
            elif scores['practical coder'] > 0.5:
                profile['preferred_modality'] = 'code_first'
            else:
                profile['preferred_modality'] = 'text'

            # Logic: Attention
            if scores['impatient learner'] > 0.5:
                profile['attention_span'] = 'short'
            elif scores['detail oriented'] > 0.5:
                profile['attention_span'] = 'long'
            else:
                profile['attention_span'] = 'normal'

            # 4. Save to DB
            db.execute("UPDATE users SET learning_profile = ? WHERE username = ?", (json.dumps(profile), user_id))
            self.logger.info(f"✅ Profile Updated for {user_id}: {profile}")

        except Exception as e:
            self.logger.error(f"Profiling Failed: {e}")