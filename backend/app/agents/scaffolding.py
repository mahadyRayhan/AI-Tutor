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
        
        should_trigger = (state.intent == "PROBLEM" and is_complex) or is_explicit_problem

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
        Initiates a new guided learning plan.
        
        1. Searches for context about the problem.
        2. Generates a breakdown of steps using the LLM.
        3. Saves the plan to the persistent session state.
        4. Presents the first step to the user.
        """
        # --- NEW UX FIX: Instant Initial Response ---
        greeting_text = "This is a complex problem! 🧠\n\n To ensure you really learn this, I've broken it down into smaller manageable steps.\n\n"
        
        # Yield the text immediately so it shows up in the chat UI instantly
        yield {"type": "token", "text": greeting_text}
        # -------------------------------------------
        yield {"type": "status", "message": "Planning & Searching...", "percent": 30}
        
        # Parallel Execution: Retrieve Context + Generate Steps
        # 1. Retrieval Task (Simulating _execute_retrieval locally)
        retrieval_task = asyncio.create_task(
            asyncio.to_thread(self._internal_retrieval, state.query, state.intent, state.user_role, state.entities)
        )
        
        # 2. Planning Task (The "Brain" of the operation)
        plan_task = asyncio.create_task(
            asyncio.to_thread(self._generate_step_by_step_plan, state.query, "Standard C Programming Context")
        )

        chunks, steps = await asyncio.gather(retrieval_task, plan_task)

        # Save State to DB
        new_plan = {
            "is_active": True,
            "original_problem": state.query,
            "steps": steps,
            "current_step_index": 0
        }
        history_manager.update_session_state(state.user_id, state.session_id, {"active_plan": new_plan})
        
        # Present Step 1
        first = steps[0]
        msg = f"This is a complex problem! 🧠\n\nTo ensure you really learn this, I've broken it down into **{len(steps)} manageable steps**.\n\n"
        msg += f"### Step 1: {first['goal']}\n{first['description']}\n\n"
        msg += "👉 *Reply with your code or logic for just this step.*"

        yield {"type": "complete", "data": {
            "answer": msg,
            "sources": [], 
            "suggestions": ["I don't know where to start", "Stop guided mode"],
            "intent": "PLANNING",
            "entities": state.entities # <--- ADD THIS
        }}

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
        # --- NEW: STEP 2 (HANDLE ESCALATION CHOICES) ---
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
                history_manager.update_session_state(state.user_id, state.session_id, {"active_plan": active_plan})
                
                current_step = active_plan['steps'][active_plan['current_step_index']]
                
                yield {"type": "status", "message": "Generating partial code...", "percent": 50}
                
                # Ask the LLM to generate the partial code based on the current step
                prompt = f"""
                You are a C Tutor. The student is stuck on this step: "{current_step['goal']}".
                Task: "{current_step['description']}"
                
                Generate a PARTIAL C code snippet that helps them complete this exact step. 
                Use `___` or `// TODO:` for the parts the student still needs to figure out.
                Do not give the complete working answer. Add 1 sentence of encouragement.
                Format the code in standard markdown ```c ... ```.
                """
                # Use to_thread to keep it async friendly
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
                # They ignored the choices and just typed random code. Turn off the flag and evaluate it normally.
                active_plan['awaiting_escalation_choice'] = False
                history_manager.update_session_state(state.user_id, state.session_id, {"active_plan": active_plan})
        # =========================================================

        # --- NORMAL STEP EVALUATION ---
        yield {"type": "status", "message": "Checking your step...", "percent": 20}
        
        steps = active_plan['steps']
        idx = active_plan['current_step_index']
        current_step = steps[idx]

        # Context for evaluation (Search for Step Goal + User Query)
        search_q = f"{current_step['goal']} {state.query}"
        chunks = self._internal_retrieval(search_q, "DEBUG", state.user_role, [])
        context_text = "\n".join([c['text'] for c in chunks])

        # LLM Evaluation of the student's work
        # (Using asyncio.to_thread to prevent blocking the event loop)
        evaluation = await asyncio.to_thread(self._evaluate_step_progress, state.query, current_step, context_text)
        answer_text = evaluation.get('feedback', "I couldn't verify that automatically.")
        
        sugg_list = ["I'm stuck", "Stop guided mode"]

        if evaluation.get('status') == "PASS":
            # --- SUCCESS: Reset strike counter ---
            active_plan['failed_attempts'] = 0
            
            idx += 1
            if idx >= len(steps):
                # ALL STEPS DONE - Victory Lap
                answer_text += "\n\n🎉 **Problem Solved!** You've completed all steps. Excellent work."
                
                topic_credit = active_plan.get('original_problem', 'General')
                topic_label = state.entities[0] if state.entities else topic_credit[:20]
                knowledge_manager.mark_concept_as_known(state.user_id, f"Solved: {topic_label}")
                
                history_manager.update_session_state(state.user_id, state.session_id, {"active_plan": {"is_active": False}})
            else:
                # MOVE TO NEXT STEP
                next_step = steps[idx]
                answer_text += f"\n\n---\n### Next Step ({idx+1}/{len(steps)}): {next_step['goal']}\n{next_step['description']}"
                active_plan['current_step_index'] = idx
                history_manager.update_session_state(state.user_id, state.session_id, {"active_plan": active_plan})
        else:
            # =========================================================
            # --- NEW: STEP 1 (TRIGGER ESCALATION MENU) ---
            # =========================================================
            failed_attempts = active_plan.get('failed_attempts', 0) + 1
            active_plan['failed_attempts'] = failed_attempts
            history_manager.update_session_state(state.user_id, state.session_id, {"active_plan": active_plan})

            if failed_attempts >= 3:
                # TRIGGER ESCALATION PROTOCOL
                answer_text = f"⚠️ **It looks like we are stuck here.** \n\n"
                answer_text += f"You have tried this step {failed_attempts} times. Learning to code is hard, and it's completely okay to hit a wall!\n\n"
                answer_text += "**How would you like to proceed?**\n"
                answer_text += "1. **Get Partial Code:** I can give you the code structure for this step with a heavy hint.\n"
                answer_text += "2. **Consult TA:** I can write a summary of what you've tried so far, so you can email your Instructor for human help.\n\n"
                
                active_plan['awaiting_escalation_choice'] = True
                history_manager.update_session_state(state.user_id, state.session_id, {"active_plan": active_plan})

                sugg_list = ["Give me Partial Code", "Help me message the TA", "Stop guided mode"]
            else:
                # Standard Hint
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

    def _evaluate_step_progress(self, user_input: str, current_step: Dict, context: str) -> Dict[str, Any]:
        """Evaluates student progress on the current step."""
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
             - Do **NOT** say "Good start" or "You declared X".
             - Acknowledge they are stuck and provide a clear hint.
        
        2. **HELP REQUESTS ARE NOT ANSWERS:**
           - If the student asks for "Pseudocode", "Hint", "Example", or "Solution":
             - Mark Status as **FAIL** (This keeps them on the current step).
             - Provide the requested help in `pseudocode_hint` or `visual_aid`.
             - Do **NOT** verify the step as complete.
        
        3. **Status Logic:**
           - PASS: Only if they provide valid C code/logic that solves the step.
           - FAIL: Wrong code, "I don't know", OR asking for help/pseudocode.

        4. **VISUAL AID (CRITICAL):** 
           - IF the student is confused, provide a MermaidJS graph string in `visual_aid`.
           - **CRITICAL SANITIZATION RULES:** 
             - ABSOLUTELY NO PARENTHESES `()` inside node labels. 
             - ABSOLUTELY NO BRACKETS `[]` inside node labels.
             - ABSOLUTELY NO QUOTES `"` inside node labels.
             - **BAD:** `A[sum(a,b)]` or `B{{arr[i]}}` or `C["Text"]`
             - **GOOD:** `A[sum a b]` or `B{{arr index i}}` or `C[Text]`
        
        5. **PSEUDOCODE:**
            - IF the student is stuck on syntax or asks for "Logic/Pseudocode", fill the `pseudocode_hint` field.
            - Format: Plain text algorithm (e.g., "FOR i FROM 0 TO N..."). Do not use C syntax.
        
        6. **USER INTENT:**
            - If user asks for "Pseudocode" specifically, ONLY provide `pseudocode_hint`.
            - If user asks for "Diagram" specifically, ONLY provide `visual_aid`.

        **OUTPUT JSON:**
        {{
            "status": "PASS" | "FAIL" | "QUESTION",
            "feedback": "Response text...",
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