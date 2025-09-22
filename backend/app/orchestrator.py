# backend/app/orchestrator.py

import logging
from typing import Dict, Any, List

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
        
        prompt = f"""You are a topic classifier. Classify the following query as either "STEM" or "Non-STEM".

            STEM includes: Science, Technology, Engineering, Mathematics, Computer Science, Physics, Chemistry, Biology, etc.
            Non-STEM includes: History, Literature, Art, Philosophy, Social Sciences, Humanities, etc.

            Focus on the PRIMARY SUBJECT of the query, not incidental words or numbers.

            Examples:
            - "What is machine learning?" → STEM (computer science topic)
            - "When was the University of Missouri established?" → Non-STEM (university history)
            - "Calculate the derivative of x²" → STEM (mathematics)
            - "Who wrote Romeo and Juliet?" → Non-STEM (literature)
            - "What is the history of calculus?" → Non-STEM (historical topic, even though calculus is math)
            - "How does photosynthesis work?" → STEM (biology)

            Query: "{query}"

            Respond with exactly one word: STEM or Non-STEM"""

        response = self.llm_interface.generate_response(prompt).strip()
        
        # More robust parsing - check for exact matches
        response_upper = response.upper()
        
        # Look for exact classification matches
        if response_upper == "STEM":
            self.logger.info("Query classified as: STEM")
            return "STEM"
        elif response_upper == "NON-STEM" or "NON-STEM" in response_upper:
            self.logger.info("Query classified as: Non-STEM")
            return "Non-STEM"
        else:
            # Fallback: if response contains stem but not in the context of "non-stem"
            if "STEM" in response_upper and "NON" not in response_upper:
                self.logger.info("Query classified as: STEM (fallback)")
                return "STEM"
            else:
                self.logger.info("Query classified as: Non-STEM (fallback)")
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

    def find_chunks_by_entities(self, entity_names: List[str]) -> List[Dict[str, Any]]:
        """
        Finds all chunk texts and metadata linked to a list of entity names.
        """
        if not entity_names:
            return []

        query = """
        UNWIND $entity_names AS entityName
        MATCH (e:Entity)-[:MENTIONS]-(c:Chunk)-[:HAS_CHUNK]-(d:Document)
        WHERE toLower(e.name) = toLower(entityName)
        RETURN c.text AS text,
               c.id AS chunk_id,
               d.name AS document_name,
               c.element_type AS element_type,
               c.access_level AS access_level
        """
        parameters = {"entity_names": entity_names}
        results = self.execute_query(query, parameters)
        
        # Format the results to match the vector store's output
        formatted_results = []
        for record in results:
            formatted_results.append({
                "text": record['text'],
                "metadata": {
                    "chunk_id": record['chunk_id'],
                    "document_name": record['document_name'],
                    "element_type": record['element_type'],
                    "access_level": record['access_level']
                }
            })
        return formatted_results