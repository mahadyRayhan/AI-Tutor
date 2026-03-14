# backend/app/db/graph_db.py

import logging
from neo4j import GraphDatabase
from typing import List, Dict, Any
from tenacity import retry, stop_after_attempt, wait_random_exponential

from app.core import config

class Neo4jGraphDB:
    """Manages connection and interactions with a Neo4j Knowledge Graph."""

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.driver = None
        try:
            self.driver = GraphDatabase.driver(
                config.NEO4J_URI,
                auth=(config.NEO4J_USER, config.NEO4J_PASSWORD)
            )
            self.driver.verify_connectivity()
            self.logger.info(f"Successfully connected to Neo4j database at {config.NEO4J_URI}.")
            self._create_constraints()
        except Exception as e:
            self.logger.error(f"Failed to connect to Neo4j at {config.NEO4J_URI}: {e}")
            raise

    def close(self):
        if self.driver:
            self.driver.close()
            self.logger.info("Neo4j connection closed.")

    def _create_constraints(self):
        """Creates unique constraints on nodes for better performance and data integrity."""
        with self.driver.session(database="neo4j") as session:
            try:
                session.run("CREATE CONSTRAINT document_name IF NOT EXISTS FOR (d:Document) REQUIRE d.name IS UNIQUE")
                session.run("CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (c:Chunk) REQUIRE c.id IS UNIQUE")
                session.run("CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE")
                # Add text index for better entity searching
                session.run("CREATE INDEX entity_name_text IF NOT EXISTS FOR (e:Entity) ON (e.name)")
                self.logger.info("Ensured unique constraints and indexes on Document, Chunk, and Entity nodes.")
            except Exception as e:
                self.logger.error(f"Error creating Neo4j constraints: {e}")

    @retry(
        wait=wait_random_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3)
    )
    def execute_query(self, query: str, parameters: Dict[str, Any] = None):
        """Executes a Cypher query with retry logic."""
        with self.driver.session(database="neo4j") as session:
            try:
                def _run_query(tx):
                    result = tx.run(query, parameters)
                    return [record.data() for record in result]

                return session.execute_write(_run_query)
            except Exception as e:
                self.logger.error(f"Error executing Cypher query: {e}\nQuery: {query}")
                raise

    def add_document_and_chunks(self, doc_name: str, chunks: List[Dict[str, Any]]):
        """Adds a Document node and its associated Chunk nodes to the graph."""
        query = """
        MERGE (d:Document {name: $doc_name})
        WITH d
        UNWIND $chunks as chunk_data
        MERGE (c:Chunk {id: chunk_data.metadata.chunk_id})
        ON CREATE SET
            c.text = chunk_data.chunk_text,
            c.element_type = chunk_data.metadata.element_type,
            c.access_level = chunk_data.metadata.access_level,
            c.content_domain = chunk_data.metadata.content_domain
        ON MATCH SET
            c.content_domain = chunk_data.metadata.content_domain
        MERGE (d)-[:HAS_CHUNK]->(c)
        """
        parameters = {"doc_name": doc_name, "chunks": chunks}
        self.execute_query(query, parameters)
        self.logger.info(f"Added/updated Document '{doc_name}' and its {len(chunks)} chunks in the graph.")

    def link_chunk_to_entities(self, chunk_id: str, entities: List[Dict[str, str]]):
        """Links a chunk to extracted entities."""
        if not entities:
            return

        query = """
        MATCH (c:Chunk {id: $chunk_id})
        WITH c
        UNWIND $entities as entity_data
        MERGE (e:Entity {id: toLower(entity_data.type + ':' + entity_data.name)})
        ON CREATE SET e.name = entity_data.name, e.type = entity_data.type
        MERGE (c)-[:MENTIONS]->(e)
        """
        parameters = {"chunk_id": chunk_id, "entities": entities}
        self.execute_query(query, parameters)
        self.logger.debug(f"Linked chunk {chunk_id} to {len(entities)} entities.")

    def add_relationships(self, relationships: List[Dict[str, Any]]):
        """Adds relationships between existing entities in the graph idempotently."""
        if not relationships:
            return

        query_apoc = """
        UNWIND $relationships AS rel
        MATCH (source:Entity {id: toLower(rel.source_type + ':' + rel.source)})
        MATCH (target:Entity {id: toLower(rel.target_type + ':' + rel.target)})
        CALL apoc.merge.relationship(source, rel.type, {}, {}, target)
        YIELD rel as createdRel
        RETURN count(createdRel)
        """

        processed_rels = []
        for rel in relationships:
            if all(k in rel for k in ['source', 'target', 'type']):
                 processed_rels.append({
                    **rel,
                    'source_type': rel.get('source_type', 'Concept'),
                    'target_type': rel.get('target_type', 'Concept'),
                 })

        if not processed_rels:
            return

        parameters = {"relationships": processed_rels}
        self.execute_query(query_apoc, parameters)
        self.logger.info(f"Merged {len(processed_rels)} relationships into the knowledge graph.")

    def get_context_for_entities(self, entity_names: List[str]) -> List[Dict[str, Any]]:
        """
        Enhanced entity matching with fuzzy search and related entity traversal.
        """
        if not entity_names: 
            return []
            
        query = """
        UNWIND $entity_names AS entityName
        // Direct name matches (exact and partial)
        MATCH (e:Entity)-[:MENTIONS]-(c:Chunk)-[:HAS_CHUNK]-(d:Document)
        WHERE toLower(e.name) CONTAINS toLower(entityName) 
           OR toLower(entityName) CONTAINS toLower(e.name)
           OR ANY(word IN split(toLower(e.name), ' ') WHERE word CONTAINS toLower(entityName))
           OR ANY(word IN split(toLower(entityName), ' ') WHERE toLower(e.name) CONTAINS word)
        
        OPTIONAL MATCH (e)-[:RELATIONSHIP*1..2]-(related:Entity)-[:MENTIONS]-(related_chunk:Chunk)-[:HAS_CHUNK]-(related_doc:Document)
        
        WITH COLLECT(DISTINCT {
            text: c.text, 
            chunk_id: c.id, 
            document_name: d.name,
            element_type: c.element_type, 
            access_level: c.access_level,
            match_type: 'direct'
        }) + COLLECT(DISTINCT {
            text: related_chunk.text, 
            chunk_id: related_chunk.id, 
            document_name: related_doc.name,
            element_type: related_chunk.element_type, 
            access_level: related_chunk.access_level,
            match_type: 'related'
        }) AS all_chunks
        
        UNWIND all_chunks AS chunk_info
        RETURN DISTINCT chunk_info.text AS text, 
               chunk_info.chunk_id AS chunk_id, 
               chunk_info.document_name AS document_name,
               chunk_info.element_type AS element_type, 
               chunk_info.access_level AS access_level
        LIMIT 50
        """
        
        parameters = {"entity_names": entity_names}
        results = self.execute_query(query, parameters)
        
        formatted_results = [
            {
                "text": record['text'],
                "metadata": {
                    "chunk_id": record['chunk_id'],
                    "document_name": record['document_name'],
                    "element_type": record['element_type'],
                    "access_level": record['access_level']
                }
            } for record in results if record['text']  # Ensure text is not None
        ]
        
        self.logger.info(f"Retrieved {len(formatted_results)} chunks for entities: {entity_names}")
        return formatted_results

    def find_chunks_by_entities(self, entity_names: List[str]) -> List[Dict[str, Any]]:
        """Finds all chunk texts and metadata linked to a list of entity names."""
        return self.get_context_for_entities(entity_names)
    
    def search_chunks_by_text_content(self, search_terms: List[str]) -> List[Dict[str, Any]]:
        """
        Direct text search in chunk content for fallback retrieval.
        """
        if not search_terms:
            return []
            
        query = """
        UNWIND $search_terms AS term
        MATCH (c:Chunk)-[:HAS_CHUNK]-(d:Document)
        WHERE toLower(c.text) CONTAINS toLower(term)
        RETURN DISTINCT c.text AS text, 
               c.id AS chunk_id, 
               d.name AS document_name,
               c.element_type AS element_type, 
               c.access_level AS access_level
        LIMIT 20
        """
        
        parameters = {"search_terms": search_terms}
        results = self.execute_query(query, parameters)
        
        return [
            {
                "text": record['text'],
                "metadata": {
                    "chunk_id": record['chunk_id'],
                    "document_name": record['document_name'],
                    "element_type": record['element_type'],
                    "access_level": record['access_level']
                }
            } for record in results if record['text']
        ]

    def get_learning_path(self, target_goal: str, known_concepts: List[str]) -> List[Dict[str, str]]:
        """
        Calculates a prerequisite learning path to reach the target_goal.
        Returns an ordered list of concepts with statuses: mastered, next, or locked.
        """
        # Query to find all prerequisite paths up to 5 levels deep
        query = """
        MATCH path = (goal:Entity)-[:REQUIRES_UNDERSTANDING_OF*1..5]->(prereq:Entity)
        WHERE toLower(goal.name) = toLower($goal)
        // Calculate depth (distance from goal) to establish learning order
        RETURN prereq.name AS concept, length(path) as depth
        ORDER BY depth DESC
        """
        parameters = {"goal": target_goal}
        results = self.execute_query(query, parameters)
        
        # Extract unique concepts in reverse-dependency order (deepest prerequisites first)
        ordered_concepts = []
        seen = set()
        for record in results:
            concept = record['concept']
            if concept not in seen:
                ordered_concepts.append(concept)
                seen.add(concept)
                
        # Always include the goal itself at the end
        target_display_name = target_goal.title()
        if not ordered_concepts:
            target_lower = target_goal.lower()
            if target_lower in ['function', 'functions']:
                ordered_concepts = ['Variables', 'Control Flow']
            elif target_lower in ['pointer', 'pointers']:
                ordered_concepts = ['Variables', 'Control Flow', 'Functions', 'Memory Allocation']
            elif target_lower in ['array', 'arrays']:
                ordered_concepts = ['Variables', 'Control Flow']
            elif target_lower in ['structure', 'structures', 'struct']:
                ordered_concepts = ['Variables', 'Arrays']
            else:
                ordered_concepts = ['Fundamentals']
        
        # Ensure the final goal is the last step
        if target_display_name not in [c.title() for c in ordered_concepts]:
            ordered_concepts.append(target_display_name)
            
        # State mapping: mastered, next, locked
        known_lower = [k.lower() for k in known_concepts]
        
        path_result = []
        found_next = False
        
        for concept in ordered_concepts:
            status = ""
            if concept.lower() in known_lower:
                status = "mastered"
            elif not found_next:
                status = "next"
                found_next = True
            else:
                status = "locked"
                
            path_result.append({
                "concept": concept,
                "status": status
            })
            
        return path_result