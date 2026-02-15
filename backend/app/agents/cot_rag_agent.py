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

    def _fast_grade_answer(self, student_answer: str, correct_vector_google: List[float], correct_vector_local: List[float] = None) -> Dict:
        """Grades using Vector Similarity (Local Fast Path or Google Slow Path)."""
        
        cos_sim = 0.0
        
        # PATH A: FAST LOCAL CHECK (Preferred)
        if correct_vector_local and config.INTENT_CLASSIFIER_MODE == "fast":
            # Use the already loaded model in FastClassifier to avoid reloading
            from app.core.fast_classifier import fast_classifier
            
            # Embed student answer locally (0.02s)
            student_vec = fast_classifier.intent_model.encode(student_answer)
            
            # Compare against LOCAL reference
            cos_sim = dot(student_vec, correct_vector_local) / (norm(student_vec) * norm(correct_vector_local))
            self.logger.info(f"⚡ Fast Grading Similarity: {cos_sim}")
            
        # PATH B: SLOW GOOGLE CHECK (Fallback)
        else:
            self.logger.info("🐢 Slow Grading (Google API)")
            student_vec = self.llm_interface.get_embedding(student_answer)
            if not student_vec or not correct_vector_google:
                return {"is_correct": False, "feedback": "I couldn't verify that automatically."}
            
            cos_sim = dot(student_vec, correct_vector_google) / (norm(student_vec) * norm(correct_vector_google))

        # --- SCORING LOGIC ---
        threshold = 0.75 
        score_pct = int(cos_sim * 100)
        
        if cos_sim > threshold:
            return {"is_correct": True, "feedback": f"Spot on! (Confidence: {score_pct}%)"}
        else:
            # If close but not quite, give a hint
            if cos_sim > 0.60:
                return {"is_correct": False, "feedback": f"Close ({score_pct}%), but not quite. Check your syntax."}
            return {"is_correct": False, "feedback": f"That doesn't match my records. (Confidence: {score_pct}%)"}

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

    def _clean_guided_visual(self, text: str) -> str:
        """
        Simple sanitizer. 
        Rely on the Prompt to format structure correctly. 
        We just clean up wrappers and enforce quote safety.
        """
        if not text: return ""

        # 1. Remove Markdown wrappers
        text = text.replace("```mermaid", "").replace("```", "").strip()

        # 2. Fix the specific error: "Node'Label'" -> "Node[Label]"
        # If the LLM generates ID'Label', convert to ID[Label]
        # This handles the case where it avoids quotes but uses single quotes as wrappers
        text = re.sub(r"(\w+)'([^']+)'", r'\1[\2]', text)

        # 3. Final Safety: If brackets contain quotes, strip them.
        # Matches: [ "Content" ] -> [ Content ]
        def strip_inner_quotes(match):
            content = match.group(1)
            clean_content = content.replace('"', '').replace("'", "")
            return f"[{clean_content}]"
            
        text = re.sub(r'\[(.*?)\]', strip_inner_quotes, text)
        
        return text

    def _expand_query_using_graph(self, query: str, initial_entities: List[str]) -> List[str]:
        expanded_terms = []
        for entity in initial_entities:
            cypher = "MATCH (target) WHERE toLower(target.name) CONTAINS toLower($name) MATCH (target)-[:REQUIRES_UNDERSTANDING_OF]->(req) RETURN req.name as name"
            results = self.graph_db.execute_query(cypher, {"name": entity})
            for record in results: expanded_terms.append(record['name'])
        return list(set(expanded_terms))

    def _execute_retrieval(self, query: str, intent: str, user_role: str = 'student', existing_entities: List[str] = None) -> List[Dict[str, Any]]:
        if existing_entities:
            entities = existing_entities
        else:
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
        3. **TEXT PRIORITY:** Be CONCISE. Max 2 short paragraphs for the explanation. No fluff.
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
            6. **GOAL CONNECTION (CRITICAL):** The student's goal is: "{user_goal}". 
               - You MUST explicitly explain how the current concept helps them achieve "{user_goal}".
            """
        return f"""
        You are an expert C Programming Tutor.
        
        Student Query: "{query}"
        User's Goal: "{user_goal if user_goal else 'None'}"
        Reference Material: {context}

        **MANDATORY RULES:**
        1. **STRICT LIMITATION:** Check the Reference Material. If the concept is NOT present, say: "I don't have information..."
        2. **PERSONALIZATION:** Acknowledge known concepts from USER CONTEXT.
        3. **TEXT PRIORITY:** Clear text explanation FIRST (min 3 sentences). Use analogies.
        4. **VISUALIZATION:** Generate a Mermaid.js diagram (`graph TD`).
           - **CRITICAL SYNTAX:** 
             - ABSOLUTELY NO PARENTHESES `()` inside node labels. 
             - ABSOLUTELY NO BRACKETS `[]` inside node labels.
             - ABSOLUTELY NO QUOTES `"` inside node labels.
             - **GOOD:** `A[Start] --> B[Declare Array]`
        5. **SOURCE GROUNDING:** Quote specific examples from text.
        6. **GOAL ALIGNMENT:** If the user has a goal, you MUST explain how this concept applies to it.
        
        **STRICT RESPONSE FORMAT:**
        
        ## Explanation
        [Text explanation...]
        
        ## Use Cases
        [Why use this...]

        {goal_section}

        ## Visual Model
        ```mermaid
        graph TD
           ...
        ```
        
        ## Example from Class
        [Reference specific code from text]
        """

    def _build_concept_prompt(self, query: str, context: str, user_goal: str = None) -> str:
        goal_section = ""
        if user_goal:
            goal_section = f"""
            6. **GOAL CONNECTION (CRITICAL):** The student's goal is: "{user_goal}". 
               - You MUST explicitly explain how the current concept helps them achieve "{user_goal}".
            """
        return f"""
        You are an expert C Programming Tutor.
        
        Student Query: "{query}"
        User's Goal: "{user_goal if user_goal else 'None'}"
        Reference Material: {context}

        **MANDATORY RULES:**
        1. **STRICT LIMITATION:** Check the Reference Material. If the concept is NOT present, say: "I don't have information..."
        2. **PERSONALIZATION:** Acknowledge known concepts from USER CONTEXT.
        3. **TEXT PRIORITY:** Clear text explanation FIRST (min 3 sentences). Use analogies. **Explicitly explain Use Cases.**
        4. **VISUALIZATION:** Generate a Mermaid.js diagram (`graph TD`) if the concept involves flow/structure.
           - **STRICT SYNTAX:** Use square brackets for labels: `A["Label"]`. Do NOT use single quotes.
        5. **SOURCE GROUNDING:** Quote specific examples from text.
        6. **GOAL ALIGNMENT:** If the user has a goal, you MUST explain how this concept applies to it.
        
        **STRICT RESPONSE FORMAT:**
        
        ## Explanation
        [Start by bridging from known concepts if applicable. Then explain the new concept using text and analogies.]
        
        ## Use Cases
        [Explain WHEN and WHY this concept is used in real programming.]

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

    def _generate_step_by_step_plan(self, query: str, context: str) -> List[Dict[str, str]]:
        prompt = f"""
        You are a C Programming Curriculum Designer.
        Problem: "{query}"
        Context: {context}

        **TASK:** Break this problem down into small, logical coding steps for a beginner.
        DO NOT WRITE CODE. Just define the sub-goals.

        **Requirements:**
        1. 3 to 6 steps max.
        2. Start with Data Structure/Variables.
        3. End with Printing Output.
        4. Each step must be a specific, verifiable task (e.g., "Write the function signature for Average").

        **OUTPUT FORMAT:**
        Strictly a JSON list of objects:
        [
            {{"goal": "Brief title of step", "description": "What the student needs to do now", "verification_criteria": "What to check for"}}
        ]
        """
        response = self.llm_interface.generate_response(prompt)
        try:
            clean_json = response.replace("```json", "").replace("```", "").strip()
            # Find first [ and last ]
            start = clean_json.find('[')
            end = clean_json.rfind(']') + 1
            return json.loads(clean_json[start:end])
        except Exception as e:
            self.logger.error(f"Plan generation failed: {e}")
            return [{"goal": "Solve the problem", "description": "Let's write the code together.", "verification_criteria": "Code validity"}]

    def _evaluate_step_progress(self, user_input: str, current_step: Dict, context: str) -> Dict[str, Any]:
        prompt = f"""
        You are a C Tutor guiding a student.
        
        **Step Goal:** {current_step['goal']}
        **Task:** {current_step['description']}
        
        **Student Input:** "{user_input}"
        **Reference Material:** {context}

        **TASK:** Evaluate the student's input.

        **OUTPUT RULES:**
        1. **STATUS:** "PASS" if correct. "FAIL" if wrong. "QUESTION" if they ask for help.
        2. **FEEDBACK:** Be encouraging. If they failed, explain WHY based on the Criteria.
        3. **VISUAL AID (CRITICAL):** 
           - IF the student is confused, provide a MermaidJS graph string in `visual_aid`.
           - **CRITICAL SANITIZATION RULES:** 
             - ABSOLUTELY NO PARENTHESES `()` inside node labels. 
             - ABSOLUTELY NO BRACKETS `[]` inside node labels.
             - ABSOLUTELY NO QUOTES `"` inside node labels.
             - **BAD:** `A[sum(a,b)]` or `B{{arr[i]}}` or `C["Text"]`
             - **GOOD:** `A[sum a b]` or `B{{arr index i}}` or `C[Text]`
        4. **PSEUDOCODE:**
            - IF the student is stuck on syntax or asks for "Logic/Pseudocode", fill the `pseudocode_hint` field.
            - Format: Plain text algorithm (e.g., "FOR i FROM 0 TO N..."). Do not use C syntax.

        **OUTPUT JSON:**
        {{
            "status": "PASS" | "FAIL" | "QUESTION",
            "feedback": "Encouraging response...",
            "visual_aid": "graph TD...", 
            "pseudocode_hint": "..."
        }}
        """
        response = self.llm_interface.generate_response(prompt)
        try:
            clean_json = response.replace("```json", "").replace("```", "").strip()
            start = clean_json.find('{')
            end = clean_json.rfind('}') + 1
            return json.loads(clean_json[start:end])
        except:
            return {"status": "FAIL", "feedback": "I couldn't verify that automatically."}

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
    # async def run_stream(self, query: str, user_role: str = 'student', **kwargs):
    #     import time
    #     t_start = time.time()
    #     profiler = {}
    #     username = kwargs.get('username', 'anonymous')
    #     user_goal = kwargs.get('user_goal')
    #     session_id = kwargs.get('session_id')
        
    #     # 1. DETECT FORCED TEACHING (Button Click)
    #     is_force_teach = "teach me" in query.lower() and "anyway" in query.lower()

    #     # 2. DETECT VERIFY REQUEST (Button Click)
    #     is_verify_request = "verify" in query.lower() and "know" in query.lower()
    #     verify_topic = ""
    #     if is_verify_request:
    #         match = re.search(r"know (.*?) \(verify\)", query.lower())
    #         if match: verify_topic = match.group(1)

    #     # 3. DETECT CONTEXTUAL STATE (Is user answering a check?)
    #     # --- PASTE THE BLOCK HERE ---
    #     is_answering_check = False
    #     check_topic = ""
    #     correct_vector = []
    #     correct_vector_local = None # Initialize this
        
    #     if session_id:
    #         # Try loading state from DB first (Robust)
    #         state = history_manager.get_session_state(username, session_id)
    #         if state.get("awaiting_quiz_answer"):
    #             is_answering_check = True
    #             check_topic = state.get("quiz_topic")
                
    #             # Load vectors from State
    #             vectors = state.get("quiz_vector")
    #             if isinstance(vectors, list):
    #                 correct_vector = vectors
    #                 correct_vector_local = None
    #             elif isinstance(vectors, dict):
    #                 correct_vector = vectors.get('google')
    #                 correct_vector_local = vectors.get('local')
                
    #             # Clear state immediately
    #             history_manager.update_session_state(username, session_id, {"awaiting_quiz_answer": False})

    #         # Fallback: Parse Chat History (Legacy / UI safety)
    #         elif not is_answering_check:
    #             session_data = history_manager.get_session_details(username, session_id)
    #             if session_data and session_data.get('messages'):
    #                 last_msg = session_data['messages'][-1]
                    
    #                 # --- PASTE START ---
    #                 if last_msg['role'] == 'bot':
    #                     # Regex for: [Check: Topic | {JSON}]
    #                     match = re.search(r"\[Check: (.*?) \| (.*?)\]", last_msg['content'])
                        
    #                     if match:
    #                         is_answering_check = True
    #                         check_topic = match.group(1)
    #                         try:
    #                             # Parse the JSON vector data
    #                             vectors = json.loads(match.group(2))
                                
    #                             # Handle Backward Compatibility
    #                             if isinstance(vectors, list):
    #                                 correct_vector = vectors # Old style (Google only)
    #                                 correct_vector_local = None
    #                             else:
    #                                 # New data is a Dict
    #                                 correct_vector = vectors.get('google')
    #                                 correct_vector_local = vectors.get('local')
                                    
    #                         except Exception as e:
    #                             self.logger.error(f"Vector parse error: {e}")
    #                             is_answering_check = False
        
    #     # =========================================================
    #     # 0. CONTEXTUALIZATION (Session Context)
    #     # =========================================================
    #     yield {"type": "status", "message": "Understanding context...", "percent": 5}
    #     t0 = time.time()
        
    #     # Skip context rewrite if it's a button click (Teach me X / I know X)
    #     if not is_force_teach and not is_verify_request:
    #         search_query = await self._contextualize_query(query, username, session_id)
    #     else:
    #         search_query = query 

    #     profiler["0_Context"] = time.time() - t0

    #     # =========================================================
    #     # 1 & 2. ANALYSIS
    #     # =========================================================
    #     yield {"type": "status", "message": "Analyzing query...", "percent": 15}
    #     t0 = time.time()

    #     intent = ""
    #     entities = []

    #     if is_answering_check:
    #         self.logger.info(f"📝 User is answering a quiz about {check_topic}. Skipping classification.")
    #         intent = "EVALUATION" # Dummy intent
    #         # We don't need entities here because Path A handles it
    #     elif config.INTENT_CLASSIFIER_MODE == "fast":
    #         if is_force_teach:
    #             # FIX: Regex extract strictly the topic from "Teach me [TOPIC] anyway"
    #             match = re.search(r"teach me (.*?) anyway", query.lower())
    #             if match:
    #                 raw_topic = match.group(1).replace(".", "").replace("?", "").strip()
    #                 entities = [raw_topic]
    #             else:
    #                 entities = [query]
    #             intent = "CONCEPT" 
    #         elif is_verify_request:
    #             # Verify request doesn't need deep classification
    #             entities = [verify_topic]
    #             intent = "QUIZ"
    #         else:
    #             intent = fast_classifier.classify_intent(search_query) 
    #             entities = fast_classifier.extract_entities(search_query)
    #             # Fallback extraction
    #             if not entities:
    #                 words = re.findall(r'\b\w+\b', search_query.lower())
    #                 stopwords_list = {'what', 'is', 'the', 'how', 'do', 'i', 'it', 'its', 'use', 'case', 'show', 'me', 'tell', 'explain', 'teach', 'anyway', 'does'}
    #                 entities = [w for w in words if w not in stopwords_list and len(w) > 2]
    #                 entities = entities[:3]
    #     else:
    #         intent = self._classify_intent(search_query)
    #         entities = [search_query] 

    #     # REWRITE FOR GENERATION (Existing Logic)
    #     if is_force_teach:
    #         main_topic = entities[0] if entities else "this concept"
    #         search_query = f"Explain {main_topic} and its specific use cases in C programming."

    #     profiler["1_Analysis"] = time.time() - t0
    #     self.logger.info(f"Query: {query} | Rewritten: {search_query} | Entities: {entities}")

    #     # --- SECURITY BLOCK (Existing) ---
    #     if intent == "SECURITY_RISK":
    #         msg = "⛔ **Security Alert**: This request violates safety policies."
    #         yield {"type": "complete", "data": {
    #             "answer": msg, "sources": [], "suggestions": [], "intent": intent
    #         }}
    #         return

    #     # =========================================================
    #     # ### NEW PATH A: FAST GRADING (Vector Similarity) ###
    #     # =========================================================
    #     if is_answering_check and correct_vector:
    #         self.logger.info(f"📝 Grading answer for topic: {check_topic}")
    #         yield {"type": "status", "message": "Verifying answer...", "percent": 30}
            
    #         # Fast Grade
    #         result = self._fast_grade_answer(query, correct_vector, correct_vector_local)
            
    #         if result['is_correct']:
    #             knowledge_manager.mark_concept_as_known(username, check_topic)
    #             msg = f"✅ **{result['feedback']}**\n\nGreat! You've mastered **{check_topic}**. What's next?"
    #             # Suggestions
    #             yield {"type": "complete", "data": {
    #                 "answer": msg, "sources": [], "suggestions": ["What should I learn next?", "Teach me the original topic"], "intent": "EVALUATION"
    #             }}
    #         else:
    #             msg = f"❌ **{result['feedback']}**\n\nLet's review **{check_topic}** to make sure you have the basics down."
    #             yield {"type": "complete", "data": {
    #                 "answer": msg, "sources": [], "suggestions": [f"Explain {check_topic}"], "intent": "EVALUATION"
    #             }}
            
    #         # CRITICAL: RETURN HERE so we don't run the rest of the function
    #         return

    #     # =========================================================
    #     # ### NEW PATH B: FAST QUIZ FETCH (Neo4j) ###
    #     # =========================================================
    #     if is_verify_request and verify_topic:
    #         yield {"type": "status", "message": "Fetching quiz...", "percent": 50}
            
    #         # Fetch Q&A data
    #         cypher = "MATCH (n:Concept) WHERE toLower(n.name) CONTAINS toLower($topic) RETURN n.quiz_data as data LIMIT 1"
    #         results = self.graph_db.execute_query(cypher, {"topic": verify_topic})
            
    #         qa_pair = None
    #         if results and results[0]['data']:
    #             try:
    #                 pairs = json.loads(results[0]['data'])
    #                 qa_pair = random.choice(pairs)
    #             except: pass
            
    #         if qa_pair:
    #             # Update Session State (Persistence)
    #             # We save the full vector dict so we can use either
    #             vector_data = {
    #                 "google": qa_pair['a_vector'],
    #                 "local": qa_pair.get('a_vector_local')
    #             }
                
    #             history_manager.update_session_state(username, session_id, {
    #                 "awaiting_quiz_answer": True,
    #                 "quiz_topic": verify_topic,
    #                 "quiz_vector": vector_data # Save dict
    #             })
                
    #             # Embed vectors in hidden tag for Regex fallback
    #             vec_str = json.dumps(vector_data)
                
    #             msg = f"🧐 **Quick Check:** {qa_pair['q']}\n\n👉 **Type your answer in the chat box below.**\n\n<span style='display:none'>[Check: {verify_topic} | {vec_str}]</span>"
                
    #             yield {"type": "complete", "data": {
    #                 "answer": msg, 
    #                 "sources": [], 
    #                 "suggestions": ["I don't know"], 
    #                 "intent": "QUIZ"
    #             }}
    #             return

    #     # --- GATEKEEPER (Existing) ---
    #     from app.core.settings_manager import settings_manager
    #     topic_settings = settings_manager.get_settings()
    #     for entity in entities:
    #         for t, on in topic_settings.items():
    #             if (entity.lower() in t.lower()) and not on:
    #                 msg = f"🔒 Topic **{t}** is locked by the teacher."
    #                 yield {"type": "complete", "data": {
    #                     "answer": msg, "sources": [], "suggestions": [], "intent": intent
    #                 }}
    #                 return

    #     # =========================================================
    #     # 3. PREREQUISITE CHECK (Existing + Fixes)
    #     # =========================================================
    #     yield {"type": "status", "message": "Checking prerequisites...", "percent": 40}
    #     t_graph = time.time()
        
    #     user_override = "anyway" in query.lower() or "i know" in query.lower()
        
    #     # Check if already mastered
    #     target_already_known = False
    #     if entities:
    #         for ent in entities:
    #             if knowledge_manager.has_mastered(username, ent):
    #                 target_already_known = True
    #                 self.logger.info(f"✅ User already mastered '{ent}'. Skipping prereq check.")
    #                 break

    #     should_check_prereqs = not user_override and not target_already_known
        
    #     if (intent == "CONCEPT" or intent == "PROBLEM") and should_check_prereqs:
    #         all_prereqs = self._check_prerequisites(search_query, entities)
            
    #         current_topics_lower = [e.lower() for e in entities]
    #         unknown = []
    #         for p in all_prereqs:
    #             is_self = False
    #             for curr in current_topics_lower:
    #                 if curr in p.lower() or p.lower() in curr:
    #                     is_self = True
    #                     break
    #             if not is_self and not knowledge_manager.has_mastered(username, p):
    #                 unknown.append(p)
            
    #         if unknown:
    #             msg = f"## 🛑 Hold on!\n\nYou need to understand **{', '.join(unknown)}** before tackling **{entities[0]}**."
    #             topic_label = entities[0] if entities else "this concept"
                
    #             # Dynamic Buttons
    #             btns = []
    #             for p in unknown:
    #                 btns.append(f"Explain {p}")
    #                 btns.append(f"I know {p} (Verify)") # <--- TRIGGERS PATH B
    #             btns.append(f"Teach me {topic_label} anyway") 
                
    #             yield {"type": "complete", "data": {
    #                 "answer": msg, 
    #                 "sources": [], 
    #                 "suggestions": btns, 
    #                 "intent": "GUIDANCE"
    #             }}
    #             return
        
    #     profiler["3_Graph"] = time.time() - t_graph

    #     # =========================================================
    #     # ### NEW LOGIC: SOCRATIC INTERVENTION FOR PROBLEMS ###
    #     # =========================================================
    #     # If user asks "How to write a loop?", ask them to check understanding first.
    #     if intent == "PROBLEM" and not is_force_teach and not target_already_known:
    #          main_topic = entities[0] if entities else "this concept"
             
    #          # Fetch quiz data
    #          cypher = "MATCH (n:Concept) WHERE toLower(n.name) CONTAINS toLower($topic) RETURN n.quiz_data as data LIMIT 1"
    #          results = self.graph_db.execute_query(cypher, {"topic": main_topic})
             
    #          if results and results[0]['data']:
    #             try:
    #                  pairs = json.loads(results[0]['data'])
    #                  qa_pair = random.choice(pairs)
                     
    #                  # Save State
    #                  history_manager.update_session_state(username, session_id, {
    #                     "awaiting_quiz_answer": True,
    #                     "quiz_topic": main_topic,
    #                     "quiz_vector": qa_pair['a_vector']
    #                  })
                     
    #                  msg = f"I can help you with that! But first, to guide you best, tell me:\n\n**{qa_pair['q']}**"
    #                  yield {"type": "complete", "data": {
    #                      "answer": msg, "sources": [], "suggestions": ["I don't know", f"Teach me {main_topic} anyway"], "intent": "SOCRATIC"
    #                  }}
    #                  return
    #             except: pass # Fallback to standard answer if json fails

    #     # =========================================================
    #     # 4. RETRIEVAL (Existing)
    #     # =========================================================
    #     yield {"type": "status", "message": "Searching knowledge base...", "percent": 60}
    #     t0 = time.time()
        
    #     q_search = f"{' '.join(entities)} in C" if len(search_query.split()) > 5 else search_query
        
    #     # Use existing_entities optimization
    #     chunks = self._execute_retrieval(q_search, intent, user_role, existing_entities=entities)
    #     profiler["4_Retrieval"] = time.time() - t0
        
    #     if not chunks:
    #          msg = f"I don't have specific info on {entities[0] if entities else 'that'}."
    #          yield {"type": "complete", "data": {
    #              "answer": msg, "sources": [], "suggestions": [], "intent": intent
    #          }}
    #          return

    #     # =========================================================
    #     # 5. GENERATION (Existing)
    #     # =========================================================
    #     yield {"type": "status", "message": "Drafting response...", "percent": 80}
    #     t0 = time.time()

    #     # 1. BUILD CONTEXT
    #     known_concepts = knowledge_manager.get_known_concepts(username)
    #     user_context_str = f"USER CONTEXT: The student already knows: {', '.join(known_concepts)}." if known_concepts else ""
    #     context_text = f"{user_context_str}\n\n"
    #     for c in chunks:
    #         context_text += f"--- Source: {c.get('metadata', {}).get('document_name')} ---\n{c.get('text')}\n\n"

    #     # 2. START SUGGESTIONS
    #     suggest_task = asyncio.create_task(
    #         asyncio.to_thread(self._generate_suggestions, search_query, context_text)
    #     )

    #     # 3. BUILD PROMPT STRING
    #     final_prompt = ""
    #     if intent == "REVIEW": 
    #         final_prompt = self._build_review_prompt(search_query, context_text, user_goal)
    #     elif intent == "PROBLEM": 
    #         final_prompt = self._build_socratic_plan_prompt(search_query, context_text, user_goal)
    #     else: 
    #         final_prompt = self._build_concept_prompt(search_query, context_text, user_goal)

    #     yield {"type": "status", "message": "Generating...", "percent": 100}
        
    #     full_answer = ""
        
    #     # 4. STREAMING CALL
    #     async for token in self.llm_interface.stream_response_async(final_prompt):
    #         full_answer += token
    #         yield {"type": "token", "text": token}

    #     suggestions = await suggest_task
    #     profiler["5_Gen"] = time.time() - t0
        
    #     # 5. AUTO-UPDATE KNOWLEDGE
    #     if intent == "CONCEPT":
    #         for entity in entities:
    #             if len(entity) > 2 and entity.lower() not in ["teach", "anyway", "me", "show", "tell", "explain"]:
    #                 knowledge_manager.mark_concept_as_known(username, entity)
    #                 self.logger.info(f"📚 Auto-Learned: {username} now knows {entity}")

    #     full_answer = self._sanitize_mermaid(full_answer)
    #     formatted_sources = [{'document_name': c['metadata']['document_name'], 'chunk_text': c['text']} for c in chunks]

    #     yield {
    #         "type": "complete",
    #         "data": {
    #             "answer": full_answer,
    #             "sources": formatted_sources,
    #             "suggestions": suggestions,
    #             "intent": intent,
    #             "timings": profiler,
    #             "session_id": session_id,
    #             "detected_entity": entities[0] if entities else "General"
    #         }
    #     }

    async def _trigger_quiz_mode(self, query, username, session_id):
        """
        Fetches a pre-calculated quiz from the Knowledge Graph and serves it to the user.
        """
        # Extract topic from "I know X (Verify)"
        match = re.search(r"know (.*?) \(verify\)", query.lower())
        verify_topic = match.group(1).strip() if match else "this concept"

        yield {"type": "status", "message": "Fetching quiz...", "percent": 50}

        # Fetch Q&A data from Neo4j
        cypher = "MATCH (n:Concept) WHERE toLower(n.name) CONTAINS toLower($topic) RETURN n.quiz_data as data LIMIT 1"
        results = self.graph_db.execute_query(cypher, {"topic": verify_topic})

        qa_pair = None
        if results and results[0]['data']:
            try:
                pairs = json.loads(results[0]['data'])
                qa_pair = random.choice(pairs)
            except: pass

        if qa_pair:
            # Update Session State (Persistence)
            vector_data = {
                "google": qa_pair['a_vector'],
                "local": qa_pair.get('a_vector_local')
            }

            history_manager.update_session_state(username, session_id, {
                "awaiting_quiz_answer": True,
                "quiz_topic": verify_topic,
                "quiz_vector": vector_data
            })

            msg = f"🧐 **Quick Check:** {qa_pair['q']}\n\n👉 **Type your answer in the chat box below.**"

            yield {"type": "complete", "data": {
                "answer": msg,
                "sources": [],
                "suggestions": ["I don't know"],
                "intent": "QUIZ"
            }}
        else:
            # Fallback if no quiz exists for this topic
            yield {"type": "complete", "data": {
                "answer": f"I don't have a specific quiz for **{verify_topic}** yet. Would you like me to explain it instead?",
                "sources": [],
                "suggestions": [f"Explain {verify_topic}"],
                "intent": "QUIZ"
            }}

    async def _handle_quiz_response(self, query, current_state, username, session_id):
        """
        Grades the user's answer to the active quiz using Vector Similarity.
        """
        check_topic = current_state.get("quiz_topic")
        vectors = current_state.get("quiz_vector")

        correct_vector = []
        correct_vector_local = None

        if isinstance(vectors, list):
            correct_vector = vectors
        elif isinstance(vectors, dict):
            correct_vector = vectors.get('google')
            correct_vector_local = vectors.get('local')

        # Clear state immediately (so they don't get stuck in quiz mode)
        history_manager.update_session_state(username, session_id, {"awaiting_quiz_answer": False})

        if correct_vector:
            self.logger.info(f"📝 Grading answer for topic: {check_topic}")
            yield {"type": "status", "message": "Verifying answer...", "percent": 30}

            # Fast Grade
            result = self._fast_grade_answer(query, correct_vector, correct_vector_local)

            if result['is_correct']:
                knowledge_manager.mark_concept_as_known(username, check_topic)
                msg = f"✅ **{result['feedback']}**\n\nGreat! You've mastered **{check_topic}**. What's next?"
                yield {"type": "complete", "data": {
                    "answer": msg, "sources": [], "suggestions": ["What should I learn next?", f"Teach me {check_topic} anyway"], "intent": "EVALUATION"
                }}
            else:
                msg = f"❌ **{result['feedback']}**\n\nLet's review **{check_topic}** to make sure you have the basics down."
                yield {"type": "complete", "data": {
                    "answer": msg, "sources": [], "suggestions": [f"Explain {check_topic}"], "intent": "EVALUATION"
                }}
        else:
             yield {"type": "complete", "data": {
                "answer": "Error retrieving quiz data. Let's move on.", "sources": [], "intent": "ERROR"
            }}
    
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

    # -------------------------------------------------------
    # LOGIC FOR GUIDED PLAN (NEW)
    # -------------------------------------------------------
    async def _trigger_guided_plan(self, query, intent, entities, user_role, username, session_id):
        yield {"type": "status", "message": "Planning & Searching...", "percent": 30}
        
        # --- PARALLEL EXECUTION START ---
        # We start both tasks simultaneously.
        
        # Task 1: Retrieve Documents (Fast due to Fix 1)
        # Note: We wrap synchronous methods in to_thread
        retrieval_task = asyncio.create_task(
            asyncio.to_thread(self._execute_retrieval, query, intent, user_role, existing_entities=entities)
        )

        # Task 2: Generate Plan (The slow 9s part)
        # We pass a placeholder context initially because the LLM knows C programming basics without reading your specific docs.
        # This sacrifice allows us to run this NOW rather than waiting for retrieval.
        plan_task = asyncio.create_task(
            asyncio.to_thread(self._generate_step_by_step_plan, query, context="Standard C Programming Context")
        )

        # Wait for both
        chunks, steps = await asyncio.gather(retrieval_task, plan_task)
        # --- PARALLEL EXECUTION END ---

        # (Optional) If you really want context-aware planning, you can't parallelize fully,
        # but usually, "Break down a problem" doesn't require reading the specific textbook files 
        # unless your curriculum is very unique.

        # Save State
        new_plan = {
            "is_active": True,
            "original_problem": query,
            "steps": steps,
            "current_step_index": 0
        }
        history_manager.update_session_state(username, session_id, {"active_plan": new_plan})
        
        # Present First Step
        first_step = steps[0]
        msg = f"This is a complex problem! 🧠\n\nTo ensure you really learn this, I've broken it down into **{len(steps)} manageable steps**.\n\n"
        msg += f"### Step 1: {first_step['goal']}\n{first_step['description']}\n\n"
        msg += "👉 *Reply with your code or logic for just this step.*"

        yield {"type": "complete", "data": {
            "answer": msg,
            "sources": [], # You can pass chunks here if you want: [{'document_name': c['metadata']['document_name']} for c in chunks]
            "suggestions": ["Show me pseudocode", "I don't know where to start", "Stop guided mode"],
            "intent": "PLANNING"
        }}

    async def _continue_guided_plan(self, query, active_plan, username, session_id, user_role):
        # Exit Check
        if any(w in query.lower() for w in ["stop", "cancel", "quit", "reset"]):
            history_manager.update_session_state(username, session_id, {"active_plan": {"is_active": False}})
            yield {"type": "complete", "data": {"answer": "Guided mode cancelled. What else can I help with?", "sources": [], "intent": "General"}}
            return

        yield {"type": "status", "message": "Checking your step...", "percent": 20}
        
        # Get Current Step Info
        steps = active_plan['steps']
        idx = active_plan['current_step_index']
        current_step_obj = steps[idx]

        # IMPROVED RETRIEVAL: Combine Step Goal + User Query
        search_q = f"{current_step_obj['goal']} {query}"
        retrieved_chunks = self._execute_retrieval(search_q, "DEBUG", user_role) 
        context_text = "\n".join([c['text'] for c in retrieved_chunks])

        # Evaluate
        evaluation = self._evaluate_step_progress(query, current_step_obj, context_text)
        
        answer_text = evaluation['feedback']
        
        if evaluation['status'] == "PASS":
            idx += 1
            if idx >= len(steps):
                answer_text += "\n\n🎉 **Problem Solved!** You've completed all steps. Excellent work."
                
                # --- NEW: Award XP/Mastery ---
                # 1. Identify the topic from the original problem text
                topic_credit = active_plan.get('original_problem', 'General')
                
                # 2. Mark it as "Known" in the database
                # This will increase their stats on the Dashboard
                knowledge_manager.mark_concept_as_known(username, f"Solved: {topic_credit[:30]}...")
                self.logger.info(f"🏆 Awarded completion credit to {username} for: {topic_credit}")
                # -----------------------------

                history_manager.update_session_state(username, session_id, {"active_plan": {"is_active": False}})
            else:
                next_step = steps[idx]
                answer_text += f"\n\n---\n### Next Step ({idx+1}/{len(steps)}): {next_step['goal']}\n{next_step['description']}"
                active_plan['current_step_index'] = idx
                history_manager.update_session_state(username, session_id, {"active_plan": active_plan})
        else:
            # (Your existing visual aid logic is here...)
            raw_visual = evaluation.get('visual_aid', '')
            if raw_visual:
                clean_visual = self._clean_guided_visual(raw_visual)
                answer_text += f"\n\nHere is a visual aid:\n```mermaid\n{clean_visual}\n```"
            elif evaluation.get('pseudocode_hint'):
                answer_text += f"\n\n💡 **Logic Hint:**\n```text\n{evaluation['pseudocode_hint']}\n```"

        # Format sources
        formatted_sources = [{'document_name': c['metadata']['document_name'], 'chunk_text': c['text']} for c in retrieved_chunks]

        # --- NEW: Dynamic Suggestions ---
        sugg_list = ["I'm stuck", "Stop guided mode"]
        
        # If they failed, offer specific help
        if evaluation['status'] == "FAIL":
            sugg_list.insert(0, "Show me Pseudocode")
        # --------------------------------

        yield {"type": "complete", "data": {
            "answer": answer_text, 
            "sources": formatted_sources,
            "suggestions": sugg_list, # <--- Use the new list
            "intent": "GUIDED_PRACTICE"
        }}

    # -------------------------------------------------------
    # PHASE 2 HELPER: ANALYSIS
    # -------------------------------------------------------
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

    # -------------------------------------------------------
    # PHASE 4 HELPER: GATEKEEPING
    # -------------------------------------------------------
    def _check_gatekeeping(self, query, intent, entities, username):
        # 1. Teacher Lock Check
        from app.core.settings_manager import settings_manager
        topic_settings = settings_manager.get_settings()
        for entity in entities:
            for t, on in topic_settings.items():
                if (entity.lower() in t.lower()) and not on:
                    return {"answer": f"🔒 Topic **{t}** is locked by the teacher.", "sources": [], "intent": intent}

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
            btns = [f"Explain {p}" for p in unknown] + [f"I know {p} (Verify)" for p in unknown] + [f"Teach me {entities[0]} anyway"]
            
            # Friendly Message
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

    # -------------------------------------------------------
    # PHASE 5 HELPER: STANDARD RAG
    # -------------------------------------------------------
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
                "answer": full_answer, #self._sanitize_mermaid(full_answer),
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
        
        # 0. CONTEXTUALIZATION (Always run this)
        yield {"type": "status", "message": "Understanding context...", "percent": 5}
        
        # Detect simple button clicks to skip rewriting
        is_force_teach = "teach me" in query.lower() and "anyway" in query.lower()
        is_verify_request = "verify" in query.lower() and "know" in query.lower()
        
        if not is_force_teach and not is_verify_request:
            search_query = await self._contextualize_query(query, username, session_id)
        else:
            search_query = query

        # =========================================================
        # PHASE 1: CHECK ACTIVE STATES (Quizzes & Guided Plans)
        # =========================================================
        # This handles the "Loop" where the user is inside a specific mode
        state_result = await self._handle_active_states(query, username, session_id, user_role)
        if state_result:
            # If the handler returns a generator/result, yield it and exit
            async for item in state_result: yield item
            return

        # =========================================================
        # PHASE 2: ANALYSIS & CLASSIFICATION
        # =========================================================
        yield {"type": "status", "message": "Analyzing query...", "percent": 15}
        
        intent, entities = self._analyze_query(search_query, query, is_force_teach, is_verify_request)
        self.logger.info(f"Intent: {intent} | Entities: {entities}")

        # Security Check
        if intent == "SECURITY_RISK":
            msg = "I can't help with that request. 😅\n\nI'm designed strictly as a **C Programming Tutor** to help you learn safely. Let's get back to coding! 💻"
            yield {"type": "complete", "data": {"answer": msg, "sources": [], "intent": intent}}
            return
        
        if intent == "OFF_TOPIC":
            # Generic, polite refusal that reinforces the AI's purpose
            msg = (
                "👋 I am an AI Tutor specialized strictly in **C Programming**.\n\n"
                "I can't help with general knowledge, personal questions, or other topics. "
                "But I **can** help you with:\n\n"
                "🔹 **Concepts** (Pointers, Arrays, Structs)\n"
                "🔹 **Debugging** (Fixing errors, Segfaults)\n"
                "🔹 **Writing Code** (Solving exercises)"
            )
            
            yield {"type": "complete", "data": {
                "answer": msg, 
                "sources": [], 
                "intent": intent,
                "suggestions": ["What is a Pointer?", "How do loops work?", "Debug my code"]
            }}
            return

        # =========================================================
        # PHASE 3: TRIGGER NEW MODES (Verify or Plan)
        # =========================================================
        
        # A. Trigger Verify/Quiz Mode
        if is_verify_request:
            async for item in self._trigger_quiz_mode(query, username, session_id): yield item
            return

        # B. Trigger Guided Plan Mode (NEW LOGIC)
        # If it's a complex problem, we start the plan here
        is_complex = len(query.split()) > 10 or "write a program" in query.lower() or "exercise" in query.lower()
        if intent == "PROBLEM" and is_complex and not is_force_teach:
             async for item in self._trigger_guided_plan(search_query, intent, entities, user_role, username, session_id): yield item
             return

        # =========================================================
        # PHASE 4: GATEKEEPING & PREREQUISITES
        # =========================================================
        yield {"type": "status", "message": "Checking prerequisites...", "percent": 40}
        
        gatekeeper_result = self._check_gatekeeping(search_query, intent, entities, username)
        if gatekeeper_result:
            yield {"type": "complete", "data": gatekeeper_result}
            return

        # =========================================================
        # PHASE 5: STANDARD RAG EXECUTION
        # =========================================================
        # If no special modes, run the standard Retrieval-Augmented Generation
        async for item in self._execute_standard_rag(search_query, intent, entities, user_role, username, user_goal, session_id):
            yield item