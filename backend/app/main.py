# backend/app/main.py (Updated with CoT RAG Agent)

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import logging
import time

# Import components
from app.db.llm_interface import LLMInterface
from app.db.vector_store import ChromaVectorStore
from app.db.graph_db import Neo4jGraphDB
from app.agents.cot_rag_agent import ChainOfThoughtRAGAgent  # New CoT agent
from app.core import config
from fastapi.responses import StreamingResponse
import json
import asyncio

app = FastAPI(title="AI Tutor API with Chain of Thought", version="2.0.0")

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
cot_rag_agent = None

@app.on_event("startup")
async def startup_event():
    """Initialize components with CoT RAG agent."""
    global llm_interface, vector_store, graph_db, cot_rag_agent
    
    try:
        logger.info("Initializing AI Tutor with Chain of Thought capabilities...")
        
        # Initialize LLM interface
        llm_interface = LLMInterface(logger=logger)
        logger.info("✅ LLM interface initialized")
        
        # Initialize vector store 
        try:
            vector_store = ChromaVectorStore(logger=logger)
        except TypeError:
            vector_store = ChromaVectorStore()
        logger.info("✅ Vector store initialized")
        
        # Initialize graph database
        graph_db = Neo4jGraphDB(logger=logger)
        logger.info("✅ Graph database initialized")
        
        # Initialize Chain of Thought RAG agent
        cot_rag_agent = ChainOfThoughtRAGAgent(
            llm_interface=llm_interface,
            vector_store=vector_store,
            graph_db=graph_db,
            logger=logger
        )
        logger.info("✅ Chain of Thought RAG agent initialized")
        
        logger.info("🧠 AI Tutor with CoT reasoning ready!")
        
    except Exception as e:
        logger.error(f"❌ Failed to initialize: {e}")
        logger.warning("⚠️ Starting in degraded mode")

@app.on_event("shutdown")
async def shutdown_event():
    """Clean up resources."""
    if graph_db:
        graph_db.close()
    logger.info("AI Tutor shutdown complete")

# Enhanced request/response models
class ChatRequest(BaseModel):
    message: str
    user_role: Optional[str] = "student"
    socratic: Optional[bool] = False
    topic_class: Optional[str] = "Auto"
    enable_cot: Optional[bool] = True
    enable_validation: Optional[bool] = True  # NEW: Toggle validation
    enable_correction: Optional[bool] = True  # NEW: Toggle correction

class CoTAnalysis(BaseModel):
    complexity: str
    reasoning_steps: List[Dict[str, Any]]
    validation: Dict[str, Any]
    correction_attempts: int

class ChatResponse(BaseModel):
    answer: str
    sources: List[Dict[str, Any]] = []
    query_domain: str = "Unknown"
    cot_analysis: Optional[CoTAnalysis] = None
    reasoning_quality: Optional[float] = None
    processing_notes: List[str] = []

# Health endpoints
@app.get("/")
async def health_check():
    return {"status": "healthy", "message": "AI Tutor API with Chain of Thought"}

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "components": {
            "llm_interface": llm_interface is not None,
            "vector_store": vector_store is not None,
            "graph_db": graph_db is not None,
            "cot_rag_agent": cot_rag_agent is not None
        },
        "features": {
            "chain_of_thought": True,
            "failure_detection": True,
            "reasoning_correction": True
        }
    }

@app.get("/api/v1/cot/analysis/{complexity}")
async def get_cot_info(complexity: str):
    """Get information about CoT processing for different complexity levels."""
    return {
        "complexity_level": complexity,
        "description": {
            "high": "Multi-step reasoning with validation and correction",
            "medium": "Structured reasoning with basic validation", 
            "low": "Direct answer without explicit CoT steps"
        }.get(complexity, "Unknown"),
        "features": {
            "step_by_step_reasoning": complexity in ["high", "medium"],
            "confidence_scoring": True,
            "failure_detection": complexity == "high",
            "automatic_correction": complexity == "high"
        }
    }

# Main chat endpoint with CoT
@app.post("/api/v1/chat", response_model=ChatResponse)
async def chat_with_cot(request: ChatRequest):
    """Enhanced chat with Chain of Thought reasoning and failure detection."""
    
    if not cot_rag_agent:
        return ChatResponse(
            answer="System initializing. Please wait...",
            query_domain="System",
            processing_notes=["CoT agent not ready"]
        )
    
    try:
        logger.info(f"Processing CoT query from {request.user_role}: {request.message}")
        
        # Use Chain of Thought RAG agent
        result = cot_rag_agent.run(
            query=request.message,
            user_role=request.user_role,
            socratic=request.socratic,
            topic_class=request.topic_class
        )
        
        # Extract CoT analysis
        cot_data = result.get("cot_analysis", {})
        
        # Create response
        response = ChatResponse(
            answer=result.get("answer", "No answer generated"),
            sources=result.get("sources", []),
            query_domain=result.get("query_domain", "Unknown"),
            cot_analysis=CoTAnalysis(**cot_data) if cot_data else None,
            reasoning_quality=result.get("reasoning_quality", 0.0),
            processing_notes=[
                f"Complexity: {cot_data.get('complexity', 'unknown')}",
                f"CoT steps: {len(cot_data.get('reasoning_steps', []))}",
                f"Corrections: {cot_data.get('correction_attempts', 0)}",
                f"Validation: {'✅ Passed' if cot_data.get('validation', {}).get('is_valid') else '⚠️ Issues detected'}"
            ]
        )
        
        logger.info(f"CoT response generated: quality={response.reasoning_quality:.2f}")
        return response
        
    except Exception as e:
        logger.error(f"CoT processing error: {e}", exc_info=True)
        
        return ChatResponse(
            answer=f"I apologize, but I encountered an error processing your request: {str(e)}",
            query_domain="Error",
            processing_notes=[f"Error: {str(e)}"]
        )

@app.post("/api/v1/chat/simple")
async def chat_without_cot(request: ChatRequest):
    """Simple chat without Chain of Thought (for comparison)."""
    
    # Force simple processing by creating a basic request
    simple_request = ChatRequest(
        message=request.message,
        user_role=request.user_role,
        socratic=request.socratic,
        topic_class=request.topic_class,
        enable_cot=False
    )
    
    # This would use your original RAG agent for comparison
    # For now, we'll use CoT agent but with complexity override
    if cot_rag_agent:
        # Temporarily override complexity detection
        original_detect = cot_rag_agent._detect_query_complexity
        cot_rag_agent._detect_query_complexity = lambda x: 'low'
        
        try:
            result = cot_rag_agent.run(
                query=request.message,
                user_role=request.user_role,
                socratic=request.socratic,
                topic_class=request.topic_class
            )
            return ChatResponse(
                answer=result.get("answer"),
                sources=result.get("sources", []),
                query_domain=result.get("query_domain"),
                processing_notes=["Simple processing without CoT"]
            )
        finally:
            # Restore original method
            cot_rag_agent._detect_query_complexity = original_detect
    
    return ChatResponse(
        answer="Simple processing not available",
        processing_notes=["CoT agent required"]
    )

# CoT debugging endpoints
@app.post("/api/v1/debug/cot-steps")
async def debug_cot_steps(request: ChatRequest):
    """Debug endpoint to see detailed CoT reasoning steps."""
    
    if not cot_rag_agent:
        return {"error": "CoT agent not available"}
    
    try:
        result = cot_rag_agent.run(
            query=request.message,
            user_role=request.user_role,
            socratic=request.socratic,
            topic_class=request.topic_class
        )
        
        return {
            "query": request.message,
            "complexity": result.get("cot_analysis", {}).get("complexity"),
            "detailed_steps": result.get("cot_analysis", {}).get("reasoning_steps", []),
            "validation_details": result.get("cot_analysis", {}).get("validation", {}),
            "quality_metrics": {
                "overall_confidence": result.get("reasoning_quality"),
                "step_confidences": [
                    step.get("confidence") for step in 
                    result.get("cot_analysis", {}).get("reasoning_steps", [])
                ]
            }
        }
        
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/v1/chat/stream")
async def chat_stream(request: ChatRequest):
    """
    Streaming endpoint with time tracking and toggleable features.
    """
    
    async def generate_stream():
        timings = {}
        overall_start = time.time()
        correction_attempts = 0
        try:
            # Initial status
            yield f"data: {json.dumps({'type': 'status', 'message': 'Starting analysis...', 'stage': 'init'})}\n\n"
            
            if not cot_rag_agent:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Agent not initialized'})}\n\n"
                return
            
            # Complexity detection
            step_start = time.time()
            complexity = cot_rag_agent._detect_query_complexity(request.message)
            timings['complexity_detection'] = time.time() - step_start
            
            yield f"data: {json.dumps({'type': 'complexity', 'complexity': complexity, 'time': timings['complexity_detection']})}\n\n"
            await asyncio.sleep(0.1)
            
            # Retrieval phase
            yield f"data: {json.dumps({'type': 'status', 'message': 'Retrieving relevant information...', 'stage': 'retrieval'})}\n\n"
            
            step_start = time.time()
            retrieved_chunks = cot_rag_agent._execute_retrieval(
                request.message, 
                request.user_role, 
                top_k=8
            )
            timings['retrieval'] = time.time() - step_start
            
            yield f"data: {json.dumps({'type': 'retrieval', 'chunks_found': len(retrieved_chunks), 'time': timings['retrieval']})}\n\n"
            await asyncio.sleep(0.1)
            
            if not retrieved_chunks:
                yield f"data: {json.dumps({'type': 'error', 'message': 'No relevant information found'})}\n\n"
                return
            
            context = "\n\n".join([chunk.get('text', '') for chunk in retrieved_chunks])
            
            if complexity in ['high', 'medium'] and request.enable_cot:
                # CoT Reasoning phase
                yield f"data: {json.dumps({'type': 'status', 'message': 'Generating reasoning steps...', 'stage': 'reasoning'})}\n\n"
                
                step_start = time.time()
                cot_steps = cot_rag_agent._generate_cot_reasoning(request.message, context)
                timings['cot_generation'] = time.time() - step_start
                
                # Stream each step with simulated delay to show progression
                for i, step in enumerate(cot_steps):
                    yield f"data: {json.dumps({'type': 'reasoning_step', 'step': step.__dict__, 'step_num': i+1, 'total_steps': len(cot_steps)})}\n\n"
                    await asyncio.sleep(0.4)  # Visual delay for UX
                
                # Validation phase (optional)
                validation = None
                if request.enable_validation:
                    yield f"data: {json.dumps({'type': 'status', 'message': 'Validating reasoning...', 'stage': 'validation'})}\n\n"
                    
                    step_start = time.time()
                    validation = cot_rag_agent._validate_cot_reasoning(cot_steps, request.message)
                    timings['validation'] = time.time() - step_start
                    
                    yield f"data: {json.dumps({'type': 'validation', 'result': validation.__dict__, 'time': timings['validation']})}\n\n"
                    await asyncio.sleep(0.3)
                    
                    # Correction phase (optional)
                    # correction_attempts = 0
                    if request.enable_correction and not validation.is_valid and validation.confidence_score < 0.7:
                        yield f"data: {json.dumps({'type': 'status', 'message': 'Applying corrections...', 'stage': 'correction'})}\n\n"
                        
                        step_start = time.time()
                        cot_steps = cot_rag_agent._correct_failed_reasoning(
                            cot_steps, validation, request.message, context
                        )
                        timings['correction'] = time.time() - step_start
                        correction_attempts = 1
                        
                        # Re-validate
                        validation = cot_rag_agent._validate_cot_reasoning(cot_steps, request.message)
                        yield f"data: {json.dumps({'type': 'validation_updated', 'result': validation.__dict__, 'time': timings.get('correction', 0)})}\n\n"
                else:
                    # Create dummy validation if disabled
                    from dataclasses import dataclass
                    @dataclass
                    class DummyValidation:
                        is_valid: bool = True
                        confidence_score: float = 0.85
                        issues: list = None
                        corrections: list = None
                        failed_steps: list = None
                        def __post_init__(self):
                            self.issues = []
                            self.corrections = []
                            self.failed_steps = []
                    validation = DummyValidation()
                    timings['validation'] = 0
                
                # Final answer synthesis
                yield f"data: {json.dumps({'type': 'status', 'message': 'Synthesizing final answer...', 'stage': 'synthesis'})}\n\n"
                
                step_start = time.time()
                final_answer = cot_rag_agent._synthesize_final_answer(cot_steps, validation, request.message)
                timings['synthesis'] = time.time() - step_start
                
                yield f"data: {json.dumps({'type': 'answer', 'text': final_answer, 'time': timings['synthesis']})}\n\n"
                
                timings['total'] = time.time() - overall_start
                
                # Send complete response with detailed timings
                result = {
                    "type": "complete",
                    "data": {
                        "answer": final_answer,
                        "sources": [chunk.get('metadata', {}) for chunk in retrieved_chunks],
                        "query_domain": cot_rag_agent._detect_domain(request.message),
                        "cot_analysis": {
                            "complexity": complexity,
                            "reasoning_steps": [step.__dict__ for step in cot_steps],
                            "validation": validation.__dict__ if validation else None,
                            "correction_attempts": correction_attempts
                        },
                        "reasoning_quality": validation.confidence_score if validation else 0.8,
                        "timings": {
                            "complexity_detection": round(timings.get('complexity_detection', 0), 2),
                            "retrieval": round(timings.get('retrieval', 0), 2),
                            "cot_generation": round(timings.get('cot_generation', 0), 2),
                            "validation": round(timings.get('validation', 0), 2),
                            "correction": round(timings.get('correction', 0), 2),
                            "synthesis": round(timings.get('synthesis', 0), 2),
                            "total": round(timings.get('total', 0), 2)
                        },
                        "features_used": {
                            "cot": request.enable_cot,
                            "validation": request.enable_validation,
                            "correction": request.enable_correction
                        }
                    }
                }
                yield f"data: {json.dumps(result)}\n\n"
                
            else:
                # Simple processing (no CoT)
                yield f"data: {json.dumps({'type': 'status', 'message': 'Generating answer directly...', 'stage': 'simple'})}\n\n"
                
                step_start = time.time()
                answer = cot_rag_agent._generate_simple_answer(request.message, context)
                timings['answer_generation'] = time.time() - step_start
                timings['total'] = time.time() - overall_start
                
                yield f"data: {json.dumps({'type': 'answer', 'text': answer, 'time': timings['answer_generation']})}\n\n"
                
                result = {
                    "type": "complete",
                    "data": {
                        "answer": answer,
                        "sources": [chunk.get('metadata', {}) for chunk in retrieved_chunks],
                        "query_domain": cot_rag_agent._detect_domain(request.message),
                        "cot_analysis": {
                            "complexity": complexity,
                            "reasoning_steps": [],
                            "validation": None,
                            "correction_attempts": 0
                        },
                        "reasoning_quality": 0.8,
                        "timings": {
                            "complexity_detection": round(timings.get('complexity_detection', 0), 2),
                            "retrieval": round(timings.get('retrieval', 0), 2),
                            "answer_generation": round(timings.get('answer_generation', 0), 2),
                            "total": round(timings.get('total', 0), 2)
                        },
                        "features_used": {
                            "cot": request.enable_cot,
                            "validation": False,
                            "correction": False
                        }
                    }
                }
                yield f"data: {json.dumps(result)}\n\n"
            
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