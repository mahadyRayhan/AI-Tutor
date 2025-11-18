# scripts/debug_retrieval.py
import sys
import os

# Add backend to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'backend'))

from app.core import config
from app.core.utils import setup_logging
from app.db.vector_store import ChromaVectorStore
from app.db.llm_interface import LLMInterface

def main():
    logger = setup_logging("INFO", "debug_retrieval.log")
    
    print("--- 1. Checking Configuration ---")
    print(f"Vector DB Path: {config.DEFAULT_VECTOR_DB_PATH}")
    
    # CHECK IF FOLDER EXISTS
    if not os.path.exists(config.DEFAULT_VECTOR_DB_PATH):
        print(f"❌ ERROR: The folder '{config.DEFAULT_VECTOR_DB_PATH}' does not exist.")
        print("   Ingestion failed or hasn't run yet.")
        return

    print("\n--- 2. Connecting to Vector DB ---")
    try:
        # FIX: Explicitly pass the persist_directory
        vector_store = ChromaVectorStore(
            persist_directory=config.DEFAULT_VECTOR_DB_PATH, 
            logger=logger
        )
        
        count = vector_store.collection.count()
        print(f"✅ SUCCESS: Connected to Persistent ChromaDB.")
        print(f"📊 Total Documents in Collection: {count}")
        
        if count == 0:
            print("\n❌ DATABASE EMPTY: The folder exists, but contains 0 documents.")
            return
            
    except Exception as e:
        print(f"❌ Connection Failed: {e}")
        return

    print("\n--- 3. Testing Search ---")
    try:
        llm = LLMInterface(logger=logger)
        query = "How do I declare a variable to count students?"
        print(f"Query: '{query}'")
        
        query_embedding = llm.get_embedding(query)
        results = vector_store.query(query_embedding, top_k=3)
        
        print(f"\nFound {len(results)} results:")
        for i, res in enumerate(results):
            meta = res.get('metadata', {})
            print(f"  Result {i+1}: {meta.get('document_name')} (Score: {res.get('score')})")
    except Exception as e:
        print(f"❌ Search Failed: {e}")

if __name__ == "__main__":
    main()