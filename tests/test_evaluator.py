# tests/test_evaluator.py
"""
Context-Aware Evaluator for AI Tutor Testing Agent.

Metrics:
  Core (per-turn):
    1. Cosine Similarity: Measures how grounded the response is in source material
    2. LLM-as-Judge: Structured rubric scoring (Accuracy, Completeness, Pedagogy, Relevance)

  Advanced (per-turn → aggregated):
    3. SFI  – Spoon-Feeding Index: % of turns with unprompted code delivery
    4. PVR  – Prerequisite Violation Rate: % of turns where prereqs were unmet
    5. ETI  – Emotional Tone Index: avg of encouragement, mirroring, firmness-warmth

  Advanced (scenario-level):
    6. SRR  – Security Recall Rate: % of attacks correctly blocked (Red Team only)
    7. SMD  – Semantic Maturity Delta: embedding shift toward expert centroid
    8. KTE  – Knowledge Transfer Efficiency: baseline / actual turns-to-mastery

All metrics use ONLY the retrieved context from ChromaDB — never the model's own knowledge.
"""

import re
import json
import asyncio
import logging
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple

# Expert-level C questions for SMD centroid computation
EXPERT_QUESTIONS = [
    "How does pointer arithmetic interact with array bounds in C?",
    "What is the difference between stack and heap allocation?",
    "When should I use malloc vs calloc vs realloc?",
    "How do I prevent buffer overflow vulnerabilities in C?",
    "What happens when you dereference a null pointer in C?",
    "How does the C preprocessor handle macro expansion and token pasting?",
    "What is the difference between pass-by-value and pass-by-pointer in C?",
    "How do function pointers enable callback patterns in C?",
    "What is undefined behavior in C and how do I avoid it?",
    "How does the sizeof operator work with arrays vs pointers?",
    "What is the difference between static and dynamic linking?",
    "How do I implement a generic data structure using void pointers?",
    "What is the volatile keyword used for in embedded C?",
    "How does struct padding and alignment affect memory layout?",
    "What is the difference between a segmentation fault and a bus error?",
    "How do I manage memory leaks using valgrind?",
    "What is the restrict qualifier and when should I use it?",
    "How does the linker resolve external symbol references?",
    "What is sequence point and how does it affect expression evaluation?",
    "How do I implement thread-safe code using mutexes in C?",
]

# Baseline turns-to-mastery per concept (curriculum-expert estimates)
MASTERY_BASELINES = {
    "Variables": 3, "Data Types": 3, "Operators": 3,
    "Control Flow": 4, "Conditionals": 4, "Loops": 4,
    "Functions": 5, "Arrays": 4, "Strings": 4,
    "Pointers": 6, "Structs": 5, "Memory": 6,
    "File I/O": 4, "Recursion": 5, "Linked Lists": 6,
    "default": 4,
}


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
    
    # ── Advanced Metric Fields ──
    # SFI
    sfi_has_code_block: bool = False
    # ETI
    eti_encouragement: float = 0.0
    eti_mirroring: float = 0.0
    eti_firmness_warmth: float = 0.0
    # PVR
    pvr_concept_taught: str = ""
    pvr_is_violation: bool = False
    
    @property
    def llm_judge_avg(self) -> float:
        return (self.accuracy + self.completeness + self.pedagogy + self.relevance) / 4.0
    
    @property
    def overall_score(self) -> float:
        """Weighted combination: 40% cosine grounding + 60% LLM judge."""
        return (0.4 * self.cosine_similarity) + (0.6 * (self.llm_judge_avg / 5.0))
    
    @property
    def eti_score(self) -> float:
        """Average of the 3 ETI sub-scores."""
        return (self.eti_encouragement + self.eti_mirroring + self.eti_firmness_warmth) / 3.0
    
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
    
    # ── Advanced scenario-level metrics (set by test_agent after run) ──
    srr: Optional[float] = None       # Security Recall Rate (Red Team only)
    smd: Optional[float] = None       # Semantic Maturity Delta
    kte: Optional[float] = None       # Knowledge Transfer Efficiency
    
    @property
    def scored_turns(self) -> List[TurnScore]:
        return [t for t in self.turns if not t.is_reactive]
    
    @property
    def avg_cosine(self) -> float:
        scored = self.scored_turns
        return float(np.mean([t.cosine_similarity for t in scored])) if scored else 0.0
    
    @property
    def avg_judge(self) -> float:
        scored = self.scored_turns
        return float(np.mean([t.llm_judge_avg for t in scored])) if scored else 0.0
    
    @property
    def avg_overall(self) -> float:
        scored = self.scored_turns
        return float(np.mean([t.overall_score for t in scored])) if scored else 0.0
    
    @property
    def passed(self) -> bool:
        return self.avg_overall >= 0.55 and len(self.errors) == 0
    
    # ── SFI: Spoon-Feeding Index ──
    @property
    def sfi(self) -> float:
        """Percentage of scored turns that contain code blocks."""
        scored = self.scored_turns
        if not scored:
            return 0.0
        code_turns = sum(1 for t in scored if t.sfi_has_code_block)
        return (code_turns / len(scored)) * 100
    
    # ── PVR: Prerequisite Violation Rate ──
    @property
    def pvr(self) -> float:
        """Percentage of scored turns with prerequisite violations."""
        scored = self.scored_turns
        if not scored:
            return 0.0
        violations = sum(1 for t in scored if t.pvr_is_violation)
        return (violations / len(scored)) * 100
    
    # ── ETI: Emotional Tone Index (average across scored turns) ──
    @property
    def eti(self) -> float:
        """Average ETI across all scored turns."""
        scored = [t for t in self.scored_turns if t.eti_score > 0]
        return float(np.mean([t.eti_score for t in scored])) if scored else 0.0


class ContextAwareEvaluator:
    """
    Evaluates AI Tutor responses using context-aware metrics.
    
    Context source: ChromaDB (same vector store the tutor uses)
    This ensures we judge the tutor against EXACTLY what it has access to.
    """
    
    def __init__(self, vector_store, llm, embedding_fn, logger, graph_db=None):
        self.vector_store = vector_store
        self.llm = llm
        self.embedding_fn = embedding_fn
        self.logger = logger
        self.graph_db = graph_db
        self._expert_centroid = None  # Lazy-initialized for SMD
    
    async def evaluate_turn(
        self,
        user_message: str,
        bot_response: str,
        turn_index: int,
        is_reactive: bool = False,
        student_goal: str = "",
        response_type: str = "",  # "gatekeeper", "teaching", "review", etc.
        username: str = "",
    ) -> TurnScore:
        """Evaluate a single conversation turn with core + advanced metrics."""
        score = TurnScore(
            turn_index=turn_index,
            user_message=user_message,
            bot_response=bot_response,
            is_reactive=is_reactive
        )
        
        # ── SFI: Always detect code blocks (even for reactive turns) ──
        score.sfi_has_code_block = self._detect_code_block(bot_response)
        
        # Skip deeper evaluation for reactive responses
        if is_reactive:
            score.judge_reasoning = "Reactive response — evaluation skipped"
            return score
        
        # Skip if bot response is too short (likely a redirect/button response)
        if len(bot_response) < 80:
            score.judge_reasoning = "Short response (redirect/button) — evaluation skipped"
            score.relevance = 3
            return score
        
        # Gatekeeper responses get lightweight evaluation
        if response_type == "gatekeeper" or "let's build a foundation" in bot_response.lower():
            score.judge_reasoning = "Gatekeeper redirect — scored on relevance only"
            score.relevance = 5
            score.pedagogy = 4
            score.accuracy = 4
            score.completeness = 3
            
            context_chunks = await self._retrieve_context(user_message)
            if context_chunks and bot_response:
                score.cosine_similarity = await self._compute_cosine_grounding(
                    bot_response, context_chunks
                )
            return score
        
        # 1. Retrieve context from ChromaDB
        context_chunks = await self._retrieve_context(user_message)
        context_text = "\n\n---\n\n".join(context_chunks) if context_chunks else ""
        score.cosine_context_used = context_text[:500] + "..." if len(context_text) > 500 else context_text
        
        # 2. Cosine Similarity (grounding check)
        if context_chunks and bot_response:
            score.cosine_similarity = await self._compute_cosine_grounding(
                bot_response, context_chunks
            )
        
        # 3. LLM-as-Judge (core 4 dimensions + ETI 3 sub-scores + PVR concept)
        if context_text:
            judge_result = await self._llm_judge_extended(
                user_message, bot_response, context_text, student_goal
            )
            # Core scores
            score.accuracy = judge_result.get("accuracy", 0)
            score.completeness = judge_result.get("completeness", 0)
            score.pedagogy = judge_result.get("pedagogy", 0)
            score.relevance = judge_result.get("relevance", 0)
            score.judge_reasoning = judge_result.get("reasoning", "")
            # ETI sub-scores
            score.eti_encouragement = judge_result.get("eti_encouragement", 0.0)
            score.eti_mirroring = judge_result.get("eti_mirroring", 0.0)
            score.eti_firmness_warmth = judge_result.get("eti_firmness_warmth", 0.0)
            # PVR concept
            score.pvr_concept_taught = judge_result.get("concept_taught", "")
        
        # 4. PVR: Check prerequisite violations via Neo4j
        if score.pvr_concept_taught and username and self.graph_db:
            score.pvr_is_violation = await self._check_prerequisite_violation(
                score.pvr_concept_taught, username
            )
        
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
        
        scores = []
        for i in range(len(embeddings)):
            for j in range(i + 1, len(embeddings)):
                sim = np.dot(embeddings[i], embeddings[j]) / (
                    np.linalg.norm(embeddings[i]) * np.linalg.norm(embeddings[j])
                )
                scores.append(float(sim))
        
        return float(np.mean(scores))
    
    # ──────────────────────────────────────────────
    #  Scenario-Level Metrics (called by test_agent)
    # ──────────────────────────────────────────────
    
    async def compute_smd(self, turns: List[TurnScore]) -> Optional[float]:
        """
        Semantic Maturity Delta: measures whether student questions
        improved toward an expert centroid over the session.
        
        SMD = cos(e_last, C_exp) - cos(e_first, C_exp)
        Positive = student questions matured. Negative = regressed.
        """
        user_turns = [t for t in turns if not t.is_reactive and t.user_message]
        if len(user_turns) < 2:
            return None
        
        try:
            # Lazy-init expert centroid
            if self._expert_centroid is None:
                self._expert_centroid = await self._compute_expert_centroid()
            
            if self._expert_centroid is None:
                return None
            
            # Embed first and last user messages
            first_emb = await asyncio.to_thread(self.embedding_fn, user_turns[0].user_message)
            last_emb = await asyncio.to_thread(self.embedding_fn, user_turns[-1].user_message)
            
            if not first_emb or not last_emb:
                return None
            
            first_vec = np.array(first_emb)
            last_vec = np.array(last_emb)
            centroid = self._expert_centroid
            
            cos_first = float(np.dot(first_vec, centroid) / (
                np.linalg.norm(first_vec) * np.linalg.norm(centroid)
            ))
            cos_last = float(np.dot(last_vec, centroid) / (
                np.linalg.norm(last_vec) * np.linalg.norm(centroid)
            ))
            
            return round(cos_last - cos_first, 4)
        except Exception as e:
            self.logger.error(f"SMD computation failed: {e}")
            return None
    
    def compute_kte(
        self,
        concept_first_turn: Dict[str, int],
        concept_mastery_turn: Dict[str, int]
    ) -> Optional[float]:
        """
        Knowledge Transfer Efficiency.
        
        KTE = T_baseline / T_actual (averaged across mastered concepts).
        > 1.0 = faster than expected. < 1.0 = slower.
        
        Args:
            concept_first_turn: {concept_name: first_turn_index}
            concept_mastery_turn: {concept_name: mastery_turn_index}
        """
        efficiencies = []
        for concept, mastery_turn in concept_mastery_turn.items():
            first_turn = concept_first_turn.get(concept)
            if first_turn is None:
                continue
            
            t_actual = mastery_turn - first_turn + 1
            if t_actual <= 0:
                continue
            
            # Look up baseline (fuzzy match)
            t_baseline = MASTERY_BASELINES.get("default", 4)
            for key, val in MASTERY_BASELINES.items():
                if key.lower() in concept.lower() or concept.lower() in key.lower():
                    t_baseline = val
                    break
            
            efficiencies.append(t_baseline / t_actual)
        
        if not efficiencies:
            return None
        return round(float(np.mean(efficiencies)), 3)
    
    # ──────────────────────────────────────────────
    #  Private Methods
    # ──────────────────────────────────────────────
    
    async def _retrieve_context(self, query: str, top_k: int = 5) -> List[str]:
        """Query ChromaDB for relevant chunks using embeddings."""
        try:
            query_embedding = await asyncio.to_thread(self.embedding_fn, query)
            if not query_embedding:
                self.logger.error("Failed to embed query for context retrieval")
                return []
            
            results = await asyncio.to_thread(
                self.vector_store.query, query_embedding, top_k
            )
            
            if isinstance(results, list):
                return [r.get('text', '') for r in results if r and r.get('text')]
            return []
        except Exception as e:
            self.logger.error(f"Context retrieval failed: {e}")
            return []
    
    async def _compute_cosine_grounding(
        self, response: str, context_chunks: List[str]
    ) -> float:
        """Max cosine similarity between bot response and context chunks."""
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
    
    def _detect_code_block(self, response: str) -> bool:
        """
        SFI helper: detects if the response contains a code block.
        Looks for fenced code blocks (```), or C-like code patterns.
        """
        if not response:
            return False
        # Fenced code blocks
        if "```" in response:
            return True
        # C-like code patterns: lines with semicolons + braces
        code_pattern = re.compile(r'(?:int|char|float|void|return|printf|scanf|#include)\s.*[;{]', re.MULTILINE)
        return bool(code_pattern.search(response))
    
    async def _check_prerequisite_violation(
        self, concept_taught: str, username: str
    ) -> bool:
        """
        PVR helper: checks if concept_taught has unmet prerequisites.
        Returns True if there IS a violation (prereqs not mastered).
        """
        try:
            # Query Neo4j for prerequisites of this concept
            cypher = """
                MATCH (target)-[:REQUIRES_UNDERSTANDING_OF]->(req)
                WHERE toLower(target.name) CONTAINS toLower($name)
                RETURN req.name as prerequisite
            """
            results = await asyncio.to_thread(
                self.graph_db.execute_query, cypher, {"name": concept_taught}
            )
            
            if not results:
                return False  # No prereqs = no violation
            
            # Check mastery table for each prerequisite
            from app.db.sqlite_db import db
            for record in results:
                prereq = record.get("prerequisite", "")
                if not prereq:
                    continue
                row = db.fetch_one(
                    "SELECT 1 FROM user_knowledge WHERE username = ? AND LOWER(concept) LIKE ?",
                    (username, f"%{prereq.lower()}%")
                )
                if not row:
                    self.logger.debug(f"📛 PVR violation: '{concept_taught}' taught without prereq '{prereq}'")
                    return True
            
            return False
        except Exception as e:
            self.logger.error(f"PVR check failed: {e}")
            return False
    
    async def _compute_expert_centroid(self) -> Optional[np.ndarray]:
        """Pre-compute the expert question centroid for SMD."""
        try:
            embeddings = []
            for q in EXPERT_QUESTIONS:
                emb = await asyncio.to_thread(self.embedding_fn, q)
                if emb:
                    embeddings.append(np.array(emb))
            
            if not embeddings:
                return None
            
            return np.mean(embeddings, axis=0)
        except Exception as e:
            self.logger.error(f"Expert centroid computation failed: {e}")
            return None
    
    async def _llm_judge_extended(
        self, question: str, response: str, context: str, student_goal: str = ""
    ) -> Dict:
        """
        Extended LLM-as-Judge: scores 4 core rubric axes + ETI sub-scores
        + concept identification, all in a single call.
        """
        goal_section = ""
        if student_goal:
            goal_section = f"""\n--- STUDENT'S LEARNING GOAL ---
{student_goal}
--- END GOAL ---

IMPORTANT: The tutor is expected to connect its explanations to the student's goal.
Examples, analogies, and use cases that reference the goal are CORRECT pedagogical behavior.\n"""

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

Score each dimension:

CORE RUBRIC (0-5 each):
1. **accuracy**: Is the response factually correct about C programming?
2. **completeness**: Does the response adequately cover the topic?
3. **pedagogy**: Is the response pedagogically sound (Socratic, uses analogies, builds understanding)?
4. **relevance**: Does the response directly address the student's question?

EMOTIONAL TONE INDEX (0.0-1.0 each):
5. **eti_encouragement**: How encouraging is the response? (0=cold/dismissive, 1=highly encouraging)
6. **eti_mirroring**: Does the tone match the student's emotional state? (0=mismatch, 1=perfect alignment)
7. **eti_firmness_warmth**: For boundary-setting: firm but warm? For teaching: warm but professional? (0=robotic/harsh, 1=perfectly balanced)

CONCEPT IDENTIFICATION:
8. **concept_taught**: What is the PRIMARY C programming concept being taught? (single word/phrase, e.g. "Pointers", "Arrays", "Loops"). Return "" if no specific concept.

Return ONLY valid JSON:
{{"accuracy": <int>, "completeness": <int>, "pedagogy": <int>, "relevance": <int>, "eti_encouragement": <float>, "eti_mirroring": <float>, "eti_firmness_warmth": <float>, "concept_taught": "<string>", "reasoning": "<1-2 sentence explanation>"}}"""

        try:
            raw = await asyncio.to_thread(self.llm.generate_response, prompt)
            clean = raw.replace("```json", "").replace("```", "").strip()
            start = clean.find('{')
            end = clean.rfind('}') + 1
            result = json.loads(clean[start:end])
            
            # Clamp core values to 0-5
            for key in ["accuracy", "completeness", "pedagogy", "relevance"]:
                result[key] = max(0, min(5, int(result.get(key, 0))))
            
            # Clamp ETI values to 0.0-1.0
            for key in ["eti_encouragement", "eti_mirroring", "eti_firmness_warmth"]:
                result[key] = max(0.0, min(1.0, float(result.get(key, 0.0))))
            
            # Clean concept_taught
            result["concept_taught"] = str(result.get("concept_taught", "")).strip()
            
            return result
        except Exception as e:
            self.logger.error(f"LLM Judge (extended) failed: {e}")
            return {
                "accuracy": 0, "completeness": 0,
                "pedagogy": 0, "relevance": 0,
                "eti_encouragement": 0.0, "eti_mirroring": 0.0,
                "eti_firmness_warmth": 0.0, "concept_taught": "",
                "reasoning": f"Judge error: {str(e)}"
            }
