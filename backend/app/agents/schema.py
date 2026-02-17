from pydantic import BaseModel
from typing import Optional, List, Dict, Any

class AgentState(BaseModel):
    query: str
    user_id: str
    session_id: str
    user_role: str = "student"
    user_goal: Optional[str] = None
    profile: Dict[str, Any] = {} # e.g. {"attention_span": "short"}
    
    # Internal State (Passed between agents)
    intent: Optional[str] = None
    entities: List[str] = []
    history: List[Dict] = [] # Last few messages
    
    # Flags
    stop_processing: bool = False # If True, return 'final_response' immediately
    final_response: Optional[str] = None
    sources: List[Dict] = []
    suggestions: List[str] = []