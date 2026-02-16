# Code Review Agent
# backend/app/agents/reviewer.py

import asyncio
from typing import AsyncGenerator
from app.agents.base import BaseAgent
from app.agents.schema import AgentState
from app.db.vector_store import VectorStore

class CodeReviewerAgent(BaseAgent):
    def __init__(self, llm, logger, vector_store: VectorStore):
        super().__init__(llm, logger)
        self.vector_store = vector_store

    async def process(self, state: AgentState) -> AsyncGenerator[dict, None]:
        # Only run if intent is specifically REVIEW
        if state.intent != "REVIEW":
            return

        yield {"type": "status", "message": "Reviewing code...", "percent": 40}

        # 1. Retrieve Reference Code (Standard RAG)
        # We search for the user's code to find similar valid examples
        query_embedding = self.llm.get_embedding(state.query)
        chunks = self.vector_store.query(query_embedding, top_k=3)
        context_text = "\n".join([c['text'] for c in chunks])

        # 2. Generate Review
        yield {"type": "status", "message": "Analyzing syntax...", "percent": 80}
        
        prompt = self._build_review_prompt(state.query, context_text, state.user_goal)
        
        full_response = ""
        async for token in self.llm.stream_response_async(prompt):
            full_response += token
            yield {"type": "token", "text": token}

        # 3. Finalize
        state.final_response = full_response
        state.stop_processing = True # We handled it, stop other agents
        
        yield {
            "type": "complete",
            "data": {
                "answer": full_response,
                "sources": [{'document_name': c['metadata']['document_name'], 'chunk_text': c['text']} for c in chunks],
                "intent": "REVIEW"
            }
        }

    def _build_review_prompt(self, query: str, context: str, user_goal: str = None) -> str:
        goal_prompt = ""
        if user_goal:
            goal_prompt = f"6. **GOAL CHECK:** Does this code show progress towards their goal: '{user_goal}'? If yes, mention it."

        return f"""
        You are a supportive C Code Reviewer.
        
        Student's Input: {query}
        Reference Material: {context}

        **RULES:**
        1. **CHECK CONTEXT:** Look for a "[CONTEXT: ...]" tag.
        2. **SANDWICH METHOD:** Positive -> Improvement -> Hint.
        3. **SOURCE GROUNDING:** Use variable names from Reference Material where possible.
        4. **NO SOLUTIONS:** Do not rewrite the full code for them.
        5. **CHECK LOGIC:** Look for common beginner mistakes (semicolons, brackets, types).
        {goal_prompt}

        Format:
        ## Code Review
        **✅ What looks good:** ...
        **⚠️ What needs work:** ...
        **💡 Hint:** ...
        """