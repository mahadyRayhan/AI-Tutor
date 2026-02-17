# backend/app/agents/profiler.py

import json
import asyncio
from app.agents.base import BaseAgent
from app.db.sqlite_db import db

class ProfilerAgent(BaseAgent):
    """
    The 'Psychologist' Agent.
    Analyzes interaction patterns to detect Learning Preferences and Frustration.
    """
    
    async def analyze_sentiment(self, user_id: str, last_message: str):
        """
        Real-time check: Is the user angry/frustrated right now?
        """
        prompt = f"""
        Analyze the emotional tone of this student message.
        Message: "{last_message}"
        
        Classify into one of: [NEUTRAL, CURIOUS, CONFUSED, FRUSTRATED, ANGRY, CELEBRATORY].
        Return ONLY the word.
        """
        sentiment = self.llm.generate_response(prompt).strip().upper()
        
        if sentiment in ["FRUSTRATED", "ANGRY"]:
            # Log immediate alert or adjust session state
            print(f"⚠️ High Emotion Detected for {user_id}: {sentiment}")
            return sentiment
        return "NEUTRAL"

    async def update_learning_profile(self, user_id: str):
        """
        Batch process: Analyzes last 20 messages to detect Neurodiverse patterns.
        Executed essentially as a background job.
        """
        # 1. Fetch History
        rows = db.fetch_all("""
            SELECT role, content FROM messages 
            WHERE username = ? 
            ORDER BY id DESC LIMIT 20
        """, (user_id,))
        
        if len(rows) < 5: return # Need data to profile

        history_text = "\n".join([f"{r['role']}: {r['content']}" for r in rows])

        # 2. The "Implicit Profiling" Prompt
        # We map behavior to adaptation strategies, NOT medical labels.
        prompt = f"""
        Analyze this student's interaction history to build a "Learning Profile".
        
        Chat History:
        {history_text}

        **DIAGNOSTIC CRITERIA:**
        1. **Attention/Focus:** Does the user ask to repeat things? Do they ignore long text? (Possible Pattern: Short Attention/ADHD-like preference).
        2. **Processing:** Do they ask for "simple terms" or "pictures"? (Possible Pattern: Visual Learner/Dyslexia-like preference).
        3. **Emotional Resilience:** do they quit easily? Do they get frustrated at errors? (Pattern: Low Frustration Tolerance).
        4. **Rigidity:** Do they ask the same question repeatedly expecting a specific format? (Pattern: High Structure Need).

        **OUTPUT JSON:**
        {{
            "attention_span": "short" | "normal" | "long",
            "preferred_modality": "text" | "visual" | "code_first",
            "frustration_level": "low" | "medium" | "high",
            "scaffolding_need": "high" | "low",
            "detected_traits": ["list 2-3 observed behaviors, e.g. 'dislikes walls of text'"]
        }}
        """
        
        response = self.llm.generate_response(prompt)
        try:
            profile_data = json.loads(response.replace("```json", "").replace("```", "").strip())
            
            # 3. Save to DB
            db.execute("UPDATE users SET learning_profile = ? WHERE username = ?", (json.dumps(profile_data), user_id))
            print(f"🧠 Updated Profile for {user_id}: {profile_data}")
            
        except Exception as e:
            print(f"Profiling failed: {e}")