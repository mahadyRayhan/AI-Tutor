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
        """
        Main processing loop for the Reviewer Agent.
        Now includes Rigorous Edge Case Analysis (GPAI-style).
        
        Args:
            state (AgentState): The current state of the agent workflow.
            
        Yields:
            dict: Workflow events (status updates, tokens, final response).
        """
        # Only run if intent is specifically REVIEW
        if state.intent != "REVIEW":
            return

        yield {"type": "status", "message": "Reviewing code...", "percent": 30}

        # 1. Retrieve Reference Code (Standard RAG)
        # We search for the user's code to find similar valid examples in our knowledge base.
        # This grounding ensures our feedback aligns with the course's preferred coding style.
        query_embedding = self.llm.get_embedding(state.query)
        chunks = self.vector_store.query(query_embedding, top_k=3)
        context_text = "\n".join([c['text'] for c in chunks])

        # 2. START PARALLEL TASKS (Optimization)
        # Task A: Generate the Standard Review (Streaming immediately to user)
        # Task B: Run the Rigorous Edge Case Analysis (Background task, appended later)
        # This prevents the user from waiting for the deep analysis before seeing the initial feedback.
        
        edge_case_task = asyncio.create_task(
            asyncio.to_thread(self._analyze_edge_cases, state.query, context_text)
        )

        # 3. Generate Standard Review (Streaming)
        yield {"type": "status", "message": "Analyzing syntax...", "percent": 60}
        
        prompt = self._build_review_prompt(state.query, context_text, state.user_goal)
        
        full_response = ""
        # Stream the initial "Sandwich Method" feedback
        async for token in self.llm.stream_response_async(prompt):
            full_response += token
            yield {"type": "token", "text": token}

        # 4. Append Engineer's Perspective (Edge Cases)
        # Once the main review is done, we check if the background analysis found any critical flaws.
        yield {"type": "status", "message": "Checking edge cases...", "percent": 90}
        
        edge_report = await edge_case_task
        
        if edge_report and edge_report.get('cases'):
            # Format the rigorous analysis section
            edge_text = "\n\n---\n### 🧐 Engineer's Perspective: Rigorous Analysis\n"
            edge_text += "Your logic works for standard inputs. Now, let's think like a Senior Engineer and test the **Edge Cases**:\n\n"
            
            for case in edge_report['cases']:
                icon = "🔴" if case.get('severity') == "High" else "⚠️"
                edge_text += f"- {icon} **Scenario:** {case['scenario']}\n  - **Outcome:** {case['outcome']}\n"
            
            edge_text += "\n*Handling these cases prevents crashes in production!*"
            
            # Stream this new section to the user
            full_response += edge_text
            yield {"type": "token", "text": edge_text}

        # 5. Finalize
        # We store the response in the shared state so downstream handlers can access it if needed
        state.final_response = full_response
        state.stop_processing = True # We handled the request, stop other agents from processing
        
        yield {
            "type": "complete",
            "data": {
                "answer": full_response,
                "sources": [{'document_name': c['metadata']['document_name'], 'chunk_text': c['text']} for c in chunks],
                "intent": "REVIEW"
            }
        }

    def _analyze_edge_cases(self, user_code: str, context: str) -> Dict[str, Any]:
        """
        GPAI-Style Feature: Rigorous Multi-Case Analysis.
        """
        prompt = f"""
        You are a Senior C Software Engineer performing a Security & Reliability Audit.
        
        **Student Code:** 
        {user_code}
        
        **Reference Context:** {context[:500]}

        **TASK:** Identify 3 specific EDGE CASES where this code might fail, crash, or produce undefined behavior.
        Ignore syntax errors. Assume the code compiles. Focus on LOGIC and SAFETY.
        
        Think about:
        1. Input Validation (Negative numbers, Zero, Non-numeric)
        2. Memory Safety (Buffer overflows, Array bounds)
        3. Integer Math (Overflow, Divide by Zero)

        **OUTPUT JSON:**
        {{
            "cases": [
                {{"scenario": "Brief description (e.g. User enters 0)", "outcome": "What happens? (e.g. Crash)", "severity": "High"}},
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

    def _build_review_prompt(self, query: str, context: str, user_goal: str = None) -> str:
        """
        Constructs the system prompt for the LLM to generate a code review.
        
        Enforces:
        - Pedagogical tone (Supportive).
        - Feedback structure (Sandwich Method).
        - Goal alignment (Checking if code moves user towards their goal).
        - Source grounding (Using variable names from context).
        """
        goal_prompt = ""
        if user_goal:
            goal_prompt = f"6. **GOAL CHECK:** Does this code show progress towards their goal: '{user_goal}'? If yes, mention it."

        return f"""
        You are a supportive C Code Reviewer.
        
        Student's Input: {query}
        Reference Material: {context}

        **RULES:**
        1. **CHECK CONTEXT:** Look for a "[CONTEXT: ...]" tag to understand what they are trying to do.
        2. **SANDWICH METHOD:** Positive -> Improvement -> Hint.
        3. **SOURCE GROUNDING:** Use variable names from Reference Material where possible.
        4. **NO SOLUTIONS:** Do not rewrite the full code for them. Guide them to fix it.
        5. **CHECK LOGIC:** Look for common beginner mistakes (semicolons, brackets, types, logic errors).
        {goal_prompt}

        Format:
        ## Code Review
        **✅ What looks good:** ...
        **⚠️ What needs work:** ...
        **💡 Hint:** ...
        """