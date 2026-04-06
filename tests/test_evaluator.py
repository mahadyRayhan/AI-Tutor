# tests/test_evaluator.py
"""
Context-Aware Evaluator for AI Tutor Testing Agent.

Two metrics:
1. Cosine Similarity: Measures how grounded the response is in source material
2. LLM-as-Judge: Structured rubric scoring (Accuracy, Completeness, Pedagogy, Relevance)

Both metrics use ONLY the retrieved context from ChromaDB — never the model's own knowledge.
"""

import json
import asyncio
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple


@dataclass
class TurnScore:
    """Score for a single conversation turn."""
    turn_index: int
    user_message: str
    bot_response: str
    # Cosine
    cosine_similarity: float = 0.0
    cosine_context_used: str = ""
    # LLM Judge
    accuracy: int = 0
    completeness: int = 0
    pedagogy: int = 0
    relevance: int = 0
    judge_reasoning: str = ""
    # Meta
    response_time_ms: float = 0.0
    detected_intent: str = ""
    is_reactive: bool = False  # Was this an auto-generated response?
    
    @property
    def llm_judge_avg(self) -> float:
        return (self.accuracy + self.completeness + self.pedagogy + self.relevance) / 4.0
    
    @property
    def overall_score(self) -> float:
        """Weighted combination: 40% cosine grounding + 60% LLM judge."""
        return (0.4 * self.cosine_similarity) + (0.6 * (self.llm_judge_avg / 5.0))
    
    @property
    def grade(self) -> str:
        s = self.overall_score
        if s >= 0.85: return "A"
        elif s >= 0.70: return "B"
        elif s >= 0.55: return "C"
        elif s >= 0.40: return "D"
        else: return "F"


@dataclass
class ScenarioResult:
    """Aggregated result for a full test scenario."""
    scenario_name: str
    turns: List[TurnScore] = field(default_factory=list)
    consistency_score: float = 0.0  # For repeat scenarios
    errors: List[str] = field(default_factory=list)
    
    @property
    def avg_cosine(self) -> float:
        scored = [t for t in self.turns if not t.is_reactive]
        return np.mean([t.cosine_similarity for t in scored]) if scored else 0.0
    
    @property
    def avg_judge(self) -> float:
        scored = [t for t in self.turns if not t.is_reactive]
        return np.mean([t.llm_judge_avg for t in scored]) if scored else 0.0
    
    @property
    def avg_overall(self) -> float:
        scored = [t for t in self.turns if not t.is_reactive]
        return np.mean([t.overall_score for t in scored]) if scored else 0.0
    
    @property
    def passed(self) -> bool:
        return self.avg_overall >= 0.55 and len(self.errors) == 0


class ContextAwareEvaluator:
    """
    Evaluates AI Tutor responses using context-aware metrics.
    
    Context source: ChromaDB (same vector store the tutor uses)
    This ensures we judge the tutor against EXACTLY what it has access to.
    """
    
    def __init__(self, vector_store, llm, embedding_fn, logger):
        self.vector_store = vector_store
        self.llm = llm
        self.embedding_fn = embedding_fn
        self.logger = logger
    
    async def evaluate_turn(
        self,
        user_message: str,
        bot_response: str,
        turn_index: int,
        is_reactive: bool = False,
        student_goal: str = "",
        response_type: str = ""  # "gatekeeper", "teaching", "review", etc.
    ) -> TurnScore:
        """Evaluate a single conversation turn."""
        score = TurnScore(
            turn_index=turn_index,
            user_message=user_message,
            bot_response=bot_response,
            is_reactive=is_reactive
        )
        
        # Skip evaluation for reactive responses (quiz answers, challenge submissions)
        if is_reactive:
            score.judge_reasoning = "Reactive response — evaluation skipped"
            return score
        
        # Skip if bot response is too short (likely a redirect/button response)
        if len(bot_response) < 80:
            score.judge_reasoning = "Short response (redirect/button) — evaluation skipped"
            score.relevance = 3  # Assume relevant if it's a redirect
            return score
        
        # Gatekeeper responses get lightweight evaluation
        # They are intentionally short redirects, NOT teaching failures
        if response_type == "gatekeeper" or "let's build a foundation" in bot_response.lower():
            score.judge_reasoning = "Gatekeeper redirect — scored on relevance only"
            score.relevance = 5  # Gatekeepers are by definition relevant
            score.pedagogy = 4   # Scaffolding is good pedagogy
            score.accuracy = 4   # Prerequisite identification is accurate
            score.completeness = 3  # Intentionally incomplete (by design)
            
            # Still compute cosine for the gatekeeper
            context_chunks = await self._retrieve_context(user_message)
            if context_chunks and bot_response:
                score.cosine_similarity = await self._compute_cosine_grounding(
                    bot_response, context_chunks
                )
            return score
        
        # 1. Retrieve context from ChromaDB (same as the tutor would)
        context_chunks = await self._retrieve_context(user_message)
        context_text = "\n\n---\n\n".join(context_chunks) if context_chunks else ""
        score.cosine_context_used = context_text[:500] + "..." if len(context_text) > 500 else context_text
        
        # 2. Cosine Similarity (grounding check)
        if context_chunks and bot_response:
            score.cosine_similarity = await self._compute_cosine_grounding(
                bot_response, context_chunks
            )
        
        # 3. LLM-as-Judge (now includes student goal)
        if context_text:
            judge_result = await self._llm_judge(
                user_message, bot_response, context_text, student_goal
            )
            score.accuracy = judge_result.get("accuracy", 0)
            score.completeness = judge_result.get("completeness", 0)
            score.pedagogy = judge_result.get("pedagogy", 0)
            score.relevance = judge_result.get("relevance", 0)
            score.judge_reasoning = judge_result.get("reasoning", "")
        
        return score
    
    async def evaluate_consistency(
        self, responses: List[str]
    ) -> float:
        """
        For repeat scenarios: compute pairwise cosine similarity between
        multiple responses to the same question.
        """
        if len(responses) < 2:
            return 1.0
        
        embeddings = []
        for resp in responses:
            emb = await asyncio.to_thread(self.embedding_fn, resp)
            if emb:
                embeddings.append(np.array(emb))
        
        if len(embeddings) < 2:
            return 0.0
        
        # Pairwise cosine similarities
        scores = []
        for i in range(len(embeddings)):
            for j in range(i + 1, len(embeddings)):
                sim = np.dot(embeddings[i], embeddings[j]) / (
                    np.linalg.norm(embeddings[i]) * np.linalg.norm(embeddings[j])
                )
                scores.append(float(sim))
        
        return float(np.mean(scores))
    
    # ---- Private Methods ----
    
    async def _retrieve_context(self, query: str, top_k: int = 5) -> List[str]:
        """Query ChromaDB for relevant chunks using embeddings."""
        try:
            # First, embed the query text
            query_embedding = await asyncio.to_thread(self.embedding_fn, query)
            if not query_embedding:
                self.logger.error("Failed to embed query for context retrieval")
                return []
            
            # Query ChromaDB with the embedding vector
            results = await asyncio.to_thread(
                self.vector_store.query, query_embedding, top_k
            )
            
            # Results are list of dicts with 'text' key
            if isinstance(results, list):
                return [r.get('text', '') for r in results if r and r.get('text')]
            return []
        except Exception as e:
            self.logger.error(f"Context retrieval failed: {e}")
            return []
    
    async def _compute_cosine_grounding(
        self, response: str, context_chunks: List[str]
    ) -> float:
        """
        Compute the maximum cosine similarity between the bot response
        and each context chunk. Higher = more grounded in source material.
        """
        try:
            resp_emb = await asyncio.to_thread(self.embedding_fn, response)
            if not resp_emb:
                return 0.0
            resp_vec = np.array(resp_emb)
            
            max_sim = 0.0
            for chunk in context_chunks:
                chunk_emb = await asyncio.to_thread(self.embedding_fn, chunk)
                if chunk_emb:
                    chunk_vec = np.array(chunk_emb)
                    sim = np.dot(resp_vec, chunk_vec) / (
                        np.linalg.norm(resp_vec) * np.linalg.norm(chunk_vec)
                    )
                    max_sim = max(max_sim, float(sim))
            
            return max_sim
        except Exception as e:
            self.logger.error(f"Cosine grounding failed: {e}")
            return 0.0
    
    async def _llm_judge(
        self, question: str, response: str, context: str, student_goal: str = ""
    ) -> Dict:
        """
        LLM-as-Judge: Score the response on 4 rubric axes.
        The judge sees both the source context AND the student's goal.
        """
        goal_section = ""
        if student_goal:
            goal_section = f"""\n--- STUDENT'S LEARNING GOAL ---
{student_goal}
--- END GOAL ---

IMPORTANT: The tutor is expected to connect its explanations to the student's goal.
Examples, analogies, and use cases that reference the goal (e.g., relating concepts to
building a specific project) are CORRECT pedagogical behavior, NOT hallucination.
Do NOT penalize the tutor for connecting material to the student's goal.\n"""

        prompt = f"""You are an expert educational evaluator assessing an AI Tutor's response.

CRITICAL RULE: Judge based on the PROVIDED CONTEXT and the STUDENT'S GOAL below.
The tutor may use analogies, examples, and goal-connected explanations that go beyond
the raw source text — this is GOOD pedagogy, not fabrication. Only penalize Accuracy
if the tutor states something factually WRONG about C programming.
{goal_section}
--- CONTEXT (Source Material Available to the Tutor) ---
{context[:3000]}
--- END CONTEXT ---

--- STUDENT QUESTION ---
{question}
--- END QUESTION ---

--- TUTOR RESPONSE ---
{response[:3000]}
--- END RESPONSE ---

Score each dimension from 0 to 5:

1. **Accuracy** (0-5): Is the response factually correct about C programming?
   - 5: All facts are correct
   - 3: Mostly correct, minor issues
   - 0: Contains factually wrong C programming information

2. **Completeness** (0-5): Does the response adequately cover the topic?
   - 5: Covers all key points relevant to the question
   - 3: Covers main idea but misses important details
   - 0: Misses the main point entirely

3. **Pedagogy** (0-5): Is the response pedagogically sound?
   - 5: Excellent Socratic approach, uses analogies, connects to student's goal, builds understanding
   - 3: Adequate but could be more engaging
   - 0: Just dumps information with no teaching strategy

4. **Relevance** (0-5): Does the response directly address the student's question?
   - 5: Perfectly on-topic
   - 3: Somewhat relevant but includes excessive tangents
   - 0: Completely off-topic

Return ONLY valid JSON:
{{"accuracy": <int>, "completeness": <int>, "pedagogy": <int>, "relevance": <int>, "reasoning": "<1-2 sentence explanation>"}}"""

        try:
            raw = await asyncio.to_thread(self.llm.generate_response, prompt)
            clean = raw.replace("```json", "").replace("```", "").strip()
            start = clean.find('{')
            end = clean.rfind('}') + 1
            result = json.loads(clean[start:end])
            
            # Clamp values to 0-5
            for key in ["accuracy", "completeness", "pedagogy", "relevance"]:
                result[key] = max(0, min(5, int(result.get(key, 0))))
            
            return result
        except Exception as e:
            self.logger.error(f"LLM Judge failed: {e}")
            return {
                "accuracy": 0, "completeness": 0,
                "pedagogy": 0, "relevance": 0,
                "reasoning": f"Judge error: {str(e)}"
            }
