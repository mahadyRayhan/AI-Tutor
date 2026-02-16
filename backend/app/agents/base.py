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