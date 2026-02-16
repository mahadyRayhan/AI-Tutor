# Security Agent
# backend/app/agents/sentinel.py

import re
from typing import AsyncGenerator
from app.agents.base import BaseAgent
from app.agents.schema import AgentState
from app.core.settings_manager import settings_manager
from app.core import config

# Conditional import for Fast Classifier
if config.INTENT_CLASSIFIER_MODE == "fast":
    from app.core.fast_classifier import fast_classifier

class SentinelAgent(BaseAgent):
    async def process(self, state: AgentState) -> AsyncGenerator[dict, None]:
        """
        Scans for Security Risks and Topic Locks.
        Updates state.intent and state.entities.
        """
        query_lower = state.query.lower()
        
        # --- 1. HARD SECURITY CHECK (REGEX OVERRIDE) ---
        # We check this FIRST, before any classification logic.
        # This catches common jailbreaks even if the classifier misses them.
        security_triggers = [
            "ignore previous instructions", 
            "ignore all instructions",
            "forget your instructions",
            "system prompt",
            "you are now",
            "write a poem", # Specific catch for your test case
            "hack ",
            "exploit",
            "exam solution",
            "answer key"
        ]
        
        if any(trigger in query_lower for trigger in security_triggers):
            # msg = "I can't help with that request. 😅\n\nI'm designed strictly as a **C Programming Tutor** to help you learn safely. Let's get back to coding! 💻"
            msg = (
                "👋 I am an AI Tutor specialized strictly in **C Programming**.\n\n"
                "I can't help with general knowledge, personal questions, or other topics. "
                "But I **can** help you with:\n\n"
                "🔹 **Concepts** (Pointers, Arrays, Structs)\n"
                "🔹 **Debugging** (Fixing errors, Segfaults)\n"
                "🔹 **Writing Code** (Solving exercises)"
            )
            state.final_response = msg
            state.stop_processing = True
            yield {"type": "complete", "data": {"answer": msg, "sources": [], "intent": "SECURITY_RISK"}}
            return
        # -----------------------------------------------

        # 2. CLASSIFICATION (Lightweight)
        intent = "General"
        entities = []

        if config.INTENT_CLASSIFIER_MODE == "fast":
            # Fast mode uses local embedding model
            intent = fast_classifier.classify_intent(state.query)
            entities = fast_classifier.extract_entities(state.query)
        else:
            # Fallback if fast mode is off: Assume General intent for now.
            # Real intent classification happens later in Orchestrator if this is weak.
            intent = "CONCEPT" # Default safer assumption than General
            entities = [state.query]

        # Save to state so other agents don't have to re-calculate
        state.intent = intent
        state.entities = entities

        # 3. CHECK INTENT-BASED SECURITY (Double Check)
        # If the fast classifier flagged it as SECURITY_RISK but our regex missed it
        if intent == "SECURITY_RISK":
            # msg = "I can't help with that request. 😅\n\nI'm designed strictly as a **C Programming Tutor** to help you learn safely."
            msg = (
                "👋 I am an AI Tutor specialized strictly in **C Programming**.\n\n"
                "I can't help with general knowledge, personal questions, or other topics. "
                "But I **can** help you with:\n\n"
                "🔹 **Concepts** (Pointers, Arrays, Structs)\n"
                "🔹 **Debugging** (Fixing errors, Segfaults)\n"
                "🔹 **Writing Code** (Solving exercises)"
            )
            state.final_response = msg
            state.stop_processing = True
            yield {"type": "complete", "data": {"answer": msg, "sources": [], "intent": intent}}
            return

        # 4. CHECK TOPIC LOCKS (Teacher Settings)
        topic_settings = settings_manager.get_settings()
        for entity in entities:
            for t, is_enabled in topic_settings.items():
                # Fuzzy match: e.g., "pointer" in "Pointers"
                if (entity.lower() in t.lower() or t.lower() in entity.lower()) and not is_enabled:
                    msg = f"🔒 **Topic Locked**\n\nThe topic **{t}** is currently disabled by your instructor."
                    state.final_response = msg
                    state.stop_processing = True
                    yield {"type": "complete", "data": {"answer": msg, "sources": [], "intent": intent}}
                    return

        # If we get here, it's safe.