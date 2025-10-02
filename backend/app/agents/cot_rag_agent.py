# backend/app/agents/cot_rag_agent.py

import logging
import json
import re
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass

from app.core import config
from app.db.llm_interface import LLMInterface
from app.db.vector_store import ChromaVectorStore
from app.db.graph_db import Neo4jGraphDB

@dataclass
class CoTStep:
    step_number: int
    reasoning: str
    conclusion: str
    confidence: float
    dependencies: List[int]  # Which previous steps this depends on
    validation_notes: str = ""

@dataclass
class CoTValidation:
    is_valid: bool
    confidence_score: float
    issues: List[str]
    corrections: List[str]
    failed_steps: List[int]

class ChainOfThoughtRAGAgent:
    """
    RAG Agent with explicit Chain of Thought reasoning and failure detection.
    """
    
    def __init__(self, 
                 llm_interface: LLMInterface,
                 vector_store: ChromaVectorStore,
                 graph_db: Neo4jGraphDB,
                 logger: logging.Logger):
        self.llm_interface = llm_interface
        self.vector_store = vector_store
        self.graph_db = graph_db
        self.logger = logger
        
        # CoT failure detection thresholds
        self.min_confidence_threshold = 0.7
        self.max_correction_attempts = 3
        
    def _detect_query_complexity(self, query: str) -> str:
        """
        Detects if a query requires Chain of Thought reasoning.
        """
        complexity_indicators = {
            'high': [
                'explain how', 'why does', 'what causes', 'step by step',
                'derive', 'prove', 'compare and contrast', 'analyze',
                'what is the difference between', 'how do you solve',
                'what are the implications', 'how does this relate to'
            ],
            'medium': [
                'what is', 'how to', 'when should', 'which method',
                'advantages and disadvantages'
            ]
        }
        
        query_lower = query.lower()
        
        # Check for high complexity indicators
        high_count = sum(1 for indicator in complexity_indicators['high'] 
                        if indicator in query_lower)
        medium_count = sum(1 for indicator in complexity_indicators['medium'] 
                          if indicator in query_lower)
        
        # STEM domain increases complexity
        stem_keywords = ['algorithm', 'mathematical', 'equation', 'formula', 
                        'optimization', 'neural network', 'machine learning']
        stem_count = sum(1 for keyword in stem_keywords if keyword in query_lower)
        
        if high_count >= 1 or stem_count >= 2:
            return 'high'
        elif medium_count >= 1 or stem_count >= 1:
            return 'medium'
        else:
            return 'low'

    def _generate_cot_reasoning(self, query: str, context: str) -> List[CoTStep]:
        """
        Generates explicit chain of thought reasoning steps.
        """
        self.logger.info("Generating Chain of Thought reasoning...")
        
        prompt = f"""
        You are an expert educator breaking down complex STEM concepts using Chain of Thought reasoning.
        
        **Task:** Analyze the query step-by-step, showing your reasoning process explicitly.
        
        **Query:** {query}
        
        **Available Context:** {context}
        
        **Instructions:**
        1. Break the problem into logical steps
        2. For each step, show your reasoning process
        3. State conclusions clearly
        4. Indicate confidence levels (0.0 to 1.0)
        5. Note dependencies between steps
        
        **Format your response as JSON:**
        {{
            "reasoning_steps": [
                {{
                    "step_number": 1,
                    "reasoning": "Detailed reasoning for this step",
                    "conclusion": "What we can conclude from this step",
                    "confidence": 0.9,
                    "dependencies": [],
                    "validation_notes": "Why this step is reliable/uncertain"
                }},
                ...
            ]
        }}
        
        **Example reasoning patterns:**
        - Mathematical: "Given X, we can apply formula Y because..."
        - Algorithmic: "First we initialize Z, then we iterate because..."
        - Comparative: "X differs from Y in that... This means..."
        
        Be explicit about assumptions, show mathematical derivations, and explain logical connections.
        """
        
        response = self.llm_interface.generate_response(prompt)
        
        try:
            # Extract JSON from response
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if not json_match:
                self.logger.warning("No JSON found in CoT reasoning response")
                return self._generate_fallback_cot(query, context)
            
            json_data = json.loads(json_match.group(0))
            
            cot_steps = []
            for step_data in json_data.get('reasoning_steps', []):
                step = CoTStep(
                    step_number=step_data.get('step_number', 0),
                    reasoning=step_data.get('reasoning', ''),
                    conclusion=step_data.get('conclusion', ''),
                    confidence=float(step_data.get('confidence', 0.5)),
                    dependencies=step_data.get('dependencies', []),
                    validation_notes=step_data.get('validation_notes', '')
                )
                cot_steps.append(step)
            
            self.logger.info(f"Generated {len(cot_steps)} CoT reasoning steps")
            return cot_steps
            
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            self.logger.error(f"Failed to parse CoT reasoning JSON: {e}")
            return self._generate_fallback_cot(query, context)

    def _generate_fallback_cot(self, query: str, context: str) -> List[CoTStep]:
        """
        Generates simplified CoT when JSON parsing fails.
        """
        return [
            CoTStep(
                step_number=1,
                reasoning=f"Analyzing the query: {query}",
                conclusion="Need to break down the problem systematically",
                confidence=0.6,
                dependencies=[],
                validation_notes="Fallback reasoning due to parsing issues"
            ),
            CoTStep(
                step_number=2,
                reasoning=f"Using available context: {context[:200]}...",
                conclusion="Extracting relevant information from knowledge base",
                confidence=0.7,
                dependencies=[1],
                validation_notes="Based on retrieved context"
            )
        ]

    def _validate_cot_reasoning(self, cot_steps: List[CoTStep], query: str) -> CoTValidation:
        """
        Validates the chain of thought for logical consistency and accuracy.
        """
        self.logger.info("Validating Chain of Thought reasoning...")
        
        # Convert CoT steps to validation prompt
        steps_text = "\n".join([
            f"Step {step.step_number}: {step.reasoning} → {step.conclusion} (Confidence: {step.confidence})"
            for step in cot_steps
        ])
        
        validation_prompt = f"""
        You are a critical reviewer evaluating Chain of Thought reasoning for accuracy and logical consistency.
        
        **Original Query:** {query}
        
        **Chain of Thought Steps:**
        {steps_text}
        
        **Validation Tasks:**
        1. Check logical consistency between steps
        2. Verify mathematical/technical accuracy  
        3. Identify gaps in reasoning
        4. Detect unsupported assumptions
        5. Check if conclusions follow from premises
        
        **Return JSON response:**
        {{
            "is_valid": boolean,
            "confidence_score": 0.0-1.0,
            "issues": ["List of identified problems"],
            "corrections": ["Suggested fixes"],
            "failed_steps": [list of step numbers with issues],
            "validation_summary": "Overall assessment"
        }}
        
        **Focus on:**
        - Mathematical errors or formula misapplication
        - Logical fallacies or non-sequiturs  
        - Missing crucial steps
        - Overconfident conclusions from weak evidence
        - Inconsistent use of terminology
        """
        
        response = self.llm_interface.generate_response(validation_prompt)
        
        try:
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                validation_data = json.loads(json_match.group(0))
                
                validation = CoTValidation(
                    is_valid=validation_data.get('is_valid', False),
                    confidence_score=float(validation_data.get('confidence_score', 0.5)),
                    issues=validation_data.get('issues', []),
                    corrections=validation_data.get('corrections', []),
                    failed_steps=validation_data.get('failed_steps', [])
                )
                
                self.logger.info(f"CoT validation: valid={validation.is_valid}, confidence={validation.confidence_score}")
                return validation
                
        except (json.JSONDecodeError, ValueError) as e:
            self.logger.error(f"Failed to parse CoT validation: {e}")
            
        # Fallback validation
        avg_confidence = sum(step.confidence for step in cot_steps) / len(cot_steps)
        return CoTValidation(
            is_valid=avg_confidence > self.min_confidence_threshold,
            confidence_score=avg_confidence,
            issues=["Validation parsing failed"],
            corrections=["Review reasoning manually"],
            failed_steps=[]
        )

    def _correct_failed_reasoning(self, 
                                cot_steps: List[CoTStep], 
                                validation: CoTValidation, 
                                query: str, 
                                context: str) -> List[CoTStep]:
        """
        Attempts to correct failed reasoning steps.
        """
        self.logger.info(f"Correcting {len(validation.failed_steps)} failed reasoning steps...")
        
        if not validation.failed_steps:
            return cot_steps
            
        correction_prompt = f"""
        You need to fix flawed reasoning steps in a Chain of Thought analysis.
        
        **Original Query:** {query}
        **Context:** {context}
        
        **Issues Identified:** {validation.issues}
        **Suggested Corrections:** {validation.corrections}
        **Failed Steps:** {validation.failed_steps}
        
        **Current Steps:**
        {chr(10).join([f"Step {s.step_number}: {s.reasoning} → {s.conclusion}" for s in cot_steps])}
        
        **Task:** Provide corrected versions of the failed steps only.
        
        **JSON Format:**
        {{
            "corrected_steps": [
                {{
                    "step_number": X,
                    "reasoning": "Corrected reasoning",
                    "conclusion": "Corrected conclusion", 
                    "confidence": 0.8,
                    "dependencies": [...],
                    "validation_notes": "What was corrected and why"
                }}
            ]
        }}
        
        Focus on:
        - Fixing mathematical errors
        - Strengthening weak logical connections
        - Adding missing intermediate steps
        - Correcting factual inaccuracies
        """
        
        response = self.llm_interface.generate_response(correction_prompt)
        
        try:
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                correction_data = json.loads(json_match.group(0))
                
                # Replace failed steps with corrected versions
                corrected_steps = cot_steps.copy()
                for correction in correction_data.get('corrected_steps', []):
                    step_num = correction['step_number']
                    
                    # Find and replace the step
                    for i, step in enumerate(corrected_steps):
                        if step.step_number == step_num:
                            corrected_steps[i] = CoTStep(
                                step_number=step_num,
                                reasoning=correction['reasoning'],
                                conclusion=correction['conclusion'],
                                confidence=float(correction.get('confidence', 0.8)),
                                dependencies=correction.get('dependencies', []),
                                validation_notes=correction.get('validation_notes', 'Corrected step')
                            )
                            break
                
                self.logger.info(f"Applied corrections to {len(correction_data.get('corrected_steps', []))} steps")
                return corrected_steps
                
        except (json.JSONDecodeError, ValueError) as e:
            self.logger.error(f"Failed to parse corrections: {e}")
            
        return cot_steps

    def _synthesize_final_answer(self, 
                                cot_steps: List[CoTStep], 
                                validation: CoTValidation,
                                query: str) -> str:
        """
        Synthesizes the final answer from validated CoT reasoning.
        """
        steps_summary = "\n".join([
            f"**Step {step.step_number}:** {step.conclusion}"
            for step in cot_steps
        ])
        
        synthesis_prompt = f"""
        Synthesize a comprehensive answer from the validated Chain of Thought reasoning.
        
        **Original Query:** {query}
        
        **Reasoning Steps:**
        {steps_summary}
        
        **Validation Status:** {"Valid" if validation.is_valid else "Partially Valid"} (Confidence: {validation.confidence_score:.2f})
        
        **Instructions:**
        1. Create a coherent, educational answer
        2. Show the logical flow of reasoning
        3. Include key insights from each step
        4. Acknowledge any uncertainties or limitations
        5. Use clear, educational language
        
        **If validation issues exist:** Address them transparently and note areas of uncertainty.
        
        **Format:** Provide a well-structured explanation that demonstrates the thinking process while being accessible to students.
        """
        
        return self.llm_interface.generate_response(synthesis_prompt)

    def run(self, query: str, user_role: str = 'student', socratic: bool = False, topic_class: str = "Auto") -> Dict[str, Any]:
        """
        Main execution with Chain of Thought reasoning and failure detection.
        """
        self.logger.info(f"CoT RAG processing query: '{query}'")
        
        # Detect if CoT is needed
        complexity = self._detect_query_complexity(query)
        self.logger.info(f"Query complexity: {complexity}")
        
        # Retrieve context (using your existing retrieval logic)
        try:
            # This would use your existing retrieval methods
            retrieved_chunks = self._execute_retrieval(query, user_role, top_k=8)
            context = "\n\n".join([chunk.get('text', '') for chunk in retrieved_chunks])
        except Exception as e:
            self.logger.error(f"Retrieval failed: {e}")
            context = "Limited context available due to retrieval issues."
            retrieved_chunks = []
        
        if complexity in ['high', 'medium']:
            # Generate Chain of Thought reasoning
            cot_steps = self._generate_cot_reasoning(query, context)
            
            # Validate reasoning
            validation = self._validate_cot_reasoning(cot_steps, query)
            
            # Attempt corrections if needed
            correction_attempts = 0
            while (not validation.is_valid and 
                   validation.confidence_score < self.min_confidence_threshold and 
                   correction_attempts < self.max_correction_attempts):
                
                self.logger.info(f"Attempting correction #{correction_attempts + 1}")
                cot_steps = self._correct_failed_reasoning(cot_steps, validation, query, context)
                validation = self._validate_cot_reasoning(cot_steps, query)
                correction_attempts += 1
            
            # Synthesize final answer
            final_answer = self._synthesize_final_answer(cot_steps, validation, query)
            
            # Prepare detailed response
            cot_analysis = {
                "complexity": complexity,
                "reasoning_steps": [
                    {
                        "step": step.step_number,
                        "reasoning": step.reasoning,
                        "conclusion": step.conclusion,
                        "confidence": step.confidence,
                        "validation_notes": step.validation_notes
                    } for step in cot_steps
                ],
                "validation": {
                    "is_valid": validation.is_valid,
                    "confidence": validation.confidence_score,
                    "issues": validation.issues,
                    "corrections": validation.corrections,
                    "failed_steps": validation.failed_steps
                },
                "correction_attempts": correction_attempts
            }
            
        else:
            # Simple reasoning for low complexity queries
            final_answer = self._generate_simple_answer(query, context)
            cot_analysis = {
                "complexity": complexity,
                "reasoning_steps": [],
                "validation": {"is_valid": True, "confidence": 0.8, "issues": [], "corrections": [], "failed_steps": []},
                "correction_attempts": 0
            }
        
        return {
            "answer": final_answer,
            "sources": [chunk.get('metadata', {}) for chunk in retrieved_chunks],
            "query_domain": self._detect_domain(query),
            "cot_analysis": cot_analysis,
            "reasoning_quality": validation.confidence_score if 'validation' in locals() else 0.8
        }
    
    def _execute_retrieval(self, query: str, user_role: str, top_k: int) -> List[Dict[str, Any]]:
        """
        Execute retrieval using the existing RAG infrastructure.
        """
        try:
            # Extract entities for knowledge graph retrieval
            entities = self._extract_entities_from_query_simple(query)
            
            # Get chunks from knowledge graph
            kg_chunks = self.graph_db.get_context_for_entities(entities)
            
            # Get chunks from vector store
            query_embedding = self.llm_interface.get_embedding(query, task_type="RETRIEVAL_QUERY")
            vector_chunks = []
            
            if query_embedding:
                where_filter = {"access_level": "student"} if user_role == 'student' else {}
                vector_chunks = self.vector_store.query(
                    query_embedding=query_embedding,
                    top_k=top_k,
                    where_filter=where_filter
                )
            
            # Combine and deduplicate
            all_chunks = kg_chunks + vector_chunks
            unique_chunks = {}
            for chunk in all_chunks:
                if chunk.get('metadata') and 'chunk_id' in chunk['metadata']:
                    chunk_id = chunk['metadata']['chunk_id']
                    if chunk_id not in unique_chunks:
                        unique_chunks[chunk_id] = chunk
            
            self.logger.info(f"Retrieved {len(unique_chunks)} chunks for query")
            return list(unique_chunks.values())
            
        except Exception as e:
            self.logger.error(f"Retrieval failed: {e}")
            return []

    def _extract_entities_from_query_simple(self, query: str) -> List[str]:
        """Simple entity extraction for retrieval."""
        # Extract key terms from query
        query_lower = query.lower()
        entities = []
        
        # Check for university mentions
        if "university" in query_lower or "missouri" in query_lower or "mizzou" in query_lower:
            entities.extend(["University of Missouri", "Mizzou", "Missouri", "university"])
        
        # Check for "firsts" related terms
        if "first" in query_lower:
            entities.extend(["first", "founded", "established", "started", "tradition", "pioneering"])
        
        # Add the main query terms
        words = query_lower.split()
        entities.extend([word for word in words if len(word) > 3])
        
        return entities
    
    def _detect_domain(self, query: str) -> str:
        """Detect query domain."""
        stem_keywords = ["algorithm", "mathematical", "formula", "optimization", "mse", "loss", "gradient"]
        return "STEM" if any(kw in query.lower() for kw in stem_keywords) else "Non-STEM"
    
    def _generate_simple_answer(self, query: str, context: str) -> str:
        """Generate simple answer for low complexity queries."""
        prompt = f"Answer this question concisely using the provided context:\n\nQuestion: {query}\n\nContext: {context}"
        return self.llm_interface.generate_response(prompt)