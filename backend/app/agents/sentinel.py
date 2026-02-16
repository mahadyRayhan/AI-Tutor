# Security Agent
# backend/app/agents/sentinel.py

import re
from typing import AsyncGenerator
from app.agents.base import BaseAgent
from app.agents.schema import AgentState
from app.core.settings_manager import settings_manager
from app.core import config

# Conditional import for Fast Classifier to optimize startup if not needed
if config.INTENT_CLASSIFIER_MODE == "fast":
    from app.core.fast_classifier import fast_classifier

class SentinelAgent(BaseAgent):
    """
    The Sentinel Agent acts as the first line of defense for the AI Tutor.
    
    It is responsible for:
    1. **Hard Security Checks**: Using Regex to catch prompt injections and jailbreaks immediately.
    2. **Intent Classification**: Determining if the user's request is a security risk using ML models.
    3. **Topic Locking**: Enforcing teacher-defined restrictions on specific concepts.
    
    If any check fails, the Sentinel blocks the request and stops further processing.
    """
    
    async def process(self, state: AgentState) -> AsyncGenerator[dict, None]:
        """
        Main processing loop for the Sentinel Agent.
        
        Args:
            state (AgentState): The current state of the agent workflow.
            
        Yields:
            dict: Security alerts or status updates.
        """
        query_lower = state.query.lower()
        
        # --- 1. HARD SECURITY CHECK (REGEX OVERRIDE) ---
        # We check this FIRST, before any expensive classification logic.
        # This catches common known jailbreaks even if the classifier misses them.
        security_triggers = [
            "ignore previous instructions", 
            "ignore all instructions",
            "forget your instructions",
            "system prompt",
            "you are now",
            "write a poem", # Specific catch for non-coding requests
            "hack ",
            "exploit",
            "exam solution",
            "answer key"
        ]
        
        if any(trigger in query_lower for trigger in security_triggers):
            msg = (
                "👋 I am an AI Tutor specialized strictly in **C Programming**.\n\n"
                "I can't help with general knowledge, personal questions, or other topics. "
                "But I **can** help you with:\n\n"
                "🔹 **Concepts** (Pointers, Arrays, Structs)\n"
                "🔹 **Debugging** (Fixing errors, Segfaults)\n"
                "🔹 **Writing Code** (Solving exercises)"
            )
            # Update state to block downstream agents
            state.final_response = msg
            state.stop_processing = True
            
            yield {"type": "complete", "data": {"answer": msg, "sources": [], "intent": "SECURITY_RISK"}}
            return
        # -----------------------------------------------

        # --- 2. CODE DETECTION (NEW) ---
        # We check if the user is submitting raw code.
        # If so, we force the intent to "REVIEW" immediately to bypass the Gatekeeper.
        # This prevents the system from thinking "int main()" is a conceptual question about integers.
        code_markers = [";", "{", "}", "#include", "int main", "printf(", "scanf(", "return 0", "void ", "char *"]
        match_count = sum(1 for m in code_markers if m in query_lower)
        
        if match_count >= 2:
            intent = "REVIEW"
            # Set generic entity to avoid triggering prerequisite checks on keywords like "int"
            entities = ["code submission"]
        else:
            # 3. CLASSIFICATION (Lightweight)
            # We determine the intent early so other agents don't have to re-run this logic.
            intent = "General"
            entities = []

            if config.INTENT_CLASSIFIER_MODE == "fast":
                # Fast mode uses local embedding model (efficient)
                intent = fast_classifier.classify_intent(state.query)
                entities = fast_classifier.extract_entities(state.query)
            else:
                # Fallback if fast mode is off: Assume a default safe intent.
                intent = "CONCEPT" 
                entities = [state.query]
        # -----------------------------------------------

        # Save to shared state so other agents can access it (Optimization)
        state.intent = intent
        state.entities = entities

        # 4. CHECK INTENT-BASED SECURITY (Double Check)
        # If the ML classifier flagged it as SECURITY_RISK but our regex missed it
        if intent == "SECURITY_RISK":
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

        # 5. CHECK TOPIC LOCKS (Teacher Settings)
        # Teachers can disable specific topics (e.g., "Pointers") until a certain date.
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

        # If we get here, the request is safe and allowed.
        # The processing continues to the next agent in the chain.