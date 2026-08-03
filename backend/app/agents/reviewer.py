# Code Review Agent
# backend/app/agents/reviewer.py

import json
import asyncio
from typing import AsyncGenerator
from app.agents.base import BaseAgent
from app.agents.schema import AgentState
from app.db.vector_store import VectorStore
from typing import AsyncGenerator, List, Dict, Any

class CodeReviewerAgent(BaseAgent):
    """
    The Code Reviewer Agent is responsible for analyzing student code and providing feedback.
    
    It works by:
    1. Identifying user queries with 'REVIEW' intent.
    2. Retrieving similar reference code examples from the Vector Store (RAG).
    3. Constructing a prompt for the LLM that enforces pedagogical best practices.
    4. Streaming a "Sandwich Method" review (Positive -> Constructive -> Hint).
    """
    def __init__(self, llm, logger, vector_store: VectorStore):
        super().__init__(llm, logger)
        self.vector_store = vector_store

    async def process(self, state: AgentState) -> AsyncGenerator[dict, None]:
        if state.intent != "REVIEW":
            return

        yield {"type": "status", "message": "Reviewing code...", "percent": 30}

        query_embedding = self.llm.get_embedding(state.query)
        chunks = self.vector_store.query(query_embedding, top_k=3)
        context_text = "\n".join([c['text'] for c in chunks])

        yield {"type": "status", "message": "Analyzing syntax...", "percent": 60}
        
        # Track review count and gather prior feedback for variety
        review_count = 1
        prior_feedback = ""
        from app.core.history_manager import history_manager
        if state.session_id and state.user_id:
            current_state = history_manager.get_session_state(state.user_id, state.session_id)
            review_count = (current_state.get("review_count", 0) if current_state else 0) + 1
            # Retrieve prior review key points to avoid repetition
            prior_points = current_state.get("review_history", []) if current_state else []
            if prior_points:
                prior_feedback = "\n".join(f"- Review #{i+1}: {p}" for i, p in enumerate(prior_points[-3:]))  # Last 3 only
            history_manager.update_session_state(state.user_id, state.session_id, {"review_count": review_count})
        
        prompt = self._build_review_prompt(state.query, context_text, state.user_goal, review_count, prior_feedback)
        
        full_response = ""
        async for token in self.llm.stream_response_async(prompt):
            full_response += token
            yield {"type": "token", "text": token}

        # --- FIX: Edge case analysis is NO LONGER automatic ---
        # We store the context for later use if the user clicks "Rigorous Analysis"
        # and offer it as a suggestion button instead.

        state.final_response = full_response
        state.stop_processing = True

        # BKT code evidence: heuristic pass/fail from review content
        if state.user_id and state.entities:
            review_topic = state.entities[0] if state.entities else None
            if review_topic:
                _error_keywords = (
                    "compilation error", "syntax error", "will not compile",
                    "won't compile", "does not compile", "missing semicolon",
                    "undefined variable", "segmentation fault", "memory leak",
                    "infinite loop", "out of bounds", "buffer overflow",
                )
                resp_lower = full_response.lower()
                has_errors = any(kw in resp_lower for kw in _error_keywords)
                from app.core.bkt_model import bkt as _bkt_code
                _bkt_code.update(state.user_id, review_topic, not has_errors, evidence_type="code")
                self.logger.info(
                    f"📐 [BKT/CODE] '{review_topic}' for {state.user_id}: "
                    f"{'PASS' if not has_errors else 'FAIL'}"
                )
                # F2-05: show updated per-tier mastery progress after the review.
                # Append to full_response (not a separate token) so it survives in the
                # final 'complete' event's answer field below.
                ledger = _bkt_code.mastery_ledger(state.user_id, review_topic)
                if ledger:
                    full_response += ledger

        # Store code submission for Rigorous Analysis retrieval
        if state.session_id and state.user_id:
            history_manager.update_session_state(
                state.user_id, state.session_id,
                {"last_code_submission": state.query}
            )
        
        # Save review key point for anti-repetition in future reviews
        if state.session_id and state.user_id:
            # Extract first meaningful line as the key point
            key_point = ""
            for line in full_response.split("\n"):
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and len(stripped) > 20:
                    key_point = stripped[:120]
                    break
            if key_point:
                current_state = history_manager.get_session_state(state.user_id, state.session_id) or {}
                review_history = current_state.get("review_history", [])
                review_history.append(key_point)
                history_manager.update_session_state(state.user_id, state.session_id, {"review_history": review_history})
        
        # Build suggestion buttons — always offer Rigorous Analysis
        suggestions = ["🧐 Rigorous Analysis"]
        
        # Add pending goal buttons if they exist
        from app.core.history_manager import history_manager
        if state.session_id and state.user_id:
            current_state = history_manager.get_session_state(state.user_id, state.session_id)
            goals_stack = current_state.get("pending_goals", []) if current_state else []
            visited_prereqs = current_state.get("visited_prereqs", []) if current_state else []
            
            if goals_stack or visited_prereqs:
                # Show all pending goals as buttons
                for goal in goals_stack:
                    suggestions.append(f"📌 Back to: {goal}")
                
                # Also offer to explain recently visited prereqs
                if visited_prereqs:
                    last_prereq = visited_prereqs[-1]
                    if f"Explain {last_prereq}" not in suggestions:
                        suggestions.append(f"Explain {last_prereq}")

        yield {
            "type": "complete",
            "data": {
                "answer": full_response,
                "sources": [{'document_name': c['metadata']['document_name'], 'chunk_text': c['text']} for c in chunks],
                "suggestions": suggestions,
                "intent": "REVIEW"
            }
        }

    async def process_edge_cases(self, state: AgentState) -> AsyncGenerator[dict, None]:
        """
        Triggered when the user clicks the 'Rigorous Analysis' button.
        """
        yield {"type": "status", "message": "Running rigorous edge case analysis...", "percent": 50}
        
        query_embedding = self.llm.get_embedding(state.query)
        chunks = self.vector_store.query(query_embedding, top_k=3)
        context_text = "\n".join([c['text'] for c in chunks])
        
        edge_report = await asyncio.to_thread(self._analyze_edge_cases, state.query, context_text)
        
        if edge_report and edge_report.get('cases'):
            edge_text = "### 🧐 Engineer's Perspective: Rigorous Analysis\n"
            edge_text += "Your logic works for standard inputs. Now, let's think like a Senior Engineer and test the **Edge Cases**:\n\n"
            for case in edge_report['cases']:
                icon = "🔴" if case.get('severity') == "High" else "⚠️"
                edge_text += f"- {icon} **Scenario:** {case['scenario']}\n  - **Outcome:** {case['outcome']}\n"
            edge_text += "\n*Handling these cases prevents crashes in production!*"
        else:
            edge_text = "### 🧐 Engineer's Perspective\nYour code looks solid! I couldn't find any critical edge cases to flag. Well done! 🎉"
        
        state.final_response = edge_text
        state.stop_processing = True
        
        yield {"type": "token", "text": edge_text}
        yield {
            "type": "complete",
            "data": {
                "answer": edge_text,
                "sources": [],
                "intent": "REVIEW"
            }
        }

    def _analyze_edge_cases(self, user_code: str, context: str) -> Dict[str, Any]:
        """
        GPAI-Style Feature: Rigorous Multi-Case Analysis.
        A4 FIX: Only analyze the student's actual code, not reference material.
        """
        prompt = f"""
        You are a Senior C Software Engineer performing a Security & Reliability Audit on code written by a junior developer.
        
        **Junior's Code:** 
        {user_code}

        **TASK:** Identify 3 specific EDGE CASES where this code might fail, crash, or produce undefined behavior.
        Ignore syntax errors. Assume the code compiles. Focus on LOGIC and SAFETY.
        
        **CRITICAL CONSTRAINTS:**
        1. **ONLY analyze the code provided above.** Do NOT reference variables, arrays, functions, or data structures that are NOT in the student's code.
        2. **Speak DIRECTLY to the programmer.** Use "You" and "Your code". 
        3. **NEVER** say "If the student code...". 
        4. **BAD:** "Your 'prices' array..." (if no 'prices' variable exists in the code)
        5. **GOOD:** Reference only variables that actually appear in the code above.
        
        Think about:
        1. Input Validation (Negative numbers, Zero, Non-numeric)
        2. Memory Safety (Buffer overflows, Array bounds)
        3. Integer Math (Overflow, Divide by Zero)

        **OUTPUT JSON:**
        {{
            "cases": [
                {{"scenario": "Brief description (e.g. Your user enters 0)", "outcome": "What happens? (e.g. Your program crashes due to division by zero)", "severity": "High"}},
                {{"scenario": "...", "outcome": "...", "severity": "Medium"}}
            ]
        }}
        """
        response = self.llm.generate_response(prompt)
        try:
            clean = response.replace("```json", "").replace("```", "").strip()
            start = clean.find('{')
            end = clean.rfind('}') + 1
            return json.loads(clean[start:end])
        except:
            return None

    def _build_review_prompt(self, query: str, context: str, user_goal: str = None, review_count: int = 1, prior_feedback: str = "") -> str:
        goal_prompt = ""
        if user_goal:
            goal_prompt = f"\n- **Goal Awareness:** The student's project goal is '{user_goal}'. If the code shows progress towards it, briefly acknowledge this."

        # Progression-aware tone and structure
        if review_count == 1:
            tone = "This is the student's FIRST code submission. Be welcoming and encouraging — celebrate that they're writing code."
            structure_hint = "Use a warm, conversational opening. Start by highlighting what they did well, then gently note improvements."
        elif review_count == 2:
            tone = "This is their SECOND submission. Acknowledge they're building a habit of submitting code."
            structure_hint = "Be direct but friendly. Point out what's improved since they started, then focus on one key area."
        elif review_count <= 4:
            tone = f"This is submission #{review_count}. They're getting into a rhythm — be a coding partner, not a lecturer."
            structure_hint = "Skip generic praise. Lead with the most interesting observation about their code, then the key fix."
        else:
            tone = f"This is submission #{review_count}. They're deeply engaged. Be a senior engineer reviewing a PR — concise and technical."
            structure_hint = "Be surgical. One key observation, one action item. No fluff."

        # Anti-repetition context from prior reviews
        history_context = ""
        if prior_feedback:
            history_context = f"""
**PRIOR REVIEWS THIS SESSION (do NOT repeat these observations):**
{prior_feedback}
"""

        return f"""You are a friendly, perceptive C Code Reviewer having a conversation with a student.

**Student's Code:**
{query}

**Reference Material:**
{context}

**Tone:** {tone}
{history_context}
**RULES:**
1. **FAIL-FORWARD (CRITICAL):** Never say "Wrong", "Incorrect", "Failed", or "Bad". Validate their thinking first, then guide them.
2. **NO SOLUTIONS (CRITICAL):** Do NOT rewrite their code or provide corrected code blocks. Give only verbal hints.
3. **REVIEW ONLY:** Do NOT add lessons or concept explanations after the review.
4. **ANTI-REPETITION (CRITICAL):** 
   - Do NOT start with "I see what you were trying to do!" 
   - Do NOT use the same opening as any prior review listed above.
   - Vary your structure — don't always use bullet points or the same section headers.{goal_prompt}

**STRUCTURE:** {structure_hint}
Use `## Code Review` as the header, then write naturally. You may use any combination of:
- Inline observations woven into prose
- Short bullet points for multiple small issues
- A single focused paragraph for one key insight
- Bold text to highlight important points

Keep it concise and human."""