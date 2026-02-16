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
    def __init__(self, llm, logger, graph_db: Neo4jGraphDB):
        super().__init__(llm, logger)
        self.graph_db = graph_db

    async def process(self, state: AgentState) -> AsyncGenerator[dict, None]:
        # 1. Check if user is ANSWERING a quiz (Active State)
        current_session_state = history_manager.get_session_state(state.user_id, state.session_id)
        if current_session_state.get("awaiting_quiz_answer"):
            async for event in self._grade_quiz(state, current_session_state):
                yield event
            state.stop_processing = True
            return

        # 2. Check if user REQUESTED a quiz (New Trigger)
        # Fix: Check for "(Verify)" keyword OR "QUIZ" intent
        is_verify_request = "(verify)" in state.query.lower()
        
        if state.intent == "QUIZ" or is_verify_request:
            # Force intent update for consistency
            state.intent = "QUIZ" 
            async for event in self._start_quiz(state):
                yield event
            state.stop_processing = True
            return

    async def _start_quiz(self, state: AgentState):
        # Extract topic from "I know X (Verify)"
        # Note: Scaffolding agent might have regex-matched this earlier, or we do it here.
        match = re.search(r"know (.*?) \(verify\)", state.query.lower())
        verify_topic = match.group(1).strip() if match else state.entities[0] if state.entities else "this concept"

        yield {"type": "status", "message": "Fetching quiz...", "percent": 50}

        # Fetch from Neo4j
        cypher = "MATCH (n:Concept) WHERE toLower(n.name) CONTAINS toLower($topic) RETURN n.quiz_data as data LIMIT 1"
        results = self.graph_db.execute_query(cypher, {"topic": verify_topic})

        qa_pair = None
        if results and results[0]['data']:
            try:
                pairs = json.loads(results[0]['data'])
                import random
                qa_pair = random.choice(pairs)
            except: pass

        if qa_pair:
            # Save State
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
            # Fallback
            yield {"type": "complete", "data": {
                "answer": f"I don't have a quiz for **{verify_topic}** yet. Shall I explain it?",
                "sources": [],
                "suggestions": [f"Explain {verify_topic}"],
                "intent": "QUIZ"
            }}

    async def _grade_quiz(self, state: AgentState, session_state):
        check_topic = session_state.get("quiz_topic")
        vectors = session_state.get("quiz_vector")
        
        # Clear state
        history_manager.update_session_state(state.user_id, state.session_id, {"awaiting_quiz_answer": False})

        self.logger.info(f"📝 Grading answer for topic: {check_topic}")
        yield {"type": "status", "message": "Verifying answer...", "percent": 30}

        # Select Vector
        correct_vector = vectors.get('google') if isinstance(vectors, dict) else vectors
        correct_vector_local = vectors.get('local') if isinstance(vectors, dict) else None

        # Grade
        result = self._fast_grade_answer(state.query, correct_vector, correct_vector_local)

        if result['is_correct']:
            knowledge_manager.mark_concept_as_known(state.user_id, check_topic)
            msg = f"✅ **{result['feedback']}**\n\nGreat! You've mastered **{check_topic}**."
            yield {"type": "complete", "data": {"answer": msg, "sources": [], "intent": "EVALUATION"}}
        else:
            msg = f"❌ **{result['feedback']}**\n\nLet's review **{check_topic}**."
            yield {"type": "complete", "data": {"answer": msg, "sources": [], "suggestions": [f"Explain {check_topic}"], "intent": "EVALUATION"}}

    def _fast_grade_answer(self, student_answer: str, correct_vector_google: list, correct_vector_local: list = None) -> dict:
        cos_sim = 0.0
        
        if correct_vector_local and config.INTENT_CLASSIFIER_MODE == "fast":
            from app.core.fast_classifier import fast_classifier
            student_vec = fast_classifier.intent_model.encode(student_answer)
            cos_sim = dot(student_vec, correct_vector_local) / (norm(student_vec) * norm(correct_vector_local))
        else:
            student_vec = self.llm.get_embedding(student_answer)
            if not student_vec or not correct_vector_google:
                return {"is_correct": False, "feedback": "Could not verify."}
            cos_sim = dot(student_vec, correct_vector_google) / (norm(student_vec) * norm(correct_vector_google))

        score_pct = int(cos_sim * 100)
        if cos_sim > 0.75:
            return {"is_correct": True, "feedback": f"Spot on! ({score_pct}%)"}
        else:
            return {"is_correct": False, "feedback": f"Not quite. ({score_pct}%)"}