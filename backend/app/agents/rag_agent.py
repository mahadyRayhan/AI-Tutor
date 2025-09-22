# backend/app/agents/rag_agent.py (Enhanced for multi-domain content)

import logging
import json
import re 
from typing import Dict, List, Any

from app.core import config
from app.db.llm_interface import LLMInterface
from app.db.vector_store import VectorStore
from app.db.graph_db import Neo4jGraphDB

class RAGAgent:
    """
    Agent responsible for handling Retrieval-Augmented Generation queries across STEM and non-STEM domains.
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

    def _detect_query_domain(self, query: str) -> str:
        """
        Detects whether a query is STEM or non-STEM focused.
        """
        stem_indicators = [
            'algorithm', 'programming', 'software', 'code', 'system', 'network', 
            'security', 'encryption', 'vulnerability', 'attack', 'machine learning',
            'neural network', 'model', 'training', 'AI', 'artificial intelligence',
            'computer science', 'cyber', 'technology', 'technical', 'data structure',
            'database', 'protocol', 'framework', 'API', 'implementation'
        ]
        
        non_stem_indicators = [
            'history', 'historical', 'tradition', 'culture', 'literature', 'author',
            'century', 'war', 'revolution', 'founded', 'established', 'first',
            'university', 'college', 'institution', 'homecoming', 'celebration',
            'movement', 'society', 'philosophy', 'art', 'narrative', 'story'
        ]
        
        query_lower = query.lower()
        
        stem_score = sum(1 for indicator in stem_indicators if indicator in query_lower)
        non_stem_score = sum(1 for indicator in non_stem_indicators if indicator in query_lower)
        
        # Default to non-STEM if unclear, since historical/institutional queries are common
        return "STEM" if stem_score > non_stem_score else "Non-STEM"

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
    
    def _extract_entities_from_query(self, query: str, query_domain: str) -> List[str]:
        """
        Domain-aware entity extraction from queries.
        """
        self.logger.info(f"Extracting entities from {query_domain} query...")
        
        if query_domain == "STEM":
            domain_context = """
            For STEM queries, extract:
            - Technical terms and concepts (algorithms, systems, protocols)
            - Technology names (programming languages, frameworks, tools)
            - Research areas (machine learning, cybersecurity, computer science)
            - Performance metrics and methods
            - Technical organizations and institutions
            """
            
            related_terms = """
            Include related technical concepts like:
            - For "security": vulnerability, threat, attack, defense, encryption
            - For "machine learning": model, training, dataset, algorithm, neural network
            - For "programming": code, software, language, framework, library
            """
        else:
            domain_context = """
            For non-STEM queries, extract:
            - Proper nouns (people, places, organizations, institutions)
            - Historical concepts and events
            - Cultural and traditional elements
            - Academic institutions and achievements
            - Time periods and dates
            """
            
            related_terms = """
            Include related concepts like:
            - For "firsts": founding, established, started, tradition, pioneering, innovation
            - For institutions: university, college, school, organization
            - For history: historical, chronological, traditional, cultural
            """

        prompt = f"""
        {domain_context}
        
        From the user's question below, extract key entities and related concepts:
        {related_terms}
        
        Return them as a JSON list of strings.

        Question: "{query}"
        
        JSON Output:
        """
        response_str = self.llm_interface.generate_response(prompt).strip()
        
        match = re.search(r'\[.*\]', response_str, re.DOTALL)
        if not match:
            self.logger.warning(f"Could not find a JSON list in the LLM response: {response_str}")
            return []

        json_str = match.group(0)
        try:
            entities = json.loads(json_str)
            if isinstance(entities, list):
                self.logger.info(f"Extracted {query_domain} entities: {entities}")
                return entities
            else:
                self.logger.warning(f"LLM returned valid JSON but not a list: {json_str}")
                return []
        except json.JSONDecodeError:
            self.logger.warning(f"Failed to parse entities from LLM response: {json_str}")
            return []

    def _execute_retrieval(self, query: str, user_role: str, query_domain: str, top_k: int) -> List[Dict[str, Any]]:
        """
        Multi-domain hybrid retrieval with domain-specific optimization.
        """
        entities = self._extract_entities_from_query(query, query_domain)
        
        # Knowledge graph retrieval
        kg_chunks = self.graph_db.get_context_for_entities(entities)
        
        # Domain-specific broader entity search
        broader_entities = []
        if query_domain == "STEM":
            broader_entities = ["technology", "system", "algorithm", "security", "machine learning", "computer science"]
        else:
            broader_entities = ["university", "institution", "history", "tradition", "first", "established"]
        
        # Check if query mentions specific institutions
        query_lower = query.lower()
        if any(term in query_lower for term in ["university", "missouri", "mizzou"]):
            broader_entities.extend(["University of Missouri", "Mizzou", "Missouri"])
        
        main_entity_chunks = self.graph_db.get_context_for_entities(broader_entities)

        # Vector search with domain context
        embedding_query = self._clean_query_for_embedding(query)
        query_embedding = self.llm_interface.get_embedding(embedding_query, task_type="RETRIEVAL_QUERY")
        
        vector_chunks = []
        if query_embedding:
            where_filter = {"access_level": "student"} if user_role == 'student' else {}
            vector_chunks = self.vector_store.query(
                query_embedding=query_embedding,
                top_k=top_k,
                where_filter=where_filter
            )

        # Domain-specific expanded search
        expanded_vector_chunks = []
        if query_embedding:
            if query_domain == "STEM":
                expanded_query = f"{embedding_query} technical system implementation algorithm method approach"
            else:
                expanded_query = f"{embedding_query} history establishment founding first tradition innovation pioneering"
            
            expanded_embedding = self.llm_interface.get_embedding(expanded_query, task_type="RETRIEVAL_QUERY")
            if expanded_embedding:
                expanded_vector_chunks = self.vector_store.query(
                    query_embedding=expanded_embedding,
                    top_k=top_k//2,
                    where_filter=where_filter
                )

        # Text-based fallback search for specific terms
        text_search_chunks = []
        if query_domain == "Non-STEM" and any(term in query_lower for term in ["first", "tradition", "homecoming", "founded"]):
            search_terms = ["homecoming", "tradition", "first", "founded", "established", "started"]
            text_search_chunks = self.graph_db.search_chunks_by_text_content(search_terms)

        # Combine all sources
        all_chunks = kg_chunks + main_entity_chunks + vector_chunks + expanded_vector_chunks + text_search_chunks
        combined_chunks = {}
        for chunk in all_chunks:
            if chunk.get('metadata') and 'chunk_id' in chunk['metadata']:
                chunk_id = chunk['metadata']['chunk_id']
                if chunk_id not in combined_chunks:
                    combined_chunks[chunk_id] = chunk
        
        self.logger.info(f"Retrieved {len(combined_chunks)} unique chunks for {query_domain} query")
        return list(combined_chunks.values())

    def _reason_about_context(self, query: str, context_chunks: List[Dict[str, Any]], query_domain: str) -> str:
        """
        Domain-aware reasoning with specialized instructions.
        """
        self.logger.info(f"Performing enhanced {query_domain} reasoning...")
        if not context_chunks:
            return "The context is empty."
            
        context_str = "\n\n---\n\n".join([chunk['text'] for chunk in context_chunks])

        if query_domain == "STEM":
            domain_instructions = """
            **STEM-Specific Instructions:**
            1. **Technical Relationships:** Look for implementation details, system architectures, method applications
            2. **Performance Metrics:** Extract benchmarks, improvements, comparisons
            3. **Research Contributions:** Identify novel approaches, innovations, breakthroughs
            4. **Problem-Solution Mapping:** Connect problems to their technical solutions
            5. **Methodological Details:** Extract algorithms, processes, and technical approaches
            """
        else:
            domain_instructions = """
            **Non-STEM Specific Instructions:**
            1. **Historical Inference:** "Started" = "First", "Founded" = "First established", "Pioneered" = "First to do"
            2. **Chronological Significance:** Look for temporal firsts and historical precedence
            3. **Institutional Achievements:** Extract university/organizational accomplishments
            4. **Traditional Elements:** Identify customs, practices, and cultural significance
            5. **Legacy and Impact:** Consider long-term influence and historical importance
            """

        prompt = f"""
        You are analyzing {query_domain} content to answer the user's question. Extract ALL relevant facts with appropriate domain expertise.

        {domain_instructions}

        **Universal Requirements:**
        - Extract EVERY piece of information relevant to the question
        - Make logical inferences based on context
        - Look for both direct statements and implied information
        - Consider chronological and causal relationships

        **User's Question:** {query}

        **Context to Analyze:**
        ---
        {context_str}
        ---

        **Complete Analysis and Fact Extraction:**
        """
        
        reasoned_context = self.llm_interface.generate_response(prompt)
        self.logger.info(f"Completed {query_domain} reasoning analysis")
        return reasoned_context
    
    def _generate_final_answer(self, query: str, reasoned_context: str, query_domain: str) -> str:
        """
        Domain-aware final answer generation.
        """
        self.logger.info(f"Generating final {query_domain} answer...")
        
        if query_domain == "STEM":
            answer_style = """
            **STEM Answer Style:**
            - Provide technical accuracy and precision
            - Include relevant technical details and specifications
            - Explain methodologies and approaches clearly
            - Use appropriate technical terminology
            - Structure information logically (problem → solution → results)
            """
        else:
            answer_style = """
            **Non-STEM Answer Style:**
            - Provide comprehensive historical context
            - Present information chronologically when relevant
            - Include cultural and institutional significance
            - Use clear, educational language
            - Structure as a complete narrative or organized list
            """

        prompt = f"""
        You are providing a {query_domain} answer based on the analyzed facts below.

        {answer_style}

        **Instructions:**
        1. Synthesize ALL facts into a complete, well-structured answer
        2. Present information clearly and comprehensively
        3. Include all relevant details found in the analysis
        4. Use appropriate domain-specific language and context
        5. Do NOT invent information beyond the provided facts

        **User's Question:** {query}

        **Analyzed Facts:** 
        ---
        {reasoned_context}
        ---

        **Your Complete {query_domain} Answer:**
        """
        
        return self.llm_interface.generate_response(prompt)

    def run(self, query: str, user_role: str = 'student', socratic: bool = False, topic_class: str = "Non-STEM") -> Dict[str, Any]:
        """
        Enhanced multi-domain RAG pipeline.
        """
        # Detect query domain automatically, but allow override via topic_class
        detected_domain = self._detect_query_domain(query)
        query_domain = topic_class if topic_class in ["STEM", "Non-STEM"] else detected_domain
        
        self.logger.info(f"RAG Agent processing {query_domain} query: '{query}' (Socratic: {socratic})")
        
        # Enhanced retrieval with domain awareness
        top_k_for_retrieval = 12  # Increased for multi-domain coverage
        retrieved_chunks = self._execute_retrieval(query, user_role, query_domain, top_k=top_k_for_retrieval)

        if not retrieved_chunks:
            return {"answer": "I could not find any relevant information to answer your question.", "sources": []}

        # Domain-aware reasoning
        reasoned_context = self._reason_about_context(query, retrieved_chunks, query_domain)

        # Domain-aware final answer generation
        final_answer = self._generate_final_answer(query, reasoned_context, query_domain)
        
        # Enhanced source filtering with domain info
        seen_sources = set()
        unique_sources = []
        for chunk in retrieved_chunks:
            source_doc = chunk.get('metadata', {}).get('document_name')
            if source_doc and source_doc not in seen_sources:
                metadata = chunk['metadata'].copy()
                metadata['query_domain'] = query_domain  # Add domain context to metadata
                unique_sources.append(metadata)
                seen_sources.add(source_doc)
        
        return {
            "answer": final_answer,
            "sources": unique_sources,
            "query_domain": query_domain  # Include domain in response
        }