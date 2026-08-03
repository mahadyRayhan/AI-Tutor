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
        # Save Initial State to DB (without steps yet)
        new_plan = {
            "is_active": True,
            "original_problem": state.query,
            "steps": [], 
            "current_step_index": 0,
            "awaiting_forethought": True,
            "steps_generated": False # <--- Tracks if BG task is done
        }
        history_manager.update_session_state(state.user_id, state.session_id, {"active_plan": new_plan})
        
        # Fire background task to generate steps while user is typing
        asyncio.create_task(
            self._generate_and_save_plan_bg(state.query, state.user_id, state.session_id, state.user_role, state.entities)
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

    async def _generate_and_save_plan_bg(self, query: str, user_id: str, session_id: str, user_role: str, entities: List[str]):
        """Runs in the background to generate steps while the user is typing their forethought answer."""
        try:
            self.logger.info("⚙️ [BG TASK] Generating scaffolding steps in the background...")
            
            # 1. Retrieve context
            chunks = self._internal_retrieval(query, "PROBLEM", user_role, entities)
            context_text = "\n".join([c['text'] for c in chunks])
            
            # 2. Generate the 5-step plan
            steps = self._generate_step_by_step_plan(query, context_text)
            
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
            answer_text = f"{affirmation}\n\nI've broken this problem down into **{num_steps} steps**.\n\n---\n### Step 1: {first_step['goal']}\n{first_step['description']}\n\n👉 *Reply with your code or logic for just this step.*"
            
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

        evaluation = await asyncio.to_thread(self._evaluate_step_progress, state.query, current_step, context_text, current_fails)
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
                    
                    msg = "🎉 **All tests passed! Your code works perfectly.**\n\n"
                    msg += "But before we close this out and award your Mastery XP, I want *you* to generate the summary, not me. "
                    msg += f"In one or two sentences, explain how the different pieces of this program work together to solve the problem."
                    
                    yield {"type": "complete", "data": {
                        "answer": msg, 
                        "sources": [], 
                        "intent": "REFLECTION", 
                        "suggestions": ["I'm not sure how to summarize it."],
                        "entities": state.entities
                    }}
                    return
                else:
                    topic_credit = active_plan.get('original_problem', 'General')
                    topic_label = state.entities[0] if state.entities else topic_credit[:20]

                    history_manager.update_session_state(state.user_id, state.session_id, {"active_plan": {"is_active": False}})

                    # BKT is the sole mastery authority. Solving one problem is strong
                    # evidence but not certification — only claim "mastered" (and write it)
                    # when BKT agrees, otherwise praise the progress without over-claiming
                    # (previously this wrote a fake "Solved: X" row and asserted mastery,
                    # contradicting the dashboard — Module-B F3-05/F4-01).
                    from app.core.bkt_model import bkt
                    if bkt.is_mastered(state.user_id, topic_label):
                        knowledge_manager.mark_concept_as_known(state.user_id, topic_label)
                        msg = f"That is an accurate and well-organized summary! 🏆\n\n"
                        msg += f"You've officially mastered **{topic_label}**. The logic you just derived applies to many other problems in C. "
                        msg += f"Next time you build something for your goal, you will find this mental model transfers directly."
                    else:
                        msg = f"That is an accurate and well-organized summary! 🎯\n\n"
                        msg += f"Great work reasoning through **{topic_label}** — that's real progress. Keep practicing it a little more and you'll have it fully mastered. "
                        msg += f"The mental model you just built will transfer directly to other problems for your goal."
                    
                    yield {"type": "complete", "data": {
                        "answer": msg, 
                        "sources": [], 
                        "intent": "REFLECTION", 
                        "suggestions": ["What should I learn next?", "Show my progress"],
                        "entities": state.entities
                    }}
                    return
            else:
                # =========================================================
                # SAGE PDF PAGE 16: DEEP REASONING QUESTIONS
                # =========================================================
                next_step = steps[idx]
                answer_text += f"\n\n✅ **Correct!**\n\n"
                
                prompt = f"""
                The student just correctly completed this step: "{current_step['goal']}".
                The NEXT step they need to do is: "{next_step['goal']}".
                
                Generate a short message to transition them. 
                CRITICAL RULE: Before introducing the next step, ask ONE "Deep Reasoning" question about the code they just wrote. 
                Choose ONE of these formats randomly:
                - WHAT-IF: "What would happen to your code if [edge case occurs]?"
                - WHY: "Why did we use [specific syntax they just wrote] instead of [alternative]?"
                
                Then, introduce the next step: "{next_step['description']}". {self._preference_directive(state.profile)}
                """
                deep_transition = await asyncio.to_thread(self.llm.generate_response, prompt)
                answer_text += deep_transition
                
                active_plan['current_step_index'] = idx
                history_manager.update_session_state(state.user_id, state.session_id, {"active_plan": active_plan})
        else:
            # =========================================================
            # C_scaff: Remediation Escalation
            # Trigger if Fail_v >= 3 OR M_state == Helplessness
            # =========================================================
            failed_attempts = active_plan.get('failed_attempts', 0) + 1
            active_plan['failed_attempts'] = failed_attempts
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

    def _generate_step_by_step_plan(self, query: str, context: str) -> List[Dict[str, str]]:
        """Generates a JSON plan of 3-6 steps."""
        prompt = f"""
        You are a friendly C Programming Tutor designing a lesson plan.
        Problem: "{query}"
        Context: {context}

        **TASK:** Break this problem down into small, logical coding steps for a beginner.
        DO NOT WRITE CODE. Just define the sub-goals.

        **Requirements:**
        1. 3 to 6 steps max.
        2. Start with Data Structure/Variables.
        3. End with Printing Output.
        4. Each step must be a specific, verifiable task (e.g., "Write the function signature for Average").

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

    def _evaluate_step_progress(self, user_input: str, current_step: Dict, context: str, failed_attempts: int) -> Dict[str, Any]:
        """Evaluates student progress on the current step."""

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