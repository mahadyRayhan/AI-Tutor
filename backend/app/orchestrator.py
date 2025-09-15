# backend/app/orchestrator.py

import logging
from typing import Dict, Any

from app.agents.rag_agent import RAGAgent
from app.agents.image_agent import ImageAgent
from app.db.llm_interface import LLMInterface


class Orchestrator:
    """
    The orchestrator routes user requests to the appropriate agent
    and returns the agent's response.
    """
    def __init__(self, llm_interface: LLMInterface, rag_agent: RAGAgent, image_agent: ImageAgent, logger: logging.Logger):
        self.llm_interface = llm_interface
        self.rag_agent = rag_agent
        self.image_agent = image_agent
        self.logger = logger

    def _route_query(self, query: str) -> str:
        """
        Determines which agent to use based on the query.
        This can be improved with an LLM call for more complex routing.
        """
        query_lower = query.lower()
        image_keywords = ["generate an image", "create a picture", "draw", "visualize"]
        
        if any(keyword in query_lower for keyword in image_keywords):
            self.logger.info("Routing query to ImageAgent.")
            return "image_agent"
        else:
            self.logger.info("Routing query to RAGAgent.")
            return "rag_agent"

    async def handle_query(self, query: str, user_role: str) -> Dict[str, Any]:
        """
        Asynchronously handles the user query by routing it to the correct agent.
        """
        agent_name = self._route_query(query)

        if agent_name == "rag_agent":
            # Running synchronous code in an async context
            # In a real high-load app, you might run this in a thread pool
            response = self.rag_agent.run(query, user_role)
        elif agent_name == "image_agent":
            response = self.image_agent.run(query)
        else:
            response = {"answer": "Sorry, I'm not sure how to handle that request.", "sources": []}
            
        return response