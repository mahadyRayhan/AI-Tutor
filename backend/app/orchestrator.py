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

    def _classify_topic(self, query: str) -> str:
        """
        Uses an LLM to classify the user's query as STEM or Non-STEM.
        """
        self.logger.info(f"Classifying query: '{query}'")
        # --- IMPROVED CLASSIFIER PROMPT ---
        prompt = f"""
        Analyze the user's query below and classify its core subject matter as either "STEM" or "Non-STEM".

        - **STEM** topics relate to hard sciences, technology, engineering, and mathematics. Examples: "What is a DDoS attack?", "Explain the bias-variance trade-off.", "What are loss functions in machine learning?".
        - **Non-STEM** topics relate to humanities, arts, and social sciences. Examples: "What is the history of the University of Missouri?", "Summarize the purpose of the document about Mizzou's campus."

        Ignore instructional phrases like "According to the document" and focus only on the subject of the question.
        Your response must be a single word: STEM or Non-STEM.

        Query: "{query}"
        Classification:
        """
        response = self.llm_interface.generate_response(prompt).strip().lower()

        if "stem" in response:
            self.logger.info("Query classified as: STEM")
            return "STEM"
        else:
            self.logger.info("Query classified as: Non-STEM")
            return "Non-STEM"


    def _route_query(self, query: str) -> str:
        """
        Determines which agent to use based on the query.
        """
        query_lower = query.lower()
        image_keywords = ["generate an image", "create a picture", "draw", "visualize"]
        
        if any(keyword in query_lower for keyword in image_keywords):
            self.logger.info("Routing query to ImageAgent.")
            return "image_agent"
        else:
            self.logger.info("Routing query to RAGAgent.")
            return "rag_agent"

    async def handle_query(self, query: str, user_role: str, socratic: bool = False) -> Dict[str, Any]:
        """
        Asynchronously handles the user query by classifying, routing, and executing.
        """
        topic_class = self._classify_topic(query)
        agent_name = self._route_query(query)

        response = {}
        if agent_name == "rag_agent":
            response = self.rag_agent.run(query, user_role, socratic, topic_class=topic_class)
        elif agent_name == "image_agent":
            response = self.image_agent.run(query)
        else:
            response = {"answer": "Sorry, I'm not sure how to handle that request.", "sources": []}
            
        response['topic_class'] = topic_class
        return response