# backend/app/analysis/cot_failure_analyzer.py

import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, List, Any, Tuple
from dataclasses import dataclass, asdict
from enum import Enum

class FailureType(Enum):
    LOGICAL_INCONSISTENCY = "logical_inconsistency"
    MATHEMATICAL_ERROR = "mathematical_error"
    INCOMPLETE_REASONING = "incomplete_reasoning"
    OVERCONFIDENT_CONCLUSION = "overconfident_conclusion"
    CIRCULAR_REASONING = "circular_reasoning"
    FALSE_PREMISE = "false_premise"
    SCOPE_CREEP = "scope_creep"  # Reasoning goes beyond the query
    CONTEXT_MISINTERPRETATION = "context_misinterpretation"

@dataclass
class FailureCase:
    query: str
    expected_reasoning: str
    actual_cot_steps: List[Dict[str, Any]]
    failure_types: List[FailureType]
    correction_attempts: int
    final_confidence: float
    human_assessment: str = ""
    timestamp: datetime = None

class CoTFailureAnalyzer:
    """
    Analyzes and categorizes Chain of Thought failures for research and improvement.
    """
    
    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.failure_cases = []
        self.failure_patterns = {}
        
    def analyze_failure(self, 
                       query: str, 
                       cot_steps: List[Dict[str, Any]], 
                       validation_result: Dict[str, Any],
                       correction_attempts: int) -> Dict[str, Any]:
        """
        Analyzes a CoT failure and categorizes the failure types.
        """
        failure_analysis = {
            "failure_types": [],
            "severity": "low",
            "root_causes": [],
            "prevention_strategies": [],
            "correction_effectiveness": 0.0
        }
        
        # Detect specific failure patterns
        failure_types = self._detect_failure_types(cot_steps, validation_result)
        failure_analysis["failure_types"] = [ft.value for ft in failure_types]
        
        # Assess severity
        severity = self._assess_failure_severity(failure_types, validation_result)
        failure_analysis["severity"] = severity
        
        # Identify root causes
        root_causes = self._identify_root_causes(cot_steps, failure_types)
        failure_analysis["root_causes"] = root_causes
        
        # Suggest prevention strategies
        prevention = self._suggest_prevention_strategies(failure_types, root_causes)
        failure_analysis["prevention_strategies"] = prevention
        
        # Evaluate correction effectiveness
        effectiveness = self._evaluate_correction_effectiveness(correction_attempts, validation_result)
        failure_analysis["correction_effectiveness"] = effectiveness
        
        # Store failure case for pattern analysis
        failure_case = FailureCase(
            query=query,
            expected_reasoning="",  # Would need human annotation
            actual_cot_steps=cot_steps,
            failure_types=failure_types,
            correction_attempts=correction_attempts,
            final_confidence=validation_result.get("confidence_score", 0.0),
            timestamp=datetime.now()
        )
        self.failure_cases.append(failure_case)
        
        return failure_analysis
    
    def _detect_failure_types(self, 
                             cot_steps: List[Dict[str, Any]], 
                             validation_result: Dict[str, Any]) -> List[FailureType]:
        """
        Detects specific types of reasoning failures.
        """
        failures = []
        issues = validation_result.get("issues", [])
        
        # Analyze validation issues
        for issue in issues:
            issue_lower = issue.lower()
            
            if any(keyword in issue_lower for keyword in ["logical", "inconsistent", "contradiction"]):
                failures.append(FailureType.LOGICAL_INCONSISTENCY)
            
            if any(keyword in issue_lower for keyword in ["mathematical", "formula", "calculation"]):
                failures.append(FailureType.MATHEMATICAL_ERROR)
            
            if any(keyword in issue_lower for keyword in ["incomplete", "missing", "gap"]):
                failures.append(FailureType.INCOMPLETE_REASONING)
            
            if any(keyword in issue_lower for keyword in ["overconfident", "unsupported", "weak evidence"]):
                failures.append(FailureType.OVERCONFIDENT_CONCLUSION)
            
            if any(keyword in issue_lower for keyword in ["circular", "assumes", "begging"]):
                failures.append(FailureType.CIRCULAR_REASONING)
            
            if any(keyword in issue_lower for keyword in ["false premise", "incorrect assumption"]):
                failures.append(FailureType.FALSE_PREMISE)
        
        # Analyze step patterns
        if len(cot_steps) > 0:
            # Check for scope creep
            if self._has_scope_creep(cot_steps):
                failures.append(FailureType.SCOPE_CREEP)
            
            # Check confidence patterns
            confidences = [step.get("confidence", 0.5) for step in cot_steps]
            if max(confidences) > 0.9 and validation_result.get("confidence_score", 0) < 0.6:
                failures.append(FailureType.OVERCONFIDENT_CONCLUSION)
        
        return list(set(failures))  # Remove duplicates
    
    def _has_scope_creep(self, cot_steps: List[Dict[str, Any]]) -> bool:
        """
        Detects if reasoning goes beyond the original query scope.
        """
        # Simple heuristic: if later steps introduce concepts not in earlier steps
        early_concepts = set()
        if len(cot_steps) > 0:
            early_text = cot_steps[0].get("reasoning", "") + cot_steps[0].get("conclusion", "")
            early_concepts = set(early_text.lower().split())
        
        for step in cot_steps[2:]:  # Check later steps
            step_text = step.get("reasoning", "") + step.get("conclusion", "")
            step_concepts = set(step_text.lower().split())
            
            # If step introduces many new concepts, might be scope creep
            new_concepts = step_concepts - early_concepts
            if len(new_concepts) > len(early_concepts):
                return True
        
        return False
    
    def _assess_failure_severity(self, 
                               failure_types: List[FailureType], 
                               validation_result: Dict[str, Any]) -> str:
        """
        Assesses the severity of reasoning failures.
        """
        confidence = validation_result.get("confidence_score", 0.5)
        
        # Critical failures
        critical_failures = [
            FailureType.MATHEMATICAL_ERROR,
            FailureType.FALSE_PREMISE,
            FailureType.CIRCULAR_REASONING
        ]
        
        if any(ft in failure_types for ft in critical_failures):
            return "critical"
        
        if confidence < 0.3:
            return "high"
        elif confidence < 0.6:
            return "medium"
        else:
            return "low"
    
    def _identify_root_causes(self, 
                            cot_steps: List[Dict[str, Any]], 
                            failure_types: List[FailureType]) -> List[str]:
        """
        Identifies potential root causes of failures.
        """
        causes = []
        
        if FailureType.MATHEMATICAL_ERROR in failure_types:
            causes.append("Insufficient mathematical knowledge in training data")
            causes.append("Lack of formula verification step")
        
        if FailureType.LOGICAL_INCONSISTENCY in failure_types:
            causes.append("Inadequate logical reasoning training")
            causes.append("Missing consistency checking between steps")
        
        if FailureType.INCOMPLETE_REASONING in failure_types:
            causes.append("Premature conclusion generation")
            causes.append("Insufficient step decomposition")
        
        if FailureType.OVERCONFIDENT_CONCLUSION in failure_types:
            causes.append("Poor confidence calibration")
            causes.append("Insufficient uncertainty modeling")
        
        if len(cot_steps) < 2:
            causes.append("Inadequate problem decomposition")
        
        if len(cot_steps) > 10:
            causes.append("Over-decomposition leading to error propagation")
        
        return causes
    
    def _suggest_prevention_strategies(self, 
                                     failure_types: List[FailureType], 
                                     root_causes: List[str]) -> List[str]:
        """
        Suggests strategies to prevent similar failures.
        """
        strategies = []
        
        if FailureType.MATHEMATICAL_ERROR in failure_types:
            strategies.extend([
                "Add mathematical verification step",
                "Include formula checking against known references",
                "Implement dimensional analysis for equations"
            ])
        
        if FailureType.LOGICAL_INCONSISTENCY in failure_types:
            strategies.extend([
                "Add logical consistency validation between steps",
                "Implement premise tracking throughout reasoning",
                "Add contradiction detection mechanisms"
            ])
        
        if FailureType.INCOMPLETE_REASONING in failure_types:
            strategies.extend([
                "Enforce minimum step requirements for complex problems",
                "Add completeness checking against query requirements",
                "Implement step dependency verification"
            ])
        
        if FailureType.OVERCONFIDENT_CONCLUSION in failure_types:
            strategies.extend([
                "Implement confidence calibration training",
                "Add uncertainty quantification methods",
                "Require evidence strength assessment"
            ])
        
        return list(set(strategies))
    
    def _evaluate_correction_effectiveness(self, 
                                         correction_attempts: int, 
                                         final_validation: Dict[str, Any]) -> float:
        """
        Evaluates how effective the correction attempts were.
        """
        if correction_attempts == 0:
            return 1.0  # No correction needed
        
        final_confidence = final_validation.get("confidence_score", 0.0)
        is_valid = final_validation.get("is_valid", False)
        
        # Effectiveness based on final state
        base_effectiveness = final_confidence if is_valid else final_confidence * 0.5
        
        # Penalty for multiple attempts
        attempt_penalty = max(0, (correction_attempts - 1) * 0.1)
        
        return max(0, base_effectiveness - attempt_penalty)
    
    def generate_failure_report(self) -> Dict[str, Any]:
        """
        Generates a comprehensive failure analysis report.
        """
        if not self.failure_cases:
            return {"message": "No failure cases recorded"}
        
        # Aggregate statistics
        total_cases = len(self.failure_cases)
        failure_type_counts = {}
        severity_counts = {"low": 0, "medium": 0, "high": 0, "critical": 0}
        
        for case in self.failure_cases:
            for failure_type in case.failure_types:
                failure_type_counts[failure_type.value] = failure_type_counts.get(failure_type.value, 0) + 1
        
        # Calculate patterns
        common_patterns = self._identify_common_patterns()
        
        # Improvement recommendations
        recommendations = self._generate_improvement_recommendations()
        
        report = {
            "summary": {
                "total_failure_cases": total_cases,
                "most_common_failures": sorted(failure_type_counts.items(), key=lambda x: x[1], reverse=True)[:5],
                "average_correction_attempts": sum(case.correction_attempts for case in self.failure_cases) / total_cases,
                "average_final_confidence": sum(case.final_confidence for case in self.failure_cases) / total_cases
            },
            "failure_patterns": common_patterns,
            "improvement_recommendations": recommendations,
            "detailed_cases": [asdict(case) for case in self.failure_cases[-10:]]  # Last 10 cases
        }
        
        return report
    
    def _identify_common_patterns(self) -> Dict[str, Any]:
        """
        Identifies common patterns across failure cases.
        """
        patterns = {
            "query_characteristics": {},
            "step_patterns": {},
            "confidence_patterns": {}
        }
        
        # Analyze query characteristics
        math_queries = 0
        algorithm_queries = 0
        comparison_queries = 0
        
        for case in self.failure_cases:
            query_lower = case.query.lower()
            if any(word in query_lower for word in ["formula", "equation", "calculate", "derive"]):
                math_queries += 1
            if any(word in query_lower for word in ["algorithm", "step by step", "process"]):
                algorithm_queries += 1
            if any(word in query_lower for word in ["difference", "compare", "versus", "vs"]):
                comparison_queries += 1
        
        patterns["query_characteristics"] = {
            "math_heavy_failure_rate": math_queries / len(self.failure_cases),
            "algorithm_failure_rate": algorithm_queries / len(self.failure_cases),
            "comparison_failure_rate": comparison_queries / len(self.failure_cases)
        }
        
        return patterns
    
    def _generate_improvement_recommendations(self) -> List[Dict[str, Any]]:
        """
        Generates specific recommendations for improving CoT performance.
        """
        recommendations = []
        
        # Analyze failure patterns to generate recommendations
        failure_type_counts = {}
        for case in self.failure_cases:
            for failure_type in case.failure_types:
                failure_type_counts[failure_type] = failure_type_counts.get(failure_type, 0) + 1
        
        # Top recommendations based on most common failures
        if failure_type_counts.get(FailureType.MATHEMATICAL_ERROR, 0) > 2:
            recommendations.append({
                "priority": "high",
                "category": "mathematical_reasoning",
                "recommendation": "Implement mathematical verification module",
                "implementation": "Add formula checking and dimensional analysis steps"
            })
        
        if failure_type_counts.get(FailureType.LOGICAL_INCONSISTENCY, 0) > 2:
            recommendations.append({
                "priority": "high", 
                "category": "logical_consistency",
                "recommendation": "Add inter-step consistency validation",
                "implementation": "Check for contradictions between reasoning steps"
            })
        
        if failure_type_counts.get(FailureType.OVERCONFIDENT_CONCLUSION, 0) > 2:
            recommendations.append({
                "priority": "medium",
                "category": "confidence_calibration", 
                "recommendation": "Improve confidence estimation",
                "implementation": "Train confidence predictor on validated reasoning examples"
            })
        
        return recommendations
    
    def export_failure_data(self, filepath: str) -> bool:
        """
        Exports failure case data for external analysis.
        """
        try:
            export_data = {
                "export_timestamp": datetime.now().isoformat(),
                "failure_cases": [asdict(case) for case in self.failure_cases],
                "summary_stats": self.generate_failure_report()
            }
            
            with open(filepath, 'w') as f:
                json.dump(export_data, f, indent=2, default=str)
            
            self.logger.info(f"Exported {len(self.failure_cases)} failure cases to {filepath}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to export failure data: {e}")
            return False
    
    def test_cot_robustness(self, test_queries: List[str], cot_agent) -> Dict[str, Any]:
        """
        Tests CoT robustness with a set of challenging queries.
        """
        test_results = {
            "total_queries": len(test_queries),
            "failures_detected": 0,
            "correction_success_rate": 0,
            "detailed_results": []
        }
        
        successful_corrections = 0
        
        for query in test_queries:
            try:
                result = cot_agent.run(query)
                cot_analysis = result.get("cot_analysis", {})
                
                validation = cot_analysis.get("validation", {})
                is_valid = validation.get("is_valid", True)
                correction_attempts = cot_analysis.get("correction_attempts", 0)
                
                if not is_valid:
                    test_results["failures_detected"] += 1
                    
                    if correction_attempts > 0 and validation.get("confidence_score", 0) > 0.6:
                        successful_corrections += 1
                
                test_results["detailed_results"].append({
                    "query": query,
                    "is_valid": is_valid,
                    "confidence": validation.get("confidence_score", 0),
                    "correction_attempts": correction_attempts,
                    "issues": validation.get("issues", [])
                })
                
            except Exception as e:
                self.logger.error(f"Error testing query '{query}': {e}")
                test_results["detailed_results"].append({
                    "query": query,
                    "error": str(e)
                })
        
        if test_results["failures_detected"] > 0:
            test_results["correction_success_rate"] = successful_corrections / test_results["failures_detected"]
        
        return test_results