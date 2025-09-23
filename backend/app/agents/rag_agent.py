# backend/app/agents/rag_agent.py (Enhanced query classification)

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

    def _classify_query_domain(self, query: str) -> str:
        """
        Uses LLM to intelligently classify query domain with high accuracy.
        """
        self.logger.info("Classifying query domain...")
        
        prompt = f"""
        You are a domain classification expert. Classify the following question as either "STEM" or "Non-STEM".

        **STEM includes:**
        - Computer Science (programming, algorithms, data structures, software engineering)
        - Cybersecurity (network security, threats, vulnerabilities, encryption, DDoS, malware)
        - Machine Learning & AI (neural networks, models, training, datasets, deep learning)
        - Mathematics, Engineering, Physics, Chemistry, Biology
        - Technology, Systems, Networks, Protocols
        - Data Science, Statistics, Analytics

        **Non-STEM includes:**
        - History, Literature, Philosophy, Arts
        - Social Sciences, Psychology, Sociology
        - Business, Economics, Management
        - Languages, Cultural Studies
        - Institutional history, traditions, achievements

        **Examples:**
        - "What is a DDoS attack?" → STEM (Cybersecurity)
        - "How do neural networks work?" → STEM (Machine Learning)
        - "What algorithms are used for sorting?" → STEM (Computer Science)
        - "What are the firsts that University of Missouri is known for?" → Non-STEM (Institutional History)
        - "Who wrote Romeo and Juliet?" → Non-STEM (Literature)

        **Question:** "{query}"

        **Classification:** Respond with ONLY "STEM" or "Non-STEM"
        """
        
        response = self.llm_interface.generate_response(prompt).strip().upper()
        
        # Parse the response to extract the classification
        if "STEM" in response and "NON-STEM" not in response:
            classification = "STEM"
        elif "NON-STEM" in response:
            classification = "Non-STEM"
        else:
            # Fallback to keyword-based classification
            self.logger.warning(f"LLM classification unclear: '{response}'. Using fallback.")
            classification = self._fallback_query_classification(query)
        
        self.logger.info(f"Query classified as: {classification}")
        return classification

    def _fallback_query_classification(self, query: str) -> str:
        """
        Fallback keyword-based classification with enhanced STEM detection.
        """
        stem_indicators = [
            # Computer Science
            'algorithm', 'programming', 'software', 'code', 'coding', 'python', 'java', 'javascript',
            'data structure', 'database', 'API', 'framework', 'object-oriented', 'functional programming',
            
            # Cybersecurity 
            'security', 'cybersecurity', 'cyber security', 'encryption', 'vulnerability', 'attack', 
            'DDoS', 'malware', 'firewall', 'penetration', 'hacking', 'threat', 'defense',
            'network security', 'information security', 'cyber attack', 'cyber defense',
            
            # Machine Learning & AI
            'machine learning', 'neural network', 'deep learning', 'AI', 'artificial intelligence',
            'model', 'training', 'dataset', 'classification', 'regression', 'supervised learning',
            'unsupervised learning', 'tensorflow', 'pytorch', 'data science', 'analytics',
            
            # General Tech & Engineering
            'system', 'network', 'server', 'protocol', 'technical', 'implementation',
            'engineering', 'mathematics', 'statistics', 'physics', 'chemistry', 'biology',
            'scientific method', 'experiment', 'hypothesis', 'theory', 'formula',
            
            # Interview/Technical Questions
            'interview questions', 'technical interview', 'coding interview', 'programming interview'
        ]
        
        non_stem_indicators = [
            'history', 'historical', 'literature', 'author', 'novel', 'poem', 'philosophy',
            'culture', 'tradition', 'society', 'social', 'psychology', 'sociology',
            'university founded', 'college established', 'homecoming', 'first university',
            'institutional history', 'cultural significance', 'artistic movement',
            'political', 'economic theory', 'business strategy', 'management',
            'language', 'linguistics', 'anthropology', 'archaeology'
        ]
        
        query_lower = query.lower()
        
        stem_score = sum(1 for indicator in stem_indicators if indicator in query_lower)
        non_stem_score = sum(1 for indicator in non_stem_indicators if indicator in query_lower)
        
        # Enhanced scoring with multi-word phrase bonuses
        if any(phrase in query_lower for phrase in ['machine learning', 'neural network', 'deep learning', 'data science']):
            stem_score += 3
        if any(phrase in query_lower for phrase in ['cyber security', 'network security', 'ddos attack']):
            stem_score += 3
        if any(phrase in query_lower for phrase in ['university founded', 'college established', 'institutional history']):
            non_stem_score += 3
            
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
            - Technical terms and concepts (algorithms, systems, protocols, attacks, defenses)
            - Technology names (programming languages, frameworks, tools, platforms)
            - Research areas (machine learning, cybersecurity, computer science, data science)
            - Performance metrics and methods
            - Technical organizations and standards
            - Security concepts (DDoS, malware, encryption, vulnerabilities)
            - ML concepts (neural networks, models, training, datasets)
            """
            
            related_terms = """
            Include related technical concepts like:
            - For "security": vulnerability, threat, attack, defense, encryption, malware, DDoS
            - For "machine learning": model, training, dataset, algorithm, neural network, AI
            - For "programming": code, software, language, framework, library, algorithm
            - For "interview": technical questions, coding problems, system design
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
        Multi-domain hybrid retrieval that works regardless of document classification.
        """
        entities = self._extract_entities_from_query(query, query_domain)
        
        # Knowledge graph retrieval (searches all documents regardless of their classification)
        kg_chunks = self.graph_db.get_context_for_entities(entities)
        
        # Domain-specific broader entity search
        broader_entities = []
        if query_domain == "STEM":
            broader_entities = [
                "technology", "system", "algorithm", "security", "machine learning", 
                "computer science", "cybersecurity", "programming", "software", 
                "neural network", "data", "model", "network", "attack", "encryption"
            ]
        else:
            broader_entities = [
                "university", "institution", "history", "tradition", "first", 
                "established", "founded", "college", "academic"
            ]
        
        # Check if query mentions specific institutions or topics
        query_lower = query.lower()
        if any(term in query_lower for term in ["university", "missouri", "mizzou"]):
            broader_entities.extend(["University of Missouri", "Mizzou", "Missouri"])
        
        main_entity_chunks = self.graph_db.get_context_for_entities(broader_entities)

        # Vector search with domain context (searches all documents)
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
                expanded_query = f"{embedding_query} technical system implementation algorithm method cybersecurity machine learning programming"
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
        if query_domain == "STEM":
            if any(term in query_lower for term in ["ddos", "cyber", "security", "machine learning", "algorithm"]):
                search_terms = ["DDoS", "cybersecurity", "security", "algorithm", "machine learning", "neural network"]
                text_search_chunks = self.graph_db.search_chunks_by_text_content(search_terms)
        else:
            if any(term in query_lower for term in ["first", "tradition", "homecoming", "founded"]):
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
            1. **Technical Accuracy:** Extract precise technical details, specifications, and methodologies
            2. **Problem-Solution Mapping:** Connect technical problems to their solutions and implementations
            3. **Security Analysis:** For cybersecurity topics, identify threats, vulnerabilities, and countermeasures
            4. **ML/AI Details:** For machine learning, extract model types, training approaches, performance metrics
            5. **System Architecture:** Identify system components, relationships, and technical workflows
            6. **Code and Implementation:** Look for programming concepts, algorithms, and technical procedures
            """
        else:
            domain_instructions = """
            **Non-STEM Specific Instructions:**
            1. **Historical Inference:** "Started" = "First", "Founded" = "First established", "Pioneered" = "First to do"
            2. **Chronological Significance:** Look for temporal firsts and historical precedence
            3. **Institutional Achievements:** Extract university/organizational accomplishments and milestones
            4. **Traditional Elements:** Identify customs, practices, and cultural significance
            5. **Legacy and Impact:** Consider long-term influence and historical importance
            """

        prompt = f"""
        You are analyzing {query_domain} content to answer the user's question. Extract ALL relevant facts with appropriate domain expertise.

        {domain_instructions}

        **Universal Requirements:**
        - Extract EVERY piece of information relevant to the question
        - Make logical inferences based on context and domain knowledge
        - Look for both direct statements and implied information
        - Consider relationships, dependencies, and connections between concepts

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
        Domain-aware final answer generation with future multimedia support preparation.
        """
        self.logger.info(f"Generating final {query_domain} answer...")
        
        if query_domain == "STEM":
            answer_style = """
            **STEM Answer Style:**
            - Provide technical accuracy and precision
            - Include relevant technical details, specifications, and methodologies
            - Explain step-by-step processes and implementations where applicable
            - Use appropriate technical terminology with clear explanations
            - Structure information logically (concept → implementation → examples/results)
            - Prepare for future multimedia: Note where diagrams, code examples, or visual demonstrations would be helpful
            """
        else:
            answer_style = """
            **Non-STEM Answer Style:**
            - Provide comprehensive historical and institutional context
            - Present information chronologically or thematically when relevant
            - Include cultural significance and broader implications
            - Use clear, educational language accessible to students
            - Structure as a complete narrative or well-organized presentation
            """

        prompt = f"""
        You are providing a comprehensive {query_domain} answer based on the analyzed facts below.

        {answer_style}

        **Instructions:**
        1. Synthesize ALL facts into a complete, well-structured answer
        2. Present information clearly and comprehensively
        3. Include all relevant details found in the analysis
        4. Use appropriate domain-specific language and context
        5. Provide educational value and depth appropriate for the domain
        6. Do NOT invent information beyond the provided facts

        **User's Question:** {query}

        **Analyzed Facts:** 
        ---
        {reasoned_context}
        ---

        **Your Complete {query_domain} Answer:**
        """
        
        return self.llm_interface.generate_response(prompt)

    def run(self, query: str, user_role: str = 'student', socratic: bool = False, topic_class: str = "Auto") -> Dict[str, Any]:
        """
        Enhanced query-based multi-domain RAG pipeline.
        """
        # Use intelligent query-based classification
        if topic_class == "Auto" or topic_class not in ["STEM", "Non-STEM"]:
            query_domain = self._classify_query_domain(query)
        else:
            query_domain = topic_class
        
        self.logger.info(f"RAG Agent processing {query_domain} query: '{query}' (Socratic: {socratic})")
        
        # Enhanced retrieval that searches all documents regardless of their classification
        top_k_for_retrieval = 15  # Increased for better coverage across misclassified documents
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
                metadata['query_domain'] = query_domain
                unique_sources.append(metadata)
                seen_sources.add(source_doc)
        
        return {
            "answer": final_answer,
            "sources": unique_sources,
            "query_domain": query_domain,
            "multimedia_ready": query_domain == "STEM"  # Flag for future multimedia features
        }