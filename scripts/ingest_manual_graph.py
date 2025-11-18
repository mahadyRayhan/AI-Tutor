# scripts/ingest_manual_graph.py
import json
import os
import sys
from pathlib import Path

# Add backend to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'backend'))

from app.core import config
from app.core.utils import setup_logging
from app.db.graph_db import Neo4jGraphDB

def sanitize_label(label: str) -> str:
    """
    Neo4j labels cannot have spaces (unless escaped). 
    We replace spaces with underscores (e.g. 'Control Flow' -> 'Control_Flow')
    """
    return label.replace(" ", "_")

def main():
    logger = setup_logging("INFO", "manual_ingest.log")
    graph = Neo4jGraphDB(logger)
    
    # Path to your JSON file
    json_path = config.PROJECT_ROOT / "resources" / "metadata" / "c_knowledge_graph.json"
    
    if not json_path.exists():
        logger.error(f"JSON file not found at {json_path}")
        return

    with open(json_path, 'r') as f:
        data = json.load(f)
        
    logger.info(f"Ingesting {len(data)} manual entities...")
    
    for item in data:
        source_name = item['entity']
        # FIX: Sanitize the label name
        source_type = sanitize_label(item['type'])
        
        # 1. Create the Source Node
        # We use an f-string for the Label (source_type) because labels cannot be parameters in Neo4j
        try:
            graph.execute_query(
                f"MERGE (n:{source_type} {{name: $name}})",
                {"name": source_name}
            )
            
            # 2. Create Relationships
            for rel in item.get('relationships', []):
                target_name = rel['target']
                relation_type = rel['relation']
                
                # We match source, MERGE target (generically as Concept if unknown), and link
                # Note: We reuse the sanitized source_type here
                query = f"""
                MATCH (s:{source_type} {{name: $source}})
                MERGE (t:Concept {{name: $target}}) 
                MERGE (s)-[:{relation_type}]->(t)
                """
                graph.execute_query(query, {"source": source_name, "target": target_name})
                
        except Exception as e:
            logger.error(f"Failed to ingest item '{source_name}': {e}")
            
    logger.info("Manual Graph Ingestion Complete.")
    graph.close()

if __name__ == "__main__":
    main()