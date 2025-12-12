# backend/app/agents/cot_rag_agent.py

import logging
import json
import re
from typing import Dict, List, Any
from dataclasses import dataclass

from app.db.llm_interface import LLMInterface
from app.db.vector_store import VectorStore
from app.db.graph_db import Neo4jGraphDB
from app.core.settings_manager import settings_manager
from app.core.user_knowledge_manager import knowledge_manager

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
        prompt = f"""
        Classify this student query about C programming:
        
        1. REVIEW: The user has provided C code. Look for:
           - Semicolons at end of lines (;)
           - Brackets {{ }} or parentheses ()
           - C keywords (int, float, void, return, printf)
           - Or asking "Is this right?"
        2. CONCEPT: Asking "what is...", "explain...", "definition of...", "how does X work?"
        3. PROBLEM: Asking "how to...", "write a code...", "solve..."
        4. DEBUG: Asking "why is this error...", "fix this..."

        Query: "{query}"
        
        Respond with ONE word: CONCEPT, PROBLEM, DEBUG, or REVIEW.
        """
        return self.llm_interface.generate_response(prompt).strip().upper()
    
    def _check_prerequisites(self, query: str, initial_entities: List[str]) -> List[str]:
        """
        Queries the Graph to see if the requested topic has hard prerequisites.
        Uses fuzzy matching (CONTAINS) to handle singular/plural mismatch.
        """
        prereqs = []
        for entity in initial_entities:
            # Cypher: Find what this entity REQUIRES (Case insensitive fuzzy match)
            cypher = """
            MATCH (target:Concept)
            WHERE toLower(target.name) CONTAINS toLower($name) 
               OR toLower($name) CONTAINS toLower(target.name)
            MATCH (target)-[:REQUIRES_UNDERSTANDING_OF]->(req)
            RETURN req.name as name
            """
            results = self.graph_db.execute_query(cypher, {"name": entity})
            for record in results:
                prereqs.append(record['name'])
        return list(set(prereqs))

    def _sanitize_mermaid(self, text: str) -> str:
        """
        Simple, aggressive cleanup. 
        Replaces specific 'dangerous' characters globally, but ONLY inside mermaid blocks.
        """
        pattern = r"(```mermaid\s*)([\s\S]*?)(```)"
        
        def clean_block(match):
            header = match.group(1)
            content = match.group(2)
            footer = match.group(3)
            
            # Simple replace: Turn bad chars into spaces
            # This is safer than complex logic
            clean_content = content.replace("(", " ").replace(")", " ")
            clean_content = clean_content.replace("[", " ").replace("]", " ")
            clean_content = clean_content.replace('"', "'")
            
            # Restore the structural brackets for the graph definition
            # This is a bit hacky but works for 99% of LLM output:
            # We assume lines starting with a letter usually define a node like A[...]
            # The regex below looks for the start of a node definition and restores the outer brackets
            # But honestly, relying on the PROMPT (steps 1 & 2) is 10x better.
            
            return f"{header}{content}{footer}"

        # NOTE: If the prompt works, we don't even need to touch the content.
        # Let's try relying PURELY on the prompt first, as regexing graph syntax is risky.
        return text

    def _expand_query_using_graph(self, query: str, initial_entities: List[str]) -> List[str]:
        expanded_terms = []
        for entity in initial_entities:
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

    def _execute_retrieval(self, query: str, intent: str, user_role: str = 'student') -> List[Dict[str, Any]]:
        """
        Retrieves documents with Graph expansion, Vector search, and Strict Filtering (Gatekeeper).
        """
        # 1. Extract keywords
        extract_prompt = f"Extract the main C programming terms from: '{query}'. Return as comma-separated list."
        entities_str = self.llm_interface.generate_response(extract_prompt)
        entities = [e.strip() for e in entities_str.split(',') if e.strip()]
        
        self.logger.info(f"Extracted Entities: {entities}")

        # 2. Graph Expansion
        related_terms = self._expand_query_using_graph(query, entities)
        self.logger.info(f"Graph suggested related terms: {related_terms}")

        # 3. Vector Search
        query_embedding = self.llm_interface.get_embedding(query)
        # We fetch more candidates (top_k=8) because the Gatekeeper might filter some out
        raw_chunks = self.vector_store.query(query_embedding, top_k=8)

        # 4. Graph Related Search (Optional expansion)
        if related_terms:
            expanded_query = " ".join(related_terms)
            expanded_embedding = self.llm_interface.get_embedding(expanded_query)
            related_chunks = self.vector_store.query(expanded_embedding, top_k=3)
            raw_chunks.extend(related_chunks)

        # --- GATEKEEPER LOGIC (Filtering) ---
        valid_chunks = []
        # Load the current visibility settings from the dashboard manager
        topic_settings = settings_manager.get_settings()
        
        # Strictness level for vector match (0.0 to 1.0)
        # Higher = stricter (fewer hallucinations), Lower = more lenient
        SCORE_THRESHOLD = 0.28 

        seen_ids = set()

        for chunk in raw_chunks:
            # Deduplicate based on chunk ID
            chunk_id = chunk.get('metadata', {}).get('chunk_id')
            if chunk_id in seen_ids:
                continue
            seen_ids.add(chunk_id)

            meta = chunk.get('metadata', {})
            
            # CHECK A: Relevance Score
            # If the vector database says this match is weak, ignore it.
            if chunk.get('score', 0) <= SCORE_THRESHOLD:
                continue

            # CHECK B: Access Level (Teacher vs Student)
            # If the user is a 'student', they cannot see documents marked 'teacher'
            chunk_access = meta.get('access_level', 'student')
            if user_role == 'student' and chunk_access == 'teacher':
                self.logger.info(f"⛔ Access Denied: Student tried to access '{meta.get('document_name')}'")
                continue

            # CHECK C: Topic Visibility (Dashboard Toggle)
            # If the teacher turned off this topic in the dashboard, hide it.
            topic = meta.get('topic', 'General')
            # If topic is not in settings, default to True (Visible)
            is_visible = topic_settings.get(topic, True)
            
            if not is_visible:
                self.logger.info(f"🙈 Hidden Content: Topic '{topic}' is currently disabled.")
                continue

            # If it passes all checks, add it
            valid_chunks.append(chunk)
        
        if not valid_chunks:
            self.logger.warning(f"No relevant documents found after filtering (Role: {user_role}).")
            return []
            
        return valid_chunks

    def _generate_suggestions(self, query: str, answer: str, context: str) -> List[str]:
        prompt = f"""
        Based on the student's query and your answer, generate 3 short follow-up options.
        
        Query: "{query}"
        Answer: "{answer}"
        Reference Material: "{context}"
        
        **RULES:**
        1. **STRICT GROUNDING:** Do NOT suggest concepts unless they appear in the Reference Material.
        2. **Option 1 (Curiosity):** A relevant follow-up question.
        3. **Option 2 (Next Step):** The logical next step.
        4. **Option 3 (Challenge):** A specific "Mini-Challenge". Start with "Challenge: ".

        **OUTPUT:** Return ONLY a JSON list of 3 strings.
        """
        response = self.llm_interface.generate_response(prompt)
        try:
            cleaned = response.replace("```json", "").replace("```", "").strip()
            import json
            return json.loads(cleaned)
        except:
            return ["Tell me more", "Show an example", "Challenge: Try writing the code"]

    def _generate_code_review(self, query: str, context: str, user_goal: str = None) -> str:
        
        goal_prompt = ""
        if user_goal:
            goal_prompt = f"6. **GOAL CHECK:** Does this code show progress towards their goal: '{user_goal}'? If yes, mention it in the 'What looks good' section."
            
        prompt = f"""
        You are a supportive C Code Reviewer.
        
        Student's Input: {query}
        Reference Material: {context}

        **RULES:**
        1. **CHECK CONTEXT:** Look for a "[CONTEXT: ...]" tag.
        2. **SANDWICH METHOD:** Positive -> Improvement -> Hint.
        3. **SOURCE GROUNDING:** Use variable names from Reference Material.
        4. **NO SOLUTIONS:** Do not rewrite code.
        5. **CHECK LOGIC:** Look for common beginner mistakes.
        {goal_prompt}

        Format:
        ## Code Review
        **✅ What looks good:** ...
        **⚠️ What needs work:** ...
        **💡 Hint:** ...
        """
        return self.llm_interface.generate_response(prompt)

    def _generate_socratic_plan(self, query: str, context: str, user_goal: str = None) -> str:
        
        # Build dynamic instruction based on whether a goal exists
        goal_instruction = ""
        if user_goal:
            goal_instruction = f"6. **GOAL ALIGNMENT:** The student's current learning goal is: '{user_goal}'. If the topic of their query helps them reach that goal, explicitly mention it in the Strategy section to motivate them."

        prompt = f"""
        You are an encouraging C Programming Tutor for junior students.
        
        Student Query: "{query}"
        Reference Material: {context}

        **CRITICAL RULES:**
        1. **CONCEPT LIMITATION:** You may ONLY teach concepts present in the Reference Material.
           - If the answer is NOT in the Reference Material, state that you do not have information on it.
        2. **SOURCE GROUNDING:** Mention specific variable names/examples from the text.
        3. **TEXT FIRST:** Text explanation MUST come before any diagrams.
        4. **VISUALIZATION:** If appropriate, include a Mermaid diagram.
           - **CRITICAL SANITIZATION:** 
             - ABSOLUTELY NO PARENTHESES `()` inside node labels. 
             - ABSOLUTELY NO BRACKETS `[]` inside node labels.
             - ABSOLUTELY NO QUOTES `"` inside node labels.
             - **BAD:** `A[sum(a,b)]` or `B{{arr[i]}}`
             - **GOOD:** `A[sum a b]` or `B{{arr index i}}`
        5. **TONE INSTRUCTIONS:** Speak DIRECTLY to the student. Use "You" and "We".
        {goal_instruction}

        Format your response like this:
        
        ## Strategy
        [Text Explanation. Mention the Goal here if applicable.]

        ## Visual Logic
        ```mermaid
        graph TD
           A[Start] --> B[End]
        ```
        
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
    
    def _generate_concept_explanation(self, query: str, context: str, user_goal: str = None) -> str:
        print(f"DEBUG: Generating concept for goal: '{user_goal}'") # <--- ADD THIS
        # Dynamic Goal Instruction
        goal_section = ""
        if user_goal:
            goal_section = f"""
            6. **GOAL CONNECTION (CRITICAL):** The student's goal is: "{user_goal}". 
               - You MUST explicitly explain how the current concept helps them achieve "{user_goal}".
               - Example: If goal is "Master Games" and topic is "Structs", say: "Structs are essential for Games because they let you define Player stats like health and score."
            """
            
        
        prompt = f"""
        You are an expert C Programming Tutor.
        
        Student Query: "{query}"
        User's Goal: "{user_goal if user_goal else 'None'}"
        Reference Material: {context}

        **MANDATORY RULES:**
        1. **STRICT LIMITATION:** Check the Reference Material. If the concept (e.g., 'switch', 'crypto') is NOT present, you MUST say:
           "I don't have information about [Concept] in my current reference library."
        2. **PERSONALIZATION (NEW):** Check the "USER CONTEXT" at the top of the Reference Material.
           - If it lists concepts the user knows, **acknowledge them** in your first sentence.
           - Example: "Since you already mastered [Known Concept], understanding [New Concept] will be easier because..."
        3. **TEXT PRIORITY:** Clear text explanation FIRST (min 3 sentences). Use analogies.
        4. **VISUALIZATION:** Generate a Mermaid.js diagram (`graph TD`) if the concept involves flow/structure.
           - **CRITICAL SANITIZATION:** 
             - ABSOLUTELY NO PARENTHESES `()` inside node labels. 
             - ABSOLUTELY NO BRACKETS `[]` inside node labels (except the outer ones defining the node).
             - ABSOLUTELY NO QUOTES `"` inside node labels. Use single quotes `'` if needed.
             - **BAD:** `A[Function(int x)]` or `B{{x[0] > 5}}` or `C[Print "Hello"]`
             - **GOOD:** `A[Function int x]` or `B{{x at 0 is greater than 5}}` or `C[Print 'Hello']`
        5. **SOURCE GROUNDING:** Quote specific examples from text.
        6. **GOAL ALIGNMENT:** If the user has a goal, you MUST explain how this concept applies to it.
        
        **STRICT RESPONSE FORMAT:**
        
        ## Explanation
        [Start by bridging from known concepts if applicable. Then explain the new concept using text and analogies.]
        {goal_section}

        ## Visual Model
        ```mermaid
        graph TD
           A[Start] --> B{{Condition?}}
           B -- Yes --> C[Action]
           B -- No --> D[End]
        ```
        
        ## Example from Class
        [Reference specific code from text]
        """
        return self.llm_interface.generate_response(prompt)

    def run(self, query: str, user_role: str = 'student', **kwargs) -> Dict[str, Any]:
        self.logger.info(f"Processing Query: {query}")
        username = kwargs.get('username', 'anonymous')
        
        # 1. Intent & Entities
        intent = self._classify_intent(query)
        extract_prompt = f"Extract the main C programming terms from: '{query}'. Return as comma-separated list."
        entities_str = self.llm_interface.generate_response(extract_prompt)
        entities = [e.strip() for e in entities_str.split(',') if e.strip()]

        # ---------------------------------------------------------
        # CHECK 1: TOPIC VISIBILITY (The Gatekeeper)
        # ---------------------------------------------------------
        from app.core.settings_manager import settings_manager
        topic_settings = settings_manager.get_settings()
        
        blocked = False
        blocked_topic_name = ""
        
        for entity in entities:
            # Fuzzy match entity to settings keys
            for setting_topic, is_enabled in topic_settings.items():
                # e.g. If setting is "Functions" and entity is "function"
                if (entity.lower() in setting_topic.lower() or setting_topic.lower() in entity.lower()):
                    if not is_enabled:
                        blocked = True
                        blocked_topic_name = setting_topic
                        break
            if blocked: break
        
        if blocked:
             return {
                "answer": f"🔒 **Topic Locked**\n\nThe topic **{blocked_topic_name}** is currently not available. Please check with your instructor to unlock it.",
                "sources": [],
                "intent": intent,
                "suggestions": ["Ask about a different topic", "Check my Dashboard"],
                "cot_analysis": None,
                "reasoning_quality": 0.0 
            }

        # ---------------------------------------------------------
        # CHECK 2: LEARNING FROM INPUT
        # ---------------------------------------------------------
        if "i know" in query.lower():
            # If the user says "I know them" or "I know variables", 
            # we need to credit them for the PREREQUISITES of the current topic.
            
            # 1. Find what the topic requires
            # (We re-run the check to see what was likely missing)
            reqs = self._check_prerequisites(query, entities)
            
            for req in reqs:
                knowledge_manager.mark_concept_as_known(username, req)
                self.logger.info(f"🧠 Learned that {username} knows prerequisite: {req}")
            
            # Also mark the explicit entity if they said "I know Variables" specifically
            # But NOT if they said "Teach me Function anyway" (they don't know Function yet)
            if "teach me" not in query.lower():
                 for entity in entities:
                    knowledge_manager.mark_concept_as_known(username, entity)

        # ---------------------------------------------------------
        # CHECK 3: PREREQUISITES (The Conversation)
        # ---------------------------------------------------------
        # Skip logic: if user explicitly says "skip", "anyway", or "i know"
        should_check_prereqs = "anyway" not in query.lower() and "skip" not in query.lower() and "know" not in query.lower()

        if (intent == "CONCEPT" or intent == "PROBLEM") and should_check_prereqs:
            all_prereqs = self._check_prerequisites(query, entities)
            
            unknown_prereqs = []
            for p in all_prereqs:
                # Ignore self-loops (e.g. Array requires Array)
                if p.lower() in [e.lower() for e in entities]: continue
                # Check User Knowledge DB
                if not knowledge_manager.has_mastered(username, p):
                    unknown_prereqs.append(p)

            if unknown_prereqs:
                # Create a nice list string: "Variables, Control Flow"
                prereq_str = "**" + "**, **".join(unknown_prereqs) + "**"
                
                # --- FIX: GENERATE BUTTONS FOR ALL MISSING ITEMS ---
                suggestion_buttons = []
                for p in unknown_prereqs:
                    suggestion_buttons.append(f"Explain {p} first")
                
                # Add the "Skip" button at the end
                suggestion_buttons.append(f"I know them, teach me {entities[0]} anyway")
                # ---------------------------------------------------

                return {
                    "answer": f"## 🛑 Hold on!\n\nTo understand **{entities[0]}**, you really need to know: {prereq_str} first.\n\nSince I don't have a record of you learning them yet, I recommend we start there.",
                    "sources": [],
                    "intent": "GUIDANCE",
                    "suggestions": suggestion_buttons, 
                    "cot_analysis": None,
                    "reasoning_quality": 1.0
                }

        # ---------------------------------------------------------
        # 4. STANDARD RETRIEVAL & GENERATION
        # ---------------------------------------------------------
        search_query = query
        if len(query.split()) > 5 and entities:
            # "I know them, teach me function anyway" -> "function"
            search_query = " ".join(entities)
            
        retrieved_chunks = self._execute_retrieval(search_query, intent, user_role)
                
        if not retrieved_chunks:
             return {
                "answer": f"I'm sorry, but I don't have any information about **{entities[0] if entities else query}** in my current reference library.",
                "sources": [],
                "intent": intent,
                "suggestions": ["Ask about Arrays", "Ask about Loops", "Ask about Variables"],
                "cot_analysis": None,
                "reasoning_quality": 0.0 
            }

        # Context Injection (Personalization)
        known_concepts = knowledge_manager.get_known_concepts(username)
        user_context_str = ""
        if known_concepts:
            user_context_str = f"USER CONTEXT: The student already knows: {', '.join(known_concepts)}."

        context_text = f"{user_context_str}\n\n"
        for c in retrieved_chunks:
            source = c.get('metadata', {}).get('document_name', 'Unknown')
            text = c.get('text', '')
            context_text += f"--- Source: {source} ---\n{text}\n\n"
        
        # Generation Logic
        user_goal = kwargs.get('user_goal')
        print("DEBUG: User Goal in run():", user_goal)  # <--- ADD THIS
        
        if intent == "REVIEW":
            final_answer = self._generate_code_review(query, context_text, user_goal)
        elif intent == "PROBLEM" or intent == "DEBUG":
            final_answer = self._generate_socratic_plan(query, context_text, user_goal)
        else:
            final_answer = self._generate_concept_explanation(query, context_text, user_goal)

        suggestions = self._generate_suggestions(query, final_answer, context_text)
        
        # Final Sanitization
        final_answer = self._sanitize_mermaid(final_answer)

        formatted_sources = []
        for c in retrieved_chunks:
            source_data = c.get('metadata', {}).copy()
            source_data['chunk_text'] = c.get('text') or c.get('chunk_text') or 'Text missing'
            formatted_sources.append(source_data)

        return {
            "answer": final_answer,
            "sources": formatted_sources,
            "intent": intent,
            "suggestions": suggestions,
            "cot_analysis": None,
            "reasoning_quality": 1.0 
        }