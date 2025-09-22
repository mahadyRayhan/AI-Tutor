# backend/app/processing/data_processing.py (Enhanced for multi-domain content)

import os
import re
import glob
import json
import logging
from typing import List, Dict, Any

from langchain_text_splitters import RecursiveCharacterTextSplitter
from unstructured.partition.auto import partition

from app.core import config
from app.db.graph_db import Neo4jGraphDB
from app.db.llm_interface import LLMInterface

logger = logging.getLogger(__name__)

def _create_chunk_id(doc_name: str, element_idx: int, chunk_idx: int) -> str:
    """Creates a unique ID for a chunk based on its document and position."""
    sanitized_doc_name = re.sub(r'[^a-zA-Z0-9_-]', '_', os.path.splitext(doc_name)[0])
    return f"{sanitized_doc_name}_elem{element_idx}_chunk{chunk_idx}"

def _detect_content_domain(chunk_text: str, doc_name: str) -> str:
    """
    Detects whether content is STEM or non-STEM based on keywords and document name.
    """
    stem_keywords = {
        'computer science': ['algorithm', 'programming', 'software', 'hardware', 'coding', 'database', 'API', 'framework'],
        'cyber security': ['cybersecurity', 'encryption', 'malware', 'firewall', 'vulnerability', 'attack', 'security'],
        'machine learning': ['neural network', 'model', 'training', 'dataset', 'classification', 'regression', 'AI'],
        'general tech': ['technology', 'system', 'network', 'server', 'protocol', 'data structure', 'interface']
    }
    
    non_stem_keywords = {
        'history': ['historical', 'century', 'war', 'revolution', 'empire', 'dynasty', 'era', 'period'],
        'literature': ['novel', 'poem', 'author', 'literary', 'narrative', 'character', 'plot', 'theme'],
        'general humanities': ['tradition', 'culture', 'society', 'philosophy', 'art', 'language']
    }
    
    text_lower = chunk_text.lower()
    doc_lower = doc_name.lower()
    
    stem_score = 0
    non_stem_score = 0
    
    # Check document name first
    for domain, keywords in stem_keywords.items():
        if any(keyword in doc_lower for keyword in keywords):
            stem_score += 2
    
    for domain, keywords in non_stem_keywords.items():
        if any(keyword in doc_lower for keyword in keywords):
            non_stem_score += 2
    
    # Check content
    for domain, keywords in stem_keywords.items():
        stem_score += sum(1 for keyword in keywords if keyword in text_lower)
    
    for domain, keywords in non_stem_keywords.items():
        non_stem_score += sum(1 for keyword in keywords if keyword in text_lower)
    
    return "STEM" if stem_score > non_stem_score else "Non-STEM"

def _extract_entities_from_chunk(chunk_text: str, llm_interface: LLMInterface, content_domain: str = "Non-STEM") -> List[Dict[str, str]]:
    """Enhanced entity extraction adapted for both STEM and non-STEM content."""
    
    if content_domain == "STEM":
        entity_types = """
        **STEM Entity Types to Extract:**
        - "Person": Researchers, scientists, engineers, developers
        - "Organization": Companies, institutions, research labs, universities
        - "Technology": Programming languages, frameworks, tools, systems, algorithms
        - "Concept": Technical concepts, methodologies, theories, principles
        - "Method": Algorithms, techniques, approaches, processes
        - "Tool": Software, hardware, platforms, applications
        - "Standard": Protocols, specifications, formats, standards
        - "Metric": Performance measures, benchmarks, KPIs
        - "Dataset": Training data, databases, corpora
        - "Model": Machine learning models, mathematical models
        - "Security": Threats, vulnerabilities, attacks, defenses
        - "Achievement": Breakthroughs, innovations, first implementations
        """
        
        focus_instructions = """
        **Special STEM Focus:**
        - Technical innovations and breakthroughs
        - Algorithms and methodologies
        - Security concepts and threats
        - Performance metrics and benchmarks
        - Research contributions and findings
        - System architectures and designs
        """
    else:
        entity_types = """
        **Non-STEM Entity Types to Extract:**
        - "Person": Historical figures, authors, leaders, scholars
        - "Organization": Universities, institutions, governments, movements
        - "Location": Places, cities, countries, regions, landmarks
        - "Date": Years, time periods, eras, specific dates
        - "Event": Historical events, traditions, celebrations, wars, movements
        - "Achievement": Historical firsts, innovations, accomplishments
        - "Tradition": Customs, practices, cultural elements
        - "Concept": Ideas, philosophies, theories, movements
        - "Work": Books, documents, treaties, artworks, publications
        - "Period": Historical eras, time periods, dynasties
        - "Culture": Cultural elements, practices, beliefs
        """
        
        focus_instructions = """
        **Special Non-STEM Focus:**
        - Historical significance and chronology
        - Cultural traditions and practices
        - Literary works and their elements
        - Social movements and changes
        - Institutional achievements and firsts
        - Geographic and temporal context
        """

    prompt = f"""
    You are extracting entities from {content_domain} content. Extract ALL meaningful entities.
    
    {entity_types}
    
    {focus_instructions}
    
    **Critical Instructions:**
    - Pay attention to domain-specific terminology
    - Extract both explicit entities and implied concepts
    - Include technical terms, proper nouns, and significant concepts
    - For achievements, look for "first", "pioneered", "developed", "created", "invented"
    
    Return ONLY a valid JSON array of objects with "name" and "type" fields.

    Text:
    ---
    {chunk_text}
    ---

    JSON Output:
    """
    
    response_str = llm_interface.generate_response(prompt)
    
    # Robust JSON extraction (same as before)
    json_matches = re.findall(r'\[.*?\]', response_str, re.DOTALL)
    if not json_matches:
        object_matches = re.findall(r'\{[^}]*"name"[^}]*"type"[^}]*\}', response_str)
        if object_matches:
            json_str = '[' + ','.join(object_matches) + ']'
        else:
            logger.warning(f"Could not find JSON in entity extraction response for {content_domain} content")
            return []
    else:
        json_str = json_matches[-1]
    
    try:
        entities = json.loads(json_str)
        if isinstance(entities, list):
            validated_entities = []
            for e in entities:
                if isinstance(e, dict) and "name" in e and "type" in e:
                    clean_name = str(e["name"]).strip()
                    clean_type = str(e["type"]).strip()
                    if clean_name and clean_type:
                        validated_entities.append({"name": clean_name, "type": clean_type})
            
            logger.debug(f"Extracted {len(validated_entities)} {content_domain} entities from chunk.")
            return validated_entities
        else:
            logger.warning(f"Entity extraction returned valid JSON but not a list: {type(entities)}")
            return []
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse {content_domain} entity extraction JSON: {e}")
        return []

def _extract_relationships_from_chunk(chunk_text: str, entities: List[Dict[str, str]], llm_interface: LLMInterface, content_domain: str = "Non-STEM") -> List[Dict[str, Any]]:
    """Enhanced relationship extraction for both STEM and non-STEM content."""
    if len(entities) < 2:
        return []

    entity_info = [f"{e['name']} ({e['type']})" for e in entities]
    
    if content_domain == "STEM":
        relationship_types = """
        **STEM Relationship Types:**
        - "IMPLEMENTS" (algorithm implements concept)
        - "USES" (system uses technology/tool)
        - "BASED_ON" (method based on theory)
        - "IMPROVES" (new method improves existing)
        - "DEVELOPED_BY" (technology developed by person/org)
        - "APPLIED_TO" (method applied to domain)
        - "ACHIEVES" (system achieves performance)
        - "DETECTS" (tool detects threat/issue)
        - "PREVENTS" (defense prevents attack)
        - "TRAINED_ON" (model trained on dataset)
        - "MEASURES" (metric measures performance)
        - "PART_OF" (component part of system)
        """
        
        focus_areas = """
        - Technical implementations and dependencies
        - Performance relationships and improvements
        - Security threats and defenses
        - Research contributions and applications
        - System architectures and components
        """
    else:
        relationship_types = """
        **Non-STEM Relationship Types:**
        - "FOUNDED" (person/org founded institution)
        - "STARTED_TRADITION_OF" (entity started tradition)
        - "IS_FIRST" (being the first of something)
        - "ESTABLISHED_IN" (established in time/place)
        - "LOCATED_IN" (geographic relationships)
        - "KNOWN_FOR" (achievements/recognition)
        - "PIONEERED" (pioneering efforts)
        - "OCCURRED_DURING" (events during periods)
        - "INFLUENCED_BY" (cultural/intellectual influence)
        - "CREATED" (authored/created works)
        - "PARTICIPATED_IN" (involvement in events)
        - "SUCCEEDED_BY" (historical succession)
        """
        
        focus_areas = """
        - Historical precedence and chronology
        - Cultural and traditional relationships
        - Institutional achievements and firsts
        - Geographic and temporal connections
        - Literary and artistic relationships
        """

    prompt = f"""
    Analyze the {content_domain} text to find direct relationships between these entities: {entity_info}
    
    {relationship_types}
    
    **Focus Areas:**
    {focus_areas}
    
    Return ONLY a valid JSON array of relationship objects.
    Each object must have: "source", "target", "type", "source_type", "target_type"

    Text:
    ---
    {chunk_text}
    ---

    JSON Output:
    """
    
    response_str = llm_interface.generate_response(prompt).strip()
    
    # Same robust JSON extraction as before
    json_matches = re.findall(r'\[.*?\]', response_str, re.DOTALL)
    if not json_matches:
        rel_pattern = r'\{[^}]*"source"[^}]*"target"[^}]*"type"[^}]*\}'
        object_matches = re.findall(rel_pattern, response_str)
        if object_matches:
            json_str = '[' + ','.join(object_matches) + ']'
        else:
            logger.debug(f"No {content_domain} relationships found in LLM response")
            return []
    else:
        json_str = json_matches[-1]
    
    try:
        relationships = json.loads(json_str)
        if isinstance(relationships, list):
            validated_relationships = []
            entity_names = [e['name'].lower() for e in entities]
            
            for r in relationships:
                if (isinstance(r, dict) and 
                    all(key in r for key in ['source', 'target', 'type']) and
                    str(r['source']).lower().strip() in entity_names and 
                    str(r['target']).lower().strip() in entity_names):
                    
                    # Add source_type and target_type if missing
                    if 'source_type' not in r:
                        source_entity = next((e for e in entities if e['name'].lower() == str(r['source']).lower()), None)
                        r['source_type'] = source_entity['type'] if source_entity else 'Concept'
                    
                    if 'target_type' not in r:
                        target_entity = next((e for e in entities if e['name'].lower() == str(r['target']).lower()), None)
                        r['target_type'] = target_entity['type'] if target_entity else 'Concept'
                    
                    validated_relationships.append(r)
            
            logger.debug(f"Extracted {len(validated_relationships)} {content_domain} relationships.")
            return validated_relationships
        else:
            logger.warning(f"Relationship extraction returned valid JSON but not a list: {type(relationships)}")
            return []
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse {content_domain} relationship JSON: {e}")
        return []

def process_and_chunk_documents(
    resource_paths: List[str],
    graph_db: Neo4jGraphDB,
    embedding_interface: LLMInterface,
    logger_parent: logging.Logger
) -> List[Dict[str, Any]]:
    """
    Enhanced document processing for multi-domain (STEM + Non-STEM) content.
    """
    global logger
    logger = logger_parent

    logger.info("Starting multi-domain document processing and chunking...")
    
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

    stem_docs_processed = 0
    non_stem_docs_processed = 0

    for doc_path in document_files:
        doc_name = os.path.basename(doc_path)
        logger.info(f"Processing document: {doc_name}")

        access_level = 'student' 
        for teacher_folder in config.TEACHER_ONLY_FOLDERS:
            if os.path.normpath(os.path.abspath(doc_path)).startswith(os.path.normpath(os.path.abspath(teacher_folder))):
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
                    element_chunks.append(f"Data Table:\n{element.metadata.text_as_html}")
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
                    
                    # Detect content domain for this chunk
                    content_domain = _detect_content_domain(cleaned_chunk, doc_name)
                    
                    chunk_id = _create_chunk_id(doc_name, i, j)
                    
                    chunk_metadata = {
                        "document_name": doc_name,
                        "chunk_id": chunk_id,
                        "element_type": element_type,
                        "page_number": getattr(element.metadata, 'page_number', None),
                        "access_level": access_level,
                        "content_domain": content_domain  # Add domain info
                    }

                    chunk_info = {"chunk_text": cleaned_chunk, "metadata": chunk_metadata}
                    doc_all_chunks_for_kg.append(chunk_info)
                    all_chunks_for_vector_store.append(chunk_info)

            if doc_all_chunks_for_kg:
                # Determine document domain
                doc_domain = _detect_content_domain(' '.join([c['chunk_text'][:200] for c in doc_all_chunks_for_kg[:5]]), doc_name)
                if doc_domain == "STEM":
                    stem_docs_processed += 1
                else:
                    non_stem_docs_processed += 1
                
                logger.info(f"Document '{doc_name}' classified as {doc_domain} content")
                
                graph_db.add_document_and_chunks(doc_name, doc_all_chunks_for_kg)
                logger.info(f"Processing {len(doc_all_chunks_for_kg)} {doc_domain} chunks from '{doc_name}'...")
                
                total_entities_found = 0
                total_relationships_found = 0
                
                for chunk in doc_all_chunks_for_kg:
                    chunk_domain = chunk['metadata']['content_domain']
                    
                    # Domain-aware entity extraction
                    entities = _extract_entities_from_chunk(chunk['chunk_text'], embedding_interface, chunk_domain)
                    if entities:
                        total_entities_found += len(entities)
                        graph_db.link_chunk_to_entities(chunk['metadata']['chunk_id'], entities)
                        
                        # Domain-aware relationship extraction
                        relationships = _extract_relationships_from_chunk(chunk['chunk_text'], entities, embedding_interface, chunk_domain)
                        if relationships:
                            total_relationships_found += len(relationships)
                            graph_db.add_relationships(relationships)
                
                logger.info(f"Processed {doc_domain} document '{doc_name}': {total_entities_found} entities, {total_relationships_found} relationships")

        except Exception as e:
            logger.exception(f"Failed to process file {doc_path}: {e}")
            
    logger.info(f"Multi-domain processing complete:")
    logger.info(f"  - STEM documents: {stem_docs_processed}")
    logger.info(f"  - Non-STEM documents: {non_stem_docs_processed}")
    logger.info(f"  - Total chunks for vector store: {len(all_chunks_for_vector_store)}")
    
    return all_chunks_for_vector_store