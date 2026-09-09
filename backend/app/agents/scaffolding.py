# Guided Mode Agent
# backend/app/agents/scaffolding.py

import json
import asyncio
import re
from typing import AsyncGenerator, List, Dict, Any
from app.agents.base import BaseAgent
from app.agents.schema import AgentState
from app.core.history_manager import history_manager
from app.core.user_knowledge_manager import knowledge_manager
from app.db.vector_store import VectorStore
from app.db.graph_db import Neo4jGraphDB

class ScaffoldingAgent(BaseAgent):
    """
    The Scaffolding Agent is responsible for breaking down complex problems into manageable steps (Scaffolding).
    
    It handles:
    1. Detecting complex problem-solving requests (e.g., "Write a program to...").
    2. Generating a step-by-step implementation plan using the LLM.
    3. storing the plan in the session state.
    4. Guiding the student through each step, validating their progress.
    5. Awarding XP/Mastery upon completion of the entire plan.
    """
    def __init__(self, llm, logger, vector_store: VectorStore, graph_db: Neo4jGraphDB):
        super().__init__(llm, logger)
        self.vector_store = vector_store
        self.graph_db = graph_db

    async def process(self, state: AgentState) -> AsyncGenerator[dict, None]:
        """
        Main processing loop for the Scaffolding Agent.
        
        Args:
            state (AgentState): The current state of the agent workflow.
            
        Yields:
            dict: Workflow events.
        """
        # 1. Check if a plan is already active for this user/session
        current_session_state = history_manager.get_session_state(state.user_id, state.session_id)
        active_plan = current_session_state.get("active_plan")

        if active_plan and active_plan.get("is_active"):
            # If so, continue the existing plan
            async for event in self._continue_plan(state, active_plan):
                yield event
            state.stop_processing = True
            return

        # 2. Check for NEW Plan Trigger
        # We look for explicit "Write a program" requests OR "PROBLEM" intent with sufficient complexity
        strong_keywords = ["write a program", "write a c program", "create a program", "exercise", "code for", "solve"]
        is_explicit_problem = any(k in state.query.lower() for k in strong_keywords)
        
        # Simple heuristic: If query is long (>8 words), it might need breakdown
        is_complex = len(state.query.split()) > 8 
        
        # NOTE: We ONLY trigger for normal PROBLEM. COMPLEX_PROBLEM goes to the Socratic agent for an unguided plan.
        should_trigger = (state.intent == "PROBLEM" and is_complex) or (state.intent == "PROBLEM" and is_explicit_problem)

        if should_trigger:
            # Force intent to PROBLEM for consistency
            state.intent = "PROBLEM"
            # Start a new guided plan
            async for event in self._start_new_plan(state):
                yield event
            state.stop_processing = True
            return

    # --- INTERNAL LOGIC: START PLAN ---
    async def _start_new_plan(self, state: AgentState):
        """
        Initiates a new guided learning plan instantly.
        """
        # If this problem came from a prelab handout, recover the instructor's
        # CONCEPTS and RUBRIC. Resolved once, here, and stored ON the plan: later
        # turns carry the student's answer rather than the prompt, so a lookup
        # then would find nothing.
        match = self._prelab_match(state.original_query or state.query)
        constraints = match.get("constraints", {})

        # Save Initial State to DB (without steps yet)
        new_plan = {
            "is_active": True,
            "original_problem": state.query,
            "steps": [], 
            "current_step_index": 0,
            "awaiting_forethought": True,
            "steps_generated": False, # <--- Tracks if BG task is done
            "constraints": constraints,
            # Where finished work gets filed. Empty for a learner's own question,
            # which is exactly the case that must NOT produce a graded row.
            "prelab_video_filename": match.get("video_filename", ""),
            "prelab_video_title": match.get("video_title", ""),
            "prelab_chapter_title": match.get("chapter_title", ""),
            "prelab_sample_output": match.get("sample_output", ""),
            "total_fails": 0,
        }
        history_manager.update_session_state(state.user_id, state.session_id, {"active_plan": new_plan})
        
        # Fire background task to generate steps while user is typing
        asyncio.create_task(
            self._generate_and_save_plan_bg(state.query, state.user_id, state.session_id,
                                            state.user_role, state.entities, constraints)
        )
        
        # Present the Forethought Prompt IMMEDIATELY (Zero latency)
        msg = f"This is a complex problem! 🧠\n\nI'm going to break this down into manageable steps for us to tackle.\n\n"
        msg += f"But before we write a single line of code, I want you to tell me in your own words: **How do you think we should approach solving this?** What is the core logic?"

        yield {"type": "complete", "data": {
            "answer": msg,
            "sources": [], 
            "suggestions": ["I have no idea", "Stop guided mode"],
            "intent": "PLANNING",
            "entities": state.entities
        }}

    async def _generate_and_save_plan_bg(self, query: str, user_id: str, session_id: str,
                                         user_role: str, entities: List[str],
                                         constraints: Dict[str, Any] = None):
        """Runs in the background to generate steps while the user is typing their forethought answer."""
        try:
            self.logger.info("⚙️ [BG TASK] Generating scaffolding steps in the background...")
            
            # 1. Retrieve context
            chunks = self._internal_retrieval(query, "PROBLEM", user_role, entities)
            context_text = "\n".join([c['text'] for c in chunks])
            
            # 2. Generate the 5-step plan
            steps = self._generate_step_by_step_plan(query, context_text, constraints)
            
            # 3. Save the generated steps safely to SQLite
            current_state = history_manager.get_session_state(user_id, session_id)
            if "active_plan" in current_state:
                current_state["active_plan"]["steps"] = steps
                current_state["active_plan"]["steps_generated"] = True
                history_manager.update_session_state(user_id, session_id, {"active_plan": current_state["active_plan"]})
                
            self.logger.info("✅ [BG TASK] Background scaffolding generation complete!")
        except Exception as e:
            self.logger.error(f"❌ [BG TASK] Failed: {e}")

    # In backend/app/agents/scaffolding.py

    async def _continue_plan(self, state: AgentState, active_plan):
        """
        Evaluates the user's response to the current step and moves to the next one if valid.
        Includes Agency-Driven Escalation Protocol.
        """
        # Exit Check (User wants to bail out completely)
        if any(w in state.query.lower() for w in ["stop", "cancel", "quit", "reset"]):
            history_manager.update_session_state(state.user_id, state.session_id, {"active_plan": {"is_active": False}})
            yield {"type": "complete", "data": {"answer": "Guided mode cancelled.", "sources": [], "intent": "General", "entities": state.entities}}
            return

        # =========================================================
        # SAGE PDF PAGE 14: EVALUATE PRIOR KNOWLEDGE (FORETHOUGHT)
        # =========================================================
        if active_plan.get("awaiting_forethought"):
            
            # --- LATENCY HIDING CHECK ---
            # If the user answered incredibly fast, the background task might still be running.
            if not active_plan.get("steps_generated"):
                yield {"type": "status", "message": "Finalizing your custom learning plan...", "percent": 80}
                # Poll the DB for up to 15 seconds to wait for the BG task
                for _ in range(15):
                    await asyncio.sleep(1)
                    fresh_state = history_manager.get_session_state(state.user_id, state.session_id)
                    active_plan = fresh_state.get("active_plan", {})
                    if active_plan.get("steps_generated"):
                        break
            
            # Turn off the flag so we move to Step 1 next
            active_plan["awaiting_forethought"] = False
            history_manager.update_session_state(state.user_id, state.session_id, {"active_plan": active_plan})
            
            # Grab the steps generated by the background task
            steps = active_plan.get('steps', [])
            first_step = steps[0] if steps else {"goal": "Start Coding", "description": "Write your logic."}
            num_steps = len(steps) if steps else 1
            
            prompt = f"""
            The student was asked to explain the core logic of solving "{active_plan['original_problem']}" before writing any code.
            Student's answer: "{state.query}"

            Write a brief, encouraging response (1-2 sentences) affirming their logic (or gently correcting them if they said 'I don't know').
            Do NOT write code. {self._preference_directive(state.profile)}
            """
            affirmation = await asyncio.to_thread(self.llm.generate_response, prompt)
            
            # Combine the affirmation with the actual Step 1
            answer_text = (f"{affirmation}\n\nI've broken this problem down into "
                           f"**{num_steps} steps**."
                           + self._step_header(0, num_steps, first_step))
            
            yield {"type": "complete", "data": {
                "answer": answer_text, 
                "sources": [], 
                "intent": "GUIDED_PRACTICE", 
                "suggestions": ["Show me Pseudocode", "Stop guided mode"],
                "entities": state.entities
            }}
            return

        # =========================================================
        # --- HANDLE ESCALATION CHOICES ---
        # =========================================================
        if active_plan.get('awaiting_escalation_choice'):
            user_input = state.query.lower()
            
            # Choice 1: They want the TA Summary
            if "ta" in user_input or "message" in user_input or "instructor" in user_input or "help me message" in user_input:
                active_plan['awaiting_escalation_choice'] = False
                active_plan['failed_attempts'] = 0 # Reset so they can try again later if they want
                history_manager.update_session_state(state.user_id, state.session_id, {"active_plan": active_plan})
                
                current_step = active_plan['steps'][active_plan['current_step_index']]
                msg = "**Here is a summary you can copy-paste to your TA:**\n\n"
                msg += f"> *\"Hi, I am trying to build a program that {active_plan.get('original_problem', 'does this')}. I am stuck on the step where I need to: {current_step['goal']}. Could you help me understand the logic?\"*\n\n"
                msg += "Whenever you are ready to try again, just type your next attempt below!"
                
                yield {"type": "complete", "data": {"answer": msg, "sources": [], "intent": "GUIDED_PRACTICE", "suggestions": ["Stop guided mode"], "entities": state.entities}}
                return

            # Choice 2: They want Partial Code
            elif "code" in user_input or "partial" in user_input or "give" in user_input:
                active_plan['awaiting_escalation_choice'] = False
                active_plan['failed_attempts'] = 0 
                history_manager.update_session_state(state.user_id, state.session_id, {"active_plan": active_plan})
                
                current_step = active_plan['steps'][active_plan['current_step_index']]
                
                yield {"type": "status", "message": "Generating partial code...", "percent": 50}
                
                prompt = f"""
                You are a C Tutor. The student is stuck on this step: "{current_step['goal']}".
                Task: "{current_step['description']}"
                
                Generate a PARTIAL C code snippet that helps them complete this exact step.
                Use `___` or `// TODO:` for the parts the student still needs to figure out.
                Do not give the complete working answer. Add 1 sentence of encouragement.
                Format the code in standard markdown ```c ... ```. {self._preference_directive(state.profile)}
                """
                partial_code_response = await asyncio.to_thread(self.llm.generate_response, prompt)
                
                yield {"type": "complete", "data": {
                    "answer": partial_code_response + "\n\n👉 *Fill in the blanks and reply with your updated code!*", 
                    "sources": [], 
                    "intent": "GUIDED_PRACTICE", 
                    "suggestions": ["Stop guided mode"],
                    "entities": state.entities
                }}
                return
            
            else:
                active_plan['awaiting_escalation_choice'] = False
                history_manager.update_session_state(state.user_id, state.session_id, {"active_plan": active_plan})

        # --- NORMAL STEP EVALUATION ---
        yield {"type": "status", "message": "Checking your step...", "percent": 20}
        
        steps = active_plan['steps']
        idx = active_plan['current_step_index']
        current_step = steps[idx]

        search_q = f"{current_step['goal']} {state.query}"
        chunks = self._internal_retrieval(search_q, "DEBUG", state.user_role, [])
        context_text = "\n".join([c['text'] for c in chunks])

        current_fails = active_plan.get('failed_attempts', 0)

        evaluation = await asyncio.to_thread(
            self._evaluate_step_progress, state.query, current_step, context_text,
            current_fails, active_plan.get("constraints"))
        answer_text = evaluation.get('feedback', "I couldn't verify that automatically.")
        
        sugg_list = ["I'm stuck", "Stop guided mode"]

        if evaluation.get('status') == "PASS":
            active_plan['failed_attempts'] = 0
            
            idx += 1
            if idx >= len(steps):
                # =========================================================
                # SAGE PDF PAGE 18: WRAP-UP & SUMMARY GENERATION
                # =========================================================
                if not active_plan.get("awaiting_reflection"):
                    active_plan["awaiting_reflection"] = True
                    history_manager.update_session_state(state.user_id, state.session_id, {"active_plan": active_plan})

                    # The run IS the submission. Captured HERE, at the last passing
                    # step, rather than after the reflection below: a student who
                    # closes the tab without writing their summary has still done
                    # the work and must not be recorded as having done nothing.
                    # No-ops unless the plan matched a stored handout.
                    await self._capture_submission(state, active_plan)
                    
                    msg = "🎉 **All tests passed! Your code works perfectly.**\n\n"
                    msg += "Two short questions before we close this out — I want *you* to do the explaining, not me.\n\n"
                    msg += "**First, the technical one:** in one or two sentences, how do the different pieces of this program work together to solve the problem?"

                    yield {"type": "complete", "data": {
                        "answer": msg, 
                        "sources": [], 
                        "intent": "REFLECTION", 
                        "suggestions": ["I'm not sure how to summarize it."],
                        "entities": state.entities
                    }}
                    return

                # Second reflection: the same program, explained to someone who has
                # never written code. Restored because it is the one that actually
                # separates understanding from recall — a student can recite "a while
                # loop with an if/else inside" from the step titles alone, but cannot
                # say WHY it was the right shape without having followed the logic.
                if not active_plan.get("awaiting_plain_explanation"):
                    active_plan["awaiting_plain_explanation"] = True
                    active_plan["technical_reflection"] = state.query
                    history_manager.update_session_state(state.user_id, state.session_id, {"active_plan": active_plan})

                    if self._is_deflection(state.query):
                        msg = ("That's alright — the summary is the hard part, and not "
                               "knowing where to start is normal.\n\n")
                        msg += ("Try it from the other side instead: **explain what you built "
                                "and why, to someone who has never programmed.** No jargon — "
                                "no 'loop', no 'variable'. Just what the program is keeping "
                                "track of, and what it does at each step.")
                    else:
                        msg = "Good — that's the mechanism.\n\n"
                        msg += ("**Now the harder one:** explain what you built and why, to "
                                "someone who has never programmed. No jargon — no 'loop', no "
                                "'variable'. If you can say it plainly, you understand it.")

                    yield {"type": "complete", "data": {
                        "answer": msg,
                        "sources": [],
                        "intent": "REFLECTION",
                        "suggestions": ["Give me an example of what you mean"],
                        "entities": state.entities
                    }}
                    return

                else:
                    topic_credit = active_plan.get('original_problem', 'General')
                    topic_label = state.entities[0] if state.entities else topic_credit[:20]

                    history_manager.update_session_state(state.user_id, state.session_id, {"active_plan": {"is_active": False}})

                    # Their own words about how the program works — the part a
                    # grader cannot get from the code alone. Both answers are kept:
                    # the mechanism and the plain-language version say different
                    # things about whether the student understood what they built.
                    technical = active_plan.get("technical_reflection") or ""
                    plain = state.query or ""
                    combined = (f"**How the pieces work together:**\n{technical}\n\n"
                                f"**Explained without jargon:**\n{plain}")
                    await self._capture_submission(state, active_plan, reflection=combined)

                    # BKT is the sole mastery authority. Solving one problem is strong
                    # evidence but not certification — only claim "mastered" (and write it)
                    # when BKT agrees, otherwise praise the progress without over-claiming
                    # (previously this wrote a fake "Solved: X" row and asserted mastery,
                    # contradicting the dashboard — Module-B F3-05/F4-01).
                    # The opening line used to assert "That is an accurate and
                    # well-organized summary!" unconditionally — including when the
                    # student had written "I'm not sure how to summarize it." Praise
                    # that arrives regardless of what was said teaches the student
                    # that the question was decorative.
                    if self._is_deflection(plain):
                        opener = ("We'll leave the explanation there for now — but come back "
                                  "to it. Being able to say what a program does in plain "
                                  "words is the part that transfers.\n\n")
                    else:
                        opener = "That's a real explanation — nicely done. 🎯\n\n"

                    from app.core.bkt_model import bkt
                    if bkt.is_mastered(state.user_id, topic_label):
                        knowledge_manager.mark_concept_as_known(state.user_id, topic_label)
                        msg = opener
                        msg += f"You've officially mastered **{topic_label}**. 🏆 The logic you just derived applies to many other problems in C. "
                        msg += f"Next time you build something for your goal, you will find this mental model transfers directly."
                    else:
                        msg = opener
                        msg += f"Great work reasoning through **{topic_label}** — that's real progress. Keep practicing it a little more and you'll have it fully mastered. "
                        msg += f"The mental model you just built will transfer directly to other problems for your goal."
                    
                    final_suggestions = ["What should I learn next?", "Show my progress"]
                    another = self._another_prelab(state, active_plan)
                    if another:
                        msg += ("\n\nThere is another practice problem on this lecture "
                                "that uses the same ideas — the fastest way to find out "
                                "whether the model really transferred is to build it again.")
                        # "Prelab:" is the client's cue to show a short label and send
                        # the problem text itself, which routes into guided mode the
                        # same way the classroom handoff does.
                        final_suggestions.insert(0, f"Prelab: {another['prompt']}")

                    yield {"type": "complete", "data": {
                        "answer": msg, 
                        "sources": [], 
                        "intent": "REFLECTION", 
                        "suggestions": final_suggestions,
                        "entities": state.entities
                    }}
                    return
            else:
                # =========================================================
                # SAGE PDF PAGE 16: DEEP REASONING QUESTIONS
                # =========================================================
                next_step = steps[idx]
                answer_text += f"\n\n✅ **Correct!**\n\n"

                # The model writes the reasoning question ONLY. It used to also be
                # asked to "introduce the next step", which is why steps 2..N lost
                # their headings — given the step's description it would paraphrase
                # it into running prose and drop the title and number entirely.
                prompt = f"""
                The student just correctly completed this step: "{current_step['goal']}".

                Write a SHORT transition (2-3 sentences total):
                1. One line of specific praise for what they just wrote.
                2. Then ask exactly ONE "Deep Reasoning" question about that code.
                   Choose ONE format:
                   - WHAT-IF: "What would happen to your code if [edge case occurs]?"
                   - WHY: "Why did we use [specific syntax they just wrote] instead of [alternative]?"

                HARD RULES:
                - Do NOT describe, introduce, or preview the next step. The system
                  prints the next step itself, immediately after your text, and
                  repeating it produces a duplicate.
                - Do NOT mention step numbers or write a heading.
                - Do NOT write any code. {self._preference_directive(state.profile)}
                """
                deep_transition = await asyncio.to_thread(self.llm.generate_response, prompt)
                answer_text += deep_transition.strip()
                answer_text += self._step_header(idx, len(steps), next_step)
                
                active_plan['current_step_index'] = idx
                history_manager.update_session_state(state.user_id, state.session_id, {"active_plan": active_plan})
        else:
            # =========================================================
            # C_scaff: Remediation Escalation
            # Trigger if Fail_v >= 3 OR M_state == Helplessness
            # =========================================================
            failed_attempts = active_plan.get('failed_attempts', 0) + 1
            active_plan['failed_attempts'] = failed_attempts
            # `failed_attempts` resets at every PASS; the grader wants the total.
            active_plan['total_fails'] = active_plan.get('total_fails', 0) + 1
            history_manager.update_session_state(state.user_id, state.session_id, {"active_plan": active_plan})

            # The Math: Force escalation if they are helpless, regardless of attempt count
            if failed_attempts >= 3 or state.m_state == "Helplessness":
                self.logger.info(f"🧗 [C_scaff] Escalating to Micro-Step (Fails: {failed_attempts}, M_state: {state.m_state})")
                
                if state.m_state == "Helplessness":
                    answer_text = f"🛑 **Hold on, it's okay!** I can see you are feeling stuck. Programming is tough, but you can do this.\n\n"
                else:
                    answer_text = f"⚠️ **It looks like we are stuck here.** You've tried this {failed_attempts} times.\n\n"
                    
                answer_text += "**How would you like to proceed?**\n"
                answer_text += "1. **Get Partial Code:** I can give you the code structure for this step with a heavy hint.\n"
                answer_text += "2. **Consult TA:** I can write a summary of what you've tried so far, so you can email your Instructor for human help.\n\n"
                
                active_plan['awaiting_escalation_choice'] = True
                history_manager.update_session_state(state.user_id, state.session_id, {"active_plan": active_plan})

                sugg_list = ["Give me Partial Code", "Help me message the TA", "Stop guided mode"]
            else:
                # Normal Scaffolding Hint (Fail_v = 1 or 2)
                if evaluation.get('visual_aid'):
                    clean_visual = self._clean_guided_visual(evaluation['visual_aid'])
                    answer_text += f"\n\nHere is a visual aid:\n```mermaid\n{clean_visual}\n```"
                elif evaluation.get('pseudocode_hint'):
                    answer_text += f"\n\n💡 **Logic Hint:**\n```text\n{evaluation['pseudocode_hint']}\n```"
                
                sugg_list.insert(0, "Show me Pseudocode")

        formatted_sources = [{'document_name': c['metadata']['document_name'], 'chunk_text': c['text']} for c in chunks]

        yield {"type": "complete", "data": {
            "answer": answer_text, 
            "sources": formatted_sources,
            "suggestions": sugg_list,
            "intent": "GUIDED_PRACTICE",
            "entities": state.entities 
        }}

    # Phrases that are a student declining the question rather than answering it.
    # Deterministic on purpose: this decides only the TONE of the next message and
    # whether the question gets asked a second way, so it is not worth an LLM call
    # — and a wrong guess here must never be expensive.
    _DEFLECTIONS = (
        "i'm not sure", "im not sure", "not sure how", "i don't know", "i dont know",
        "no idea", "idk", "i can't", "i cant", "you tell me", "skip", "pass",
        "i'm not sure how to summarize it", "don't know how to",
    )

    @classmethod
    def _is_deflection(cls, text: str) -> bool:
        """True when the reply declines the question instead of answering it."""
        t = (text or "").strip().lower()
        if len(t) < 15:
            return True
        return any(t.startswith(d) or t == d for d in cls._DEFLECTIONS)

    def _another_prelab(self, state, plan: Dict[str, Any]) -> Dict[str, str]:
        """One more prelab on the same lecture, if there is one they haven't done.

        Returns {} for a learner's own question, and for the last prelab in a set —
        offering "try another" and then having nothing to hand over is worse than
        not offering. The chip carries the whole problem statement as its payload
        because that text is what routes back into guided mode; the label the
        student sees is set on the client.
        """
        try:
            from app.core import prelab_ingest, prelab_submission
            video = plan.get("prelab_video_filename")
            if not video:
                return {}
            done = prelab_submission.solved_prompts(state.user_id, video)
            done.append(plan.get("original_problem") or "")
            nxt = prelab_ingest.next_unsolved(prelab_ingest.load_prelab_file(), video, done)
            if not nxt:
                return {}
            return {"prompt": nxt["prompt"]}
        except Exception as e:
            self.logger.warning(f"[PRELAB] next-prelab lookup failed: {e}")
            return {}

    async def _capture_submission(self, state, plan: Dict[str, Any], reflection: str = None) -> None:
        """File this finished prelab under the student and the video.

        Never raises into the tutor: a capture failure must cost the student a
        grade record, not the rest of their conversation. Non-prelab runs fall
        out inside `prelab_submission`, which has the plan's attribution and can
        tell the two apart.
        """
        try:
            from app.core import prelab_submission
            details = history_manager.get_session_details(state.user_id, state.session_id) or {}
            messages = details.get("messages", [])
            if reflection is None:
                # Grading compiles and runs the student's program, so it goes to a
                # thread: a runaway submission must not stall the event loop for
                # every other student in the class.
                await asyncio.to_thread(prelab_submission.save_solved,
                                        state.user_id, state.session_id, plan, messages)
            else:
                prelab_submission.record_reflection(state.user_id, state.session_id,
                                                    plan, reflection, messages)
        except Exception as e:
            self.logger.error(f"[PRELAB] submission capture failed: {e}")

    @staticmethod
    def _step_header(index: int, total: int, step: Dict[str, str]) -> str:
        """The banner that opens a step, numbered against the whole plan.

        EVERY step gets one, not just the first. Steps 2..N used to be announced
        only by the free-form transition text, and because that text is generated
        prose it slid straight into the next instruction without ever naming it:
        a student saw "Step 1: Initialize Probe Tracking Variables" and then three
        unlabelled walls of paragraph, with no way to tell which step they were on
        or how many were left. The number is structure, so the code emits it.
        """
        return (f"\n\n---\n### Step {index + 1} of {total}: "
                f"{step.get('goal') or 'Next step'}\n"
                f"{step.get('description') or ''}\n\n"
                f"👉 *Reply with your code or logic for just this step.*")

    def _prelab_match(self, text: str) -> Dict[str, Any]:
        """What the stored handout contributes to a plan, when this IS a handout.

        Two things, resolved in one lookup: the instructor's CONCEPTS and RUBRIC
        (which bound the guidance) and where the prelab lives (which is how the
        finished work gets filed under the right video without the client ever
        naming it). Returns {} for anything that is not a stored handout, which
        is the common case — a learner's own question has no rubric and gets the
        unconstrained plan it always got.
        """
        try:
            from app.core import prelab_ingest
            rec = prelab_ingest.find_by_prompt(text, prelab_ingest.load_prelab_file())
            if not rec:
                return {}
            c = prelab_ingest.teaching_constraints(rec)
            self.logger.info(
                f"📋 [PRELAB] matched handout '{rec.get('video_title') or '?'}' — "
                f"{len(c['concepts'])} concept(s), {len(c['rubric'])} rubric item(s) "
                f"will constrain the plan; work will be filed under "
                f"{rec.get('video_filename') or 'no video'}")
            return {
                "constraints": c,
                "video_filename": rec.get("video_filename") or "",
                "video_title": rec.get("video_title") or "",
                "chapter_title": rec.get("chapter_title") or "",
                # Printed in the learner's own handout, so carrying it costs
                # nothing — and it is what the finished program is graded against.
                "sample_output": rec.get("sample_output") or "",
            }
        except Exception as e:
            self.logger.warning(f"[PRELAB] handout lookup failed: {e}")
            return {}

    def _prelab_constraints(self, text: str) -> Dict[str, Any]:
        """Just the teaching constraints. Kept as the narrow entry point."""
        return (self._prelab_match(text) or {}).get("constraints", {})

    @staticmethod
    def _constraint_block(constraints: Dict[str, Any]) -> str:
        """Render the handout's constraints for a prompt, or "" when there are none.

        Concepts are framed as the ALLOWED TOOLSET rather than as a topic list: the
        point of a prelab is to practise what the class has covered, so reaching
        for a construct that has not been taught is a wrong answer even when the
        program works. The rubric's prohibitions are stated separately and last,
        because they are the half a plan most easily violates — a step-by-step
        breakdown that ends in "now print each marker" walks the student directly
        into the fifteen print statements the handout forbids.
        """
        if not constraints:
            return ""
        concepts = constraints.get("concepts") or []
        rubric = constraints.get("rubric") or []
        if not concepts and not rubric:
            return ""

        out = ["\n        **INSTRUCTOR CONSTRAINTS (this problem is a course prelab):**"]
        if concepts:
            out.append("        ALLOWED TOOLSET — the class has covered these, and the")
            out.append("        solution must be built from them. Do NOT introduce any other")
            out.append("        construct (no arrays, functions, or pointers unless listed):")
            for c in concepts:
                out.append(f"          - {c}")
        if rubric:
            out.append("        REQUIREMENTS from the handout. Every step must be consistent")
            out.append("        with these, and any prohibition below is absolute:")
            for r in rubric:
                out.append(f"          - {r}")
        return "\n".join(out) + "\n"

    def _generate_step_by_step_plan(self, query: str, context: str,
                                    constraints: Dict[str, Any] = None) -> List[Dict[str, str]]:
        """Generates a JSON plan of 3-6 steps, bounded by the handout when there is one."""
        prompt = f"""
        You are a friendly C Programming Tutor designing a lesson plan.
        Problem: "{query}"
        Context: {context}
{self._constraint_block(constraints or {})}
        **TASK:** Break this problem down into small, logical coding steps for a beginner.
        DO NOT WRITE CODE. Just define the sub-goals.

        **Requirements:**
        1. 3 to 6 steps max.
        2. Start with Data Structure/Variables.
        3. End with Printing Output.
        4. Each step must be a specific, verifiable task (e.g., "Write the function signature for Average").
        5. If an ALLOWED TOOLSET is given above, every step must be achievable with
           it alone. Prefer the named constructs explicitly (e.g. if `while` is
           listed, the loop step says while, not for).

        **TONE INSTRUCTIONS (CRITICAL):**
        - Write the `description` as if you are talking DIRECTLY to the student.
        - Use "You" and "Let's". Be encouraging and active.
        - **BAD:** "Prompt the user for input."
        - **GOOD:** "Now, let's ask the user for a number. Use printf to show a message and scanf to read it into our variable."

        **OUTPUT FORMAT:**
        Strictly a JSON list of objects:
        [
            {{"goal": "Brief title of step", "description": "Conversational instructions...(What the student needs to do now)", "verification_criteria": "What to check for"}}
        ]
        """
        response = self.llm.generate_response(prompt)
        try:
            clean_json = response.replace("```json", "").replace("```", "").strip()
            start = clean_json.find('[')
            end = clean_json.rfind(']') + 1
            return json.loads(clean_json[start:end])
        except Exception as e:
            self.logger.error(f"Plan generation failed: {e}")
            return [{"goal": "Solve the problem", "description": "Let's write the code together.", "verification_criteria": "Code validity"}]

    def _evaluate_step_progress(self, user_input: str, current_step: Dict, context: str,
                                failed_attempts: int,
                                constraints: Dict[str, Any] = None) -> Dict[str, Any]:
        """Evaluates student progress on the current step.

        `constraints` carries the handout's toolset and requirements when this plan
        came from a prelab. They belong here as well as in plan generation: a step
        can be satisfied by code that works and still breaks the handout — fifteen
        printf lines produce the right output — and passing that teaches the
        student the assignment was about the output rather than the loop.
        """

        # =========================================================
        # SAGE PDF PAGE 13 & 17: THE REMEDIATION LADDER
        # =========================================================
        if failed_attempts == 0:
            remediation_rule = (
                "REMEDIATION LADDER (PUMP): The student just made their first mistake. "
                "DO NOT give the answer. PUMP them with an open question to encourage productive struggle. "
                "(e.g., 'Look at line X. What do you think is happening at the moment you execute that?')"
            )
        elif failed_attempts == 1:
            remediation_rule = (
                "REMEDIATION LADDER (HINT): The student failed twice. Give a DIRECTED CUE. "
                "Narrow their attention to the specific gap without revealing the solution. "
                "(e.g., 'You are right about X, but think about what happens to Y after you overwrite it.')"
            )
        else:
            remediation_rule = (
                "REMEDIATION LADDER (PROMPT): The student is stuck. Give a FILL-IN-THE-BLANK prompt. "
                "Tell them exactly what concept is missing, but make them write the code. "
                "(e.g., 'You need to preserve the reference. What single line of code do you need to add before overwriting X?')"
            )

        prompt = f"""
        You are a C Tutor guiding a student.
        
        **Step Goal:** {current_step['goal']}
        **Task:** {current_step['description']}
        **Criteria:** {current_step['verification_criteria']}
        
        **Student Input:** "{user_input}"
        **Reference Material:** {context}
{self._constraint_block(constraints or {})}
        **TASK:** Evaluate the student's input.

        **CRITICAL RULES:**
        1. **HALLUCINATION CHECK (PRIORITY):**
           - If the student says "I don't know", "Help", or "Where to start":
             - Mark Status as **FAIL**.
             - Acknowledge they are stuck and provide a clear hint.
        
        2. **Status Logic:**
           - PASS: If they provide valid C code/logic that solves the step.
           - IF THIS IS A REFLECTION SUMMARY: Any reasonable English explanation of the code is a **PASS**.
           - FAIL: Wrong code, "I don't know", OR asking for help/pseudocode.
           - FAIL if INSTRUCTOR CONSTRAINTS are given above and the input breaks one,
             EVEN IF the code is correct and produces the right output. Say which
             requirement it breaks and why that requirement exists — do not simply
             reject it. A working solution that sidesteps the point of the exercise
             is the specific thing the handout is trying to prevent.

        3. **IF STATUS IS FAIL, APPLY THIS TUTORING STRATEGY:**
           {remediation_rule}

        4. **VISUAL AID (CRITICAL):** 
           - IF the student is confused, provide a MermaidJS graph string in `visual_aid`.
           - **CRITICAL SANITIZATION RULES:** ABSOLUTELY NO PARENTHESES (), BRACKETS [], OR QUOTES "" inside node labels.
        
        5. **PSEUDOCODE:**
            - IF the student is stuck on syntax or asks for "Logic/Pseudocode", fill the `pseudocode_hint` field.
            - Format: Plain text algorithm (e.g., "FOR i FROM 0 TO N..."). Do not use C syntax.

        **OUTPUT JSON:**
        {{
            "status": "PASS" | "FAIL",
            "feedback": "Response text based on the Remediation Ladder...",
            "visual_aid": "graph TD...", 
            "pseudocode_hint": "..."
        }}
        """
        response = self.llm.generate_response(prompt)
        try:
            clean_json = response.replace("```json", "").replace("```", "").strip()
            start = clean_json.find('{')
            end = clean_json.rfind('}') + 1
            return json.loads(clean_json[start:end])
        except Exception as e:
            self.logger.error(f"Scaffolding JSON parse error: {e}. Raw response: {response}")
            # FIX: Must return a dict with a valid 'status' key!
            return {
                "status": "FAIL", 
                "feedback": "I couldn't quite understand that. Remember, the goal right now is to write the code for this specific step. Give it a try, or type 'help'."
            }

    def _clean_guided_visual(self, text: str) -> str:
        """
        Specific cleaner for the Guided Mode JSON visual_aid field.
        Strips markdown wrappers and fixes syntax.
        """
        if not text: return ""

        text = text.replace("```mermaid", "").replace("```", "").strip()
        text = re.sub(r"(\w+)'([^']+)'", r'\1[\2]', text)

        def strip_inner_quotes(match):
            content = match.group(1)
            clean_content = content.replace('"', '').replace("'", "")
            return f"[{clean_content}]"
            
        text = re.sub(r'\[(.*?)\]', strip_inner_quotes, text)
        return text

    def _internal_retrieval(self, query: str, intent: str, user_role: str, entities: List[str]):
        """
        Local copy of retrieval logic for internal agent use.
        Kept separate so we don't depend on the main agent's implementation.
        """
        # 1. Expand Query with Graph (simplified)
        related_terms = []
        if entities:
            for entity in entities:
                cypher = "MATCH (target) WHERE toLower(target.name) CONTAINS toLower($name) MATCH (target)-[:REQUIRES_UNDERSTANDING_OF]->(req) RETURN req.name as name"
                try:
                    results = self.graph_db.execute_query(cypher, {"name": entity})
                    for record in results: related_terms.append(record['name'])
                except: pass
        
        # 2. Vector Search
        query_embedding = self.llm.get_embedding(query)
        raw_chunks = self.vector_store.query(query_embedding, top_k=5)
        
        if related_terms:
            exp_query = " ".join(related_terms)
            exp_emb = self.llm.get_embedding(exp_query)
            raw_chunks.extend(self.vector_store.query(exp_emb, top_k=2))

        # 3. Basic Gatekeeper (Dedupe)
        seen_ids = set()
        valid_chunks = []
        for chunk in raw_chunks:
            cid = chunk.get('metadata', {}).get('chunk_id')
            if cid not in seen_ids:
                seen_ids.add(cid)
                valid_chunks.append(chunk)
                
        return valid_chunks