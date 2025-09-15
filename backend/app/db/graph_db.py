# backend/app/db/graph_db.py

import logging
from neo4j import GraphDatabase
from typing import List, Dict, Any
from tenacity import retry, stop_after_attempt, wait_random_exponential

# Use the centralized config now
from app.core import config

class Neo4jGraphDB:
    """Manages connection and interactions with a Neo4j Knowledge Graph."""

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.driver = None
        try:
            # Load credentials from the config file
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
                session.run("CREATE CONSTRAINT author_name IF NOT EXISTS FOR (a:Author) REQUIRE a.name IS UNIQUE")
                self.logger.info("Ensured unique constraints on Document, Chunk, Entity, and Author nodes.")
            except Exception as e:
                self.logger.error(f"Error creating Neo4j constraints: {e}")

    @retry(wait=wait_random_exponential(multiplier=1, min=2, max=30), stop=stop_after_attempt(3))
    def execute_query(self, query: str, parameters: Dict[str, Any] = None):
        """Executes a Cypher query with retry logic."""
        with self.driver.session(database="neo4j") as session:
            try:
                result = session.run(query, parameters)
                return [record.data() for record in result]
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
            c.access_level = chunk_data.metadata.access_level
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
        MERGE (e:Entity {id: entity_data.type + ':' + toLower(entity_data.name)})
        ON CREATE SET e.name = entity_data.name, e.type = entity_data.type
        MERGE (c)-[:MENTIONS]->(e)
        """
        parameters = {"chunk_id": chunk_id, "entities": entities}
        self.execute_query(query, parameters)
        self.logger.debug(f"Linked chunk {chunk_id} to {len(entities)} entities.")