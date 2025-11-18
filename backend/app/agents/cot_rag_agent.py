# backend/app/agents/cot_rag_agent.py

import logging
import json
import re
from typing import Dict, List, Any
from dataclasses import dataclass

from app.db.llm_interface import LLMInterface
from app.db.vector_store import VectorStore
from app.db.graph_db import Neo4jGraphDB

@dataclass
class CoTStep:
    step_number: int
    reasoning: str
    conclusion: str
    confidence: float

class ChainOfThoughtRAGAgent:
    def __init__(self, llm_interface: LLMInterface, vector_store: VectorStore, graph_db: Neo4jGraphDB, logger: logging.Logger):
        self.llm_interface = llm_interface
        self.vector_store = vector_store
        self.graph_db = graph_db
        self.logger = logger

    def _classify_intent(self, query: str) -> str:
        """
        Decides if the student is asking for a definition (Concept) or how to do something (Problem).
        """
        prompt = f"""
        Classify this student query about C programming into one of these categories:
        1. CONCEPT: Asking "what is...", "explain...", "definition of..."
        2. PROBLEM: Asking "how to...", "write a code...", "solve..."
        3. DEBUG: Asking "why is this error...", "fix this..."

        Query: "{query}"
        
        Respond with ONE word: CONCEPT, PROBLEM, or DEBUG.
        """
        return self.llm_interface.generate_response(prompt).strip().upper()

    def _expand_query_using_graph(self, query: str, initial_entities: List[str]) -> List[str]:
        """
        Uses the Knowledge Graph to find prerequisites or related concepts.
        """
        expanded_terms = []
        
        # 1. Find prerequisites (If user asks about X, and X requires Y, add Y)
        for entity in initial_entities:
            # Cypher: Find things that are prerequisites for this entity
            # Also find things that explain this entity
            cypher = """
            MATCH (req)-[:IS_PREREQUISITE_FOR]->(target {name: $name})
            RETURN req.name as name
            UNION
            MATCH (target {name: $name})-[:REQUIRES_UNDERSTANDING_OF]->(req)
            RETURN req.name as name
            """
            results = self.graph_db.execute_query(cypher, {"name": entity})
            for record in results:
                expanded_terms.append(record['name'])
        
        return list(set(expanded_terms))

    def _execute_retrieval(self, query: str, intent: str) -> List[Dict[str, Any]]:
        """
        Smart retrieval combining Vector Search + Graph Expansion.
        """
        # 1. Extract keywords (Simple LLM call)
        extract_prompt = f"Extract the main C programming terms from: '{query}'. Return as comma-separated list (e.g. Arrays, int, printf)."
        entities_str = self.llm_interface.generate_response(extract_prompt)
        # clean up list
        entities = [e.strip() for e in entities_str.split(',') if e.strip()]
        
        self.logger.info(f"Extracted Entities: {entities}")

        # 2. Graph Expansion (Find prerequisites)
        related_terms = self._expand_query_using_graph(query, entities)
        self.logger.info(f"Graph suggested related terms: {related_terms}")

        # 3. Vector Search (Primary Query)
        query_embedding = self.llm_interface.get_embedding(query)
        chunks = self.vector_store.query(query_embedding, top_k=3)

        # 4. Vector Search (Related Terms - if any)
        if related_terms:
            expanded_query = " ".join(related_terms)
            expanded_embedding = self.llm_interface.get_embedding(expanded_query)
            # Fetch 2 extra chunks based on prerequisites
            related_chunks = self.vector_store.query(expanded_embedding, top_k=2)
            chunks.extend(related_chunks)

        return chunks
    
    def _generate_socratic_plan(self, query: str, context: str) -> str:
        prompt = f"""
        You are an encouraging C Programming Tutor for junior students.
        
        Student Query: "{query}"
        
        Reference Material:
        {context}

        **CRITICAL RULES:**
        1. **CONCEPT LIMITATION:** You may ONLY teach concepts that are present in the Reference Material.
           - If the student asks about a concept (like 'switch', 'pointers', 'recursion') that is NOT in the Reference Material, you MUST say: "I don't have information on [Concept] in my current library."
           - Then, try to solve their problem using ONLY the concepts you DO have (e.g., use `if/else` instead of `switch`).
        2. **SOURCE GROUNDING:** You MUST mention specific variable names/examples from the text (e.g., "In `demo_basics.c`, we used...").
        3. **GENERIC SYNTAX:** Provide generic syntax for the concepts you explain.
        4. **SOCRATIC METHOD:** Do not write the full solution.
        5. **TONE INSTRUCTIONS:** 
           - Speak DIRECTLY to the student. Use "You" and "We". 
           - NEVER say "The student wants..." or "The user is asking...". 
           - Instead, say "To solve this, we need to..." or "Since you want to..."

        Format your response like this:
        
        ## Strategy
        [Explain the logical approach directly to the student. Example: "To count items, we first need a specific type of variable..."]

        ## Implementation Plan
        1. **[Step Name]**: [Description]
           - *Example from text:* "In [Filename], we saw..."
           ```c
           // Generic Syntax
           code...
           ```
        
        ## Guiding Question
        [Your question here]
        """
        return self.llm_interface.generate_response(prompt)

    def run(self, query: str, user_role: str = 'student', **kwargs) -> Dict[str, Any]:
        self.logger.info(f"Processing Query: {query}")
        
        # 1. Intent
        intent = self._classify_intent(query)
        self.logger.info(f"Intent Classified: {intent}")
        
        # 2. Retrieval
        retrieved_chunks = self._execute_retrieval(query, intent)
        
        # Format context for LLM
        context_text = ""
        for c in retrieved_chunks:
            source = c.get('metadata', {}).get('document_name', 'Unknown')
            text = c.get('text', '')
            context_text += f"--- Source: {source} ---\n{text}\n\n"
        
        # 3. Socratic Generation
        if intent == "PROBLEM" or intent == "DEBUG":
            final_answer = self._generate_socratic_plan(query, context_text)
        else:
            # For simple concept questions, explain clearly
            final_answer = self.llm_interface.generate_response(
                f"Explain this C concept clearly for a beginner using the provided context.\nQ: {query}\n\nContext:\n{context_text}"
            )

        # 4. Structure Response
        formatted_sources = []
        for c in retrieved_chunks:
            source_data = c.get('metadata', {}).copy()
            # Ensure text is grabbed from the correct key. 
            # Sometimes vector store uses 'text', sometimes 'chunk_text'.
            source_data['chunk_text'] = c.get('text') or c.get('chunk_text') or 'Text missing'
            formatted_sources.append(source_data)

        # --- DEBUG PRINT ---
        print(f"DEBUG SOURCES: Found {len(formatted_sources)} sources.")
        if len(formatted_sources) > 0:
            print(f"Sample Source 1: {formatted_sources[0].get('document_name')}")
        # -------------------

        return {
            "answer": final_answer,
            "sources": formatted_sources, 
            "intent": intent,
            "cot_analysis": None,
            "reasoning_quality": 1.0 
        }