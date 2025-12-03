# backend/app/processing/data_processing.py

import os
import re
import glob
import json
import logging
from typing import List, Dict, Any

# We use the Language-aware splitter for C code
from langchain_text_splitters import RecursiveCharacterTextSplitter, Language

from app.core import config
from app.db.graph_db import Neo4jGraphDB
from app.db.llm_interface import LLMInterface

logger = logging.getLogger(__name__)

def _create_chunk_id(doc_name: str, chunk_idx: int) -> str:
    sanitized = re.sub(r'[^a-zA-Z0-9_-]', '_', os.path.splitext(doc_name)[0])
    return f"{sanitized}_chunk{chunk_idx}"

def _extract_c_metadata(chunk_text: str, llm: LLMInterface) -> Dict[str, Any]:
    """
    Asks the LLM to identify Functions, Libraries, and Concepts in a C code snippet.
    """
    prompt = f"""
    Analyze this C programming code snippet. Extract the following strictly as JSON:
    1. "functions_defined": names of functions created in this code.
    2. "functions_called": names of functions used/called (e.g. printf, scanf).
    3. "libraries": header files included (e.g. stdio.h).
    4. "concepts": programming concepts present (e.g. Loops, Pointers, Arrays, Recursion).

    Code Snippet:
    ---
    {chunk_text}
    ---

    Respond ONLY with valid JSON:
    {{
        "functions_defined": [],
        "functions_called": [],
        "libraries": [],
        "concepts": []
    }}
    """
    response = llm.generate_response(prompt)
    
    # Basic cleanup to ensure we get JSON
    try:
        # Find JSON in response (in case LLM adds conversational filler)
        match = re.search(r'\{.*\}', response, re.DOTALL)
        if match:
            return json.loads(match.group(0))
    except Exception as e:
        logger.warning(f"Failed to parse LLM C-extraction: {e}")
    
    return {}

def _extract_concept_metadata(chunk_text: str, llm: LLMInterface) -> Dict[str, Any]:
    """
    Asks the LLM to identify C Concepts defined in a Markdown text.
    """
    prompt = f"""
    Analyze this educational text about C programming. 
    Identify the key "concepts" being explained (e.g. Variables, Memory, Structs).
    
    Text:
    ---
    {chunk_text}
    ---

    Respond ONLY with valid JSON:
    {{
        "concepts": []
    }}
    """
    response = llm.generate_response(prompt)
    try:
        match = re.search(r'\{.*\}', response, re.DOTALL)
        if match:
            return json.loads(match.group(0))
    except Exception as e:
        logger.warning(f"Failed to parse LLM Concept-extraction: {e}")
    
    return {}

def _determine_metadata(filename: str) -> Dict[str, str]:
    """
    Auto-tags content based on filename.
    """
    name = filename.lower()
    
    # 1. Determine Access Level
    # If file implies exam or solution, it's teacher only
    access_level = "student" 
    if "exam" in name or "solution" in name or "quiz" in name:
        access_level = "teacher"

    # 2. Determine Topic (Categories)
    topic = "General"
    if "array" in name: topic = "Arrays"
    elif "loop" in name or "control" in name or "switch" in name: topic = "Control Flow"
    elif "string" in name: topic = "Strings"
    elif "pointer" in name: topic = "Pointers"
    elif "function" in name: topic = "Functions"
    elif "var" in name or "type" in name: topic = "Variables"
    elif "struct" in name: topic = "Structures"
    
    return {"access_level": access_level, "topic": topic}

def process_and_chunk_documents(
    resource_paths: List[str],
    graph_db: Neo4jGraphDB,
    embedding_interface: LLMInterface,
    logger_parent: logging.Logger
) -> List[Dict[str, Any]]:
    """
    Iterates through files, chunks them for VectorDB, and extracts entities for GraphDB.
    """
    global logger
    logger = logger_parent
    logger.info("Starting C-Programming Data Ingestion...")

    all_files = []
    for path in resource_paths:
        # recursively find files
        files = glob.glob(os.path.join(path, "**", "*.*"), recursive=True)
        all_files.extend([f for f in files if os.path.isfile(f)])

    all_chunks_for_vector_store = []

    # 1. Configure Splitters
    # For C Code: Splits by function definitions and control structures
    c_splitter = RecursiveCharacterTextSplitter.from_language(
        language=Language.C, chunk_size=1000, chunk_overlap=100
    )
    # For Markdown: Splits by headers and paragraphs
    md_splitter = RecursiveCharacterTextSplitter.from_language(
        language=Language.MARKDOWN, chunk_size=1000, chunk_overlap=100
    )

    for file_path in all_files:
        filename = os.path.basename(file_path)
        ext = os.path.splitext(filename)[1].lower()
        
        logger.info(f"Processing: {filename}")
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # A. CHUNKING (Prepare for Vector DB)
            chunks = []
            file_type = "unknown"
            
            if ext == '.c':
                file_type = "code"
                chunks = c_splitter.create_documents([content])
            elif ext == '.md':
                file_type = "concept"
                chunks = md_splitter.create_documents([content])
            else:
                logger.info(f"Skipping unsupported file type: {filename}")
                continue

            # Prepare document node in Graph
            # We add the document first so chunks can link to it
            graph_db.execute_query(
                "MERGE (d:Document {name: $name, type: $type})",
                {"name": filename, "type": file_type}
            )

            # GET METADATA
            meta = _determine_metadata(filename) # <--- NEW CALL

            # B. PROCESSING EACH CHUNK
            for i, chunk_obj in enumerate(chunks):
                text = chunk_obj.page_content
                chunk_id = _create_chunk_id(filename, i)
                
                # 1. Prepare for Vector Store Return
                # The `vector_store.py` calls this function and expects this list back
                vector_record = {
                    "chunk_text": text,
                    "metadata": {
                        "chunk_id": chunk_id,
                        "document_name": filename,
                        "type": file_type,
                        "access_level": meta["access_level"], # <--- SAVED
                        "topic": meta["topic"]                # <--- SAVED
                    }
                }
                all_chunks_for_vector_store.append(vector_record)

                # 2. Add Chunk to Graph
                # Create Chunk Node and link to Document
                graph_db.execute_query("""
                    MATCH (d:Document {name: $doc_name})
                    MERGE (c:Chunk {id: $chunk_id})
                    SET c.text = $text
                    MERGE (d)-[:HAS_CHUNK]->(c)
                """, {"doc_name": filename, "chunk_id": chunk_id, "text": text})

                # 3. AUTOMATIC ENTITY EXTRACTION (The "Smart" Part)
                entities = {}
                if file_type == "code":
                    entities = _extract_c_metadata(text, embedding_interface)
                    
                    # Link Functions Defined
                    for func in entities.get('functions_defined', []):
                        graph_db.execute_query("""
                            MATCH (c:Chunk {id: $cid})
                            MERGE (f:Function {name: $name})
                            MERGE (c)-[:DEFINES]->(f)
                        """, {"cid": chunk_id, "name": func})

                    # Link Functions Called
                    for func in entities.get('functions_called', []):
                        graph_db.execute_query("""
                            MATCH (c:Chunk {id: $cid})
                            MERGE (f:Function {name: $name})
                            MERGE (c)-[:CALLS]->(f)
                        """, {"cid": chunk_id, "name": func})
                    
                    # Link Libraries
                    for lib in entities.get('libraries', []):
                        graph_db.execute_query("""
                            MATCH (c:Chunk {id: $cid})
                            MERGE (l:Library {name: $name})
                            MERGE (c)-[:INCLUDES]->(l)
                        """, {"cid": chunk_id, "name": lib})

                elif file_type == "concept":
                    entities = _extract_concept_metadata(text, embedding_interface)

                # Link Concepts (Common to both Code and Markdown)
                # This bridges the gap: The MD mentions "Pointers", the C code demonstrates "Pointers"
                for concept in entities.get('concepts', []):
                    graph_db.execute_query("""
                        MATCH (c:Chunk {id: $cid})
                        MERGE (con:Concept {name: $name})
                        MERGE (c)-[:RELATES_TO]->(con)
                    """, {"cid": chunk_id, "name": concept})

        except Exception as e:
            logger.error(f"Error processing {filename}: {e}")

    logger.info(f"Processed {len(all_chunks_for_vector_store)} total chunks.")
    return all_chunks_for_vector_store