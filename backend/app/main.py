# backend/app/main.py

import shutil
import subprocess
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import logging
import time
import json
from fastapi.responses import StreamingResponse

# Import components
from app.core import config
from app.db.llm_interface import LLMInterface
from app.db.vector_store import ChromaVectorStore
from app.db.graph_db import Neo4jGraphDB
from app.agents.cot_rag_agent import ChainOfThoughtRAGAgent
from app.core.settings_manager import settings_manager
from app.core.user_manager import user_manager # <--- Import this
from app.core.history_manager import history_manager # <--- Import

app = FastAPI(title="C Programming Tutor API", version="2.0.0")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

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

# @app.get("/api/v1/graph/context")
# async def get_graph_context(query: str):
#     """
#     Returns nodes and edges related to the query from Neo4j.
#     """
#     if not graph_db:
#         return {"nodes": [], "edges": []}
    
#     # Simplified Cypher to ensure we get specific properties, not complex objects
#     # We return the Relationship TYPE as a string (type(r)) to avoid object parsing issues
#     cypher = """
#     MATCH (n:Concept)
#     WHERE toLower(n.name) CONTAINS toLower($query)
#     MATCH (n)-[r]-(m)
#     RETURN n.name as source, type(r) as rel_type, m.name as target, labels(m) as target_labels
#     LIMIT 20
#     """
    
#     # Basic keyword extraction logic
#     search_term = query.split()[-1].replace("?", "")
#     if "array" in query.lower(): search_term = "Arrays"
#     elif "variable" in query.lower(): search_term = "Variables"
#     elif "function" in query.lower(): search_term = "Functions"
#     elif "loop" in query.lower(): search_term = "For Loop"

#     try:
#         results = graph_db.execute_query(cypher, {"query": search_term})
#     except Exception as e:
#         logger.error(f"Graph query failed: {e}")
#         return {"nodes": [], "edges": []}

#     nodes = []
#     edges = []
#     seen_nodes = set()

#     for record in results:
#         # In the updated Cypher, record is a dictionary with simple keys
#         s_id = record['source']
#         rel_type = record['rel_type']
#         t_id = record['target']
#         t_labels = record['target_labels']
        
#         # Process Source Node (Group: Focus)
#         if s_id not in seen_nodes:
#             nodes.append({"id": s_id, "label": s_id, "group": "focus"})
#             seen_nodes.add(s_id)

#         # Process Target Node
#         # t_labels is a list, grab the first one (e.g. 'Concept')
#         t_group = t_labels[0] if t_labels else 'Node'
        
#         if t_id not in seen_nodes:
#             nodes.append({"id": t_id, "label": t_id, "group": t_group})
#             seen_nodes.add(t_id)

#         # Process Edge
#         edges.append({
#             "from": s_id,
#             "to": t_id,
#             "label": rel_type.replace("_", " ").lower(),
#             "arrows": "to"
#         })

#     return {"nodes": nodes, "edges": edges}

@app.post("/api/v1/chat/stream")
@app.post("/api/v1/chat/stream")
async def chat_stream(request: ChatRequest):
    """
    Stream the response from the C Tutor Agent with Topic Tracking.
    """
    async def generate_stream():
        if not cot_rag_agent:
            yield f"data: {json.dumps({'type': 'error', 'message': 'Agent not initialized'})}\n\n"
            return

        try:
            # 1. Send status update
            yield f"data: {json.dumps({'type': 'status', 'message': 'Analyzing your C programming question...', 'stage': 'init'})}\n\n"
            
            start_time = time.time()
            
            # 2. Run the Agent
            result = cot_rag_agent.run(request.message, request.user_role)
            
            duration = time.time() - start_time
            
            # 3. Log History with Topic Detection
            try:
                # A. Get Username
                user_id = request.username if request.username else "anonymous"
                
                # B. Get Intent
                intent = result.get('intent', 'UNKNOWN')
                
                # C. Detect Topic (New Logic)
                # We look at the first source retrieved to guess the topic.
                sources = result.get('sources', [])
                detected_topic = "General"
                
                if intent == "GUIDANCE":
                    detected_topic = "Prerequisite Check"
                elif sources:
                    # 'topic' was added to metadata in data_processing.py
                    detected_topic = sources[0].get('topic', 'General')
                
                # D. Log to file (Requires updated HistoryManager)
                history_manager.log_interaction(
                    user_id, 
                    request.message, 
                    intent, 
                    result['answer'], 
                    detected_topic  # <--- The new argument
                )
            except Exception as log_err:
                logger.error(f"Logging failed: {log_err}")

            # 4. Stream Status
            yield f"data: {json.dumps({'type': 'status', 'message': f'Identified as {intent} query', 'stage': 'intent'})}\n\n"
            
            # 5. Stream Answer
            yield f"data: {json.dumps({'type': 'answer', 'text': result['answer'], 'time': duration})}\n\n"

            # 6. Send Complete Signal
            final_payload = {
                "type": "complete",
                "data": {
                    "answer": result['answer'],
                    "sources": result.get('sources', []),
                    "suggestions": result.get('suggestions', []),
                    "query_domain": "C Programming",
                    "cot_analysis": {
                        "complexity": intent,
                        "reasoning_steps": [],
                        "validation": None
                    },
                    "timings": {"total": round(duration, 2)}
                }
            }
            yield f"data: {json.dumps(final_payload)}\n\n"

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
    user = user_manager.authenticate(creds.username, creds.password)
    if user:
        return {"status": "success", "user": user}
    else:
        # Return 401 Unauthorized
        raise HTTPException(status_code=401, detail="Invalid credentials")

@app.get("/api/v1/analytics/student/{username}")
async def get_student_analytics(username: str):
    """
    Returns stats and an LLM-generated report card.
    """
    history = history_manager.get_student_history(username)
    
    if not history:
        return {"stats": {}, "report": "No history found."}

    # 1. Calculate Stats
    stats = {"CONCEPT": 0, "PROBLEM": 0, "DEBUG": 0, "REVIEW": 0}
    for h in history:
        i = h.get('intent', 'UNKNOWN')
        if i in stats: stats[i] += 1
    
    # 2. Generate LLM Report
    # We take the last 15 queries to analyze trends
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
    
    Return JSON: {{ "focus": "<ul><li>...</li></ul>", "strengths": "<ul><li>...</li></ul>", "weakness": "<ul><li>...</li></ul>" }}
    """
    
    report_raw = llm_interface.generate_response(prompt)
    
    # Simple cleanup to ensure JSON
    try:
        import json
        clean_json = report_raw.replace("```json", "").replace("```", "").strip()
        report_data = json.loads(clean_json)
    except:
        report_data = {"focus": "Analysis failed", "strengths": "N/A", "weakness": "N/A"}

    return {
        "stats": stats,
        "total_queries": len(history),
        "report": report_data
    }

# --- RESOURCE MANAGEMENT ENDPOINTS ---
@app.post("/api/v1/resources/upload")
async def upload_resource(
    files: List[UploadFile] = File(...), 
    resource_type: str = "code" # 'code', 'concept', 'metadata'
):
    """
    Uploads files to the specific resource folder and triggers ingestion.
    """
    saved_files = []
    
    # 1. Determine Target Directory
    if resource_type == "code":
        target_dir = config.PROJECT_ROOT / "resources" / "code"
    elif resource_type == "concept":
        target_dir = config.PROJECT_ROOT / "resources" / "concepts"
    elif resource_type == "metadata":
        target_dir = config.PROJECT_ROOT / "resources" / "metadata"
    else:
        raise HTTPException(status_code=400, detail="Invalid resource type")

    # 2. Save Files
    for file in files:
        file_path = target_dir / file.filename
        try:
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            saved_files.append(file.filename)
        except Exception as e:
            logger.error(f"Failed to save {file.filename}: {e}")

    # 3. Trigger Re-Ingestion (Run scripts in background)
    # We use subprocess to run the scripts exactly as if you typed them in terminal
    try:
        script_name = "ingest_manual_graph.py" if resource_type == "metadata" else "ingest_data.py"
        script_path = config.PROJECT_ROOT / "scripts" / script_name
        
        # Run the script
        subprocess.Popen(["python", str(script_path)])
        
        return {
            "status": "success", 
            "message": f"Uploaded {len(saved_files)} files. Database update started in background.",
            "files": saved_files
        }
    except Exception as e:
        return {"status": "warning", "message": f"Files saved, but ingestion failed to start: {e}"}

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
    
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)