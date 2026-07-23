from pydantic import BaseModel
from typing import Optional, List, Dict, Any

class AgentState(BaseModel):
    query: str
    original_query: str = ""
    user_id: str
    session_id: str
    user_role: str = "student"
    user_goal: Optional[str] = None
    profile: Dict[str, Any] = {} 
    
    # --- NEW: Phase 1 Sensory Variables ---
    s_goal: float = 0.0          # Goal Alignment Score
    c_code: int = 0              # Code Complexity
    m_state: str = "Planning"    # Metacognitive State
    delta_f: float = 0.0         # Frustration Trajectory
    n_strike: int = 0            # Off-topic strikes
    # --------------------------------------

    # Mastery-Conditioned Response Adaptation
    mastery_level: str = "novice"    # novice | developing | proficient | reviewing
    mastery_detail: str = ""         # BKT summary injected into LLM prompt

    # Metacognitive Calibration (MCN) — inferred, flag-gated. "" when unknown/disabled.
    calibration_state: str = ""      # "" | over | cal | under
    calibration_detail: str = ""     # short account injected into the LLM prompt

    # Internal State (Passed between agents)
    intent: Optional[str] = None
    entities: List[str] = []
    history: List[Dict] = [] 
    
    # Flags
    stop_processing: bool = False 
    final_response: Optional[str] = None
    sources: List[Dict] = []
    suggestions: List[str] = []