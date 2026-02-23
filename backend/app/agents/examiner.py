# Quiz Agent
# backend/app/agents/examiner.py

import json
import re
import asyncio
from numpy import dot
from numpy.linalg import norm
from typing import AsyncGenerator
from app.agents.base import BaseAgent
from app.agents.schema import AgentState
from app.core.history_manager import history_manager
from app.core.user_knowledge_manager import knowledge_manager
from app.db.graph_db import Neo4jGraphDB
from app.core import config

class ExaminerAgent(BaseAgent):
    """
    The Examiner Agent is responsible for conducting quizzes and verifying student knowledge.
    
    It handles:
    1. Detecting quiz requests (e.g., "I know X (Verify)").
    2. Fetching relevant Q&A pairs from the Neo4j Knowledge Graph.
    3. Managing the quiz state (awaiting answer).
    4. Grading student answers using vector similarity (fast local or slow remote).
    5. Updating the student's mastery record upon success.
    """
    def __init__(self, llm, logger, graph_db: Neo4jGraphDB):
        super().__init__(llm, logger)
        self.graph_db = graph_db

    async def process(self, state: AgentState) -> AsyncGenerator[dict, None]:
        """
        Main processing loop for the Examiner Agent.
        
        Args:
            state (AgentState): The current state of the agent workflow.
            
        Yields:
            dict: Workflow events (status updates, final response).
        """
        # 1. Check if user is ANSWERING a quiz (Active State)
        # We check the session state to see if we are waiting for a quiz answer.
        current_session_state = history_manager.get_session_state(state.user_id, state.session_id)
        if current_session_state.get("awaiting_quiz_answer"):
            # Delegate to the grading logic
            async for event in self._grade_quiz(state, current_session_state):
                yield event
            # Stop further processing by other agents since we handled it
            state.stop_processing = True
            return

        # 2. Check if user REQUESTED a quiz (New Trigger)
        # Fix: Check for "(Verify)" keyword OR "QUIZ" intent explicitly
        is_verify_request = "(verify)" in state.query.lower()
        
        if state.intent == "QUIZ" or is_verify_request:
            # Force intent update for consistency in logs/upstream
            state.intent = "QUIZ" 
            # Start the quiz flow
            async for event in self._start_quiz(state):
                yield event
            # Stop further processing
            state.stop_processing = True
            return

    async def _start_quiz(self, state: AgentState):
        """
        Initiates a new quiz session.
        
        1. Identifies the topic to quiz on.
        2. Fetches a question from the Graph DB.
        3. Updates session state to expect an answer.
        4. Returns the question to the user.
        """
        # Extract topic
        match = re.search(r"know (.*?) \(verify\)", state.query.lower())
        verify_topic = match.group(1).strip() if match else state.entities[0] if state.entities else "this concept"

        yield {"type": "status", "message": "Fetching quiz...", "percent": 50}

        # --- SMART HIERARCHICAL QUERY ---
        # 1. Match the requested topic (e.g., "Variables and Types")
        # 2. Look for its quiz data
        # 3. OR look for quiz data in its CHILDREN (e.g., "Variables", "int")
        cypher = """
            MATCH (n) 
            WHERE toLower(n.name) CONTAINS toLower($topic)
            
            // Find children with quizzes optionally
            OPTIONAL MATCH (n)-[:INCLUDES]->(child)
            WHERE child.quiz_data IS NOT NULL
            
            // Collect candidates
            WITH n, child
            WITH collect(n) + collect(child) as candidates
            
            // Unwind and Filter
            UNWIND candidates as node
            WITH node
            WHERE node.quiz_data IS NOT NULL
            
            RETURN node.quiz_data as data
            LIMIT 1
        """
        # --------------------------------
        
        results = self.graph_db.execute_query(cypher, {"topic": verify_topic})

        qa_pair = None
        if results and results[0]['data']:
            try:
                pairs = json.loads(results[0]['data'])
                import random
                qa_pair = random.choice(pairs)
            except: pass

        if qa_pair:
            # ... (Rest of your existing saving/display logic) ...
            # Save State
            vector_data = {
                "google": qa_pair['a_vector'],
                "local": qa_pair.get('a_vector_local')
            }
            history_manager.update_session_state(state.user_id, state.session_id, {
                "awaiting_quiz_answer": True,
                "quiz_topic": verify_topic,
                "quiz_vector": vector_data,
                "quiz_correct_text": qa_pair['a']
            })

            msg = f"🧐 **Quick Check:** {qa_pair['q']}\n\n👉 **Type your answer in the chat box below.**"
            yield {"type": "complete", "data": {"answer": msg, "sources": [], "suggestions": ["I don't know"], "intent": "QUIZ"}}
        else:
            # Fallback
            yield {"type": "complete", "data": {
                "answer": f"I don't have a specific quiz for **{verify_topic}** yet. Shall I explain it instead?",
                "sources": [],
                "suggestions": [f"Explain {verify_topic}"],
                "intent": "QUIZ"
            }}

    async def _grade_quiz(self, state: AgentState, session_state):
        """
        Grades the pending quiz answer.
        
        1. Retrieves the stored correct vector from session state.
        2. Compares user's answer (state.query) against the correct vector.
        3. Updates knowledge profile if correct.
        4. Returns feedback.
        """
        check_topic = session_state.get("quiz_topic")
        vectors = session_state.get("quiz_vector")
        correct_text = session_state.get("quiz_correct_text")
        pending_goal = session_state.get("pending_goal")
        
        # Clear state immediately so we don't get stuck in a loop
        history_manager.update_session_state(state.user_id, state.session_id, {"awaiting_quiz_answer": False})

        self.logger.info(f"📝 Grading answer for topic: {check_topic}")
        yield {"type": "status", "message": "Verifying answer...", "percent": 30}

        # Select Vector (Handle legacy list format or new dict format)
        correct_vector = vectors.get('google') if isinstance(vectors, dict) else vectors
        correct_vector_local = vectors.get('local') if isinstance(vectors, dict) else None

        # Grade
        # result = self._fast_grade_answer(state.query, correct_vector, correct_vector_local)
        result = await self._smart_grade_answer(state.query, correct_vector, correct_vector_local, correct_text)

        if result['is_correct']:
            knowledge_manager.mark_concept_as_known(state.user_id, check_topic)
            msg = f"✅ **{result['feedback']}**\n\nGreat! You've mastered **{check_topic}**."
            suggestions = ["What should I learn next?"]
            
            if pending_goal:
                msg += f"\n\nNow that you're ready, shall we go back to: **\"{pending_goal}\"**?"
                suggestions.insert(0, pending_goal)
                history_manager.update_session_state(state.user_id, state.session_id, {"pending_goal": None})            
            yield {"type": "complete", "data": {"answer": msg, "sources": [], "suggestions": suggestions, "intent": "EVALUATION"}}
        else:
            # FAILURE or PARTIAL Logic
            msg = result['feedback'] # Contains the "I see you have understanding..." text
            
            # --- DYNAMIC SUGGESTIONS ---
            suggestions = [
                f"Explain {check_topic}",   # Review the current topic
                # Maybe offer the advanced topic IF they were verifying to skip?
                # For now, safer to stick to review.
                "Try another question"
            ]
            
            yield {"type": "complete", "data": {
                "answer": msg, 
                "sources": [], 
                "suggestions": suggestions, 
                "intent": "EVALUATION"
            }}

    async def _smart_grade_answer(self, student_answer: str, vec_google, vec_local, correct_text: str) -> dict:
        # 1. Surrender Check
        if any(p in student_answer.lower() for p in ["i don't know", "idk", "skip", "no idea"]):
             return {"is_correct": False, "feedback": "That's okay! It's better to admit it than to guess."}

        # 2. Vector Check
        cos_sim = 0.0
        
        # Priority: Fast Local Check
        if vec_local and config.INTENT_CLASSIFIER_MODE == "fast":
            try:
                from app.core.fast_classifier import fast_classifier
                s_vec = fast_classifier.intent_model.encode(student_answer)
                cos_sim = dot(s_vec, vec_local) / (norm(s_vec) * norm(vec_local))
            except Exception as e:
                self.logger.error(f"Local grading failed: {e}")
                # Fall through to Google logic if local fails

        # Fallback: Google/API Check
        if cos_sim == 0.0 and vec_google:
            try:
                # Note: This is a synchronous call in most SDKs, consider to_thread if slow
                s_vec = await asyncio.to_thread(self.llm.get_embedding, student_answer)
                if s_vec:
                    cos_sim = dot(s_vec, vec_google) / (norm(s_vec) * norm(vec_google))
            except Exception as e:
                self.logger.error(f"Google grading failed: {e}")

        score_pct = int(cos_sim * 100)

        # 3. Decision Logic (Hybrid)
        # Thresholds:
        # > 82%: High Confidence Match (Pass)
        # 50% - 82%: Gray Zone (Ask LLM Judge)
        # < 50%: Low Confidence (Likely Fail, but check LLM if input is substantial)
        
        if cos_sim > 0.82:
            return {"is_correct": True, "feedback": f"Spot on! ({score_pct}%)"}
        
        # If score is low/medium, but we have text to verify, use LLM Judge
        if correct_text:
             return await self._llm_grade_fallback(student_answer, correct_text, score_pct)
             
        # Default Fail
        return {"is_correct": False, "feedback": f"Not quite. ({score_pct}%)"}

    async def _llm_grade_fallback(self, student, correct, vector_score):
        prompt = f"""
        You are a friendly, encouraging C Programming Tutor grading a pop quiz.
        
        **Quiz Context:**
        - **Correct Answer:** "{correct}"
        - **Student Answer:** "{student}"
        
        **Task:** 
        Determine if the Student Answer is semantically correct.
        
        **CRITICAL RULES:**
        1. **Detect Answers vs Questions:**
           - Short statements like "yes", "no", "break", or "int" are **ANSWERS**.
           - Only classify as **QUESTION** if the student explicitly asks for help (e.g., "I don't know", "Can you explain?").
        
        2. **Lenient Grading for Short Answers (CRITICAL):**
           - If the student's answer captures the CORE TRUTH of the Correct Answer, mark it **TRUE**.
           - Example: If correct is "No, it skips the rest" and student says "No", mark it **TRUE**. Do not penalize for brevity.
        
        3. **Conversational Tone (MANDATORY):**
           - You MUST speak directly to the student using "You" and "Your".
           - NEVER say "The student's answer is...".
           - BAD: "The student is partially correct."
           - GOOD: "You got the first part right, but..."
           
        4. **Integer Division:** If the quiz is about division (e.g. 7/2), exact integer truncation (3) is required. (3.5 is WRONG).

        **Classification:**
        - **TRUE:** Conceptually correct (even if very short).
        - **PARTIAL:** Mostly right but fundamentally missing a key technical constraint.
        - **FALSE:** Factually wrong.
        - **QUESTION:** Explicit request for help.

        **Respond JSON:** 
        {{ 
            "status": "TRUE" | "PARTIAL" | "FALSE" | "QUESTION", 
            "feedback": "Conversational, direct feedback spoken to the student." 
        }}
        """
        
        try:
            # We use the smart LLM for grading if possible, but fallback to whatever is provided
            resp = await asyncio.to_thread(self.llm.generate_response, prompt)
            clean_resp = resp.replace("```json", "").replace("```", "").strip()
            
            # Find JSON block
            start = clean_resp.find('{')
            end = clean_resp.rfind('}') + 1
            data = json.loads(clean_resp[start:end])
            
            status = data.get("status", "FALSE").upper()

            if status == "TRUE":
                return {"is_correct": True, "feedback": f"✅ {data['feedback']}"}
            elif status == "PARTIAL":
                 return {"is_correct": False, "feedback": f"⚠️ {data['feedback']}"}
            elif status == "QUESTION":
                 return {"is_correct": False, "feedback": "🤔 Good question! Let me explain..."}
            else: 
                 return {"is_correct": False, "feedback": f"❌ {data['feedback']}"}
                 
        except Exception as e:
            self.logger.error(f"LLM Grade Error: {e}")
            return {"is_correct": False, "feedback": f"Not quite. ({vector_score}%)"}
    
    def _fast_grade_answer(self, student_answer: str, correct_vector_google: list, correct_vector_local: list = None) -> dict:
        """
        Computes cosine similarity between student answer and correct answer.
        
        Args:
            student_answer (str): The user's text input.
            correct_vector_google (list): Embedding from Google (OpenAI/Vertex) model.
            correct_vector_local (list, optional): Embedding from local FastEmbed model.
            
        Returns:
            dict: {is_correct: bool, feedback: str}
        """
        # --- 1. SURRENDER CHECK (New) ---
        surrender_phrases = ["i don't know", "idk", "no idea", "unsure", "skip", "pass"]
        if any(p in student_answer.lower() for p in surrender_phrases):
             return {"is_correct": False, "feedback": "That's okay! It's better to be honest than to guess."}

        cos_sim = 0.0
        
        # Priority: Use Local FastEmbed if available and config allows (Faster, cheaper)
        if correct_vector_local and config.INTENT_CLASSIFIER_MODE == "fast":
            from app.core.fast_classifier import fast_classifier
            # Encode student answer locally (~20ms)
            student_vec = fast_classifier.intent_model.encode(student_answer)
            # Cosine Similarity formula: (A . B) / (||A|| * ||B||)
            cos_sim = dot(student_vec, correct_vector_local) / (norm(student_vec) * norm(correct_vector_local))
        else:
            # Fallback: API Call (Slower)
            student_vec = self.llm.get_embedding(student_answer)
            if not student_vec or not correct_vector_google:
                return {"is_correct": False, "feedback": "Could not verify."}
            cos_sim = dot(student_vec, correct_vector_google) / (norm(student_vec) * norm(correct_vector_google))

        # Threshold logic
        score_pct = int(cos_sim * 100)
        if cos_sim > 0.75: # Strict threshold for verification
            return {"is_correct": True, "feedback": f"Spot on! ({score_pct}%)"}
        else:
            return {"is_correct": False, "feedback": f"Not quite. ({score_pct}%)"}