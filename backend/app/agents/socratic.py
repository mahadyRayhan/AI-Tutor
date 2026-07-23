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
        if state.intent == "DEBUG":
            # Reuse Socratic Plan prompt for debugging logic as it encourages step-by-step thinking
            prompt = self._build_socratic_plan_prompt(state.query, context_text, state.user_goal)
            style_used = None
        elif state.intent == "COMPLEX_PROBLEM":
            # Provide a high-level architectural plan without scaffolding
            prompt = self._build_complex_plan_prompt(state.query, context_text, state.user_goal)
            style_used = None
        else:
            # Default to Concept Explanation
            prompt, style_used = self._build_concept_prompt(
                state.query, context_text, state.user_goal, state.profile,
                state.original_query, state.entities,
                mastery_level=state.mastery_level, mastery_detail=state.mastery_detail,
                calibration_state=state.calibration_state,
                calibration_detail=state.calibration_detail,
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
 
    def _build_concept_prompt(self, query: str, context: str, user_goal: str = None, profile: Dict[str, Any] = {}, original_query: str = "", entities: List[str] = [], mastery_level: str = "novice", mastery_detail: str = "", calibration_state: str = "", calibration_detail: str = "") -> str:
        
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
            _MASTERY_INSTRUCTIONS = {
                "novice": (
                    "MASTERY ADAPTATION (NOVICE — maximum scaffolding): "
                    "Provide maximum scaffolding. Use simple real-world analogies. "
                    "Break complex ideas into small steps. Define every technical term before using it. "
                    "Assume NO prior programming knowledge. Use encouraging, patient language."
                ),
                "developing": (
                    "MASTERY ADAPTATION (DEVELOPING — moderate scaffolding): "
                    "Do NOT start from absolute zero — the student has some experience. "
                    "Build on what they already know. Fill knowledge gaps with moderate scaffolding. "
                    "Use more technical language but still explain non-obvious terms. "
                    "Reference concepts they have practiced before."
                ),
                "proficient": (
                    "MASTERY ADAPTATION (PROFICIENT — light scaffolding): "
                    "Do NOT over-explain basics the student already knows. "
                    "Focus on nuances, edge cases, and advanced patterns. "
                    "Use precise technical language. Keep explanations focused and efficient. "
                    "Challenge them with deeper reasoning."
                ),
                "reviewing": (
                    "MASTERY ADAPTATION (REVIEWING — concise refresher): "
                    "This student previously mastered this concept and is refreshing. "
                    "Do NOT re-teach from scratch. Provide a concise refresher of key points. "
                    "Focus on common gotchas and subtle edge cases. "
                    "Frame as 'remember that...' not 'let me explain...'. "
                    "Keep it brief — they need recall triggers, not full instruction."
                ),
            }
            style_instruction += "\n\n" + _MASTERY_INSTRUCTIONS.get(mastery_level, _MASTERY_INSTRUCTIONS["novice"])

            # Explicit mastery label for LLM context
            if mastery_detail:
                style_instruction += f"\n\nStudent's Mastery Level: {mastery_level.upper()} — {mastery_detail}"
            else:
                style_instruction += f"\n\nStudent's Mastery Level: {mastery_level.upper()}"

            # --- MCN: metacognitive-calibration directive (empty unless over/under) ---
            if calibration_state in ("over", "under"):
                try:
                    from app.core.mcn_service import prompt_directive
                    style_instruction += prompt_directive({"map_C": calibration_state})
                except Exception:
                    pass
            # =========================================================

            format_builder = ["**STRICT RESPONSE FORMAT:**\nYou MUST use ONLY the exact Markdown headers (##) requested below."]

            if mastery_level == "reviewing":
                # Reviewing: compact 2-section format — refresher + quick check
                format_builder.append(
                    "## Refresher\n"
                    "[Concise refresher of this concept's key points, common gotchas, "
                    "and subtle edge cases. Use 'remember that...' framing. "
                    "Include a short code snippet ONLY if it highlights a tricky detail. "
                    "3-6 sentences max.]"
                )
            elif mastery_level == "proficient":
                # Proficient: focused 3-section format — explanation + example + challenge
                format_builder.append(
                    "## Explanation\n"
                    "[Focus on nuances, edge cases, and advanced patterns. "
                    "Skip basic definitions — go straight to what's interesting. "
                    "3-5 sentences.]"
                )
                if prefs.get("show_example_code", True):
                    format_builder.append(
                        "## Example\n"
                        "[Show an intermediate-to-advanced code example that demonstrates "
                        "edge cases or non-obvious behavior. Include brief inline comments.]"
                    )
            else:
                # Novice / Developing: full section format
                if prefs.get("show_explanation", True):
                    if is_literal or best_style == "technical":
                        format_builder.append("## Explanation\n[Clear, literal, technical definition. Min 3 sentences. NO ANALOGIES.]")
                    else:
                        format_builder.append("## Explanation\n[Clear text explanation. Min 3 sentences. Use relatable real-world analogies.]")

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
            
            format_rules = "\n\n".join(format_builder)

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

    def _build_complex_plan_prompt(self, query: str, context: str, user_goal: str = None) -> str:
        goal_instruction = ""
        if user_goal:
            goal_instruction = f"""
            5. **Goal Alignment:** Briefly mention how building this connects to their goal: "{user_goal}".
            """

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
        """