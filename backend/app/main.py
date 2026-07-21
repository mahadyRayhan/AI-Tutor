# backend/app/main.py
import os
import asyncio
import shutil
import subprocess
from fastapi import FastAPI, HTTPException, UploadFile, File, Form 
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import logging
import re
import time
import json
from fastapi.responses import StreamingResponse, FileResponse
from pathlib import Path
from datetime import datetime
import csv

from pyinstrument import Profiler
from fastapi.staticfiles import StaticFiles # Needed to serve the reports
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from fastapi import Header, Depends
from fastapi import Request
from pathlib import Path

# Import components
from app.core import config
from app.db.llm_interface import LLMInterface
from app.db.vector_store import ChromaVectorStore
from app.db.graph_db import Neo4jGraphDB
from app.agents.cot_rag_agent import ChainOfThoughtRAGAgent
from app.core.settings_manager import settings_manager
from app.core.user_manager import user_manager 
from app.core.history_manager import history_manager
from app.core.user_knowledge_manager import knowledge_manager
from app.core.assignment_manager import assignment_manager
from app.db.sqlite_db import db
from app.services.video_service import transcribe_video, get_transcript_until, list_available_videos, get_or_generate_video_meta, find_timestamp_hints, generate_checkpoints, get_video_duration_from_transcript

app = FastAPI(title="C Programming Tutor API", version="2.0.0")

# 2. Setup Static Mount for Profiles
PROFILE_DIR = os.path.join(config.PROJECT_ROOT, "profiling_reports")
if not os.path.exists(PROFILE_DIR):
    os.makedirs(PROFILE_DIR)

# This makes http://localhost:8000/profiles/ accessible
app.mount("/profiles", StaticFiles(directory=PROFILE_DIR), name="profiles")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent  # Points to backend/app/
TEMPLATES_DIR = BASE_DIR / "templates"      # Points to backend/app/templates/
STATIC_DIR = BASE_DIR / "static"          # Points to backend/app/static/

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Mount the static directory
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Video directory (served dynamically, not as a static mount — avoids Docker startup issues)
VIDEO_DIR = config.DB_DIR / "video"
logger.info(f"VIDEO_DIR resolved to: {VIDEO_DIR} (exists: {VIDEO_DIR.exists()})")


def _find_classroom_video(topic: str):
    """Returns the first available classroom video whose topic matches, or None."""
    if not VIDEO_DIR.exists():
        return None
    for v in list_available_videos(str(VIDEO_DIR)):
        if v.get("topic") == topic:
            return v
    return None


def _clean_text_for_tts(raw: str) -> str:
    """Strip markdown/code syntax before sending to TTS so the voice doesn't read symbols aloud."""
    text = re.sub(r'```[\s\S]*?```', 'code example.', raw)      # fenced code blocks → label
    text = re.sub(r'`([^`]+)`', r'\1', text)                    # inline code → plain text
    # Headers: keep the text but add a period so TTS pauses after the category name
    text = re.sub(
        r'^#{1,6}\s+(.*?)$',
        lambda m: m.group(1).rstrip('.:') + '.',
        text, flags=re.MULTILINE
    )
    text = re.sub(r'\*{1,3}(.*?)\*{1,3}', r'\1', text)         # bold / italic
    text = re.sub(r'_{1,3}(.*?)_{1,3}', r'\1', text)
    text = re.sub(r'^>\s?', '', text, flags=re.MULTILINE)       # blockquotes
    text = re.sub(r'^-{3,}$', '', text, flags=re.MULTILINE)     # horizontal rules
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)        # links → display text
    text = re.sub(r'!\[([^\]]*)\]\([^)]+\)', '', text)          # images removed
    text = re.sub(r'<[^>]+>', '', text)                         # HTML tags
    # Emojis — remove so TTS doesn't read "book emoji" or "check mark emoji"
    text = re.sub(r'[^\x00-\x7FÀ-ɏḀ-ỿ]', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# --- Data Models ---
class SignupRequest(BaseModel):
    username: str
    password: str
    name: str
    email: str
    university: Optional[str] = ""
    department: Optional[str] = ""
    interest: Optional[str] = ""

class AdminUserUpdate(BaseModel):
    target_username: str
    new_role: Optional[str] = None
    blocked: Optional[bool] = None

# Global variables
llm_fast = None
llm_smart = None
vector_store = None
graph_db = None
cot_rag_agent = None

class TopicUpdate(BaseModel):
    topic: str
    enabled: bool

class LoginRequest(BaseModel):
    username: str
    password: str
    
class GoalRequest(BaseModel):
    username: str
    goal: str

class FeedbackRequest(BaseModel):
    username: str
    session_id: str
    message_index: int  # Which message in the chat history is this for?
    feedback_type: str  # "up", "down", "simplify", "deep_dive"
    original_query: str # Needed for re-generation
    feedback_text: Optional[str] = None

class ChallengeRequest(BaseModel):
    teacher: str
    student: str
    question: str

class SubmissionRequest(BaseModel):
    assignment_id: str
    answer: str

# RENAMED to avoid conflict
class AssignmentGradeRequest(BaseModel):
    assignment_id: str
    feedback: str

class VideoChatRequest(BaseModel):
    video_filename: str
    question: str
    timestamp: float  # seconds into the video where student paused
    username: Optional[str] = "anonymous"

class ClassroomHistoryRequest(BaseModel):
    username: str
    session_id: Optional[str] = None
    video_title: str
    question: str
    answer: str

class TutorPreferences(BaseModel):
    custom_instructions: str = ""
    show_explanation: bool = True
    show_use_cases: bool = True
    show_visual_model: bool = True
    show_example_code: bool = True
    # NEW: Neurodiversity & Pacing Settings
    literal_mode: bool = False
    concise_mode: bool = False
    dyslexia_font: bool = False
    extra_spacing: bool = False
    high_contrast: bool = False
    break_reminders: bool = False

class PreferencesUpdateRequest(BaseModel):
    username: str
    preferences: TutorPreferences

class TTSSpeakRequest(BaseModel):
    text: str
    voice: str = "nova"

def _calculate_mastery(history: List[Dict]) -> Dict[str, float]:
    """
    Calculates a 0-100 mastery score per topic based on interaction types.
    """
    scores = {}
    topic_interactions = {}

    for h in history:
        # Get Topic and Intent (Default to General/Unknown if missing)
        t = h.get('topic', 'General')
        i = h.get('intent', 'UNKNOWN')
        
        if t not in scores: scores[t] = 0
        if t not in topic_interactions: topic_interactions[t] = 0
        
        topic_interactions[t] += 1
        
        # Scoring Logic (XP Calculation)
        if i == "REVIEW": 
            scores[t] += 15  # Tried writing code (High effort)
        elif i == "PROBLEM":
            scores[t] += 10  # Asked for a plan (Medium effort)
        elif i == "CONCEPT":
            scores[t] += 5   # Reading (Passive)
        elif i == "DEBUG":
            scores[t] += 2   # Struggling (Needs help, but trying)
            
    # Normalize (Simple heuristic: 50 points = 100% mastery for this demo)
    final_scores = {}
    for t, score in scores.items():
        # Cap at 100
        normalized = min(100, score)
        
        # Penalize if they ONLY ask debug questions (High count, low score)
        if topic_interactions[t] > 5 and score < 20:
            normalized = max(10, normalized - 10)
            
        final_scores[t] = normalized
        
    return final_scores
    
async def verify_teacher(x_user_role: str = Header(None, alias="X-User-Role")):
    """
    Simple header check. In a real app, use JWT tokens.
    For this prototype, the Frontend must send 'X-User-Role: teacher' 
    and the Backend trusts it (weak security) OR we validate session.
    """
    # Since we are stateless, this is a basic check.
    if x_user_role != "teacher":
        raise HTTPException(status_code=403, detail="Not authorized")

@app.on_event("startup")
async def startup_event():
    # 1. Declare ALL globals we need to set
    global cot_rag_agent, llm_fast, llm_smart, vector_store, graph_db 
    
    try:
        logger.info("Initializing C Tutor components...")
        
        from app.core import config 
        
        # 2. Initialize LLMs
        llm_fast = LLMInterface(
            google_model_id=config.DEFAULT_GOOGLE_MODEL_ID, 
            logger=logger
        )
        
        llm_smart = LLMInterface(
            google_model_id=config.DEFAULT_REASONING_MODEL_ID, 
            logger=logger
        )
        
        # 3. Initialize DBs
        vector_store = ChromaVectorStore(
            persist_directory=config.DEFAULT_VECTOR_DB_PATH, 
            logger=logger
        )
        graph_db = Neo4jGraphDB(logger=logger)

        # Register the shared graph handle for prerequisite-coupled priors (head start)
        try:
            from app.core import prereq_headstart
            prereq_headstart.set_graph_db(graph_db)
            logger.info("✅ Head-start (prerequisite-coupled priors) wired to graph DB.")
        except Exception as e:
            logger.warning(f"Head-start graph wiring failed: {e}")

        # 4. Initialize Orchestrator
        cot_rag_agent = ChainOfThoughtRAGAgent(
            llm_fast=llm_fast,
            llm_smart=llm_smart,
            vector_store=vector_store,
            graph_db=graph_db,
            logger=logger
        )
        logger.info("✅ C Tutor Agent ready!")
    except Exception as e:
        logger.error(f"❌ Initialization failed: {e}")

@app.on_event("shutdown")
async def shutdown_event():
    if graph_db:
        graph_db.close()

class ChatRequest(BaseModel):
    message: str
    user_role: Optional[str] = "student"
    username: Optional[str] = "anonymous"  # <--- CRITICAL
    session_id: Optional[str] = None 
    
    # Old legacy fields (keep them to prevent validation errors if UI sends them)
    socratic: Optional[bool] = False
    topic_class: Optional[str] = "Auto"
    enable_cot: Optional[bool] = True
    enable_validation: Optional[bool] = True
    enable_correction: Optional[bool] = True

LEARNING_PATH_CACHE = {}

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    # Pass arguments explicitly by name
    return templates.TemplateResponse(
        request=request, 
        name="index.html", 
        context={"request": request}
    )

# 4. Dynamic Page Endpoint
@app.get("/{page_name}.html", response_class=HTMLResponse)
async def serve_specific_html(request: Request, page_name: str):
    """ Dynamically serves teacher_dashboard.html, student_dashboard.html, etc. """
    file_name = f"{page_name}.html"
    file_path = TEMPLATES_DIR / file_name

    # Check if file exists using the Path object
    if file_path.exists():
        # Pass arguments explicitly by name here too
        return templates.TemplateResponse(
            request=request, 
            name=file_name, 
            context={"request": request}
        )
    return HTMLResponse(content="Page Not Found", status_code=404)

@app.get("/health")
async def health():
    return {"status": "healthy", "agent_ready": cot_rag_agent is not None}

@app.post("/api/v1/chat/stream")
async def chat_stream(request: ChatRequest):
    # --- 1. START PROFILER ---
    profiler = None
    if config.ENABLE_PROFILING:
        profiler = Profiler(interval=0.001, async_mode="enabled")
        profiler.start()

    # --- 2. SESSION MANAGEMENT ---
    session_id = request.session_id
    if not session_id:
        # Don't create a fresh session just for the greeting trigger
        if request.message.strip() == "[INIT_SESSION]":
            session_id = history_manager.create_session(request.username)
        else:
            session_id = history_manager.create_session(request.username)

    # --- 3. FETCH CONTEXT ---
    session_data = history_manager.get_session_details(request.username, session_id)
    last_context = ""
    if session_data and session_data.get("messages"):
        for msg in reversed(session_data["messages"]):
            if msg["role"] == "bot":
                last_context = msg["content"][:200]
                break

    # --- 4. SAVE USER MESSAGE & GET ID ---
    # Skip saving the special greeting trigger — it's an internal signal, not a real question
    msg_text = request.message.strip()
    
    # Do not save system triggers or skipped warmups to the database
    skip_save = msg_text in ["[INIT_SESSION]", "[NEW_CHAT]"] or msg_text.startswith("[WARMUP_ANSWER] skip")
    
    if skip_save:
        user_msg_id = None
    else:
        user_msg_id = history_manager.add_message(request.username, session_id, "user", request.message)

    async def generate_stream():
        full_bot_response = ""
        final_sources = []
        style_used_for_session = None
        _turn_start = time.time()

        try:
            if not cot_rag_agent:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Agent not initialized'})}\n\n"
                return

            user_goal = knowledge_manager.get_goal(request.username)

            # --- 5. RUN AGENT ---
            async for event in cot_rag_agent.run_stream(
                request.message, 
                request.user_role, 
                username=request.username,
                user_goal=user_goal,
                conversation_context=last_context,
                session_id=session_id
            ):
                # A. Capture Tokens (Streaming)
                if event["type"] in ["token", "answer"]:
                    text_chunk = event.get("text", "")
                    full_bot_response += text_chunk
                
                # B. Handle Completion (Metadata)
                if event["type"] == "complete":
                    final_data = event["data"]
                    style_used_for_session = final_data.get("style_used")

                    # Ensure we capture the final authoritative answer
                    if final_data.get('answer'):
                        final_data['answer'] = cot_rag_agent._sanitize_mermaid(final_data['answer'])
                        full_bot_response = final_data.get('answer')
                        
                    final_sources = final_data.get('sources', [])
                    
                    # --- NEW: INJECT GLOBAL SKIPPED CHALLENGES INTO PAYLOAD ---
                    user_row = db.fetch_one("SELECT learning_profile FROM users WHERE username = ?", (request.username,))
                    user_profile = json.loads(user_row['learning_profile']) if user_row and user_row['learning_profile'] else {}
                    event["data"]["skipped_challenges"] = user_profile.get("skipped_challenges", [])
                    # ---------------------------------------------------
                    
                    detected_topic = "General"
                    
                    # 1. Get raw entities from the Agent (sent in Step 1)
                    raw_entities = final_data.get('entities', [])
                    
                    # 2. Flatten to a single string for easy searching
                    # e.g. "for loop iteration"
                    search_str = " ".join(raw_entities).lower() + " " + final_data.get('detected_entity', "").lower()
                    
                    # 3. Map to Dashboard Topics (Priority Order)
                    if any(x in search_str for x in ["pointer", "memory", "address", "malloc", "free", "*", "&"]):
                        detected_topic = "Pointers"
                    elif any(x in search_str for x in ["struct", "union", "member", "dot operator"]):
                        detected_topic = "Structures"
                    elif any(x in search_str for x in ["string", "char array", "text", "strcat", "strcpy"]):
                        detected_topic = "Strings" # Strings often contain 'array', so check this before Arrays
                    elif any(x in search_str for x in ["array", "list", "collection", "index", "["]):
                        detected_topic = "Arrays"
                    elif any(x in search_str for x in ["function", "void", "return", "param", "arg", "call"]):
                        detected_topic = "Functions"
                    elif any(x in search_str for x in ["loop", "while", "for", "if", "else", "switch", "break", "continue", "control"]):
                        detected_topic = "Control Flow"
                    elif any(x in search_str for x in ["int", "float", "double", "char", "variable", "const", "type"]):
                        detected_topic = "Variables"
                    
                    # 4. Fallback: If still General, try to trust the Source Document Metadata
                    if detected_topic == "General" and final_sources:
                        # Extract all topics found in sources
                        topics = [s.get('topic') for s in final_sources if s.get('topic')]
                        if topics:
                            from collections import Counter
                            # Pick most common topic from the retrieved chunks
                            detected_topic = Counter(topics).most_common(1)[0][0]
                    try:
                        if user_msg_id is not None:
                            # 1. Save the interaction (Concept, Problem, Review, etc.)
                            history_manager.log_interaction(
                                user_msg_id, 
                                final_data['intent'], 
                                detected_topic 
                            )
                            if detected_topic != "General":
                                # BKT is the sole mastery authority: mark concept known only when P(L) >= 0.95
                                from app.core.bkt_model import bkt as _bkt
                                if _bkt.is_mastered(request.username, detected_topic):
                                    knowledge_manager.mark_concept_as_known(request.username, detected_topic)
                                    logger.info(f"🏆 [BKT] Mastery confirmed: '{detected_topic}'")
                    except Exception as analytics_err:
                        logger.error(f"Analytics logging failed: {analytics_err}")

                    # --- Telemetry: full turn record + session + path deviation ---
                    try:
                        from app.core import telemetry
                        _state = final_data.get("_state") or {}
                        _was_blocked = "_state" not in final_data
                        telemetry.touch_session(request.username, session_id)
                        telemetry.log_turn(
                            username=request.username,
                            session_id=session_id,
                            turn_index=(db.fetch_one(
                                "SELECT turn_count c FROM session_log WHERE session_id=?",
                                (session_id,)) or {"c": 0})["c"],
                            query_text=request.message,
                            response_text=full_bot_response,
                            intent=final_data.get("intent", ""),
                            entities=raw_entities,
                            topic=detected_topic,
                            mastery_level=_state.get("mastery_level", ""),
                            s_goal=_state.get("s_goal"),
                            c_code=_state.get("c_code"),
                            m_state=_state.get("m_state"),
                            delta_f=_state.get("delta_f"),
                            n_strike=_state.get("n_strike"),
                            n_sources=len(final_sources),
                            latency_ms=int((time.time() - _turn_start) * 1000),
                            was_blocked=_was_blocked,
                            block_reason=(final_data.get("intent") if _was_blocked else None),
                        )
                        # Path deviation (Forethought / SRL) using cached learning path
                        if detected_topic and detected_topic != "General":
                            _goal = knowledge_manager.get_goal(request.username)
                            _cache_key = f"{request.username}_{_goal}"
                            _cached = LEARNING_PATH_CACHE.get(_cache_key) or {}
                            _path = _cached.get("path") if isinstance(_cached, dict) else None
                            _dev, _expected = telemetry.classify_path_deviation(_path or [], detected_topic)
                            telemetry.log_path_event(request.username, detected_topic,
                                                     _expected, _dev, _goal)
                        # Strip internal telemetry key before sending to client
                        final_data.pop("_state", None)
                    except Exception as tele_err:
                        logger.warning(f"Turn telemetry failed: {tele_err}")

                    # Classroom video suggestion for CONCEPT responses
                    if final_data.get('intent') == 'CONCEPT' and detected_topic != 'General':
                        classroom_video = _find_classroom_video(detected_topic)
                        if classroom_video:
                            suggestion_text = (
                                f"\n\n---\n"
                                f"> 📹 **Watch in Classroom**\n"
                                f"> *{classroom_video['title']}* covers this topic with visual examples."
                            )
                            full_bot_response += suggestion_text
                            final_data['answer'] = full_bot_response
                            final_data['classroom_video'] = classroom_video
                            yield f"data: {json.dumps({'type': 'token', 'text': suggestion_text})}\n\n"
                            logger.info(f"📹 [CLASSROOM] Suggested video: '{classroom_video['title']}' for topic '{detected_topic}'")

                    event["data"]["session_id"] = session_id

                yield f"data: {json.dumps(event)}\n\n"

        except Exception as e:
            logger.error(f"Streaming error: {e}", exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        
        finally:
            # --- 6. SAVE BOT MESSAGE (Guaranteed Execution) ---
            # We save whatever response we have, even if the stream crashed
            if full_bot_response.strip():
                try:
                    history_manager.add_message(request.username, session_id, "bot", full_bot_response, final_sources, style_used=style_used_for_session)
                    # Invalidate learning path cache so it immediately updates based on new history/mastery
                    LEARNING_PATH_CACHE.pop(request.username, None)
                except Exception as save_err:
                    logger.error(f"Failed to save bot message: {save_err}")

            # --- 7. STOP PROFILER ---
            if profiler:
                try:
                    profiler.stop()
                    timestamp = int(time.time())
                    filename = f"profile_{session_id}_{timestamp}.html"
                    filepath = os.path.join(PROFILE_DIR, filename)
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(profiler.output_html())
                    yield f"data: {json.dumps({'type': 'profiler_report', 'url': f'/profiles/{filename}'})}\n\n"
                except: pass

    return StreamingResponse(generate_stream(), media_type="text/event-stream")

@app.get("/api/v1/analytics/student/{username}")
async def get_student_stats(username: str):
    """FAST Endpoint: Returns charts data only."""
    history = history_manager.get_student_history(username)
    goal = knowledge_manager.get_goal(username)
    
    mastery = _calculate_mastery(history)
    stats = {"CONCEPT": 0, "PROBLEM": 0, "DEBUG": 0, "REVIEW": 0}
    for h in history:
        i = h.get('intent', 'UNKNOWN')
        if i in stats: stats[i] += 1
        
    return {
        "stats": stats,
        "mastery": mastery, 
        "total_queries": len(history),
        "goal": goal
    }

@app.get("/api/v1/analytics/report/{username}")
def get_student_report(username: str):
    """SLOW Endpoint: Returns LLM advice using the Smart Model."""
    history = history_manager.get_student_history(username)
    recent_logs = history[-15:]
    
    if not recent_logs:
        return {"focus": "Just getting started", "strengths": "N/A", "weakness": "N/A", "reading": ["Basics"]}
        
    log_text = "\n".join([
        f"- [{log.get('intent', 'N/A')}] "
        f"Topic: {log.get('topic', 'General')} "
        f"- Q: {log.get('query', 'Unknown')[:50]}..." 
        for log in recent_logs
    ])
    
    prompt = f"""
    You are an AI Tutor Analyst. Analyze this student's recent interaction history.
    
    Student Logs:
    {log_text}
    
    Task: Write a helpful "Progress Report" strictly in JSON format.
    
    1. **Focus:** What topics are they asking about most? (Max 10 words)
    2. **Strengths:** What are they doing well? (e.g., "Good curiosity about Pointers")
    3. **Weakness:** What are they struggling with? (e.g., "Syntax errors in Loops")
    4. **Reading:** Suggest 2 specific C topics or concepts they should study next based on their weaknesses (e.g., "Arrays", "Memory Management"). Return as a list of strings.
    
    Return ONLY JSON: {{ "focus": "...", "strengths": "...", "weakness": "...", "reading": ["Topic A", "Topic B"] }}
    """
    
    report_raw = "" # Initialize to avoid UnboundLocalError
    try:
        # FIX: Use llm_smart instead of llm_interface
        report_raw = llm_smart.generate_response(prompt)
        
        # Clean JSON string
        clean_text = report_raw.replace("```json", "").replace("```", "").strip()
        start = clean_text.find('{')
        end = clean_text.rfind('}') + 1
        return json.loads(clean_text[start:end])
        
    except Exception as e:
        logger.error(f"Analytics Report Failed: {e}")
        # report_raw might be empty, so we check before logging
        if report_raw:
            logger.error(f"Raw Output was: {report_raw}")
        return {"focus": "Analysis Error", "strengths": "N/A", "weakness": "Try again later", "reading": []}

# async def get_student_report(username: str):
#     """SLOW Endpoint: Returns LLM advice."""
#     history = history_manager.get_student_history(username)
#     recent_logs = history[-15:]
    
#     if not recent_logs:
#         return {"focus": "Just getting started", "strengths": "N/A", "weakness": "N/A", "reading": ["Basics"]}
        
#     log_text = "\n".join([f"- [{log['intent']}] Topic: {log.get('topic', 'General')} - Q: {log['query']}" for log in recent_logs])
    
#     # --- RESTORED PROMPT ---
#     prompt = f"""
#     You are an AI Tutor Analyst. Analyze this student's recent interaction history.
    
#     Student Logs:
#     {log_text}
    
#     Task: Write a helpful "Progress Report" strictly in JSON format.
    
#     1. **Focus:** What topics are they asking about most? (Max 10 words)
#     2. **Strengths:** What are they doing well? (e.g., "Good curiosity about Pointers")
#     3. **Weakness:** What are they struggling with? (e.g., "Syntax errors in Loops")
#     4. **Reading:** Suggest 2 specific C topics or concepts they should study next based on their weaknesses (e.g., "Arrays", "Memory Management"). Return as a list of strings.
    
#     Return ONLY JSON: {{ "focus": "...", "strengths": "...", "weakness": "...", "reading": ["Topic A", "Topic B"] }}
#     """
    
#     try:
#         report_raw = llm_interface.generate_response(prompt)
        
#         # --- ROBUST JSON CLEANER ---
#         # 1. Strip markdown
#         clean_text = report_raw.replace("```json", "").replace("```", "").strip()
        
#         # 2. Extract JSON substring if LLM chatted (Find first '{' and last '}')
#         start = clean_text.find('{')
#         end = clean_text.rfind('}') + 1
#         if start != -1 and end != -1:
#             clean_text = clean_text[start:end]
            
#         return json.loads(clean_text)
#         # ---------------------------
        
#     except Exception as e:
#         # Log the specific error to the terminal so you can see it
#         logger.error(f"Analytics Report Failed: {e}")
#         logger.error(f"Raw Output was: {report_raw}")
#         return {"focus": "Analysis Error", "strengths": "N/A", "weakness": "Try again later", "reading": []}

@app.get("/api/v1/config/topics")
async def get_topics():
    """Get list of topics and their visibility status"""
    return settings_manager.get_settings()

@app.post("/api/v1/config/topics")
async def update_topic(update: TopicUpdate):
    """Enable or Disable a topic"""
    settings_manager.update_topic(update.topic, update.enabled)
    return {"status": "success", "topic": update.topic, "enabled": update.enabled}

@app.post("/api/v1/auth/login")
async def login(creds: LoginRequest):
    try:
        user = user_manager.authenticate(creds.username, creds.password)
        if user:
            return {"status": "success", "user": user}
        else:
            raise HTTPException(status_code=401, detail="Invalid credentials")
    except Exception as e:
        # Catch the "Account Blocked" exception from user_manager
        raise HTTPException(status_code=403, detail=str(e))

@app.post("/api/v1/auth/signup")
async def signup(req: SignupRequest):
    success = user_manager.create_user(
        username=req.username,
        password=req.password,
        name=req.name,
        email=req.email,
        university=req.university,
        department=req.department,
        interest=req.interest
    )
    if success:
        return {"status": "success", "message": "Account created! Please login."}
    else:
        raise HTTPException(status_code=400, detail="Username already exists")

@app.get("/api/v1/admin/users", dependencies=[Depends(verify_teacher)])
async def get_users_paginated(page: int = 1, page_size: int = 10):
    # 1. Get Data from Manager
    users = user_manager.get_all_users(page, page_size)
    total_records = user_manager.get_total_user_count()
    
    # 2. Calculate Metadata
    total_pages = (total_records + page_size - 1) // page_size
    
    return {
        "items": users,
        "total": total_records,
        "page": page,
        "pages": total_pages
    }

@app.post("/api/v1/admin/users/update")
async def update_user(req: AdminUserUpdate):
    # In a real app, verify the caller is an admin here
    success = user_manager.update_user_status(
        username=req.target_username,
        role=req.new_role,
        blocked=req.blocked
    )
    if success:
        return {"status": "success"}
    raise HTTPException(status_code=404, detail="User not found")

@app.get("/api/v1/analytics/learning_path/{username}")
def get_learning_path_endpoint(username: str):
    import string
    import json
    
    # 1. Fetch Goal and Mastery
    goal = knowledge_manager.get_goal(username)
    if not goal or goal == "No Goal Yet":
        return {"goal": None, "path": []}
        
    history = history_manager.get_student_history(username)
    mastery = _calculate_mastery(history)
    known_concepts = [topic for topic, score in mastery.items() if score >= 70]
        
    # Normalize the goal to prevent cache misses on punctuation
    clean_goal = goal.translate(str.maketrans('', '', string.punctuation)).strip().lower()
    
    # 2. Dynamic Cache Key
    cache_key = f"{username}_{clean_goal}_{','.join(sorted(known_concepts))}"
    cached = LEARNING_PATH_CACHE.get(cache_key)
    
    if cached:
        return {"goal": goal, "path": cached}
        
    print(f"🐢 Generating Strict AI Curriculum for {username}...")
    
    # =========================================================
    # --- THE FIX: ANTI-HALLUCINATION CURRICULUM PROMPT ---
    # =========================================================
    prompt = f"""
    You are an expert C Programming University Professor designing a linear curriculum for a beginner.
    
    Student's Goal: "{clean_goal}"
    
    Standard Topics Available:
    1. Fundamentals (Main function, basic I/O)
    2. Variables
    3. Control Flow (If/Else, Loops)
    4. Functions
    5. Arrays
    6. Strings
    7. Pointers
    8. Structures
    9. Memory Allocation
    10. File I/O

    TASK:
    Create a logical, step-by-step learning path that takes the student from absolute beginner up to building their goal.
    
    CRITICAL RULES:
    1. START SIMPLE: Always start with Fundamentals and Variables.
    2. MAXIMUM 6 STEPS: Keep the path focused. Do not over-engineer.
    3. THE FINAL STEP must be a clean, title-cased name for their project.
    
    EXAMPLES OF PERFECT CURRICULUMS:
    
    Goal: "build a calculator"
    [
      {{"concept": "Fundamentals"}},
      {{"concept": "Variables"}},
      {{"concept": "Control Flow"}},
      {{"concept": "Functions"}},
      {{"concept": "Build A Calculator"}}
    ]
    
    Goal: "learn pointers"
    [
      {{"concept": "Fundamentals"}},
      {{"concept": "Variables"}},
      {{"concept": "Control Flow"}},
      {{"concept": "Functions"}},
      {{"concept": "Pointers"}}
    ]
    
    Goal: "tic-tac-toe game"
    [
      {{"concept": "Fundamentals"}},
      {{"concept": "Variables"}},
      {{"concept": "Control Flow"}},
      {{"concept": "Arrays"}},
      {{"concept": "Tic-Tac-Toe Game"}}
    ]

    Now, generate the JSON array for the Student's Goal: "{clean_goal}"
    Return ONLY the valid JSON list.
    """
    
    try:
        response = llm_smart.generate_response(prompt)
        clean_text = response.replace("```json", "").replace("```", "").strip()
        start = clean_text.find('[')
        end = clean_text.rfind(']') + 1
        path_data = json.loads(clean_text[start:end])
        
        # 4. Enforce Statuses (Mastered / Next / Locked)
        found_next = False
        for step in path_data:
            # Check if this step is mastered
            is_known = any(k.lower() in step['concept'].lower() for k in known_concepts)
            
            # Smart Fallback: If they already know Variables, they implicitly know Fundamentals!
            if step['concept'].lower() == 'fundamentals' and any('variable' in k.lower() for k in known_concepts):
                is_known = True

            # If they know it, give them the Checkmark!
            if is_known:
                step['status'] = 'mastered'
            # If they DON'T know it, and we haven't found the "Next" step yet, assign it the Star!
            elif not found_next:
                step['status'] = 'next'
                found_next = True
            # Otherwise, lock it.
            else:
                step['status'] = 'locked'
                
        # Force the final goal node to never be "mastered" implicitly
        if path_data and path_data[-1]['status'] == 'mastered':
             path_data[-1]['status'] = 'next'

             
        # 5. Update Cache
        keys_to_delete = [k for k in LEARNING_PATH_CACHE.keys() if k.startswith(f"{username}_")]
        for k in keys_to_delete:
            del LEARNING_PATH_CACHE[k]
            
        LEARNING_PATH_CACHE[cache_key] = path_data
        
        return {"goal": goal, "path": path_data}
        
    except Exception as e:
        print(f"Error generating learning path via LLM: {e}")
        return {"goal": goal, "path": []}

@app.get("/api/v1/analytics/student/{username}")
def get_student_analytics(username: str):
    # Start timer
    import time
    t0 = time.time()
    
    history = history_manager.get_student_history(username)
    goal = knowledge_manager.get_goal(username)
    
    # 1. Mastery Calculation
    mastery = _calculate_mastery(history)

    # 2. Basic Stats
    stats = {"CONCEPT": 0, "PROBLEM": 0, "DEBUG": 0, "REVIEW": 0}
    for h in history:
        i = h.get('intent', 'UNKNOWN')
        if i in stats: stats[i] += 1
    
    # 3. Generate LLM Report (Optimized)
    recent_logs = history[-15:]
    
    # --- CRITICAL CHECK ---
    if not recent_logs:
        print(f"⚡ FAST PATH: No history for {username}. Skipping LLM.")
        report_data = {
            "focus": "Just getting started", 
            "strengths": "N/A", 
            "weakness": "N/A", 
            "reading": ["Introduction to C"]
        }
    else:
        print(f"🐢 SLOW PATH: Generative report for {username}...")
        log_text = "\n".join([f"- [{log['intent']}] Topic: {log.get('topic', 'General')} - Q: {log['query']}" for log in recent_logs])
        
        prompt = f"""
        Analyze this student's recent interaction history with a C Tutor AI.
        Student Logs:
        {log_text}
        
        Task: Write a helpful "Progress Report" in JSON format.
        1. **Focus:** What topics are they asking about most? (Max 10 words)
        2. **Strengths:** What are they doing well?
        3. **Weakness:** What are they struggling with?
        4. **Reading:** Suggest 2 specific C topics or concepts they should study next.
        
        Return ONLY JSON: {{ "focus": "...", "strengths": "...", "weakness": "...", "reading": ["Topic A", "Topic B"] }}
        """
        
        try:
            report_raw = llm_interface.generate_response(prompt)
            clean_json = report_raw.replace("```json", "").replace("```", "").strip()
            report_data = json.loads(clean_json)
        except Exception as e:
            logger.error(f"Analytics LLM error: {e}")
            report_data = {
                "focus": "Analysis unavailable", 
                "strengths": "N/A", "weakness": "N/A", 
                "reading": ["Basics"]
            }

    print(f"⏱️ Analytics took: {time.time() - t0:.2f}s")
    
    return {
        "stats": stats,
        "mastery": mastery, 
        "total_queries": len(history),
        "report": report_data,
        "goal": goal
    }

# --- RESOURCE MANAGEMENT ENDPOINTS ---
@app.post("/api/v1/resources/upload")
async def upload_resource(
    files: List[UploadFile] = File(...), 
    resource_type: str = Form(...) # Changed to Form to parse correctly
):
    """
    Securely uploads files. Enforces extension checks based on resource_type.
    """
    saved_files = []
    rejected_files = []

    # 1. Validate Resource Type
    if resource_type not in config.ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Invalid resource category.")

    # 2. Determine Target Directory
    if resource_type == "code":
        target_dir = config.PROJECT_ROOT / "resources" / "code"
    elif resource_type == "concept":
        target_dir = config.PROJECT_ROOT / "resources" / "concepts"
    else:
        target_dir = config.PROJECT_ROOT / "resources" / "metadata"

    # Ensure directory exists
    os.makedirs(target_dir, exist_ok=True)

    # 3. Process Files
    allowed_exts = config.ALLOWED_EXTENSIONS[resource_type]

    for file in files:
        filename = file.filename
        # Get extension (lowercase)
        ext = Path(filename).suffix.lower()

        # SECURITY CHECK: Extension
        if ext not in allowed_exts:
            rejected_files.append(filename)
            continue
        
        # SECURITY CHECK: Path Traversal (Basic)
        safe_filename = os.path.basename(filename)
        file_path = target_dir / safe_filename

        try:
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            saved_files.append(safe_filename)
        except Exception as e:
            logger.error(f"Failed to save {filename}: {e}")
            rejected_files.append(filename)

    # 4. Trigger Ingestion (Only if files were saved)
    if saved_files:
        try:
            script_name = "ingest_manual_graph.py" if resource_type == "metadata" else "ingest_data.py"
            script_path = config.PROJECT_ROOT / "scripts" / script_name
            subprocess.Popen(["python", str(script_path)])
        except Exception as e:
            logger.error(f"Ingestion trigger failed: {e}")

    # 5. Return Response
    if not saved_files and rejected_files:
        raise HTTPException(status_code=400, detail=f"Rejected invalid files: {', '.join(rejected_files)}")
    
    return {
        "status": "success", 
        "message": f"Saved {len(saved_files)} files.",
        "rejected": rejected_files
    }

@app.get("/api/v1/analytics/teacher/overview")
async def get_teacher_analytics():
    """
    Returns global class stats using SQL aggregation.
    """
    # 1. Fetch all analytics data (Intent and Topic)
    # We only care about messages where intent/topic were actually logged
    rows = db.fetch_all("SELECT topic, intent FROM messages WHERE topic IS NOT NULL")
    
    # 2. Process in Python (keeping your existing logic logic)
    topic_counts = {}
    struggle_counts = {}
    
    for r in rows:
        topic = r['topic'] or 'General'
        intent = r['intent'] or 'UNKNOWN'
        
        # Count Topics
        topic_counts[topic] = topic_counts.get(topic, 0) + 1
        
        # Count Struggles (DEBUG or REVIEW)
        if intent in ['REVIEW', 'DEBUG']:
            struggle_counts[topic] = struggle_counts.get(topic, 0) + 1

    return {
        "popular_topics": topic_counts,
        "struggle_areas": struggle_counts,
        "total_interactions": len(rows)
    }
    
@app.get("/api/v1/analytics/teacher/detailed")
async def get_teacher_detailed_analytics(page: int = 1, page_size: int = 10):
    """
    Returns calculated matrix with Pagination.
    Optimization: Only runs heavy calculations for the requested page of students.
    """
    # 1. Fetch ALL student usernames to establish order and total count
    # We use a raw query here because we need the full list to sort alphabetically before slicing
    rows = db.fetch_all("SELECT username FROM users WHERE role = 'student'")
    students = [r['username'] for r in rows]
    students.sort() # Ensure consistent order across pages
    
    # 2. Pagination Logic
    total_records = len(students)
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    
    # Slice the list: only process students for THIS page
    page_students = students[start_idx:end_idx]

    class_matrix = []
    current_time = datetime.now()

    # 3. Heavy Calculation (Only for the sliced students)
    for idx, student in enumerate(page_students):
        # Calculate display ID based on actual index in the full list
        real_idx = start_idx + idx + 1
        anon_id = f"Student_{real_idx:02d}"
        
        history = history_manager.get_student_history(student)
        mastery = _calculate_mastery(history)
        
        # --- A. Last Active Calculation ---
        last_active_str = "Never"
        days_inactive = 999
        if history:
            last_ts = history[-1]['timestamp']
            try:
                # Handle both ISO strings and datetime objects
                if isinstance(last_ts, str):
                    last_date = datetime.fromisoformat(last_ts)
                else:
                    last_date = last_ts
                
                last_active_str = last_date.strftime("%Y-%m-%d")
                days_inactive = (current_time - last_date).days
            except: 
                pass

        # --- B. Risk Assessment ---
        total_interactions = len(history)
        debug_count = sum(1 for h in history if h.get('intent') == 'DEBUG')
        avg_mastery = sum(mastery.values()) / len(mastery) if mastery else 0
        
        risk = "Low"
        risk_reason = "Doing well"

        if total_interactions == 0:
            risk = "Inactive"
            risk_reason = "No data yet"
        elif days_inactive > 7:
            risk = "High"
            risk_reason = f"Absent for {days_inactive} days"
        elif total_interactions > 5 and (debug_count / total_interactions) > 0.6:
            risk = "High"
            risk_reason = "High error rate"
        elif total_interactions > 0 and avg_mastery < 30:
            risk = "Medium"
            risk_reason = "Low topic mastery"
            
        # --- C. Strongest / Weakest Logic ---
        # Filter out "General" and None values
        valid_topics = {k: v for k, v in mastery.items() if k and k != "General"} 
        sorted_topics = sorted(valid_topics.items(), key=lambda x: x[1], reverse=True)
        
        strongest = "-"
        weakest = "-"

        if sorted_topics:
            top_topic, top_score = sorted_topics[0]
            if top_score > 10: strongest = top_topic
            else: strongest = "Just Started"
            
            if len(sorted_topics) > 1: weakest = sorted_topics[-1][0]
            elif top_score < 40: weakest = top_topic

        class_matrix.append({
            "hidden_username": student, 
            "display_id": anon_id,      
            "risk_level": risk,
            "risk_reason": risk_reason,
            "strongest_topic": strongest,
            "weakest_topic": weakest,
            "last_active": last_active_str,
            "mastery": mastery
        })
        
    return {
        "items": class_matrix,
        "total": total_records,
        "page": page,
        "pages": (total_records + page_size - 1) // page_size if page_size > 0 else 1
    }

@app.get("/api/v1/analytics/teacher/risk_matrix")
async def get_risk_matrix(days: int = 14):
    """
    Risk Matrix (Action 5): flags students by HIGH FRUSTRATION + LOW MASTERY using
    the affect_log (frustration trajectory) and BKT mastery telemetry.
    Returns students sorted most-at-risk first so the instructor can intervene.
    """
    from datetime import timedelta
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()

    students = [r["username"] for r in
                db.fetch_all("SELECT username FROM users WHERE role = 'student'")]

    matrix = []
    for idx, student in enumerate(sorted(students)):
        # --- Frustration signal (affect_log, recent window) ---
        aff = db.fetch_one(
            "SELECT AVG(delta_f) avg_df, MAX(delta_f) max_df, COUNT(*) n, "
            "SUM(CASE WHEN LOWER(frustration_level) LIKE '%rage%' THEN 1 ELSE 0 END) rage, "
            "SUM(CASE WHEN intervention_fired=1 THEN 1 ELSE 0 END) interventions "
            "FROM affect_log WHERE username=? AND ts_utc >= ?",
            (student, since),
        )
        avg_df = (aff["avg_df"] or 0.0) if aff else 0.0
        max_df = (aff["max_df"] or 0.0) if aff else 0.0
        n_turns = (aff["n"] or 0) if aff else 0
        rage_events = (aff["rage"] or 0) if aff else 0
        interventions = (aff["interventions"] or 0) if aff else 0

        # --- Mastery signal (user_knowledge composite) ---
        mk = db.fetch_one(
            "SELECT AVG(p_mastery) avg_m, COUNT(*) n_concepts, "
            "SUM(CASE WHEN ever_certified=1 THEN 1 ELSE 0 END) n_cert "
            "FROM user_knowledge WHERE username=?",
            (student,),
        )
        avg_mastery = (mk["avg_m"] or 0.0) if mk else 0.0
        n_concepts = (mk["n_concepts"] or 0) if mk else 0
        n_cert = (mk["n_cert"] or 0) if mk else 0
        # Normalize composite (ceiling ≈ 0.95) to 0–1
        mastery_norm = min(avg_mastery / 0.95, 1.0) if avg_mastery else 0.0

        # --- Risk scoring (0–100, higher = more at risk) ---
        frustration_component = min(50.0, rage_events * 15 + max(0.0, avg_df) * 120 + max(0.0, max_df) * 30)
        low_mastery_component = (1.0 - mastery_norm) * 50.0 if n_concepts > 0 else 0.0
        risk_score = round(frustration_component + low_mastery_component, 1)

        high_frustration = rage_events > 0 or avg_df > 0.1
        low_mastery = n_concepts > 0 and mastery_norm < 0.35

        # --- Tier + human-readable reason ---
        if n_turns == 0 and n_concepts == 0:
            tier, reason = "No Data", "No activity yet"
        elif high_frustration and low_mastery:
            tier = "Critical"
            reason = f"High frustration ({rage_events} rage) + low mastery ({mastery_norm*100:.0f}%)"
        elif risk_score >= 50:
            tier = "High"
            reason = ("Frustration elevated" if high_frustration
                      else f"Low mastery ({mastery_norm*100:.0f}%)")
        elif risk_score >= 30:
            tier = "Watch"
            reason = "Mild frustration or slow progress"
        else:
            tier = "OK"
            reason = "Engaged, progressing"

        matrix.append({
            "display_id": f"Student_{idx+1:02d}",
            "hidden_username": student,
            "risk_tier": tier,
            "risk_score": risk_score,
            "risk_reason": reason,
            "avg_frustration": round(avg_df, 3),
            "rage_events": rage_events,
            "interventions": interventions,
            "avg_mastery_pct": round(mastery_norm * 100, 1),
            "concepts_touched": n_concepts,
            "concepts_certified": n_cert,
            "turns": n_turns,
        })

    # Most-at-risk first
    matrix.sort(key=lambda x: x["risk_score"], reverse=True)
    summary = {
        "critical": sum(1 for m in matrix if m["risk_tier"] == "Critical"),
        "high": sum(1 for m in matrix if m["risk_tier"] == "High"),
        "watch": sum(1 for m in matrix if m["risk_tier"] == "Watch"),
        "window_days": days,
    }
    return {"items": matrix, "summary": summary}


@app.get("/api/v1/analytics/student_detail/{username}")
async def get_student_detail_view(username: str):
    """
    Fetches deep-dive data: Full list of sessions + Full text of last 2.
    """
    # 1. Get All Sessions (Lightweight: ID, Title, Date)
    sessions_list = history_manager.get_user_sessions_list(username)
    
    # Sort by date desc (Newest first)
    sessions_list.sort(key=lambda x: x['date'], reverse=True)
    
    # 2. Get Details for ONLY the last 2 (Heavy)
    recent_chats_details = []
    for sess in sessions_list[:2]:
        details = history_manager.get_session_details(username, sess['id'])
        if details:
            recent_chats_details.append({
                "id": sess['id'],
                "title": sess['title'],
                "date": sess['date'],
                "messages": details.get('messages', [])
            })
            
    return {
        "all_sessions_summary": sessions_list, # List of {id, title, date}
        "recent_chats": recent_chats_details   # Full text
    }

@app.get("/api/v1/history/sessions")
async def get_sessions(username: str):
    """Get list of past conversations for sidebar."""
    return history_manager.get_user_sessions_list(username)

# 1. GET Endpoint (For Loading Chat)
@app.get("/api/v1/history/session/{session_id}")
async def get_session_chat(session_id: str, username: str):
    """Get full chat log for a specific session."""
    session = history_manager.get_session_details(username, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session

# 2. DELETE Endpoint (For Deleting Chat)
@app.delete("/api/v1/history/session/{session_id}")
async def delete_session(session_id: str, username: str):
    """Soft deletes a session."""
    success = history_manager.delete_session(username, session_id)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "success"}


@app.post("/api/v1/user/goal")
async def set_user_goal(req: GoalRequest):
    # =========================================================
    # META-SECURITY: PREVENT GOAL POISONING
    # Ensure the student isn't setting a malicious goal to bypass S_goal
    # =========================================================
    malicious_keywords = [
        "hack", "exploit", "virus", "keylogger", "bypass", "ddos", 
        "exam answer", "cheat", "malware", "steal", "destroy"
    ]
    if any(w in req.goal.lower() for w in malicious_keywords):
        raise HTTPException(
            status_code=400, 
            detail="Goal rejected. Please choose a constructive educational goal (e.g., 'Build a calculator' or 'Learn Memory Management')."
        )
        
    from app.core.user_knowledge_manager import knowledge_manager
    # Capture the prior goal so we can tell "first set" from "revision" (Forethought signal)
    prior_goal = knowledge_manager.get_goal(req.username)
    knowledge_manager.set_goal(req.username, req.goal)

    # --- Telemetry: goal-set / goal-revision event (SRL Forethought) ---
    try:
        from app.core import telemetry
        telemetry.log_event(req.username, "goal_set", {
            "goal": req.goal,
            "prior_goal": prior_goal,
            "is_revision": bool(prior_goal and prior_goal != req.goal),
        })
    except Exception as e:
        logger.warning(f"Goal telemetry failed: {e}")

    # Invalidate cache when the goal updates
    LEARNING_PATH_CACHE.pop(req.username, None)
    return {"status": "success", "goal": req.goal}

class ResetKnowledgeRequest(BaseModel):
    username: str

@app.post("/api/v1/user/reset-knowledge")
async def reset_user_knowledge(req: ResetKnowledgeRequest):
    """Clear all mastery data for a user (used by test agent for clean runs)."""
    from app.core.user_knowledge_manager import knowledge_manager
    knowledge_manager.clear_concepts(req.username)
    return {"status": "success", "message": f"Cleared all mastery for {req.username}"}


# ── SRL-BKT Calibration Loop Endpoints ──────────────────────────────────────

class SelfAssessmentRequest(BaseModel):
    username: str
    concept: str
    tier: str
    self_assessment: float

@app.get("/api/v1/mastery/{username}/{concept}")
async def get_mastery_detail(username: str, concept: str):
    """Return per-tier mastery breakdown with BKT, self-assessment, and effective scores."""
    from app.core.bkt_model import (bkt, EVIDENCE_CONFIG, _read_row, _apply_decay, _parse_ts,
                                    _TS_COL, _LAM_COL, _get_user_params, answers_to_certify,
                                    calibrator)
    from app.core.srl_calibration import get_self_assessment

    row = _read_row(username, concept)
    if not row:
        # Brand-new topic: answers-to-master computed from the cold-start priors.
        return {
            "concept": concept,
            "tiers": {t: {"p_bkt": round(EVIDENCE_CONFIG[t]["P_L0"], 4), "p_self": None,
                          "p_effective": 0.0, "n_evidence": 0, "adapted_P_G": None,
                          "answers_to_master": answers_to_certify(
                              EVIDENCE_CONFIG[t]["P_L0"], 0, EVIDENCE_CONFIG[t],
                              theta=calibrator.get_threshold(concept, t))}
                      for t in ("quiz", "micro", "code")},
            "composite_effective": 0.0,
            "is_certified": False,
            "ever_certified": False,
        }

    effective = bkt.get_effective_mastery(username, concept)
    tiers = {}
    for tier in ("quiz", "micro", "code"):
        p_raw = row[EVIDENCE_CONFIG[tier]["col"]] or EVIDENCE_CONFIG[tier]["P_L0"]
        p_bkt = _apply_decay(p_raw, tier, _parse_ts(row[_TS_COL[tier]]), lam=row[_LAM_COL[tier]])
        cal = get_self_assessment(username, concept, tier)
        n_ev = row[f"n_evidence_{tier}"] or 0
        # Best-case consecutive-correct answers still needed to certify this tier.
        # Uses raw p_bkt (certification reads raw) and the user's own BKT params.
        answers = answers_to_certify(
            p_bkt, n_ev, _get_user_params(username, concept, tier),
            theta=calibrator.get_threshold(concept, tier),
        )
        tiers[tier] = {
            "p_bkt": round(p_bkt, 4),
            "p_self": cal["self_assessment"] if cal else None,
            "p_effective": effective[tier],
            "n_evidence": n_ev,
            "adapted_P_G": cal["adapted_P_G"] if cal else None,
            "answers_to_master": answers,
        }

    return {
        "concept": concept,
        "tiers": tiers,
        "composite_effective": effective["composite"],
        "is_certified": bool(row["is_certified"]),
        "ever_certified": bool(row["ever_certified"]),
    }


@app.post("/api/v1/mastery/self-assess")
async def submit_self_assessment(req: SelfAssessmentRequest):
    """Student adjusts mastery for a specific tier (downward-only)."""
    from app.core.bkt_model import EVIDENCE_CONFIG, _read_row, _apply_decay, _parse_ts, _TS_COL, _LAM_COL
    from app.core.srl_calibration import record_self_assessment

    if req.tier not in ("quiz", "micro", "code"):
        raise HTTPException(status_code=400, detail=f"Invalid tier: {req.tier}")

    row = _read_row(req.username, req.concept)
    if not row:
        raise HTTPException(status_code=404, detail=f"No mastery data for {req.username}/{req.concept}")

    p_raw = row[EVIDENCE_CONFIG[req.tier]["col"]] or EVIDENCE_CONFIG[req.tier]["P_L0"]
    p_bkt = _apply_decay(
        p_raw, req.tier, _parse_ts(row[_TS_COL[req.tier]]), lam=row[_LAM_COL[req.tier]]
    )

    try:
        result = record_self_assessment(
            username=req.username,
            concept=req.concept,
            tier=req.tier,
            p_self=req.self_assessment,
            p_bkt_current=p_bkt,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return result


# ── Prerequisite-coupled priors ("head start") ───────────────────────────────

@app.get("/api/v1/head-start/{username}/{concept}")
async def get_head_start(username: str, concept: str):
    """Explain the head start for one topic: prerequisites, which are certified,
    the resulting seeded priors, and the certification wall a head start can't cross."""
    from app.core import prereq_headstart
    return prereq_headstart.explain(username, concept)


# Canonical C-curriculum prerequisite edges among the dashboard skill topics.
# Used as a reliable fallback when the graph DB is empty or names don't line up.
SKILL_TOPICS = ["Variables", "Control Flow", "Functions", "Arrays", "Strings",
                "Pointers", "Structures", "Memory Allocation", "File I/O"]
CANONICAL_PREREQ_EDGES = [
    ("Variables", "Control Flow"),
    ("Variables", "Pointers"),
    ("Control Flow", "Functions"),
    ("Control Flow", "Arrays"),
    ("Arrays", "Strings"),
    ("Arrays", "Structures"),
    ("Pointers", "Memory Allocation"),
    ("Strings", "File I/O"),
]


@app.get("/api/v1/skill-network/{username}")
async def get_skill_network(username: str):
    """Prerequisite network for the skill-progress dashboard: one node per topic
    (with mastery state, certification, and head-start flags) plus prerequisite edges.
    This is the visual face of the same graph the head-start mechanism runs on."""
    from app.core import bkt_model, prereq_headstart

    nodes = []
    for c in SKILL_TOPICS:
        row = bkt_model._read_row(username, c)
        if row:
            n_ev = (row["n_evidence_quiz"] or 0) + (row["n_evidence_micro"] or 0) + (row["n_evidence_code"] or 0)
            hs = (row["hs_quiz"] or 0) + (row["hs_micro"] or 0) + (row["hs_code"] or 0)
            nodes.append({
                "concept": c,
                "ever_certified": bool(row["ever_certified"]),
                "is_certified": bool(row["is_certified"]),
                "has_head_start": hs > 1e-9 and n_ev == 0,
                "n_evidence": n_ev,
            })
        else:
            nodes.append({
                "concept": c, "ever_certified": False, "is_certified": False,
                "has_head_start": False, "n_evidence": 0,
            })

    # Prefer the live graph; fall back to the canonical curriculum map.
    edges = []
    try:
        by_lower = {t.lower(): t for t in SKILL_TOPICS}
        for dep in SKILL_TOPICS:
            for pre in prereq_headstart._prerequisites_of(dep):
                key = pre.lower()
                if key in by_lower and by_lower[key] != dep:
                    pair = [by_lower[key], dep]
                    if pair not in edges:
                        edges.append(pair)
    except Exception:
        edges = []
    if not edges:
        edges = [[a, b] for a, b in CANONICAL_PREREQ_EDGES]

    return {"nodes": nodes, "edges": edges}


# ── Unified Learner Model + Motivational Self-Report ─────────────────────────

@app.get("/api/v1/learner-model/{username}")
async def get_learner_model(username: str):
    """Full multidimensional learner profile (cognitive/metacognitive/affective/motivational)."""
    from app.core.learner_model import get_learner_profile
    return get_learner_profile(username)

class SelfReportItem(BaseModel):
    dimension: str      # self_efficacy | interest | goal_orientation
    item_id: str
    score: float

class SelfReportRequest(BaseModel):
    username: str
    items: List[SelfReportItem]

@app.post("/api/v1/self-report")
async def submit_self_report(req: SelfReportRequest):
    """Store a motivational self-report survey (Tier 2)."""
    from app.core import telemetry
    for it in req.items:
        telemetry.log_self_report(req.username, it.dimension, it.item_id, it.score)
    telemetry.log_event(req.username, "self_report", {"n_items": len(req.items)})
    return {"status": "ok", "recorded": len(req.items)}


# ── Behavioral Telemetry (Productive Struggle — Contribution 2) ──────────────

class BehaviorEventRequest(BaseModel):
    username: str
    session_id: Optional[str] = None
    event: str                       # copy_code | dwell | hint_request | skip_challenge
    value: Optional[str] = None      # dwell seconds, code length, etc.
    message_id: Optional[str] = None

@app.post("/api/v1/telemetry/behavior")
async def log_behavior_event(req: BehaviorEventRequest):
    """Frontend-driven behavioral telemetry: copy-code clicks, dwell time, etc."""
    from app.core import telemetry
    telemetry.log_behavior(req.username, req.session_id, req.event,
                           req.value, req.message_id)
    return {"status": "ok"}

@app.post("/api/v1/chat/feedback")
async def handle_feedback(req: FeedbackRequest):
    """
    Logs feedback to SQL and optionally returns a trigger for a new answer.
    """
    history_manager.log_feedback(
        username=req.username,
        session_id=req.session_id,
        original_query=req.original_query,
        feedback_type=req.feedback_type,
        feedback_text=req.feedback_text
    )

    # --- Telemetry: mirror feedback into the study_id-linked stream ---
    try:
        from app.core import telemetry
        telemetry.log_event(req.username, "feedback", {
            "feedback_type": req.feedback_type,
            "feedback_text": req.feedback_text,
            "original_query": req.original_query,
        }, session_id=req.session_id)
    except Exception as e:
        logger.warning(f"Feedback telemetry failed: {e}")

    # Update UCB1 style win rates based on thumbs up/down
    if req.feedback_type in ["up", "down"]:
        last_bot = db.fetch_one(
            "SELECT style_used FROM messages WHERE session_id=? AND role='bot' AND style_used IS NOT NULL ORDER BY timestamp DESC LIMIT 1",
            (req.session_id,)
        )
        if last_bot and last_bot["style_used"]:
            style = last_bot["style_used"]
            row = db.fetch_one("SELECT learning_profile FROM users WHERE username=?", (req.username,))
            profile = json.loads(row["learning_profile"] or "{}") if row and row["learning_profile"] else {}
            win_rates = profile.get("style_win_rates", {
                "analogy":   {"wins": 0, "total": 0},
                "technical": {"wins": 0, "total": 0},
                "visual":    {"wins": 0, "total": 0}
            })
            if style in win_rates:
                win_rates[style]["total"] += 1
                if req.feedback_type == "up":
                    win_rates[style]["wins"] += 1
            profile["style_win_rates"] = win_rates
            db.execute("UPDATE users SET learning_profile=? WHERE username=?",
                       (json.dumps(profile), req.username))
            logger.info(f"🎨 [UCB1] {req.feedback_type.upper()} → style='{style}' | wins={win_rates[style]['wins']}/{win_rates[style]['total']}")

    # --- FIX 1: Add distinct system tags and simplify the rewrite request ---
    if req.feedback_type in ["simplify", "deep_dive"]:
        if req.feedback_type == "simplify":
            modified_query = f"[SIMPLIFY] Explain the last concept again, but make it extremely simple for a beginner."
        elif req.feedback_type == "deep_dive":
            modified_query = f"[DEEP_DIVE] Explain the last concept again, but go into advanced technical detail, covering memory and edge cases."
            
        return {"action": "regenerate", "modified_query": modified_query}

    return {"status": "recorded"}

@app.post("/api/v1/assignments/create")
async def create_assignment(req: ChallengeRequest):
    aid = assignment_manager.create_challenge(req.teacher, req.student, req.question)
    return {"status": "success", "id": aid}

@app.get("/api/v1/assignments/student/{username}")
async def get_student_assignments(username: str):
    return assignment_manager.get_by_student(username)

@app.post("/api/v1/assignments/submit")
async def submit_assignment(req: SubmissionRequest):
    assignment_manager.submit_answer(req.assignment_id, req.answer)
    return {"status": "success"}

@app.get("/api/v1/assignments/teacher/pending")
async def get_pending_reviews(username: str):
    return assignment_manager.get_pending_reviews(username)

@app.post("/api/v1/assignments/grade")
async def grade_assignment(req: AssignmentGradeRequest):
    assignment_manager.grade_assignment(req.assignment_id, req.feedback)
    return {"status": "success"}

@app.get("/api/v1/analytics/active_time")
async def get_active_time_stats(username: str, days: int = 7):
    """
    Calculates active time on the fly.
    """
    duration_str = history_manager.calculate_active_time(username, days)
    return {"username": username, "days": days, "active_time": duration_str}

# --- THE MAGIC: AI CHECKER ---
@app.post("/api/v1/assignments/ai_check")
async def ai_check_assignment(req: SubmissionRequest):
    """
    Teacher clicks 'Check with AI'. 
    We send the student's code to the RAG Agent with intent='REVIEW'.
    """
    # 1. Get the original question (we need it for context)
    # In a real app, fetch from DB. For now, assume passed or construct prompt
    prompt = f"Teacher Question: [Hidden context]\nStudent Answer: {req.answer}\n\nTask: Review this code/answer. Be constructive."
    
    # Reuse your existing agent!
    # We use run_stream or just internal generation logic
    # For simplicity, let's use the llm_interface directly for a quick review
    
    review_prompt = f"""
    You are a Teaching Assistant. 
    Review this student submission.
    
    Student Answer:
    {req.answer}
    
    Provide a short, constructive critique (3-4 sentences). 
    Highlight 1 strength and 1 area for improvement.
    Do NOT give a grade.
    """
    
    feedback = llm_fast.generate_response(review_prompt)
    return {"ai_feedback": feedback}

@app.get("/api/v1/user/preferences/{username}")
async def get_user_preferences(username: str):
    row = db.fetch_one("SELECT learning_profile FROM users WHERE username = ?", (username,))
    profile = {}
    if row and row['learning_profile']:
        profile = json.loads(row['learning_profile'])
    
    return profile.get("tutor_preferences", {
        "custom_instructions": "",
        "show_explanation": True,
        "show_use_cases": True,
        "show_visual_model": True,
        "show_example_code": True,
        "literal_mode": False,
        "concise_mode": False,
        "dyslexia_font": False,
        "extra_spacing": False,
        "high_contrast": False,
        "break_reminders": False
    })

@app.post("/api/v1/user/preferences")
async def update_user_preferences(req: PreferencesUpdateRequest):
    row = db.fetch_one("SELECT learning_profile FROM users WHERE username = ?", (req.username,))
    profile = {}
    if row and row['learning_profile']:
        profile = json.loads(row['learning_profile'])
    
    # Update only the tutor_preferences, preserve other profiler data
    profile["tutor_preferences"] = req.preferences.dict()
    
    db.execute(
        "UPDATE users SET learning_profile = ? WHERE username = ?", 
        (json.dumps(profile), req.username)
    )
    return {"status": "success"}

# --- VIDEO CHAT ENDPOINTS ---
@app.post("/api/v1/video/chat")
async def video_chat_stream(request: VideoChatRequest):
    """
    Stream an AI response to a student question about video content.
    Uses transcript up to the student's current timestamp as PRIMARY context,
    and topic-filtered vectorDB resources as SUPPLEMENTARY context.
    """
    # 1. Resolve video path
    video_path = VIDEO_DIR / request.video_filename
    if not video_path.exists():
        raise HTTPException(status_code=404, detail=f"Video not found: {request.video_filename}")
    
    # 2. Get transcript up to the paused timestamp
    transcript_context = get_transcript_until(str(video_path), request.timestamp)
    
    # 3. Format the timestamp for display
    mins = int(request.timestamp // 60)
    secs = int(request.timestamp % 60)
    time_str = f"{mins:02d}:{secs:02d}"
    
    # 4. Detect the video's topic from filename
    TOPIC_KEYWORDS = {
        "variable": "Variables", "var": "Variables", "datatype": "Variables",
        "control": "Control Flow", "loop": "Control Flow", "switch": "Control Flow",
        "function": "Functions",
        "array": "Arrays",
        "string": "Strings",
        "pointer": "Pointers",
        "struct": "Structures",
    }
    video_stem = video_path.stem.lower()
    video_topic = "General"
    for keyword, topic_name in TOPIC_KEYWORDS.items():
        if keyword in video_stem:
            video_topic = topic_name
            break
    logger.info(f"🎬 [CLASSROOM] Video: {request.video_filename}, Topic: {video_topic}, Timestamp: {time_str}")

    # 4b. Meta-question: return high-level video summary, skip the full pipeline
    _META_PATTERNS = (
        "what is this video about", "what does this video cover", "what will i learn",
        "what are the core concepts", "what topics are covered", "summarize this video",
        "give me an overview", "what is covered", "overview of this video",
        "what's in this video", "what is taught here", "what does this teach",
    )
    if any(p in request.question.lower() for p in _META_PATTERNS):
        meta = get_or_generate_video_meta(str(video_path), llm_fast)
        summary = meta.get("summary") or f"This video covers {video_topic} concepts in C programming."
        logger.info(f"📋 [CLASSROOM] Meta-question → returning cached summary")

        async def generate_meta():
            yield f"data: {json.dumps({'type': 'token', 'text': summary})}\n\n"
            yield f"data: {json.dumps({'type': 'complete', 'data': {'answer': summary, 'timestamp': time_str, 'topic': video_topic, 'used_resources': False, 'resource_sources': []}})}\n\n"

        return StreamingResponse(generate_meta(), media_type="text/event-stream")

    # 5. Query vectorDB for supplementary resources (topic-filtered)
    supplementary_context = ""
    resource_sources = []
    if vector_store and vector_store.is_ready() and llm_fast and video_topic != "General":
        try:
            query_embedding = llm_fast.get_embedding(request.question)
            topic_filter = {"topic": video_topic}
            resource_chunks = vector_store.query(query_embedding, top_k=4, where_filter=topic_filter)
            
            if resource_chunks:
                chunk_texts = []
                for chunk in resource_chunks:
                    if chunk.get("score", 0) > 0.25:  # Only include relevant chunks
                        chunk_texts.append(chunk["text"])
                        source_name = chunk.get("metadata", {}).get("source_file", "Course Material")
                        if source_name not in resource_sources:
                            resource_sources.append(source_name)
                
                if chunk_texts:
                    supplementary_context = "\n\n".join(chunk_texts)
                    logger.info(f"📚 [CLASSROOM] Found {len(chunk_texts)} supplementary chunks for topic '{video_topic}'")
        except Exception as e:
            logger.warning(f"⚠️ [CLASSROOM] VectorDB query failed (non-fatal): {e}")
    
    # 6. Build the LLM prompt with two-tier context
    has_supplement = bool(supplementary_context.strip())
    
    system_prompt = f"""You are a helpful AI Teaching Assistant. A student is watching a lecture video about **{video_topic}** and has paused at {time_str} to ask a question.

IMPORTANT RULES:
1. **PRIMARY SOURCE**: Use the LECTURE TRANSCRIPT below as your main source. Reference specific points from the lecture when possible (e.g., "As the instructor explained at 01:23...").
2. **SUPPLEMENTARY SOURCE**: If the transcript doesn't fully answer the question but the COURSE MATERIALS section has relevant information, use it to provide a complete answer. Clearly indicate when you're drawing from course materials vs. the lecture.
3. If NEITHER source covers the student's question, tell them: "This topic hasn't been covered yet in the lecture or available course materials."
4. Be concise and educational. Use examples if helpful.
5. Format your response with Markdown for readability.

--- LECTURE TRANSCRIPT (0:00 to {time_str}) ---
{transcript_context}
--- END TRANSCRIPT ---"""

    if has_supplement:
        system_prompt += f"""

--- COURSE MATERIALS ({video_topic}) ---
{supplementary_context}
--- END COURSE MATERIALS ---"""

    full_prompt = f"{system_prompt}\n\nStudent Question: {request.question}"
    
    async def generate():
        full_response = ""
        try:
            if not llm_fast:
                yield f"data: {json.dumps({'type': 'error', 'message': 'LLM not initialized'})}\n\n"
                return
            
            # Stream tokens
            async for chunk in llm_fast.stream_response_async(full_prompt):
                full_response += chunk
                yield f"data: {json.dumps({'type': 'token', 'text': chunk})}\n\n"

            # Timestamp hints (forward + backward)
            hints = find_timestamp_hints(str(video_path), request.question, request.timestamp)
            hint_text = ""

            if hints.get("backward"):
                h = hints["backward"]
                bm, bs = int(h["start"] // 60), int(h["start"] % 60)
                em, es = int(h["end"]   // 60), int(h["end"]   % 60)
                hint_text += (
                    f"\n\n---\n⏮️ **Already covered:** "
                    f"This was discussed at **{bm:02d}:{bs:02d}–{em:02d}:{es:02d}** "
                    f"in the video — you may want to revisit it."
                )

            if hints.get("forward"):
                h = hints["forward"]
                fm, fs = int(h["start"] // 60), int(h["start"] % 60)
                em, es = int(h["end"]   // 60), int(h["end"]   % 60)
                hint_text += (
                    f"\n\n---\n⏭️ **Coming up ahead:** "
                    f"This is also covered at **{fm:02d}:{fs:02d}–{em:02d}:{es:02d}** "
                    f"in the video — keep watching!"
                )

            if hint_text:
                full_response += hint_text
                yield f"data: {json.dumps({'type': 'token', 'text': hint_text})}\n\n"
                logger.info(f"⏱️ [CLASSROOM] Hints → backward={hints['backward']} forward={hints['forward']}")

            # Send completion event with source metadata
            completion_data = {
                'answer': full_response,
                'timestamp': time_str,
                'topic': video_topic,
                'used_resources': has_supplement,
                'resource_sources': resource_sources,
            }
            yield f"data: {json.dumps({'type': 'complete', 'data': completion_data})}\n\n"
                
        except Exception as e:
            logger.error(f"Video chat streaming error: {e}", exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
    
    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/api/v1/history/save-classroom")
async def save_classroom_history(req: ClassroomHistoryRequest):
    """
    Saves a classroom Q&A pair directly to history without going through the AI pipeline.
    Creates a new session titled '📹 {video_title}' on first call, reuses it on subsequent calls.
    """
    session_id = req.session_id
    if not session_id:
        session_id = history_manager.create_session(req.username, title=f"📹 {req.video_title}")
    history_manager.add_message(req.username, session_id, "user", req.question)
    history_manager.add_message(req.username, session_id, "bot",  req.answer)
    return {"session_id": session_id}


@app.get("/api/v1/video/list")
async def get_video_list():
    """Returns a list of available lecture videos, filtered by teacher-enabled topics."""
    all_videos = list_available_videos(str(VIDEO_DIR))
    
    # Get teacher-enabled topics
    enabled_topics = settings_manager.get_settings()
    
    # Filter: only include videos whose topic is enabled (or if no topic match, include it)
    filtered = []
    for v in all_videos:
        topic = v.get("topic", "General")
        # If the topic exists in settings and is enabled, or if it's not in settings at all (show by default)
        if enabled_topics.get(topic, True):
            filtered.append(v)
    
    return {"videos": filtered}


@app.get("/videos/{video_filename:path}")
async def serve_video(video_filename: str):
    """Dynamically serves video files from VIDEO_DIR (replaces static mount for Docker compatibility)."""
    import mimetypes
    video_path = VIDEO_DIR / video_filename
    
    # Security: prevent path traversal
    try:
        video_path = video_path.resolve()
        VIDEO_DIR.resolve()
        if not str(video_path).startswith(str(VIDEO_DIR.resolve())):
            raise HTTPException(status_code=403, detail="Access denied")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid path")
    
    if not video_path.exists() or not video_path.is_file():
        logger.error(f"Video not found: {video_path} (VIDEO_DIR={VIDEO_DIR}, exists={VIDEO_DIR.exists()})")
        raise HTTPException(status_code=404, detail=f"Video not found: {video_filename}")
    
    media_type, _ = mimetypes.guess_type(str(video_path))
    return FileResponse(
        path=str(video_path),
        media_type=media_type or "video/mp4",
        filename=video_filename,
    )


@app.post("/api/v1/video/transcribe")
async def trigger_transcription(video_filename: str = Form(...)):
    """
    Pre-transcribes a video so the first chat doesn't have to wait.
    """
    video_path = VIDEO_DIR / video_filename
    if not video_path.exists():
        raise HTTPException(status_code=404, detail=f"Video not found: {video_filename}")
    
    try:
        segments = transcribe_video(str(video_path))
        return {"status": "success", "segments": len(segments)}
    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/video/transcript/{video_filename}")
async def get_video_transcript(video_filename: str):
    """Returns the full transcript segments for a video (for synced display)."""
    video_path = VIDEO_DIR / video_filename
    if not video_path.exists():
        raise HTTPException(status_code=404, detail=f"Video not found: {video_filename}")
    
    try:
        segments = transcribe_video(str(video_path))
        return {"segments": segments}
    except Exception as e:
        logger.error(f"Transcript fetch failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Classroom Video: checkpoints, engagement, MCQ, reflection ───────────────

# Dynamic checkpoint placement: the video is divided into equal segments whose
# count scales with length (target ~8.5 min apart), with one at the end.
CHECKPOINT_SPACING_SEC = 510.0
CHECKPOINT_MIN = 1
CHECKPOINT_MAX = 8

class VideoEngagementRequest(BaseModel):
    username: str
    video_filename: str
    event: str                       # play|pause|seek|ended|tab_hidden|tab_visible|mute|unmute
    position_sec: Optional[float] = None
    detail: Optional[str] = None

class VideoCoverageRequest(BaseModel):
    username: str
    video_filename: str
    duration_sec: float
    watched_sec: float
    hidden_sec: float = 0.0

class VideoMCQAnswerRequest(BaseModel):
    username: str
    video_filename: str
    checkpoint_time: float
    selected_index: int
    confidence: Optional[int] = 3
    time_to_answer_sec: Optional[float] = None
    attempts: Optional[int] = 1

class VideoReflectionRequest(BaseModel):
    username: str
    video_filename: str
    phase: str                       # intention | reflection
    prompt: Optional[str] = ""
    response: str


@app.get("/api/v1/video/checkpoints/{video_filename}")
async def get_video_checkpoints(video_filename: str):
    """Return checkpoint MCQs for a video (WITHOUT the correct answer).
    Generates + caches them from the transcript on first request."""
    video_path = VIDEO_DIR / video_filename
    if not video_path.exists():
        raise HTTPException(status_code=404, detail=f"Video not found: {video_filename}")

    rows = db.fetch_all(
        "SELECT checkpoint_time, question, options, concept FROM video_checkpoint "
        "WHERE video_filename=? ORDER BY checkpoint_time", (video_filename,))

    if not rows:
        # Generate + cache
        if not llm_fast:
            raise HTTPException(status_code=503, detail="LLM not initialized")
        generated = await asyncio.to_thread(
            generate_checkpoints, str(video_path), llm_fast,
            CHECKPOINT_SPACING_SEC, CHECKPOINT_MIN, CHECKPOINT_MAX)
        for cp in generated:
            db.execute(
                "INSERT OR IGNORE INTO video_checkpoint "
                "(video_filename, checkpoint_time, question, options, correct_index, "
                " concept, explanation, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (video_filename, cp["checkpoint_time"], cp["question"],
                 json.dumps(cp["options"]), cp["correct_index"], cp["concept"],
                 cp["explanation"], datetime.utcnow().isoformat()))
        rows = db.fetch_all(
            "SELECT checkpoint_time, question, options, concept FROM video_checkpoint "
            "WHERE video_filename=? ORDER BY checkpoint_time", (video_filename,))

    checkpoints = [{
        "checkpoint_time": r["checkpoint_time"],
        "question": r["question"],
        "options": json.loads(r["options"]),
        "concept": r["concept"],
    } for r in rows]
    return {"checkpoints": checkpoints}


@app.post("/api/v1/video/checkpoint/answer")
async def answer_video_checkpoint(req: VideoMCQAnswerRequest):
    """Grade a checkpoint MCQ, feed BKT (quiz tier), and log telemetry."""
    from app.core import telemetry
    from app.core.bkt_model import bkt

    row = db.fetch_one(
        "SELECT correct_index, concept, explanation FROM video_checkpoint "
        "WHERE video_filename=? AND checkpoint_time=?",
        (req.video_filename, req.checkpoint_time))
    if not row:
        raise HTTPException(status_code=404, detail="Checkpoint not found")

    is_correct = (req.selected_index == row["correct_index"])
    concept = row["concept"] or "General"

    # Feed BKT quiz tier (video learning contributes to mastery)
    if concept and concept != "General":
        try:
            bkt.update(req.username, concept, is_correct, evidence_type="quiz")
        except Exception as e:
            logger.warning(f"[video-mcq] BKT update failed: {e}")

    telemetry.log_video_mcq(
        req.username, req.video_filename, req.checkpoint_time, concept,
        req.selected_index, is_correct, req.confidence or 3,
        req.time_to_answer_sec, req.attempts or 1)
    telemetry.log_event(req.username, "video_checkpoint_answer", {
        "video": req.video_filename, "checkpoint": req.checkpoint_time,
        "concept": concept, "correct": is_correct, "attempts": req.attempts})

    return {
        "is_correct": is_correct,
        "correct_index": row["correct_index"],
        "explanation": row["explanation"],
    }


@app.post("/api/v1/video/engagement")
async def log_video_engagement_event(req: VideoEngagementRequest):
    from app.core import telemetry
    telemetry.log_video_engagement(req.username, req.video_filename, req.event,
                                   req.position_sec, req.detail)
    return {"status": "ok"}


@app.post("/api/v1/video/coverage")
async def update_video_coverage_summary(req: VideoCoverageRequest):
    from app.core import telemetry
    telemetry.update_video_coverage(req.username, req.video_filename,
                                    req.duration_sec, req.watched_sec, req.hidden_sec)
    return {"status": "ok"}


@app.post("/api/v1/video/reflection")
async def submit_video_reflection(req: VideoReflectionRequest):
    from app.core import telemetry
    telemetry.log_video_reflection(req.username, req.video_filename, req.phase,
                                   req.prompt or "", req.response)
    return {"status": "ok"}


@app.get("/api/v1/video/prelab/{video_filename}")
async def get_video_prelab(video_filename: str):
    """Return the list of prelab complex problems for a video (from prelab.json)."""
    prelab_path = VIDEO_DIR / "prelab.json"
    if not prelab_path.exists():
        return {"problems": []}
    try:
        with open(prelab_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        raw = data.get(video_filename, [])
        # Support both the simple list form and an object with a "problems" key
        problems = raw if isinstance(raw, list) else raw.get("problems", [])
        # Normalize each entry to a string prompt
        norm = [p if isinstance(p, str) else p.get("prompt", "") for p in problems]
        norm = [p for p in norm if p and p.strip()]
        return {"problems": norm}
    except Exception as e:
        logger.warning(f"Prelab load failed for {video_filename}: {e}")
        return {"problems": []}


class PrelabStartRequest(BaseModel):
    username: str
    video_filename: str
    problem: str

@app.post("/api/v1/video/prelab-start")
async def log_prelab_start(req: PrelabStartRequest):
    """Log that a student chose to solve a prelab problem in SAGE (SRL transfer signal)."""
    from app.core import telemetry
    telemetry.log_event(req.username, "prelab_started", {
        "video": req.video_filename, "problem": req.problem[:300]})
    return {"status": "ok"}


@app.post("/api/v1/tts/speak")
async def tts_speak(req: TTSSpeakRequest):
    """Converts text to speech using OpenAI TTS (nova voice). Strips markdown first."""
    if not config.OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="TTS unavailable: OPENAI_API_KEY not configured.")

    cleaned = _clean_text_for_tts(req.text)
    if len(cleaned) > 4096:
        cleaned = cleaned[:4093] + "..."
    if not cleaned.strip():
        raise HTTPException(status_code=400, detail="No speakable text after cleaning.")

    try:
        import openai as _openai  # local import — avoids interfering with langchain-openai at module level
        client = _openai.OpenAI(api_key=config.OPENAI_API_KEY)
        response = client.audio.speech.create(
            model="tts-1",
            voice=req.voice,
            input=cleaned,
            response_format="mp3",
        )
        from io import BytesIO
        return StreamingResponse(
            BytesIO(response.content),
            media_type="audio/mpeg",
            headers={"Content-Disposition": "inline; filename=speech.mp3"},
        )
    except _openai.AuthenticationError:
        raise HTTPException(status_code=503, detail="TTS authentication failed.")
    except _openai.RateLimitError:
        raise HTTPException(status_code=429, detail="TTS rate limit. Try again shortly.")
    except Exception as e:
        logger.error(f"TTS error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"TTS failed: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)