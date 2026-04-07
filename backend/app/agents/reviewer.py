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
        
        # Track review count for response variety
        review_count = 1
        from app.core.history_manager import history_manager
        if state.session_id and state.user_id:
            current_state = history_manager.get_session_state(state.user_id, state.session_id)
            review_count = (current_state.get("review_count", 0) if current_state else 0) + 1
            history_manager.update_session_state(state.user_id, state.session_id, {"review_count": review_count})
        
        prompt = self._build_review_prompt(state.query, context_text, state.user_goal, review_count)
        
        full_response = ""
        async for token in self.llm.stream_response_async(prompt):
            full_response += token
            yield {"type": "token", "text": token}

        # --- FIX: Edge case analysis is NO LONGER automatic ---
        # We store the context for later use if the user clicks "Rigorous Analysis"
        # and offer it as a suggestion button instead.

        state.final_response = full_response
        state.stop_processing = True
        
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

    def _build_review_prompt(self, query: str, context: str, user_goal: str = None, review_count: int = 1) -> str:
        goal_prompt = ""
        if user_goal:
            goal_prompt = f"7. **GOAL CHECK:** Does this code show progress towards their goal: '{user_goal}'? If yes, mention it."

        # Variety instructions based on review count
        if review_count == 1:
            variety = "This is the student's FIRST code submission. Welcome their effort warmly."
        elif review_count == 2:
            variety = "This is the student's SECOND submission. Acknowledge their continued effort and note any improvement from their approach."
        elif review_count <= 4:
            variety = f"This is submission #{review_count}. The student is building momentum — acknowledge their growth and be more specific in your feedback."
        else:
            variety = f"This is submission #{review_count}. The student is highly engaged. Be concise, skip generic encouragement, and focus on actionable technical insight."

        return f"""
        You are a supportive C Code Reviewer.
        
        Student's Input: {query}
        Reference Material: {context}

        **CONTEXT:** {variety}

        **RULES:**
        1. **CHECK CONTEXT:** Look for a "[CONTEXT: ...]" tag to understand what they are trying to do.
        2. **RSD SAFETY (CRITICAL):** Never use the words "Wrong", "Incorrect", "Failed", or "Bad". Always validate their logic first, then gently point out the syntax rule that got in the way. This is called "Fail-Forward" feedback.
        3. **SANDWICH METHOD:** Positive -> Improvement -> Hint.
        4. **SOURCE GROUNDING:** Use variable names from Reference Material where possible.
        5. **ABSOLUTELY NO SOLUTIONS (CRITICAL):** Do NOT rewrite the code for them. Do NOT provide the correct code block. Only provide a text hint. If you provide the answer, you will be penalized.
        6. **REVIEW ONLY (CRITICAL):** ONLY review the code. Do NOT explain any other topic. Do NOT add lessons or concept explanations after the review. Your response must end after the Hint.
        {goal_prompt}
        8. **VARIETY (CRITICAL):** Do NOT start with "I see what you were trying to do!" — vary your opening every time. Use different phrasing for each review. Examples: "Nice work on...", "You're on the right track with...", "Great use of...", "Looking at your code,..."

        Format:
        ## Code Review
        **✅ What looks good:** ...
        **⚠️ What needs work:** ...
        **💡 Hint:** ...
        """