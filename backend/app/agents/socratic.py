# Standard RAG Agent
# backend/app/agents/socratic.py

import asyncio
import json
import math
from typing import AsyncGenerator, Dict, Any, List
from app.agents.base import BaseAgent
from app.agents.schema import AgentState
from app.db.vector_store import VectorStore
from app.db.graph_db import Neo4jGraphDB
from app.core.user_knowledge_manager import knowledge_manager
from app.core.cac_graph import (
    Rung, section_allowed, disclosure_directive, redirect_preamble)

class SocraticTutorAgent(BaseAgent):
    """
    The Socratic Tutor Agent is the primary "Teacher" in the system.
    
    It is responsible for:
    1. Handling general concept questions (e.g., "What is a pointer?").
    2. Retrieval-Augmented Generation (RAG) using both Vector Search and Knowledge Graph.
    3. Generating Socratic explanations that guide students rather than just giving answers.
    4. Auto-marking concepts as "Known" when a student asks about them (passive learning tracking).
    """
    def __init__(self, llm, logger, vector_store: VectorStore, graph_db: Neo4jGraphDB):
        super().__init__(llm, logger)
        self.vector_store = vector_store
        self.graph_db = graph_db
        self.llm_interface = llm

    async def process(self, state: AgentState) -> AsyncGenerator[dict, None]:
        """
        Main processing loop for the Socratic Agent.
        
        Args:
            state (AgentState): The current state of the agent workflow.
            
        Yields:
            dict: Workflow events.
        """
        # This is the "Catch-All" agent, so it runs if no one else stopped processing.
        
        yield {"type": "status", "message": "Searching knowledge base...", "percent": 60}
        
        # 1. Retrieval
        # Uses entities if available, otherwise query.
        # For queries with few entities (1-2), keep the original query — it has
        # better semantic context (e.g. "simple example of a variable" beats "variable in C").
        # For queries producing many entities, join them to avoid noise.
        if len(state.query.split()) > 5 and len(state.entities) > 2:
            q_search = f"{' '.join(state.entities)} in C"
        else:
            q_search = state.query
        chunks = self._execute_retrieval(q_search, state.intent, state.user_role, state.entities)
        
        # 2. Build Context (Graceful Fallback)
        yield {"type": "status", "message": "Drafting response...", "percent": 80}
        
        known = knowledge_manager.get_known_concepts(state.user_id)
        user_context = f"USER CONTEXT: Student knows: {', '.join(known)}." if known else ""
        
        if not chunks:
            # If the database is empty for this broad topic (like "Fundamentals"), rely on LLM baseline
            context_text = f"{user_context}\n\n--- Source: Baseline AI Knowledge ---\nExplain the core concepts based on your general knowledge of C programming."
            sources_payload = []
        else:
            context_text = f"{user_context}\n\n" + "\n".join([f"--- Source: {c['metadata']['document_name']} ---\n{c['text']}" for c in chunks])
            sources_payload = [{'document_name': c['metadata']['document_name'], 'chunk_text': c['text']} for c in chunks]

        # 2. Build Context
        yield {"type": "status", "message": "Drafting response...", "percent": 80}
        
        # Add User Context (Known concepts) to help LLM personalize
        known = knowledge_manager.get_known_concepts(state.user_id)
        user_context = f"USER CONTEXT: Student knows: {', '.join(known)}." if known else ""
        context_text = f"{user_context}\n\n" + "\n".join([f"--- Source: {c['metadata']['document_name']} ---\n{c['text']}" for c in chunks])

        # 3. Generate Suggestions (Parallel)
        # We start this task early so it runs while the main answer is streaming.
        suggest_task = asyncio.create_task(asyncio.to_thread(self._generate_suggestions, state.query, context_text))

        # 4. Select Prompt based on Intent
        if state.intent in ("DEBUG", "REVIEW"):
            # Diagnostic mode. The learner has a specific artifact and a specific problem,
            # so the goal is diagnosis and repair, not exposition: what's wrong -> why
            # (targeted) -> how to fix (a hint, not the solution) -> optional apply-the-fix.
            # Deliberately NOT the concept scaffold: use-cases and diagrams serve acquisition,
            # not debugging, and only dilute the fix.
            # The debug path emits repair guidance, so it is governed by the same
            # cap as exposition. Without this a learner capped at HINT could
            # reach full disclosure simply by pasting code and asking why it
            # breaks — the same rephrasing bypass the scaffolding door had.
            prompt = self._build_diagnostic_prompt(state.query, context_text, state.user_goal,
                                                   state.profile, rung_cap=state.rung_cap)
            style_used = None
        elif state.intent == "COMPLEX_PROBLEM":
            # Provide a high-level architectural plan without scaffolding
            # Emits a "Starter Skeleton" of real C, so it is capped too.
            prompt = self._build_complex_plan_prompt(state.query, context_text,
                                                     state.user_goal, rung_cap=state.rung_cap)
            style_used = None
        else:
            # Default to Concept Explanation
            prompt, style_used = self._build_concept_prompt(
                state.query, context_text, state.user_goal, state.profile,
                state.original_query, state.entities,
                mastery_level=state.mastery_level, mastery_detail=state.mastery_detail,
                mastery_weak_tier=state.mastery_weak_tier,
                calibration_state=state.calibration_state,
                calibration_detail=state.calibration_detail,
                rung_cap=state.rung_cap,
                redirect_to=state.redirect_to, redirect_from=state.redirect_from,
            )

        # 5. Stream Answer
        yield {"type": "status", "message": "Generating...", "percent": 100}
        full_answer = ""
        async for token in self.llm.stream_response_async(prompt):
            full_answer += token
            yield {"type": "token", "text": token}

        # 6. Auto-Learn (If Concept)
        # If the user asked about a specific concept, we assume they are learning it now.
        # if state.intent == "CONCEPT" and state.entities:
            #  knowledge_manager.mark_concept_as_known(state.user_id, state.entities[0])

        # 7. Finalize
        state.stop_processing = True

        try:
            suggestions = await asyncio.wait_for(suggest_task, timeout=8.0)
        except (asyncio.TimeoutError, Exception):
            suggest_task.cancel()
            suggestions = ["Tell me more", "Show me an example", "What should I learn next?"]

        yield {
            "type": "complete",
            "data": {
                "answer": full_answer,
                "sources": sources_payload,
                "suggestions": suggestions,
                "style_used": style_used,
                "intent": state.intent,
                "entities": state.entities,
                "session_id": state.session_id
            }
        }

    # --- HELPERS (Moved from main file) ---
    
    def _expand_query_using_graph(self, query: str, initial_entities: List[str]) -> List[str]:
        """
        Uses Neo4j Knowledge Graph to find related concepts or prerequisites.
        Result is used to expand the vector search query.
        """
        expanded_terms = []
        if not initial_entities:
            return []
            
        for entity in initial_entities:
            # Query Neo4j for prerequisites or related concepts
            cypher = "MATCH (target) WHERE toLower(target.name) CONTAINS toLower($name) MATCH (target)-[:REQUIRES_UNDERSTANDING_OF]->(req) RETURN req.name as name"
            try:
                results = self.graph_db.execute_query(cypher, {"name": entity})
                for record in results: 
                    expanded_terms.append(record['name'])
            except Exception as e:
                # Log error but don't crash retrieval
                self.logger.warning(f"Graph expansion failed for {entity}: {e}")
                
        return list(set(expanded_terms))
        
    def _execute_retrieval(self, query: str, intent: str, user_role: str = 'student', existing_entities: List[str] = None) -> List[Dict[str, Any]]:
        """
        Executes the Hybrid Retrieval Strategy:
        1. Extract Entities (if not provided).
        2. Expand Query using Knowledge Graph (Graph Retrieval).
        3. Search Vector Database (Vector Retrieval).
        4. Apply Gatekeeping (Topic Locks & Access Levels).
        """
        if existing_entities:
            entities = existing_entities
        else:
            extract_prompt = f"Extract C terms: '{query}'. Return CSV."
            entities_str = self.llm_interface.generate_response(extract_prompt)
            entities = [e.strip() for e in entities_str.split(',') if e.strip()]
            
        # Graph Expansion
        related_terms = self._expand_query_using_graph(query, entities)
        
        # Vector Search (Primary)
        query_embedding = self.llm_interface.get_embedding(query)
        raw_chunks = self.vector_store.query(query_embedding, top_k=8)
        
        # Vector Search (Expanded)
        if related_terms:
            exp_emb = self.llm_interface.get_embedding(" ".join(related_terms))
            raw_chunks.extend(self.vector_store.query(exp_emb, top_k=3))
        
        # Gatekeeper
        from app.core.settings_manager import settings_manager
        topic_settings = settings_manager.get_settings()
        SCORE_THRESHOLD = 0.22 
        
        valid_chunks = []
        seen_ids = set()
        
        for chunk in raw_chunks:
            cid = chunk.get('metadata', {}).get('chunk_id')
            if cid in seen_ids: continue
            seen_ids.add(cid)
            
            meta = chunk.get('metadata', {})
            
            # Filter low relevance
            if chunk.get('score', 0) <= SCORE_THRESHOLD: continue
            
            # Filter Teacher-Only content
            if user_role == 'student' and meta.get('access_level') == 'teacher': continue
            
            # Filter Locked Topics
            topic = meta.get('topic', 'General')
            if not topic_settings.get(topic, True): continue # Skip if topic is disabled
            
            valid_chunks.append(chunk)
            
        return valid_chunks

    def _generate_suggestions(self, query: str, context: str) -> List[str]:
        """Generates 3 follow-up suggestions based on the context."""
        prompt = f"Based on the student's query and the Reference Material below, generate 3 short follow-up options.\nQuery: \"{query}\"\nReference Material: \"{context}\"\nRULES: 1. STRICT GROUNDING. 2. Option 1 (Curiosity). 3. Option 2 (Next Step). 4. Option 3 (Challenge).\nOUTPUT: Return ONLY a JSON list of 3 strings."
        response = self.llm_interface.generate_response(prompt)
        try: return json.loads(response.replace("```json", "").replace("```", "").strip())
        except: return ["Tell me more", "Example code", "Challenge: Write it"]
 
    def _build_concept_prompt(self, query: str, context: str, user_goal: str = None, profile: Dict[str, Any] = {}, original_query: str = "", entities: List[str] = [], mastery_level: str = "novice", mastery_detail: str = "", mastery_weak_tier: str = "", calibration_state: str = "", calibration_detail: str = "", rung_cap: int = 5, redirect_to: str = "", redirect_from: str = "") -> str:
        
        # =========================================================
        # C_style: Few-Shot Personalization Vector
        # Calculates the argmax of empirical win rates for this user
        # =========================================================
        # Note: In production, this data is aggregated from Mem_LT.
        # We use a default structure if the user is new.
        style_stats = profile.get("style_win_rates", {
            "analogy": {"wins": 0, "total": 0},
            "technical": {"wins": 0, "total": 0},
            "visual": {"wins": 0, "total": 0}
        })
        
        # UCB1 bandit: balance exploitation (win rate) with exploration (underused styles)
        # Score = win_rate + sqrt(2 * ln(N) / n_s)
        # Unvisited styles get infinite score, forcing initial exploration of all 3.
        N = sum(s["total"] for s in style_stats.values())
        best_style = "analogy"  # Global prior (default)
        best_score = -1.0

        for style_name, stats in style_stats.items():
            n_s = stats["total"]
            if n_s == 0:
                best_style = style_name
                best_score = float('inf')
                break
            win_rate = stats["wins"] / n_s
            exploration = math.sqrt(2 * math.log(max(N, 1)) / n_s)
            ucb_score = win_rate + exploration
            if ucb_score > best_score:
                best_score = ucb_score
                best_style = style_name

        self.logger.info(f"🎨 [C_style] UCB1 selected: {best_style.upper()} (Score: {best_score:.3f}, N={N})")

        goal_section = ""
        if user_goal:
            goal_section = f"## Connection to Your Goal\nExplain explicitly how this helps achieve: '{user_goal}'"

        prefs = profile.get("tutor_preferences", {})
        custom_inst = prefs.get("custom_instructions", "").strip()
        frustration_level = profile.get("frustration_level", "normal")
        
        q_lower = original_query.lower()
        is_impatient = (
            prefs.get("concise_mode", False) or 
            profile.get("attention_span") == "short" or 
            any(w in q_lower for w in ["short", "brief", "just", "quick", "syntax only", "too long"])
        )
        is_literal = prefs.get("literal_mode", False)

        # =========================================================
        # 1. HIGH FRUSTRATION OVERRIDE
        # =========================================================
        if frustration_level in ["high", "rage"]:
            style_instruction = (
                "TONE: Extremely empathetic, patient, and non-technical. You are a supportive human mentor stepping away from the keyboard. "
                "DIFFICULTY NORMALIZATION: Explicitly state that this specific concept trips up everyone, and it is normal to be confused."
            )
            format_rules = """
            **STRICT RESPONSE FORMAT (COGNITIVE REFRAME MODE):**
            [Paragraph 1: Validate their frustration and normalize the difficulty.]
            [Paragraph 2: Explain the concept using ONLY a relatable real-world analogy. (e.g., mailboxes, parking spaces).]
            [Paragraph 3: A gentle, non-technical question to see if the analogy made sense.]
            
            CRITICAL WARNING: DO NOT output any C code, syntax, brackets, or semicolons in this response. Strip all code from your explanation.
            """

        # =========================================================
        # 2. IMPATIENT OVERRIDE (TL;DR MODE)
        # =========================================================
        elif is_impatient:
            style_instruction = "TONE: Extremely concise, code-first, no fluff."
            format_rules = """
            **STRICT RESPONSE FORMAT (CONCISE MODE):**
            [Provide the Code Syntax immediately]
            [Provide 1 sentence explaining the syntax]

            WARNING: Do NOT use markdown headers (##).
            """

        # =========================================================
        # 3. STANDARD TUTORING MODE (Applying C_style)
        # =========================================================
        else:
            # We apply the dynamically calculated 'best_style' here!
            if is_literal or best_style == "technical":
                style_instruction = "TONE: Highly technical, literal, precise. DO NOT use metaphors or analogies."
            elif best_style == "visual":
                style_instruction = "TONE: Spatial and structural. Heavily emphasize the visual layout of memory and execution flow."
            else:
                style_instruction = "TONE: Standard academic tone, encouraging. Heavily rely on relatable real-world analogies."

            # =========================================================
            # Mastery-Conditioned Response Adaptation
            # =========================================================
            # Each level states a distinct JOB, not a relative amount of scaffolding.
            # The old definitions were written as a sliding scale ("maximum" / "moderate" /
            # "light" scaffolding), and "moderate" was additionally defined by negation
            # ("do NOT start from absolute zero"). A model cannot act on a quantity it
            # cannot observe, so DEVELOPING collapsed into NOVICE. Each entry below names
            # what the response is FOR, what it may assume, and what it must not do.
            _MASTERY_INSTRUCTIONS = {
                "novice": (
                    "MASTERY ADAPTATION (NOVICE) — YOUR JOB: build this concept from "
                    "nothing. The student has no usable knowledge of it. "
                    "ASSUME: no prior programming knowledge at all. "
                    "DO: define every technical term at first use; carry the idea with one "
                    "concrete real-world analogy; break it into small ordered steps; keep "
                    "the tone patient and encouraging. "
                    "DO NOT: use unexplained jargon, or raise edge cases and exceptions — "
                    "they obscure the main idea before it has landed."
                ),
                "developing": (
                    "MASTERY ADAPTATION (DEVELOPING) — YOUR JOB: consolidate partial "
                    "knowledge. The student has met this concept and can use it in simple "
                    "cases, but their understanding breaks at the boundaries. "
                    "ASSUME: they know the basics, the vocabulary, and elementary syntax. "
                    "DO: explain by tracing what actually happens in memory or in the code, "
                    "step by step; name the misconceptions that commonly appear at this "
                    "stage and why they happen; connect this concept to one they already "
                    "know. "
                    "DO NOT: re-define elementary terms, and DO NOT use real-world "
                    "analogies — a concrete trace of the real mechanism replaces them here."
                ),
                "proficient": (
                    "MASTERY ADAPTATION (PROFICIENT) — YOUR JOB: extend a solid concept "
                    "into depth. The student has demonstrated competence in all three of "
                    "recall, reasoning and writing code. "
                    "ASSUME: everything an introductory course covers. "
                    "DO: teach only what they are unlikely to know — edge cases, undefined "
                    "behaviour, correctness and performance subtleties; use precise "
                    "technical language; be efficient. "
                    "DO NOT: define anything, use analogies, or restate the basic rule."
                ),
                "reviewing": (
                    "MASTERY ADAPTATION (REVIEWING) — YOUR JOB: verify retention. This "
                    "student already CERTIFIED this concept and is refreshing it. "
                    "ASSUME: they knew this thoroughly and may have partially forgotten. "
                    "DO: remind rather than teach — 'remember that...', not 'let me "
                    "explain...'; surface the rules and gotchas most likely to have faded; "
                    "stay brief, they need recall triggers. "
                    "DO NOT: teach ANY new material. This is the one level that introduces "
                    "nothing the student has not already met. That is what separates it "
                    "from PROFICIENT, which does teach, just at depth."
                ),
            }
            style_instruction += "\n\n" + _MASTERY_INSTRUCTIONS.get(mastery_level, _MASTERY_INSTRUCTIONS["novice"])

            # Explicit mastery label for LLM context
            if mastery_detail:
                style_instruction += f"\n\nStudent's Mastery Level: {mastery_level.upper()} — {mastery_detail}"
            else:
                style_instruction += f"\n\nStudent's Mastery Level: {mastery_level.upper()}"

            # Evidence-tier steering: the diversity signal does not only gate the level,
            # it says WHICH competence is lagging. Aim the response at that competence.
            _WEAK_TIER_DIRECTIVE = {
                "quiz": (
                    "WEAKEST EVIDENCE TIER — CONCEPTUAL RECALL: This student's factual/"
                    "conceptual grasp lags behind their practical work. Be explicit about "
                    "definitions, terminology, and the rules that govern this concept, even "
                    "if their code is competent."
                ),
                "micro": (
                    "WEAKEST EVIDENCE TIER — APPLIED REASONING: This student can state the "
                    "concept but struggles to apply it to short problems. Emphasise worked "
                    "reasoning on small concrete cases over further definition."
                ),
                "code": (
                    "WEAKEST EVIDENCE TIER — CODE PRODUCTION: This student can discuss the "
                    "concept but has not demonstrated they can write it. Ground the "
                    "explanation in concrete syntax and steer the challenge toward "
                    "producing code, not recalling facts."
                ),
            }
            if mastery_weak_tier in _WEAK_TIER_DIRECTIVE and mastery_level != "reviewing":
                style_instruction += "\n\n" + _WEAK_TIER_DIRECTIVE[mastery_weak_tier]

            # --- MCN: metacognitive-calibration directive (empty unless over/under) ---
            if calibration_state in ("over", "under"):
                try:
                    from app.core.mcn_service import prompt_directive
                    style_instruction += prompt_directive({"map_C": calibration_state})
                except Exception:
                    pass
            # =========================================================

            format_builder = ["**STRICT RESPONSE FORMAT:**\nYou MUST use ONLY the exact Markdown headers (##) requested below."]

            # =========================================================
            # Four structurally distinct formats — one per mastery level.
            #
            # DESIGN RULE: every level must OWN at least one section no other level emits.
            # Previously `novice` and `developing` shared one branch, so they produced
            # identical section lists and differed only in the name of the closing header.
            # Measured over 120 responses they were indistinguishable on code density
            # (p=0.85), analogies (p=0.72) and edge cases (p=0.79): prose guidance in
            # _MASTERY_INSTRUCTIONS cannot override an identical structural template.
            #
            #   level        owns                     job
            #   novice       Visual Model, Use Cases  build the concept from nothing
            #   developing   Common Mistakes          consolidate partial knowledge
            #   proficient   advanced Example         extend into depth
            #   reviewing    Refresher                verify retention, teach nothing
            #
            # Analogies are a NOVICE-only device. They were previously hardcoded into the
            # shared Explanation string, which is why `developing` kept producing them.
            # =========================================================
            if mastery_level == "reviewing":
                # Job: verify retention. Teaches nothing new.
                format_builder.append(
                    "## Refresher\n"
                    "[Remind — do NOT re-teach. The student already certified this concept. "
                    "State the key rules as reminders ('remember that...'), then the gotchas "
                    "most likely to have faded. Introduce NO new material. "
                    "Include a short code snippet ONLY if it highlights a tricky detail. "
                    "3-6 sentences max.]"
                )
            elif mastery_level == "proficient":
                # Job: extend a solid concept into depth. Teaches advanced material only.
                format_builder.append(
                    "## Explanation\n"
                    "[The student has demonstrated competence in ALL THREE of recall, "
                    "reasoning and code. Skip every definition and every basic. Teach only "
                    "what they are unlikely to know: edge cases, undefined behaviour, "
                    "correctness and performance subtleties. NO ANALOGIES — precise "
                    "technical language only. 3-5 sentences.]"
                )
                if prefs.get("show_example_code", True):
                    format_builder.append(
                        "## Example\n"
                        "[Show an advanced code example built around an edge case or "
                        "non-obvious behaviour — not a demonstration of basic syntax. "
                        "Include brief inline comments.]"
                    )
            elif mastery_level == "developing":
                # Job: consolidate partial knowledge. Assumes the basics are in place; the
                # distinctive device is a concrete step-by-step trace, NOT an analogy.
                if prefs.get("show_explanation", True):
                    format_builder.append(
                        "## Explanation\n"
                        "[The student already knows the basics — do NOT define elementary "
                        "terms and do NOT open with a real-world analogy. Explain by walking "
                        "through what actually happens in memory or in the code, step by "
                        "step. Use correct technical vocabulary, pausing only for terms "
                        "specific to THIS concept. 3-4 sentences.]"
                    )

                # Owned by this level. Partial knowledge fails at the boundaries, so name
                # the boundaries: a novice has no misconceptions to correct yet, and a
                # proficient student meets these as edge cases rather than as mistakes.
                format_builder.append(
                    "## Common Mistakes\n"
                    "[Name the 2-3 errors students most often make with this concept once "
                    "they know the basics, and say briefly why each happens. No analogies.]"
                )

                if user_goal:
                    format_builder.append(goal_section)

                if prefs.get("show_example_code", True):
                    format_builder.append(
                        "## Worked Example\n"
                        "[Show a short code example combining this concept with one the "
                        "student already knows, and trace what it does line by line in "
                        "inline comments.]"
                    )
            else:
                # NOVICE — job: build the concept from nothing. The only level that gets an
                # analogy, a Use Cases section and a Visual Model diagram.
                if prefs.get("show_explanation", True):
                    if is_literal or best_style == "technical":
                        format_builder.append("## Explanation\n[Clear, literal, technical definition. Min 3 sentences. NO ANALOGIES.]")
                    else:
                        format_builder.append(
                            "## Explanation\n"
                            "[Assume NO prior programming knowledge. Define every technical "
                            "term before using it, and carry the idea with ONE concrete "
                            "real-world analogy. Min 3 sentences.]"
                        )

                if prefs.get("show_use_cases", True):
                    format_builder.append("## Use Cases\n[Explain WHEN and WHY this concept is used in real programming.]")

                if user_goal:
                    format_builder.append(goal_section)

                if prefs.get("show_visual_model", True):
                    format_builder.append("## Visual Model\n[Generate a Mermaid.js flowchart explaining the concept.]\n```mermaid\ngraph TD\n   ...\n```")

                if prefs.get("show_example_code", True):
                    format_builder.append("## Example from Class\n[Reference specific code from text]")

            # Mastery-adapted challenge section
            _MASTERY_CHALLENGES = {
                "novice": (
                    "## Your Turn! (Micro-Challenge)\n"
                    "[Write one short, simple challenge question asking the student to write "
                    "a single line of C code related to this topic. Keep it very guided — "
                    "tell them exactly what to declare or print. "
                    "Example format: 'Try it: declare an integer variable called `score`.' "
                    "Your response must end after this question. Do not add any explanation or answer after it.]"
                ),
                "developing": (
                    "## Challenge\n"
                    "[Write a moderate challenge question. The student has some experience — "
                    "ask them to combine 2 concepts or write a small snippet (2-4 lines). "
                    "Do not give the answer. Your response must end after this question.]"
                ),
                "proficient": (
                    "## Challenge\n"
                    "[Write an advanced challenge involving edge cases, subtle bugs, or "
                    "tricky behavior. Assume the student is comfortable with syntax. "
                    "Ask them to predict output, find a bug, or handle a corner case. "
                    "Do not give the answer. Your response must end after this question.]"
                ),
                "reviewing": (
                    "## Quick Check\n"
                    "[Write a quick retention-check question. Since the student previously "
                    "mastered this, ask them to recall a key rule, predict behavior, or "
                    "spot a common mistake. Keep it brief. "
                    "Do not give the answer. Your response must end after this question.]"
                ),
            }
            format_builder.append(_MASTERY_CHALLENGES.get(mastery_level, _MASTERY_CHALLENGES["novice"]))

            # CAC: drop the sections this turn's disclosure cap does not permit.
            # Removal only — the cap can never ADD a section, which is the same
            # one-way property _tighten() enforces on the decision itself. The
            # leading preamble carries no "## " header and is always kept, as is
            # any header cac_graph has not classified.
            if rung_cap < int(Rung.CODE):
                _cap = Rung(rung_cap)
                format_builder = [
                    s for s in format_builder
                    if not s.lstrip().startswith("## ")
                    or section_allowed(s.lstrip()[3:].split("\n", 1)[0], _cap)
                ]

            format_rules = "\n\n".join(format_builder)

        # CAC disclosure cap, appended LAST so it outranks every formatting
        # instruction above it. This placement is deliberate: the frustration and
        # impatient branches replace `format_rules` wholesale rather than building
        # it up, so a cap applied inside the mastery branch alone would be silently
        # discarded on exactly the turns where a learner is most likely to be
        # reaching for an answer they have not attempted.
        if rung_cap < int(Rung.CODE):
            format_rules += disclosure_directive(Rung(rung_cap))

        # CAC horizon redirect (Phase 3). Appended after the disclosure limit so
        # both constraints survive: a redirected turn may ALSO be rung-capped, and
        # the two narrow different things — what is taught, and how much of it.
        if redirect_to and redirect_from:
            format_rules += redirect_preamble(redirect_from, redirect_to)

        if custom_inst:
            style_instruction += f"\n\n**STUDENT'S CUSTOM INSTRUCTIONS:**\n{custom_inst}"

        current_topic = entities[0] if entities else "C Programming"

        return f"""
        You are an expert C Programming Tutor.
        
        Student's Emotional State: {frustration_level.upper()}
        Current Topic Being Learned: "{current_topic}"
        Student's Message: "{query}"
        
        Reference Material: {context}

        **ADAPTIVE STYLE INSTRUCTIONS:**
        {style_instruction}

        **MANDATORY RULES:**
        1. **STRICT LIMITATION:** Check the Reference Material. If the concept is NOT present, say: "I don't have information..."
        2. **STAY ON TOPIC:** Frame your response around the 'Current Topic Being Learned'. Do not randomly switch topics.
        
        {format_rules}
        """, best_style

    def _build_diagnostic_prompt(self, query: str, context: str, user_goal: str = None,
                                 profile: Dict[str, Any] = {},
                                 rung_cap: int = 5) -> str:
        """DEBUG / REVIEW mode. The learner has a concrete artifact and a concrete problem;
        the job is to diagnose and guide repair, not to teach the topic from scratch.

        Structure: What's Wrong -> Why (targeted) -> How to Fix (a hint, not a full solution)
        -> optional Apply the Fix. It intentionally OMITS the concept scaffold — Use Cases,
        Visual Model / Mermaid diagram, and the generic Micro-Challenge — because those serve
        concept acquisition, not debugging, and only dilute the fix. Sections are gated
        through `tutor_preferences`, mirroring `_build_concept_prompt`, so an instructor can
        turn the targeted 'Why' or the 'Apply the Fix' step on or off.
        """
        prefs = profile.get("tutor_preferences", {}) if profile else {}
        frustration_level = profile.get("frustration_level", "normal") if profile else "normal"

        tone = "Supportive, precise, and focused. Speak directly to the student using 'you'."
        if frustration_level in ["high", "rage"]:
            tone = ("Extra patient — the student is frustrated. Normalise the mistake (this bug "
                    "catches everyone) before the diagnosis, and keep it calm and concrete.")

        goal_line = f"\n        Student's Goal: '{user_goal}'" if user_goal else ""

        fmt = ["**STRICT RESPONSE FORMAT — use ONLY the exact Markdown headers (##) below, in this order:**",
               "## What's Wrong\n[Identify the specific error(s) in the student's code. Quote the exact token, line, or construct at fault. If there are several, use a short bulleted list. This is a diagnosis — do not dwell on what the code does correctly.]"]
        if prefs.get("show_explanation", True):
            fmt.append("## Why\n[Explain the reason THIS specific error is wrong — only the concept behind this bug. Keep it targeted: 2-4 sentences. Do NOT give a general tutorial on the topic, and include no use-cases and no diagram.]")
        fmt.append("## How to Fix\n[Guide the student to the correction: state what must change and why it resolves the error. Do NOT rewrite their whole program or hand over a complete corrected solution — give the rule and a targeted hint so they make the edit themselves.]")
        if prefs.get("show_apply_fix", True):
            fmt.append("## Your Turn: Apply the Fix\n[Ask the student to write the single corrected line or construct themselves. One specific instruction. End the response here — do not add the corrected answer after it.]")
        # CAC: same two-step as the concept branch — drop the sections this cap
        # forbids, then state the limit explicitly. Removal only; a cap can
        # never add a section.
        if rung_cap < int(Rung.CODE):
            _cap = Rung(rung_cap)
            fmt = [x for x in fmt
                   if not x.lstrip().startswith("## ")
                   or section_allowed(x.lstrip()[3:].split("\n", 1)[0], _cap)]
        format_rules = "\n\n".join(fmt)
        if rung_cap < int(Rung.CODE):
            format_rules += disclosure_directive(Rung(rung_cap))

        return f"""
        You are an expert C debugging tutor reviewing a student's own code.

        Student's Emotional State: {frustration_level.upper()}
        Student's Message: "{query}"{goal_line}

        Reference Material: {context}

        **TONE:** {tone}

        **MANDATORY RULES:**
        1. This is a DEBUG/REVIEW request, not a concept lesson. Diagnose the student's actual code; do not pivot into a general explanation of the topic.
        2. NO "Use Cases" section. NO flowchart / Mermaid diagram. NO generic micro-challenge unrelated to their bug.
        3. Do NOT provide a full corrected program. Guide the fix and let the student apply it.
        4. If the code is actually correct, say so plainly under "## What's Wrong" (e.g., "Nothing — this compiles and behaves as intended") and note anything risky. Do NOT invent errors.
        5. The student's own code is the primary subject; use the Reference Material only where it helps ground the fix.

        {format_rules}
        """

    def _build_socratic_plan_prompt(self, query: str, context: str, user_goal: str = None) -> str:
        goal_instruction = ""
        if user_goal:
            goal_instruction = f"""
            6. **CONNECT TO USER'S PROJECT:** The student's long-term goal is: "{user_goal}".
               - You MUST explicitly explain how this specific concept helps them achieve "{user_goal}".
            """

        return f"""
        You are an encouraging C Programming Tutor. You are talking DIRECTLY to a junior student.
        
        **YOUR TASK:** Help the student solve their problem: "{query}" using the Reference Material below.

        Reference Material: {context}

        **CRITICAL RULES:**
        1. **TONE:** Be active, encouraging, and direct. Use "You" and "We".
        2. **NO META-TALK:** Do NOT say "Here is a Socratic plan". Just start teaching!
        3. **CONCEPT LIMITATION:** Only use concepts found in the Reference Material.
        4. **SOURCE GROUNDING:** Mention specific variable names/examples from the text.
        5. **TEXT FIRST:** Text explanation MUST come before any diagrams.
        {goal_instruction}

        **STRICT RESPONSE FORMAT:**
        
        ## Strategy
        [Explain the concept enthusiastically. Connect it to their "{user_goal}" project immediately.]

        ## Visual Logic
        ```mermaid
        graph TD
           ...
        ```
        
        ## Implementation Plan
        1. **[Step Name]**: [Description]
           ```c
           // Generic Syntax
           code...
           ```
        
        ## Guiding Question
        [A thoughtful question to check their understanding]
        """

    def _build_complex_plan_prompt(self, query: str, context: str, user_goal: str = None,
                                   rung_cap: int = 5) -> str:
        goal_instruction = ""
        if user_goal:
            goal_instruction = f"""
            5. **Goal Alignment:** Briefly mention how building this connects to their goal: "{user_goal}".
            """

        # CAC: this prompt is one literal rather than a section list, so the cap
        # is applied as an explicit limit appended last — it emits a "Starter
        # Skeleton" of real C, which a capped learner must not receive.
        _cac_limit = (disclosure_directive(Rung(rung_cap))
                      if rung_cap < int(Rung.CODE) else "")

        return f"""
        You are an expert Software Architect and C Programming Tutor.
        
        **YOUR TASK:** The student wants to build a complex system or solve a large problem: "{query}".
        They do NOT want to be guided step-by-step interactively. They want a high-level, precise plan mapping out how to build it from start to finish.

        Reference Material: {context}

        **CRITICAL RULES:**
        1. **NO INTERACTIVE GUIDANCE:** Do not ask them "What do you think is next?". Give them the complete plan upfront.
        2. **BE PRECISE & CONCISE:** Do not write long essays. Use bullet points and clear technical language.
        3. **ARCHITECTURE FIRST:** Start with a Mermaid graph showing the data flow, module structure, or state machine of the complex system.
        4. **CODE SKELETONS ONLY:** Do NOT provide the complete working code. Provide the architectural skeleton (structs, function signatures, main loop).
        {goal_instruction}

        **STRICT RESPONSE FORMAT:**
        
        ## Architectural Overview
        [2-3 sentences explaining the core design pattern or approach to solving the problem.]

        ## System Design (Diagram)
        ```mermaid
        graph TD
           ...
        ```
        (CRITICAL: NO () [] or "" inside node labels to prevent syntax errors. Use generic labels like A[Main Menu])

        ## Implementation Phases
        ### Phase 1: [Name]
        - **Objective:** [What this phase accomplishes]
        - **Key Components:** [e.g., Structs, specific functions needed]
        
        ### Phase 2: [Name]
        - **Objective:** [What this phase accomplishes]
        - **Key Components:** [e.g., File I/O, specific algorithms]
        
        ... [Add more phases as needed, up to 4-5 max]

        ## Starter Skeleton
        ```c
        // Define the core data structures and function prototypes here
        // Leave the implementation logic blank for the student to fill in
        ```
        
        ## Why this approach works
        [1-2 sentences explaining why this specific architecture or plan is robust for C programming.]
        {_cac_limit}
        """