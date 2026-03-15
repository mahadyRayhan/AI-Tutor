# backend/app/agents/cot_rag_agent.py

import logging
import json
import asyncio 
import re
from typing import Dict, List, Any
from dataclasses import dataclass
from numpy import dot
from numpy.linalg import norm
import random

from app.db.llm_interface import LLMInterface
from app.db.vector_store import VectorStore
from app.db.graph_db import Neo4jGraphDB
from app.core.settings_manager import settings_manager
from app.core.user_knowledge_manager import knowledge_manager
from app.core.history_manager import history_manager
from app.core import config

from app.agents.schema import AgentState
from app.agents.sentinel import SentinelAgent
from app.agents.scaffolding import ScaffoldingAgent
from app.agents.examiner import ExaminerAgent
from app.agents.reviewer import CodeReviewerAgent
from app.agents.socratic import SocraticTutorAgent
from app.db.sqlite_db import db
from app.agents.profiler import ProfilerAgent

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
    # def __init__(self, llm_interface: LLMInterface, vector_store: VectorStore, graph_db: Neo4jGraphDB, logger: logging.Logger):
    def __init__(self, llm_fast: LLMInterface, llm_smart: LLMInterface, vector_store: VectorStore, graph_db: Neo4jGraphDB, logger: logging.Logger):
        self.llm_fast = llm_fast
        self.llm_smart = llm_smart
        self.vector_store = vector_store
        self.graph_db = graph_db
        self.logger = logger
        self.sentinel = SentinelAgent(llm_fast, logger)
        self.scaffolding = ScaffoldingAgent(llm_smart, logger, vector_store, graph_db)
        self.examiner = ExaminerAgent(llm_fast, logger, graph_db)
        self.reviewer = CodeReviewerAgent(llm_smart, logger, vector_store)
        self.socratic = SocraticTutorAgent(llm_fast, logger, vector_store, graph_db)
        self.profiler = ProfilerAgent(llm_fast, logger)

    # async def _contextualize_query(self, current_query: str, username: str, session_id: str) -> str:
    #     """
    #     Rewrites the query to include context.
    #     """
    #     if not session_id: return current_query

    #     session_data = history_manager.get_session_details(username, session_id)
    #     if not session_data or not session_data.get('messages'): return current_query

    #     # Get last 4 messages
    #     msgs = session_data['messages'][-4:] 
    #     history_str = ""
    #     for m in msgs:
    #         role = "Student" if m['role'] == 'user' else "Tutor"
    #         history_str += f"{role}: {m['content']}\n"

    #     self.logger.info(f"📜 Context History Length: {len(msgs)}")
    #     if msgs:
    #         self.logger.info(f"📜 Last Message: {msgs[-1]['content'][:500]}...")

    #     # --- FIX: STRICTER CONTEXT PROMPT ---
    #     prompt = f"""
    #     Chat History:
    #     {history_str}
        
    #     Latest Student Question: {current_query}
        
    #     Task: Rewrite the "Latest Student Question" into a standalone sentence.
        
    #     CRITICAL RULES:
    #     1. If the student uses pronouns (it, this, that), refer back to the *STUDENT'S* previous topic, NOT the Tutor's technical explanation.
    #        - Example History: 
    #          Student: What is a loop? 
    #          Tutor: A loop is Control Flow...
    #          Student: What is its use case?
    #        - CORRECT Rewrite: "What is the use case of a loop?"
    #        - WRONG Rewrite: "What is the use case of Control Flow?"
    #     2. Keep the original intent exactly.
    #     3. Do NOT answer the question.
        
    #     Rewritten Question:
    #     """
        
    #     try:
    #         rewritten = await asyncio.to_thread(self.llm_interface.generate_response, prompt)
    #         rewritten = rewritten.strip().replace('"', '')
    #         # --- ADD THIS PRINT STATEMENT ---
    #         print(f"\n🔍 [CONTEXTUALIZER] Input: '{current_query}' -> Rewritten: '{rewritten}'\n")
    #         # --------------------------------
    #         self.logger.info(f"🔄 Contextualized: '{current_query}' -> '{rewritten}'")
    #         return rewritten
    #     except Exception as e:
    #         return current_query

    async def _contextualize_query(self, current_query, username, session_id):
        if not session_id: return current_query

        session_data = history_manager.get_session_details(username, session_id)
        if not session_data or not session_data.get('messages'): return current_query

        # --- FIX 1: EXCLUDE CURRENT MESSAGE ---
        # main.py adds the user message BEFORE calling this. 
        # We want history to be everything BEFORE that message.
        all_msgs = session_data['messages']
        
        # Take the last 5 messages, EXCLUDING the very last one (which is current_query)
        # format: [..., Bot, User, Bot, (Current_User_Msg)] -> we want up to the last Bot
        relevant_msgs = all_msgs[:-1][-5:] 
        
        history_str = ""
        for m in relevant_msgs:
            role = "Student" if m['role'] == 'user' else "Tutor"
            history_str += f"{role}: {m['content']}\n"
        # --------------------------------------

        # LOGGING (Keep this for debugging)
        # self.logger.info(f"📜 Context History ({len(relevant_msgs)} items): \n{history_str}")

        # --- FIX 2: STRONGER PROMPT ---
        prompt = f"""
        Chat History:
        {history_str}
        
        Latest User Input: "{current_query}"
        
        **TASK:**
        Rewrite the "Latest User Input" into a complete, standalone question.
        
        **CRITICAL RULE:**
        Look at the **LAST TUTOR MESSAGE** in the history.
        If the user refers to "it", "this", or "the syntax", substitute it with the **Topic** of that Tutor message.
        
        **Examples:**
        - History: [Tutor: "Arrays are lists..."] -> Input: "How do I declare it?" -> Rewrite: "How do I declare an **Array**?"
        - History: [Tutor: "Variables store data..."] -> Input: "Give syntax" -> Rewrite: "Give syntax for **Variables**."

        **Rewritten Question:**
        """
        
        try:
            rewritten = await asyncio.to_thread(self.llm_fast.generate_response, prompt)
            rewritten = rewritten.strip().replace('"', '')
            self.logger.info(f"🔄 Contextualized: '{current_query}' -> '{rewritten}'")
            return rewritten
        except Exception as e:
            self.logger.error(f"Contextualization failed: {e}")
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
        3. PROBLEM: Asking "how to...", "write a code...", "solve...", "create code..."
        4. DEBUG: Asking "why is this error...", "fix this...", crashes
        5. OFF_TOPIC: Anything NOT related to teaching/learning C Programming. Includes:
           - Greetings ("hi", "how are you", "your name")
           - General Knowledge ("what is the time", "who is the president", "capital of France")
           - Other Languages ("python code", "java vs c++", "how to cook")
           - Creative writing, math, or casual chat unrelated to coding.

        Query: "{query}"
        
        Respond with ONE word: CONCEPT, PROBLEM, DEBUG, REVIEW or OFF_TOPIC.
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
        Robust Mermaid sanitizer.
        Strips [], (), and quotes from INSIDE node labels to prevent syntax errors.
        E.g. A[Declare sum[ set to 0]] → A["Declare sum set to 0"]
        """
        def clean_node_label(raw_label: str) -> str:
            """Strip all brackets, parens, and quotes from inside a label."""
            clean = raw_label.replace('[', '').replace(']', '')
            clean = clean.replace('(', '').replace(')', '')
            clean = clean.replace('"', '').replace("'", '')
            return clean.strip()
        
        def clean_mermaid_line(line: str) -> str:
            """Process a single mermaid line, fixing all node definitions."""
            stripped = line.strip()
            
            # Skip non-node lines
            if not stripped or any(stripped.startswith(kw) for kw in ['graph ', 'flowchart ', '%%', 'style ', 'classDef ', 'subgraph', 'end', 'direction ']):
                return line
            
            # Skip pure arrow lines (no brackets at all)
            if '[' not in stripped and '{' not in stripped and '(' not in stripped:
                return line
            
            indent = re.match(r'^(\s*)', line).group(1)
            
            # Rebuild the line by finding and fixing each node definition
            # Strategy: walk through the line character by character
            result = []
            i = 0
            chars = stripped
            
            while i < len(chars):
                # Look for node ID followed by opening bracket
                id_match = re.match(r'(\w+)\s*(\[|\{|\()', chars[i:])
                if id_match:
                    node_id = id_match.group(1)
                    open_br = id_match.group(2)
                    close_br = {'[': ']', '{': '}', '(': ')'}[open_br]
                    
                    # Move past ID and opening bracket
                    start = i + id_match.end()
                    
                    # Find the matching close bracket (handle nesting)
                    depth = 1
                    j = start
                    while j < len(chars) and depth > 0:
                        if chars[j] == open_br:
                            depth += 1
                        elif chars[j] == close_br:
                            depth -= 1
                        j += 1
                    
                    # Extract and clean the label
                    label_raw = chars[start:j-1]  # everything between brackets
                    label_clean = clean_node_label(label_raw)
                    
                    if label_clean:
                        result.append(f'{node_id}["{label_clean}"]')
                    else:
                        result.append(f'{node_id}')
                    
                    i = j  # move past the closing bracket
                else:
                    # Not a node definition — copy character(s) as-is
                    # But try to grab chunks (arrows, whitespace, etc.)
                    arrow_match = re.match(r'(\s*(?:-->|--\>|---|==>|-\.->|--)\s*)', chars[i:])
                    if arrow_match:
                        result.append(arrow_match.group(1))
                        i += arrow_match.end()
                    else:
                        result.append(chars[i])
                        i += 1
            
            return indent + ''.join(result)
        
        def clean_mermaid_block(block: str) -> str:
            return '\n'.join(clean_mermaid_line(line) for line in block.split('\n'))
        
        # Find all ```mermaid ... ``` blocks and sanitize them
        def replacer(match):
            return '```mermaid\n' + clean_mermaid_block(match.group(1)) + '\n```'
        
        text = re.sub(r'```mermaid\s*\n(.*?)\n\s*```', replacer, text, flags=re.DOTALL)
        return text


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
        elif intent == "COMPLEX_PROBLEM":
            # Handled directly by Socratic agent now, but if this legacy run() is called:
            final_answer = "This should be handled by the Socratic Agent."
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
    
    async def _handle_active_states(self, query, username, session_id, user_role):
        """Checks if user is currently inside a Quiz or a Guided Plan."""
        if not session_id: return None
        
        current_state = history_manager.get_session_state(username, session_id)
        
        # 1. Handle Active Guided Plan (NEW)
        active_plan = current_state.get("active_plan")
        if active_plan and active_plan.get("is_active"):
            return self._continue_guided_plan(query, active_plan, username, session_id, user_role)

        # 2. Handle Active Quiz/Check (Existing)
        if current_state.get("awaiting_quiz_answer"):
            return self._handle_quiz_response(query, current_state, username, session_id)
            
        return None

    def _analyze_query(self, search_query, original_query, is_force_teach, is_verify_request):
        intent = ""
        entities = []
        
        # ---------------------------------------------------------
        # 1. FORCE 'PROBLEM' INTENT FOR EXERCISES (The Fix)
        # ---------------------------------------------------------
        # If user explicitly asks to write/create code, it is a PROBLEM.
        # We check this BEFORE asking the AI model to avoid misclassification.
        strong_problem_keywords = ["write a c program", "write a program", "create a program", "code for", "exercise"]
        if any(k in original_query.lower() for k in strong_problem_keywords):
            intent = "PROBLEM"
            entities = [original_query] 
            return intent, entities
        # ---------------------------------------------------------

        if config.INTENT_CLASSIFIER_MODE == "fast":
            if is_force_teach:
                match = re.search(r"teach me (.*?) anyway", original_query.lower())
                entities = [match.group(1).strip()] if match else [original_query]
                intent = "CONCEPT"
            elif is_verify_request:
                match = re.search(r"know (.*?) \(verify\)", original_query.lower())
                entities = [match.group(1).strip()] if match else []
                intent = "QUIZ"
            else:
                # Fast Classify
                intent = fast_classifier.classify_intent(search_query)
                entities = fast_classifier.extract_entities(search_query)
                if not entities:
                    # Fallback Regex
                    words = re.findall(r'\b\w+\b', search_query.lower())
                    stop = {'what', 'is', 'how', 'to', 'c', 'programming', 'code'}
                    entities = [w for w in words if w not in stop and len(w)>2][:3]
        else:
            # Slow LLM Classify
            intent = self._classify_intent(search_query)
            entities = [search_query] 

        return intent, entities

    def _check_gatekeeping(self, query, intent, entities, username, session_id):
        # 1. Teacher Lock Check
        # from app.core.settings_manager import settings_manager
        # topic_settings = settings_manager.get_settings()
        # for entity in entities:
        #     for t, on in topic_settings.items():
        #         if (entity.lower() in t.lower()) and not on:
        #             return {"answer": f"🔒 Topic **{t}** is locked by the teacher.", "sources": [], "intent": intent}

        # 2. Prerequisite Check
        if "anyway" in query.lower() or "i know" in query.lower(): return None # User Override
        
        # Don't check prereqs for simple greetings or non-concept intents
        if intent not in ["CONCEPT", "PROBLEM"]: return None
        
        # Do the Graph Check
        all_prereqs = self._check_prerequisites(query, entities)
        
        # --- BUG FIX START: Robust Filtering ---
        target_concepts = [e.lower() for e in entities] # e.g. ['loop']
        unknown = []
        
        for p in all_prereqs:
            p_norm = p.lower() # e.g. 'loops'
            
            # A. Check Mastery
            if knowledge_manager.has_mastered(username, p): 
                continue
                
            # B. Check Self-Reference (The Bug Fix)
            # If 'loop' is inside 'loops' (or vice versa), ignore it.
            is_same_topic = False
            for t in target_concepts:
                # Check for substring match to handle plurals
                if t in p_norm or p_norm in t: 
                    is_same_topic = True
                    break
            
            if not is_same_topic:
                unknown.append(p)
        # ---------------------------------------
        
        if unknown:
            # --- FIX: SAVE THE USER'S GOAL ---
            # We save "Explain Pointers" so we can remember it after the quiz
            history_manager.update_session_state(username, session_id, {
                "pending_goal": query 
            })
            # ---------------------------------

            btns = [f"Explain {p}" for p in unknown] + [f"I know {p} (Verify)" for p in unknown] + [f"Teach me {entities[0]} anyway"]
            
            topic_name = entities[0] if entities else 'this concept'
            prereq_list = "**, **".join(unknown)
            
            msg = f"## 🧱 Let's build a foundation first!\n\n**{topic_name}** is an exciting topic, but it relies heavily on **{prereq_list}**.\n\nTo make learning {topic_name} much easier (and less frustrating!), I recommend we quickly review those basics first. What do you think?"

            return {
                "answer": msg,
                "sources": [],
                "suggestions": btns,
                "intent": "GUIDANCE"
            }
        return None

    async def _execute_standard_rag(self, query, intent, entities, user_role, username, user_goal, session_id):
        yield {"type": "status", "message": "Searching knowledge base...", "percent": 60}
        
        # 1. Retrieval
        q_search = f"{' '.join(entities)} in C" if len(query.split()) > 5 else query
        chunks = self._execute_retrieval(q_search, intent, user_role, existing_entities=entities)
        
        if not chunks:
             yield {"type": "complete", "data": {"answer": "I don't have information on that.", "sources": [], "intent": intent}}
             return

        # 2. Build Context
        yield {"type": "status", "message": "Drafting response...", "percent": 80}
        context_text = "\n\n".join([f"--- Source: {c['metadata']['document_name']} ---\n{c['text']}" for c in chunks])

        # 3. Generate Suggestions
        suggest_task = asyncio.create_task(asyncio.to_thread(self._generate_suggestions, query, context_text))

        # 4. Select Prompt
        if intent == "REVIEW": prompt = self._build_review_prompt(query, context_text, user_goal)
        elif intent == "PROBLEM": prompt = self._build_socratic_plan_prompt(query, context_text, user_goal)
        else: prompt = self._build_concept_prompt(query, context_text, user_goal)

        # 5. Stream Answer
        yield {"type": "status", "message": "Generating...", "percent": 100}
        full_answer = ""
        async for token in self.llm_interface.stream_response_async(prompt):
            full_answer += token
            yield {"type": "token", "text": token}

        # 6. Auto-Learn Concept (If explanation provided)
        if intent == "CONCEPT" and entities:
             knowledge_manager.mark_concept_as_known(username, entities[0])

        yield {
            "type": "complete",
            "data": {
                "answer": self._sanitize_mermaid(full_answer),
                "sources": [{'document_name': c['metadata']['document_name'], 'chunk_text': c['text']} for c in chunks],
                "suggestions": await suggest_task,
                "intent": intent,
                "session_id": session_id
            }
        }
    
    async def run_stream(self, query: str, user_role: str = 'student', **kwargs):
        import time
        t_start = time.time()
        
        # Unpack Arguments
        username = kwargs.get('username', 'anonymous')
        user_goal = kwargs.get('user_goal')
        session_id = kwargs.get('session_id')

        # --- FIX 2: EXTRACT FEEDBACK TAGS ---
        feedback_mode = None
        if query.startswith("[SIMPLIFY]"):
            feedback_mode = "simplify"
            query = query.replace("[SIMPLIFY]", "").strip()
        elif query.startswith("[DEEP_DIVE]"):
            feedback_mode = "deep_dive"
            query = query.replace("[DEEP_DIVE]", "").strip()
        # ------------------------------------
        
        # --- 1. STATE FETCH & DEBUG ---
        current_state = {}
        if session_id:
            current_state = history_manager.get_session_state(username, session_id)
            
        self.logger.info(f"🔄 [ORCHESTRATOR] Session: {session_id} | State: {current_state}")
        
        # --- 2. CONTEXT SKIP RULES ---
        is_in_quiz = current_state.get("awaiting_quiz_answer", False)
        active_plan = current_state.get("active_plan", {})
        is_in_plan = active_plan.get("is_active", False)
        is_force_teach = "teach me" in query.lower() and "anyway" in query.lower()
        is_verify_request = "verify" in query.lower() and "know" in query.lower()
        
        # NEW: Don't let the LLM rewrite raw code blocks!
        # If it has brackets or semicolons, it's code, leave it alone.
        is_raw_code = "{" in query or "}" in query or ";" in query
        
        should_skip_context = is_force_teach or is_verify_request or is_in_quiz or is_in_plan or is_raw_code

        # --- 3. CONTEXTUALIZATION & ONBOARDING INTERCEPT ---
        yield {"type": "status", "message": "Understanding context...", "percent": 5}
        
        onboarding_chips = ["i'm a complete beginner", "i know a little bit", "what is the syntax?"]
        
        # Check if the user clicked one of our initial greeting buttons
        if query.strip().lower() in onboarding_chips:
            self.logger.info(f"💡 Intercepted Onboarding Response: {query}")
            target_concept = "C Programming" # Default fallback
            
            # Find the topic from the bot's previous greeting message
            if current_state and current_state.get("messages"):
                last_bot_msg = next((m for m in reversed(current_state["messages"]) if m["role"] == "bot"), None)
                if last_bot_msg:
                    import re
                    # Extracts the bolded concept from "Let's dive into **Concept**"
                    match = re.search(r"\*\*([^*]+)\*\*", last_bot_msg["content"])
                    if match:
                        target_concept = match.group(1)
            
            # Translate the conversational click into a technical search query
            search_query = f"Explain {target_concept} for a beginner."
            should_skip_context = True # Skip LLM contextualization
            
        elif not should_skip_context:
            search_query = await self._contextualize_query(query, username, session_id)
        else:
            self.logger.info(f"⏭️ Skipping Contextualization for: '{query}'")
            search_query = query

        # =========================================================
        # --- NEW: PROACTIVE GREETING (STAGE 4) ---
        # =========================================================
        if query.strip() == "[INIT_SESSION]":
            known_concepts = knowledge_manager.get_known_concepts(username)
            
            greeting = ""
            suggestions = ["Help me get started", "I have a specific question"]
            
            if user_goal:
                # --- FIX: Dynamic Goal Phrasing ---
                goal_lower = user_goal.strip().lower()
                action_verbs = ['build', 'create', 'make', 'write', 'code', 'develop', 'learn', 'master', 'finish', 'complete']
                
                # Check if the goal starts with an action verb
                starts_with_action = any(goal_lower.startswith(verb) for verb in action_verbs)
                
                if starts_with_action:
                    # Example: "Your Current Goal is to build a Tic-Tac-Toe Game."
                    greeting += f"Your Current Goal is to <b><i>{user_goal}</i></b><br>"
                else:
                    # Example: "Your Current Goal is to master Functions."
                    greeting += f"Your Current Goal is to master <b><i>{user_goal}</i></b><br>"
                # ----------------------------------
            
            if known_concepts:
                last_known = known_concepts[-1] 
                greeting += f"Last time, you successfully mastered <b>{last_known}</b>. Awesome job! 🚀<br>"
                suggestions = [f"Review {last_known}", "Teach me something new", "I need to debug code"]
            else:
                greeting += "We have a blank slate! What topic should we dive into first?"
                suggestions = ["What is a Variable?", "How does C work?", "I need help with an assignment"]

            # Only yield the complete data package. No token stream.
            yield {"type": "complete", "data": {
                "answer": greeting,
                "sources": [],
                "intent": "GREETING",
                "suggestions": suggestions,
                "entities": ["General"],
                "session_id": session_id
            }}
            return
            
        if query.strip().startswith("[START_TOPIC]"):
            import re
            match = re.search(r"\[START_TOPIC\]\s+(.*?)\s+\[GOAL\]\s+(.*)", query.strip())
            if match:
                concept = match.group(1).strip()
                goal = match.group(2).strip()
                greeting = f"Awesome! Let's dive into **{concept}** to help you reach your goal of **{goal}**.<br><br>Before we start, what do you currently know about {concept}? Are you completely new to it, or have you seen it before?"
            else:
                concept = query.replace("[START_TOPIC]", "").strip()
                greeting = f"Awesome! Let's dive into **{concept}**.<br><br>Before we start, what do you currently know about {concept}? Are you completely new to it, or have you seen it before?"
            
            suggestions = ["I'm a complete beginner", "I know a little bit", "What is the syntax?"]
            
            yield {"type": "complete", "data": {
                "answer": greeting,
                "sources": [],
                "intent": "GREETING",
                "suggestions": suggestions,
                "entities": [concept],
                "session_id": session_id
            }}
            return
        # =========================================================

        # --- 4. LOAD PROFILE & SETUP STATE ---
        user_row = db.fetch_one("SELECT learning_profile FROM users WHERE username = ?", (username,))
        learning_profile = json.loads(user_row['learning_profile']) if user_row and user_row['learning_profile'] else {}

        # Inject the feedback mode if a button was clicked
        if feedback_mode:
            learning_profile['feedback_mode'] = feedback_mode

        state = AgentState(
            query=search_query,
            original_query=query, 
            user_id=username,
            session_id=session_id,
            user_role=user_role,
            user_goal=user_goal,
            profile=learning_profile
        )

        # --- 5. PROACTIVE POP QUIZZES ---
        msg_list = current_state.get("messages", []) if current_state else []
        if not msg_list and session_id:
            sess_details = history_manager.get_session_details(username, session_id)
            msg_list = sess_details.get("messages", []) if sess_details else []
            
        user_msg_count = sum(1 for m in msg_list if m.get("role") == "user")

        if user_msg_count > 0 and user_msg_count % 5 == 0 and not is_in_quiz and not is_in_plan:
            self.logger.info("🎯 Triggering Proactive Pop Quiz!")
            
            recent_topic = "Variables" 
            for msg in reversed(msg_list):
                if msg.get("role") == "bot" and msg.get("topic") and msg.get("topic") != "General":
                    recent_topic = msg.get("topic")
                    break
            
            history_manager.update_session_state(username, session_id, {"pending_goal": state.query})
            state.query = f"know {recent_topic} (verify)"
            state.intent = "QUIZ"
            state.entities = [recent_topic]
            state.profile['is_surprise_quiz'] = True
        # --------------------------------------------

        # ---------------------------------------------------------
        # AGENT PIPELINE
        # ---------------------------------------------------------

        # 1. ACTIVE SCAFFOLDING PRIORITY (The Fix)
        # If the user is currently in a guided plan, let the Scaffolding agent handle it.
        # This prevents the Sentinel from blocking valid menu clicks like "Help me message the TA".
        if is_in_plan:
            async for event in self.scaffolding.process(state):
                yield event
            if state.stop_processing: return

        # 2. SENTINEL (Security & Classification)
        # Runs on all new queries that aren't part of an active plan
        async for event in self.sentinel.process(state):
            yield event
        if state.stop_processing: return

        # 3. THE SOCRATIC LOCK
        if current_state.get("awaiting_micro_challenge"):
            user_input_lower = state.original_query.lower()
            skip_phrases = ["skip", "idk", "i don't know", "no", "stop", "answer", "tell me", "hint"]
            has_code = any(c in state.original_query for c in [";", "{", "}", "=", "(", ")", "int ", "char ", "float "])
            
            is_new_question = not any(p in user_input_lower for p in skip_phrases) and not has_code and ("?" in state.original_query or state.original_query.lower().startswith(("what", "how", "why")))

            if is_new_question:
                challenge_topic = current_state.get("challenge_topic", "that concept")
                history_manager.update_session_state(username, session_id, {"pending_goal": state.original_query})
                
                msg = f"I'd be happy to explain **{state.original_query}** next! \n\nBut first, I want to make sure you understood **{challenge_topic}**. Give my micro-challenge a try! *(Or type 'skip' if you are stuck).* "
                
                yield {"type": "complete", "data": {
                    "answer": msg,
                    "sources": [],
                    "intent": "GUIDANCE",
                    "suggestions": ["I'm stuck (Skip)", "Can I have a hint?"]
                }}
                return
            else:
                history_manager.update_session_state(username, session_id, {"awaiting_micro_challenge": False})
                if has_code and not any(p in user_input_lower for p in skip_phrases):
                    state.intent = "REVIEW" 
                    pending = current_state.get("pending_goal")
                    if pending:
                        state.query = f"[CONTEXT: I am answering your micro-challenge: '{state.original_query}'] Review my code. If it is correct, please answer my next question: '{pending}'"
                        history_manager.update_session_state(username, session_id, {"pending_goal": None})
                    else:
                        state.query = f"[CONTEXT: I am answering your micro-challenge: '{state.original_query}'] Review my code. Keep it brief."

        # 4. NEW SCAFFOLDING TRIGGERS 
        if not is_in_plan:
            async for event in self.scaffolding.process(state):
                yield event
            if state.stop_processing: return

        # 5. EXAMINER AGENT (Quizzes)
        async for event in self.examiner.process(state):
            yield event
        if state.stop_processing: return

        # 6. CODE REVIEWER AGENT (Reviews)
        async for event in self.reviewer.process(state):
            yield event
        if state.stop_processing: return

        # # 7. CODE REVIEWER AGENT (Reviews)
        # async for event in self.reviewer.process(state):
        #     yield event
        # if state.stop_processing: return

        # ---------------------------------------------------------
        # FINAL FALLBACK: CONCEPT TUTORING
        # ---------------------------------------------------------

        # 7. GATEKEEPER CHECK
        gatekeeper_result = self._check_gatekeeping(
            state.query, state.intent, state.entities, state.user_id, state.session_id
        )
        if gatekeeper_result:
            yield {"type": "complete", "data": gatekeeper_result}
            return

        # 8. SOCRATIC TUTOR (Standard RAG)
        async for event in self.socratic.process(state):
            if event["type"] == "complete":
                event["data"]["entities"] = state.entities 
                event["data"]["intent"] = state.intent
                
                topic_name = state.entities[0] if state.entities else "the last topic"
                history_manager.update_session_state(
                    username, session_id, 
                    {"awaiting_micro_challenge": True, "challenge_topic": topic_name}
                )
            yield event

        # ---------------------------------------------------------
        # POST-PROCESSING (PROFILER & PENDING GOALS)
        # ---------------------------------------------------------

        # Fire-and-forget sentiment analysis
        asyncio.create_task(
            self.profiler.analyze_sentiment(username, query)
        )
        
        if random.random() < 0.1:
            asyncio.create_task(
                self.profiler.update_learning_profile(username)
            )

        # HANDLE THE PENDING GOAL IMMEDIATELY
        pending = current_state.get("pending_goal")
        if pending and not current_state.get("awaiting_micro_challenge") and state.intent == "REVIEW":
            self.logger.info(f"🔄 Executing Pending Goal: {pending}")
            
            history_manager.update_session_state(username, session_id, {"pending_goal": None})
            
            yield {"type": "token", "text": "\n\n---\n\n### Now, back to your question: *" + pending + "*\n\n"}
            yield {"type": "status", "message": "Loading next topic...", "percent": 90}
            
            followup_state = AgentState(
                query=pending,
                original_query=pending,
                user_id=username,
                session_id=session_id,
                user_role=user_role,
                user_goal=user_goal,
                profile=learning_profile,
                intent="CONCEPT" 
            )
            
            if config.INTENT_CLASSIFIER_MODE == "fast":
                followup_state.entities = fast_classifier.extract_entities(pending)
            else:
                followup_state.entities = [pending]
                
            async for follow_event in self.socratic.process(followup_state):
                if follow_event["type"] == "complete":
                    topic_name = followup_state.entities[0] if followup_state.entities else "the last topic"
                    history_manager.update_session_state(
                        username, session_id, 
                        {"awaiting_micro_challenge": True, "challenge_topic": topic_name}
                    )
                    
                    follow_event["data"]["entities"] = followup_state.entities
                    follow_event["data"]["intent"] = "REVIEW_AND_CONCEPT"
                    
                yield follow_event