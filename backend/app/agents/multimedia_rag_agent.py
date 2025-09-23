# backend/app/agents/multimedia_rag_agent.py

import logging
import json
import re 
import asyncio
from typing import Dict, List, Any, Optional
import base64
import io
from PIL import Image

from app.core import config
from app.db.llm_interface import LLMInterface
from app.db.vector_store import VectorStore
from app.db.graph_db import Neo4jGraphDB
from app.services.multimedia_generator import MultimediaGenerator

class MultimediaRAGAgent:
    """
    Enhanced RAG Agent with multimedia generation capabilities for STEM content.
    """
    def __init__(self,
                 llm_interface: LLMInterface,
                 vector_store: VectorStore,
                 graph_db: Neo4jGraphDB,
                 multimedia_generator: MultimediaGenerator,
                 logger: logging.Logger):
        self.llm_interface = llm_interface
        self.vector_store = vector_store
        self.graph_db = graph_db
        self.multimedia_generator = multimedia_generator
        self.logger = logger

    def _classify_query_domain(self, query: str) -> str:
        """
        Uses LLM to intelligently classify query domain with high accuracy.
        """
        self.logger.info("Classifying query domain...")
        
        prompt = f"""
        You are a domain classification expert. Classify the following question as either "STEM" or "Non-STEM".

        **STEM includes:**
        - Computer Science (programming, algorithms, data structures, software engineering)
        - Cybersecurity (network security, threats, vulnerabilities, encryption, DDoS, malware)
        - Machine Learning & AI (neural networks, models, training, datasets, deep learning, gradient descent)
        - Mathematics, Engineering, Physics, Chemistry, Biology
        - Technology, Systems, Networks, Protocols
        - Data Science, Statistics, Analytics

        **Non-STEM includes:**
        - History, Literature, Philosophy, Arts
        - Social Sciences, Psychology, Sociology
        - Business, Economics, Management
        - Languages, Cultural Studies
        - Institutional history, traditions, achievements

        **Question:** "{query}"

        **Classification:** Respond with ONLY "STEM" or "Non-STEM"
        """
        
        response = self.llm_interface.generate_response(prompt).strip().upper()
        
        if "STEM" in response and "NON-STEM" not in response:
            classification = "STEM"
        elif "NON-STEM" in response:
            classification = "Non-STEM"
        else:
            classification = self._fallback_query_classification(query)
        
        self.logger.info(f"Query classified as: {classification}")
        return classification

    def _fallback_query_classification(self, query: str) -> str:
        """Fallback keyword-based classification."""
        stem_indicators = [
            'algorithm', 'programming', 'machine learning', 'gradient descent', 'neural network',
            'cybersecurity', 'encryption', 'deep learning', 'data science', 'statistics'
        ]
        
        non_stem_indicators = [
            'history', 'literature', 'tradition', 'culture', 'university founded', 'homecoming'
        ]
        
        query_lower = query.lower()
        stem_score = sum(1 for indicator in stem_indicators if indicator in query_lower)
        non_stem_score = sum(1 for indicator in non_stem_indicators if indicator in query_lower)
        
        return "STEM" if stem_score > non_stem_score else "Non-STEM"

    def _generate_chain_of_thoughts(self, query: str, context: str) -> Dict[str, Any]:
        """
        Generates a structured chain of thoughts for complex STEM concepts.
        """
        self.logger.info("Generating chain of thoughts for STEM query...")
        
        prompt = f"""
        You are an expert STEM educator creating a comprehensive learning experience. Break down the complex concept into a structured chain of thoughts.

        **Task:** Create a detailed explanation structure for: "{query}"

        **Available Context:**
        {context}

        **Generate a JSON response with the following structure:**
        {{
            "concept_overview": "Brief 2-3 sentence overview of the main concept",
            "key_components": [
                {{
                    "component": "Component name",
                    "explanation": "Detailed explanation",
                    "importance": "Why this component matters"
                }}
            ],
            "step_by_step_process": [
                {{
                    "step": 1,
                    "title": "Step title",
                    "description": "Detailed description",
                    "visual_description": "What should be visualized in this step"
                }}
            ],
            "real_world_applications": [
                {{
                    "application": "Application name",
                    "description": "How it's used in practice"
                }}
            ],
            "common_misconceptions": [
                {{
                    "misconception": "Common misunderstanding",
                    "correction": "Correct explanation"
                }}
            ],
            "visualization_needs": {{
                "diagrams": ["Description of needed diagrams"],
                "animations": ["Description of needed animations"],
                "interactive_elements": ["Description of interactive elements"]
            }}
        }}

        Ensure all explanations are clear, accurate, and educational.
        """
        
        response = self.llm_interface.generate_response(prompt)
        
        try:
            chain_of_thoughts = json.loads(response)
            self.logger.info("Successfully generated chain of thoughts")
            return chain_of_thoughts
        except json.JSONDecodeError:
            self.logger.error(f"Failed to parse chain of thoughts JSON: {response}")
            return self._generate_fallback_chain_of_thoughts(query, context)

    def _generate_fallback_chain_of_thoughts(self, query: str, context: str) -> Dict[str, Any]:
        """Fallback simplified chain of thoughts."""
        return {
            "concept_overview": f"Explanation of {query} based on available resources.",
            "key_components": [{"component": "Main Concept", "explanation": context[:500], "importance": "Fundamental understanding"}],
            "step_by_step_process": [{"step": 1, "title": "Understanding", "description": context[:300], "visual_description": "Conceptual diagram"}],
            "real_world_applications": [{"application": "General Use", "description": "Applied in various STEM fields"}],
            "common_misconceptions": [],
            "visualization_needs": {
                "diagrams": ["Basic concept diagram"],
                "animations": [],
                "interactive_elements": []
            }
        }

    def _identify_multimedia_opportunities(self, chain_of_thoughts: Dict[str, Any], query: str) -> Dict[str, Any]:
        """
        Identifies opportunities for multimedia content generation.
        """
        multimedia_plan = {
            "images": [],
            "videos": [],
            "priority": "high" if any(keyword in query.lower() for keyword in 
                                   ['gradient descent', 'neural network', 'algorithm', 'process', 'how']) else "medium"
        }
        
        # Identify image opportunities
        for component in chain_of_thoughts.get("key_components", []):
            multimedia_plan["images"].append({
                "type": "concept_diagram",
                "description": f"Diagram illustrating {component['component']}",
                "prompt": f"Create a clear educational diagram showing {component['component']} - {component['explanation'][:100]}"
            })
        
        for step in chain_of_thoughts.get("step_by_step_process", []):
            if step.get("visual_description"):
                multimedia_plan["images"].append({
                    "type": "process_step",
                    "description": f"Step {step['step']}: {step['title']}",
                    "prompt": f"Educational illustration of {step['title']} - {step['visual_description']}"
                })
        
        # Identify video opportunities for process-based concepts
        if len(chain_of_thoughts.get("step_by_step_process", [])) > 2:
            multimedia_plan["videos"].append({
                "type": "process_animation",
                "description": f"Animated explanation of {query}",
                "prompt": f"Create an educational animation showing the step-by-step process of {query}",
                "duration": "30-60 seconds"
            })
        
        return multimedia_plan

    def _clean_query_for_embedding(self, query: str) -> str:
        """Clean query for embedding search."""
        prompt = f"""
        Extract the essential question from the user's query below.
        Remove any conversational filler, greetings, or instructions.
        Your response should ONLY be the core question.

        User Query: "{query}"
        
        Core Question:
        """
        cleaned_query = self.llm_interface.generate_response(prompt).strip()
        return cleaned_query

    def _extract_entities_from_query(self, query: str, query_domain: str) -> List[str]:
        """Extract entities for STEM queries."""
        stem_prompt = f"""
        Extract technical concepts and terms from this STEM query: "{query}"
        
        Include:
        - Main technical concepts (gradient descent, neural networks, algorithms)
        - Related mathematical concepts
        - Process names and methodologies
        - Technical terms and jargon
        
        Return as JSON list of strings.
        """
        
        response_str = self.llm_interface.generate_response(stem_prompt).strip()
        
        match = re.search(r'\[.*\]', response_str, re.DOTALL)
        if not match:
            return [query.lower()]  # Fallback
            
        try:
            entities = json.loads(match.group(0))
            return entities if isinstance(entities, list) else [query.lower()]
        except json.JSONDecodeError:
            return [query.lower()]

    def _execute_retrieval(self, query: str, user_role: str, query_domain: str, top_k: int) -> List[Dict[str, Any]]:
        """Execute comprehensive retrieval for STEM content."""
        entities = self._extract_entities_from_query(query, query_domain)
        
        # Knowledge graph retrieval
        kg_chunks = self.graph_db.get_context_for_entities(entities)
        
        # Enhanced STEM entity search
        broader_entities = [
            "machine learning", "gradient descent", "neural network", "algorithm", 
            "training", "optimization", "mathematics", "statistics", "model",
            "deep learning", "artificial intelligence", "data science"
        ]
        main_entity_chunks = self.graph_db.get_context_for_entities(broader_entities)

        # Vector search
        embedding_query = self._clean_query_for_embedding(query)
        query_embedding = self.llm_interface.get_embedding(embedding_query, task_type="RETRIEVAL_QUERY")
        
        vector_chunks = []
        if query_embedding:
            where_filter = {"access_level": "student"} if user_role == 'student' else {}
            vector_chunks = self.vector_store.query(
                query_embedding=query_embedding,
                top_k=top_k,
                where_filter=where_filter
            )

        # Technical expanded search
        expanded_vector_chunks = []
        if query_embedding:
            expanded_query = f"{embedding_query} technical mathematical algorithm process step-by-step implementation"
            expanded_embedding = self.llm_interface.get_embedding(expanded_query, task_type="RETRIEVAL_QUERY")
            if expanded_embedding:
                expanded_vector_chunks = self.vector_store.query(
                    query_embedding=expanded_embedding,
                    top_k=top_k//2,
                    where_filter=where_filter
                )

        # Combine and deduplicate
        all_chunks = kg_chunks + main_entity_chunks + vector_chunks + expanded_vector_chunks
        combined_chunks = {}
        for chunk in all_chunks:
            if chunk.get('metadata') and 'chunk_id' in chunk['metadata']:
                chunk_id = chunk['metadata']['chunk_id']
                if chunk_id not in combined_chunks:
                    combined_chunks[chunk_id] = chunk
        
        return list(combined_chunks.values())

    def _reason_about_context(self, query: str, context_chunks: List[Dict[str, Any]], query_domain: str) -> str:
        """STEM-focused reasoning with technical depth."""
        if not context_chunks:
            return "The context is empty."
            
        context_str = "\n\n---\n\n".join([chunk['text'] for chunk in context_chunks])

        prompt = f"""
        You are analyzing STEM content to provide a comprehensive technical explanation.

        **STEM Analysis Instructions:**
        1. **Technical Accuracy:** Extract precise mathematical and technical details
        2. **Process Understanding:** Identify step-by-step procedures and algorithms  
        3. **Mathematical Foundations:** Extract formulas, equations, and mathematical relationships
        4. **Implementation Details:** Look for practical applications and implementation approaches
        5. **Performance Aspects:** Identify metrics, benchmarks, and optimization techniques
        6. **Conceptual Connections:** Map relationships between different technical concepts

        **User's Question:** {query}

        **Context to Analyze:**
        ---
        {context_str}
        ---

        **Comprehensive Technical Analysis:**
        Provide a detailed technical analysis covering mathematical foundations, step-by-step processes, 
        implementation details, and practical applications. Include any formulas, algorithms, or 
        technical procedures mentioned in the context.
        """
        
        return self.llm_interface.generate_response(prompt)

    async def _generate_multimedia_content(self, multimedia_plan: Dict[str, Any], chain_of_thoughts: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generates multimedia content based on the plan.
        """
        multimedia_content = {
            "images": [],
            "videos": [],
            "generation_log": []
        }
        
        # Generate images
        for image_plan in multimedia_plan.get("images", []):
            try:
                self.logger.info(f"Generating image: {image_plan['description']}")
                
                # Use Gemini 2.5 Flash for image generation
                image_data = await self.multimedia_generator.generate_image(
                    prompt=image_plan["prompt"],
                    style="educational_diagram",
                    size="1024x1024"
                )
                
                if image_data:
                    multimedia_content["images"].append({
                        "type": image_plan["type"],
                        "description": image_plan["description"],
                        "data": image_data,
                        "format": "base64_png"
                    })
                    multimedia_content["generation_log"].append(f"Successfully generated {image_plan['type']}")
                else:
                    multimedia_content["generation_log"].append(f"Failed to generate {image_plan['type']}")
                    
            except Exception as e:
                self.logger.error(f"Error generating image: {e}")
                multimedia_content["generation_log"].append(f"Error generating {image_plan['type']}: {str(e)}")
        
        # Generate videos (for high-priority content)
        if multimedia_plan.get("priority") == "high":
            for video_plan in multimedia_plan.get("videos", []):
                try:
                    self.logger.info(f"Generating video: {video_plan['description']}")
                    
                    # Use Veo for video generation
                    video_data = await self.multimedia_generator.generate_video(
                        prompt=video_plan["prompt"],
                        duration=video_plan.get("duration", "30 seconds"),
                        style="educational"
                    )
                    
                    if video_data:
                        multimedia_content["videos"].append({
                            "type": video_plan["type"],
                            "description": video_plan["description"],
                            "data": video_data,
                            "format": "mp4_base64"
                        })
                        multimedia_content["generation_log"].append(f"Successfully generated {video_plan['type']}")
                    else:
                        multimedia_content["generation_log"].append(f"Failed to generate {video_plan['type']}")
                        
                except Exception as e:
                    self.logger.error(f"Error generating video: {e}")
                    multimedia_content["generation_log"].append(f"Error generating {video_plan['type']}: {str(e)}")
        
        return multimedia_content

    def _format_multimedia_response(self, 
                                   query: str, 
                                   chain_of_thoughts: Dict[str, Any], 
                                   multimedia_content: Dict[str, Any],
                                   sources: List[Dict]) -> str:
        """
        Formats the final response with multimedia content.
        """
        response_parts = []
        
        # Main explanation
        response_parts.append(f"# {query}\n")
        response_parts.append(f"## Overview\n{chain_of_thoughts['concept_overview']}\n")
        
        # Key components with images
        if chain_of_thoughts.get("key_components"):
            response_parts.append("## Key Components\n")
            for i, component in enumerate(chain_of_thoughts["key_components"]):
                response_parts.append(f"### {component['component']}\n")
                response_parts.append(f"{component['explanation']}\n")
                
                # Insert related image if available
                if i < len(multimedia_content["images"]):
                    img = multimedia_content["images"][i]
                    response_parts.append(f"![{img['description']}](data:image/png;base64,{img['data']})\n")
                
                response_parts.append(f"**Why it matters:** {component['importance']}\n")
        
        # Step-by-step process
        if chain_of_thoughts.get("step_by_step_process"):
            response_parts.append("## Step-by-Step Process\n")
            for step in chain_of_thoughts["step_by_step_process"]:
                response_parts.append(f"### Step {step['step']}: {step['title']}\n")
                response_parts.append(f"{step['description']}\n")
        
        # Videos
        if multimedia_content["videos"]:
            response_parts.append("## Visual Learning\n")
            for video in multimedia_content["videos"]:
                response_parts.append(f"### {video['description']}\n")
                response_parts.append(f"[Video: {video['type']}](data:video/mp4;base64,{video['data']})\n")
        
        # Real-world applications
        if chain_of_thoughts.get("real_world_applications"):
            response_parts.append("## Real-World Applications\n")
            for app in chain_of_thoughts["real_world_applications"]:
                response_parts.append(f"- **{app['application']}:** {app['description']}\n")
        
        # Common misconceptions
        if chain_of_thoughts.get("common_misconceptions"):
            response_parts.append("## Common Misconceptions\n")
            for misc in chain_of_thoughts["common_misconceptions"]:
                response_parts.append(f"- **Misconception:** {misc['misconception']}\n")
                response_parts.append(f"  **Correction:** {misc['correction']}\n")
        
        return "\n".join(response_parts)

    async def run(self, query: str, user_role: str = 'student', socratic: bool = False, topic_class: str = "Auto") -> Dict[str, Any]:
        """
        Enhanced multimedia RAG pipeline for STEM questions.
        """
        # Classify query domain
        if topic_class == "Auto" or topic_class not in ["STEM", "Non-STEM"]:
            query_domain = self._classify_query_domain(query)
        else:
            query_domain = topic_class
        
        self.logger.info(f"Multimedia RAG processing {query_domain} query: '{query}'")
        
        # For non-STEM or teacher queries, use standard processing
        if query_domain != "STEM" or user_role != 'student':
            return await self._standard_processing(query, user_role, query_domain)
        
        # Enhanced STEM processing for students
        retrieved_chunks = self._execute_retrieval(query, user_role, query_domain, top_k=15)
        
        if not retrieved_chunks:
            return {"answer": "I could not find any relevant information to answer your question.", "sources": []}
        
        # Generate reasoned context
        reasoned_context = self._reason_about_context(query, retrieved_chunks, query_domain)
        
        # Generate chain of thoughts
        chain_of_thoughts = self._generate_chain_of_thoughts(query, reasoned_context)
        
        # Identify multimedia opportunities
        multimedia_plan = self._identify_multimedia_opportunities(chain_of_thoughts, query)
        
        # Generate multimedia content
        multimedia_content = await self._generate_multimedia_content(multimedia_plan, chain_of_thoughts)
        
        # Format final response
        final_answer = self._format_multimedia_response(query, chain_of_thoughts, multimedia_content, retrieved_chunks)
        
        # Prepare sources
        seen_sources = set()
        unique_sources = []
        for chunk in retrieved_chunks:
            source_doc = chunk.get('metadata', {}).get('document_name')
            if source_doc and source_doc not in seen_sources:
                metadata = chunk['metadata'].copy()
                metadata['query_domain'] = query_domain
                unique_sources.append(metadata)
                seen_sources.add(source_doc)
        
        return {
            "answer": final_answer,
            "sources": unique_sources,
            "query_domain": query_domain,
            "multimedia_enabled": True,
            "chain_of_thoughts": chain_of_thoughts,
            "multimedia_content": multimedia_content,
            "generation_log": multimedia_content.get("generation_log", [])
        }

    async def _standard_processing(self, query: str, user_role: str, query_domain: str) -> Dict[str, Any]:
        """Standard processing for non-STEM queries or teacher users."""
        # Implement standard RAG processing here
        # This would be your existing RAG logic for non-multimedia responses
        return {
            "answer": "Standard response (implement existing RAG logic here)",
            "sources": [],
            "query_domain": query_domain,
            "multimedia_enabled": False
        }