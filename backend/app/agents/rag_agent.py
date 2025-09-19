# backend/app/agents/rag_agent.py

import logging
from typing import Dict, List, Any, Optional

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

    def _clean_query_for_embedding(self, query: str) -> str:
        """
        Uses an LLM to strip conversational filler and instructions from a query,
        leaving only the core question for better semantic search.
        """
        self.logger.info("Cleaning query for embedding...")
        prompt = f"""
        Extract the essential question from the user's query below.
        Remove any conversational filler, greetings, or instructions like "According to the document...".
        Your response should ONLY be the core question.

        User Query: "{query}"
        
        Core Question:
        """
        cleaned_query = self.llm_interface.generate_response(prompt).strip()
        self.logger.info(f"Cleaned query: '{cleaned_query}'")
        return cleaned_query

    def _execute_retrieval(self, query: str, user_role: str, top_k: int) -> List[Dict[str, Any]]:
        """
        Performs retrieval from the vector store, applying RBAC filters.
        """
        # --- NEW STEP: CLEAN THE QUERY BEFORE EMBEDDING ---
        embedding_query = self._clean_query_for_embedding(query)

        self.logger.info(f"Executing retrieval for user role: '{user_role}' with top_k: {top_k}")
        query_embedding = self.llm_interface.get_embedding(embedding_query, task_type="RETRIEVAL_QUERY")
        if not query_embedding:
            self.logger.error("Failed to generate embedding for query.")
            return []

        where_filter = {}
        if user_role == 'student':
            where_filter = {"access_level": "student"}
        
        retrieved_chunks = self.vector_store.query(
            query_embedding=query_embedding,
            top_k=top_k,
            where_filter=where_filter
        )
        return retrieved_chunks

    def _generate_final_answer(self, query: str, context_chunks: List[Dict[str, Any]], socratic: bool = False) -> str:
        # This function remains the same as our last version with the improved prompts.
        # ... (omitted for brevity)
        if not context_chunks:
            return "I could not find relevant information in the provided materials to answer your question."

        context_str = "\n\n---\n\n".join([f"Source Document: {chunk['metadata'].get('document_name', 'Unknown')}\n\n{chunk['text']}" for chunk in context_chunks])
        
        if socratic:
            prompt = f"""You are a Socratic AI Tutor. Your only goal is to ask a guiding question that helps the student think for themselves.

            **USER'S QUESTION:**
            "{query}"

            **CONTEXT FROM DOCUMENTS:**
            ---
            {context_str}
            ---

            **YOUR TASK:**
            - DO NOT answer the user's question directly.
            - Your entire response MUST be a single, insightful question.
            - The question should be based on the provided context and guide the student toward the answer.
            - If the context provides a direct definition, ask a question that makes the student apply that definition.

            **YOUR GUIDING QUESTION:**
            """
        else:
            prompt = f"""You are an expert AI Tutor. Your task is to provide a comprehensive, complete, and well-structured answer to the user's question based *only* on the provided context.

            **Your Task:**
            1.  Carefully read and synthesize the information from all provided context snippets.
            2.  Construct a detailed and thorough answer that directly addresses the user's question.
            3.  If the question involves comparison (e.g., "What is the difference between X and Y?"), you MUST describe both X and Y and then highlight their key differences.
            4.  If the question asks for a list or summary of techniques, you MUST include all relevant techniques mentioned in the context.
            5.  Structure your answer clearly using bullet points or numbered lists where appropriate. Do not omit any relevant details from the context.

            **CRITICAL RULES:**
            - Your answer MUST be based exclusively on the provided context. Do not use any outside knowledge.
            - If the context is insufficient to provide a complete answer, you MUST state that the provided materials do not contain enough information. Do not try to answer partially.

            User's Question: {query}

            Context:
            ---
            {context_str}
            ---

            Comprehensive Answer:
            """
        
        return self.llm_interface.generate_response(prompt)


    def run(self, query: str, user_role: str = 'student', socratic: bool = False, topic_class: str = "Non-STEM") -> Dict[str, Any]:
        """
        The main entry point for the RAG agent.
        """
        self.logger.info(f"RAG Agent running for query: '{query}' with Socratic mode: {socratic} and Topic: {topic_class}")
        
        top_k_for_retrieval = 12
        
        retrieved_chunks = self._execute_retrieval(query, user_role, top_k=top_k_for_retrieval)
        
        final_answer = self._generate_final_answer(query, retrieved_chunks, socratic=socratic)
        
        sources = [chunk['metadata'] for chunk in retrieved_chunks]
        
        return {
            "answer": final_answer,
            "sources": sources
        }