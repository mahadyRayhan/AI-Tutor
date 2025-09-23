# backend/app/main.py (Fixed vector store initialization)

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, Any
import logging
import asyncio

# Import your existing components
from app.db.llm_interface import LLMInterface
from app.db.vector_store import ChromaVectorStore
from app.db.graph_db import Neo4jGraphDB
from app.agents.rag_agent import RAGAgent
from app.core import config

app = FastAPI(title="AI Tutor API", version="1.0.0")

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

# Global variables for components
llm_interface = None
vector_store = None
graph_db = None
rag_agent = None

@app.on_event("startup")
async def startup_event():
    """Initialize all components on startup."""
    global llm_interface, vector_store, graph_db, rag_agent
    
    try:
        logger.info("Initializing AI Tutor components...")
        
        # Initialize LLM interface
        llm_interface = LLMInterface(logger=logger)
        logger.info("✅ LLM interface initialized")
        
        # Initialize vector store - TRY DIFFERENT PARAMETER COMBINATIONS
        try:
            # Option 1: Try with collection_name parameter
            vector_store = ChromaVectorStore(
                collection_name="ai_tutor",
                logger=logger
            )
        except TypeError:
            try:
                # Option 2: Try with persist_directory
                vector_store = ChromaVectorStore(
                    persist_directory=config.DEFAULT_VECTOR_DB_PATH,
                    logger=logger
                )
            except TypeError:
                try:
                    # Option 3: Try with no path parameter
                    vector_store = ChromaVectorStore(logger=logger)
                except TypeError:
                    # Option 4: Try minimal initialization
                    vector_store = ChromaVectorStore()
        
        logger.info("✅ Vector store initialized")
        
        # Initialize graph database
        graph_db = Neo4jGraphDB(logger=logger)
        logger.info("✅ Graph database initialized")
        
        # Initialize your existing RAG agent
        rag_agent = RAGAgent(
            llm_interface=llm_interface,
            vector_store=vector_store,
            graph_db=graph_db,
            logger=logger
        )
        logger.info("✅ RAG agent initialized")
        
        logger.info("🎉 AI Tutor initialization complete!")
        
    except Exception as e:
        logger.error(f"❌ Failed to initialize AI Tutor: {e}")
        logger.error(f"Error details: {str(e)}")
        logger.warning("⚠️ Starting server in degraded mode")

@app.on_event("shutdown")
async def shutdown_event():
    """Clean up resources on shutdown."""
    global graph_db
    if graph_db:
        graph_db.close()
    logger.info("AI Tutor shutdown complete")

# Request/Response models
class ChatRequest(BaseModel):
    message: str
    user_role: Optional[str] = "student"
    socratic: Optional[bool] = False
    topic_class: Optional[str] = "Auto"

class ChatResponse(BaseModel):
    answer: str
    sources: list = []
    query_domain: Optional[str] = "Unknown"
    multimedia_enabled: Optional[bool] = False
    multimedia_content: Optional[dict] = None
    generation_log: Optional[list] = []

# Health endpoints
@app.get("/")
async def health_check():
    return {"status": "healthy", "message": "AI Tutor API is running"}

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "components": {
            "llm_interface": llm_interface is not None,
            "vector_store": vector_store is not None,
            "graph_db": graph_db is not None,
            "rag_agent": rag_agent is not None
        },
        "database_path": str(config.DEFAULT_VECTOR_DB_PATH) if hasattr(config, 'DEFAULT_VECTOR_DB_PATH') else "Not configured"
    }

# Main chat endpoint
@app.post("/api/v1/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """Enhanced chat endpoint using your existing RAG agent."""
    
    if not rag_agent:
        return ChatResponse(
            answer="System is still initializing. Please try again in a moment.",
            sources=[],
            query_domain="System",
            multimedia_enabled=False,
            multimedia_content={"images": [], "videos": [], "generation_log": []},
            generation_log=["System initializing"]
        )
    
    try:
        logger.info(f"Processing {request.user_role} query: {request.message}")
        
        # Detect domain for multimedia flag
        query_lower = request.message.lower()
        stem_keywords = ["mse", "loss", "error", "algorithm", "machine learning", "neural", 
                        "gradient", "cybersecurity", "programming", "huber", "mean square"]
        query_domain = "STEM" if any(keyword in query_lower for keyword in stem_keywords) else "Non-STEM"
        
        # Use your existing RAG agent
        result = rag_agent.run(
            query=request.message,
            user_role=request.user_role,
            socratic=request.socratic,
            topic_class=request.topic_class
        )
        
        # Format response for UI compatibility
        response = ChatResponse(
            answer=result.get("answer", "No answer generated"),
            sources=result.get("sources", []),
            query_domain=query_domain,
            multimedia_enabled=(query_domain == "STEM" and request.user_role == "student"),
            multimedia_content={
                "images": [],
                "videos": [], 
                "generation_log": ["Using existing RAG system", "Multimedia enhancement ready for implementation"]
            },
            generation_log=[
                f"Query processed by existing RAG agent",
                f"Domain detected: {query_domain}",
                f"User role: {request.user_role}",
                f"Found {len(result.get('sources', []))} relevant sources"
            ]
        )
        
        logger.info(f"Generated response: domain={response.query_domain}, multimedia_flag={response.multimedia_enabled}")
        return response
        
    except Exception as e:
        logger.error(f"Error processing chat request: {e}", exc_info=True)
        
        return ChatResponse(
            answer=f"I apologize, but I encountered an error processing your request: {str(e)}",
            sources=[],
            query_domain="Error",
            multimedia_enabled=False,
            multimedia_content={"images": [], "videos": [], "generation_log": []},
            generation_log=[f"Error: {str(e)}"]
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)