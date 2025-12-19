# backend/app/agents/cot_rag_agent.py

import logging
import json
import asyncio 
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
        """
        prereqs = []
        for entity in initial_entities:
            # FIX: Removed ':Concept' label restriction. Now matches ANY node.
            cypher = """
            MATCH (target) 
            WHERE (toLower(target.name) CONTAINS toLower($name) 
               OR toLower($name) CONTAINS toLower(target.name))
               AND target:Concept OR target:Control_Flow OR target:Data_Structure OR target:Core_Concept 
               // Or simpler: just match (target) where we define the relationship
            MATCH (target)-[:REQUIRES_UNDERSTANDING_OF]->(req)
            RETURN req.name as name
            """
            
            # Let's use the cleanest version (No label restriction, relies on relationship):
            cypher = """
            MATCH (target)
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
        # return text
        return text.replace("(", " ").replace(")", " ")

    def _expand_query_using_graph(self, query: str, initial_entities: List[str]) -> List[str]:
        expanded_terms = []
        for entity in initial_entities:
            cypher = """
            MATCH (target) WHERE toLower(target.name) CONTAINS toLower($name) 
            MATCH (target)-[:REQUIRES_UNDERSTANDING_OF]->(req)
            RETURN req.name as name
            """
            results = self.graph_db.execute_query(cypher, {"name": entity})
            for record in results:
                expanded_terms.append(record['name'])
        return list(set(expanded_terms))

    def _execute_retrieval(self, query: str, intent: str, user_role: str = 'student') -> List[Dict[str, Any]]:
        extract_prompt = f"Extract C terms: '{query}'. Return CSV."
        entities_str = self.llm_interface.generate_response(extract_prompt)
        entities = [e.strip() for e in entities_str.split(',') if e.strip()]
        
        # Graph Expansion
        related_terms = self._expand_query_using_graph(query, entities)
        
        # Vector Search
        query_embedding = self.llm_interface.get_embedding(query)
        raw_chunks = self.vector_store.query(query_embedding, top_k=8)
        
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
            if chunk.get('score', 0) <= SCORE_THRESHOLD: continue
            
            # RBAC
            if user_role == 'student' and meta.get('access_level') == 'teacher': continue
            
            # ABAC
            topic = meta.get('topic', 'General')
            if not topic_settings.get(topic, True): continue
            
            valid_chunks.append(chunk)
            
        return valid_chunks

    def _generate_suggestions(self, query: str, context: str) -> List[str]:
        prompt = f"""
        Based on the student's query and the Reference Material below, generate 3 short follow-up options.
        
        Query: "{query}"
        # Answer: (Removed to allow parallel generation)
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
            return json.loads(response.replace("```json", "").replace("```", "").strip())
        except:
            return ["Tell me more", "Example code", "Challenge: Write it"]

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
        # return self.llm_interface.generate_response(prompt)
        return self.llm_interface.generate_response(f"Review C code: {query} using context: {context}")

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
        # return self.llm_interface.generate_response(prompt)
        return self.llm_interface.generate_response(f"Socratic plan for {query} using {context}")
    
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
        # return self.llm_interface.generate_response(prompt)
        return self.llm_interface.generate_response(f"Explain {query} using {context}. Include Mermaid diagram.")

    def _build_concept_prompt(self, query: str, context: str, user_goal: str = None) -> str:
        goal_section = ""
        if user_goal:
            goal_section = f"""
            ## Connection to Your Goal
            Explain explicitly how this concept helps achieve the goal: "{user_goal}".
            """

        return f"""
        You are an expert C Programming Tutor.
        
        Student Query: "{query}"
        User's Goal: "{user_goal if user_goal else 'None'}"
        Reference Material: {context}

        **MANDATORY RULES:**
        1. **STRICT LIMITATION:** Check the Reference Material. If concept is missing, say "I don't have information..."
        2. **TEXT PRIORITY:** Clear text explanation FIRST (min 3 sentences).
        3. **VISUALIZATION (MANDATORY):** You MUST synthesize a Mermaid.js diagram (`graph TD`) to visualize the concept (e.g. data flow, memory layout, or logic), even if the text doesn't explicitly describe a diagram.
           - **SANITIZATION:** No `()`, `[]`, or `"` in node labels. Use single quotes if needed.
        4. **SOURCE GROUNDING:** Quote specific examples from text.
        5. **GOAL ALIGNMENT:** If the user has a goal, you MUST explain how this concept applies to it.

        **STRICT RESPONSE FORMAT:**
        
        ## Explanation
        [Detailed text explanation...]
        {goal_section}
        ## Visual Model
        ```mermaid
        graph TD
           ...
        ```
        
        ## Example from Class
        [Reference specific code from text]
        """

    def _build_socratic_prompt(self, query: str, context: str, user_goal: str = None) -> str:
        goal_instruction = ""
        if user_goal:
            goal_instruction = f"6. **GOAL ALIGNMENT:** The student's goal is: '{user_goal}'. Mention if this helps them."

        return f"""
        You are an encouraging C Programming Tutor for junior students.
        
        Student Query: "{query}"
        Reference Material: {context}

        **CRITICAL RULES:**
        1. **CONCEPT LIMITATION:** You may ONLY teach concepts present in the Reference Material.
        2. **SOURCE GROUNDING:** Mention specific variable names/examples from the text.
        3. **TEXT FIRST:** Text explanation MUST come before any diagrams.
        4. **VISUALIZATION:** If appropriate, include a Mermaid diagram.
           - **SANITIZATION:** No `()`, `[]`, or `"` in node labels.
        5. **TONE INSTRUCTIONS:** Speak DIRECTLY to the student. Use "You" and "We".
        {goal_instruction}

        Format your response like this:
        
        ## Strategy
        [Text Explanation]

        ## Visual Logic
        ```mermaid
        graph TD
           A[Start] --> B[End]
        ```
        
        ## Implementation Plan
        1. **[Step Name]**: ...
           ```c
           // Generic Syntax
           code...
           ```
        
        ## Guiding Question
        ...
        """

    def _build_review_prompt(self, query: str, context: str, user_goal: str = None) -> str:
        return f"""
        You are a supportive C Code Reviewer.
        
        Student's Input: {query}
        Reference Material: {context}

        **RULES:**
        1. **CHECK CONTEXT:** Look for a "[CONTEXT: ...]" tag.
        2. **SANDWICH METHOD:** Positive -> Improvement -> Hint.
        3. **SOURCE GROUNDING:** Use variable names from Reference Material.
        4. **NO SOLUTIONS:** Do not rewrite code.
        
        Format:
        ## Code Review
        **✅ What looks good:** ...
        **⚠️ What needs work:** ...
        **💡 Hint:** ...
        """

    def run(self, query: str, user_role: str = 'student', **kwargs) -> Dict[str, Any]:
        import time
        
        # --- TIMER START ---
        t_start = time.time()
        profiler = {}
        
        self.logger.info(f"Processing Query: {query}")
        username = kwargs.get('username', 'anonymous')
        user_goal = kwargs.get('user_goal')
        
        # Initialize Causal Flags
        causal_flags = {
            "treatment_diagram": False,
            "treatment_prereq_check": False,
            "treatment_code_review": False,
            "treatment_topic_block": False,
            "context_mastery_score": 0,
            "context_has_goal": bool(user_goal)
        }
        
        # 1. Intent Classification
        # --- UPDATE STATUS ---
        yield {"type": "status", "message": "Analyzing intent...", "percent": 10}
        t0 = time.time()
        intent = self._classify_intent(query)
        profiler["1_Intent_Class"] = time.time() - t0
        self.logger.info(f"Intent Classified: {intent}")
        
        # 2. Entity Extraction
        t0 = time.time()
        extract_prompt = f"Extract the main C programming terms from: '{query}'. Return as comma-separated list."
        entities_str = self.llm_interface.generate_response(extract_prompt)
        entities = [e.strip() for e in entities_str.split(',') if e.strip()]
        profiler["2_Entity_Extract"] = time.time() - t0

        # --- GATEKEEPER CHECK (Logic only) ---
        from app.core.settings_manager import settings_manager
        topic_settings = settings_manager.get_settings()
        
        blocked = False
        blocked_topic_name = ""
        for entity in entities:
            entity_lower = entity.lower()
            for setting_topic, is_enabled in topic_settings.items():
                t_lower = setting_topic.lower()
                if (entity_lower in t_lower or t_lower in entity_lower) and not is_enabled:
                    blocked = True
                    blocked_topic_name = setting_topic
                    break
            if blocked: break
        
        if blocked:
             causal_flags["treatment_topic_block"] = True
             return {
                "answer": f"🔒 **Topic Locked**\n\nThe topic **{blocked_topic_name}** is currently not available.",
                "sources": [],
                "intent": intent,
                "suggestions": ["Ask about a different topic"],
                "cot_analysis": None,
                "reasoning_quality": 0.0,
                "causal_flags": causal_flags
            }

        # --- LEARNING UPDATE (Logic only) ---
        if "i know" in query.lower():
            # If the user says "I know them" or "I know variables", 
            # we need to credit them for the PREREQUISITES of the current topic.
            reqs = self._check_prerequisites(query, entities)
            for req in reqs:
                knowledge_manager.mark_concept_as_known(username, req)
                self.logger.info(f"🧠 Learned that {username} knows prerequisite: {req}")
            
            if "teach me" not in query.lower():
                 for entity in entities:
                    knowledge_manager.mark_concept_as_known(username, entity)

        # 3. Prerequisite Check (Graph DB)
        t0 = time.time()
        should_check_prereqs = "anyway" not in query.lower() and "skip" not in query.lower() and "know" not in query.lower()

        if (intent == "CONCEPT" or intent == "PROBLEM") and should_check_prereqs:
            all_prereqs = self._check_prerequisites(query, entities)
            unknown_prereqs = []
            for p in all_prereqs:
                if p.lower() in [e.lower() for e in entities]: continue
                if not knowledge_manager.has_mastered(username, p):
                    unknown_prereqs.append(p)

            if unknown_prereqs:
                causal_flags["treatment_prereq_check"] = True
                prereq_str = "**" + "**, **".join(unknown_prereqs) + "**"
                suggestion_buttons = [f"Explain {p} first" for p in unknown_prereqs]
                suggestion_buttons.append(f"I know them, teach me {entities[0]} anyway")

                return {
                    "answer": f"## 🛑 Hold on!\n\nTo understand **{entities[0]}**, you really need to know: {prereq_str} first.\n\nSince I don't have a record of you learning them yet, I recommend we start there.",
                    "sources": [],
                    "intent": "GUIDANCE",
                    "suggestions": suggestion_buttons, 
                    "cot_analysis": None,
                    "reasoning_quality": 1.0,
                    "causal_flags": causal_flags
                }
        profiler["3_Graph_Check"] = time.time() - t0

        # 4. Retrieval (Vector DB + Logic)
        t0 = time.time()
        search_query = query
        if len(query.split()) > 5 and entities:
            search_query = f"{' '.join(entities)} in C programming"
            
        retrieved_chunks = self._execute_retrieval(search_query, intent, user_role)
        profiler["4_Retrieval"] = time.time() - t0
        
        if not retrieved_chunks:
             return {
                "answer": f"I'm sorry, but I don't have any information about **{entities[0] if entities else query}**.",
                "sources": [],
                "intent": intent,
                "suggestions": ["Ask about Arrays", "Ask about Loops"],
                "cot_analysis": None,
                "reasoning_quality": 0.0 
            }

        # Context Building
        yield {"type": "status", "message": "Reading documents...", "percent": 75}
        known_concepts = knowledge_manager.get_known_concepts(username)
        user_context_str = ""
        if known_concepts:
            user_context_str = f"USER CONTEXT: The student already knows: {', '.join(known_concepts)}."

        context_text = f"{user_context_str}\n\n"
        for c in retrieved_chunks:
            source = c.get('metadata', {}).get('document_name', 'Unknown')
            text = c.get('text', '')
            context_text += f"--- Source: {source} ---\n{text}\n\n"
        
        # 5. Answer Generation (LLM)
        yield {"type": "status", "message": "Drafting response...", "percent": 90}
        t0 = time.time()
        
        if intent == "REVIEW":
            causal_flags["treatment_code_review"] = True
            final_answer = self._generate_code_review(query, context_text, user_goal)
        elif intent == "PROBLEM" or intent == "DEBUG":
            final_answer = self._generate_socratic_plan(query, context_text, user_goal)
        else:
            final_answer = self._generate_concept_explanation(query, context_text, user_goal)
        
        if "```mermaid" in final_answer:
            causal_flags["treatment_diagram"] = True
            
        profiler["5_Answer_Gen"] = time.time() - t0

        # 6. Suggestion Generation (LLM)
        t0 = time.time()
        suggestions = self._generate_suggestions(query, final_answer, context_text)
        profiler["6_Suggest_Gen"] = time.time() - t0
        
        # Cleanup
        final_answer = self._sanitize_mermaid(final_answer)

        formatted_sources = []
        for c in retrieved_chunks:
            source_data = c.get('metadata', {}).copy()
            source_data['chunk_text'] = c.get('text') or c.get('chunk_text') or 'Text missing'
            formatted_sources.append(source_data)

        # --- TIMER END & LOGGING ---
        total_time = time.time() - t_start
        
        print("\n" + "="*40)
        print(f"⏱️  LATENCY PROFILE ({total_time:.2f}s total)")
        print("-" * 40)
        for step, duration in profiler.items():
            pct = (duration / total_time) * 100
            bar = "█" * int(pct / 5)
            print(f"{step:<20} : {duration:.2f}s ({pct:.0f}%) {bar}")
        print("="*40 + "\n")
        # ---------------------------

        return {
            "answer": final_answer,
            "sources": formatted_sources,
            "intent": intent,
            "suggestions": suggestions,
            "causal_flags": causal_flags,
            "cot_analysis": None,
            "reasoning_quality": 1.0 
        }

    # --- ASYNC STREAMING METHOD ---
    async def run_stream(self, query: str, user_role: str = 'student', **kwargs):
        import time
        t_start = time.time()
        profiler = {}
        
        username = kwargs.get('username', 'anonymous')
        user_goal = kwargs.get('user_goal')
        
        # =========================================================
        # 1 & 2. PARALLEL ANALYSIS (Intent + Entity)
        # =========================================================
        yield {"type": "status", "message": "Analyzing query...", "percent": 10}
        t0 = time.time()

        # FIX: Define as regular functions (synchronous wrappers)
        def get_intent():
            # This calls the synchronous method self._classify_intent
            return self._classify_intent(query)

        def get_entities():
            # This calls the synchronous method self.llm_interface.generate_response
            prompt = f"Extract the main C programming terms from: '{query}'. Return as comma-separated list."
            return self.llm_interface.generate_response(prompt)

        # Launch both tasks simultaneously in threads
        task_intent = asyncio.create_task(asyncio.to_thread(get_intent))
        task_entities = asyncio.create_task(asyncio.to_thread(get_entities))

        # Wait for both
        intent, entities_str = await asyncio.gather(task_intent, task_entities)
        
        # Process results
        entities = [e.strip() for e in entities_str.split(',') if e.strip()]
        
        profiler["1+2_Analysis"] = time.time() - t0 # Combined time
        self.logger.info(f"Intent: {intent}, Entities: {entities}")

        # =========================================================
        # GATEKEEPER CHECK (Topic Visibility)
        # =========================================================
        from app.core.settings_manager import settings_manager
        topic_settings = settings_manager.get_settings()
        for entity in entities:
            for t, on in topic_settings.items():
                if (entity.lower() in t.lower()) and not on:
                    msg = f"🔒 Topic **{t}** is locked."
                    yield {"type": "complete", "data": {"answer": msg, "sources": [], "suggestions": [], "intent": intent}}
                    return

        # =========================================================
        # 3. PREREQUISITE CHECK
        # =========================================================
        yield {"type": "status", "message": "Checking prerequisites...", "percent": 40}
        
        # Learning Update
        if "i know" in query.lower():
            for entity in entities: knowledge_manager.mark_concept_as_known(username, entity)

        # Graph Logic
        should_check = "anyway" not in query.lower() and "skip" not in query.lower()
        if (intent == "CONCEPT" or intent == "PROBLEM") and should_check:
            t0 = time.time()
            prereqs = self._check_prerequisites(query, entities)
            unknown = [p for p in prereqs if not knowledge_manager.has_mastered(username, p)]
            profiler["3_Graph"] = time.time() - t0
            
            if unknown:
                msg = f"## 🛑 Hold on!\nYou need: {', '.join(unknown)} first."
                btns = [f"Explain {p}" for p in unknown] + [f"Teach me {entities[0]} anyway"]
                yield {"type": "complete", "data": {"answer": msg, "sources": [], "suggestions": btns, "intent": "GUIDANCE"}}
                return

        # =========================================================
        # 4. RETRIEVAL
        # =========================================================
        yield {"type": "status", "message": "Searching knowledge base...", "percent": 60}
        t0 = time.time()
        
        q_search = f"{' '.join(entities)} in C" if len(query.split()) > 5 else query
        chunks = self._execute_retrieval(q_search, intent, user_role)
        profiler["4_Retrieval"] = time.time() - t0
        
        if not chunks:
             msg = "I don't have info on that."
             yield {"type": "complete", "data": {"answer": msg, "sources": [], "suggestions": [], "intent": intent}}
             return

        # Context Building
        known_concepts = knowledge_manager.get_known_concepts(username)
        user_context_str = f"USER CONTEXT: The student already knows: {', '.join(known_concepts)}." if known_concepts else ""
        context_text = f"{user_context_str}\n\n"
        for c in chunks:
            context_text += f"--- Source: {c.get('metadata', {}).get('document_name')} ---\n{c.get('text')}\n\n"

        # =========================================================
        # 5 & 6. PARALLEL GENERATION (Answer + Suggestions)
        # =========================================================
        yield {"type": "status", "message": "Drafting response...", "percent": 80}
        t0 = time.time()

        # Task A: Start Suggestions in Background (Non-blocking)
        suggest_task = asyncio.create_task(
            asyncio.to_thread(self._generate_suggestions, query, context_text)
        )

        # Task B: Prepare Prompt
        prompt = ""
        if intent == "REVIEW": prompt = self._build_review_prompt(query, context_text, user_goal)
        elif intent == "PROBLEM": prompt = self._build_socratic_prompt(query, context_text, user_goal)
        else: prompt = self._build_concept_prompt(query, context_text, user_goal)

        # Task C: Stream Answer
        yield {"type": "status", "message": "Generating...", "percent": 100}
        full_answer = ""
        async for token in self.llm_interface.stream_response(prompt):
            full_answer += token
            yield {"type": "token", "text": token}

        # Wait for Suggestions to finish
        suggestions = await suggest_task
        profiler["5_Parallel_Gen"] = time.time() - t0
        
        # Cleanup
        full_answer = self._sanitize_mermaid(full_answer)
        
        formatted_sources = []
        for c in chunks:
            formatted_sources.append({'document_name': c['metadata']['document_name'], 'chunk_text': c['text']})

        # --- LATENCY LOGGING ---
        total_time = time.time() - t_start
        print("\n" + "="*40)
        print(f"⏱️  PARALLEL STREAM LATENCY ({total_time:.2f}s total)")
        print("-" * 40)
        for step, duration in profiler.items():
            pct = (duration / total_time) * 100
            bar = "█" * int(pct / 5)
            print(f"{step:<20} : {duration:.2f}s ({pct:.0f}%) {bar}")
        print("="*40 + "\n")
        # -----------------------

        yield {
            "type": "complete",
            "data": {
                "answer": full_answer,
                "sources": formatted_sources,
                "suggestions": suggestions,
                "intent": intent
            }
        }