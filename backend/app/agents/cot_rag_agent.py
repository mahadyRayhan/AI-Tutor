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
from app.core.history_manager import history_manager
from app.core import config

import nltk
from nltk.corpus import stopwords
# Ensure resources are downloaded (do this once, maybe in __init__)
try:
    nltk.data.find('corpora/stopwords')
except LookupError:
    nltk.download('stopwords', quiet=True)

# Only import fast_classifier if mode is fast (optional, but cleaner)
if config.INTENT_CLASSIFIER_MODE == "fast":
    from app.core.fast_classifier import fast_classifier

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

    async def _contextualize_query(self, current_query: str, username: str, session_id: str) -> str:
        """
        Rewrites the query to include context.
        """
        if not session_id: return current_query

        session_data = history_manager.get_session_details(username, session_id)
        if not session_data or not session_data.get('messages'): return current_query

        # Get last 4 messages
        msgs = session_data['messages'][-4:] 
        history_str = ""
        for m in msgs:
            role = "Student" if m['role'] == 'user' else "Tutor"
            history_str += f"{role}: {m['content']}\n"

        # --- FIX: STRICTER CONTEXT PROMPT ---
        prompt = f"""
        Chat History:
        {history_str}
        
        Latest Student Question: {current_query}
        
        Task: Rewrite the "Latest Student Question" into a standalone sentence.
        
        CRITICAL RULES:
        1. If the student uses pronouns (it, this, that), refer back to the *STUDENT'S* previous topic, NOT the Tutor's technical explanation.
           - Example History: 
             Student: What is a loop? 
             Tutor: A loop is Control Flow...
             Student: What is its use case?
           - CORRECT Rewrite: "What is the use case of a loop?"
           - WRONG Rewrite: "What is the use case of Control Flow?"
        2. Keep the original intent exactly.
        3. Do NOT answer the question.
        
        Rewritten Question:
        """
        
        try:
            rewritten = await asyncio.to_thread(self.llm_interface.generate_response, prompt)
            rewritten = rewritten.strip().replace('"', '')
            self.logger.info(f"🔄 Contextualized: '{current_query}' -> '{rewritten}'")
            return rewritten
        except Exception as e:
            return current_query

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
        # Same as before
        prereqs = []
        for entity in initial_entities:
            cypher = "MATCH (target) WHERE toLower(target.name) CONTAINS toLower($name) OR toLower($name) CONTAINS toLower(target.name) MATCH (target)-[:REQUIRES_UNDERSTANDING_OF]->(req) RETURN req.name as name"
            results = self.graph_db.execute_query(cypher, {"name": entity})
            for record in results: prereqs.append(record['name'])
        return list(set(prereqs))

    def _sanitize_mermaid(self, text: str) -> str:
        """
        Fixes Mermaid syntax. 
        PREVIOUSLY: It stripped brackets (BAD).
        NOW: It only fixes quotes and ensures ID compliance.
        """
        # 1. Fix the specific error you saw: "Node'Label'" -> "Node['Label']"
        # This regex looks for IDs followed immediately by a single-quoted string
        text = re.sub(r"(\w+)'([^']+)'", r'\1["\2"]', text)

        # 2. Ensure text labels use double quotes inside brackets
        # Capture: ID[Text] -> ID["Text"] if quotes missing
        text = re.sub(r"(\w+)\[([^\"\]]+)\]", r'\1["\2"]', text)
        
        return text

    def _expand_query_using_graph(self, query: str, initial_entities: List[str]) -> List[str]:
        expanded_terms = []
        for entity in initial_entities:
            cypher = "MATCH (target) WHERE toLower(target.name) CONTAINS toLower($name) MATCH (target)-[:REQUIRES_UNDERSTANDING_OF]->(req) RETURN req.name as name"
            results = self.graph_db.execute_query(cypher, {"name": entity})
            for record in results: expanded_terms.append(record['name'])
        return list(set(expanded_terms))

    def _execute_retrieval(self, query: str, intent: str, user_role: str = 'student') -> List[Dict[str, Any]]:
        # Same as before
        extract_prompt = f"Extract C terms: '{query}'. Return CSV."
        entities_str = self.llm_interface.generate_response(extract_prompt)
        entities = [e.strip() for e in entities_str.split(',') if e.strip()]
        related_terms = self._expand_query_using_graph(query, entities)
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
            if user_role == 'student' and meta.get('access_level') == 'teacher': continue
            topic = meta.get('topic', 'General')
            if not topic_settings.get(topic, True): continue
            valid_chunks.append(chunk)
        return valid_chunks

    def _generate_suggestions(self, query: str, context: str) -> List[str]:
        # Same as before
        prompt = f"Based on the student's query and the Reference Material below, generate 3 short follow-up options.\nQuery: \"{query}\"\nReference Material: \"{context}\"\nRULES: 1. STRICT GROUNDING. 2. Option 1 (Curiosity). 3. Option 2 (Next Step). 4. Option 3 (Challenge).\nOUTPUT: Return ONLY a JSON list of 3 strings."
        response = self.llm_interface.generate_response(prompt)
        try: return json.loads(response.replace("```json", "").replace("```", "").strip())
        except: return ["Tell me more", "Example code", "Challenge: Write it"]

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

    # def _generate_socratic_plan(self, query: str, context: str, user_goal: str = None) -> str:
        
    #     # Build dynamic instruction based on whether a goal exists
    #     goal_instruction = ""
    #     if user_goal:
    #         goal_instruction = f"6. **GOAL ALIGNMENT:** The student's current learning goal is: '{user_goal}'. If the topic of their query helps them reach that goal, explicitly mention it in the Strategy section to motivate them."

    #     prompt = f"""
    #     You are an encouraging C Programming Tutor for junior students.
        
    #     Student Query: "{query}"
    #     Reference Material: {context}

    #     **CRITICAL RULES:**
    #     1. **CONCEPT LIMITATION:** You may ONLY teach concepts present in the Reference Material.
    #        - If the answer is NOT in the Reference Material, state that you do not have information on it.
    #     2. **SOURCE GROUNDING:** Mention specific variable names/examples from the text.
    #     3. **TEXT FIRST:** Text explanation MUST come before any diagrams.
    #     4. **VISUALIZATION:** If appropriate, include a Mermaid diagram.
    #        - **CRITICAL SANITIZATION:** 
    #          - ABSOLUTELY NO PARENTHESES `()` inside node labels. 
    #          - ABSOLUTELY NO BRACKETS `[]` inside node labels.
    #          - ABSOLUTELY NO QUOTES `"` inside node labels.
    #          - **BAD:** `A[sum(a,b)]` or `B{{arr[i]}}`
    #          - **GOOD:** `A[sum a b]` or `B{{arr index i}}`
    #     5. **TONE INSTRUCTIONS:** Speak DIRECTLY to the student. Use "You" and "We".
    #     {goal_instruction}

    #     Format your response like this:
        
    #     ## Strategy
    #     [Text Explanation. Mention the Goal here if applicable.]

    #     ## Visual Logic
    #     ```mermaid
    #     graph TD
    #        A[Start] --> B[End]
    #     ```
        
    #     ## Implementation Plan
    #     1. **[Step Name]**: [Description]
    #        - *Example from text:* "In [Filename], we saw..."
    #        ```c
    #        // Generic Syntax
    #        code...
    #        ```
        
    #     ## Guiding Question
    #     [Your question here]
    #     """
    #     # return self.llm_interface.generate_response(prompt)
    #     return self.llm_interface.generate_response(f"Socratic plan for {query} using {context}")

    def _generate_socratic_plan(self, query: str, context: str, user_goal: str = None) -> str:
        
        # Explicitly label the user's personal goal to avoid confusion
        goal_instruction = ""
        if user_goal:
            goal_instruction = f"""
            6. **CONNECT TO USER'S PROJECT:** The student's long-term goal is: "{user_goal}".
               - You MUST explicitly explain how this specific concept helps them achieve "{user_goal}".
               - Example: "You need While Loops for your Calculator to keep asking for numbers until the user presses 'Quit'."
            """

        prompt = f"""
        You are an encouraging C Programming Tutor. You are talking DIRECTLY to a junior student.
        
        **YOUR TASK:** Help the student solve their problem: "{query}" using the Reference Material below.

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
           - *Example from text:* "In [Filename], we saw..."
           ```c
           // Generic Syntax
           code...
           ```
        
        ## Guiding Question
        [A thoughtful question to check their understanding]
        """
        
        return self.llm_interface.generate_response(prompt)
    
    def _generate_concept_explanation(self, query: str, context: str, user_goal: str = None) -> str:
        print(f"DEBUG: Generating concept for goal: '{user_goal}'") 
        
        goal_section = ""
        if user_goal:
            goal_section = f"""
            6. **GOAL CONNECTION (CRITICAL):** The student's goal is: "{user_goal}". 
               - You MUST explicitly explain how the current concept helps them achieve "{user_goal}".
            """
            
        prompt = f"""
        You are an expert C Programming Tutor.
        
        Student Query: "{query}"
        User's Goal: "{user_goal if user_goal else 'None'}"
        Reference Material: {context}

        **MANDATORY RULES:**
        1. **STRICT LIMITATION:** Check the Reference Material. If the concept is NOT present, say: "I don't have information..."
        2. **PERSONALIZATION:** Acknowledge known concepts from USER CONTEXT.
        3. **TEXT PRIORITY:** Clear text explanation FIRST (min 3 sentences). Use analogies. **Explicitly explain Use Cases.**
        4. **VISUALIZATION:** Generate a Mermaid.js diagram (`graph TD`) if the concept involves flow/structure.
           - **STRICT SYNTAX:** Use square brackets for labels: `A["Label"]`. Do NOT use single quotes like `A'Label'`. 
           - Escape internal quotes.
        5. **SOURCE GROUNDING:** Quote specific examples from text.
        6. **GOAL ALIGNMENT:** If the user has a goal, you MUST explain how this concept applies to it.
        
        **STRICT RESPONSE FORMAT:**
        
        ## Explanation
        [Start by bridging from known concepts if applicable. Then explain the new concept using text and analogies.]
        
        ## Use Cases
        [Explain WHEN and WHY this concept is used in real programming. If the user asked "What is its use case?", focus heavily here.]

        {goal_section}

        ## Visual Model
        ```mermaid
        graph TD
           A["Start"] --> B{{"Condition?"}}
           B -- "Yes" --> C["Action"]
           B -- "No" --> D["End"]
        ```
        
        ## Example from Class
        [Reference specific code from text]
        """
        
        return self.llm_interface.generate_response(prompt)

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
                
                # Create buttons for the missing prereqs
                suggestion_buttons = [f"Explain {p}" for p in unknown_prereqs]
                
                # --- CHANGE THIS LINE ---
                # OLD: suggestion_buttons.append(f"Teach me what anyway")
                # NEW: Use the 'entities' list to insert the actual topic name
                topic_name = entities[0] if entities else "this"
                suggestion_buttons.append(f"Teach me {topic_name} anyway") 
                # ------------------------

                return {
                    "answer": f"## 🛑 Hold on!\n\nTo understand **{entities[0] if entities else 'this'}**, you really need to know: {prereq_str} first...",
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
        session_id = kwargs.get('session_id')
        
        # 1. DETECT FORCED TEACHING (Button Click)
        # We check this *before* contextualization to avoid rewriting the button text
        is_force_teach = "teach me" in query.lower() and "anyway" in query.lower()
        
        # =========================================================
        # 0. CONTEXTUALIZATION (Session Context)
        # =========================================================
        yield {"type": "status", "message": "Understanding context...", "percent": 5}
        t0 = time.time()
        
        # If it's a normal question, rewrite it using chat history. 
        # If it's a button click, keep it as is for now.
        if not is_force_teach:
            search_query = await self._contextualize_query(query, username, session_id)
        else:
            search_query = query 

        profiler["0_Context"] = time.time() - t0

        # =========================================================
        # 1 & 2. ANALYSIS
        # =========================================================
        yield {"type": "status", "message": "Analyzing query...", "percent": 15}
        t0 = time.time()

        intent = ""
        entities = []

        if config.INTENT_CLASSIFIER_MODE == "fast":
            if is_force_teach:
                # FIX: Clean Regex to extract ONLY the topic from the button text
                # Input: "Teach me Loops anyway" -> Output: "Loops"
                # Input: "Teach me Linked Lists anyway" -> Output: "Linked Lists"
                match = re.search(r"teach me (.*?) anyway", query.lower())
                if match:
                    raw_topic = match.group(1).replace(".", "").replace("?", "").strip()
                    entities = [raw_topic]
                else:
                    entities = [query]
                intent = "CONCEPT" # Force intent to explanation
            else:
                intent = fast_classifier.classify_intent(search_query) 
                entities = fast_classifier.extract_entities(search_query)
                
                # Fallback extraction
                if not entities:
                    words = re.findall(r'\b\w+\b', search_query.lower())
                    stopwords_list = {'what', 'is', 'the', 'how', 'do', 'i', 'it', 'its', 'use', 'case', 'show', 'me', 'tell', 'explain', 'teach', 'anyway', 'does'}
                    entities = [w for w in words if w not in stopwords_list and len(w) > 2]
                    entities = entities[:3]
        else:
            intent = self._classify_intent(search_query)
            entities = [search_query] 

        # --- FIX: REWRITE QUERY FOR GENERATION ---
        # If forced, we must ask the LLM specifically for Use Cases and Definition
        # otherwise it will hallucinate about "enthusiasm".
        if is_force_teach:
            main_topic = entities[0] if entities else "this concept"
            search_query = f"Explain {main_topic} and its specific use cases in C programming."

        profiler["1_Analysis"] = time.time() - t0
        self.logger.info(f"Query: {query} | Rewritten: {search_query} | Entities: {entities}")

        # --- SECURITY BLOCK ---
        if intent == "SECURITY_RISK":
            msg = "⛔ **Security Alert**: This request violates safety policies."
            yield {"type": "complete", "data": {
                "answer": msg, "sources": [], "suggestions": [], "intent": intent
            }}
            return
        
        # --- GATEKEEPER (Topic Lock) ---
        from app.core.settings_manager import settings_manager
        topic_settings = settings_manager.get_settings()
        for entity in entities:
            for t, on in topic_settings.items():
                if (entity.lower() in t.lower()) and not on:
                    msg = f"🔒 Topic **{t}** is locked by the teacher."
                    yield {"type": "complete", "data": {
                        "answer": msg, "sources": [], "suggestions": [], "intent": intent
                    }}
                    return

        # =========================================================
        # 3. PREREQUISITE CHECK (Universal Knowledge)
        # =========================================================
        yield {"type": "status", "message": "Checking prerequisites...", "percent": 40}
        t_graph = time.time()
        
        # 1. User Override check
        user_override = "anyway" in query.lower() or "i know" in query.lower()
        
        # 2. "Already Learned" check (The Fix)
        # If the user asks about "Loops", and "Loops" is in user_knowledge.json, skip check.
        target_already_known = False
        if entities:
            for ent in entities:
                if knowledge_manager.has_mastered(username, ent):
                    target_already_known = True
                    self.logger.info(f"✅ User already mastered '{ent}'. Skipping prereq check.")
                    break

        should_check_prereqs = not user_override and not target_already_known
        
        if (intent == "CONCEPT" or intent == "PROBLEM") and should_check_prereqs:
            # Perform the Graph Check
            all_prereqs = self._check_prerequisites(search_query, entities)
            
            # Filter: Prereq is unknown AND Prereq is NOT the topic itself
            current_topics_lower = [e.lower() for e in entities]
            unknown = []
            
            for p in all_prereqs:
                # Check for Self-Dependency (e.g. Loop requires Loop)
                is_self = False
                for curr in current_topics_lower:
                    if curr in p.lower() or p.lower() in curr:
                        is_self = True
                        break
                
                # Check if Prereq is mastered
                if not is_self and not knowledge_manager.has_mastered(username, p):
                    unknown.append(p)
            
            # If genuine prerequisites are missing, BLOCK
            if unknown:
                msg = f"## 🛑 Hold on!\n\nYou need to understand **{', '.join(unknown)}** before tackling **{entities[0]}**."
                topic_label = entities[0] if entities else "this concept"
                
                yield {"type": "complete", "data": {
                    "answer": msg, 
                    "sources": [], 
                    "suggestions": [f"Explain {p}" for p in unknown] + [f"Teach me {topic_label} anyway"], 
                    "intent": "GUIDANCE"
                }}
                return
        
        profiler["3_Graph"] = time.time() - t_graph

        # =========================================================
        # 4. RETRIEVAL
        # =========================================================
        yield {"type": "status", "message": "Searching knowledge base...", "percent": 60}
        t0 = time.time()
        
        q_search = f"{' '.join(entities)} in C" if len(search_query.split()) > 5 else search_query
        chunks = self._execute_retrieval(q_search, intent, user_role)
        profiler["4_Retrieval"] = time.time() - t0
        
        if not chunks:
             msg = f"I don't have specific info on {entities[0] if entities else 'that'}."
             yield {"type": "complete", "data": {
                 "answer": msg, "sources": [], "suggestions": [], "intent": intent
             }}
             return

        # =========================================================
        # 5. GENERATION
        # =========================================================
        yield {"type": "status", "message": "Drafting response...", "percent": 80}
        t0 = time.time()

        # Context Building
        known_concepts = knowledge_manager.get_known_concepts(username)
        user_context_str = f"USER CONTEXT: The student already knows: {', '.join(known_concepts)}." if known_concepts else ""
        context_text = f"{user_context_str}\n\n"
        for c in chunks:
            context_text += f"--- Source: {c.get('metadata', {}).get('document_name')} ---\n{c.get('text')}\n\n"

        suggest_task = asyncio.create_task(
            asyncio.to_thread(self._generate_suggestions, search_query, context_text)
        )

        prompt = ""
        if intent == "REVIEW": 
            prompt = self._generate_code_review(search_query, context_text, user_goal)
        elif intent == "PROBLEM": 
            prompt = self._generate_socratic_plan(search_query, context_text, user_goal)
        else: 
            prompt = self._generate_concept_explanation(search_query, context_text, user_goal)

        yield {"type": "status", "message": "Generating...", "percent": 100}
        
        # Simulate Streaming / Pass-through
        full_answer = prompt # The prompt variable actually holds the response text here
        chunk_size = 20
        for i in range(0, len(full_answer), chunk_size):
            yield {"type": "token", "text": full_answer[i:i+chunk_size]}
            await asyncio.sleep(0.01)

        suggestions = await suggest_task
        profiler["5_Gen"] = time.time() - t0
        
        # --- FIX: AUTO-UPDATE KNOWLEDGE ---
        # If we successfully explained a concept, mark it as known!
        # This prevents the Prereq check from triggering again in future sessions.
        if intent == "CONCEPT":
            for entity in entities:
                # Filter out garbage words before saving
                if len(entity) > 2 and entity.lower() not in ["teach", "anyway", "me", "show", "tell", "explain"]:
                    knowledge_manager.mark_concept_as_known(username, entity)
                    self.logger.info(f"📚 Auto-Learned: {username} now knows {entity}")

        full_answer = self._sanitize_mermaid(full_answer)
        formatted_sources = [{'document_name': c['metadata']['document_name'], 'chunk_text': c['text']} for c in chunks]

        yield {
            "type": "complete",
            "data": {
                "answer": full_answer,
                "sources": formatted_sources,
                "suggestions": suggestions,
                "intent": intent,
                "timings": profiler,
                "session_id": session_id # Ensure session is maintained
            }
        }