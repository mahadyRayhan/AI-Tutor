# Standard RAG Agent
# backend/app/agents/socratic.py

import asyncio
import json
from typing import AsyncGenerator, Dict, Any, List 
from app.agents.base import BaseAgent
from app.agents.schema import AgentState
from app.db.vector_store import VectorStore
from app.db.graph_db import Neo4jGraphDB
from app.core.user_knowledge_manager import knowledge_manager

class SocraticTutorAgent(BaseAgent):
    """
    The Socratic Tutor Agent is the primary "Teacher" in the system.
    
    It is responsible for:
    1. Handling general concept questions (e.g., "What is a pointer?").
    2. Retrieval-Augmented Generation (RAG) using both Vector Search and Knowledge Graph.
    3. Generating Socratic explanations that guide students rather than just giving answers.
    4. Auto-marking concepts as "Known" when a student asks about them (passive learning tracking).
    """
    def __init__(self, llm, logger, vector_store: VectorStore, graph_db: Neo4jGraphDB):
        super().__init__(llm, logger)
        self.vector_store = vector_store
        self.graph_db = graph_db
        self.llm_interface = llm

    async def process(self, state: AgentState) -> AsyncGenerator[dict, None]:
        """
        Main processing loop for the Socratic Agent.
        
        Args:
            state (AgentState): The current state of the agent workflow.
            
        Yields:
            dict: Workflow events.
        """
        # This is the "Catch-All" agent, so it runs if no one else stopped processing.
        
        yield {"type": "status", "message": "Searching knowledge base...", "percent": 60}
        
        # 1. Retrieval
        # Uses entities if available, otherwise query.
        # If query is long (>5 words), we use the entities string for better search results.
        q_search = f"{' '.join(state.entities)} in C" if len(state.query.split()) > 5 else state.query
        chunks = self._execute_retrieval(q_search, state.intent, state.user_role, state.entities)
        
        if not chunks:
             msg = f"I don't have specific info on {state.entities[0] if state.entities else 'that'}."
             yield {"type": "complete", "data": {"answer": msg, "sources": [], "intent": state.intent}}
             state.stop_processing = True
             return

        # 2. Build Context
        yield {"type": "status", "message": "Drafting response...", "percent": 80}
        
        # Add User Context (Known concepts) to help LLM personalize
        known = knowledge_manager.get_known_concepts(state.user_id)
        user_context = f"USER CONTEXT: Student knows: {', '.join(known)}." if known else ""
        context_text = f"{user_context}\n\n" + "\n".join([f"--- Source: {c['metadata']['document_name']} ---\n{c['text']}" for c in chunks])

        # 3. Generate Suggestions (Parallel)
        # We start this task early so it runs while the main answer is streaming.
        suggest_task = asyncio.create_task(asyncio.to_thread(self._generate_suggestions, state.query, context_text))

        # 4. Select Prompt based on Intent
        if state.intent == "DEBUG": 
            # Reuse Socratic Plan prompt for debugging logic as it encourages step-by-step thinking
            prompt = self._build_socratic_plan_prompt(state.query, context_text, state.user_goal)
        else: 
            # Default to Concept Explanation
            prompt = self._build_concept_prompt(state.query, context_text, state.user_goal, state.profile, state.original_query)

        # 5. Stream Answer
        yield {"type": "status", "message": "Generating...", "percent": 100}
        full_answer = ""
        async for token in self.llm.stream_response_async(prompt):
            full_answer += token
            yield {"type": "token", "text": token}

        # 6. Auto-Learn (If Concept)
        # If the user asked about a specific concept, we assume they are learning it now.
        if state.intent == "CONCEPT" and state.entities:
             knowledge_manager.mark_concept_as_known(state.user_id, state.entities[0])

        # 7. Finalize
        state.stop_processing = True
        
        yield {
            "type": "complete",
            "data": {
                "answer": full_answer, 
                "sources": [{'document_name': c['metadata']['document_name'], 'chunk_text': c['text']} for c in chunks],
                "suggestions": await suggest_task,
                "intent": state.intent,
                "entities": state.entities, # <--- ADD THIS
                "session_id": state.session_id
            }
        }

    # --- HELPERS (Moved from main file) ---
    
    def _expand_query_using_graph(self, query: str, initial_entities: List[str]) -> List[str]:
        """
        Uses Neo4j Knowledge Graph to find related concepts or prerequisites.
        Result is used to expand the vector search query.
        """
        expanded_terms = []
        if not initial_entities:
            return []
            
        for entity in initial_entities:
            # Query Neo4j for prerequisites or related concepts
            cypher = "MATCH (target) WHERE toLower(target.name) CONTAINS toLower($name) MATCH (target)-[:REQUIRES_UNDERSTANDING_OF]->(req) RETURN req.name as name"
            try:
                results = self.graph_db.execute_query(cypher, {"name": entity})
                for record in results: 
                    expanded_terms.append(record['name'])
            except Exception as e:
                # Log error but don't crash retrieval
                self.logger.warning(f"Graph expansion failed for {entity}: {e}")
                
        return list(set(expanded_terms))
        
    def _execute_retrieval(self, query: str, intent: str, user_role: str = 'student', existing_entities: List[str] = None) -> List[Dict[str, Any]]:
        """
        Executes the Hybrid Retrieval Strategy:
        1. Extract Entities (if not provided).
        2. Expand Query using Knowledge Graph (Graph Retrieval).
        3. Search Vector Database (Vector Retrieval).
        4. Apply Gatekeeping (Topic Locks & Access Levels).
        """
        if existing_entities:
            entities = existing_entities
        else:
            extract_prompt = f"Extract C terms: '{query}'. Return CSV."
            entities_str = self.llm_interface.generate_response(extract_prompt)
            entities = [e.strip() for e in entities_str.split(',') if e.strip()]
            
        # Graph Expansion
        related_terms = self._expand_query_using_graph(query, entities)
        
        # Vector Search (Primary)
        query_embedding = self.llm_interface.get_embedding(query)
        raw_chunks = self.vector_store.query(query_embedding, top_k=8)
        
        # Vector Search (Expanded)
        if related_terms:
            exp_emb = self.llm_interface.get_embedding(" ".join(related_terms))
            raw_chunks.extend(self.vector_store.query(exp_emb, top_k=3))
        
        # Gatekeeper
        from app.core.settings_manager import settings_manager
        topic_settings = settings_manager.get_settings()
        SCORE_THRESHOLD = 0.22 
        
        valid_chunks = []
        seen_ids = set()
        
        for chunk in raw_chunks:
            cid = chunk.get('metadata', {}).get('chunk_id')
            if cid in seen_ids: continue
            seen_ids.add(cid)
            
            meta = chunk.get('metadata', {})
            
            # Filter low relevance
            if chunk.get('score', 0) <= SCORE_THRESHOLD: continue
            
            # Filter Teacher-Only content
            if user_role == 'student' and meta.get('access_level') == 'teacher': continue
            
            # Filter Locked Topics
            topic = meta.get('topic', 'General')
            if not topic_settings.get(topic, True): continue # Skip if topic is disabled
            
            valid_chunks.append(chunk)
            
        return valid_chunks

    def _generate_suggestions(self, query: str, context: str) -> List[str]:
        """Generates 3 follow-up suggestions based on the context."""
        prompt = f"Based on the student's query and the Reference Material below, generate 3 short follow-up options.\nQuery: \"{query}\"\nReference Material: \"{context}\"\nRULES: 1. STRICT GROUNDING. 2. Option 1 (Curiosity). 3. Option 2 (Next Step). 4. Option 3 (Challenge).\nOUTPUT: Return ONLY a JSON list of 3 strings."
        response = self.llm_interface.generate_response(prompt)
        try: return json.loads(response.replace("```json", "").replace("```", "").strip())
        except: return ["Tell me more", "Example code", "Challenge: Write it"]

    # def _build_concept_prompt(self, query: str, context: str, user_goal: str = None, profile: Dict[str, Any] = {}) -> str:
    #     goal_section = ""
    #     if user_goal:
    #         goal_section = f"""
    #         6. **GOAL CONNECTION (CRITICAL):** The student's goal is: "{user_goal}". 
    #            - You MUST explicitly explain how the current concept helps them achieve "{user_goal}".
    #         """
    #     # Dynamic Style Injection
    #     style_instruction = "Standard academic tone."
    #     if profile.get("attention_span") == "short":
    #         style_instruction = "EXTREMELY CONCISE. Use bullet points. No paragraphs longer than 2 sentences. The user loses focus easily."
        
    #     if profile.get("preferred_modality") == "visual":
    #         style_instruction += " PRIORITY: Generate a Mermaid Diagram FIRST, then explain textually."
            
    #     if profile.get("frustration_level") == "high":
    #         style_instruction += " TONE: Highly encouraging, patient, and gentle. Validate their effort."

    #     return f"""
    #     You are an expert C Programming Tutor.
        
    #     Student Query: "{query}"
    #     User's Goal: "{user_goal if user_goal else 'None'}"
    #     Reference Material: {context}
    #     **ADAPTIVE STYLE INSTRUCTIONS:**{style_instruction}

    #     **MANDATORY RULES:**
    #     1. **STRICT LIMITATION:** Check the Reference Material. If the concept is NOT present, say: "I don't have information..."
    #     2. **PERSONALIZATION:** Acknowledge known concepts from USER CONTEXT.
    #     3. **TEXT PRIORITY:** Clear text explanation FIRST (min 3 sentences). Use analogies.
    #     4. **VISUALIZATION:** Generate a Mermaid.js diagram (`graph TD`).
    #        - **CRITICAL SYNTAX:** 
    #          - ABSOLUTELY NO PARENTHESES `()` inside node labels. 
    #          - ABSOLUTELY NO BRACKETS `[]` inside node labels.
    #          - ABSOLUTELY NO QUOTES `"` inside node labels.
    #          - **GOOD:** `A[Start] --> B[Declare Array]`
    #     5. **SOURCE GROUNDING:** Quote specific examples from text.
    #     6. **GOAL ALIGNMENT:** If the user has a goal, you MUST explain how this concept applies to it.
        
    #     **STRICT RESPONSE FORMAT:**
        
    #     ## Explanation
    #     [Start by bridging from known concepts if applicable. Then explain the new concept using text and analogies.]
        
    #     ## Use Cases
    #     [Explain WHEN and WHY this concept is used in real programming.]

    #     {goal_section}

    #     ## Visual Model
    #     ```mermaid
    #     graph TD
    #        A["Start"] --> B{{"Condition?"}}
    #        B -- "Yes" --> C["Action"]
    #        B -- "No" --> D["End"]
    #     ```
        
    #     ## Example from Class
    #     [Reference specific code from text]
    #     """

    def _build_concept_prompt(self, query: str, context: str, user_goal: str = None, profile: Dict[str, Any] = {}, original_query: str = "") -> str:
        goal_section = ""
        if user_goal:
            goal_section = f"\n## Connection to Your Goal\nExplain explicitly how this helps achieve: '{user_goal}'\n"

        # 1. Determine if the user is impatient OR explicitly asked for a short answer
        q_lower = original_query.lower()
        is_impatient = profile.get("attention_span") == "short" or any(w in q_lower for w in ["short", "brief", "just", "quick", "syntax only", "too long"])
        
        print(f"🕵️‍♂️ [SOCRATIC DEBUG] is_impatient: {is_impatient} | Original: '{original_query}'")

        # 2. Build the Format Block based on state
        if is_impatient:
            format_rules = """
            **STRICT RESPONSE FORMAT (CONCISE MODE):**
            [Provide the Code Syntax immediately]
            [Provide 1 sentence explaining the syntax]

            WARNING: Do NOT use the words "Explanation", "Use Cases", or "Visual Model". 
            WARNING: Do NOT output any markdown headers (##).
            """
            style_instruction = "TONE: Extremely concise, code-first, no fluff."
        else:
            format_rules = f"""
            **STRICT RESPONSE FORMAT (STANDARD MODE):**
            
            ## Explanation
            [Clear text explanation. Min 3 sentences. Use analogies.]
            
            ## Use Cases
            [Explain WHEN and WHY this concept is used in real programming.]
            
            {goal_section}
            
            ## Visual Model
            ```mermaid
            graph TD
               ...
            ```
            (CRITICAL SYNTAX: NO () [] or "" inside node labels. Use A[Label].)
            
            ## Example from Class
            [Reference specific code from text]

            ## Your Turn! (Micro-Challenge)
            [End your explanation by asking the student to write exactly ONE line of code based on what you just taught. Do not give them the answer.]
            Example: "Now it's your turn. How would you declare an integer variable named 'score' and set it to 100?"
            """
            style_instruction = "TONE: Standard academic tone, encouraging, structured."
            
            if profile.get("preferred_modality") == "visual":
                style_instruction += " PRIORITY: Focus heavily on the Mermaid Diagram and visual analogies."
            if profile.get("frustration_level") == "high":
                style_instruction += " TONE: Highly encouraging, patient, and gentle. Validate their effort."

        # 3. Assemble the final prompt
        return f"""
        You are an expert C Programming Tutor.
        
        Student Query: "{query}"
        Reference Material: {context}

        **ADAPTIVE STYLE INSTRUCTIONS:**
        {style_instruction}

        **MANDATORY RULES:**
        1. **STRICT LIMITATION:** Check the Reference Material. If the concept is NOT present, say: "I don't have information..."
        2. **PERSONALIZATION:** Acknowledge known concepts from USER CONTEXT.
        3. **SOURCE GROUNDING:** Quote specific examples from text.
        
        {format_rules}
        """

    def _build_socratic_plan_prompt(self, query: str, context: str, user_goal: str = None) -> str:
        goal_instruction = ""
        if user_goal:
            goal_instruction = f"""
            6. **CONNECT TO USER'S PROJECT:** The student's long-term goal is: "{user_goal}".
               - You MUST explicitly explain how this specific concept helps them achieve "{user_goal}".
            """

        return f"""
        You are an encouraging C Programming Tutor. You are talking DIRECTLY to a junior student.
        
        **YOUR TASK:** Help the student solve their problem: "{query}" using the Reference Material below.

        Reference Material: {context}

        **CRITICAL RULES:**
        1. **TONE:** Be active, encouraging, and direct. Use "You" and "We".
        2. **NO META-TALK:** Do NOT say "Here is a Socratic plan". Just start teaching!
        3. **CONCEPT LIMITATION:** Only use concepts found in the Reference Material.
        4. **SOURCE GROUNDING:** Mention specific variable names/examples from the text.
        5. **TEXT FIRST:** Text explanation MUST come before any diagrams.
        {goal_instruction}

        **STRICT RESPONSE FORMAT:**
        
        ## Strategy
        [Explain the concept enthusiastically. Connect it to their "{user_goal}" project immediately.]

        ## Visual Logic
        ```mermaid
        graph TD
           ...
        ```
        
        ## Implementation Plan
        1. **[Step Name]**: [Description]
           ```c
           // Generic Syntax
           code...
           ```
        
        ## Guiding Question
        [A thoughtful question to check their understanding]
        """