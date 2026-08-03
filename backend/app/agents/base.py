# Abstract Base Class Agent
from abc import ABC, abstractmethod
from typing import AsyncGenerator
import logging
from app.agents.schema import AgentState
from app.db.llm_interface import LLMInterface

class BaseAgent(ABC):
    def __init__(self, llm: LLMInterface, logger: logging.Logger):
        self.llm = llm
        self.logger = logger

    @abstractmethod
    async def process(self, state: AgentState) -> AsyncGenerator[dict, None]:
        """
        Processes the state.
        Yields chunks: {"type": "token", "text": "..."} or {"type": "status", ...}
        Returns updated state (implicitly via object mutation or return).
        """
        pass

    def _preference_directive(self, profile: dict) -> str:
        """Build a shared tone/format directive from the student's saved preferences.

        F7-02: preferences (custom tone, no-diagrams, concise, literal) were only
        applied in the concept-teaching path, so other LLM-generated responses ignored
        them. Append this to any FREE-TEXT prose prompt so every agent honors the same
        preferences. Do NOT append to prompts that must return structured JSON.
        Returns "" when no preferences are set.
        """
        prefs = (profile or {}).get("tutor_preferences", {}) or {}
        parts = []
        custom = (prefs.get("custom_instructions") or "").strip()
        if custom:
            parts.append(f"Follow the student's custom instructions verbatim: {custom}")
        if prefs.get("concise_mode"):
            parts.append("Be extremely concise — no filler.")
        if prefs.get("literal_mode"):
            parts.append("Be literal and precise; avoid metaphors and analogies.")
        if prefs.get("show_visual_model") is False:
            parts.append("Do NOT include any diagrams, ASCII art, or Mermaid charts.")
        if not parts:
            return ""
        return "\n\n**STUDENT PREFERENCES (MUST FOLLOW):**\n- " + "\n- ".join(parts)