# backend/app/dependencies.py

import logging
from functools import lru_cache

# Adjusted imports
from app.core import config
from app.core.utils import setup_logging
from app.db.llm_interface import LLMInterface
from app.db.graph_db import Neo4jGraphDB
from app.db.vector_store import get_vector_store
from app.agents.rag_agent import RAGAgent
from app.agents.image_agent import ImageAgent
from app.orchestrator import Orchestrator

# This is a global variable that will hold our initialized system.
# It's a simple way to ensure it's a singleton for the app's lifetime.
_orchestrator_instance = None

def _initialize_system():
    """Initializes all components of the RAG system."""
    global _orchestrator_instance
    if _orchestrator_instance:
        return _orchestrator_instance
        
    logger = setup_logging(config.DEFAULT_LOG_LEVEL, config.DEFAULT_LOG_FILE_PATH)
    logger.info("--- Initializing AI Tutor System for API ---")

    try:
        llm_interface = LLMInterface(
            llm_provider=config.DEFAULT_GENERATIVE_LLM_PROVIDER,
            google_api_key=config.GOOGLE_API_KEY,
            openai_api_key=config.OPENAI_API_KEY,
            logger=logger
        )
        
        graph_db = Neo4jGraphDB(logger=logger)
        
        vector_store = get_vector_store(
            vector_db_type=config.DEFAULT_VECTOR_DB_TYPE,
            vector_db_path=config.DEFAULT_VECTOR_DB_PATH,
            logger=logger
        )
        
        if vector_store.collection.count() == 0:
             logger.warning("Vector store collection is empty. The application will run but may not find documents.")
             logger.warning("Run the ingestion script (`scripts/ingest_data.py`) to build the knowledge base.")
        
        # IMPORTANT: Data ingestion should be done offline via a script, not here.
        # We assume the vector store is already built.
        if not vector_store.is_ready():
             logger.warning("Vector store is not populated. The application will run but may not find documents.")
             logger.warning("Run the ingestion script (`scripts/ingest_data.py`) to build the knowledge base.")

        # Initialize Agents
        rag_agent = RAGAgent(llm_interface, vector_store, graph_db, logger)
        image_agent = ImageAgent(llm_interface, logger)
        
        # Initialize Orchestrator
        _orchestrator_instance = Orchestrator(llm_interface, rag_agent, image_agent, logger)
        
        logger.info("--- AI Tutor System Initialized Successfully ---")
        return _orchestrator_instance
    except Exception as e:
        logging.getLogger(__name__).exception(f"Fatal error during system initialization: {e}")
        raise

# This is the dependency that FastAPI will use.
# @lru_cache is not strictly needed with our global pattern but is good practice.
@lru_cache()
def get_orchestrator() -> Orchestrator:
    return _initialize_system()