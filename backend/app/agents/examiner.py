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
        # Extract topic from "I know X (Verify)"
        # Note: Scaffolding agent might have regex-matched this earlier, or we do it here.
        match = re.search(r"know (.*?) \(verify\)", state.query.lower())
        verify_topic = match.group(1).strip() if match else state.entities[0] if state.entities else "this concept"

        yield {"type": "status", "message": "Fetching quiz...", "percent": 50}

        # Fetch from Neo4j
        # We look for a Concept node with the matching name and get its 'quiz_data' property.
        cypher = "MATCH (n:Concept) WHERE toLower(n.name) CONTAINS toLower($topic) RETURN n.quiz_data as data LIMIT 1"
        results = self.graph_db.execute_query(cypher, {"topic": verify_topic})

        qa_pair = None
        if results and results[0]['data']:
            try:
                # expecting quiz_data to be a JSON string of a list of pairs
                pairs = json.loads(results[0]['data'])
                import random
                qa_pair = random.choice(pairs)
            except: pass

        if qa_pair:
            # Save State so we know the correct answer for the next turn
            vector_data = {
                "google": qa_pair['a_vector'],
                "local": qa_pair.get('a_vector_local')
            }
            history_manager.update_session_state(state.user_id, state.session_id, {
                "awaiting_quiz_answer": True,
                "quiz_topic": verify_topic,
                "quiz_vector": vector_data
            })

            msg = f"🧐 **Quick Check:** {qa_pair['q']}\n\n👉 **Type your answer in the chat box below.**"
            yield {"type": "complete", "data": {"answer": msg, "sources": [], "suggestions": ["I don't know"], "intent": "QUIZ"}}
        else:
            # Fallback if no quiz is found
            yield {"type": "complete", "data": {
                "answer": f"I don't have a quiz for **{verify_topic}** yet. Shall I explain it?",
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
        
        # Clear state immediately so we don't get stuck in a loop
        history_manager.update_session_state(state.user_id, state.session_id, {"awaiting_quiz_answer": False})

        self.logger.info(f"📝 Grading answer for topic: {check_topic}")
        yield {"type": "status", "message": "Verifying answer...", "percent": 30}

        # Select Vector (Handle legacy list format or new dict format)
        correct_vector = vectors.get('google') if isinstance(vectors, dict) else vectors
        correct_vector_local = vectors.get('local') if isinstance(vectors, dict) else None

        # Grade
        result = self._fast_grade_answer(state.query, correct_vector, correct_vector_local)

        if result['is_correct']:
            # Mark as mastered in the user's knowledge profile
            knowledge_manager.mark_concept_as_known(state.user_id, check_topic)
            msg = f"✅ **{result['feedback']}**\n\nGreat! You've mastered **{check_topic}**."
            yield {"type": "complete", "data": {"answer": msg, "sources": [], "intent": "EVALUATION"}}
        else:
            msg = f"❌ **{result['feedback']}**\n\nLet's review **{check_topic}**."
            yield {"type": "complete", "data": {"answer": msg, "sources": [], "suggestions": [f"Explain {check_topic}"], "intent": "EVALUATION"}}

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