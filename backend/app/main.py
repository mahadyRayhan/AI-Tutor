# backend/app/main.py
import os
import asyncio
import shutil
import subprocess
from fastapi import FastAPI, HTTPException, UploadFile, File, Form 
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
import logging
import re
import time
import json
from fastapi.responses import StreamingResponse, FileResponse
from pathlib import Path
import hashlib
import secrets
from datetime import timedelta
from datetime import datetime
import csv

from pyinstrument import Profiler
from fastapi.staticfiles import StaticFiles # Needed to serve the reports
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi import Header, Depends
from fastapi import Request, Response, BackgroundTasks
from pathlib import Path

# Import components
from app.core import config
from app.core import auth
from app.core.rate_limiter import rate_limit
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


def _bkt_topic_mastery(username: str) -> Dict[str, float]:
    """Real 3-tier BKT mastery per dashboard topic, as 0-100.

    The student dashboard used to render `_calculate_mastery` — an XP heuristic over
    message intents — while the node detail panel and the certification ring read the
    BKT model. The same topic therefore reported different numbers on three surfaces
    of one screen: a fully certified topic showed as "5" on the skill graph and sat at
    the origin of the proficiency radar.

    The instructor detail modal was already migrated off that heuristic for exactly
    this reason (see analytics/student_detail); this is the student-side equivalent.

    A topic with NO evidence returns 0, never its Cromwell prior — the prior is a
    starting belief, not progress, and rendering ~19% for an untouched topic is the
    same "baseline looks like progress" confusion the priors already cause elsewhere.
    """
    from app.core import bkt_model

    out: Dict[str, float] = {}
    for topic in SKILL_TOPICS:
        try:
            row = bkt_model._read_row(username, topic)
            n_ev = 0 if not row else (
                (row["n_evidence_quiz"] or 0)
                + (row["n_evidence_micro"] or 0)
                + (row["n_evidence_code"] or 0)
            )
            if not row or n_ev == 0:
                out[topic] = 0.0
                continue
            # composite ∈ [0, 0.95] (the tier ceilings sum to 0.95) → 0-100
            composite = bkt_model.get_mastery(username, topic)
            out[topic] = round(min(100.0, composite / 0.95 * 100), 1)
        except Exception as e:
            logger.warning(f"[dashboard] mastery read failed for '{topic}': {e}")
            out[topic] = 0.0
    return out


async def verify_teacher(user: dict = Depends(auth.require_teacher)):
    """Authorize a teacher. The role is read from the SIGNED SESSION COOKIE.

    This previously compared a client-supplied `X-User-Role` header against
    "teacher". The frontend sourced that header from localStorage, so editing
    `c_tutor_user` in devtools to {"role":"teacher"} both unlocked the teacher UI
    and satisfied this check — a reported and confirmed privilege escalation.
    A signed token cannot be forged from the browser, and the cookie carrying it is
    httpOnly so page JavaScript cannot read or alter it.

    Kept as a thin wrapper so the six existing Depends(verify_teacher) call sites
    keep working unchanged.
    """
    return user

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
    # Cap message length to bound per-request LLM token cost / abuse. Oversized
    # payloads are rejected with 422 before any model call is made.
    message: str = Field(..., max_length=config.MAX_MESSAGE_CHARS)
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
LATEST_PATH_BY_USER = {}     # username -> most recently GENERATED path, for telemetry


def _path_for_telemetry(username: str, goal: str = None) -> list:
    """The learner's current path, as [{concept, status}], for deviation logging.

    This used to read LEARNING_PATH_CACHE with the key f"{username}_{goal}", but
    the cache is WRITTEN under f"{username}_{goal}_{known_concepts}" — the keys
    never matched, so the lookup always missed, classify_path_deviation() got an
    empty list, and all 1,247 recorded events were "unknown"/NULL.

    Two changes: read the path the dashboard actually generated (kept in
    LATEST_PATH_BY_USER, no key juggling), and when there is none — the common
    case, since most turns happen before a student ever opens their dashboard —
    derive one from CANONICAL_TOPICS, which is already in prerequisite order.
    The fallback needs no cache and no LLM call, so deviation is always
    classifiable rather than only when a cache happens to be warm.
    """
    cached = LATEST_PATH_BY_USER.get(username)
    if cached:
        return cached

    try:
        from app.core.concept_canon import CANONICAL_TOPICS
        from app.core.bkt_model import bkt

        path, found_next = [], False
        for topic in CANONICAL_TOPICS:
            try:
                mastered = bkt.is_mastered(username, topic)
            except Exception:
                mastered = False
            if mastered:
                status = "mastered"
            elif not found_next:
                status = "next"          # first unmastered concept in prereq order
                found_next = True
            else:
                status = "locked"        # beyond the frontier → asking here is skip_ahead
            path.append({"concept": topic, "status": status})
        return path
    except Exception as e:
        logger.warning(f"[telemetry] path fallback failed for {username}: {e}")
        return []

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    # Pass arguments explicitly by name
    return templates.TemplateResponse(
        request=request, 
        name="index.html", 
        context={"request": request}
    )

# Pages that require a teacher/admin session to be served at all.
_STAFF_PAGES = {"teacher_dashboard"}


# 4. Dynamic Page Endpoint
@app.get("/{page_name}.html", response_class=HTMLResponse)
async def serve_specific_html(request: Request, page_name: str):
    """ Dynamically serves teacher_dashboard.html, student_dashboard.html, etc. """
    file_name = f"{page_name}.html"
    file_path = TEMPLATES_DIR / file_name

    # Gate staff-only pages on the SESSION, not on client-side JS. The in-page
    # check can only run after the browser has already been handed the markup, so
    # a student could read the dashboard's structure (and its inline JS) before
    # being redirected. The APIs behind it already return 403, so this leaks no
    # student data — but the page should not be served in the first place.
    if page_name in _STAFF_PAGES:
        viewer = auth.get_current_user_optional(request)
        if not viewer or viewer["role"] not in ("teacher", "admin"):
            # Send them home rather than showing a bare 403: an unauthenticated
            # teacher just needs to log in, and a student clicked something they
            # shouldn't have seen.
            return RedirectResponse(url="/", status_code=303)

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

@app.post("/api/v1/chat/stream",
          dependencies=[Depends(rate_limit("chat", config.RATE_LIMIT_CHAT_MAX, 60))])
async def chat_stream(request: ChatRequest, _caller: dict = Depends(auth.get_current_user)):
    # AUTHZ: overwrite the client-supplied identity with the session's.
    # `username` and `user_role` arrive in the request BODY, and everything
    # downstream — chat history, BKT evidence, session state — keys off them. A
    # student could therefore POST as another student and write to that account.
    # The body values are now ignored entirely rather than merely validated.
    request.username = _caller["username"]
    request.user_role = _caller["role"]

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
    skip_save = msg_text in ["[INIT_SESSION]", "[NEW_CHAT]"] or msg_text.startswith("[WARMUP_ANSWER] skip") or msg_text.startswith("[MASTERY_EXAM]")
    
    if skip_save:
        user_msg_id = None
    else:
        user_msg_id = history_manager.add_message(request.username, session_id, "user", request.message)

    # Watchdog: max seconds to wait for the *next* event from the agent before we
    # treat the stream as hung. Generous enough for RAG + first-token latency, but
    # bounded so a stalled LLM can never leave the client "stuck on generating".
    STREAM_STALL_TIMEOUT = 90

    async def generate_stream():
        full_bot_response = ""
        final_sources = []
        style_used_for_session = None
        _turn_start = time.time()
        terminal_sent = False   # did we emit a terminal 'complete' event?
        _agen = None

        try:
            if not cot_rag_agent:
                yield f"data: {json.dumps({'type': 'complete', 'data': {'answer': '⚠️ The tutor is still starting up. Please try again in a moment.', 'sources': [], 'intent': 'ERROR', 'suggestions': [], 'session_id': session_id}})}\n\n"
                terminal_sent = True
                return

            user_goal = knowledge_manager.get_goal(request.username)

            # --- 5. RUN AGENT (timeout-guarded manual iteration) ---
            # We drive the async generator by hand so we can bound how long we wait
            # for each event; a hung LLM call trips STREAM_STALL_TIMEOUT and the
            # finally block below still emits a terminal event to the client.
            _agen = cot_rag_agent.run_stream(
                request.message,
                request.user_role,
                username=request.username,
                user_goal=user_goal,
                conversation_context=last_context,
                session_id=session_id
            )
            while True:
                try:
                    event = await asyncio.wait_for(_agen.__anext__(), timeout=STREAM_STALL_TIMEOUT)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    logger.error(f"⏱️ [Stream] Agent produced no event for >{STREAM_STALL_TIMEOUT}s — aborting to avoid a stuck client.")
                    break
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

                    # 5. Last resort: the session's remembered topic.
                    # The keyword map above runs over EXTRACTED ENTITIES, and a pasted
                    # code block yields tokens like "printf" that match none of them —
                    # so every REVIEW landed on "General". REVIEW is the highest-weighted
                    # activity on the skill graph (+15) and none of it ever reached a
                    # node; it also made the analytics history unusable for code work.
                    if detected_topic == "General":
                        try:
                            _sess = history_manager.get_session_state(request.username, session_id) or {}
                            _anchor = _sess.get("last_valid_topic")
                            from app.core.concept_canon import is_attributable_concept
                            if _anchor and is_attributable_concept(_anchor):
                                detected_topic = _anchor
                        except Exception as e:
                            logger.warning(f"[analytics] topic anchor fallback failed: {e}")
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
                        # run_stream now attaches `_state` on every terminal event, so the
                        # block signal is the explicit flag it sets, not the key's absence.
                        # The fallback keeps the old rule for any path that bypasses the
                        # wrapper.
                        _was_blocked = bool(_state.get("blocked", "_state" not in final_data))
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
                            # The Sentinel names WHY it blocked ("Cognitive Overload",
                            # "Academic Integrity", "Trajectory Risk", …) and puts it in
                            # final_data["block_reason"]. Logging the intent instead
                            # discarded all ten reasons and collapsed them into
                            # GUIDANCE / SECURITY_RISK, so the ledger could never show
                            # cognitive blocks. Prefer the real reason; fall back to
                            # intent only when the block came from somewhere else.
                            block_reason=((final_data.get("block_reason")
                                           or final_data.get("intent")) if _was_blocked else None),
                            traj_risk=_state.get("traj_risk"),
                            traj_acc=_state.get("traj_acc"),
                            traj_peak=_state.get("traj_peak"),
                        )
                        # Path deviation (Forethought / SRL) using cached learning path
                        if detected_topic and detected_topic != "General":
                            _goal = knowledge_manager.get_goal(request.username)
                            _path = _path_for_telemetry(request.username, _goal)
                            _dev, _expected = telemetry.classify_path_deviation(_path, detected_topic)
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
                    terminal_sent = True   # this is a terminal 'complete' event

                yield f"data: {json.dumps(event)}\n\n"

        except Exception as e:
            # Log, but don't emit a bespoke 'error' here — the finally block emits a
            # single guaranteed terminal 'complete' so the client always resolves.
            logger.error(f"Streaming error: {e}", exc_info=True)

        finally:
            # Close the underlying agent generator (it may have been cancelled by the
            # stall timeout mid-step); ignore any teardown noise.
            if _agen is not None:
                try:
                    await _agen.aclose()
                except Exception:
                    pass

            # --- TERMINAL-EVENT GUARANTEE ---
            # If the agent finished (or hung, or crashed) without emitting a 'complete',
            # the client would sit on the spinner forever ("stuck on generating").
            # Emit one final terminal event so the UI always resolves.
            if not terminal_sent:
                _partial = full_bot_response.strip()
                _ans = _partial or "⚠️ Sorry — I couldn't finish that response. Please try again."
                _fallback_event = {
                    "type": "complete",
                    "data": {
                        "answer": _ans,
                        "sources": final_sources,
                        "intent": "PARTIAL" if _partial else "ERROR",
                        "suggestions": [],
                        "session_id": session_id,
                    },
                }
                logger.warning(f"🔚 [Stream] No terminal event from agent — emitting fallback ({'partial' if _partial else 'error'}).")
                yield f"data: {json.dumps(_fallback_event)}\n\n"
                terminal_sent = True

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
async def get_student_stats(username: str, _caller: dict = Depends(auth.get_current_user)):
    """FAST Endpoint: Returns charts data only."""
    # AUTHZ: the path parameter selects WHICH record; the signed session decides
    # whether this caller may see it. Without this, any username in the URL
    # returned that student's data to anyone who asked.
    auth.require_self_or_teacher(username, _caller)
    history = history_manager.get_student_history(username)
    goal = knowledge_manager.get_goal(username)

    # `mastery` drives BOTH the proficiency radar and the skill-graph node numbers, so
    # it must be the same model the detail panel and the certification ring read —
    # otherwise one screen reports a topic as certified, "5", and ~0 simultaneously.
    mastery = _bkt_topic_mastery(username)
    # The old intent-XP heuristic is kept as a SEPARATE, honestly-named field: it
    # measures activity, not knowledge, and it silently drops every code review
    # (those messages are tagged topic="General", so +15 XP lands on no node).
    activity_xp = _calculate_mastery(history)

    stats = {"CONCEPT": 0, "PROBLEM": 0, "DEBUG": 0, "REVIEW": 0}
    for h in history:
        i = h.get('intent', 'UNKNOWN')
        if i in stats: stats[i] += 1

    return {
        "stats": stats,
        "mastery": mastery,
        "activity_xp": activity_xp,
        "total_queries": len(history),
        "goal": goal
    }

@app.get("/api/v1/analytics/report/{username}")
def get_student_report(username: str, _caller: dict = Depends(auth.get_current_user)):
    """SLOW Endpoint: Returns LLM advice using the Smart Model."""
    # AUTHZ: the path parameter selects WHICH record; the signed session decides
    # whether this caller may see it. Without this, any username in the URL
    # returned that student's data to anyone who asked.
    auth.require_self_or_teacher(username, _caller)
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

@app.post("/api/v1/auth/login",
          dependencies=[Depends(rate_limit("auth", config.RATE_LIMIT_AUTH_MAX, 60))])
async def login(creds: LoginRequest, response: Response):
    try:
        user = user_manager.authenticate(creds.username, creds.password)
        if user:
            # Issue a signed session cookie. The returned `user` object is for
            # DISPLAY ONLY — the browser copy is advisory and is never trusted for
            # authorization again. Identity and role now come from this token.
            token = auth.create_token(user["username"], user["role"], user.get("name", ""))
            auth.set_session_cookie(response, token)
            return {"status": "success", "user": user}
        else:
            raise HTTPException(status_code=401, detail="Invalid credentials")
    except HTTPException:
        raise
    except Exception as e:
        # Catch the "Account Blocked" exception from user_manager
        raise HTTPException(status_code=403, detail=str(e))


# ── Account recovery ──────────────────────────────────────────────────────────
# Every endpoint here returns the SAME response whether or not the address is
# registered. Differentiating would turn these into an account-enumeration oracle:
# anyone could test a list of addresses and learn who has a SAGE account.

class ForgotUsernameRequest(BaseModel):
    email: str


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


_RESET_TTL_MINUTES = 60
_GENERIC_RECOVERY_REPLY = {
    "status": "success",
    "message": "If that email is registered, we've sent instructions to it.",
}


def _hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@app.post("/api/v1/auth/forgot-username",
          dependencies=[Depends(rate_limit("auth", config.RATE_LIMIT_AUTH_MAX, 60))])
async def forgot_username(req: ForgotUsernameRequest):
    """Email the username associated with an address."""
    from app.core import email_service
    user = user_manager.find_by_email(req.email)
    if user:
        email_service.send(
            user["email"],
            "Your SAGE username",
            f"Hi {user.get('name') or 'there'},\n\n"
            f"Your SAGE username is:  {user['username']}\n\n"
            f"Sign in at {email_service.base_url()}\n\n"
            f"If you did not request this, you can ignore this email.\n")
    return _GENERIC_RECOVERY_REPLY


@app.post("/api/v1/auth/forgot-password",
          dependencies=[Depends(rate_limit("auth", config.RATE_LIMIT_AUTH_MAX, 60))])
async def forgot_password(req: ForgotPasswordRequest):
    """Email a single-use, time-limited password reset link."""
    from app.core import email_service
    user = user_manager.find_by_email(req.email)
    if user and not user.get("is_blocked"):
        raw = secrets.token_urlsafe(32)
        now = datetime.now()
        # Invalidate any outstanding links for this account, so requesting a new
        # one revokes the old — a link sitting in an old email should stop working.
        db.execute("DELETE FROM password_reset_token WHERE username = ?", (user["username"],))
        db.execute(
            "INSERT INTO password_reset_token (token_hash, username, created_at, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (_hash_reset_token(raw), user["username"], now,
             now + timedelta(minutes=_RESET_TTL_MINUTES)))
        link = f"{email_service.base_url()}/reset_password.html?token={raw}"
        email_service.send(
            user["email"],
            "Reset your SAGE password",
            f"Hi {user.get('name') or 'there'},\n\n"
            f"Use this link to choose a new password. It expires in "
            f"{_RESET_TTL_MINUTES} minutes and can only be used once:\n\n"
            f"{link}\n\n"
            f"Your username is:  {user['username']}\n\n"
            f"If you did not request this, ignore this email — your password is unchanged.\n")
    return _GENERIC_RECOVERY_REPLY


@app.post("/api/v1/auth/reset-password",
          dependencies=[Depends(rate_limit("auth", config.RATE_LIMIT_AUTH_MAX, 60))])
async def reset_password(req: ResetPasswordRequest):
    """Consume a reset token and set a new password."""
    from app.core import validators

    row = db.fetch_one(
        "SELECT username, expires_at, used_at FROM password_reset_token WHERE token_hash = ?",
        (_hash_reset_token(req.token or ""),))
    if not row:
        raise HTTPException(status_code=400, detail="This reset link is invalid or has already been used.")
    if row["used_at"]:
        raise HTTPException(status_code=400, detail="This reset link has already been used.")
    if datetime.fromisoformat(str(row["expires_at"])) < datetime.now():
        raise HTTPException(status_code=400, detail="This reset link has expired. Please request a new one.")

    username = row["username"]
    # Same strength rules as signup — a reset must not be a way around them.
    err = validators.validate_password(req.new_password, username, "")
    if err:
        raise HTTPException(status_code=400, detail=err)

    if not user_manager.set_password(username, req.new_password):
        raise HTTPException(status_code=500, detail="Could not update the password. Please try again.")

    db.execute("UPDATE password_reset_token SET used_at = ? WHERE token_hash = ?",
               (datetime.now(), _hash_reset_token(req.token)))
    logger.info(f"[recovery] password reset completed for {username}")
    return {"status": "success", "username": username}


@app.post("/api/v1/auth/logout")
async def logout(response: Response):
    """Clear the session cookie. The client cannot do this itself — the cookie is
    httpOnly by design."""
    auth.clear_session_cookie(response)
    return {"status": "success"}


@app.get("/api/v1/auth/me")
async def whoami(user: dict = Depends(auth.get_current_user)):
    """Who does the SERVER think you are? The frontend calls this on load to
    rehydrate its display state from the token rather than from localStorage."""
    return {"user": user}

@app.post("/api/v1/auth/signup",
          dependencies=[Depends(rate_limit("auth", config.RATE_LIMIT_AUTH_MAX, 60))])
async def signup(req: SignupRequest):
    from app.core import validators

    # 1. Authoritative server-side validation (never trust the client).
    field, err = validators.validate_signup(req.name, req.email, req.username, req.password)
    if err:
        raise HTTPException(status_code=422, detail=err)

    name = req.name.strip()
    username = req.username.strip()
    email = validators.normalize_email(req.email)

    # 2. Uniqueness checks with specific, actionable messages.
    if user_manager.email_taken(email):
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    success = user_manager.create_user(
        username=username,
        password=req.password,
        name=name,
        email=email,
        university=req.university,
        department=req.department,
        interest=req.interest
    )
    if success:
        return {"status": "success", "message": "Account created! Please log in."}
    else:
        raise HTTPException(status_code=409, detail="That username is already taken.")

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
async def update_user(req: AdminUserUpdate, _t: dict = Depends(verify_teacher)):
    # AUTHZ: teacher-only. These endpoints expose whole-class data and admin
    # actions and previously had NO guard at all — reachable by anyone who
    # could reach the port.
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
def get_learning_path_endpoint(username: str, _caller: dict = Depends(auth.get_current_user)):
    # AUTHZ: the path parameter selects WHICH record; the signed session decides
    # whether this caller may see it. Without this, any username in the URL
    # returned that student's data to anyone who asked.
    auth.require_self_or_teacher(username, _caller)
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
        LATEST_PATH_BY_USER[username] = path_data

        return {"goal": goal, "path": path_data}
        
    except Exception as e:
        print(f"Error generating learning path via LLM: {e}")
        return {"goal": goal, "path": []}

@app.get("/api/v1/analytics/student/{username}")
def get_student_analytics(username: str, _caller: dict = Depends(auth.get_current_user)):
    # AUTHZ: the path parameter selects WHICH record; the signed session decides
    # whether this caller may see it. Without this, any username in the URL
    # returned that student's data to anyone who asked.
    auth.require_self_or_teacher(username, _caller)
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
async def get_teacher_analytics(_t: dict = Depends(verify_teacher)):
    # AUTHZ: teacher-only. These endpoints expose whole-class data and admin
    # actions and previously had NO guard at all — reachable by anyone who
    # could reach the port.
    """
    Returns global class stats using SQL aggregation.
    """
    # 1. Fetch all analytics data (Intent and Topic)
    # We only care about messages where intent/topic were actually logged
    rows = db.fetch_all("SELECT topic, intent FROM messages WHERE topic IS NOT NULL")
    
    # Group raw intents into teacher-facing "kind of help" buckets. ONLY student-initiated
    # intents count here — a real ask, code submitted, an error brought, a quiz taken.
    # Tutor-side / system labels (GUIDANCE fallbacks, GUIDED_PRACTICE, PLANNING, GREETING)
    # and SECURITY_RISK are intentionally excluded — they aren't things students asked for
    # (evasion lives in the Security Ledger).
    INTENT_LABEL = {
        "CONCEPT": "Understand a concept",
        "PROBLEM": "Solve a problem", "COMPLEX_PROBLEM": "Solve a problem",
        "DEBUG": "Debug an error",
        "REVIEW": "Review my code",
        "QUIZ": "Quiz / test", "EXAM": "Quiz / test", "EVALUATION": "Quiz / test",
    }

    # 2. Process in Python (keeping your existing logic logic)
    topic_counts = {}
    struggle_counts = {}
    question_types = {}

    for r in rows:
        topic = r['topic'] or 'General'
        intent = r['intent'] or 'UNKNOWN'

        # Count Topics
        topic_counts[topic] = topic_counts.get(topic, 0) + 1

        # Count question types (what kind of help students seek)
        label = INTENT_LABEL.get((intent or "").upper())
        if label:
            question_types[label] = question_types.get(label, 0) + 1

        # Count Struggles (DEBUG or REVIEW) — kept for back-compat
        if intent in ['REVIEW', 'DEBUG']:
            struggle_counts[topic] = struggle_counts.get(topic, 0) + 1

    # Sort both distributions high→low so the charts read as a ranking.
    topic_counts = dict(sorted(topic_counts.items(), key=lambda kv: -kv[1]))
    question_types = dict(sorted(question_types.items(), key=lambda kv: -kv[1]))

    return {
        "popular_topics": topic_counts,
        "question_types": question_types,
        "struggle_areas": struggle_counts,
        "total_interactions": len(rows)
    }
    
@app.get("/api/v1/analytics/teacher/detailed")
async def get_teacher_detailed_analytics(page: int = 1, page_size: int = 10, _t: dict = Depends(verify_teacher)):
    # AUTHZ: teacher-only. These endpoints expose whole-class data and admin
    # actions and previously had NO guard at all — reachable by anyone who
    # could reach the port.
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

        # Real 3-tier BKT means from user_knowledge (0–100; None if no graded evidence yet).
        uk = db.fetch_all(
            "SELECT p_mastery_quiz, p_mastery_micro, p_mastery_code FROM user_knowledge WHERE username = ?",
            (student,)
        )
        def _tier_avg(col):
            vals = [row[col] for row in uk if row[col] is not None]
            return round(100.0 * sum(vals) / len(vals), 1) if vals else None
        tier_quiz = _tier_avg("p_mastery_quiz")
        tier_micro = _tier_avg("p_mastery_micro")
        tier_code = _tier_avg("p_mastery_code")
        kt_concepts = len(uk)

        class_matrix.append({
            "hidden_username": student, 
            "display_id": anon_id,      
            "risk_level": risk,
            "risk_reason": risk_reason,
            "strongest_topic": strongest,
            "weakest_topic": weakest,
            "last_active": last_active_str,
            "mastery": mastery,
            "tier_quiz": tier_quiz,
            "tier_micro": tier_micro,
            "tier_code": tier_code,
            "kt_concepts": kt_concepts,
        })
        
    return {
        "items": class_matrix,
        "total": total_records,
        "page": page,
        "pages": (total_records + page_size - 1) // page_size if page_size > 0 else 1
    }

@app.get("/api/v1/analytics/teacher/risk_matrix")
async def get_risk_matrix(days: int = 14, _t: dict = Depends(verify_teacher)):
    # AUTHZ: teacher-only. These endpoints expose whole-class data and admin
    # actions and previously had NO guard at all — reachable by anyone who
    # could reach the port.
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

    # Cohort affect aggregate: frustration is a MODEL ESTIMATE, so it is reported
    # at the class level only — never per learner as fact. Denominator is learners
    # with any affect turns in the window; numerator is those reading elevated.
    frust_active = sum(1 for m in matrix if m["turns"] > 0)
    frust_elevated = sum(1 for m in matrix
                         if m["turns"] > 0 and (m["rage_events"] > 0 or m["avg_frustration"] > 0.1))
    summary = {
        "critical": sum(1 for m in matrix if m["risk_tier"] == "Critical"),
        "high": sum(1 for m in matrix if m["risk_tier"] == "High"),
        "watch": sum(1 for m in matrix if m["risk_tier"] == "Watch"),
        "frust_active": frust_active,
        "frust_elevated": frust_elevated,
        "frust_pct": round(100.0 * frust_elevated / frust_active, 0) if frust_active else None,
        "window_days": days,
    }
    return {"items": matrix, "summary": summary}


@app.get("/api/v1/analytics/teacher/triage")
async def get_teacher_triage(_t: dict = Depends(verify_teacher)):
    # AUTHZ: teacher-only. These endpoints expose whole-class data and admin
    # actions and previously had NO guard at all — reachable by anyone who
    # could reach the port.
    """
    Action Triage (landing view): one row per learner, flagged by a SINGLE named
    mechanism derived from the 3-tier BKT in user_knowledge — not a composite risk
    score. Each row carries its own one-line derivation so the instructor can act
    with low inference cost. Thresholds come from the model itself (bkt_model).
    """
    from app.core.bkt_model import (THETA_DECERTIFY, N_MIN, reconcile_certifications,
                                     _apply_decay, _parse_ts, _TS_COL, _LAM_COL)
    from collections import defaultdict

    # Correct any stale is_certified flags before we trust them: certification decays
    # with time but the stored flag is only fixed lazily during active practice, so an
    # idle certified learner reads certified while already below θ_dec. The sweep writes
    # corrections to the DB, so the decertified check below (and every other panel that
    # reads is_certified) sees truth, not a stale flag.
    reconcile_certifications()

    TIER_HI, TIER_LO, GAP = 0.70, 0.40, 0.35      # "defines but can't use"
    STALL_EV, STALL_P = 6, 0.40                    # wheel-spinning proxy

    students = sorted([r["username"] for r in
                       db.fetch_all("SELECT username FROM users WHERE role = 'student'")])
    if not students:
        return {"items": [], "counts": {}, "total_learners": 0}
    id_map = {u: f"Student_{i+1:02d}" for i, u in enumerate(students)}

    ph = ",".join("?" * len(students))
    uk = db.fetch_all(f"SELECT * FROM user_knowledge WHERE username IN ({ph})", tuple(students))
    by_user = defaultdict(list)
    for r in uk:
        by_user[r["username"]].append(r)

    SEV = {"decertified": 4, "tier_imbalance": 3, "stalled": 2, "thin_evidence": 1}
    # Teacher-language labels (Phase 2 vocabulary): no model jargon in the teaching views.
    LABEL = {"decertified": "Faded", "tier_imbalance": "Knows it, can't code it yet",
             "stalled": "Stuck", "thin_evidence": "Not enough practice"}

    def evaluate(r):
        """Return (mechanism, why, action, tiebreak) for the most salient flag on this
        learner-concept, or None. Ordered so the most urgent mechanism wins."""
        concept = r["concept"] or "a topic"
        # Decay-adjust every tier so Triage reads the SAME retained mastery the modal,
        # is_mastered(), and the forecast use — Triage was the one view showing the raw
        # as-of-last-practice posterior, which reads too high for an idle learner.
        q  = _apply_decay(r["p_mastery_quiz"]  or 0.0, "quiz",  _parse_ts(r["last_quiz_at"]),  lam=r["decay_quiz_lam"])
        mi = _apply_decay(r["p_mastery_micro"] or 0.0, "micro", _parse_ts(r["last_micro_at"]), lam=r["decay_micro_lam"])
        co = _apply_decay(r["p_mastery_code"]  or 0.0, "code",  _parse_ts(r["last_code_at"]),  lam=r["decay_code_lam"])
        nq  = r["n_evidence_quiz"]  or 0
        nmi = r["n_evidence_micro"] or 0
        nco = r["n_evidence_code"]  or 0
        cert = r["is_certified"]
        ever = r["ever_certified"]
        total_ev = nq + nmi + nco

        if ever and not cert:
            return ("decertified",
                    f"Was solid on {concept}, but it has faded from not practising.",
                    f"Give a quick refresher on {concept}", 1.0)
        if q >= TIER_HI and co <= TIER_LO and (q - co) >= GAP:
            return ("tier_imbalance",
                    f"Can explain {concept} ({q*100:.0f}%) but can't write it yet ({co*100:.0f}%).",
                    f"Assign a hands-on {concept} coding task", q - co)
        if total_ev >= STALL_EV and max(q, mi, co) < STALL_P:
            return ("stalled",
                    f"Stuck on {concept}: {total_ev} practice attempts, still not getting it.",
                    f"1:1 check-in on {concept}; try a different explanation", float(total_ev))
        if cert and min(nq, nmi, nco) <= N_MIN:
            thin_label = {"code": "writing it", "micro": "completing code", "quiz": "explaining it"}
            thin = "code" if nco <= N_MIN else ("micro" if nmi <= N_MIN else "quiz")
            return ("thin_evidence",
                    f"Marked solid on {concept}, but only {min(nq, nmi, nco)} practice attempts at {thin_label[thin]}.",
                    f"Add one more {concept} check to confirm", 0.0)
        return None

    items = []
    for user, recs in by_user.items():
        best = None
        for r in recs:
            ev = evaluate(r)
            if not ev:
                continue
            mech, why, action, tie = ev
            key = (SEV[mech], tie)
            if best is None or key > best["key"]:
                best = {"key": key, "mechanism": mech, "why": why, "action": action, "concept": r["concept"]}
        if best:
            items.append({
                "display_id": id_map.get(user, user),
                "hidden_username": user,
                "mechanism": best["mechanism"],
                "mechanism_label": LABEL[best["mechanism"]],
                "why": best["why"],
                "action": best["action"],
                "concept": best["concept"],
                "topic": _canon_topic(best["concept"]) or best["concept"],  # canonical, for clustering
                "severity": SEV[best["mechanism"]],
            })
    items.sort(key=lambda x: -x["severity"])
    counts = {m: sum(1 for i in items if i["mechanism"] == m) for m in SEV}
    return {"items": items, "counts": counts, "total_learners": len(students)}


@app.get("/api/v1/analytics/teacher/decert_forecast")
async def get_decert_forecast(horizon: int = 14, _t: dict = Depends(verify_teacher)):
    # AUTHZ: teacher-only. These endpoints expose whole-class data and admin
    # actions and previously had NO guard at all — reachable by anyone who
    # could reach the port.
    """
    Decertification Forecast: which certified learner-concepts will decay below
    THETA_DECERTIFY within the horizon, and in how many days — projected from the
    forgetting curve (Eq. 18) using each tier's stored decay rate λ and last-seen
    time. Deterministic, no new data. Prescriptive: an instructor can schedule a
    retention check BEFORE the lapse instead of catching it after (that's Triage's
    'Decertified'). Stale certs are reconciled first so the baseline is the truth.

    days-to-lapse solves value(T)=θ for the forgetting curve:
        p̃·e^(−λT) + p₀·(1−e^(−λT)) = θ  ⇒  T* = −ln((θ−p₀)/(p̃−p₀)) / λ
    then subtracts the time already elapsed. The soonest-crossing tier drives the
    learner-concept's lapse date (conjunctive certification: ANY tier < θ decertifies).
    """
    import math
    from app.core.bkt_model import (reconcile_certifications, THETA_DECERTIFY,
                                     DECAY_RATES, EVIDENCE_CONFIG, _parse_ts,
                                     _TS_COL, _LAM_COL)
    reconcile_certifications()

    students = sorted([r["username"] for r in
                       db.fetch_all("SELECT username FROM users WHERE role='student'")])
    id_map = {u: f"Student_{i+1:02d}" for i, u in enumerate(students)}

    rows = db.fetch_all(
        "SELECT username, concept, p_mastery_quiz, p_mastery_micro, p_mastery_code, "
        "last_quiz_at, last_micro_at, last_code_at, "
        "decay_quiz_lam, decay_micro_lam, decay_code_lam "
        "FROM user_knowledge WHERE is_certified=1")

    TIER_LABEL = {"quiz": "explaining it", "micro": "completing code", "code": "writing it"}
    now = datetime.now()
    items = []
    for r in rows:
        soonest = None  # (days_to_lapse, tier, current_tier_pct)
        for tier in ("quiz", "micro", "code"):
            p_raw = r[EVIDENCE_CONFIG[tier]["col"]]
            last = _parse_ts(r[_TS_COL[tier]])
            if p_raw is None or last is None:
                continue
            if last.tzinfo is not None:
                last = last.astimezone().replace(tzinfo=None)
            p0 = EVIDENCE_CONFIG[tier]["P_L0"]
            lam = r[_LAM_COL[tier]] or DECAY_RATES[tier]
            T0 = max(0.0, (now - last).total_seconds() / 86400.0)
            cur = max(p_raw * math.exp(-lam * T0) + p0 * (1 - math.exp(-lam * T0)), p0)
            if cur < THETA_DECERTIFY:               # already lapsed (guard; reconcile should prevent)
                d = 0.0
            else:
                ratio = (THETA_DECERTIFY - p0) / (p_raw - p0)
                if not (0.0 < ratio < 1.0):         # never crosses θ (shouldn't happen: p̃>θ>p₀)
                    continue
                d = max(0.0, (-math.log(ratio) / lam) - T0)
            if soonest is None or d < soonest[0]:
                soonest = (d, tier, round(cur * 100, 1))
        if soonest is None:
            continue
        d, tier, cur_pct = soonest
        items.append({
            "display_id": id_map.get(r["username"], r["username"]),
            "hidden_username": r["username"],
            "concept": r["concept"],
            "first_tier": tier,
            "first_tier_label": TIER_LABEL[tier],
            "days_to_lapse": round(d, 1),
            "tier_pct_now": cur_pct,
            "action": f"Quick refresher on {r['concept']} before they lose {TIER_LABEL[tier]}",
        })

    items.sort(key=lambda x: x["days_to_lapse"])
    n7 = sum(1 for i in items if i["days_to_lapse"] <= 7)
    n14 = sum(1 for i in items if i["days_to_lapse"] <= 14)
    within = [i for i in items if i["days_to_lapse"] <= horizon]
    return {"items": within, "n_7": n7, "n_14": n14,
            "total_certified": len(rows), "horizon": horizon}


# user_knowledge concepts are entity-extraction fragments ("variable", "variables",
# "Variables and Types", plus junk like "fork", "Bob", "what is 1+1?"). This maps them to
# the C curriculum topics; anything unmapped is dropped — the class views show the syllabus,
# not raw extractor output. (Replaceable later by Neo4j graph resolution.)
_CURRICULUM_CANON = {
    "variable": "Variables", "variables": "Variables", "variables and types": "Variables",
    "variable and types": "Variables", "variables in c": "Variables", "types": "Variables",
    "type": "Variables", "constant": "Variables", "constants": "Variables", "declare": "Variables",
    "control flow": "Control Flow", "conditional": "Control Flow", "conditionals": "Control Flow",
    "loop": "Control Flow", "loops": "Control Flow", "sequential execution": "Control Flow",
    "operator": "Operators", "operators": "Operators", "logical": "Operators",
    "logical operators": "Operators",
    "function": "Functions", "functions": "Functions",
    "array": "Arrays", "arrays": "Arrays",
    "string": "Strings", "strings": "Strings",
    "pointer": "Pointers", "pointers": "Pointers", "memory and pointers": "Pointers",
    "memory management": "Pointers", "memory allocation": "Pointers",
    "structure": "Structures", "structures": "Structures", "struct": "Structures",
    "structs": "Structures",
}
_CURRICULUM_TOPICS = ["Variables", "Operators", "Control Flow", "Functions",
                      "Arrays", "Strings", "Pointers", "Structures"]


def _canon_topic(name: str):
    """Map a raw concept string to its curriculum topic, or None if it isn't one."""
    return _CURRICULUM_CANON.get((name or "").strip().lower())


def _class_tier_matrix():
    """Per-student, per-curriculum-topic decayed tier values (max across merged fragments).

    Returns (per_user, active, id_map) where per_user[(topic, username)] = {quiz,micro,code}
    in 0–100, active = set of students with any curriculum activity, id_map = username→Student_NN.
    Shared by every CLASS/NEXT panel so they agree on canonicalization and decay.
    """
    from app.core.bkt_model import _apply_decay, _parse_ts, _TS_COL, _LAM_COL, EVIDENCE_CONFIG
    from collections import defaultdict

    students = sorted([r["username"] for r in
                       db.fetch_all("SELECT username FROM users WHERE role='student'")])
    id_map = {u: f"Student_{i+1:02d}" for i, u in enumerate(students)}

    rows = db.fetch_all(
        "SELECT username, concept, p_mastery_quiz, p_mastery_micro, p_mastery_code, "
        "last_quiz_at, last_micro_at, last_code_at, "
        "decay_quiz_lam, decay_micro_lam, decay_code_lam FROM user_knowledge")

    def decayed(r, tier):
        col = EVIDENCE_CONFIG[tier]["col"]
        p_raw = r[col] if r[col] is not None else EVIDENCE_CONFIG[tier]["P_L0"]
        return _apply_decay(p_raw, tier, _parse_ts(r[_TS_COL[tier]]), lam=r[_LAM_COL[tier]]) * 100.0

    per_user = defaultdict(lambda: {"quiz": 0.0, "micro": 0.0, "code": 0.0})
    active = set()
    for r in rows:
        canon = _canon_topic(r["concept"])
        if not canon:
            continue
        active.add(r["username"])
        cur = per_user[(canon, r["username"])]
        for tier in ("quiz", "micro", "code"):
            cur[tier] = max(cur[tier], decayed(r, tier))
    return per_user, active, id_map


@app.get("/api/v1/analytics/teacher/class_standing")
async def get_class_standing(_t: dict = Depends(verify_teacher)):
    # AUTHZ: teacher-only. These endpoints expose whole-class data and admin
    # actions and previously had NO guard at all — reachable by anyone who
    # could reach the port.
    """
    CLASS / "Where the class stands": one row per topic, split into the three things a
    learner can do — Can explain it / Can complete code / Can write it from scratch —
    as class averages over the students who have practised it (decay-adjusted). Plus a
    prescriptive status ("Needs a coding session", "Needs re-explaining", "Not started",
    "Solid") and, on expand, the students behind the row (weakest first). Built on
    user_knowledge only, so the 62% 'General' bucket in turn_log doesn't touch it.
    """
    from app.core.bkt_model import reconcile_certifications
    from collections import defaultdict
    reconcile_certifications()

    per_user, active, id_map = _class_tier_matrix()
    total_active = len(active)

    by_topic = defaultdict(list)   # canonical -> [ {username, explain, complete, write} ]
    for (canon, user), v in per_user.items():
        by_topic[canon].append({"username": user, "explain": v["quiz"],
                                 "complete": v["micro"], "write": v["code"]})

    def status_for(explain, write, practiced, frac):
        # Prescriptive, teacher language. Order of checks = priority (attention first).
        if practiced < 3 or frac < 0.15:
            return ("Not enough data", "#95a5a6", 4)
        if write >= 75:
            return ("Solid", "#27ae60", 5)
        if explain >= 60 and write < 40:
            return ("Needs a coding session", "#e67e22", 1)   # strong left, weak right
        if explain < 40:
            return ("Needs re-explaining", "#e74c3c", 0)        # weak across
        return ("In progress", "#2980b9", 2)

    topics = []
    for canon, studs in by_topic.items():
        n = len(studs)
        ex = sum(s["explain"] for s in studs) / n
        co = sum(s["complete"] for s in studs) / n
        wr = sum(s["write"] for s in studs) / n
        frac = n / total_active if total_active else 0.0
        label, color, rank = status_for(ex, wr, n, frac)
        out_studs = sorted(({
            "display_id": id_map.get(s["username"], s["username"]),
            "hidden_username": s["username"],
            "explain": round(s["explain"], 0), "complete": round(s["complete"], 0),
            "write": round(s["write"], 0),
            "weakest": round(min(s["explain"], s["complete"], s["write"]), 0),
        } for s in studs), key=lambda s: s["weakest"])
        topics.append({
            "concept": canon,
            "explain": round(ex, 0), "complete": round(co, 0), "write": round(wr, 0),
            "practiced": n, "total": total_active,
            "status": label, "status_color": color, "sort_rank": rank,
            "students": out_studs,
        })

    topics.sort(key=lambda t: (t["sort_rank"], t["write"]))
    return {"topics": topics, "class_size": total_active}


def _ensure_calendar_table():
    """Course calendar is instructor setup, created lazily so no schema migration is needed."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS course_calendar ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " entry_date TEXT NOT NULL,"          # ISO date the session/assignment happens
        " topics TEXT,"                        # JSON list of curriculum topics
        " activity_type TEXT,"                 # Lecture | Lab | Review | Exam | Assignment
        " assignment_due TEXT,"                # optional ISO date
        " note TEXT,"
        " created_at TEXT)")


class CalendarEntryRequest(BaseModel):
    entry_date: str
    topics: list[str] | None = None
    activity_type: str | None = None
    assignment_due: str | None = None
    note: str | None = None


@app.get("/api/v1/analytics/teacher/calendar")
async def get_course_calendar(_t: dict = Depends(verify_teacher)):
    # AUTHZ: teacher-only. These endpoints expose whole-class data and admin
    # actions and previously had NO guard at all — reachable by anyone who
    # could reach the port.
    """The course schedule: dated entries of what's taught/assigned. Time-anchors the
    other panels (a signal is only interpretable once it's aligned to instruction)."""
    import json
    _ensure_calendar_table()
    rows = db.fetch_all("SELECT * FROM course_calendar ORDER BY entry_date ASC, id ASC")
    out = []
    for r in rows:
        try:
            topics = json.loads(r["topics"]) if r["topics"] else []
        except Exception:
            topics = []
        out.append({
            "id": r["id"], "entry_date": r["entry_date"], "topics": topics,
            "activity_type": r["activity_type"], "assignment_due": r["assignment_due"],
            "note": r["note"],
        })
    return {"entries": out, "topics_available": _CURRICULUM_TOPICS}


@app.post("/api/v1/analytics/teacher/calendar")
async def add_course_calendar_entry(req: CalendarEntryRequest, _t: dict = Depends(verify_teacher)):
    # AUTHZ: teacher-only. These endpoints expose whole-class data and admin
    # actions and previously had NO guard at all — reachable by anyone who
    # could reach the port.
    import json
    from datetime import datetime as _dt
    _ensure_calendar_table()
    if not (req.entry_date or "").strip():
        raise HTTPException(status_code=400, detail="entry_date is required")
    cur = db.execute(
        "INSERT INTO course_calendar (entry_date, topics, activity_type, assignment_due, note, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (req.entry_date.strip(), json.dumps(req.topics or []), req.activity_type,
         req.assignment_due, req.note, _dt.now().isoformat()))
    return {"status": "added", "id": cur.lastrowid}


@app.delete("/api/v1/analytics/teacher/calendar/{entry_id}")
async def delete_course_calendar_entry(entry_id: int, _t: dict = Depends(verify_teacher)):
    # AUTHZ: teacher-only. These endpoints expose whole-class data and admin
    # actions and previously had NO guard at all — reachable by anyone who
    # could reach the port.
    _ensure_calendar_table()
    db.execute("DELETE FROM course_calendar WHERE id = ?", (entry_id,))
    return {"status": "deleted", "id": entry_id}


@app.get("/api/v1/analytics/teacher/readiness")
async def get_readiness(topic: str = None, _t: dict = Depends(verify_teacher)):
    # AUTHZ: teacher-only. These endpoints expose whole-class data and admin
    # actions and previously had NO guard at all — reachable by anyone who
    # could reach the port.
    """
    NEXT / "Ready for what's coming": pick the topic you plan to teach; this walks the
    Neo4j prerequisite DAG (REQUIRES_UNDERSTANDING_OF) and reports what share of the class
    already holds ALL prerequisites, plus the single biggest gap (which prereq, which
    tier). Uses only data you already have. "Holds a prerequisite" = all three tiers at or
    above the ready bar (conjunctive). Prereqs the graph names but we can't measure (not a
    curriculum topic with BKT data) are listed separately, not silently dropped.
    """
    from app.core.bkt_model import reconcile_certifications
    reconcile_certifications()
    per_user, active, id_map = _class_tier_matrix()
    total = len(active)

    if not topic:
        return {"topic": None, "topics_available": _CURRICULUM_TOPICS, "total": total}

    try:
        from app.core import prereq_headstart
        raw_prereqs = prereq_headstart._prerequisites_of(topic)
    except Exception as e:
        logger.warning(f"[readiness] prereq lookup failed for '{topic}': {e}")
        raw_prereqs = []

    # Canonicalize to measurable curriculum topics; keep the rest as "not tracked".
    measurable, untracked, seen = [], [], set()
    for p in raw_prereqs:
        cp = _canon_topic(p)
        if cp and cp != topic and cp not in seen:
            seen.add(cp); measurable.append(cp)
        elif not cp and p not in untracked:
            untracked.append(p)

    READY = 50.0
    TIER_LABEL = {"explain": "explaining it", "complete": "completing code", "write": "writing it"}
    TKEY = {"explain": "quiz", "complete": "micro", "write": "code"}

    holds_all = {u: True for u in active}
    prereq_info, gap = [], None
    for pt in measurable:
        tier_ready = {}
        for disp, key in TKEY.items():
            cnt = sum(1 for u in active
                      if (per_user.get((pt, u)) or {}).get(key, 0.0) >= READY)
            tier_ready[disp] = round(100 * cnt / total) if total else 0
        for u in active:
            v = per_user.get((pt, u))
            if not (v and v["quiz"] >= READY and v["micro"] >= READY and v["code"] >= READY):
                holds_all[u] = False
        weak = min(tier_ready, key=tier_ready.get)
        prereq_info.append({"name": pt, "tiers": tier_ready,
                            "weakest_tier": weak, "weakest_label": TIER_LABEL[weak],
                            "weakest_pct": tier_ready[weak]})
        if gap is None or tier_ready[weak] < gap["pct"]:
            gap = {"prereq": pt, "tier": weak, "tier_label": TIER_LABEL[weak], "pct": tier_ready[weak]}

    holders = sum(1 for u in active if holds_all[u]) if measurable else 0
    readiness_pct = round(100 * holders / total) if (total and measurable) else None

    return {
        "topic": topic,
        "topics_available": _CURRICULUM_TOPICS,
        "total": total,
        "prereqs": prereq_info,
        "untracked_prereqs": untracked,
        "readiness_pct": readiness_pct,
        "holders": holders,
        "gap": gap,
        "ready_bar": int(READY),
    }


# Curriculum topic → the reading (concept notes) and code demos already in resources/.
# Lets the Prep List suggest *real* material to assign per group, not a placeholder.
_TOPIC_RESOURCES = {
    "Variables":    {"reading": ["02_variables_datatypes.md", "03_input_output.md"],
                     "code": ["demo_hello_world.c", "demo_io.c"]},
    "Operators":    {"reading": ["04_operators.md"],
                     "code": ["demo_math.c"]},
    "Control Flow": {"reading": ["05_control_flow.md", "06_loops.md"],
                     "code": ["demo_conditions.c", "demo_loops.c"]},
    "Functions":    {"reading": ["09_functions.md"],
                     "code": ["demo_functions.c", "demo_recursion.c"]},
    "Arrays":       {"reading": ["07_arrays.md"],
                     "code": ["demo_arrays.c"]},
    "Strings":      {"reading": ["08_strings.md", "11_input_safety.md"],
                     "code": ["demo_strings.c"]},
    "Pointers":     {"reading": ["10_pointers_basic.md", "15_memory_allocation.md"],
                     "code": ["demo_pointers.c", "demo_memory_allocation.c"]},
    "Structures":   {"reading": ["13_structures.md"],
                     "code": ["demo_structures.c"]},
}


def _topic_materials(topic: str):
    """The assignable materials for a topic, tagged by kind, as a flat list."""
    r = _TOPIC_RESOURCES.get(topic, {})
    return ([{"file": f, "kind": "reading",
              "label": f.split("_", 1)[-1].rsplit(".", 1)[0].replace("_", " ").title()}
             for f in r.get("reading", [])] +
            [{"file": f, "kind": "code",
              "label": f.replace("demo_", "").rsplit(".", 1)[0].replace("_", " ").title() + " (code)"}
             for f in r.get("code", [])])


@app.get("/api/v1/analytics/teacher/prep_list")
async def get_prep_list(_t: dict = Depends(verify_teacher)):
    # AUTHZ: teacher-only. These endpoints expose whole-class data and admin
    # actions and previously had NO guard at all — reachable by anyone who
    # could reach the port.
    """
    NEXT / "What to prepare next": turns class standing into a to-do for the instructor.
    Per topic it emits ONE prep action (re-explain / coding session / first exposure /
    reinforce / on track), the *group of students it's for* (weakest first — this set is
    what you assign to), and the real reading/code from resources/ that fits. Ranked by how
    many students the prep would help, so prep time goes where it moves the most people.
    Reuses _class_tier_matrix() so it agrees with CLASS standing and NEXT readiness.
    """
    from app.core.bkt_model import reconcile_certifications
    from collections import defaultdict
    reconcile_certifications()

    per_user, active, id_map = _class_tier_matrix()
    total_active = len(active)
    WEAK = 50.0   # a learner is "weak" on a tier below this

    by_topic = defaultdict(dict)   # topic -> {username: {explain,complete,write}}
    for (canon, user), v in per_user.items():
        by_topic[canon][user] = {"explain": v["quiz"], "complete": v["micro"], "write": v["code"]}

    def student_row(user, v):
        return {"display_id": id_map.get(user, user), "hidden_username": user,
                "explain": round(v["explain"], 0), "complete": round(v["complete"], 0),
                "write": round(v["write"], 0),
                "weakest": round(min(v["explain"], v["complete"], v["write"]), 0)}

    ACTIONS = {
        "re_explain":     ("Prepare a re-explainer",      "#e74c3c", "reading"),
        "coding_session": ("Prepare a coding / lab session", "#e67e22", "code"),
        "first_exposure": ("Assign first exposure before you lecture it", "#8e44ad", "reading"),
        "reinforce":      ("Reinforce — targeted practice", "#2980b9", "reading"),
        "on_track":       ("On track — no prep needed",    "#27ae60", None),
    }

    rows = []
    for topic in _CURRICULUM_TOPICS:
        studs = by_topic.get(topic, {})
        n = len(studs)
        frac = n / total_active if total_active else 0.0
        materials = _topic_materials(topic)

        if n < 3 or frac < 0.15:
            # Barely touched — assign intro reading to those who haven't started yet.
            not_started = [u for u in active if u not in studs]
            group = [{"display_id": id_map.get(u, u), "hidden_username": u,
                      "explain": 0, "complete": 0, "write": 0, "weakest": 0}
                     for u in not_started]
            action = "first_exposure"
            why = (f"only {n} of {total_active} have practised it — get the class exposed "
                   f"before the lecture")
        else:
            ex = sum(s["explain"] for s in studs.values()) / n
            wr = sum(s["write"] for s in studs.values()) / n
            if wr >= 75:
                action = "on_track"; group = []
                why = f"{round(wr)}% can write it on average — leave it be"
            elif ex >= 60 and wr < 40:
                action = "coding_session"
                grp = [(u, v) for u, v in studs.items() if v["write"] < WEAK]
                group = [student_row(u, v) for u, v in grp]
                why = (f"{round(ex)}% can explain it but only {round(wr)}% can write it — "
                       f"{len(grp)} students need hands-on coding")
            elif ex < 40:
                action = "re_explain"
                grp = [(u, v) for u, v in studs.items() if v["explain"] < 60]
                group = [student_row(u, v) for u, v in grp]
                why = (f"only {round(ex)}% can explain it — {len(grp)} students need the "
                       f"concept retaught")
            else:
                action = "reinforce"
                grp = [(u, v) for u, v in studs.items()
                       if min(v["explain"], v["complete"], v["write"]) < WEAK]
                group = [student_row(u, v) for u, v in grp]
                why = f"in progress — {len(grp)} students still weak on at least one level"

        group.sort(key=lambda s: s["weakest"])
        label, color, prefer = ACTIONS[action]
        # Order materials so the recommended kind for this action comes first.
        mats = sorted(materials, key=lambda m: (m["kind"] != prefer, m["kind"]))
        rows.append({
            "topic": topic, "action": action, "action_label": label, "action_color": color,
            "why": why, "group_size": len(group), "group": group,
            "practiced": n, "materials": mats,
        })

    # Prep-needed first, biggest group first; on-track topics sink to the bottom.
    order = {"re_explain": 0, "coding_session": 1, "first_exposure": 2, "reinforce": 3, "on_track": 4}
    rows.sort(key=lambda r: (order[r["action"]], -r["group_size"]))
    return {"rows": rows, "class_size": total_active}


class GroupAssignRequest(BaseModel):
    usernames: list[str]
    topic: str | None = None
    action: str = "assign_material"     # assign_material | assign_task | force_review
    material: str | None = None         # resource filename, if assigning reading/code
    material_kind: str | None = None    # reading | code
    note: str | None = None
    by: str | None = None


@app.post("/api/v1/analytics/teacher/assign_group")
async def assign_to_group(req: GroupAssignRequest, _t: dict = Depends(verify_teacher)):
    # AUTHZ: teacher-only. These endpoints expose whole-class data and admin
    # actions and previously had NO guard at all — reachable by anyone who
    # could reach the port.
    """
    Assign the same material/task to a whole group at once (a Prep List row's students).
    Records one append-only event per learner in event_log, capturing which material and
    which topic — so a group assignment is a real artifact, not just a display. Delivery to
    the student's tutor session is layered on top of this record later, never instead of it.
    """
    ALLOWED = {"assign_material", "assign_task", "force_review"}
    if req.action not in ALLOWED:
        raise HTTPException(status_code=400, detail=f"Unknown action '{req.action}'")
    if not req.usernames:
        raise HTTPException(status_code=400, detail="No students in the group")
    from app.core import telemetry
    teacher = req.by or "instructor"
    ok = 0
    for u in req.usernames:
        try:
            # 1) Deliver to the student: shows in their "Active Challenges" panel.
            if req.action == "assign_material" and req.material:
                assignment_manager.create_material_assignment(
                    teacher, u, req.topic, req.material, req.material_kind, note=req.note)
            # 2) Append-only record for dashboard-actionability analysis.
            telemetry.log_event(u, "instructor_action", {
                "action": req.action, "topic": req.topic,
                "material": req.material, "material_kind": req.material_kind,
                "group_size": len(req.usernames), "note": req.note, "by": req.by,
            })
            ok += 1
        except Exception as e:
            logger.warning(f"[assign_group] failed for {u}: {e}")
    return {"status": "recorded", "assigned": ok, "of": len(req.usernames),
            "topic": req.topic, "material": req.material}


# Where uploaded assignment/handout files live (kept apart from curriculum resources).
_ASSIGNMENT_DIR = config.PROJECT_ROOT / "resources" / "assignments"
_MATERIAL_DIRS = {
    "reading": config.PROJECT_ROOT / "resources" / "concepts",
    "code": config.PROJECT_ROOT / "resources" / "code",
    "file": _ASSIGNMENT_DIR,
}
_UPLOAD_EXTS = {".pdf", ".md", ".txt", ".docx", ".pptx", ".c", ".h", ".png", ".jpg", ".jpeg", ".zip"}


@app.post("/api/v1/analytics/teacher/upload_material")
async def upload_teacher_material(file: UploadFile = File(...), _t: dict = Depends(verify_teacher)):
    # AUTHZ: teacher-only. These endpoints expose whole-class data and admin
    # actions and previously had NO guard at all — reachable by anyone who
    # could reach the port.
    """Teacher uploads their own handout/assignment file to attach to a group assignment.
    Saved under resources/assignments/ (separate from the ingested curriculum resources)."""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in _UPLOAD_EXTS:
        raise HTTPException(status_code=400,
                            detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(_UPLOAD_EXTS))}")
    os.makedirs(_ASSIGNMENT_DIR, exist_ok=True)
    safe_name = os.path.basename(file.filename)
    dest = _ASSIGNMENT_DIR / safe_name
    try:
        with open(dest, "wb") as buf:
            shutil.copyfileobj(file.file, buf)
    except Exception as e:
        logger.error(f"[upload_material] save failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to save file")
    return {"status": "success", "filename": safe_name, "material_kind": "file"}


@app.get("/api/v1/materials/download")
async def download_material(kind: str, name: str):
    """Serve an assignable material (curriculum reading/code or an uploaded handout) to a
    student. Path-traversal-safe: kind picks the directory, name is reduced to a basename."""
    base = _MATERIAL_DIRS.get(kind)
    if base is None:
        raise HTTPException(status_code=400, detail="Unknown material kind")
    safe_name = os.path.basename(name or "")
    path = base / safe_name
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Material not found")
    return FileResponse(str(path), filename=safe_name)


class MaterialDoneRequest(BaseModel):
    assignment_id: str


@app.post("/api/v1/assignments/material_done")
async def material_done(req: MaterialDoneRequest):
    """Student marks an assigned material as read/done (so it stops nagging)."""
    assignment_manager.mark_material_done(req.assignment_id)
    return {"status": "success"}


@app.get("/api/v1/analytics/teacher/assignment_status")
async def get_assignment_status(_t: dict = Depends(verify_teacher)):
    # AUTHZ: teacher-only. These endpoints expose whole-class data and admin
    # actions and previously had NO guard at all — reachable by anyone who
    # could reach the port.
    """
    NEXT / "Did assignments land?": the follow-through half of the Prep List. Each material
    assignment sent (grouped by topic + material + day) with how many of the assigned
    students have opened / marked it done, and who hasn't yet. Closes the loop the Prep List
    opens — turns assign-and-forget into assign-and-check. Pure read on the assignments table.
    """
    from collections import defaultdict
    assignment_manager._ensure_columns()
    rows = db.fetch_all(
        "SELECT student_id, status, material, material_kind, topic, timestamp "
        "FROM assignments WHERE kind='material'")
    students = sorted([r["username"] for r in
                       db.fetch_all("SELECT username FROM users WHERE role='student'")])
    id_map = {u: f"Student_{i+1:02d}" for i, u in enumerate(students)}

    groups = defaultdict(lambda: {"total": 0, "done": 0, "not_done": [], "last": ""})
    meta = {}
    for r in rows:
        day = (r["timestamp"] or "")[:10]
        key = (r["topic"], r["material"], r["material_kind"], day)
        meta[key] = {"topic": r["topic"], "material": r["material"],
                     "material_kind": r["material_kind"], "date": day}
        g = groups[key]
        g["total"] += 1
        if r["status"] == "DONE":
            g["done"] += 1
        else:
            g["not_done"].append(id_map.get(r["student_id"], r["student_id"]))
        if (r["timestamp"] or "") > g["last"]:
            g["last"] = r["timestamp"] or ""

    out = []
    for key, g in groups.items():
        m = meta[key]
        g["not_done"].sort()
        out.append({
            "topic": m["topic"], "material": m["material"], "material_kind": m["material_kind"],
            "date": m["date"], "total": g["total"], "done": g["done"],
            "pending": g["total"] - g["done"], "not_done": g["not_done"],
        })
    out.sort(key=lambda x: (-x["pending"], x["topic"] or ""))
    return {"assignments": out}


@app.get("/api/v1/analytics/teacher/model_health")
async def get_model_health(_t: dict = Depends(verify_teacher)):
    # AUTHZ: teacher-only. These endpoints expose whole-class data and admin
    # actions and previously had NO guard at all — reachable by anyone who
    # could reach the port.
    """
    Model Health: how well the mastery model's pre-update predictions have matched the
    observed outcomes recorded in prediction_log. Surfaces the Brier score (raw BKT vs
    the effective, adaptation-adjusted prediction the system acted on) and a reliability
    curve, so an instructor can see the model's track record before trusting a
    certification decision. Honest-by-design: it shows the model grading itself.
    """
    rows = [r for r in db.fetch_all(
        "SELECT username, p_bkt_pred, p_eff_pred, is_correct FROM prediction_log"
    ) if r["is_correct"] is not None]
    n = len(rows)
    if n == 0:
        return {"n": 0, "n_users": 0, "brier_eff": None, "brier_bkt": None,
                "bins_eff": [], "bins_bkt": []}

    def brier(pairs):
        return round(sum((p - y) ** 2 for p, y in pairs) / len(pairs), 4) if pairs else None

    def reliability(pairs, nb=5):
        buckets = [[] for _ in range(nb)]
        for p, y in pairs:
            idx = min(nb - 1, max(0, int(p * nb)))
            buckets[idx].append((p, y))
        out = []
        for b in buckets:
            if b:
                out.append({
                    "p": round(sum(p for p, _ in b) / len(b), 3),
                    "acc": round(sum(y for _, y in b) / len(b), 3),
                    "n": len(b),
                })
        return out

    eff = [(r["p_eff_pred"], r["is_correct"]) for r in rows if r["p_eff_pred"] is not None]
    bkt = [(r["p_bkt_pred"], r["is_correct"]) for r in rows if r["p_bkt_pred"] is not None]
    n_users = db.fetch_one("SELECT COUNT(DISTINCT username) c FROM prediction_log")["c"]

    # --- Single instructor-facing reliability value (0–100) from the effective preds. ---
    # Expected Calibration Error: bin-size-weighted gap between predicted and observed;
    # reliability = 100·(1 − ECE). One honest number an instructor can read without stats.
    def ece(pairs, nb=10):
        if not pairs:
            return None
        buckets = [[] for _ in range(nb)]
        for p, y in pairs:
            buckets[min(nb - 1, max(0, int(p * nb)))].append((p, y))
        e = 0.0
        for b in buckets:
            if b:
                pm = sum(p for p, _ in b) / len(b)
                am = sum(y for _, y in b) / len(b)
                e += (len(b) / len(pairs)) * abs(pm - am)
        return e

    e_eff = ece(eff)
    rel_score = round(100 * (1 - e_eff)) if e_eff is not None else None
    avg_p = sum(p for p, _ in eff) / len(eff) if eff else None
    avg_y = sum(y for _, y in eff) / len(eff) if eff else None
    if avg_p is None:
        rel_dir = None
    elif avg_p < avg_y - 0.05:
        rel_dir = "under"    # the model predicts lower than students actually perform
    elif avg_p > avg_y + 0.05:
        rel_dir = "over"     # the model predicts higher than students perform
    else:
        rel_dir = "well"
    if rel_score is None:
        rel_label = None
    elif rel_score >= 90:
        rel_label = "Reliable"
    elif rel_score >= 75:
        rel_label = "Mostly reliable"
    elif rel_score >= 60:
        rel_label = "Use with caution"
    else:
        rel_label = "Unreliable"

    return {
        "n": n,
        "n_users": n_users,
        "brier_eff": brier(eff),
        "brier_bkt": brier(bkt),
        "bins_eff": reliability(eff),
        "bins_bkt": reliability(bkt),
        "reliability_score": rel_score,
        "reliability_label": rel_label,
        "reliability_dir": rel_dir,
    }


@app.get("/api/v1/analytics/teacher/security_ledger")
async def get_security_ledger(_t: dict = Depends(verify_teacher)):
    # AUTHZ: teacher-only. These endpoints expose whole-class data and admin
    # actions and previously had NO guard at all — reachable by anyone who
    # could reach the port.
    """
    Security Ledger (aggregate-first): cohort counts of gated turns, with the EVASION
    case (Sentinel security block) visually separated from CURRICULUM / pedagogical
    redirects (the tutor guiding instead of handing over answers). No learner is named
    by default — leading with "who tried to jailbreak" is itself a harm.
    """
    total_turns = db.fetch_one("SELECT COUNT(*) n FROM turn_log")["n"]
    rows = db.fetch_all(
        "SELECT block_reason, COUNT(*) n, COUNT(DISTINCT username) u "
        "FROM turn_log WHERE was_blocked = 1 AND block_reason IS NOT NULL "
        "GROUP BY block_reason"
    )
    # The Sentinel blocks for ten distinct reasons, and they are NOT all security.
    # A student stopped because they are overwhelmed needs a very different response
    # from one probing for a jailbreak, so they are counted separately here.
    SECURITY = "SECURITY_RISK"
    SECURITY_REASONS = {
        "security_risk", "goal-bounded security", "harmful code", "cross-user privacy",
        "ai semantic judge", "trajectory risk", "tag bypass attempt", "security policy",
    }
    COGNITIVE_REASONS = {
        # Wellbeing gates: fired by rage, escalating frustration, or the
        # disengagement-prediction trigger — not by anything adversarial.
        "cognitive overload",
    }
    FOCUS_REASONS = {"off-topic warning", "attention hijacking"}
    INTEGRITY_REASONS = {"academic integrity"}
    POLICY_REASONS = {"teacher lock"}

    def _bucket(reason: str) -> str:
        r = (reason or "").strip().lower()
        if r in SECURITY_REASONS:  return "security"
        if r in COGNITIVE_REASONS: return "cognitive"
        if r in FOCUS_REASONS:     return "focus"
        if r in INTEGRITY_REASONS: return "integrity"
        if r in POLICY_REASONS:    return "policy"
        return "pedagogical"

    evasion = {"count": 0, "learners": 0}
    pedagogical, cognitive, focus, integrity, policy = [], [], [], [], []
    _BUCKETS = {"cognitive": cognitive, "focus": focus,
                "integrity": integrity, "policy": policy,
                "pedagogical": pedagogical}
    for r in rows:
        raw = r["block_reason"] or ""
        bucket = _bucket(raw)
        entry = {"reason": raw.title().replace("_", " "),
                 "count": r["n"], "learners": r["u"]}
        if bucket == "security":
            evasion = {"count": evasion["count"] + r["n"],
                       "learners": max(evasion["learners"], r["u"])}
        else:
            _BUCKETS[bucket].append(entry)
    for lst in (pedagogical, cognitive, focus, integrity, policy):
        lst.sort(key=lambda x: -x["count"])

    risk = db.fetch_one(
        "SELECT ROUND(AVG(traj_risk), 3) avg, ROUND(MAX(traj_risk), 3) mx "
        "FROM turn_log WHERE block_reason = ?", (SECURITY,)
    )
    evasion["avg_risk"] = risk["avg"] if risk else None
    evasion["max_risk"] = risk["mx"] if risk else None
    ped_total = sum(p["count"] for p in pedagogical)
    cog_total = sum(p["count"] for p in cognitive)
    focus_total = sum(p["count"] for p in focus)
    integrity_total = sum(p["count"] for p in integrity)
    policy_total = sum(p["count"] for p in policy)

    # --- What are students actually trying? Categorize the blocked queries by technique,
    # with a few representative (truncated, UNATTRIBUTED) examples so the instructor sees
    # the *kind* of attempt — the academic-integrity signal, not just a total count.
    import re as _re
    CATS = [
        ("Posing as staff / false authority",
         ["i am the teacher", "i'm the teacher", "i am teacher", "as the teacher", "teacher, give",
          "i am the instructor", "i am the professor", "professor said", "i am the admin",
          "i am an administrator", "give me the key", "give answer key",
          "i'm a ta", "i am a ta", "actually a ta", "as a ta", "i have permission",
          "i'm allowed", "i am allowed", "it's fine, i have"]),
        ("Prompt injection / override",
         ["ignore all", "ignore rules", "ignore the rules", "ignore previous", "ignore your",
          "disregard", "forget your", "forget the", "previous instruction", "prior instruction",
          "system prompt", "override", "pretend", "act as", "jailbreak", "developer mode",
          "in that mode", "print the exam", "print exam", "that got blocked", "just describe in words"]),
        ("Malicious / system-abuse code",
         ["keylogger", "keylog", "virus", "malware", "reverse shell", "ransomware", "ddos",
          "dos attack", "backdoor", "trojan", "spyware", "botnet", "rootkit", "worm",
          "fork bomb", "fork(", "self-replicat", "shellcode", "exploit", "sql injection",
          "<script>", "alert(", "phishing", "brute-force", "brute force", "steal", "bomb", "hack",
          "freezes the system", "freeze the system", "consume all", "keeps calling",
          "keeps executing", "keeps running", "wipe", "leak the file", "send packets",
          "packets over", "run commands", "quietly run"]),
        ("Trying to extract answers",
         ["exam answer", "answer key", "give me the answer", "the answer to", "the solution",
          "exam solution", "do my homework", "do my assignment", "just give me the code",
          "full solution", "complete code", "write it for me", "cheat"]),
        ("Probing other students' data",
         ["another student", "other student", "other students", "someone else", "classmate",
          "other people's", "all students", "students'", "everyone's", "the whole class",
          "list all", "'s progress", "'s answers", "'s mastery", "'s goal", "mastery scores"]),
    ]

    def _categorize(t):
        tl = t.lower()
        for name, kws in CATS:
            if any(k in tl for k in kws):
                return name
        return "Other attempts"

    sec_texts = db.fetch_all(
        "SELECT query_text FROM turn_log WHERE block_reason = ? AND query_text IS NOT NULL",
        (SECURITY,))
    tech = {}
    for r in sec_texts:
        t = (r["query_text"] or "").strip()
        if not t:
            continue
        cat = _categorize(t)
        d = tech.setdefault(cat, {"count": 0, "examples": []})
        d["count"] += 1
        if len(d["examples"]) < 3:
            snip = _re.sub(r"\s+", " ", t)[:100]
            if snip not in d["examples"]:
                d["examples"].append(snip)
    techniques = [{"category": k, "count": v["count"], "examples": v["examples"]}
                  for k, v in tech.items()]
    techniques.sort(key=lambda x: -x["count"])

    return {
        "total_turns": total_turns,
        "total_gated": (evasion["count"] + ped_total + cog_total
                        + focus_total + integrity_total + policy_total),
        "evasion": evasion,
        "pedagogical": pedagogical,
        "ped_total": ped_total,
        # Non-security gates, bucketed so the UI can distinguish a student being
        # PROTECTED (overwhelmed, off-track) from one being STOPPED (evasion).
        "cognitive": cognitive, "cog_total": cog_total,
        "focus": focus, "focus_total": focus_total,
        "integrity": integrity, "integrity_total": integrity_total,
        "policy": policy, "policy_total": policy_total,
        "techniques": techniques,
    }


@app.get("/api/v1/analytics/student_detail/{username}")
async def get_student_detail_view(username: str, _caller: dict = Depends(auth.get_current_user)):
    """
    Fetches deep-dive data: real 3-tier BKT mastery + full session list + last 2 transcripts.

    The mastery profile is the REAL model (user_knowledge, decay applied) — not the
    _calculate_mastery intent heuristic the modal used to render. This closes the
    last "modal disagrees with the cohort panels" gap: a learner shown decertified in
    Triage now reads decertified here too.
    """
    # AUTHZ: the path parameter selects WHICH record; the signed session decides
    # whether this caller may see it. Without this, any username in the URL
    # returned that student's data to anyone who asked.
    auth.require_self_or_teacher(username, _caller)
    from app.core.bkt_model import (reconcile_certifications, _apply_decay, _parse_ts,
                                     _TS_COL, _LAM_COL, _N_COL, EVIDENCE_CONFIG, N_MIN)

    # Correct any stale certs for THIS learner first, so the modal's ✓/lapsed badges
    # match Triage rather than a flag that decay already invalidated.
    try:
        reconcile_certifications(username)
    except Exception as e:
        logger.warning(f"[modal] reconcile_certifications failed for {username}: {e}")

    # --- Real per-concept mastery (3-tier BKT, decay applied) ---
    # Reported per TIER with evidence counts, NOT as a single compensatory composite:
    # a learner at quiz 91% / code 1% (n=0) is not "59% mastered" — the applied tier is
    # UNMEASURED, and only the min tier + its evidence count makes that visible. The UI
    # colors by the weakest tier (conjunctive), so no tier compensates for another.
    mastery = []
    uk = db.fetch_all(
        "SELECT concept, p_mastery_quiz, p_mastery_micro, p_mastery_code, "
        "n_evidence_quiz, n_evidence_micro, n_evidence_code, "
        "last_quiz_at, last_micro_at, last_code_at, "
        "decay_quiz_lam, decay_micro_lam, decay_code_lam, "
        "is_certified, ever_certified "
        "FROM user_knowledge WHERE username=?", (username,))
    for r in uk:
        concept = (r["concept"] or "").strip()
        if concept.lower() in ("", "general", "unknown", "code submission"):
            continue
        tiers, ns, comp = {}, {}, 0.0
        for tier in ("quiz", "micro", "code"):
            col = EVIDENCE_CONFIG[tier]["col"]
            p_raw = r[col] if r[col] is not None else EVIDENCE_CONFIG[tier]["P_L0"]
            p = _apply_decay(p_raw, tier, _parse_ts(r[_TS_COL[tier]]), lam=r[_LAM_COL[tier]])
            tiers[tier] = round(p * 100, 1)
            ns[tier] = r[_N_COL[tier]] or 0
            comp += p * EVIDENCE_CONFIG[tier]["ceiling"]
        min_tier = min(tiers, key=tiers.get)          # conjunctive: weakest tier governs
        mastery.append({
            "concept": concept,
            "tiers": tiers,                            # {quiz,micro,code} decayed %
            "n": ns,                                   # evidence count per tier (N^(k))
            "min_tier": min_tier,
            "min_pct": tiers[min_tier],                # governs color; NOT the composite
            "composite_pct": round(min(comp / 0.95, 1.0) * 100, 1),  # kept for reference only
            "n_min": N_MIN,
            "is_certified": bool(r["is_certified"]),
            "ever_certified": bool(r["ever_certified"]),
        })
    # Sort by the weakest tier ascending — the least-mastered surfaces first (needs attention).
    mastery.sort(key=lambda m: m["min_pct"])

    # --- Learner-level flag: ONE severity function, derived from the SAME conjunctive
    # tier data shown below — so the modal header can't say "OK" while Triage flags the
    # learner (the previous badge came from the frustration-based risk tier, a second
    # severity function). Mirrors Triage's mechanism priority (decertified > imbalance
    # > low > thin evidence). ---
    TIER_HI, TIER_LO, GAP = 70.0, 40.0, 35.0
    flag = {"label": "On track", "color": "#27ae60", "mechanism": "on_track", "concept": None, "severity": 0}
    for m in mastery:
        t, n = m["tiers"], m["n"]
        if m["ever_certified"] and not m["is_certified"]:
            cand = (4, "Faded", "#c0392b", "decertified")
        elif t["quiz"] >= TIER_HI and t["code"] <= TIER_LO and (t["quiz"] - t["code"]) >= GAP:
            cand = (3, "Knows it, can't code it yet", "#e67e22", "tier_imbalance")
        elif m["min_pct"] < 35.0:
            cand = (2, "Struggling", "#e74c3c", "low")
        elif m["is_certified"] and min(n.values()) <= m["n_min"]:
            cand = (1, "Not enough practice", "#2980b9", "thin_evidence")
        else:
            continue
        if cand[0] > flag["severity"]:
            flag = {"severity": cand[0], "label": cand[1], "color": cand[2],
                    "mechanism": cand[3], "concept": m["concept"]}

    # --- Per-session summary tags from turn_log (topic, intent mix, struggle) ---
    # Replaces raw truncated query strings with a readable, meta-level tag per session:
    # dominant topic, what the learner was doing (intent distribution), and whether they
    # struggled (a gate block, a strike streak, or an elevated frustration reading).
    from collections import OrderedDict, Counter, defaultdict
    turns = db.fetch_all(
        "SELECT session_id, topic, intent, was_blocked, n_strike, delta_f "
        "FROM turn_log WHERE username=?", (username,))
    tags_by_session = {}
    grp = defaultdict(lambda: {"topics": Counter(), "intents": Counter(), "turns": 0,
                               "blocked": 0, "max_strike": 0, "max_df": 0.0})
    for t in turns:
        g = grp[t["session_id"]]
        g["turns"] += 1
        if t["topic"] and t["topic"].lower() not in ("general", "unknown", ""):
            g["topics"][t["topic"]] += 1
        if t["intent"]:
            g["intents"][t["intent"]] += 1
        g["blocked"] += 1 if t["was_blocked"] else 0
        g["max_strike"] = max(g["max_strike"], t["n_strike"] or 0)
        g["max_df"] = max(g["max_df"], t["delta_f"] or 0.0)
    for sid, g in grp.items():
        struggled = g["blocked"] > 0 or g["max_strike"] >= 2 or g["max_df"] > 0.1
        tags_by_session[sid] = {
            "topic": (g["topics"].most_common(1)[0][0] if g["topics"] else None),
            "intents": dict(g["intents"].most_common(3)),
            "turns": g["turns"],
            "blocked": g["blocked"],
            "struggled": struggled,
        }

    # 1. Get All Sessions, then DEDUPE identical (title, day) rows into one with a count.
    sessions_list = history_manager.get_user_sessions_list(username)
    sessions_list.sort(key=lambda x: x['date'], reverse=True)   # newest first
    grouped = OrderedDict()
    for s in sessions_list:
        key = ((s.get("title") or "").strip().lower(), (s.get("date") or "")[:10])
        if key in grouped:
            grouped[key]["count"] += 1                          # collapse the duplicate
        else:
            grouped[key] = {**s, "count": 1, "tags": tags_by_session.get(s["id"])}
    deduped_sessions = list(grouped.values())

    # 2. Get Details for ONLY the last 2 DISTINCT sessions (Heavy).
    recent_chats_details = []
    for sess in deduped_sessions[:2]:
        details = history_manager.get_session_details(username, sess['id'])
        if details:
            recent_chats_details.append({
                "id": sess['id'],
                "title": sess['title'],
                "date": sess['date'],
                "messages": details.get('messages', [])
            })

    return {
        "mastery": mastery,                        # real 3-tier BKT per concept
        "flag": flag,                              # single conjunctive learner status
        "all_sessions_summary": deduped_sessions,  # {id, title, date, count} — deduped
        "recent_chats": recent_chats_details       # Full text
    }

@app.get("/api/v1/analytics/student_trajectory/{username}")
async def get_student_trajectory(username: str, _caller: dict = Depends(auth.get_current_user)):
    """
    Per-concept mastery trajectory for one learner, reconstructed from bkt_history.
    Three tier series (quiz/micro/code) over time + certification/decertification event
    markers — so a snapshot's "50%" can be told apart from a learner climbing vs one who
    has lapsed. Pure read of the existing telemetry stream; no new logging.
    """
    # AUTHZ: the path parameter selects WHICH record; the signed session decides
    # whether this caller may see it. Without this, any username in the URL
    # returned that student's data to anyone who asked.
    auth.require_self_or_teacher(username, _caller)
    rows = db.fetch_all(
        "SELECT concept, tier, p_tilde, n_evidence, is_certified, ts_utc "
        "FROM bkt_history WHERE username=? ORDER BY ts_utc ASC", (username,))

    from collections import defaultdict
    by_concept = defaultdict(lambda: {"quiz": [], "micro": [], "code": [], "events": [], "_cert": 0})
    for r in rows:
        c = r["concept"]
        if not c:
            continue
        tier = r["tier"]
        if tier in ("quiz", "micro", "code") and r["p_tilde"] is not None:
            by_concept[c][tier].append({"t": r["ts_utc"], "p": round(r["p_tilde"] * 100, 1)})
        # Certification transition (any tier's row carries the current flag).
        cur = int(r["is_certified"] or 0)
        prev = by_concept[c]["_cert"]
        if cur != prev:
            by_concept[c]["events"].append(
                {"t": r["ts_utc"], "type": "certified" if cur > prev else "decertified"})
            by_concept[c]["_cert"] = cur

    trajectory = []
    for c, d in by_concept.items():
        pts = len(d["quiz"]) + len(d["micro"]) + len(d["code"])
        if pts < 2:                       # a single point isn't a trajectory
            continue
        trajectory.append({
            "concept": c, "points": pts,
            "quiz": d["quiz"], "micro": d["micro"], "code": d["code"],
            "events": d["events"],
        })
    trajectory.sort(key=lambda x: -x["points"])   # richest series first
    return {"trajectory": trajectory}


class InstructorActionRequest(BaseModel):
    target_username: str
    action: str                 # assign_task | unlock_prereq | force_review
    concept: str | None = None
    flag_state: str | None = None   # learner's flag AT PRESS TIME (for actionability analysis)
    note: str | None = None
    by: str | None = None       # instructor username, if the client knows it


@app.post("/api/v1/analytics/teacher/log_action")
async def log_instructor_action(req: InstructorActionRequest, _t: dict = Depends(verify_teacher)):
    # AUTHZ: teacher-only. These endpoints expose whole-class data and admin
    # actions and previously had NO guard at all — reachable by anyone who
    # could reach the port.
    """
    Append-only record of an instructor acting on a learner from the dashboard.
    Logging is the point (the agent flagged this as irreversible-if-missed before
    deployment): every press is written to event_log so dashboard actionability can be
    measured numerically later, not by a post-hoc Likert. Writes the intent; any real
    side-effect (unlock/schedule) is layered on top of this record, never instead of it.
    """
    ALLOWED = {"assign_task", "unlock_prereq", "force_review"}
    if req.action not in ALLOWED:
        raise HTTPException(status_code=400, detail=f"Unknown action '{req.action}'")
    try:
        from app.core import telemetry
        telemetry.log_event(req.target_username, "instructor_action", {
            "action": req.action,
            "concept": req.concept,
            "flag_state": req.flag_state,   # what the flag said when the button was pressed
            "note": req.note,
            "by": req.by,
        })
    except Exception as e:
        logger.warning(f"[instructor_action] log failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to record action")
    return {"status": "recorded", "action": req.action, "target": req.target_username}


@app.get("/api/v1/history/sessions")
async def get_sessions(username: str,
                       _caller: dict = Depends(auth.get_current_user)):
    """Get list of past conversations for sidebar."""
    # AUTHZ: `username` arrives as a QUERY parameter here, not a path parameter.
    # That is the only reason these three routes survived the {username} audit —
    # ?username=someone-else returned their whole conversation history.
    auth.require_self_or_teacher(username, _caller)
    return history_manager.get_user_sessions_list(username)

# 1. GET Endpoint (For Loading Chat)
@app.get("/api/v1/history/session/{session_id}")
async def get_session_chat(session_id: str, username: str,
                           _caller: dict = Depends(auth.get_current_user)):
    """Get full chat log for a specific session."""
    auth.require_self_or_teacher(username, _caller)
    session = history_manager.get_session_details(username, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session

# 2. DELETE Endpoint (For Deleting Chat)
@app.delete("/api/v1/history/session/{session_id}")
async def delete_session(session_id: str, username: str,
                         _caller: dict = Depends(auth.get_current_user)):
    """Soft deletes a session."""
    auth.require_self_or_teacher(username, _caller)
    success = history_manager.delete_session(username, session_id)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "success"}


@app.post("/api/v1/user/goal")
async def set_user_goal(req: GoalRequest, _caller: dict = Depends(auth.get_current_user)):
    # AUTHZ: identity comes from the session, not the body. `req.username`
    # is client-controlled, and this endpoint WRITES to that account — so a
    # student could post as anyone by editing one JSON field.
    req.username = _caller["username"]

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
async def reset_user_knowledge(req: ResetKnowledgeRequest, _caller: dict = Depends(auth.get_current_user)):
    # AUTHZ: identity comes from the session, not the body. `req.username`
    # is client-controlled, and this endpoint WRITES to that account — so a
    # student could post as anyone by editing one JSON field.
    req.username = _caller["username"]

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
async def get_mastery_detail(username: str, concept: str, _caller: dict = Depends(auth.get_current_user)):
    """Return per-tier mastery breakdown with BKT, self-assessment, and effective scores."""
    # AUTHZ: the path parameter selects WHICH record; the signed session decides
    # whether this caller may see it. Without this, any username in the URL
    # returned that student's data to anyone who asked.
    auth.require_self_or_teacher(username, _caller)
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
async def submit_self_assessment(req: SelfAssessmentRequest, _caller: dict = Depends(auth.get_current_user)):
    # AUTHZ: identity comes from the session, not the body. `req.username`
    # is client-controlled, and this endpoint WRITES to that account — so a
    # student could post as anyone by editing one JSON field.
    req.username = _caller["username"]

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
async def get_head_start(username: str, concept: str, _caller: dict = Depends(auth.get_current_user)):
    """Explain the head start for one topic: prerequisites, which are certified,
    the resulting seeded priors, and the certification wall a head start can't cross."""
    # AUTHZ: the path parameter selects WHICH record; the signed session decides
    # whether this caller may see it. Without this, any username in the URL
    # returned that student's data to anyone who asked.
    auth.require_self_or_teacher(username, _caller)
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
async def get_skill_network(username: str, _caller: dict = Depends(auth.get_current_user)):
    """Prerequisite network for the skill-progress dashboard: one node per topic
    (with mastery state, certification, and head-start flags) plus prerequisite edges.
    This is the visual face of the same graph the head-start mechanism runs on."""
    # AUTHZ: the path parameter selects WHICH record; the signed session decides
    # whether this caller may see it. Without this, any username in the URL
    # returned that student's data to anyone who asked.
    auth.require_self_or_teacher(username, _caller)
    from app.core import bkt_model

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

    # The dashboard shows 9 COARSE topics whose prerequisite structure is fixed
    # curriculum knowledge — that's exactly CANONICAL_PREREQ_EDGES (curated to match
    # SKILL_TOPICS, fully connected). We do NOT derive edges from the live graph: it
    # stores fine-grained concept names ("Variables and Types", "Loops", "Operators")
    # that don't match the coarse topic names, which silently dropped most edges and
    # disconnected the graph. Node STATE still comes from live BKT data above.
    edges = [[a, b] for a, b in CANONICAL_PREREQ_EDGES]

    return {"nodes": nodes, "edges": edges}


@app.get("/api/v1/mcn/calibration/{username}")
async def get_calibration_map(username: str, _caller: dict = Depends(auth.get_current_user)):
    """
    Metacognitive Calibration Network readout for the dashboard: per-topic calibration
    state (over / calibrated / under) fused from self-report, performance, behaviour,
    and BKT knowledge. Flag-gated — returns enabled:false and an empty list when the
    MCN feature is off, so the panel simply hides itself.
    """
    # AUTHZ: the path parameter selects WHICH record; the signed session decides
    # whether this caller may see it. Without this, any username in the URL
    # returned that student's data to anyone who asked.
    auth.require_self_or_teacher(username, _caller)
    from app.core import mcn_service

    if not mcn_service.is_enabled():
        return {"enabled": False, "items": []}

    from app.core import bkt_model
    items = []
    for c in SKILL_TOPICS:
        # Only surface topics the learner has actually touched (has a BKT row).
        if not bkt_model._read_row(username, c):
            continue
        verdict = mcn_service.get_calibration(username, c, log=False)
        if not verdict:
            continue  # insufficient signal → don't show a misleading verdict
        items.append({
            "concept": c,
            "state": verdict.get("map_C"),
            "label": verdict.get("label"),
            "knowledge": verdict.get("map_K"),
            "confidence": round(verdict.get("confidence", 0.0), 3),
            "n_signals": verdict.get("n_signals"),
            "explanation": verdict.get("explanation"),
        })
    return {"enabled": True, "items": items}


# ── Unified Learner Model + Motivational Self-Report ─────────────────────────

@app.get("/api/v1/learner-model/{username}")
async def get_learner_model(username: str, _caller: dict = Depends(auth.get_current_user)):
    """Full multidimensional learner profile (cognitive/metacognitive/affective/motivational)."""
    # AUTHZ: the path parameter selects WHICH record; the signed session decides
    # whether this caller may see it. Without this, any username in the URL
    # returned that student's data to anyone who asked.
    auth.require_self_or_teacher(username, _caller)
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
async def submit_self_report(req: SelfReportRequest, _caller: dict = Depends(auth.get_current_user)):
    # AUTHZ: identity comes from the session, not the body. `req.username`
    # is client-controlled, and this endpoint WRITES to that account — so a
    # student could post as anyone by editing one JSON field.
    req.username = _caller["username"]

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
async def log_behavior_event(req: BehaviorEventRequest, _caller: dict = Depends(auth.get_current_user)):
    # AUTHZ: identity comes from the session, not the body. `req.username`
    # is client-controlled, and this endpoint WRITES to that account — so a
    # student could post as anyone by editing one JSON field.
    req.username = _caller["username"]

    """Frontend-driven behavioral telemetry: copy-code clicks, dwell time, etc."""
    from app.core import telemetry
    telemetry.log_behavior(req.username, req.session_id, req.event,
                           req.value, req.message_id)
    return {"status": "ok"}

@app.post("/api/v1/chat/feedback")
async def handle_feedback(req: FeedbackRequest, _caller: dict = Depends(auth.get_current_user)):
    # AUTHZ: identity comes from the session, not the body. `req.username`
    # is client-controlled, and this endpoint WRITES to that account — so a
    # student could post as anyone by editing one JSON field.
    req.username = _caller["username"]

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
async def create_assignment(req: ChallengeRequest, _t: dict = Depends(verify_teacher)):
    # AUTHZ: teacher-only. These endpoints expose whole-class data and admin
    # actions and previously had NO guard at all — reachable by anyone who
    # could reach the port.
    aid = assignment_manager.create_challenge(req.teacher, req.student, req.question)
    return {"status": "success", "id": aid}

@app.get("/api/v1/assignments/student/{username}")
async def get_student_assignments(username: str, _caller: dict = Depends(auth.get_current_user)):
    # AUTHZ: the path parameter selects WHICH record; the signed session decides
    # whether this caller may see it. Without this, any username in the URL
    # returned that student's data to anyone who asked.
    auth.require_self_or_teacher(username, _caller)
    return assignment_manager.get_by_student(username)

@app.post("/api/v1/assignments/submit")
async def submit_assignment(req: SubmissionRequest):
    assignment_manager.submit_answer(req.assignment_id, req.answer)
    return {"status": "success"}

@app.get("/api/v1/assignments/teacher/pending")
async def get_pending_reviews(username: str, _t: dict = Depends(verify_teacher)):
    # AUTHZ: teacher-only. These endpoints expose whole-class data and admin
    # actions and previously had NO guard at all — reachable by anyone who
    # could reach the port.
    return assignment_manager.get_pending_reviews(username)

@app.post("/api/v1/assignments/grade")
async def grade_assignment(req: AssignmentGradeRequest):
    assignment_manager.grade_assignment(req.assignment_id, req.feedback)
    return {"status": "success"}

@app.get("/api/v1/analytics/active_time")
async def get_active_time_stats(username: str, days: int = 7,
                                _caller: dict = Depends(auth.get_current_user)):
    """
    Calculates active time on the fly.
    """
    # AUTHZ: username arrives as a QUERY parameter here rather than in the path,
    # but it leaks exactly the same way without an ownership check.
    auth.require_self_or_teacher(username, _caller)
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
async def get_user_preferences(username: str, _caller: dict = Depends(auth.get_current_user)):
    # AUTHZ: the path parameter selects WHICH record; the signed session decides
    # whether this caller may see it. Without this, any username in the URL
    # returned that student's data to anyone who asked.
    auth.require_self_or_teacher(username, _caller)
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
async def update_user_preferences(req: PreferencesUpdateRequest, _caller: dict = Depends(auth.get_current_user)):
    # AUTHZ: identity comes from the session, not the body. `req.username`
    # is client-controlled, and this endpoint WRITES to that account — so a
    # student could post as anyone by editing one JSON field.
    req.username = _caller["username"]

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
async def video_chat_stream(request: VideoChatRequest, _caller: dict = Depends(auth.get_current_user)):
    # AUTHZ: identity comes from the session, not the body. `request.username`
    # is client-controlled, and this endpoint WRITES to that account — so a
    # student could post as anyone by editing one JSON field.
    request.username = _caller["username"]

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
async def save_classroom_history(req: ClassroomHistoryRequest, _caller: dict = Depends(auth.get_current_user)):
    # AUTHZ: identity comes from the session, not the body. `req.username`
    # is client-controlled, and this endpoint WRITES to that account — so a
    # student could post as anyone by editing one JSON field.
    req.username = _caller["username"]

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


# =========================================================
# LECTURE CATALOG — chapters → numbered videos
# =========================================================
# Mirrors the flipped-classroom plan. Videos used to be "whatever .mp4 files are in
# the directory", ordered by filename with the topic guessed from that filename — so
# chapter order, slide ranges, and not-yet-recorded lectures had nowhere to live.

_VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm"}
_UPLOAD_TMP = "_uploads"          # partial chunk files live here until finalised


@app.get("/api/v1/video/catalog")
async def get_video_catalog(include_planned: bool = True):
    """Full chapter → video catalog for the classroom and the teacher content tab.

    `include_planned=False` returns only slots that actually have a recording, which
    is what a student browsing available lectures wants.
    """
    chapters = db.fetch_all(
        "SELECT number, title, status FROM video_chapter ORDER BY sort_order, number"
    ) or []
    rows = db.fetch_all(
        "SELECT id, chapter_number, video_number, title, slides, sections, "
        "filename, topic, status FROM video_catalog ORDER BY sort_order, video_number"
    ) or []

    by_chapter: Dict[int, List[Dict]] = {}
    for r in rows:
        d = dict(r)
        if not include_planned and not d.get("filename"):
            continue
        # A row can claim a filename that has since been deleted from disk; report it
        # rather than handing the player a 404.
        d["available"] = bool(d.get("filename")) and (VIDEO_DIR / d["filename"]).exists()
        by_chapter.setdefault(d["chapter_number"], []).append(d)

    out = []
    for c in chapters:
        vids = by_chapter.get(c["number"], [])
        out.append({
            "number": c["number"],
            "title": c["title"],
            "status": c["status"] or "",
            "videos": vids,
            "recorded": sum(1 for v in vids if v["available"]),
            "total": len(vids),
        })
    return {"chapters": out}


@app.get("/api/v1/video/unassigned")
async def get_unassigned_videos(_: str = Depends(verify_teacher)):
    """Recordings present on disk that no catalog slot claims."""
    claimed = {r["filename"] for r in (db.fetch_all(
        "SELECT filename FROM video_catalog WHERE filename IS NOT NULL") or [])}
    files = []
    if VIDEO_DIR.exists():
        for f in sorted(VIDEO_DIR.iterdir()):
            if f.is_file() and f.suffix.lower() in _VIDEO_EXTS and f.name not in claimed:
                files.append({"filename": f.name,
                              "size_mb": round(f.stat().st_size / 1_048_576, 1)})
    return {"files": files}


@app.post("/api/v1/video/upload-chunk")
async def upload_video_chunk(
    file: UploadFile = File(...),
    upload_id: str = Form(...),
    chunk_index: int = Form(...),
    total_chunks: int = Form(...),
    filename: str = Form(...),
    _: str = Depends(verify_teacher),
):
    """Receive one chunk of a lecture recording.

    Lecture files run 20-140MB, which a single multipart POST tends to lose to proxy
    and client timeouts, so the browser slices the file and sends it in parts. Chunks
    are appended to a temp file and only promoted into VIDEO_DIR once the final chunk
    arrives — an interrupted upload therefore never leaves a truncated .mp4 that the
    catalog would happily serve as a real lecture.
    """
    safe_name = os.path.basename(filename or "")
    ext = Path(safe_name).suffix.lower()
    if ext not in _VIDEO_EXTS:
        raise HTTPException(status_code=400,
                            detail=f"Unsupported video type '{ext}'. Allowed: {', '.join(sorted(_VIDEO_EXTS))}")
    # upload_id lands in a filesystem path — keep it to an opaque token.
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", upload_id or ""):
        raise HTTPException(status_code=400, detail="Invalid upload id")

    tmp_dir = VIDEO_DIR / _UPLOAD_TMP
    tmp_dir.mkdir(parents=True, exist_ok=True)
    part = tmp_dir / f"{upload_id}.part"

    try:
        # Chunks arrive in order; index 0 truncates any stale partial from a retry.
        mode = "wb" if chunk_index == 0 else "ab"
        with open(part, mode) as buf:
            shutil.copyfileobj(file.file, buf)
    except Exception as e:
        logger.error(f"[video upload] chunk {chunk_index} failed: {e}")
        raise HTTPException(status_code=500, detail="Chunk write failed")

    if chunk_index + 1 < total_chunks:
        return {"status": "partial", "received": chunk_index + 1, "total": total_chunks}

    dest = VIDEO_DIR / safe_name
    if dest.exists():
        stem, suffix = Path(safe_name).stem, Path(safe_name).suffix
        dest = VIDEO_DIR / f"{stem}_{datetime.now():%Y%m%d%H%M%S}{suffix}"
    try:
        shutil.move(str(part), str(dest))
    except Exception as e:
        logger.error(f"[video upload] finalise failed: {e}")
        raise HTTPException(status_code=500, detail="Could not save video")

    size_mb = round(dest.stat().st_size / 1_048_576, 1)
    logger.info(f"🎬 [CLASSROOM] Uploaded '{dest.name}' ({size_mb} MB)")
    return {"status": "complete", "filename": dest.name, "size_mb": size_mb}


class CatalogAssign(BaseModel):
    catalog_id: int
    filename: Optional[str] = None      # None unassigns the slot


@app.post("/api/v1/video/catalog/assign")
async def assign_video_to_slot(req: CatalogAssign, _: str = Depends(verify_teacher)):
    """Attach an uploaded recording to a planned catalog slot (or clear it)."""
    slot = db.fetch_one("SELECT id FROM video_catalog WHERE id=?", (req.catalog_id,))
    if not slot:
        raise HTTPException(status_code=404, detail="Catalog entry not found")

    fname = os.path.basename(req.filename) if req.filename else None
    if fname:
        if not (VIDEO_DIR / fname).exists():
            raise HTTPException(status_code=404, detail=f"No such recording: {fname}")
        taken = db.fetch_one(
            "SELECT id FROM video_catalog WHERE filename=? AND id<>?", (fname, req.catalog_id))
        if taken:
            raise HTTPException(status_code=409,
                                detail="That recording is already assigned to another video")

    db.execute(
        "UPDATE video_catalog SET filename=?, status=?, updated_at=? WHERE id=?",
        (fname, "complete" if fname else "planned", datetime.now(), req.catalog_id))

    # Warm the transcript now, in the background. Otherwise the first STUDENT to
    # open the lecture pays for it: on CPU that is minutes of an apparently frozen
    # page. Doing it at assign time puts the wait on the teacher, who is already
    # expecting one, and by class time the cache is on disk.
    transcript = {"state": "absent", "position": 0}
    if fname:
        from app.services.video_service import enqueue_transcription
        transcript = enqueue_transcription(str(VIDEO_DIR / fname))

    return {"status": "success", "catalog_id": req.catalog_id, "filename": fname,
            "transcript": transcript}


def _transcript_exists(fname: str) -> bool:
    return (VIDEO_DIR / f"{Path(fname).stem}.transcript.json").exists()


@app.get("/api/v1/video/transcript-status/{filename}")
async def video_transcript_status(filename: str, _: str = Depends(verify_teacher)):
    """Where is this recording in the transcription queue?

    state: ready | running | queued | absent
    """
    from app.services.video_service import transcription_status
    fname = os.path.basename(filename)
    st = transcription_status(str(VIDEO_DIR / fname))
    return {"filename": fname, "ready": st["state"] == "ready", **st}


class CatalogEntry(BaseModel):
    chapter_number: int
    video_number: Optional[int] = None
    title: str
    slides: str = ""
    sections: str = ""


@app.post("/api/v1/video/catalog/add")
async def add_catalog_entry(req: CatalogEntry, _: str = Depends(verify_teacher)):
    """Add a video slot to a chapter (for lectures beyond the seeded plan)."""
    ch = db.fetch_one("SELECT number FROM video_chapter WHERE number=?", (req.chapter_number,))
    if not ch:
        raise HTTPException(status_code=404, detail="Chapter not found")
    nxt = db.fetch_one("SELECT COALESCE(MAX(sort_order),0)+1 AS n FROM video_catalog") or {"n": 1}
    now = datetime.now()
    db.execute(
        "INSERT INTO video_catalog (chapter_number,video_number,title,slides,sections,"
        "status,sort_order,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (req.chapter_number, req.video_number, req.title.strip(), req.slides,
         req.sections, "planned", nxt["n"], now, now))
    return {"status": "success"}


@app.delete("/api/v1/video/catalog/{catalog_id}")
async def delete_catalog_entry(catalog_id: int, _: str = Depends(verify_teacher)):
    """Remove a catalog slot. The video FILE on disk is left untouched — deleting a
    plan entry should never destroy a recording."""
    row = db.fetch_one("SELECT filename FROM video_catalog WHERE id=?", (catalog_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Catalog entry not found")
    db.execute("DELETE FROM video_catalog WHERE id=?", (catalog_id,))
    return {"status": "success", "file_kept": row["filename"]}


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
async def answer_video_checkpoint(req: VideoMCQAnswerRequest, _caller: dict = Depends(auth.get_current_user)):
    # AUTHZ: identity comes from the session, not the body. `req.username`
    # is client-controlled, and this endpoint WRITES to that account — so a
    # student could post as anyone by editing one JSON field.
    req.username = _caller["username"]

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


@app.get("/api/v1/analytics/frustration")
async def frustration_analytics(days: int = 14, _: str = Depends(verify_teacher)):
    """Who is struggling, and where.

    affect_log carries ~3.3k rows and had no read path. `academic_emotion` is the
    useful column — `frustration_level` is 'normal' on 97% of turns, while
    confusion is the signal that actually separates students.
    """
    since = (datetime.now() - timedelta(days=days)).isoformat()

    rows = db.fetch_all(
        "SELECT username, "
        "  COUNT(*) AS turns, "
        "  SUM(CASE WHEN academic_emotion='confusion'   THEN 1 ELSE 0 END) AS confusion, "
        "  SUM(CASE WHEN academic_emotion='frustration' THEN 1 ELSE 0 END) AS frustration, "
        "  SUM(CASE WHEN academic_emotion='boredom'     THEN 1 ELSE 0 END) AS boredom, "
        "  SUM(CASE WHEN academic_emotion='flow'        THEN 1 ELSE 0 END) AS flow, "
        "  SUM(CASE WHEN frustration_level IN ('high','rage') THEN 1 ELSE 0 END) AS high_level, "
        "  SUM(COALESCE(intervention_fired,0)) AS interventions, "
        "  MAX(ts_utc) AS last_seen "
        "FROM affect_log WHERE ts_utc >= ? GROUP BY username", (since,))

    students = []
    for r in rows:
        turns = r["turns"] or 0
        neg = (r["confusion"] or 0) + (r["frustration"] or 0)
        students.append({
            "username": r["username"],
            "turns": turns,
            "confusion": r["confusion"] or 0,
            "frustration": r["frustration"] or 0,
            "boredom": r["boredom"] or 0,
            "flow": r["flow"] or 0,
            "high_level": r["high_level"] or 0,
            "interventions": r["interventions"] or 0,
            # Share of turns showing confusion or frustration. A rate, not a count,
            # so a heavy user is not flagged merely for asking a lot of questions.
            "struggle_pct": round(100 * neg / turns, 1) if turns else 0.0,
            "last_seen": r["last_seen"],
        })
    students.sort(key=lambda s: (-s["struggle_pct"], -s["turns"]))

    # Concepts where negative affect clusters — join affect to the concept asked
    # in the same session, which is what path_event records.
    concepts = db.fetch_all(
        "SELECT p.concept_asked AS concept, COUNT(*) AS n "
        "FROM affect_log a JOIN path_event p "
        "  ON p.username = a.username "
        " AND ABS(STRFTIME('%s', p.ts_utc) - STRFTIME('%s', a.ts_utc)) <= 30 "
        "WHERE a.ts_utc >= ? AND a.academic_emotion IN ('confusion','frustration') "
        "  AND TRIM(COALESCE(p.concept_asked,'')) <> '' "
        "GROUP BY p.concept_asked ORDER BY n DESC LIMIT 10", (since,))

    total_turns = sum(s["turns"] for s in students)
    total_neg = sum(s["confusion"] + s["frustration"] for s in students)
    return {
        "days": days,
        "summary": {
            "students": len(students),
            "turns": total_turns,
            "struggle_pct": round(100 * total_neg / total_turns, 1) if total_turns else 0.0,
            "interventions": sum(s["interventions"] for s in students),
            "at_risk": sum(1 for s in students if s["struggle_pct"] >= 20 and s["turns"] >= 5),
        },
        "students": students,
        "hot_concepts": [dict(c) for c in concepts],
    }


@app.get("/api/v1/analytics/path-deviation")
async def path_deviation_analytics(days: int = 14, _: str = Depends(verify_teacher)):
    """Where students leave the recommended sequence, and where they stall.

    Only rows with a real deviation_type count. Events logged before the write
    path was fixed are all 'unknown' with no expected_concept — they are reported
    separately as `unclassified` rather than silently skewing the percentages.
    """
    since = (datetime.now() - timedelta(days=days)).isoformat()

    unclassified = db.fetch_one(
        "SELECT COUNT(*) AS n FROM path_event "
        "WHERE ts_utc >= ? AND (deviation_type IS NULL OR deviation_type='unknown')",
        (since,))["n"]

    by_type = db.fetch_all(
        "SELECT deviation_type, COUNT(*) AS n FROM path_event "
        "WHERE ts_utc >= ? AND deviation_type NOT IN ('unknown','') "
        "GROUP BY deviation_type ORDER BY n DESC", (since,))

    # A concept students repeatedly sit on without moving past = a stall point.
    stalls = db.fetch_all(
        "SELECT expected_concept AS concept, COUNT(*) AS asks, "
        "       COUNT(DISTINCT username) AS students "
        "FROM path_event WHERE ts_utc >= ? "
        "  AND TRIM(COALESCE(expected_concept,'')) <> '' "
        "GROUP BY expected_concept ORDER BY asks DESC LIMIT 10", (since,))

    per_student = db.fetch_all(
        "SELECT username, "
        "  SUM(CASE WHEN deviation_type='on_path'    THEN 1 ELSE 0 END) AS on_path, "
        "  SUM(CASE WHEN deviation_type='skip_ahead' THEN 1 ELSE 0 END) AS skip_ahead, "
        "  SUM(CASE WHEN deviation_type='revisit'    THEN 1 ELSE 0 END) AS revisit, "
        "  SUM(CASE WHEN deviation_type='off_path'   THEN 1 ELSE 0 END) AS off_path, "
        "  COUNT(*) AS total, MAX(expected_concept) AS stuck_on "
        "FROM path_event WHERE ts_utc >= ? AND deviation_type NOT IN ('unknown','') "
        "GROUP BY username ORDER BY total DESC", (since,))

    students = []
    for r in per_student:
        t = r["total"] or 1
        students.append({**dict(r),
                         "on_path_pct": round(100 * (r["on_path"] or 0) / t, 1)})

    return {
        "days": days,
        "unclassified": unclassified,
        "by_type": [dict(r) for r in by_type],
        "stall_points": [dict(r) for r in stalls],
        "students": students,
    }


@app.get("/api/v1/video/analytics/{video_filename}")
async def video_analytics(video_filename: str, _: str = Depends(verify_teacher)):
    """Per-student watch + quiz results for one lecture.

    The telemetry tables were write-only: everything below was already being
    collected and had no way to be read back.

    Quiz semantics, since video_mcq_response stores ONE ROW PER ATTEMPT:
      attempted  = any row for that checkpoint
      passed     = some row with is_correct = 1
      first_try  = a correct row whose attempts = 1
    A student who answers wrong then right has two rows; counting rows would
    double-count them, so everything here aggregates per checkpoint.
    """
    fname = os.path.basename(video_filename)

    cps = db.fetch_all(
        "SELECT checkpoint_time, question, concept FROM video_checkpoint "
        "WHERE video_filename=? ORDER BY checkpoint_time", (fname,))
    cp_times = [c["checkpoint_time"] for c in cps]
    final_time = cp_times[-1] if cp_times else None

    cov = db.fetch_all(
        "SELECT username, duration_sec, watched_sec, completion_pct, hidden_sec, "
        "completed, updated_at FROM video_coverage WHERE video_filename=?", (fname,))

    # One row per (student, checkpoint): did they pass, and on which attempt.
    mcq = db.fetch_all(
        "SELECT username, checkpoint_time, "
        "       MAX(is_correct) AS passed, "
        "       MIN(CASE WHEN is_correct=1 THEN attempts END) AS winning_attempt, "
        "       MAX(attempts) AS total_attempts, "
        "       AVG(confidence_1_5) AS avg_conf "
        "FROM video_mcq_response WHERE video_filename=? "
        "GROUP BY username, checkpoint_time", (fname,))

    by_user = {}
    for r in mcq:
        by_user.setdefault(r["username"], []).append(dict(r))

    students, usernames = [], set()
    for c in cov:
        usernames.add(c["username"])
    usernames |= set(by_user.keys())

    cov_map = {c["username"]: c for c in cov}

    for u in sorted(usernames):
        c = cov_map.get(u)
        rows = by_user.get(u, [])
        passed = [r for r in rows if r["passed"]]
        first_try = [r for r in passed if (r["winning_attempt"] or 99) == 1]

        fin = next((r for r in rows if final_time is not None
                    and abs(r["checkpoint_time"] - final_time) < 0.01), None)

        watched = float(c["watched_sec"] or 0) if c else 0.0
        hidden = float(c["hidden_sec"] or 0) if c else 0.0
        students.append({
            "username": u,
            "watched_sec": round(watched),
            "duration_sec": round(float(c["duration_sec"] or 0)) if c else 0,
            "completion_pct": round(float(c["completion_pct"] or 0), 1) if c else 0.0,
            "hidden_sec": round(hidden),
            # Fraction of playing time the tab was in the background. High values
            # mean the video ran to nobody — completion_pct alone hides this.
            "inattention_pct": round(100 * hidden / (watched + hidden), 1) if (watched + hidden) else 0.0,
            "completed": bool(c["completed"]) if c else False,
            "checkpoints_total": len(cp_times),
            "checkpoints_attempted": len(rows),
            "checkpoints_passed": len(passed),
            "passed_first_try": len(first_try),
            "needed_retries": len(passed) - len(first_try),
            "avg_confidence": round(sum(r["avg_conf"] or 0 for r in rows) / len(rows), 1) if rows else None,
            "final_attempted": bool(fin),
            "final_passed": bool(fin and fin["passed"]),
            "final_attempts": (fin["total_attempts"] if fin else 0),
            "final_first_try": bool(fin and fin["passed"] and (fin["winning_attempt"] or 99) == 1),
            "last_seen": (c["updated_at"] if c else None),
        })

    # Per-checkpoint difficulty, for spotting a question the class fell over.
    per_cp = []
    for cp in cps:
        t = cp["checkpoint_time"]
        rows = [r for rs in by_user.values() for r in rs if abs(r["checkpoint_time"] - t) < 0.01]
        passed = [r for r in rows if r["passed"]]
        first = [r for r in passed if (r["winning_attempt"] or 99) == 1]
        per_cp.append({
            "checkpoint_time": t,
            "is_final": (final_time is not None and abs(t - final_time) < 0.01),
            "concept": cp["concept"],
            "question": cp["question"],
            "attempted": len(rows),
            "passed": len(passed),
            "first_try": len(first),
            "first_try_pct": round(100 * len(first) / len(rows), 1) if rows else None,
        })

    n = len(students)
    summary = {
        "students": n,
        "avg_completion_pct": round(sum(s["completion_pct"] for s in students) / n, 1) if n else 0,
        "avg_inattention_pct": round(sum(s["inattention_pct"] for s in students) / n, 1) if n else 0,
        "finished_video": sum(1 for s in students if s["completed"]),
        "final_attempted": sum(1 for s in students if s["final_attempted"]),
        "final_passed": sum(1 for s in students if s["final_passed"]),
        "final_first_try": sum(1 for s in students if s["final_first_try"]),
    }

    return {"video": fname, "checkpoints": per_cp, "students": students, "summary": summary}


@app.post("/api/v1/video/engagement")
async def log_video_engagement_event(req: VideoEngagementRequest, _caller: dict = Depends(auth.get_current_user)):
    # AUTHZ: identity comes from the session, not the body. `req.username`
    # is client-controlled, and this endpoint WRITES to that account — so a
    # student could post as anyone by editing one JSON field.
    req.username = _caller["username"]

    from app.core import telemetry
    telemetry.log_video_engagement(req.username, req.video_filename, req.event,
                                   req.position_sec, req.detail)
    return {"status": "ok"}


@app.post("/api/v1/video/coverage")
async def update_video_coverage_summary(req: VideoCoverageRequest, _caller: dict = Depends(auth.get_current_user)):
    # AUTHZ: identity comes from the session, not the body. `req.username`
    # is client-controlled, and this endpoint WRITES to that account — so a
    # student could post as anyone by editing one JSON field.
    req.username = _caller["username"]

    from app.core import telemetry
    telemetry.update_video_coverage(req.username, req.video_filename,
                                    req.duration_sec, req.watched_sec, req.hidden_sec)
    return {"status": "ok"}


@app.post("/api/v1/video/reflection")
async def submit_video_reflection(req: VideoReflectionRequest, _caller: dict = Depends(auth.get_current_user)):
    # AUTHZ: identity comes from the session, not the body. `req.username`
    # is client-controlled, and this endpoint WRITES to that account — so a
    # student could post as anyone by editing one JSON field.
    req.username = _caller["username"]

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
        raw = data.get(os.path.basename(video_filename), [])

        # A video maps to a LIST of prompt strings (legacy), an OBJECT holding this
        # lecture's own questions, or the ID of a graded prelab in _prelabs.
        prelabs = data.get("_prelabs") or {}
        meta = {}
        if isinstance(raw, str):
            meta = prelabs.get(raw) or {}
            problems = meta.get("problems", [])
        elif isinstance(raw, dict):
            meta = dict(raw)
            problems = list(raw.get("problems", []))
            ref = prelabs.get(raw.get("prelab_ref") or "")
            # Only the video that covers the whole assignment sets show_graded, so
            # the graded program is offered once rather than after every lecture.
            if ref and raw.get("show_graded"):
                problems = problems + list(ref.get("problems", []))
                meta["graded"] = ref.get("graded", False)
                meta["title"] = ref.get("title", meta.get("title", ""))
                meta["note"] = ref.get("note", "")
                meta["due_before"] = ref.get("due_before", "")
        else:
            problems = raw

        norm = []
        for prob in problems:
            if isinstance(prob, str):
                if prob.strip():
                    norm.append({"prompt": prob, "samples": [], "concept": ""})
            elif isinstance(prob, dict) and (prob.get("prompt") or "").strip():
                norm.append({"prompt": prob["prompt"],
                             "samples": prob.get("samples", []),
                             "concept": prob.get("concept", "")})

        return {
            "problems": norm,
            "graded": bool(meta.get("graded")),
            "title": meta.get("title", ""),
            "note": meta.get("note", ""),
            "due_before": meta.get("due_before", ""),
            "concepts": meta.get("concepts", []),
        }
    except Exception as e:
        logger.warning(f"Prelab load failed for {video_filename}: {e}")
        return {"problems": []}


class PrelabSaveRequest(BaseModel):
    video_filename: str
    questions: List[str]


def _prelab_path() -> Path:
    return VIDEO_DIR / "prelab.json"


def _load_prelab_file() -> dict:
    p = _prelab_path()
    if not p.exists():
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"[prelab] unreadable prelab.json: {e}")
        raise HTTPException(status_code=500, detail="prelab.json is unreadable")


@app.get("/api/v1/video/prelab-edit/{video_filename}")
async def get_prelab_for_edit(video_filename: str, _: str = Depends(verify_teacher)):
    """The editable question list for one video.

    Concept-tagged questions come back prefixed "[Concept] ..." so a teacher editing
    the box can see and keep the tag; the save endpoint parses that prefix back out.
    Without it, editing would silently strip the concept mapping.
    """
    data = _load_prelab_file()
    raw = data.get(os.path.basename(video_filename), [])
    if isinstance(raw, str):                       # points at a graded prelab
        raw = (data.get("_prelabs") or {}).get(raw, {}).get("problems", [])
    elif isinstance(raw, dict):
        raw = raw.get("problems", [])

    out = []
    for prob in raw or []:
        if isinstance(prob, str):
            out.append(prob)
        elif isinstance(prob, dict):
            prompt = (prob.get("prompt") or "").strip()
            if not prompt:
                continue
            concept = (prob.get("concept") or "").strip()
            out.append(f"[{concept}] {prompt}" if concept else prompt)
    return {"video_filename": os.path.basename(video_filename), "questions": out}


@app.post("/api/v1/video/prelab")
async def save_video_prelab(req: PrelabSaveRequest, _: str = Depends(verify_teacher)):
    """Replace the prelab questions for one video.

    Only that video's entry changes: `_prelabs` and every other video are read back
    and rewritten untouched, so editing one lecture cannot wipe another. The write
    goes to a temp file and is then renamed, so a crash mid-write leaves the old
    file intact rather than a truncated one the app would fail to parse.
    """
    fname = os.path.basename(req.video_filename or "")
    if not fname:
        raise HTTPException(status_code=400, detail="video_filename is required")

    problems = []
    for q in req.questions or []:
        q = (q or "").strip()
        if not q:
            continue
        concept = ""
        if q.startswith("["):
            close = q.find("]")
            if close > 1:
                concept = q[1:close].strip()
                q = q[close + 1:].strip()
        if q:
            problems.append({"concept": concept, "prompt": q} if concept else {"prompt": q})

    data = _load_prelab_file()
    existing = data.get(fname)

    if not problems:
        data.pop(fname, None)                      # cleared → no prelab for this video
    elif isinstance(existing, dict):
        existing["problems"] = problems            # keep title / prelab_ref / show_graded
        data[fname] = existing
    else:
        data[fname] = {"problems": problems}

    path = _prelab_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as e:
        logger.error(f"[prelab] write failed: {e}")
        raise HTTPException(status_code=500, detail="Could not save prelab.json")

    logger.info(f"[prelab] {fname}: saved {len(problems)} question(s)")
    return {"status": "success", "video_filename": fname, "count": len(problems)}


class PrelabStartRequest(BaseModel):
    username: str
    video_filename: str
    problem: str

@app.post("/api/v1/video/prelab-start")
async def log_prelab_start(req: PrelabStartRequest, _caller: dict = Depends(auth.get_current_user)):
    # AUTHZ: identity comes from the session, not the body. `req.username`
    # is client-controlled, and this endpoint WRITES to that account — so a
    # student could post as anyone by editing one JSON field.
    req.username = _caller["username"]

    """Log that a student chose to solve a prelab problem in SAGE (SRL transfer signal)."""
    from app.core import telemetry
    telemetry.log_event(req.username, "prelab_started", {
        "video": req.video_filename, "problem": req.problem[:300]})
    return {"status": "ok"}


# ── TTS helpers ─────────────────────────────────────────────────────────────
# Gemini TTS returns raw 24 kHz, 16-bit, mono PCM. Browsers need a container, so wrap it
# in a minimal WAV header before streaming (avoids an mp3 transcode / ffmpeg dependency).
def _pcm_to_wav(pcm: bytes, sample_rate: int = 24000, channels: int = 1, bits: int = 16) -> bytes:
    import struct
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, bits)
    header += b"data" + struct.pack("<I", len(pcm))
    return header + pcm

# The frontend sends OpenAI voice names (e.g. "nova"). Map the common ones to a comparable
# Gemini voice; anything unrecognized falls back to the configured default.
_OPENAI_TO_GOOGLE_VOICE = {
    "nova": "Kore", "shimmer": "Aoede", "alloy": "Puck",
    "echo": "Charon", "fable": "Fenrir", "onyx": "Orus",
}

def _google_voice_for(requested: str) -> str:
    if not requested:
        return config.DEFAULT_GOOGLE_TTS_VOICE
    if requested[:1].isupper():        # already a Gemini voice name → pass through
        return requested
    return _OPENAI_TO_GOOGLE_VOICE.get(requested.lower(), config.DEFAULT_GOOGLE_TTS_VOICE)

def _synthesize_google(text: str, voice: str) -> bytes:
    """Gemini TTS → WAV bytes, using the existing GOOGLE_API_KEY (AI Studio key).
    Local import so a missing google-genai install only fails the request, not startup."""
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=config.GOOGLE_API_KEY)
    resp = client.models.generate_content(
        model=config.DEFAULT_GOOGLE_TTS_MODEL,
        contents=text,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=_google_voice_for(voice)
                    )
                )
            ),
        ),
    )
    pcm = resp.candidates[0].content.parts[0].inline_data.data
    return _pcm_to_wav(pcm)


@app.post("/api/v1/tts/speak")
async def tts_speak(req: TTSSpeakRequest):
    """Text-to-speech for the "Listen" button. Default provider is Google (Gemini TTS on
    the existing GOOGLE_API_KEY); set TTS_PROVIDER=openai to use the legacy tts-1 path."""
    cleaned = _clean_text_for_tts(req.text)
    if len(cleaned) > 4096:
        cleaned = cleaned[:4093] + "..."
    if not cleaned.strip():
        raise HTTPException(status_code=400, detail="No speakable text after cleaning.")

    # ── Google (Gemini TTS) — default ──
    if config.DEFAULT_TTS_PROVIDER == "google":
        if not config.GOOGLE_API_KEY:
            raise HTTPException(status_code=503, detail="TTS unavailable: GOOGLE_API_KEY not configured.")
        try:
            wav = _synthesize_google(cleaned, req.voice)
            from io import BytesIO
            return StreamingResponse(
                BytesIO(wav),
                media_type="audio/wav",
                headers={"Content-Disposition": "inline; filename=speech.wav"},
            )
        except Exception as e:
            logger.error(f"Google TTS error: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"TTS failed: {str(e)}")

    # ── OpenAI (legacy fallback, TTS_PROVIDER=openai) ──
    if not config.OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="TTS unavailable: OPENAI_API_KEY not configured.")
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