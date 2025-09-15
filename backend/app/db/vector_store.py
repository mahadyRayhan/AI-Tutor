# backend/app/db/vector_store.py

import os
import logging
import chromadb
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

# Adjusted imports
from app.db.llm_interface import LLMInterface
from app.processing.data_processing import process_and_chunk_documents
from app.core import config
from app.db.graph_db import Neo4jGraphDB

class VectorStore(ABC):
    """Abstract base class for vector stores."""
    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self._is_ready = False

    @abstractmethod
    def load_or_build(self, resource_paths: List[str], embedding_interface: LLMInterface, graph_db: Neo4jGraphDB) -> bool:
        pass

    @abstractmethod
    def query(self, query_embedding: List[float], top_k: int, where_filter: Optional[Dict] = None) -> List[Dict[str, Any]]:
        pass

    def is_ready(self) -> bool:
        return self._is_ready

    def _build_index_internal(self, resource_paths: List[str], embedding_interface: LLMInterface, graph_db: Neo4jGraphDB) -> Optional[List[Dict[str, Any]]]:
        self.logger.info("Starting internal index build process...")

        all_chunks_info = process_and_chunk_documents(
            resource_paths=resource_paths,
            graph_db=graph_db,
            embedding_interface=embedding_interface,
            logger_parent=self.logger
        )

        if not all_chunks_info:
            self.logger.error("No chunks extracted from documents. Index build failed.")
            return None

        chunk_texts = [info["chunk_text"] for info in all_chunks_info]
        self.logger.info(f"Generating embeddings for {len(chunk_texts)} chunks...")
        all_embeddings = embedding_interface.batch_get_embeddings(chunk_texts, task_type="RETRIEVAL_DOCUMENT")

        processed_chunks_data = []
        for i, chunk_info in enumerate(all_chunks_info):
            if i < len(all_embeddings) and all_embeddings[i]:
                processed_chunks_data.append({**chunk_info, "embeddings": all_embeddings[i]})
            else:
                self.logger.warning(f"Failed to get valid embedding for chunk {chunk_info.get('metadata', {}).get('chunk_id')}. Skipping.")

        if not processed_chunks_data:
            self.logger.error("No chunks were successfully embedded. Index build failed.")
            return None

        self.logger.info(f"Successfully embedded {len(processed_chunks_data)} chunks.")
        return processed_chunks_data

class ChromaVectorStore(VectorStore):
    """Vector store using ChromaDB, supporting metadata filtering."""
    def __init__(self, collection_name: str = "ai_tutor_collection", persist_directory: Optional[str] = None, logger: logging.Logger = logging.getLogger("AITutor")):
        super().__init__(logger)
        self.collection_name = collection_name
        self.persist_directory = persist_directory
        self._initialize_chroma_client()

    def _initialize_chroma_client(self):
        try:
            if self.persist_directory:
                os.makedirs(self.persist_directory, exist_ok=True)
                self.chroma_client = chromadb.PersistentClient(path=self.persist_directory)
                self.logger.info(f"Initializing ChromaDB with persistence at: {self.persist_directory}")
            else:
                self.chroma_client = chromadb.Client()
                self.logger.info("Initializing ChromaDB in-memory.")

            self.collection = self.chroma_client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"}
            )
            self.logger.info(f"ChromaDB client connected. Using collection: '{self.collection_name}'")
            
            # --- START OF THE FIX ---
            # The store is considered "ready" as soon as the client and collection are available.
            # The responsibility of checking if it's populated lies with the ingestion script
            # and the application logic, not the store's basic initialization.
            self._is_ready = True
            self.logger.info(f"ChromaVectorStore is ready. Documents in collection: {self.collection.count()}")
            # --- END OF THE FIX ---
            
            if self.collection.count() > 0:
                self.logger.info(f"Chroma collection already contains {self.collection.count()} documents.")
                self._is_ready = True
        except Exception as e:
            self.logger.exception(f"ChromaDB setup error: {e}")
            raise

    def _populate_chroma(self, processed_chunks: List[Dict[str, Any]]):
        if not self.collection or not processed_chunks:
            return False
        
        ids, embeddings, documents, metadatas = [], [], [], []
        for chunk in processed_chunks:
            ids.append(chunk['metadata']['chunk_id'])
            embeddings.append(chunk['embeddings'])
            documents.append(chunk['chunk_text'])
            # Chroma can only store basic types in metadata (str, int, float, bool)
            # Ensure all values are of these types.
            clean_metadata = {k: v for k, v in chunk['metadata'].items() if isinstance(v, (str, int, float, bool))}
            metadatas.append(clean_metadata)

        if not ids:
            return False
        
        # Add to collection in batches
        batch_size = 100
        for i in range(0, len(ids), batch_size):
            self.collection.add(
                ids=ids[i:i+batch_size],
                embeddings=embeddings[i:i+batch_size],
                documents=documents[i:i+batch_size],
                metadatas=metadatas[i:i+batch_size]
            )
        self.logger.info(f"Finished populating ChromaDB. Added {len(ids)} documents.")
        return True

    def load_or_build(self, resource_paths: List[str], embedding_interface: LLMInterface, graph_db: Neo4jGraphDB) -> bool:
        if self.collection.count() > 0:
            self.logger.info(f"ChromaDB collection already populated with {self.collection.count()} documents.")
            self._is_ready = True
            return True

        self.logger.info("ChromaDB collection is empty. Building index...")
        processed_chunks = self._build_index_internal(resource_paths, embedding_interface, graph_db)
        if processed_chunks:
            populated = self._populate_chroma(processed_chunks)
            self._is_ready = populated
            return populated
        else:
            self.logger.error("Failed to build index (no processed chunks).")
            self._is_ready = False
            return False

    def query(self, query_embedding: List[float], top_k: int, where_filter: Optional[Dict] = None) -> List[Dict[str, Any]]:
        if not self.is_ready():
            self.logger.warning("ChromaDB is not ready.")
            return []
        
        # --- ADD A CHECK FOR EMPTY COLLECTION ---
        # This is a better place for this check than the readiness flag.
        if self.collection.count() == 0:
            self.logger.warning("Query attempted on an empty ChromaDB collection.")
            return []
        
        try:
            self.logger.info(f"Querying Chroma with top_k={top_k} and filter: {where_filter}")
            # --- START OF FIX ---
            # Build the arguments for the query dynamically.
            # This ensures we don't pass `where={}` to ChromaDB.
            query_args = {
                "query_embeddings": [query_embedding],
                "n_results": top_k,
                "include": ["documents", "metadatas", "distances"]
            }
            if where_filter:  # Only add the 'where' key if the filter is not None or empty
                query_args["where"] = where_filter

            results = self.collection.query(**query_args)
            # --- END OF FIX ---
            
            formatted_results = []
            if not results or not results.get("ids") or not results["ids"][0]:
                return []
            
            for i, chunk_id in enumerate(results["ids"][0]):
                similarity_score = 1.0 - results["distances"][0][i]
                formatted_results.append({
                    "id": chunk_id,
                    "text": results["documents"][0][i],
                    "metadata": results["metadatas"][0][i],
                    "score": float(similarity_score)
                })
            return formatted_results
        except Exception as e:
            self.logger.exception(f"An error occurred while querying ChromaDB: {e}")
            return []

def get_vector_store(vector_db_type: str, vector_db_path: Optional[str], logger: logging.Logger) -> VectorStore:
    db_type = vector_db_type.lower()
    if db_type == "chroma":
        logger.info("Creating ChromaDB Vector Store")
        return ChromaVectorStore(persist_directory=vector_db_path, logger=logger)
    elif db_type == "inmemory":
        logger.info("Creating InMemory ChromaDB Vector Store")
        return ChromaVectorStore(persist_directory=None, logger=logger)
    else:
        raise ValueError(f"Unknown or unsupported vector database type: {vector_db_type}")