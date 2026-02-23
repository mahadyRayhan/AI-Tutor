# backend/app/main.py
import os
import shutil
import subprocess
from fastapi import FastAPI, HTTPException, UploadFile, File, Form 
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import logging
import time
import json
from fastapi.responses import StreamingResponse
from pathlib import Path
from datetime import datetime
import csv

from pyinstrument import Profiler
from fastapi.staticfiles import StaticFiles # Needed to serve the reports
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from fastapi import Header, HTTPException, Depends
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

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

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

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global variables
llm_interface = None
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

# --- ASSIGNMENT DATA MODELS ---
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

def _calculate_mastery(history: List[Dict]) -> Dict[str, float]:
    """
    Calculates a 0-100 mastery score per topic based on interaction types.
    """
    scores = {}
    topic_interactions = {}

    for h in history:
        t = h.get('topic', 'General')
        i = h.get('intent', 'UNKNOWN')
        
        if t not in scores: scores[t] = 0
        if t not in topic_interactions: topic_interactions[t] = 0
        
        topic_interactions[t] += 1
        
        # Scoring Logic
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
        # Cap at 100, minimum based on interaction count
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
    global cot_rag_agent # (This is your Orchestrator)
    try:
        logger.info("Initializing C Tutor components...")
        
        from app.core import config 
        
        # 1. Initialize TWO LLMs
        # Fast Model (for Chat, Routing, Security)
        llm_fast = LLMInterface(
            google_model_id=config.DEFAULT_GOOGLE_MODEL_ID, 
            logger=logger
        )
        
        # Smart Model (for Planning, Grading, Code Review)
        llm_smart = LLMInterface(
            google_model_id=config.DEFAULT_REASONING_MODEL_ID, 
            logger=logger
        )
        
        # 2. Initialize DBs
        vector_store = ChromaVectorStore(
            persist_directory=config.DEFAULT_VECTOR_DB_PATH, 
            logger=logger
        )
        graph_db = Neo4jGraphDB(logger=logger)
        
        # 3. Initialize Orchestrator with BOTH LLMs
        # Note: You need to update Orchestrator's __init__ to accept both!
        cot_rag_agent = ChainOfThoughtRAGAgent(
            llm_fast=llm_fast,      # <--- Pass Fast
            llm_smart=llm_smart,    # <--- Pass Smart
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

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# 4. Dynamic Page Endpoint
@app.get("/{page_name}.html", response_class=HTMLResponse)
async def serve_specific_html(request: Request, page_name: str):
    """
    Dynamically serves teacher_dashboard.html, student_dashboard.html, etc.
    """
    file_name = f"{page_name}.html"
    file_path = TEMPLATES_DIR / file_name

    # Check if file exists using the Path object
    if file_path.exists():
        return templates.TemplateResponse(file_name, {"request": request})
    
    return HTMLResponse(content="Page Not Found", status_code=404)

@app.get("/health")
async def health():
    return {"status": "healthy", "agent_ready": cot_rag_agent is not None}

@app.post("/api/v1/chat/stream")
async def chat_stream(request: ChatRequest):
    """
    Stream the response AND save to session history, while Profiling execution time.
    """
    # --- 1. START PROFILER ---
    profiler = None
    if config.ENABLE_PROFILING:
        profiler = Profiler(interval=0.001, async_mode="enabled")
        profiler.start()

    # --- 2. SESSION MANAGEMENT ---
    # Create session if new
    session_id = request.session_id
    if not session_id:
        session_id = history_manager.create_session(request.username)

    # --- 3. FETCH CONTEXT ---
    # Get the last few messages to find the active topic
    session_data = history_manager.get_session_details(request.username, session_id)
    last_context = ""
    
    if session_data and session_data.get("messages"):
        # Look backwards for the last BOT message to find what we were talking about
        for msg in reversed(session_data["messages"]):
            if msg["role"] == "bot":
                last_context = msg["content"][:200]
                break
    # --------------------------

    # Save USER message immediately
    history_manager.add_message(request.username, session_id, "user", request.message)

    async def generate_stream():
        # Define vars scope for finally block
        full_bot_response = ""
        final_sources = []
        
        try:
            if not cot_rag_agent:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Agent not initialized'})}\n\n"
                return

            user_goal = knowledge_manager.get_goal(request.username)

            # --- 4. RUN AGENT ---
            async for event in cot_rag_agent.run_stream(
                request.message, 
                request.user_role, 
                username=request.username,
                user_goal=user_goal,
                conversation_context=last_context, # Passing the context we fetched
                session_id=session_id # Passing session ID for contextualization
            ):
                # Capture text for history
                if event["type"] in ["token", "answer"]:
                    full_bot_response += event.get("text", "")
                
                # Capture sources/metadata for history
                if event["type"] == "complete":
                    final_data = event["data"]
                    if not full_bot_response: 
                        full_bot_response = final_data.get('answer', '')
                    final_sources = final_data.get('sources', [])
                    
                    # --- FIX: Better Topic Detection ---
                    detected_topic = "General"
                    
                    # 1. Try to get topic from sources
                    if final_sources:
                        # Extract all topics found in sources
                        topics = [s.get('topic') for s in final_sources if s.get('topic')]
                        if topics:
                            # Pick the most common topic (e.g., if 3 docs say "Arrays" and 1 says "General", pick "Arrays")
                            from collections import Counter
                            detected_topic = Counter(topics).most_common(1)[0][0]
                    
                    # 2. Fallback: If no sources (e.g. Prereq Check), try to infer from Intent + Content
                    if detected_topic == "General":
                         # Get the entity the agent found (e.g., "array")
                         entity = final_data.get("detected_entity", "General").lower()
                         
                         # Map entity to frontend topic
                         if "array" in entity: detected_topic = "Arrays"
                         elif "loop" in entity: detected_topic = "Control Flow"
                         elif "pointer" in entity: detected_topic = "Pointers"
                         elif "struct" in entity: detected_topic = "Structures"
                         elif "string" in entity: detected_topic = "Strings"
                         elif "function" in entity: detected_topic = "Functions"
                         elif "var" in entity: detected_topic = "Variables"
                         # You could add simple keyword matching here if you want, 
                         # but usually sources are the source of truth.
                    # -----------------------------------

                    # Log with the CORRECT topic
                    history_manager.log_interaction(
                        request.username, request.message, final_data['intent'], 
                        final_data['answer'], detected_topic 
                    )
                    
                    event["data"]["session_id"] = session_id 

                yield f"data: {json.dumps(event)}\n\n"

            # SAVE BOT MESSAGE to Session History
            history_manager.add_message(request.username, session_id, "bot", full_bot_response, final_sources)

        except Exception as e:
            logger.error(f"Streaming error: {e}", exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        
        finally:
            # --- 5. STOP PROFILER & SAVE REPORT (Conditional) ---
            if profiler:
                try:
                    profiler.stop()
                    
                    timestamp = int(time.time())
                    safe_sess_id = str(session_id).replace("/", "_")
                    filename = f"profile_{safe_sess_id}_{timestamp}.html"
                    filepath = os.path.join(PROFILE_DIR, filename)
                    
                    # This is the heavy part (2-3s) - only runs if enabled
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(profiler.output_html())
                    
                    profile_url = f"/profiles/{filename}"
                    yield f"data: {json.dumps({'type': 'profiler_report', 'url': profile_url})}\n\n"
                except Exception as prof_e:
                    logger.error(f"Profiling save failed: {prof_e}")

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
async def get_student_report(username: str):
    """SLOW Endpoint: Returns LLM advice."""
    history = history_manager.get_student_history(username)
    recent_logs = history[-15:]
    
    if not recent_logs:
        return {"focus": "Just getting started", "strengths": "N/A", "weakness": "N/A", "reading": ["Basics"]}
        
    log_text = "\n".join([f"- [{log['intent']}] Topic: {log.get('topic', 'General')} - Q: {log['query']}" for log in recent_logs])
    
    # --- RESTORED PROMPT ---
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
    
    try:
        report_raw = llm_interface.generate_response(prompt)
        
        # --- ROBUST JSON CLEANER ---
        # 1. Strip markdown
        clean_text = report_raw.replace("```json", "").replace("```", "").strip()
        
        # 2. Extract JSON substring if LLM chatted (Find first '{' and last '}')
        start = clean_text.find('{')
        end = clean_text.rfind('}') + 1
        if start != -1 and end != -1:
            clean_text = clean_text[start:end]
            
        return json.loads(clean_text)
        # ---------------------------
        
    except Exception as e:
        # Log the specific error to the terminal so you can see it
        logger.error(f"Analytics Report Failed: {e}")
        logger.error(f"Raw Output was: {report_raw}")
        return {"focus": "Analysis Error", "strengths": "N/A", "weakness": "Try again later", "reading": []}

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
async def get_all_users():
    return user_manager.get_all_users()

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

@app.get("/api/v1/analytics/student/{username}")
async def get_student_analytics(username: str):
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
async def get_teacher_detailed_analytics():
    """
    Returns calculated matrix with ANONYMIZED IDs.
    """
    all_users = user_manager.get_all_users()
    students = [u['username'] for u in all_users if u['role'] == 'student']

    class_matrix = []
    current_time = datetime.now()

    # Create consistent anonymous IDs based on sorting
    students.sort() 
    
    for idx, student in enumerate(students):
        # Generate ID like "Student_01"
        anon_id = f"Student_{idx + 1:02d}"
        
        history = history_manager.get_student_history(student)
        mastery = _calculate_mastery(history)
        
        # ... (Keep your existing Risk Logic here) ...
        # [PASTE YOUR EXISTING RISK LOGIC FROM PREVIOUS STEP HERE]
        # Recalculate risk/last_active variables...
        
        # 1. Last Active
        last_active_str = "Never"
        days_inactive = 999
        if history:
            last_ts = history[-1]['timestamp']
            last_date = datetime.fromisoformat(last_ts)
            last_active_str = last_date.strftime("%Y-%m-%d")
            days_inactive = (current_time - last_date).days

        # 2. Risk Assessment
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
        elif (debug_count / max(1, total_interactions)) > 0.6 and total_interactions > 5:
            risk = "High"
            risk_reason = "High error rate (Struggling)"
        elif avg_mastery < 30:
            risk = "Medium"
            risk_reason = "Low topic mastery"
            
        sorted_topics = sorted(mastery.items(), key=lambda x: x[1], reverse=True)
        strongest = sorted_topics[0][0] if sorted_topics else "-"
        weakest = sorted_topics[-1][0] if sorted_topics else "-"

        class_matrix.append({
            "hidden_username": student, # Kept for API calls, NOT for display
            "display_id": anon_id,      # Show this to teacher
            "risk_level": risk,
            "risk_reason": risk_reason,
            "strongest_topic": strongest,
            "weakest_topic": weakest,
            "last_active": last_active_str,
            "mastery": mastery
        })
        
    return class_matrix

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
    from app.core.user_knowledge_manager import knowledge_manager
    knowledge_manager.set_goal(req.username, req.goal)
    return {"status": "success", "goal": req.goal}

@app.post("/api/v1/chat/feedback")
async def handle_feedback(req: FeedbackRequest):
    """
    Logs feedback and optionally returns a new answer.
    """
    # 1. Log the Feedback (Crucial for your future ML model)
    feedback_entry = {
        "timestamp": datetime.now().isoformat(),
        "username": req.username,
        "query": req.original_query,
        "preference": req.feedback_type
    }
    
    # Save to a new 'feedback_dataset.csv' for training later
    feedback_file = os.path.join(config.PROJECT_ROOT, "database", "feedback_history.csv")
    file_exists = os.path.isfile(feedback_file)
    
    with open(feedback_file, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "username", "query", "preference"])
        if not file_exists: writer.writeheader()
        writer.writerow(feedback_entry)

    # 2. Handle Re-Generation (Simplify / Deep Dive)
    if req.feedback_type in ["simplify", "deep_dive"]:
        # We need to trigger the agent with a specific instruction
        instruction = ""
        if req.feedback_type == "simplify":
            instruction = "Explain this again, but extremely simple. Use 5th-grader language. Keep it under 3 sentences."
        elif req.feedback_type == "deep_dive":
            instruction = "Explain this again, but go very deep. Cover memory management, advanced edge cases, and best practices."
            
        # We use a special internal prompt format for this
        modified_query = f"{instruction} Query: {req.original_query}"
        
        # Return a stream response just like the main chat
        # Note: We reuse the existing chat_stream logic by creating a dummy request
        # But since we can't call an endpoint from an endpoint easily in FastAPI without overhead,
        # we will return a flag to the frontend to call the stream endpoint again.
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
    
    feedback = llm_interface.generate_response(review_prompt)
    return {"ai_feedback": feedback}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)