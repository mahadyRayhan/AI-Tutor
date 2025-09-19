# backend/app/processing/data_processing.py

import os
import re
import glob
import json
import logging
import hashlib
from typing import List, Dict, Any

from langchain_text_splitters import RecursiveCharacterTextSplitter
from unstructured.partition.auto import partition
from unstructured.documents.elements import Table, Image

# Adjusted imports for the new structure
from app.core import config
from app.db.graph_db import Neo4jGraphDB
from app.db.llm_interface import LLMInterface

logger = logging.getLogger(__name__)

def _create_chunk_id(doc_name: str, element_idx: int, chunk_idx: int) -> str:
    """Creates a unique ID for a chunk based on its document and position."""
    return f"{os.path.splitext(doc_name)[0]}_elem{element_idx}_chunk{chunk_idx}"

def _extract_entities_from_chunk(chunk_text: str, llm_interface: LLMInterface) -> List[Dict[str, str]]:
    """Uses an LLM to extract key entities from a text chunk."""
    
    # --- IMPROVED GENERALIZED PROMPT ---
    prompt = f"""
    You are a precise data extraction tool. Your job is to extract key named entities from the text provided.
    From the text below, extract important entities of the following types: "Person", "Organization", "Location", "Date", "Technology", "Concept", or "Event".

    **CRITICAL INSTRUCTIONS:**
    1.  Analyze the text to find entities matching the types.
    2.  If you find entities, format them as a valid JSON list of objects. Each object must have a "type" and a "name".
    3.  **If you find NO entities, you MUST output an empty JSON list: `[]`.**
    4.  **DO NOT output any other text or explanations. Your entire response must be ONLY the JSON or the empty list.**

    **EXAMPLE 1 (History Text):**
    Text: "Established in 1839, the University of Missouri was the first public university west of the Mississippi River."
    Your Output:
    ```json
    [
        {{"type": "Date", "name": "1839"}},
        {{"type": "Organization", "name": "University of Missouri"}},
        {{"type": "Location", "name": "Mississippi River"}}
    ]
    ```

    **EXAMPLE 2 (Cybersecurity Text):**
    Text: "A SYN Flood is a type of DDoS attack that targets Layer 4 of the OSI model."
    Your Output:
    ```json
    [
        {{"type": "Concept", "name": "SYN Flood"}},
        {{"type": "Technology", "name": "DDoS"}},
        {{"type": "Concept", "name": "OSI model Layer 4"}}
    ]
    ```

    **Now, analyze the following text and provide ONLY the JSON output.**

    Text:
    ---
    {chunk_text}
    ---

    JSON Output:
    """
    # --- END OF CHANGE ---

    response_str = llm_interface.generate_response(prompt)
    try:
        match = re.search(r"```json\s*([\s\S]*?)\s*```", response_str)
        if match:
            json_str = match.group(1)
        else:
            json_str = response_str.strip()
            
        if json_str.startswith('[') and json_str.endswith(']'):
            entities = json.loads(json_str)
            if isinstance(entities, list):
                validated_entities = [e for e in entities if isinstance(e, dict) and "type" in e and "name" in e]
                if validated_entities:
                    logger.info(f"Successfully extracted {len(validated_entities)} entities.")
                return validated_entities
        return []
    except (json.JSONDecodeError, TypeError):
        logger.warning(f"Failed to decode LLM response for entity extraction. Raw: {response_str[:150]}...")
        return []

# --- REMOVED THE _classify_chunk_difficulty FUNCTION ---

def process_and_chunk_documents(
    resource_paths: List[str],
    graph_db: Neo4jGraphDB,
    embedding_interface: LLMInterface,
    logger_parent: logging.Logger
) -> List[Dict[str, Any]]:
    """
    Parses documents from specified paths, assigns access levels, chunks them,
    and populates the knowledge graph and a list for the vector store.
    """
    global logger
    logger = logger_parent

    logger.info("Starting multi-modal document processing and chunking...")
    
    all_document_files = []
    for path in resource_paths:
        all_document_files.extend(glob.glob(os.path.join(path, "**", "*"), recursive=True))
    
    document_files = [f for f in all_document_files if os.path.isfile(f) and not os.path.basename(f).startswith('~')]

    if not document_files:
        logger.error(f"No document files found in paths: {resource_paths}")
        return []

    all_chunks_for_vector_store: List[Dict[str, Any]] = []
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=config.DEFAULT_CHUNK_SIZE,
        chunk_overlap=config.DEFAULT_CHUNK_OVERLAP
    )

    for doc_path in document_files:
        doc_name = os.path.basename(doc_path)
        logger.info(f"Processing document: {doc_name}")

        access_level = 'student' 
        for teacher_folder in config.TEACHER_ONLY_FOLDERS:
            if os.path.abspath(doc_path).startswith(os.path.abspath(teacher_folder)):
                access_level = 'teacher'
                break
        logger.info(f"Document '{doc_name}' assigned access level: '{access_level}'")

        try:
            elements = partition(filename=doc_path, strategy="fast")
            
            doc_all_chunks_for_kg = []
            
            for i, element in enumerate(elements):
                element_type = type(element).__name__
                element_chunks = []

                if element_type == "Table":
                    element_chunks.append(f"Data Table: {str(element)}")
                elif element_type == "Image":
                    continue 
                else:
                    text = str(element)
                    if len(text.strip()) > config.MIN_CHUNK_LENGTH:
                        element_chunks.extend(text_splitter.split_text(text))

                for j, chunk_text in enumerate(element_chunks):
                    cleaned_chunk = chunk_text.strip()
                    if not cleaned_chunk:
                        continue
                    
                    chunk_id = _create_chunk_id(doc_name, i, j)
                    
                    # --- REMOVED DIFFICULTY CLASSIFICATION CALL ---
                    
                    chunk_metadata = {
                        "document_name": doc_name,
                        "chunk_id": chunk_id,
                        "element_type": element_type,
                        "page_number": getattr(element.metadata, 'page_number', None),
                        "access_level": access_level,
                    }

                    chunk_info = {"chunk_text": cleaned_chunk, "metadata": chunk_metadata}
                    doc_all_chunks_for_kg.append(chunk_info)
                    all_chunks_for_vector_store.append(chunk_info)

            if doc_all_chunks_for_kg:
                graph_db.add_document_and_chunks(doc_name, doc_all_chunks_for_kg)
                logger.info(f"Extracting and linking entities for {len(doc_all_chunks_for_kg)} chunks...")
                for chunk in doc_all_chunks_for_kg:
                    entities = _extract_entities_from_chunk(chunk['chunk_text'], embedding_interface)
                    if entities:
                        graph_db.link_chunk_to_entities(chunk['metadata']['chunk_id'], entities)

        except Exception as e:
            logger.exception(f"Failed to process file {doc_path}: {e}")
            
    logger.info(f"Finished processing. Total chunks for vector store: {len(all_chunks_for_vector_store)}")
    return all_chunks_for_vector_store