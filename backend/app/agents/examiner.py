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
        
        current_session_state = history_manager.get_session_state(state.user_id, state.session_id)
        
        # =========================================================
        # SAGE PDF PAGE 5: HANDLE CONFIDENCE RATING
        # =========================================================
        if current_session_state.get("awaiting_confidence_rating"):
            # Turn off the confidence flag, turn ON the grading flag
            import time as _time
            history_manager.update_session_state(state.user_id, state.session_id, {
                "awaiting_confidence_rating": False,
                "awaiting_quiz_answer": True,
                "student_confidence": state.query, # Save their rating for future analytics
                "quiz_shown_at": _time.time()  # telemetry: start time-to-answer clock
            })
            
            # Retrieve the question we stored earlier
            question = current_session_state.get("pending_quiz_question")
            
            # Briefly acknowledge their confidence and ask the question
            ack = await asyncio.to_thread(self.llm.generate_response, f"The student rated their confidence as: '{state.query}'. Write a 1-sentence supportive acknowledgment.")
            
            msg = f"{ack}\n\n**Here is the question:**\n{question}\n\n👉 *Type your answer below!*"
            
            yield {"type": "complete", "data": {"answer": msg, "sources": [], "suggestions": ["I don't know (Skip)"], "intent": "QUIZ", "entities": state.entities}}
            
            state.stop_processing = True
            return

        # 1. Check if user is ANSWERING a quiz (Active State)
        if current_session_state.get("awaiting_quiz_answer"):
            async for event in self._grade_quiz(state, current_session_state):
                yield event
            state.stop_processing = True
            return

        # 2. Check if user REQUESTED a quiz (New Trigger)
        is_verify_request = "(verify)" in state.query.lower()
        
        if state.intent == "QUIZ" or is_verify_request:
            state.intent = "QUIZ"
            async for event in self._start_quiz(state, current_session_state):
                yield event
            state.stop_processing = True
            return

    async def _start_quiz(self, state: AgentState, session_state: dict = None):
        """
        Initiates a new quiz session.
        
        1. Identifies the topic to quiz on.
        2. Fetches a question from the Graph DB.
        3. Updates session state to expect an answer.
        4. Returns the question to the user.
        """
        # Extract topic — try patterns in priority order
        _quiz_non_topics = {"quiz", "me", "test", "my", "knowledge", "ask", "challenge",
                            "on", "about", "please", "another", "question", "try", "can", "give", "you"}
        verify_topic = None

        # Pattern 1: internal verify format "know X (verify)"
        m = re.search(r"know (.*?) \(verify\)", state.query.lower())
        if m:
            verify_topic = m.group(1).strip()

        # Pattern 2: "quiz me on X", "test me on X", "challenge me on X"
        if not verify_topic:
            m = re.search(r"(?:quiz|test|challenge)\s+me\s+on\s+(.+)", state.query.lower())
            if m:
                verify_topic = m.group(1).strip().rstrip(".")

        # Pattern 3: "another question about X"
        if not verify_topic:
            m = re.search(r"(?:another question|try another).+?(?:about|on)\s+(.+)", state.query.lower())
            if m:
                verify_topic = m.group(1).strip().rstrip(".")

        # Pattern 4: reuse last quiz topic from session state
        if not verify_topic and session_state:
            verify_topic = session_state.get("quiz_topic")

        # Pattern 5: filter non-topic words from extracted entities
        if not verify_topic:
            topic_entities = [e for e in state.entities if e.lower() not in _quiz_non_topics]
            verify_topic = topic_entities[0] if topic_entities else (state.entities[0] if state.entities else "this concept")

        yield {"type": "status", "message": "Fetching quiz...", "percent": 50}

        asked_so_far = list(session_state.get("asked_quiz_questions", [])) if session_state else []
        qa_pair = self._fetch_quiz_question(verify_topic, asked_so_far)

        if qa_pair:
            # =========================================================
            # SAGE PDF PAGE 5: METACOGNITIVE JOLs (Confidence Judgments)
            # Instead of asking the question immediately, we ask for a confidence rating first.
            # =========================================================
            vector_data = {
                "google": qa_pair['a_vector'],
                "local": qa_pair.get('a_vector_local')
            }

            if qa_pair['q'] not in asked_so_far:
                asked_so_far.append(qa_pair['q'])

            is_verify = "(verify)" in state.query.lower()
            # Save State: We are waiting for a CONFIDENCE RATING, not the actual answer yet.
            history_manager.update_session_state(state.user_id, state.session_id, {
                "awaiting_confidence_rating": True,
                "pending_quiz_question": qa_pair['q'],
                "quiz_topic": verify_topic,
                "quiz_vector": vector_data,
                "quiz_correct_text": qa_pair['a'],
                "asked_quiz_questions": asked_so_far,
                # Knowledge credibility test (Fix #2): 3 separate real questions, 1 BKT update each.
                "is_verification_quiz": is_verify,
                "verification_q_num": 1,   # tracks which verification question we're on (1–3)
            })

            if state.profile.get('is_surprise_quiz'):
                msg = f"I will gladly help you with that next! But first, it's time for a **Pop Quiz**! 📝\n\n"
                msg += f"Before I show you the question on **{verify_topic}**, I want you to rate yourself: \n\n"
                msg += f"**On a scale of 1 to 5, how confident are you that you understand {verify_topic} right now?**"
            else:
                msg = f"Let's verify your knowledge on **{verify_topic}**! \n\n"
                msg += f"Before I ask the question: **On a scale of 1 to 5, how confident are you with this topic?**"

            # Give them quick 1-5 buttons to click
            yield {"type": "complete", "data": {
                "answer": msg, 
                "sources": [], 
                "suggestions": ["1 - Not at all", "3 - Somewhat", "5 - Very confident", "I don't know (Skip)"], 
                "intent": "QUIZ", 
                "entities": state.entities
            }}
        else:
            # Fallback
            yield {"type": "complete", "data": {
                "answer": f"I don't have a specific quiz for **{verify_topic}** yet. Shall I explain it instead?",
                "sources": [],
                "suggestions": [f"Explain {verify_topic}"],
                "intent": "QUIZ",
                "entities": state.entities
            }}


    def _fetch_quiz_question(self, topic: str, asked_so_far: list) -> dict | None:
        """Fetch one quiz question for topic, excluding already-asked questions."""
        cypher = """
            OPTIONAL MATCH (exact)
            WHERE toLower(exact.name) = toLower($topic) AND exact.quiz_data IS NOT NULL
            WITH collect(exact) as exact_matches
            OPTIONAL MATCH (partial)
            WHERE toLower(partial.name) CONTAINS toLower($topic) AND partial.quiz_data IS NOT NULL
            WITH exact_matches, collect(partial) as partial_matches
            OPTIONAL MATCH (parent)-[:INCLUDES]->(child)
            WHERE toLower(parent.name) CONTAINS toLower($topic)
              AND child.quiz_data IS NOT NULL AND toLower(child.name) CONTAINS toLower($topic)
            WITH exact_matches, partial_matches, collect(child) as child_matches
            WITH exact_matches + partial_matches + child_matches as all_candidates
            UNWIND all_candidates as node
            RETURN node.quiz_data as data LIMIT 1
        """
        results = self.graph_db.execute_query(cypher, {"topic": topic})
        if not (results and results[0]['data']):
            return None
        try:
            import random
            pairs = json.loads(results[0]['data'])
            asked = set(asked_so_far)
            unseen = [p for p in pairs if p['q'] not in asked]
            pool = unseen if unseen else pairs
            return random.choice(pool)
        except Exception:
            return None

    async def _grade_quiz(self, state: AgentState, session_state):
        """
        Grades the pending quiz answer and compares it to their prior confidence.
        """
        check_topic = session_state.get("quiz_topic")
        vectors = session_state.get("quiz_vector")
        correct_text = session_state.get("quiz_correct_text")
        goals_stack = session_state.get("pending_goals", [])
        
        # =========================================================
        # THE FIX: PULL THEIR CONFIDENCE RATING
        # =========================================================
        confidence_str = session_state.get("student_confidence", "3") # Default to 3 if missing
        
        # Extract the first number from their string (e.g., "5 - Very confident" -> 5)
        import re
        match = re.search(r'\d+', confidence_str)
        confidence_score = int(match.group()) if match else 3

        # Clear state immediately so we don't get stuck in a loop
        history_manager.update_session_state(state.user_id, state.session_id, {"awaiting_quiz_answer": False})

        self.logger.info(f"📝 Grading answer for topic: {check_topic} (Student Confidence: {confidence_score}/5)")
        yield {"type": "status", "message": "Verifying answer...", "percent": 30}

        # Select Vector (Handle legacy list format or new dict format)
        correct_vector = vectors.get('google') if isinstance(vectors, dict) else vectors
        correct_vector_local = vectors.get('local') if isinstance(vectors, dict) else None

        # Grade
        # result = self._fast_grade_answer(state.query, correct_vector, correct_vector_local)
        result = await self._smart_grade_answer(state.query, correct_vector, correct_vector_local, correct_text)

        # SM-2 quality score: combines correctness with confidence calibration
        if result['is_correct']:
            sm2_quality = 5 if confidence_score >= 4 else 4
        else:
            sm2_quality = 0 if confidence_score >= 4 else (1 if confidence_score <= 2 else 2)
        knowledge_manager.update_sm2(state.user_id, check_topic, sm2_quality)

        # BKT update: 1 update per question — always.
        # Knowledge credibility test (Fix #2): instead of counting 1 answer as 3,
        # the system asks 3 separate real questions. Each gets exactly 1 BKT update.
        # verification_q_num tracks which question of 3 we are grading.
        from app.core.bkt_model import bkt
        is_verification_quiz = session_state.get("is_verification_quiz", False)
        verification_q_num   = session_state.get("verification_q_num", 1)

        p_mastery = bkt.update(state.user_id, check_topic, result['is_correct'], evidence_type="quiz")

        # --- Telemetry: JOL (confidence vs actual outcome) + item-level quiz record ---
        try:
            from app.core import telemetry
            import time as _time
            telemetry.log_jol(state.user_id, check_topic, confidence_score,
                              result['is_correct'])
            _shown_at = session_state.get("quiz_shown_at")
            _tta = round(_time.time() - _shown_at, 2) if _shown_at else None
            telemetry.log_quiz_item(
                username=state.user_id,
                session_id=state.session_id,
                concept=check_topic,
                question_text=session_state.get("pending_quiz_question", ""),
                correct_answer=correct_text or "",
                student_answer=state.query,
                is_correct=result['is_correct'],
                confidence_1_5=confidence_score,
                time_to_answer_sec=_tta,
                is_verification=is_verification_quiz,
                verification_q_num=verification_q_num,
            )
            # SRL signal: distinguish "gave up / skipped" from an honest wrong attempt
            _surrender = ["i don't know", "idk", "skip", "no idea", "i dont know", "no clue", "pass"]
            if state.query.lower().strip().rstrip('.!') in _surrender:
                telemetry.log_event(state.user_id, "quiz_skip", {
                    "concept": check_topic, "confidence": confidence_score,
                    "is_verification": is_verification_quiz,
                }, session_id=state.session_id)
        except Exception as e:
            self.logger.warning(f"[Examiner] JOL telemetry failed: {e}")

        # If this is an intermediate verification question (Q1 or Q2) and correct,
        # ask the next question instead of showing the full grading response.
        if is_verification_quiz and result['is_correct'] and verification_q_num < 3:
            asked_so_far = session_state.get("asked_quiz_questions", [])
            next_q = self._fetch_quiz_question(check_topic, asked_so_far)
            if next_q:
                updated_asked = list(asked_so_far)
                if next_q['q'] not in updated_asked:
                    updated_asked.append(next_q['q'])
                import time as _time
                history_manager.update_session_state(state.user_id, state.session_id, {
                    "awaiting_quiz_answer": True,
                    "pending_quiz_question": next_q['q'],
                    "quiz_vector": {"google": next_q['a_vector'], "local": next_q.get('a_vector_local')},
                    "quiz_correct_text": next_q['a'],
                    "asked_quiz_questions": updated_asked,
                    "is_verification_quiz": True,
                    "verification_q_num": verification_q_num + 1,
                    "quiz_shown_at": _time.time(),  # telemetry: start time-to-answer clock
                })
                msg = (f"✅ Correct! ({verification_q_num}/3 verified)\n\n"
                       f"**Question {verification_q_num + 1} of 3:**\n\n{next_q['q']}\n\n"
                       f"👉 *Type your answer below!*")
                yield {"type": "complete", "data": {
                    "answer": msg, "sources": [], "suggestions": ["I don't know (Skip)"],
                    "intent": "QUIZ", "entities": state.entities
                }}
                return  # defer full grading response until Q3

        # Full grading response path: either not a verification quiz, a wrong answer, or Q3
        is_verified_credit = (is_verification_quiz and result['is_correct']
                              and verification_q_num == 3 and confidence_score >= 4)

        if result['is_correct']:
            knowledge_manager.resolve_misconception(state.user_id, check_topic)
            # BKT-gated mastery: mark known only when P(L) crosses 0.95 threshold
            if bkt.is_mastered(state.user_id, check_topic):
                knowledge_manager.mark_concept_as_known(state.user_id, check_topic)
                self.logger.info(f"🏆 [BKT] Mastery unlocked: '{check_topic}' P(L)={p_mastery:.3f}")
            else:
                self.logger.info(f"📐 [BKT] '{check_topic}' P(L)={p_mastery:.3f} (threshold 0.95 not yet reached)")

            # =========================================================
            # SAGE PDF PAGE 2: CALIBRATION ACCURACY (PASS SCENARIOS)
            # =========================================================
            if confidence_score <= 2:
                # Low Confidence + PASS = Self-Efficacy Boost
                msg = f"✅ **{result['feedback']}**\n\n"
                msg += f"See? You rated your confidence as a {confidence_score}/5, but your actual skills are much higher! Trust your instincts, you know this.\n\n"
            elif confidence_score >= 4:
                # High Confidence + PASS = Validation
                msg = f"✅ **{result['feedback']}**\n\n"
                msg += f"Your confidence ({confidence_score}/5) was perfectly placed. Great job backing it up with a solid answer.\n\n"
                if is_verified_credit:
                    msg += "📊 *Knowledge credibility verified — 3 evidence points credited to your mastery record.*\n\n"
            else:
                msg = f"✅ **{result['feedback']}**\n\n"
                
            msg += f"You've officially mastered **{check_topic}**.\n\n"
            msg += f"🧠 **Feynman Challenge:** To truly lock this into your long-term memory, try explaining **{check_topic}** back to me in your own words, as if I were a 5-year-old!"
            
            suggestions = ["I'll try explaining it!", "What should I learn next?"]
            if goals_stack:
                last_goal = goals_stack[-1]
                msg += f"\n\nOr, if you prefer, shall we go back to your goal: **\"{last_goal}\"**?"
                suggestions.append(f"Back to: {last_goal}")
            
            yield {"type": "complete", "data": {"answer": msg, "sources": [], "suggestions": suggestions, "intent": "EVALUATION", "entities": state.entities}}
        else:
            # =========================================================
            # SAGE PDF PAGE 2: CALIBRATION ACCURACY (FAIL SCENARIOS)
            # =========================================================
            if confidence_score >= 4:
                # High Confidence + FAIL = Misconception (not just a gap)
                knowledge_manager.store_misconception(
                    state.user_id, check_topic, state.query, correct_text or ""
                )
                msg = f"❌ **{result['feedback']}**\n\n"
                msg += f"I noticed you felt very confident ({confidence_score}/5) before starting. It's incredibly common to feel like we understand code when reading it, but answering questions exposes the hidden gaps. Let's look at exactly where the logic broke down.\n\n"
            elif confidence_score <= 2:
                # Low Confidence + FAIL = Vulnerability Validation
                msg = f"❌ **{result['feedback']}**\n\n"
                msg += f"You didn't get it, but you were incredibly self-aware to rate your confidence as a {confidence_score}/5. You knew exactly where your boundaries were, and that self-awareness is a superpower in programming. Now let's fix that gap!\n\n"
            else:
                msg = f"❌ **{result['feedback']}**\n\n"

            suggestions = [
                f"Explain {check_topic}",   
                "Try another question"
            ]
            
            yield {"type": "complete", "data": {
                "answer": msg, 
                "sources": [], 
                "suggestions": suggestions, 
                "intent": "EVALUATION",
                "entities": state.entities 
            }}

    async def _smart_grade_answer(self, student_answer: str, vec_google, vec_local, correct_text: str) -> dict:
        # 1. Surrender Check — only explicit "I don't know" phrases, not just short answers
        surrender_phrases = ["i don't know", "idk", "skip", "no idea", "i dont know", "no clue", "pass"]
        if student_answer.lower().strip() in surrender_phrases or student_answer.lower().strip().rstrip('.!') in surrender_phrases:
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

        # Fallback: Google/API Check
        if cos_sim == 0.0 and vec_google:
            try:
                s_vec = await asyncio.to_thread(self.llm.get_embedding, student_answer)
                if s_vec:
                    cos_sim = dot(s_vec, vec_google) / (norm(s_vec) * norm(vec_google))
            except Exception as e:
                self.logger.error(f"Google grading failed: {e}")

        score_pct = int(cos_sim * 100)

        # 3. Decision Logic (Hybrid) — A3 FIX: Lowered thresholds
        # Thresholds (adjusted to reduce false negatives):
        # > 75%: High Confidence Match (Pass)
        # 40% - 75%: Gray Zone (Ask LLM Judge)
        # < 40%: Low Confidence (Likely Fail, but still check LLM if input is substantial)
        
        if cos_sim > 0.75:
            return {"is_correct": True, "feedback": f"Spot on! ({score_pct}%)"}
        
        # If score is low/medium, always use LLM Judge for fair grading
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