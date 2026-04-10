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

    async def _contextualize_query(self, current_query, username, session_id):
        if not session_id: return current_query

        session_data = history_manager.get_session_details(username, session_id)
        if not session_data or not session_data.get('messages'): return current_query

        # --- FIX 1: EXCLUDE CURRENT MESSAGE ---
        all_msgs = session_data['messages']
        relevant_msgs = all_msgs[:-1][-5:] 
        
        history_str = ""
        for m in relevant_msgs:
            role = "Student" if m['role'] == 'user' else "Tutor"
            history_str += f"{role}: {m['content']}\n"

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
            
            # --- A1 FIX: STRIP LLM ECHO PATTERNS ---
            for prefix in ["Rewritten Question:", "Rewritten:", "Question:", "Standalone Question:"]:
                if rewritten.lower().startswith(prefix.lower()):
                    rewritten = rewritten[len(prefix):].strip()
            
            # --- A1 FIX: VALIDATE AGAINST KNOWLEDGE GRAPH ---
            rewritten_entities = []
            if config.INTENT_CLASSIFIER_MODE == "fast":
                from app.core.fast_classifier import fast_classifier
                rewritten_entities = fast_classifier.extract_entities(rewritten)
            
            has_valid_entity = False
            if rewritten_entities:
                for entity in rewritten_entities:
                    cypher = "MATCH (n) WHERE toLower(n.name) CONTAINS toLower($name) RETURN n LIMIT 1"
                    try:
                        results = self.graph_db.execute_query(cypher, {"name": entity})
                        if results:
                            has_valid_entity = True
                            break
                    except:
                        has_valid_entity = True  # Permissive on error
                        break
            else:
                has_valid_entity = True  # No entities to check
            
            if not has_valid_entity:
                # SELF-LEARNING: add garbage entities to knowledge manager's blocklist
                for entity in rewritten_entities:
                    if len(entity) >= 3:
                        knowledge_manager._garbage_concepts.add(entity.lower())
                        self.logger.warning(f"🛡️🧠 Contextualization garbage '{entity}' → added to blocklist")
                
                self.logger.warning(f"⚠️ Contextualized query has no graph match: '{rewritten}', using original: '{current_query}'")
                return current_query
            
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

    # def _check_prerequisites(self, query: str, initial_entities: List[str]) -> List[str]:
    #     prereqs = []
    #     for entity in initial_entities:
    #         # NEW CYPHER: Looks for direct prereqs OR prereqs inherited from parent nodes
    #         cypher = """
    #         MATCH (target) 
    #         WHERE toLower(target.name) CONTAINS toLower($name) OR toLower($name) CONTAINS toLower(target.name)
    #         MATCH (target)<-[:INCLUDES*0..1]-(parent)-[:REQUIRES_UNDERSTANDING_OF]->(req)
    #         RETURN req.name as name
    #         """
    #         results = self.graph_db.execute_query(cypher, {"name": entity})
    #         for record in results: 
    #             prereqs.append(record['name'])
    #     return list(set(prereqs))


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
        # 1. Prerequisite Check
        if "anyway" in query.lower() or "i know" in query.lower(): return None # User Override
        
        # Don't check prereqs for simple greetings or non-concept intents
        if intent not in ["CONCEPT", "PROBLEM"]: return None
        
        # Do the Graph Check
        all_prereqs = self._check_prerequisites(query, entities)
        
        # --- C1 FIX: GET PREVIOUSLY VISITED PREREQS TO PREVENT LOOPS ---
        current_state = history_manager.get_session_state(username, session_id) if session_id else {}
        visited_prereqs = current_state.get("visited_prereqs", [])
        visited_lower = [v.lower() for v in visited_prereqs]
        # ---------------------------------------------------------------
        
        target_concepts = [e.lower() for e in entities]
        
        # --- A1 FIX: VALIDATE topic_name (entities[0]) AGAINST GRAPH ---
        # If entities[0] is garbage (e.g. "rewritten", "can"), fix it now.
        topic_name = entities[0] if entities else 'this concept'
        if entities:
            cypher = "MATCH (n) WHERE toLower(n.name) CONTAINS toLower($name) RETURN n.name LIMIT 1"
            try:
                result = self.graph_db.execute_query(cypher, {"name": topic_name})
                if not result:
                    # entities[0] is NOT in the graph — it's garbage.
                    # Try to recover from original query
                    self.logger.warning(f"⚠️ Gatekeeping entity '{topic_name}' not in graph, extracting from original query")
                    import re as _re
                    original_entities = fast_classifier.extract_entities(query) if config.INTENT_CLASSIFIER_MODE == "fast" else [query]
                    if original_entities:
                        entities = original_entities
                        target_concepts = [e.lower() for e in entities]
                        topic_name = entities[0]
                        # Re-validate the recovered entity
                        result2 = self.graph_db.execute_query(cypher, {"name": topic_name})
                        if not result2:
                            self.logger.warning(f"⚠️ Recovered entity '{topic_name}' also not in graph, skipping gatekeeper")
                            return None
            except Exception as e:
                self.logger.error(f"Graph validation error in gatekeeping: {e}")
        # --- END A1 FIX ---
        
        unknown = []
        for p in all_prereqs:
            p_norm = p.lower()
            
            # A. Check Mastery
            if knowledge_manager.has_mastered(username, p): 
                continue
            
            # B. Check Self-Reference (plural handling)
            is_same_topic = False
            for t in target_concepts:
                if t in p_norm or p_norm in t: 
                    is_same_topic = True
                    break
            
            if is_same_topic:
                continue
            
            # C. C1 FIX: Skip prereqs the student was already redirected through
            already_visited = False
            for v in visited_lower:
                if v in p_norm or p_norm in v:
                    already_visited = True
                    break
            
            if already_visited:
                self.logger.info(f"⏭️ Skipping already-visited prereq: '{p}'")
                continue
                
            unknown.append(p)
        
        if unknown:
            # Save the user's goal as a STACK (push, don't overwrite)
            existing_goals = current_state.get("pending_goals", [])
            # Only push if this query isn't already in the stack
            if query not in existing_goals:
                existing_goals.append(query)
            new_visited = visited_prereqs + unknown
            history_manager.update_session_state(username, session_id, {
                "pending_goals": existing_goals,
                "visited_prereqs": new_visited
            })

            # Limit to max 3 prereqs to avoid overwhelming the student
            display_prereqs = unknown[:3]
            
            # Build a clear roadmap instead of a vague dependency chain
            prereq_roadmap = "\n".join(f"   {i+1}. **{p}**" for i, p in enumerate(display_prereqs))
            
            msg = f"## 🧱 Quick Roadmap for **{topic_name}**\n\n"
            msg += f"To make **{topic_name}** click, it helps to know these first:\n\n"
            msg += prereq_roadmap
            msg += f"\n\n💡 You can **skip ahead** if you're comfortable, or I'll walk you through each one quickly!"

            # Put "Teach me anyway" FIRST (most prominent) to reduce frustration
            btns = [f"Teach me {topic_name} anyway"]
            btns += [f"Explain {p}" for p in display_prereqs]
            btns += [f"I know {p} (Verify)" for p in display_prereqs]

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
        # If query produced few entities (1-2), keep original query for better embedding;
        # otherwise join entities to reduce noise from long queries.
        if len(query.split()) > 5 and len(entities) > 2:
            q_search = f"{' '.join(entities)} in C"
        else:
            q_search = query
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
            
        # Create a clean copy for logging so we don't spam the terminal with giant vectors
        log_state = current_state.copy()
        if "quiz_vector" in log_state:
            log_state["quiz_vector"] = "<Vector Data Omitted for Readability>"
            
        self.logger.info(f"🔄 [ORCHESTRATOR] Session: {session_id} | State: {log_state}")
        
        # --- 2. CONTEXT SKIP RULES ---
        is_in_quiz = current_state.get("awaiting_quiz_answer", False)
        active_plan = current_state.get("active_plan", {})
        is_in_plan = active_plan.get("is_active", False)
        is_force_teach = "teach me" in query.lower() and "anyway" in query.lower()
        is_verify_request = "verify" in query.lower() and "know" in query.lower()
        
        # NEW: Don't let the LLM rewrite raw code blocks!
        # If it has brackets or semicolons, it's code, leave it alone.
        is_raw_code = "{" in query or "}" in query or ";" in query
        
        # FIX: Handle "Back to:" pending goal button clicks
        if query.strip().startswith("📌 Back to:"):
            query = query.replace("📌 Back to:", "").strip()
            search_query = query  # Use the cleaned query directly
            # Pop this goal from the stack
            goals_stack = current_state.get("pending_goals", [])
            if query in goals_stack:
                goals_stack.remove(query)
            history_manager.update_session_state(username, session_id, {"pending_goals": goals_stack})
            self.logger.info(f"📌 Resumed pending goal: '{query}'")
        
        # A1 FIX: Skip contextualization for already-complete standalone questions.
        # These patterns are unambiguous and the LLM rewrite only corrupts them.
        q_lower_stripped = query.lower().strip()
        complete_patterns = ("explain", "what is", "what are", "what's", "define",
                            "how do i", "how do you", "how can i", "how to",
                            "tell me about", "describe", "write a", "create a",
                            "build a", "why is", "why does", "fix", "debug")
        is_complete_question = any(q_lower_stripped.startswith(p) for p in complete_patterns)
        
        # FIX: Handle "Rigorous Analysis" button click
        is_rigorous_analysis = q_lower_stripped in ["rigorous analysis", "🧐 rigorous analysis"]
        
        should_skip_context = is_force_teach or is_verify_request or is_in_quiz or is_in_plan or is_raw_code or feedback_mode is not None or is_complete_question or is_rigorous_analysis

        # --- RIGOROUS ANALYSIS HANDLER ---
        if is_rigorous_analysis:
            # Strategy 1: Check session state for stored last code submission
            last_code = current_state.get("last_code_submission") if current_state else None
            if last_code:
                self.logger.info(f"🧐 [Rigorous] Found code via session state (last_code_submission)")
            
            # Strategy 2: Search current state messages
            if not last_code and current_state and current_state.get("messages"):
                for msg in reversed(current_state["messages"]):
                    if msg.get("role") == "user":
                        content = msg.get("content", "")
                        if "{" in content or ";" in content:
                            last_code = content
                            self.logger.info(f"🧐 [Rigorous] Found code via current state messages")
                            break
            
            # Strategy 3: Search full session history (fallback)
            if not last_code and session_id:
                try:
                    sess_details = history_manager.get_session_details(username, session_id)
                    if sess_details and sess_details.get("messages"):
                        for msg in reversed(sess_details["messages"]):
                            if msg.get("role") == "user":
                                content = msg.get("content", "")
                                if "{" in content or ";" in content:
                                    last_code = content
                                    self.logger.info(f"🧐 [Rigorous] Found code via full session history")
                                    break
                except Exception as e:
                    self.logger.error(f"🧐 [Rigorous] Failed to search session history: {e}")
            
            if last_code:
                try:
                    edge_state = AgentState(
                        query=last_code,
                        original_query=query,
                        user_id=username,
                        session_id=session_id,
                        user_role=user_role,
                        user_goal=user_goal,
                        profile={},
                        intent="REVIEW"
                    )
                    async for event in self.reviewer.process_edge_cases(edge_state):
                        yield event
                except Exception as e:
                    self.logger.error(f"🧐 [Rigorous] Edge case analysis failed: {e}")
                    fallback = "### 🧐 Rigorous Analysis\n\nSorry, I ran into an issue analyzing your code. Please try submitting it again and then click Rigorous Analysis."
                    yield {"type": "complete", "data": {"answer": fallback, "sources": [], "intent": "REVIEW"}}
                return
            else:
                self.logger.warning(f"🧐 [Rigorous] No code found in any of the 3 search strategies")
                yield {"type": "complete", "data": {"answer": "I couldn't find a recent code submission to analyze. Submit some code first, then click Rigorous Analysis!", "sources": [], "intent": "GUIDANCE"}}
                return
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
        # Accept BOTH tags
        if query.strip() in ["[INIT_SESSION]", "[NEW_CHAT]"]:
            is_initial_login = (query.strip() == "[INIT_SESSION]")
            
            known_concepts = knowledge_manager.get_known_concepts(username)
            greeting = ""
            suggestions = []
            
            if user_goal:
                goal_lower = user_goal.strip().lower()
                action_verbs = ['build', 'create', 'make', 'write', 'code', 'develop', 'learn', 'master', 'finish', 'complete']
                starts_with_action = any(goal_lower.startswith(verb) for verb in action_verbs)
                if starts_with_action:
                    greeting += f"Your Current Goal is to <b><i>{user_goal}</i></b><br><br>"
                else:
                    greeting += f"Your Current Goal is to master <b><i>{user_goal}</i></b><br><br>"
            
            warmup_topic = None
            if known_concepts:
                last_known = known_concepts[-1] 
                greeting += f"Last time, you successfully mastered <b>{last_known}</b>. Awesome job! 🚀<br>"
                suggestions = [f"Review {last_known}", "Teach me something new", "I need to debug code"]
                
                # ONLY trigger the warmup modal if this is an initial login!
                if is_initial_login:
                    valid_warmups = [c for c in known_concepts if len(c.split()) <= 3 and "?" not in c]
                    
                    if valid_warmups:
                        warmup_topic = random.choice(valid_warmups).title()
                        history_manager.update_session_state(username, session_id, {"awaiting_warmup_topic": warmup_topic})
            else:
                greeting += "We have a blank slate! What topic should we dive into first?"
                suggestions = ["What is a Variable?", "How does C work?", "I need help with an assignment"]
                
            yield {"type": "complete", "data": {
                "answer": greeting, 
                "warmup_topic": warmup_topic, 
                "sources": [], "intent": "GREETING", "suggestions": suggestions, "entities": ["General"], "session_id": session_id
            }}
            return

        # =========================================================
        # --- 1B: WARM-UP GRADER (STRICT TAG) ---
        # =========================================================
        if query.strip().startswith("[WARMUP_ANSWER]"):
            user_ans = query.replace("[WARMUP_ANSWER]", "").strip()
            warmup_topic = current_state.get("awaiting_warmup_topic", "a previous concept")
            history_manager.update_session_state(username, session_id, {"awaiting_warmup_topic": None})

            if "skip" in user_ans.lower() or not user_ans:
                yield {"type": "complete", "data": {"answer": f"No problem! We'll skip the warm-up for now. What would you like to work on?", "sources": [], "intent": "GREETING", "suggestions": ["Teach me something new"]}}
                return

            yield {"type": "status", "message": "Evaluating your memory...", "percent": 50}
            prompt = f"The student was asked to briefly explain '{warmup_topic}' as a brain warm-up. Their answer: '{user_ans}'. Evaluate it in 1-2 friendly, conversational sentences. If correct, praise them. If wrong, gently correct them. End by asking what they want to learn today."
            
            eval_ans = await asyncio.to_thread(self.llm_fast.generate_response, prompt)
            
            yield {"type": "complete", "data": {
                "answer": f"**🧠 Warm-Up Review ({warmup_topic}):**\n\n{eval_ans}", 
                "sources": [], "intent": "GREETING", "suggestions": ["Teach me something new", "I have a specific question"]
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

        # FIX: Don't hijack learning requests OR struggling students with a quiz.
        # Use 'in' instead of 'startswith' to catch rephrased questions like
        # "can you give me just one simple example of a variable?"
        q_lower_quiz = query.lower().strip()
        learning_phrases = (
            "explain", "how do", "how to", "what is", "what are", "tell me",
            "write a", "create a", "build a", "describe", "define", "help",
            "can you", "give me", "show me", "example", "simple",
            "don't understand", "don't get", "confused", "struggling", "teach me"
        )
        is_learning_request = any(p in q_lower_quiz for p in learning_phrases)
        
        if user_msg_count > 0 and user_msg_count % 5 == 0 and not is_in_quiz and not is_in_plan and not is_learning_request:
            self.logger.info("🎯 Triggering Proactive Pop Quiz!")
            
            # FIX: Use ACTUAL mastered concepts from the DB, not bot message metadata.
            # This ensures the quiz is about something the student actually learned.
            known_concepts = knowledge_manager.get_known_concepts(username)
            
            if known_concepts:
                # Pick the most recently mastered concept (last in the list)
                recent_topic = known_concepts[-1]
                
                # Push the current query onto the goals stack
                goals_stack = current_state.get("pending_goals", []) if current_state else []
                if state.query not in goals_stack:
                    goals_stack.append(state.query)
                history_manager.update_session_state(username, session_id, {"pending_goals": goals_stack})
                state.query = f"know {recent_topic} (verify)"
                state.intent = "QUIZ"
                state.entities = [recent_topic]
                state.profile['is_surprise_quiz'] = True
                self.logger.info(f"🎯 Quiz topic from mastery DB: '{recent_topic}'")
            else:
                # No mastered concepts — skip the quiz entirely instead of defaulting to 'Variables'
                self.logger.info("⏭️ Skipping pop quiz: no mastered concepts to quiz on")
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
        if feedback_mode:
            state.intent = "CONCEPT"
            state.entities = [current_state.get("challenge_topic", "C Programming")]
        else:
            async for event in self.sentinel.process(state):
                yield event
            if state.stop_processing: return

        # =========================================================
        # --- FIX 1: STRICT INTENT SANITIZATION ---
        # Prevent the ML Classifier from hallucinating heavy intents on simple text
        # =========================================================
        if state.intent == "REVIEW":
            # If they didn't type a semicolon, bracket, or equals sign, it's NOT a code review.
            has_code_indicators = any(c in state.original_query for c in [";", "{", "}", "="])
            if not has_code_indicators:
                state.intent = "CONCEPT" # Downgrade to standard explanation

        if state.intent == "COMPLEX_PROBLEM":
            # Only trigger complex architecture if they explicitly ask to build something
            is_project_request = any(w in state.original_query.lower() for w in ["build", "create", "project", "app", "game", "system", "calculator"])
            if not is_project_request:
                state.intent = "CONCEPT" # Downgrade to standard explanation

        # 3. THE SOCRATIC LOCK
        if current_state.get("awaiting_micro_challenge") and not feedback_mode:
            user_input_lower = state.original_query.lower()
            challenge_topic = current_state.get("challenge_topic", "that concept")

            # 1. Did they click "Can I have a hint?"
            if "hint" in user_input_lower:
                msg = f"💡 **Hint for {challenge_topic}:**\nLook closely at the 'Example from Class' section above. How did they write the syntax? Give it a try, or click 'Skip' if you are truly stuck!"
                yield {"type": "complete", "data": {"answer": msg, "sources": [], "intent": "GUIDANCE", "suggestions": ["I'm stuck (Skip)"]}}
                return

            # 2. Did they click "I'm stuck (Skip)"?
            if "skip" in user_input_lower or "stuck" in user_input_lower or "idk" in user_input_lower:
                history_manager.update_session_state(username, session_id, {"awaiting_micro_challenge": False})
                goals_stack = current_state.get("pending_goals", [])
                
                msg = "No worries at all! Learning C takes practice. Let's move on."
                if goals_stack:
                    # Pop the most recent goal from the stack
                    pending = goals_stack.pop()
                    msg += f"\n\nNow, back to your question: **{pending}**"
                    state.query = pending
                    state.original_query = pending
                    state.intent = "CONCEPT"
                    history_manager.update_session_state(username, session_id, {"pending_goals": goals_stack})
                    # DO NOT RETURN here, let the code fall through to the Socratic Agent below!
                else:
                    yield {"type": "complete", "data": {"answer": msg + "\nWhat would you like to explore next?", "sources": [], "intent": "GUIDANCE", "suggestions": ["Teach me something new"]}}
                    return

            # 3. Did they try to bypass the challenge by asking a NEW question?
            has_code = any(c in state.original_query for c in [";", "{", "}", "=", "(", ")"])
            is_question = ("?" in state.original_query or state.original_query.lower().startswith(("what", "how", "why")))

            if not has_code and is_question:
                # Push the new question onto the goals stack
                goals_stack = current_state.get("pending_goals", [])
                if state.original_query not in goals_stack:
                    goals_stack.append(state.original_query)
                history_manager.update_session_state(username, session_id, {"pending_goals": goals_stack})
                msg = f"I'd be happy to explain **{state.original_query}** next! \n\nBut first, I want to make sure you understood **{challenge_topic}**. Give my micro-challenge a try! *(Or click 'skip' if you are stuck).* "
                yield {"type": "complete", "data": {"answer": msg, "sources": [], "intent": "GUIDANCE", "suggestions": ["I'm stuck (Skip)", "Can I have a hint?"]}}
                return

            # 4. If they actually submitted an answer (Code or Text)
            history_manager.update_session_state(username, session_id, {"awaiting_micro_challenge": False})

            # Route code to the Reviewer, and plain text to the Socratic Agent
            if has_code:
                state.intent = "REVIEW"
                # FIX: Do NOT merge pending goal into the review prompt.
                # The reviewer will show pending goals as suggestion buttons.
                state.query = f"[CONTEXT: Evaluating micro-challenge answer: '{state.original_query}']. Please review this code. Keep it brief."
            else:
                state.intent = "CONCEPT" 
                state.query = f"[CONTEXT: Evaluating micro-challenge answer: '{state.original_query}']. Please review this briefly."

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

        # =========================================================
        # --- FEATURE 4: SOCRATIC WITHHOLDING ---
        # =========================================================
        known_concepts = knowledge_manager.get_known_concepts(username)
        
        if state.intent in ["CONCEPT", "PROBLEM"]:
            # Safely grab the topic
            topic = state.entities[0] if state.entities else state.original_query
            
            # --- C3 FIX: STRICT MATCHING (exact match, min length, garbage guard) ---
            is_known = False
            matched_concept = ""
            topic_lower = topic.lower().strip()
            
            for k in known_concepts:
                k_lower = k.lower().strip()
                # Require BOTH strings to be meaningful (>= 4 chars)
                # and use exact match or very close match (not arbitrary substring)
                if len(k_lower) >= 4 and len(topic_lower) >= 4:
                    # Exact match OR one completely contains the other (for plurals)
                    if k_lower == topic_lower or (len(k_lower) > 4 and k_lower == topic_lower + 's') or (len(topic_lower) > 4 and topic_lower == k_lower + 's'):
                        is_known = True
                        matched_concept = k
                        break
            # --- END C3 FIX ---

            has_bypassed = current_state.get("bypassed_withholding", False)
            is_asking_for_reminder = any(w in state.original_query.lower() for w in ["remind", "forgot", "explain", "don't remember", "help", "how"])
            
            if is_known and not has_bypassed and not is_asking_for_reminder:
                msg = f"Wait a minute... my records show you already mastered **{matched_concept}**! 😉\n\n"
                msg += "Before I just give you the answer, look back at your code or notes. Based on what we learned before, how do *you* think we should approach this?"
                
                history_manager.update_session_state(username, session_id, {"bypassed_withholding": True})
                
                yield {"type": "complete", "data": {
                    "answer": msg, 
                    "sources": [], 
                    "intent": "GUIDANCE", 
                    "suggestions": [f"I completely forgot {matched_concept}, please remind me.", "Oh right, let me try!"]
                }}
                return
            elif has_bypassed:
                history_manager.update_session_state(username, session_id, {"bypassed_withholding": False})
        # =========================================================

        # 8. SOCRATIC TUTOR (Standard RAG)
        async for event in self.socratic.process(state):
            if event["type"] == "complete":
                event["data"]["entities"] = state.entities 
                event["data"]["intent"] = state.intent
                
                topic_name = state.entities[0] if state.entities else "the last topic"
                
                # Validate topic_name — prevent garbage entities like "can", "give", "just"
                garbage_words = {'can', 'you', 'give', 'show', 'just', 'one', 'simple', 'some',
                                'example', 'want', 'need', 'please', 'help', 'like', 'know',
                                'learn', 'think', 'make', 'write', 'get', 'start', 'where'}
                if topic_name.lower() in garbage_words:
                    # Try to find a real C topic from entities list
                    real_topic = next(
                        (e for e in state.entities if e.lower() not in garbage_words and len(e) > 2),
                        "the last topic"
                    )
                    self.logger.warning(f"⚠️ Rejected garbage challenge_topic '{topic_name}', using '{real_topic}'")
                    topic_name = real_topic
                
                history_manager.update_session_state(
                    username, session_id, 
                    {"awaiting_micro_challenge": True, "challenge_topic": topic_name}
                )
                
                # --- B2 FIX: PERSIST MASTERY AFTER CONCEPT EXPLANATION ---
                if state.intent == "CONCEPT" and state.entities:
                    knowledge_manager.mark_concept_as_known(username, state.entities[0])
                    self.logger.info(f"🧠 Auto-mastered after explanation: {state.entities[0]}")
                # --------------------------------------------------------
            yield event

        # ---------------------------------------------------------
        # POST-PROCESSING (PROFILER & PENDING GOALS)
        # ---------------------------------------------------------

        # Fire-and-forget sentiment analysis
        asyncio.create_task(
            self.profiler.analyze_sentiment(username, query)
        )

        # --- D2 FIX: DON'T APPEND PENDING GOAL INLINE ---
        # Instead of streaming a follow-up topic into the same response,
        # offer it as a suggestion button so the user can choose when to continue.
        goals_stack = current_state.get("pending_goals", [])
        if goals_stack and not current_state.get("awaiting_micro_challenge") and state.intent == "REVIEW":
            self.logger.info(f"📌 Pending goals stack: {goals_stack}")
            # Don't clear — they will be cleared when the user clicks a button