# backend/app/main.py

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import logging
import time
import json
from fastapi.responses import StreamingResponse

# Import components
from app.db.llm_interface import LLMInterface
from app.db.vector_store import ChromaVectorStore
from app.db.graph_db import Neo4jGraphDB
from app.agents.cot_rag_agent import ChainOfThoughtRAGAgent

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
    # Old fields kept for compatibility but ignored
    socratic: Optional[bool] = False
    topic_class: Optional[str] = "Auto"
    enable_cot: Optional[bool] = True
    enable_validation: Optional[bool] = True
    enable_correction: Optional[bool] = True

@app.get("/health")
async def health():
    return {"status": "healthy", "agent_ready": cot_rag_agent is not None}

@app.get("/api/v1/graph/context")
async def get_graph_context(query: str):
    """
    Returns nodes and edges related to the query from Neo4j.
    """
    if not graph_db:
        return {"nodes": [], "edges": []}
    
    # Simplified Cypher to ensure we get specific properties, not complex objects
    # We return the Relationship TYPE as a string (type(r)) to avoid object parsing issues
    cypher = """
    MATCH (n:Concept)
    WHERE toLower(n.name) CONTAINS toLower($query)
    MATCH (n)-[r]-(m)
    RETURN n.name as source, type(r) as rel_type, m.name as target, labels(m) as target_labels
    LIMIT 20
    """
    
    # Basic keyword extraction logic
    search_term = query.split()[-1].replace("?", "")
    if "array" in query.lower(): search_term = "Arrays"
    elif "variable" in query.lower(): search_term = "Variables"
    elif "function" in query.lower(): search_term = "Functions"
    elif "loop" in query.lower(): search_term = "For Loop"

    try:
        results = graph_db.execute_query(cypher, {"query": search_term})
    except Exception as e:
        logger.error(f"Graph query failed: {e}")
        return {"nodes": [], "edges": []}

    nodes = []
    edges = []
    seen_nodes = set()

    for record in results:
        # In the updated Cypher, record is a dictionary with simple keys
        s_id = record['source']
        rel_type = record['rel_type']
        t_id = record['target']
        t_labels = record['target_labels']
        
        # Process Source Node (Group: Focus)
        if s_id not in seen_nodes:
            nodes.append({"id": s_id, "label": s_id, "group": "focus"})
            seen_nodes.add(s_id)

        # Process Target Node
        # t_labels is a list, grab the first one (e.g. 'Concept')
        t_group = t_labels[0] if t_labels else 'Node'
        
        if t_id not in seen_nodes:
            nodes.append({"id": t_id, "label": t_id, "group": t_group})
            seen_nodes.add(t_id)

        # Process Edge
        edges.append({
            "from": s_id,
            "to": t_id,
            "label": rel_type.replace("_", " ").lower(),
            "arrows": "to"
        })

    return {"nodes": nodes, "edges": edges}

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
            # 1. Send status update
            yield f"data: {json.dumps({'type': 'status', 'message': 'Analyzing your C programming question...', 'stage': 'init'})}\n\n"
            
            start_time = time.time()
            
            # 2. Run the Agent
            # The agent now handles intent classification and retrieval internally
            result = cot_rag_agent.run(request.message, request.user_role)
            
            duration = time.time() - start_time
            
            # 3. Stream the "Intent" (Concept vs Problem)
            intent = result.get('intent', 'UNKNOWN')
            yield f"data: {json.dumps({'type': 'status', 'message': f'Identified as {intent} query', 'stage': 'intent'})}\n\n"
            
            # 4. Stream the final answer
            # We send it as type 'answer' which the UI expects
            yield f"data: {json.dumps({'type': 'answer', 'text': result['answer'], 'time': duration})}\n\n"

            # 5. Send Complete signal with sources
            final_payload = {
                "type": "complete",
                "data": {
                    "answer": result['answer'],
                    "sources": result.get('sources', []),
                    "suggestions": result.get('suggestions', []),
                    "query_domain": "C Programming",
                    "cot_analysis": {
                        "complexity": intent, # reusing complexity field for intent
                        "reasoning_steps": [], # No detailed steps in new agent
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)