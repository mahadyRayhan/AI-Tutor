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
    mastery_weak_tier: str = ""      # "" | quiz | micro | code — lowest-posterior tier

    # Metacognitive Calibration (MCN) — inferred, flag-gated. "" when unknown/disabled.
    calibration_state: str = ""      # "" | over | cal | under
    calibration_detail: str = ""     # short account injected into the LLM prompt

    # CAC disclosure cap (cac_graph.Rung as int). 5 = CODE = no restriction, which
    # is both the default and what a disconnected/shadow-mode layer leaves in place,
    # so an agent that never reads it behaves exactly as before. Only ever lowered.
    rung_cap: int = 5
    cac_reasons: List[str] = []      # audit trail for why it was lowered

    # Multi-turn (crescendo) trajectory risk — snapshot on EVERY turn, not only blocks,
    # so the layer's contribution is measurable rather than inferable.
    traj_risk: float = 0.0     # Eq. (11)  R_t = ½·peak + ½·acc
    traj_acc: float = 0.0      # Eq. (10)  accumulator
    traj_peak: float = 0.0     # Eq. (9)   decayed peak

    # Internal State (Passed between agents)
    intent: Optional[str] = None
    entities: List[str] = []

    # Concept that mastery evidence for this turn belongs to, when a caller KNOWS
    # it (e.g. the micro-challenge router, which is answering a challenge issued
    # about a specific topic). Set this rather than relying on `entities[0]`:
    # entities are extracted from the user's text, and for a pasted code block
    # that yields tokens like "printf" — which is how code-tier evidence ended up
    # filed under non-concepts. None means "infer it".
    evidence_topic: Optional[str] = None
    history: List[Dict] = [] 
    
    # Flags
    stop_processing: bool = False 
    final_response: Optional[str] = None
    sources: List[Dict] = []
    suggestions: List[str] = []