# backend/app/core/config.py

import os
from dotenv import load_dotenv
from pathlib import Path

# --- THE CRITICAL FIX: DEFINE THE PROJECT ROOT ---
# This makes all paths absolute and independent of the current working directory.
# It navigates up three levels from this file (core -> app -> backend -> AI-Tutor/)
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Load environment variables from the .env file at the project root
dotenv_path = PROJECT_ROOT / '.env'
load_dotenv(dotenv_path=dotenv_path)

# --- API Configuration ---
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
OPENAI_API_KEY = os.getenv("GPT_API_KEY")

if not GOOGLE_API_KEY:
    print("Warning: GOOGLE_API_KEY environment variable not set.")
if not OPENAI_API_KEY:
    print("Warning: OPENAI_API_KEY environment variable not set.")

# --- Model IDs ---
DEFAULT_GENERATIVE_LLM_PROVIDER = os.getenv("LLM_PROVIDER", "google")
DEFAULT_GOOGLE_MODEL_ID = "gemini-2.5-flash"
DEFAULT_GOOGLE_EMBEDDING_MODEL = "text-embedding-004"
DEFAULT_OPENAI_CHAT_MODEL = "gpt-4-turbo"
DEFAULT_OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"

# --- Default Paths (Now built from PROJECT_ROOT) ---
RESOURCES_DIR = PROJECT_ROOT / "resources"
TEACHING_RESOURCES_PATH = RESOURCES_DIR / "teaching"
COURSE_EVALUATION_RESOURCES_PATH = RESOURCES_DIR / "course_evaluation"

TEACHER_ONLY_FOLDERS = [str(COURSE_EVALUATION_RESOURCES_PATH)]

DB_DIR = PROJECT_ROOT / "database"
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

# --- Caching ---
DEFAULT_USE_EMBEDDING_CACHE = True
DEFAULT_USE_LLM_CACHE = True

# --- Generation Configs ---
DEFAULT_GOOGLE_GENERATION_CONFIG = { "temperature": 0.3, "top_p": 0.95, "top_k": 40, "max_output_tokens": 8192, "response_mime_type": "text/plain" }
DEFAULT_OPENAI_GENERATION_CONFIG = { "temperature": 0.3, "max_tokens": 4096 }

# --- Context Management ---
CHAR_TO_TOKEN_RATIO = 3.5
MAX_CONTEXT_TOKENS = 16000
SIMILARITY_THRESHOLD = 0.95
MIN_CHUNK_LENGTH = 50

# --- Neo4j Graph Database ---
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")