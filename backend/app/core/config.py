# backend/app/core/config.py

import os
from dotenv import load_dotenv
from pathlib import Path
from typing import Dict, List, Any

# --- THE CRITICAL FIX: DEFINE THE PROJECT ROOT ---
# This makes all paths absolute and independent of the current working directory.
# It navigates up three levels from this file (core -> app -> backend -> AI-Tutor/)
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Load environment variables from the .env file at the project root
dotenv_path = PROJECT_ROOT / '.env'
load_dotenv(dotenv_path=dotenv_path)

# --- API Configuration ---
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not GOOGLE_API_KEY:
    print("Warning: GOOGLE_API_KEY environment variable not set.")
if not OPENAI_API_KEY:
    print("Warning: OPENAI_API_KEY environment variable not set.")

# --- Model IDs ---
DEFAULT_GENERATIVE_LLM_PROVIDER = os.getenv("LLM_PROVIDER", "google")
# DEFAULT_GOOGLE_MODEL_ID = "gemini-2.5-flash"
# DEFAULT_GOOGLE_MODEL_ID = "gemini-3.1-pro-preview" # just for data integration
# DEFAULT_REASONING_MODEL_ID = "gemini-3-pro-preview" 
DEFAULT_REASONING_MODEL_ID = "gemini-flash-latest"
DEFAULT_GOOGLE_MODEL_ID = "gemini-3.1-flash-lite-preview"
DEFAULT_GOOGLE_EMBEDDING_MODEL = "gemini-embedding-001"
DEFAULT_OPENAI_CHAT_MODEL = "gpt-4-turbo"
DEFAULT_OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
SYSTEM_TYPE = os.getenv("SYSTEM_TYPE", "math-system")

# --- Default Paths (Now built from PROJECT_ROOT) ---
RESOURCES_DIR = PROJECT_ROOT / "resources"

# Update these paths
RESOURCES_DIR = PROJECT_ROOT / "resources"
CONCEPTS_PATH = RESOURCES_DIR / "concepts"
CODE_PATH = RESOURCES_DIR / "code"

RESOURCE_PATHS = [CONCEPTS_PATH, CODE_PATH]

# DB_DIR = PROJECT_ROOT / "database"
DB_DIR = PROJECT_ROOT / "backend" / "database"
LOG_DIR = PROJECT_ROOT / "logs"
DEFAULT_VECTOR_DB_PATH = str(DB_DIR / "chroma_db")
DEFAULT_LOG_FILE_PATH = str(LOG_DIR / "ai_tutor.log")
DEFAULT_FEEDBACK_DB_PATH = str(DB_DIR / "feedback_history.csv")

# --- Default RAG Settings ---
DEFAULT_VECTOR_DB_TYPE = "chroma"
DEFAULT_CHUNK_STRATEGY = "recursive"
DEFAULT_CHUNK_SIZE = 1500
DEFAULT_CHUNK_OVERLAP = 200
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_TOP_K = 5
DEFAULT_MAX_HOPS = 2

# --- FILE SECURITY SETTINGS ---
ALLOWED_EXTENSIONS = {
    "code": {".c"},
    "concept": {".md", ".pdf", ".docx", ".pptx"},
    "metadata": {".json"} # Keeping this for graph logic if needed
}

# Max file size (e.g., 10MB)
MAX_FILE_SIZE_MB = 10

# --- Multi-Domain Processing Settings ---
# Domain-specific chunk sizes (can override defaults based on content type)
STEM_CHUNK_SIZE = int(os.getenv("STEM_CHUNK_SIZE", str(DEFAULT_CHUNK_SIZE + 200)))  # Larger chunks for technical content
NON_STEM_CHUNK_SIZE = int(os.getenv("NON_STEM_CHUNK_SIZE", str(DEFAULT_CHUNK_SIZE - 200)))  # Smaller chunks for narrative content

STEM_CHUNK_OVERLAP = int(os.getenv("STEM_CHUNK_OVERLAP", str(DEFAULT_CHUNK_OVERLAP + 100)))  # More overlap for technical continuity
NON_STEM_CHUNK_OVERLAP = int(os.getenv("NON_STEM_CHUNK_OVERLAP", str(DEFAULT_CHUNK_OVERLAP - 50)))  # Less overlap for distinct concepts

# Domain detection keywords (can be customized based on your content)
STEM_KEYWORDS = {
    'computer_science': ['algorithm', 'programming', 'software', 'hardware', 'coding', 'database', 'API', 'framework', 'data structure', 'python', 'java', 'javascript'],
    'cybersecurity': ['cybersecurity', 'cyber security', 'encryption', 'malware', 'firewall', 'vulnerability', 'attack', 'security', 'threat', 'defense', 'penetration', 'hacking'],
    'machine_learning': ['neural network', 'model', 'training', 'dataset', 'classification', 'regression', 'AI', 'artificial intelligence', 'deep learning', 'tensorflow', 'pytorch'],
    'general_tech': ['technology', 'system', 'network', 'server', 'protocol', 'technical', 'implementation', 'method', 'digital', 'computing']
}

NON_STEM_KEYWORDS = {
    'history': ['historical', 'century', 'war', 'revolution', 'empire', 'dynasty', 'era', 'period', 'ancient', 'medieval', 'chronology', 'timeline'],
    'literature': ['novel', 'poem', 'author', 'literary', 'narrative', 'character', 'plot', 'theme', 'poetry', 'prose', 'fiction', 'writing'],
    'institutional': ['university', 'college', 'institution', 'founded', 'established', 'tradition', 'homecoming', 'academic', 'campus', 'student'],
    'cultural': ['tradition', 'culture', 'society', 'philosophy', 'art', 'language', 'custom', 'practice', 'social', 'community']
}

# Retrieval settings by domain
STEM_RETRIEVAL_SETTINGS = {
    'kg_weight': 0.6,  # Knowledge graph more important for technical relationships
    'vector_weight': 0.4,
    'top_k_multiplier': 1.2  # Retrieve more chunks for complex technical queries
}

NON_STEM_RETRIEVAL_SETTINGS = {
    'kg_weight': 0.4,  # Vector search more important for narrative content
    'vector_weight': 0.6,
    'top_k_multiplier': 1.0
}

# --- Caching ---
DEFAULT_USE_EMBEDDING_CACHE = True
DEFAULT_USE_LLM_CACHE = False

# --- Generation Configs ---
DEFAULT_GOOGLE_GENERATION_CONFIG = { 
    "temperature": 0.3, 
    "top_p": 0.95, 
    "top_k": 40, 
    "max_output_tokens": 8192, 
    "response_mime_type": "text/plain" 
}
DEFAULT_OPENAI_GENERATION_CONFIG = { 
    "temperature": 0.3, 
    "max_tokens": 4096 
}

# --- Context Management ---
CHAR_TO_TOKEN_RATIO = 3.5
MAX_CONTEXT_TOKENS = 16000
SIMILARITY_THRESHOLD = 0.95
MIN_CHUNK_LENGTH = 50

# --- Neo4j Graph Database ---
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")

# --- Entity and Relationship Extraction Settings ---
MAX_ENTITIES_PER_CHUNK = int(os.getenv("MAX_ENTITIES_PER_CHUNK", "15"))
MAX_RELATIONSHIPS_PER_CHUNK = int(os.getenv("MAX_RELATIONSHIPS_PER_CHUNK", "10"))

# Domain-specific processing flags
ENABLE_TECHNICAL_ENTITY_EXTRACTION = os.getenv("ENABLE_TECHNICAL_ENTITY_EXTRACTION", "true").lower() == "true"
ENABLE_HISTORICAL_RELATIONSHIP_INFERENCE = os.getenv("ENABLE_HISTORICAL_RELATIONSHIP_INFERENCE", "true").lower() == "true"

# Document classification thresholds
DOMAIN_CLASSIFICATION_THRESHOLD = float(os.getenv("DOMAIN_CLASSIFICATION_THRESHOLD", "0.3"))  # Minimum score difference to classify as STEM vs Non-STEM

# --- Enhanced Logging Settings ---
ENTITY_EXTRACTION_LOG_LEVEL = os.getenv("ENTITY_EXTRACTION_LOG_LEVEL", DEFAULT_LOG_LEVEL)
RELATIONSHIP_EXTRACTION_LOG_LEVEL = os.getenv("RELATIONSHIP_EXTRACTION_LOG_LEVEL", DEFAULT_LOG_LEVEL)
RETRIEVAL_LOG_LEVEL = os.getenv("RETRIEVAL_LOG_LEVEL", DEFAULT_LOG_LEVEL)

# --- Multi-Domain RAG Settings ---
# Enhanced retrieval for diverse content
MULTI_DOMAIN_TOP_K = int(os.getenv("MULTI_DOMAIN_TOP_K", "12"))  # Higher default for diverse content
CROSS_DOMAIN_SEARCH_ENABLED = os.getenv("CROSS_DOMAIN_SEARCH_ENABLED", "true").lower() == "true"

# Domain-aware reasoning settings
STEM_REASONING_TEMPERATURE = float(os.getenv("STEM_REASONING_TEMPERATURE", "0.2"))  # Lower for technical accuracy
NON_STEM_REASONING_TEMPERATURE = float(os.getenv("NON_STEM_REASONING_TEMPERATURE", "0.3"))  # Slightly higher for creative inference

# Text search fallback settings
ENABLE_TEXT_SEARCH_FALLBACK = os.getenv("ENABLE_TEXT_SEARCH_FALLBACK", "true").lower() == "true"
TEXT_SEARCH_CHUNK_LIMIT = int(os.getenv("TEXT_SEARCH_CHUNK_LIMIT", "20"))

# --- Utility Functions for Multi-Domain Processing ---
def get_chunk_size_for_domain(domain: str) -> int:
    """Get appropriate chunk size based on content domain."""
    return STEM_CHUNK_SIZE if domain == "STEM" else NON_STEM_CHUNK_SIZE

def get_chunk_overlap_for_domain(domain: str) -> int:
    """Get appropriate chunk overlap based on content domain."""
    return STEM_CHUNK_OVERLAP if domain == "STEM" else NON_STEM_CHUNK_OVERLAP

def get_retrieval_settings_for_domain(domain: str) -> dict:
    """Get retrieval settings based on content domain."""
    return STEM_RETRIEVAL_SETTINGS if domain == "STEM" else NON_STEM_RETRIEVAL_SETTINGS

def get_reasoning_temperature_for_domain(domain: str) -> float:
    """Get reasoning temperature based on content domain."""
    return STEM_REASONING_TEMPERATURE if domain == "STEM" else NON_STEM_REASONING_TEMPERATURE

# --- Multimedia Generation Settings (Using Existing GOOGLE_API_KEY) ---
# Your existing VEO model configuration
VEO3_MODEL = os.getenv("VEO3_MODEL", "veo-3.0-generate-preview")

# Multimedia Content Limits  
MAX_IMAGES_PER_RESPONSE = int(os.getenv("MAX_IMAGES_PER_RESPONSE", "3"))
MAX_VIDEOS_PER_RESPONSE = int(os.getenv("MAX_VIDEOS_PER_RESPONSE", "1"))
MULTIMEDIA_GENERATION_TIMEOUT = int(os.getenv("MULTIMEDIA_GENERATION_TIMEOUT", "300"))  # 5 minutes
MAX_MULTIMEDIA_SIZE_MB = int(os.getenv("MAX_MULTIMEDIA_SIZE_MB", "50"))

# Feature Flags (you can disable these if needed)
ENABLE_IMAGE_GENERATION = os.getenv("ENABLE_IMAGE_GENERATION", "true").lower() == "true"
ENABLE_VIDEO_GENERATION = os.getenv("ENABLE_VIDEO_GENERATION", "true").lower() == "true"
ENABLE_MULTIMEDIA_FOR_STUDENTS_ONLY = os.getenv("ENABLE_MULTIMEDIA_FOR_STUDENTS_ONLY", "true").lower() == "true"
# --- Profiling ---
ENABLE_PROFILING = os.getenv("ENABLE_PROFILING", "false").lower() == "true" # Default to False for production speed

# Chain of Thoughts Settings
COT_MAX_COMPONENTS = int(os.getenv("COT_MAX_COMPONENTS", "6"))
COT_MAX_STEPS = int(os.getenv("COT_MAX_STEPS", "8"))
COT_REASONING_TEMPERATURE = float(os.getenv("COT_REASONING_TEMPERATURE", "0.2"))

# High-priority concepts that should trigger multimedia
HIGH_PRIORITY_CONCEPTS = [
    "gradient descent", "neural network", "deep learning", "algorithm", 
    "cybersecurity", "machine learning", "data structure", "encryption",
    "backpropagation", "optimization", "classification", "regression"
]

VISUALIZATION_TRIGGERS = [
    "how does", "how it works", "step by step", "process", "algorithm",
    "explain", "visualize", "show me", "demonstrate", "what is"
]

# Cache directories (optional)
MULTIMEDIA_CACHE_DIR = PROJECT_ROOT / "cache" / "multimedia"
MULTIMEDIA_TEMP_DIR = PROJECT_ROOT / "temp" / "multimedia"

# Classifier Toggle
fast = "Local"
LLM = "Gemini"
INTENT_CLASSIFIER_MODE = os.getenv("INTENT_CLASSIFIER_MODE", "fast")

# Path to store downloaded models
MODELS_CACHE_DIR = PROJECT_ROOT / "models"
if not MODELS_CACHE_DIR.exists():
    MODELS_CACHE_DIR.mkdir(parents=True)

# =========================================================
# NEURO-SYMBOLIC HYPERPARAMETERS (\tau)
# =========================================================
# Security & Semantic Radius
TAU_BASE = 0.7        # Base acceptable cosine similarity for off-script queries
ALPHA = 0.1           # Decay rate for mastery-adaptive security radius

# Affective & Cognitive Overload
TAU_DELTA_F = 0.1     # Threshold for escalating frustration (ΔF)
TAU_RAGE = 0.85       # Absolute threshold for rage state (F_t)
TAU_COMP = 5          # AST depth / code complexity threshold

# Attention & Debt Ceilings
TAU_STRIKE = 3        # Max off-topic strikes before Attention Hijacking block
TAU_QUEUE = 5         # Max pending micro-challenges before Cognitive Debt block
K_MIN = 3             # Cold-start threshold for Few-Shot Personalization

# Utility function to check if query should trigger multimedia
def should_generate_multimedia(query: str, user_role: str, query_domain: str) -> bool:
    """
    Determines if a query should trigger multimedia generation.
    """
    if not ENABLE_MULTIMEDIA_FOR_STUDENTS_ONLY or user_role != 'student':
        return False
        
    if query_domain != "STEM":
        return False
        
    if not (ENABLE_IMAGE_GENERATION or ENABLE_VIDEO_GENERATION):
        return False
    
    query_lower = query.lower()
    
    # Check for high-priority concepts
    has_priority_concept = any(concept in query_lower for concept in HIGH_PRIORITY_CONCEPTS)
    
    # Check for visualization triggers
    has_viz_trigger = any(trigger in query_lower for trigger in VISUALIZATION_TRIGGERS)
    
    return has_priority_concept or has_viz_trigger

def get_multimedia_settings() -> Dict[str, Any]:
    """
    Returns current multimedia generation settings.
    """
    return {
        "image_generation_enabled": ENABLE_IMAGE_GENERATION,
        "video_generation_enabled": ENABLE_VIDEO_GENERATION,
        "max_images": MAX_IMAGES_PER_RESPONSE,
        "max_videos": MAX_VIDEOS_PER_RESPONSE,
        "max_size_mb": MAX_MULTIMEDIA_SIZE_MB,
        "timeout_seconds": MULTIMEDIA_GENERATION_TIMEOUT,
        "google_api_available": bool(GOOGLE_API_KEY),
        "veo_model": VEO3_MODEL
    }