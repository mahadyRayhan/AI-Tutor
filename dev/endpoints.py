# backend/app/api/endpoints.py

from fastapi import APIRouter, Depends, HTTPException, Body
from pydantic import BaseModel
from typing import Dict, Any

from app.orchestrator import Orchestrator
from app.dependencies import get_orchestrator # We will create this dependency injector

router = APIRouter()

class ChatRequest(BaseModel):
    query: str
    user_role: str = 'student' # Default role is student

@router.post("/chat", response_model=Dict[str, Any])
async def chat(
    request: ChatRequest = Body(...),
    orchestrator: Orchestrator = Depends(get_orchestrator)
):
    """
    Main endpoint for chat interactions.
    Receives a query and user role, and returns a response from the appropriate agent.
    """
    if not request.query:
        raise HTTPException(status_code=400, detail="Query cannot be empty.")
    
    if request.user_role not in ['student', 'teacher']:
        raise HTTPException(status_code=400, detail="Invalid user role. Must be 'student' or 'teacher'.")
        
    try:
        response = await orchestrator.handle_query(query=request.query, user_role=request.user_role)
        return response
    except Exception as e:
        # In production, you'd want more specific error handling
        raise HTTPException(status_code=500, detail=str(e))
