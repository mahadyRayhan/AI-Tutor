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
            # --- NEW FIX: STRIP MARKDOWN ASTERISKS TOO ---
            rewritten = rewritten.strip().replace('"', '').replace('**', '')
            
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
    
    def _get_global_skipped_challenges(self, username: str) -> list:
        row = db.fetch_one("SELECT learning_profile FROM users WHERE username = ?", (username,))
        profile = json.loads(row['learning_profile']) if row and row['learning_profile'] else {}
        return profile.get("skipped_challenges", [])

    def _calculate_code_complexity(self, text: str) -> int:
        """C_{code} = AST_depth proxy. Returns 0 if no code."""
        # Fast heuristic: if no braces or semicolons, it's not code
        if "{" not in text and ";" not in text:
            return 0
        
        complexity = 0
        complexity += text.count("for") + text.count("while") # Loops
        complexity += text.count("if") + text.count("switch") # Branching
        complexity += text.count("*") + text.count("&")       # Pointers/Memory
        complexity += text.count("{")                         # Nesting depth proxy
        return complexity

    def _determine_m_state(self, query_lower: str, intent: str, c_code: int, delta_f: float) -> str:
        """M_{state} = FSM_{metacog}.step(q_t)"""
        helpless_phrases = ["just tell me", "i give up", "i can't do this", "im stuck", "too hard", "idk"]
        
        # Any State -> Helplessness
        if any(p in query_lower for p in helpless_phrases) or delta_f > 0.5:
            return "Helplessness"
        
        # Planning -> Monitoring (If they submit code or ask to debug)
        if intent == "DEBUG" or c_code > 0:
            return "Monitoring"
            
        # Monitoring -> Reflecting (If they summarize/explain)
        reflect_phrases = ["so that means", "basically", "to summarize", "is it like"]
        if intent == "CONCEPT" and any(p in query_lower for p in reflect_phrases):
            return "Reflecting"
            
        return "Planning" # Default state

    def _calculate_goal_alignment(self, query: str, user_goal: str) -> float:
        """s_{goal} = cos(Emb(q), Emb(G_{macro}))"""
        # =========================================================
        # FIX: FAIL-SAFE DEFAULT
        # If no goal is set, alignment is 0.0. Malicious queries are strictly blocked.
        # =========================================================
        if not user_goal or user_goal.strip() == "": 
            return 0.0 
        
        try:
            q_emb = self.llm_fast.get_embedding(query)
            g_emb = self.llm_fast.get_embedding(user_goal)
            if q_emb and g_emb:
                return float(dot(q_emb, g_emb) / (norm(q_emb) * norm(g_emb)))
        except Exception as e:
            self.logger.error(f"Goal Alignment Error: {e}")
        return 0.0 # Neutral fallback changed to 0.0 for strict security

    def _update_global_skipped_challenges(self, username: str, skipped: list):
        row = db.fetch_one("SELECT learning_profile FROM users WHERE username = ?", (username,))
        profile = json.loads(row['learning_profile']) if row and row['learning_profile'] else {}
        profile["skipped_challenges"] = skipped
        db.execute("UPDATE users SET learning_profile = ? WHERE username = ?", (json.dumps(profile), username))
    
    # Sub-topic → canonical concept-node aliases. The Neo4j curriculum stores broad
    # concepts ("Memory Allocation", "Recursion"), but students ask using specific API
    # names ("malloc", "calloc") that aren't node names — so the prereq gate found no
    # node and silently let advanced topics through (Module-B F3-03/F3-04). Map them.
    _CONCEPT_ALIASES = {
        "malloc": "Memory Allocation",
        "calloc": "Memory Allocation",
        "realloc": "Memory Allocation",
        "dynamic memory": "Memory Allocation",
        "dynamic memory allocation": "Memory Allocation",
        "recursion": "Recursion",
        "recursive": "Recursion",
    }

    def _canonicalize_entities(self, entities: List[str]) -> List[str]:
        """Map specific sub-topic names to their canonical curriculum concept node."""
        out = []
        for e in entities or []:
            out.append(self._CONCEPT_ALIASES.get((e or "").lower().strip(), e))
        return out

    def _check_prerequisites(self, query: str, initial_entities: List[str]) -> List[str]:
        # Canonicalize sub-topics first (malloc → Memory Allocation, recursion → Recursion)
        # so they resolve to a concept node that carries a direct prerequisite edge.
        # We match the node's OWN REQUIRES edges only (not a parent Section's) — inheriting
        # Section-level prereqs over-gates foundational concepts like Variables (F2-01).
        initial_entities = self._canonicalize_entities(initial_entities)
        prereqs = []
        for entity in initial_entities:
            cypher = (
                "MATCH (target) "
                "WHERE (toLower(target.name) CONTAINS toLower($name) "
                "       OR toLower($name) CONTAINS toLower(target.name)) "
                "  AND NOT target:Section "
                "MATCH (target)-[:REQUIRES_UNDERSTANDING_OF]->(req) "
                "RETURN DISTINCT req.name as name"
            )
            results = self.graph_db.execute_query(cypher, {"name": entity})
            for record in results:
                prereqs.append(record['name'])
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

        # --- LEARNING UPDATE ---
        # NOTE: self-declaration ("I know variables") is NOT certification. It already
        # bypasses the prerequisite gate for THIS turn (see _check_gatekeeping, which
        # returns None when "i know" is in the query), but it must never write permanent
        # mastery — only BKT certification (bkt.is_mastered) may call mark_concept_as_known.
        # Writing here caused Module-B F3-05/F4-01/F5-02: "mastered" claims the dashboard
        # (BKT) disagreed with. Intentionally left as a no-op.
        if "i know" in query.lower():
            self.logger.info(f"🗣️ {username} self-declared prior knowledge — honored for this turn's gate only, not certified.")

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

    def _check_gatekeeping(self, query, intent, entities, username, session_id, force_bypass=False):
        # 1. Prerequisite Check
        if force_bypass or "anyway" in query.lower() or "i know" in query.lower(): return None # User Override
        
        # Don't check prereqs for simple greetings or non-concept intents
        if intent not in ["CONCEPT", "PROBLEM"]: return None

        # Resolve sub-topic aliases (malloc → Memory Allocation, recursion → Recursion)
        # so both the prereq lookup and the graph-node validation below succeed.
        entities = self._canonicalize_entities(entities)

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

    # =========================================================
    # Mastery Classification (for response adaptation)
    # =========================================================
    def _classify_mastery_level(self, username: str, entities: list) -> tuple:
        """
        Resolve entities to a knowledge-graph concept, read BKT state,
        and classify the student's mastery level for this topic.

        Returns (mastery_level, mastery_detail, resolved_concept_or_None).
        """
        from app.core.bkt_model import _read_row, _apply_decay, _parse_ts, EVIDENCE_CONFIG, get_effective_mastery

        # Step 1: Get canonical concept names from knowledge graph
        try:
            cypher = "MATCH (n) RETURN n.name AS name"
            graph_concepts = [r["name"] for r in self.graph_db.execute_query(cypher, {}) if r.get("name")]
        except Exception:
            graph_concepts = []

        if not graph_concepts:
            return ("novice", "No knowledge graph available", None)

        # Step 2: Entity-to-concept resolution
        resolved = None
        for entity in entities:
            e_lower = entity.lower().strip()

            # Pass 1: Exact case-insensitive match
            for gc in graph_concepts:
                if e_lower == gc.lower():
                    resolved = gc
                    break
            if resolved:
                break

            # Pass 2: Word-boundary fuzzy match (min entity length 3)
            if len(e_lower) < 3:
                continue
            for gc in graph_concepts:
                gc_words = gc.lower().split()
                if any(
                    (w.startswith(e_lower) or e_lower.startswith(w))
                    for w in gc_words if len(w) >= 3
                ):
                    resolved = gc
                    self.logger.info(f"🎯 [MCRA] Fuzzy matched '{entity}' → '{gc}'")
                    break
            if resolved:
                break

        if not resolved:
            return ("novice", f"No matching concept for entities: {entities}", None)

        # Step 3: Read BKT row (try resolved node, then its parent concept)
        row = _read_row(username, resolved)
        if not row:
            # Entity may match a graph sub-node (e.g., "printf") while BKT is
            # stored under the parent concept ("Input Output"). Resolve one level up.
            try:
                parent_q = (
                    "MATCH (parent)-[:INCLUDES]->(n) "
                    "WHERE toLower(n.name) = toLower($name) "
                    "RETURN parent.name AS name LIMIT 1"
                )
                parents = self.graph_db.execute_query(parent_q, {"name": resolved})
                if parents and parents[0].get("name"):
                    parent_name = parents[0]["name"]
                    row = _read_row(username, parent_name)
                    if row:
                        self.logger.info(f"🎯 [MCRA] Resolved via parent: '{resolved}' → '{parent_name}'")
                        resolved = parent_name
            except Exception:
                pass
        if not row:
            return ("novice", f"No prior interactions with '{resolved}'", resolved)

        # Step 4: ever_certified takes priority → reviewing
        if row["ever_certified"]:
            return ("reviewing", f"Previously certified on '{resolved}'", resolved)

        # Step 5: Compute effective mastery (blends BKT + self-assessment for display/adaptation)
        eff = get_effective_mastery(username, resolved)
        p_q, p_m, p_c = eff["quiz"], eff["micro"], eff["code"]

        n_q = row["n_evidence_quiz"] or 0
        n_m = row["n_evidence_micro"] or 0
        n_c = row["n_evidence_code"] or 0
        avg_p = (p_q + p_m + p_c) / 3.0

        detail = (
            f"'{resolved}': avg_P_eff={avg_p:.2f} "
            f"[quiz={p_q:.2f}(n={n_q}), micro={p_m:.2f}(n={n_m}), code={p_c:.2f}(n={n_c})]"
        )

        # Step 6: Classification (uses effective mastery, NOT raw BKT)
        if avg_p >= 0.75 and n_q >= 2 and n_m >= 2 and n_c >= 2:
            return ("proficient", detail, resolved)
        if avg_p >= 0.35 or n_q >= 2 or n_m >= 2 or n_c >= 2:
            return ("developing", detail, resolved)
        return ("novice", detail, resolved)

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
        # if intent == "CONCEPT" and entities:
            #  knowledge_manager.mark_concept_as_known(username, entities[0])

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
        _quiz_kws = ("quiz me", "test me on", "test my knowledge", "give me a quiz",
                     "ask me a question", "challenge me on", "another question", "try another")
        is_quiz_request = any(kw in query.lower() for kw in _quiz_kws)

        # =========================================================
        # FIX: RESTORE ORIGINAL METAPHOR ON BYPASS
        # =========================================================
        force_gatekeeper_bypass = False
        if is_force_teach:
            force_gatekeeper_bypass = True
            goals_stack = current_state.get("pending_goals", [])
            if goals_stack:
                # Pop the original request (e.g., "Explain pointers like a pizza guy")
                original_request = goals_stack.pop() 
                query = original_request
                search_query = original_request
                history_manager.update_session_state(username, session_id, {"pending_goals": goals_stack})
                self.logger.info(f"🔄 [Bypass] Restored original query: '{query}'")
        # =========================================================

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
        
        should_skip_context = is_force_teach or is_verify_request or is_quiz_request or is_in_quiz or is_in_plan or is_raw_code or feedback_mode is not None or is_complete_question or is_rigorous_analysis

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
                    # SM-2: prefer concepts whose spaced-repetition review date has arrived
                    due_concepts = knowledge_manager.get_due_for_review(username)
                    valid_due = [c for c in due_concepts if len(c.split()) <= 3 and "?" not in c]

                    if valid_due:
                        warmup_topic = valid_due[0].title()
                        greeting += f"📅 <b>Spaced Review:</b> It's a good time to revisit <b>{warmup_topic}</b>.<br>"
                    else:
                        valid_warmups = [c for c in known_concepts if len(c.split()) <= 3 and "?" not in c]
                        warmup_topic = random.choice(valid_warmups).title() if valid_warmups else None

                    if warmup_topic:
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
            # Trust gate: only honour this tag if the server actually issued a warm-up.
            # A user typing [WARMUP_ANSWER] with no pending warm-up is treated as plain text.
            pending_warmup = current_state.get("awaiting_warmup_topic")
            if not pending_warmup:
                self.logger.warning(f"[SECURITY] Forged [WARMUP_ANSWER] from {username} with no pending warm-up — treating as plain text.")
                query = query.replace("[WARMUP_ANSWER]", "").strip()
                state.query = query
                # fall through to normal processing (do NOT run the grader, do NOT return)
            else:
                user_ans = query.replace("[WARMUP_ANSWER]", "").strip()
                warmup_topic = pending_warmup
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

        # =========================================================
        # --- GREETING / CASUAL CHAT HANDLER ---
        # Intercept greetings and casual messages BEFORE the heavy pipeline.
        # Prevents "Hello!" from triggering scaffolding, RAG, micro-challenges, etc.
        # =========================================================
        if config.INTENT_CLASSIFIER_MODE == "fast":
            pre_intent = fast_classifier.classify_intent(query)
        else:
            pre_intent = None
        
        _in_active_quiz = current_state.get("awaiting_confidence_rating") or current_state.get("awaiting_quiz_answer")
        if pre_intent == "GREETING" and not _in_active_quiz:
            self.logger.info(f"👋 [GREETING] Intercepted casual message: '{query}'")
            
            # Build a warm, human-like greeting
            q_lower = query.lower().strip().rstrip('!?.,')
            known_concepts = knowledge_manager.get_known_concepts(username)
            
            if q_lower in ("thanks", "thank you", "thx"):
                greeting_msg = "You're welcome! 😊 I'm always here to help. What would you like to learn next?"
            elif q_lower in ("bye", "goodbye", "see you", "see ya"):
                greeting_msg = "Goodbye! 👋 Great studying today. Come back anytime you need help with C programming!"
            else:
                # Standard hello/hi greeting
                greeting_msg = f"Hey there! 👋 Welcome to the C Programming Tutor.\n\n"
                if user_goal:
                    greeting_msg += f"You're working toward: **{user_goal}**.\n\n"
                if known_concepts:
                    greeting_msg += f"Last time you were learning about **{known_concepts[-1]}**. "
                    greeting_msg += "Want to continue, or explore something new?"
                else:
                    greeting_msg += "I'm here to help you learn C programming — ask me anything about variables, loops, arrays, pointers, and more!"
            
            suggestions = []
            if known_concepts:
                suggestions = [f"Review {known_concepts[-1]}", "Teach me something new", "I need help with code"]
            else:
                suggestions = ["What is a Variable?", "How does C work?", "I need help with an assignment"]
            
            yield {"type": "complete", "data": {
                "answer": greeting_msg,
                "sources": [],
                "intent": "GREETING",
                "suggestions": suggestions,
                "entities": ["General"],
            }}
            return
        # =========================================================

        current_frustration = await self.profiler.analyze_sentiment(username, query)
        # --- 4. LOAD PROFILE & SETUP STATE ---
        user_row = db.fetch_one("SELECT learning_profile FROM users WHERE username = ?", (username,))
        learning_profile = json.loads(user_row['learning_profile']) if user_row and user_row['learning_profile'] else {}
        
        # Inject the freshly calculated emotion directly into the profile!
        learning_profile["frustration_level"] = current_frustration

        # --- CALCULATE SENSORY MATH (PHASE 1) ---
        q_lower = query.lower().strip()
        c_code_val = self._calculate_code_complexity(query)
        m_state_val = self._determine_m_state(q_lower, pre_intent or "CONCEPT", c_code_val, current_frustration_delta := learning_profile.get("delta_f", 0.0))
        s_goal_val = self._calculate_goal_alignment(query, user_goal)
        n_strike_val = learning_profile.get("off_topic_strikes", 0)

        self.logger.info(f"📊 [PHASE 1] C_code: {c_code_val} | M_state: {m_state_val} | s_goal: {s_goal_val:.2f} | N_strike: {n_strike_val}")

        # --- Telemetry: affect trajectory (per-turn frustration snapshot) ---
        try:
            from app.core import telemetry
            academic_emotion = getattr(self.profiler, "_last_academic_emotion", None)
            telemetry.log_affect(username, session_id, current_frustration_delta,
                                 str(current_frustration), False, academic_emotion)
        except Exception as e:
            self.logger.warning(f"[Affect] telemetry failed: {e}")

        state = AgentState(
            query=search_query,
            original_query=query, 
            user_id=username,
            session_id=session_id,
            user_role=user_role,
            user_goal=user_goal,
            profile=learning_profile,
            s_goal=s_goal_val,
            c_code=c_code_val,
            m_state=m_state_val,
            delta_f=current_frustration_delta,
            n_strike=n_strike_val
        )

        # =========================================================
        # --- NEW: PENDING CHALLENGE SOLVER INTERCEPTOR ---
        # =========================================================
        is_pending_solve = False
        if query.startswith("[SOLVE_CHALLENGE]"):
            parts = query.replace("[SOLVE_CHALLENGE]", "").split("|", 1)
            solve_topic = parts[0].strip() if len(parts) > 0 else "Concept"
            solve_ans = parts[1].strip() if len(parts) > 1 else ""

            # Trust gate: the topic must actually be a pending challenge for THIS user.
            # A forged/stale [SOLVE_CHALLENGE] must never mark a challenge complete or
            # be routed as a challenge submission.
            skipped = self._get_global_skipped_challenges(username)
            def _is_this_challenge(c):
                return ((isinstance(c, dict) and c.get("topic") == solve_topic)
                        or (isinstance(c, str) and c == solve_topic))
            is_genuinely_pending = any(_is_this_challenge(c) for c in skipped)

            if not is_genuinely_pending:
                self.logger.warning(f"[SECURITY] Forged/stale [SOLVE_CHALLENGE] for '{solve_topic}' from {username} — no such pending challenge. Treating as plain text.")
                query = solve_ans if solve_ans else query.replace("[SOLVE_CHALLENGE]", "").strip()
                state.query = query
                state.original_query = query
                # fall through to normal processing (no completion, no forced REVIEW)
            else:
                # 1. Remove from GLOBAL pending queue (it was genuinely assigned)
                new_skipped = [c for c in skipped if not _is_this_challenge(c)]
                self._update_global_skipped_challenges(username, new_skipped)

                # 2. Force the pipeline state to route to the Code Reviewer
                state.original_query = solve_ans
                state.query = f"[CONTEXT: Evaluating pending micro-challenge for '{solve_topic}'].\n\n{solve_ans}"
                state.intent = "REVIEW"
                state.entities = [solve_topic]
                is_pending_solve = True

                should_skip_context = True # Skip LLM rewrites
        # =========================================================

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
        # --- FLEXIBLE POP QUIZ TRIGGER ---
        is_learning_request = any(p in q_lower_quiz for p in learning_phrases)
        is_frustrated = learning_profile.get("frustration_level") in ["high", "rage"]
        is_coding = state.intent in ["DEBUG", "REVIEW", "PROBLEM"]
        
        # Only quiz if they are in a normal state, not actively coding/debugging, and not asking for help
        should_trigger_quiz = (
            user_msg_count > 0 and 
            user_msg_count % 5 == 0 and 
            not is_in_quiz and 
            not is_in_plan and 
            not is_learning_request and 
            not is_frustrated and 
            not is_coding
        )
        
        if should_trigger_quiz:
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
        elif is_pending_solve:
            state.intent = "REVIEW" # Bypass sentinel to protect forced intent
        else:
            async for event in self.sentinel.process(state):
                yield event
            if state.stop_processing: return
        
        # =========================================================
        # THE FIX: TOPIC AMNESIA CACHE
        # =========================================================
        # 1. Determine if the user provided a real C-concept this turn
        # We ensure it's not just an exact echo of their raw query
        is_real_topic = len(state.entities) > 0 and state.entities[0].lower() != state.original_query.lower()

        if is_real_topic:
            # =========================================================
            # SAGE PDF PAGE 6: TOPIC CACHE ARGMAX
            # Select the most specific/salient entity (proxy: longest string) 
            # rather than just the first one found.
            # e.g., ["int", "Linked List"] -> "Linked List"
            # =========================================================
            best_entity = max(state.entities, key=len)
            
            # Save it to the SQLite session cache!
            history_manager.update_session_state(username, session_id, {"last_valid_topic": best_entity})
            cached_topic = best_entity
            self.logger.info(f"🗂️ [TOPIC CACHE] Saved new anchor topic: {cached_topic}")
            
        else:
            cached_topic = current_state.get("last_valid_topic", "C Programming")
            
            # --- FIX: ONLY OVERRIDE IF VAGUE OR FRUSTRATED ---
            is_vague = len(state.original_query.split()) <= 4
            is_frustrated = learning_profile.get("frustration_level") in ["high", "rage"]
            
            if is_vague or is_frustrated or "confused" in state.original_query.lower():
                state.entities = [cached_topic]
                self.logger.info(f"🧠 [TOPIC CACHE] Overriding vague/emotional query with cached topic: {cached_topic}")
                
                # If they are actively raging/confused, override the RAG search entirely
                if is_frustrated or "confused" in state.original_query.lower():
                    state.query = cached_topic
            else:
                # Let specific, long queries (like "Show me how to write a virus") pass through 
                # so the RAG agent fails naturally and doesn't hallucinate previous topics.
                self.logger.info(f"⏭️ [TOPIC CACHE] Query is specific. Bypassing amnesia cache.")
        # =========================================================
        # =========================================================

        # =========================================================
        # --- FIX 1: STRICT INTENT SANITIZATION ---
        # Prevent the ML Classifier from hallucinating heavy intents on simple text
        # =========================================================
        if state.intent == "REVIEW" and not is_pending_solve:
            # If they didn't type a semicolon, bracket, or equals sign, it's NOT a code review.
            has_code_indicators = any(c in state.original_query for c in [";", "{", "}", "="])
            if not has_code_indicators:
                state.intent = "CONCEPT" # Downgrade to standard explanation

        if state.intent == "COMPLEX_PROBLEM":
            # Only trigger complex architecture if they explicitly ask to build something
            is_project_request = any(w in state.original_query.lower() for w in ["build", "create", "project", "app", "game", "system", "calculator"])
            if not is_project_request:
                state.intent = "CONCEPT" # Downgrade to standard explanation

        # --- NEW: RETRY PENDING CHALLENGE HANDLER ---
        if query.strip().startswith("[RETRY_CHALLENGE]"):
            retry_topic = query.replace("[RETRY_CHALLENGE]", "").strip()
            skipped = current_state.get("skipped_challenges", [])
            
            if retry_topic in skipped:
                skipped.remove(retry_topic)
            
            history_manager.update_session_state(username, session_id, {
                "skipped_challenges": skipped,
                "awaiting_micro_challenge": True,
                "challenge_topic": retry_topic
            })
            
            prompt = f"The student wants to retry their pending micro-challenge about '{retry_topic}'. Write a short, encouraging 1-2 sentence challenge asking them to write 1 line of C code related to {retry_topic}. End your response with the challenge question. Do not provide the answer."
            challenge_msg = await asyncio.to_thread(self.llm_fast.generate_response, prompt)
            
            yield {"type": "complete", "data": {"answer": f"Awesome, let's clear that pending challenge!\n\n{challenge_msg}", "sources": [], "intent": "GUIDANCE", "suggestions": ["I'm stuck (Skip)"]}}
            return

        # 3. THE SOCRATIC LOCK
        if current_state.get("awaiting_micro_challenge") and not feedback_mode and not is_pending_solve:
            challenge_topic = current_state.get("challenge_topic", "that concept")
            challenge_text = current_state.get("challenge_text", f"Write a quick code snippet demonstrating {challenge_topic}.")
            
            # GET GLOBALLY
            skipped_challenges = self._get_global_skipped_challenges(username)
            user_input_lower = state.original_query.lower()

            is_hint = "hint" in user_input_lower
            is_explicit_skip = any(w in user_input_lower for w in ["skip", "stuck", "idk", "don't know", "dont know"])
            
            # INTENT-BASED DETECTION: Did they change the subject?
            is_bypass = False
            if state.intent in ["PROBLEM", "COMPLEX_PROBLEM", "GREETING", "OFF_TOPIC", "SECURITY_RISK"]:
                is_bypass = True
            elif state.intent == "CONCEPT":
                # If they ask a new question (e.g. "what is a loop?"), it's a bypass.
                # If it's just a statement (e.g. "a loop repeats code"), they might be answering.
                question_words = ("what", "how", "why", "can", "explain", "define", "tell")
                if "?" in user_input_lower or user_input_lower.startswith(question_words):
                    is_bypass = True

            # 1. Ask for a Hint
            if is_hint:
                msg = f"💡 **Hint for {challenge_topic}:**\nLook closely at the 'Example from Class' section above. Give it a try, or click 'Skip' if you are stuck!"
                yield {"type": "complete", "data": {"answer": msg, "sources": [], "intent": "GUIDANCE", "suggestions": ["I'm stuck (Skip)"]}}
                return

            # 2. Bypass Detection (Explicit Skip or Changed Subject)
            if is_explicit_skip or is_bypass:
                if len(skipped_challenges) >= 5:
                    msg = f"🛑 **Challenge Limit Reached!**\n\nYou currently have 5 pending micro-challenges in your Task Menu. To ensure we're building a solid foundation, please clear at least one of them before we move on to new topics!"
                    yield {"type": "complete", "data": {"answer": msg, "sources": [], "intent": "GUIDANCE", "suggestions": []}}
                    return
                
                topic_exists = any(isinstance(c, dict) and c.get("topic") == challenge_topic for c in skipped_challenges) or (challenge_topic in skipped_challenges)
                
                if not topic_exists:
                    skipped_challenges.append({
                        "topic": challenge_topic, 
                        "question": challenge_text
                    })
                
                # SAVE GLOBALLY
                self._update_global_skipped_challenges(username, skipped_challenges)
                history_manager.update_session_state(username, session_id, {"awaiting_micro_challenge": False})

                if is_explicit_skip:
                    goals_stack = current_state.get("pending_goals", [])
                    msg = f"No worries! I've saved **{challenge_topic}** to your Pending Tasks menu (top right). We can revisit it later."
                    
                    if goals_stack:
                        pending = goals_stack.pop()
                        msg += f"\n\nNow, back to your question: **{pending}**"
                        state.query = pending
                        state.original_query = pending
                        state.intent = "CONCEPT"
                        history_manager.update_session_state(username, session_id, {"pending_goals": goals_stack})
                        # Let it fall through to Socratic Agent
                    else:
                        msg += "\n\nWhat would you like to explore next?"
                        yield {"type": "complete", "data": {"answer": msg, "sources": [], "intent": "GUIDANCE", "suggestions": ["Teach me something new"]}}
                        return
                else:
                    # Natural Bypass: They asked a new question.
                    # Just let it fall through so the agent answers their new question naturally!
                    self.logger.info(f"⏭️ Bypassed micro-challenge for {challenge_topic}, added to queue.")
                    pass 
            else:
                # 3. They are Answering the Challenge!
                history_manager.update_session_state(username, session_id, {"awaiting_micro_challenge": False})

                # Ensure it routes correctly based on whether they wrote code or text
                has_code = any(c in state.original_query for c in [";", "{", "}", "=", "(", ")"])
                if has_code:
                    state.intent = "REVIEW"
                    state.query = f"[CONTEXT: Evaluating micro-challenge answer: '{state.original_query}']. Please review this code. Keep it brief."
                    # BKT micro evidence: student attempted procedural code → always counts as attempt
                    from app.core.bkt_model import bkt as _bkt_micro
                    _bkt_micro.update(username, challenge_topic, True, evidence_type="micro")
                    self.logger.info(f"📐 [BKT/MICRO] '{challenge_topic}' updated for {username}")
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

        # =========================================================
        # Mastery Classification for response adaptation (must run BEFORE gatekeeper)
        # =========================================================
        if state.intent in ["CONCEPT", "PROBLEM"] and state.entities and username:
            try:
                level, detail, _ = self._classify_mastery_level(username, state.entities)
                state.mastery_level = level
                state.mastery_detail = detail
                self.logger.info(f"🎯 [MCRA] {username}: {level.upper()} | {detail}")
            except Exception as e:
                self.logger.warning(f"[MCRA] Classification failed: {e}")

        # --- Telemetry: response adaptation (mastery level applied to this turn) ---
        if username and state.intent in ["CONCEPT", "PROBLEM"]:
            try:
                from app.core import telemetry
                concept = state.entities[0] if state.entities else ""
                telemetry.log_response(username, session_id, concept,
                                       state.intent, state.mastery_level)
            except Exception as e:
                self.logger.warning(f"[MCRA] response telemetry failed: {e}")

        # 7. GATEKEEPER CHECK (reviewing students bypass — they proved mastery)
        if state.mastery_level != "reviewing":
            gatekeeper_result = self._check_gatekeeping(
                state.query, state.intent, state.entities, state.user_id, state.session_id, force_gatekeeper_bypass
            )
            if gatekeeper_result:
                try:
                    from app.core import telemetry
                    telemetry.log_event(username, "gatekeeper_block", {
                        "entities": state.entities, "intent": state.intent,
                    }, session_id=session_id)
                except Exception:
                    pass
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
            
            if is_known and not has_bypassed and not is_asking_for_reminder and state.mastery_level != "reviewing":
                msg = f"Wait a minute... my records show you already mastered **{matched_concept}**! 😉\n\n"
                msg += "Before I just give you the answer, look back at your code or notes. Based on what we learned before, how do *you* think we should approach this?"
                
                history_manager.update_session_state(username, session_id, {"bypassed_withholding": True})

                try:
                    from app.core import telemetry
                    telemetry.log_event(username, "withholding_fired",
                                        {"concept": matched_concept}, session_id=session_id)
                except Exception:
                    pass

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
                # Telemetry: carry the full sensory/state vector to the turn logger
                event["data"]["_state"] = {
                    "s_goal": state.s_goal, "c_code": state.c_code,
                    "m_state": state.m_state, "delta_f": state.delta_f,
                    "n_strike": state.n_strike, "mastery_level": state.mastery_level,
                }
                
                topic_name = state.entities[0] if state.entities else "the last topic"
                
                # Validate topic_name...
                garbage_words = {'can', 'you', 'give', 'show', 'just', 'one', 'simple'}
                if topic_name.lower() in garbage_words:
                    real_topic = next((e for e in state.entities if e.lower() not in garbage_words and len(e) > 2), "the last topic")
                    topic_name = real_topic
                
                # --- NEW: EXTRACT THE MICRO-CHALLENGE TEXT FROM THE LLM RESPONSE ---
                full_text = event["data"].get("answer", "")
                import re
                # Extract challenge question after the "Your Turn!" header
                match = re.search(r"##\s*(?:Your Turn!|Challenge|Quick Check).*?\n(.*)", full_text, re.IGNORECASE | re.DOTALL)
                extracted_question = match.group(1).strip() if match else ""
                # Fallback covers both: no match AND match with empty capture (LLM stopped early)
                if not extracted_question:
                    extracted_question = f"Try it: write a line of C code that demonstrates `{topic_name}`."
                
                history_manager.update_session_state(
                    username, session_id, 
                    {
                        "awaiting_micro_challenge": True, 
                        "challenge_topic": topic_name,
                        "challenge_text": extracted_question # Save it here!
                    }
                )

                
                # --- B2 FIX: PERSIST MASTERY AFTER CONCEPT EXPLANATION ---
                # if state.intent == "CONCEPT" and state.entities:
                    # knowledge_manager.mark_concept_as_known(username, state.entities[0])
                    # self.logger.info(f"🧠 Auto-mastered after explanation: {state.entities[0]}")
                # --------------------------------------------------------
            yield event

        # ---------------------------------------------------------
        # POST-PROCESSING (PROFILER & PENDING GOALS)
        # ---------------------------------------------------------

        # Fire-and-forget sentiment analysis
        # asyncio.create_task(
        #     self.profiler.analyze_sentiment(username, query)
        # )

        # --- D2 FIX: DON'T APPEND PENDING GOAL INLINE ---
        # Instead of streaming a follow-up topic into the same response,
        # offer it as a suggestion button so the user can choose when to continue.
        goals_stack = current_state.get("pending_goals", [])
        if goals_stack and not current_state.get("awaiting_micro_challenge") and state.intent == "REVIEW":
            self.logger.info(f"📌 Pending goals stack: {goals_stack}")
            # Don't clear — they will be cleared when the user clicks a button