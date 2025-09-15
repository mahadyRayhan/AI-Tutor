# backend/app/agents/rag_agent.py

import logging
from typing import Dict, List, Any, Optional

# Adjusted imports
from app.core import config
from app.db.llm_interface import LLMInterface
from app.db.vector_store import VectorStore
from app.db.graph_db import Neo4jGraphDB

class RAGAgent:
    """
    Agent responsible for handling Retrieval-Augmented Generation queries.
    """
    def __init__(self,
                 llm_interface: LLMInterface,
                 vector_store: VectorStore,
                 graph_db: Neo4jGraphDB,
                 logger: logging.Logger):
        self.llm_interface = llm_interface
        self.vector_store = vector_store
        self.graph_db = graph_db
        self.logger = logger
        # if not self.vector_store.is_ready():
        #     self.logger.critical("Vector store for RAGAgent is not ready!")

    def _execute_retrieval(self, query: str, user_role: str, top_k: int) -> List[Dict[str, Any]]:
        """
        Performs retrieval from the vector store, applying RBAC filters.
        """
        self.logger.info(f"Executing retrieval for user role: '{user_role}'")
        query_embedding = self.llm_interface.get_embedding(query, task_type="RETRIEVAL_QUERY")
        if not query_embedding:
            self.logger.error("Failed to generate embedding for query.")
            return []

        # --- RBAC FILTER LOGIC ---
        where_filter = {}
        if user_role == 'student':
            # Students can only access documents with 'student' access level
            where_filter = {"access_level": "student"}
        # Teachers have access to all documents, so no filter is applied.

        retrieved_chunks = self.vector_store.query(
            query_embedding=query_embedding,
            top_k=top_k,
            where_filter=where_filter
        )
        return retrieved_chunks

    def _generate_final_answer(self, query: str, context_chunks: List[Dict[str, Any]]) -> str:
        """
        Generates the final answer based on the query and retrieved context.
        """
        if not context_chunks:
            return "I could not find relevant information in the provided materials to answer your question."

        context_str = "\n\n---\n\n".join([chunk['text'] for chunk in context_chunks])
        
        prompt = f"""You are an expert AI Tutor. Your task is to provide a clear, concise, and helpful answer to the user's question based ONLY on the provided context.

        User's Question: {query}

        Context:
        ---
        {context_str}
        ---

        Answer:
        """
        
        return self.llm_interface.generate_response(prompt)

    def run(self, query: str, user_role: str = 'student') -> Dict[str, Any]:
        """
        The main entry point for the RAG agent.
        
        Args:
            query (str): The user's question.
            user_role (str): The role of the user ('student' or 'teacher').
            
        Returns:
            A dictionary containing the answer and sources.
        """
        self.logger.info(f"RAG Agent running for query: '{query}'")
        
        # Step 1: Retrieval with RBAC
        retrieved_chunks = self._execute_retrieval(query, user_role, config.DEFAULT_TOP_K)
        
        # Step 2: Generate Answer
        final_answer = self._generate_final_answer(query, retrieved_chunks)
        
        # Step 3: Format sources
        sources = [chunk['metadata'] for chunk in retrieved_chunks]
        
        return {
            "answer": final_answer,
            "sources": sources
        }