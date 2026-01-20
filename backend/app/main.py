# backend/app/main.py

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
from pydantic import BaseModel
from pathlib import Path

# Import components
from app.core import config
from app.db.llm_interface import LLMInterface
from app.db.vector_store import ChromaVectorStore
from app.db.graph_db import Neo4jGraphDB
from app.agents.cot_rag_agent import ChainOfThoughtRAGAgent
from app.core.settings_manager import settings_manager
from app.core.user_manager import user_manager # <--- Import this
from app.core.history_manager import history_manager # <--- Import
from app.core.user_knowledge_manager import knowledge_manager # Ensure this is imported

app = FastAPI(title="C Programming Tutor API", version="2.0.0")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

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

@app.on_event("startup")
async def startup_event():
    global llm_interface, vector_store, graph_db, cot_rag_agent
    try:
        logger.info("Initializing C Tutor components...")
        llm_interface = LLMInterface(logger=logger)
        
        # --- FIX STARTS HERE ---
        # We must pass the path from config!
        from app.core import config 
        vector_store = ChromaVectorStore(
            persist_directory=config.DEFAULT_VECTOR_DB_PATH, 
            logger=logger
        )
        # --- FIX ENDS HERE ---
        
        graph_db = Neo4jGraphDB(logger=logger)
        
        cot_rag_agent = ChainOfThoughtRAGAgent(
            llm_interface=llm_interface,
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
    
    # Old legacy fields (keep them to prevent validation errors if UI sends them)
    socratic: Optional[bool] = False
    topic_class: Optional[str] = "Auto"
    enable_cot: Optional[bool] = True
    enable_validation: Optional[bool] = True
    enable_correction: Optional[bool] = True

@app.get("/health")
async def health():
    return {"status": "healthy", "agent_ready": cot_rag_agent is not None}


@app.post("/api/v1/chat/stream")
async def chat_stream(request: ChatRequest):
    """
    Stream the response from the C Tutor Agent.
    """
    async def generate_stream():
        if not cot_rag_agent:
            yield f"data: {json.dumps({'type': 'error', 'message': 'Agent not initialized'})}\n\n"
            return

        try:
            # Get User Info
            user_id = request.username if request.username else "anonymous"
            user_goal = knowledge_manager.get_goal(user_id)

            # --- CALL THE NEW STREAMING METHOD ---
            async for event in cot_rag_agent.run_stream(
                request.message, 
                request.user_role, 
                username=user_id,
                user_goal=user_goal
            ):
                # Send Event to Frontend
                yield f"data: {json.dumps(event)}\n\n"
                
                # --- LOGGING ---
                # We only log when the "complete" event arrives because that has the full answer
                if event["type"] == "complete":
                    try:
                        final_data = event["data"]
                        # Detect Topic
                        sources = final_data.get('sources', [])
                        detected_topic = sources[0].get('topic', 'General') if sources else "General"
                        if final_data['intent'] == "GUIDANCE": detected_topic = "Prerequisite Check"

                        # Log
                        history_manager.log_interaction(
                            user_id, 
                            request.message, 
                            final_data['intent'], 
                            final_data['answer'], 
                            detected_topic
                        )
                    except Exception as log_err:
                        logger.error(f"Logging failed: {log_err}")

        except Exception as e:
            logger.error(f"Streaming error: {e}", exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
    
    return StreamingResponse(
        generate_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )

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

@app.get("/api/v1/admin/users")
async def get_all_users():
    # In a real app, verify the caller is an admin here via token/session
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
    history = history_manager.get_student_history(username)
    goal = knowledge_manager.get_goal(username) # <--- Correct Manager
 
    
    if not history:
        return {"stats": {}, "mastery": {}, "report": "No history found.", "goal": goal}

    # 1. Mastery Calculation
    mastery = _calculate_mastery(history) # Make sure _calculate_mastery is defined above this function

    # 2. Basic Stats
    stats = {"CONCEPT": 0, "PROBLEM": 0, "DEBUG": 0, "REVIEW": 0}
    for h in history:
        i = h.get('intent', 'UNKNOWN')
        if i in stats: stats[i] += 1
    
    # 3. Generate LLM Report
    recent_logs = history[-15:]
    log_text = "\n".join([f"- [{log['intent']}] Q: {log['query']}" for log in recent_logs])
    
    prompt = f"""
    Analyze this student's recent interaction history with a C Tutor AI.
    
    Student Logs:
    {log_text}
    
    Task: Write a helpful "Progress Report".
    **FORMAT RULES:**
    - Use HTML `<ul><li>...</li></ul>` for lists.
    - Do NOT use Markdown.
    - Keep it concise.

    1. **Focus Areas:** What topics are they asking about most?
    2. **Strengths:** Are they asking good conceptual questions or writing code?
    3. **Weakness/Recommendations:** What should they practice next?
    
    Return JSON: {{ "focus": "...", "strengths": "...", "weakness": "..." }}
    """
    
    report_raw = llm_interface.generate_response(prompt)
    
    try:
        import json
        clean_json = report_raw.replace("```json", "").replace("```", "").strip()
        report_data = json.loads(clean_json)
        # --- FIX: Removed the hardcoded overwrite line here ---
    except:
        report_data = {"focus": "Analysis failed", "strengths": "N/A", "weakness": "N/A"}

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
    Returns global class stats.
    """
    # Load all history
    import json
    from app.core import config
    history_path = config.PROJECT_ROOT / "database" / "chat_history.json"
    
    if not history_path.exists():
        return {"topics": {}, "struggles": []}
        
    with open(history_path, 'r') as f:
        history = json.load(f)
        
    # 1. Topic Popularity
    topic_counts = {}
    # 2. Struggle Detection (Intent = REVIEW or DEBUG)
    struggle_counts = {}
    
    for h in history:
        topic = h.get('topic', 'General')
        intent = h.get('intent', 'UNKNOWN')
        
        # Count Topics
        topic_counts[topic] = topic_counts.get(topic, 0) + 1
        
        # Count Struggles
        if intent in ['REVIEW', 'DEBUG']:
            struggle_counts[topic] = struggle_counts.get(topic, 0) + 1

    return {
        "popular_topics": topic_counts,
        "struggle_areas": struggle_counts,
        "total_interactions": len(history)
    }
    
@app.get("/api/v1/analytics/teacher/detailed")
async def get_teacher_detailed_analytics():
    """
    Returns a matrix of all students and their mastery scores.
    """
    import csv
    # 1. Get all students
    users = []
    # Read users.csv manually or via manager
    with open(config.PROJECT_ROOT / "database" / "users.csv", 'r') as f:
        reader = csv.DictReader(f)
        users = [row['username'] for row in reader if row['role'] == 'student']

    class_matrix = []
    
    for student in users:
        history = history_manager.get_student_history(student)
        mastery = _calculate_mastery(history)
        
        # Determine "Risk Level"
        avg_mastery = sum(mastery.values()) / len(mastery) if mastery else 0
        risk = "Low"
        if avg_mastery < 30 and len(history) > 5: risk = "High"
        elif avg_mastery < 50: risk = "Medium"
        
        class_matrix.append({
            "username": student,
            "mastery": mastery,
            "total_interactions": len(history),
            "risk_level": risk,
            "last_active": history[-1]['timestamp'] if history else "Never"
        })
        
    return class_matrix

@app.post("/api/v1/user/goal")
async def set_user_goal(req: GoalRequest):
    from app.core.user_knowledge_manager import knowledge_manager
    knowledge_manager.set_goal(req.username, req.goal)
    return {"status": "success", "goal": req.goal}
    
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)