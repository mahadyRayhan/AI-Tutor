# scripts/ingest_data.py

import os
import sys

# This allows the script to import modules from the 'backend' directory
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'backend'))

from app.core import config
from app.core.utils import setup_logging
from app.db.llm_interface import LLMInterface
from app.db.graph_db import Neo4jGraphDB
from app.db.vector_store import get_vector_store

def main():
    """
    Main function to run the data ingestion process.
    """
    logger = setup_logging(config.DEFAULT_LOG_LEVEL, config.DEFAULT_LOG_FILE_PATH)
    logger.info("--- Starting Data Ingestion Script ---")

    try:
        llm_interface = LLMInterface(
            llm_provider=config.DEFAULT_GENERATIVE_LLM_PROVIDER,
            google_api_key=config.GOOGLE_API_KEY,
            openai_api_key=config.OPENAI_API_KEY,
            logger=logger
        )
        
        logger.info("Initializing Knowledge Graph connection...")
        graph_db = Neo4jGraphDB(logger=logger)

        vector_store = get_vector_store(
            vector_db_type=config.DEFAULT_VECTOR_DB_TYPE,
            vector_db_path=config.DEFAULT_VECTOR_DB_PATH,
            logger=logger
        )
        
        resource_paths = [config.CONCEPTS_PATH, config.CODE_PATH]
        
        logger.info(f"Loading and building vector store and knowledge graph from: {resource_paths}")
        
        # This function will process documents, chunk them, get embeddings,
        # and populate both the KG and the vector store.
        build_success = vector_store.load_or_build(
            resource_paths=resource_paths,
            embedding_interface=llm_interface,
            graph_db=graph_db
        )

        if build_success:
            logger.info("--- Data Ingestion Completed Successfully ---")
        else:
            logger.error("--- Data Ingestion Failed ---")

    except Exception as e:
        logger.exception(f"An error occurred during data ingestion: {e}")
    finally:
        if 'graph_db' in locals():
            graph_db.close()

if __name__ == "__main__":
    main()