# test_cot_failures.py - Script to test and analyze CoT failures

import asyncio
import aiohttp
import json
import time
from typing import List, Dict, Any
import pandas as pd
from datetime import datetime

class CoTFailureTester:
    """
    Tests Chain of Thought reasoning with challenging queries designed to expose failure modes.
    """
    
    def __init__(self, api_base_url="http://127.0.0.1:8000"):
        self.api_base_url = api_base_url
        self.test_results = []
        
    def get_challenging_test_queries(self) -> List[Dict[str, Any]]:
        """
        Returns a set of queries designed to test different failure modes.
        """
        return [
            # Mathematical reasoning challenges
            {
                "query": "Derive the gradient descent update rule and explain why it minimizes the cost function",
                "expected_failures": ["mathematical_error", "incomplete_reasoning"],
                "complexity": "high",
                "domain": "machine_learning"
            },
            {
                "query": "What is the difference between MSE and Huber loss, and when should you use each?",
                "expected_failures": ["incomplete_reasoning"],
                "complexity": "medium", 
                "domain": "machine_learning"
            },
            {
                "query": "Prove that the derivative of x^2 is 2x using the definition of a derivative",
                "expected_failures": ["mathematical_error", "incomplete_reasoning"],
                "complexity": "high",
                "domain": "mathematics"
            },
            
            # Logical reasoning challenges
            {
                "query": "If all cats are mammals and Fluffy is a cat, what can we conclude about dogs?",
                "expected_failures": ["logical_inconsistency", "scope_creep"],
                "complexity": "medium",
                "domain": "logic"
            },
            {
                "query": "Explain why correlation does not imply causation with examples",
                "expected_failures": ["incomplete_reasoning", "overconfident_conclusion"],
                "complexity": "medium",
                "domain": "statistics"
            },
            
            # Algorithmic reasoning challenges
            {
                "query": "How does quicksort work and what is its time complexity in the worst case?",
                "expected_failures": ["incomplete_reasoning"],
                "complexity": "high",
                "domain": "algorithms"
            },
            {
                "query": "Explain the backpropagation algorithm step by step for a simple neural network",
                "expected_failures": ["mathematical_error", "incomplete_reasoning"],
                "complexity": "high",
                "domain": "machine_learning"
            },
            
            # Comparison challenges (often lead to scope creep)
            {
                "query": "Compare bubble sort and merge sort in terms of efficiency and implementation complexity",
                "expected_failures": ["scope_creep", "incomplete_reasoning"],
                "complexity": "medium",
                "domain": "algorithms"
            },
            {
                "query": "What are the advantages and disadvantages of decision trees vs neural networks?",
                "expected_failures": ["scope_creep", "overconfident_conclusion"],
                "complexity": "medium",
                "domain": "machine_learning"
            },
            
            # Ambiguous or trick questions
            {
                "query": "Why is it impossible to solve the halting problem?",
                "expected_failures": ["overconfident_conclusion", "incomplete_reasoning"],
                "complexity": "high",
                "domain": "computer_science"
            },
            
            # Multi-step reasoning requiring careful dependency tracking
            {
                "query": "If a machine learning model has 90% accuracy on training data but 60% on test data, what might be wrong and how would you fix it?",
                "expected_failures": ["incomplete_reasoning", "logical_inconsistency"],
                "complexity": "high",
                "domain": "machine_learning"
            }
        ]
    
    async def test_single_query(self, test_case: Dict[str, Any]) -> Dict[str, Any]:
        """
        Tests a single query and analyzes the results.
        """
        query = test_case["query"]
        print(f"\nTesting: {query}")
        
        try:
            # Call the CoT endpoint
            async with aiohttp.ClientSession() as session:
                payload = {
                    "message": query,
                    "user_role": "student",
                    "socratic": False,
                    "topic_class": "STEM",
                    "enable_cot": True
                }
                
                start_time = time.time()
                async with session.post(
                    f"{self.api_base_url}/api/v1/chat",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=120)
                ) as response:
                    
                    if response.status == 200:
                        result = await response.json()
                        processing_time = time.time() - start_time
                        
                        # Analyze the CoT response
                        analysis = self._analyze_cot_response(result, test_case)
                        analysis.update({
                            "query": query,
                            "processing_time": processing_time,
                            "success": True,
                            "timestamp": datetime.now().isoformat()
                        })
                        
                        return analysis
                    else:
                        error_text = await response.text()
                        return {
                            "query": query,
                            "success": False,
                            "error": f"HTTP {response.status}: {error_text}",
                            "timestamp": datetime.now().isoformat()
                        }
                        
        except Exception as e:
            return {
                "query": query,
                "success": False,
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
    
    def _analyze_cot_response(self, result: Dict[str, Any], test_case: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyzes the CoT response for failure patterns.
        """
        cot_analysis = result.get("cot_analysis", {})
        
        analysis = {
            "test_case": test_case,
            "response_quality": result.get("reasoning_quality", 0.0),
            "complexity_detected": cot_analysis.get("complexity", "unknown"),
            "num_reasoning_steps": len(cot_analysis.get("reasoning_steps", [])),
            "validation_passed": cot_analysis.get("validation", {}).get("is_valid", False),
            "confidence_score": cot_analysis.get("validation", {}).get("confidence", 0.0),
            "correction_attempts": cot_analysis.get("correction_attempts", 0),
            "detected_issues": cot_analysis.get("validation", {}).get("issues", []),
            "applied_corrections": cot_analysis.get("validation", {}).get("corrections", [])
        }
        
        # Check if expected failures were detected
        expected_failures = test_case.get("expected_failures", [])
        detected_failures = []
        
        for issue in analysis["detected_issues"]:
            issue_lower = issue.lower()
            if any(expected in issue_lower for expected in expected_failures):
                detected_failures.append(issue)
        
        analysis["expected_failures_detected"] = len(detected_failures)
        analysis["unexpected_behavior"] = analysis["validation_passed"] and len(expected_failures) > 0
        
        # Analyze reasoning step quality
        reasoning_steps = cot_analysis.get("reasoning_steps", [])
        if reasoning_steps:
            confidences = [step.get("confidence", 0.5) for step in reasoning_steps]
            analysis["step_confidence_stats"] = {
                "min": min(confidences),
                "max": max(confidences), 
                "avg": sum(confidences) / len(confidences),
                "std": self._calculate_std(confidences)
            }
            
            # Check for confidence inconsistencies
            if max(confidences) - min(confidences) > 0.5:
                analysis["confidence_inconsistent"] = True
        
        return analysis
    
    def _calculate_std(self, values: List[float]) -> float:
        """Calculate standard deviation."""
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
        return variance ** 0.5
    
    async def run_comprehensive_test(self) -> Dict[str, Any]:
        """
        Runs a comprehensive test of CoT failure modes.
        """
        print("Starting Comprehensive CoT Failure Analysis")
        print("=" * 50)
        
        test_cases = self.get_challenging_test_queries()
        results = []
        
        for i, test_case in enumerate(test_cases, 1):
            print(f"\n[{i}/{len(test_cases)}] Testing: {test_case['domain']} - {test_case['complexity']}")
            result = await self.test_single_query(test_case)
            results.append(result)
            
            # Print immediate feedback
            if result.get("success"):
                quality = result.get("response_quality", 0)
                passed = result.get("validation_passed", False)
                print(f"   Quality: {quality:.2f} | Validation: {'✅' if passed else '❌'} | Steps: {result.get('num_reasoning_steps', 0)}")
                
                if result.get("detected_issues"):
                    print(f"   Issues: {', '.join(result['detected_issues'][:2])}...")
            else:
                print(f"   ❌ Failed: {result.get('error', 'Unknown error')}")
        
        # Generate comprehensive analysis
        return self._generate_test_report(results)
    
    def _generate_test_report(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Generates a comprehensive test report.
        """
        successful_tests = [r for r in results if r.get("success")]
        failed_tests = [r for r in results if not r.get("success")]
        
        if not successful_tests:
            return {"error": "No successful tests to analyze"}
        
        # Overall statistics
        total_tests = len(results)
        success_rate = len(successful_tests) / total_tests
        
        # Quality analysis
        qualities = [r.get("response_quality", 0) for r in successful_tests]
        avg_quality = sum(qualities) / len(qualities) if qualities else 0
        
        # Validation analysis
        validation_passes = sum(1 for r in successful_tests if r.get("validation_passed"))
        validation_rate = validation_passes / len(successful_tests)
        
        # Complexity analysis
        complexity_stats = {}
        for result in successful_tests:
            complexity = result.get("test_case", {}).get("complexity", "unknown")
            if complexity not in complexity_stats:
                complexity_stats[complexity] = {"total": 0, "passed": 0, "avg_quality": 0}
            
            complexity_stats[complexity]["total"] += 1
            if result.get("validation_passed"):
                complexity_stats[complexity]["passed"] += 1
            complexity_stats[complexity]["avg_quality"] += result.get("response_quality", 0)
        
        # Calculate averages
        for complexity in complexity_stats:
            stats = complexity_stats[complexity]
            stats["pass_rate"] = stats["passed"] / stats["total"]
            stats["avg_quality"] = stats["avg_quality"] / stats["total"]
        
        # Failure pattern analysis
        all_issues = []
        for result in successful_tests:
            all_issues.extend(result.get("detected_issues", []))
        
        issue_counts = {}
        for issue in all_issues:
            issue_counts[issue] = issue_counts.get(issue, 0) + 1
        
        top_issues = sorted(issue_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        
        # Correction effectiveness
        correction_attempts = [r.get("correction_attempts", 0) for r in successful_tests]
        avg_corrections = sum(correction_attempts) / len(correction_attempts) if correction_attempts else 0
        
        report = {
            "summary": {
                "total_tests": total_tests,
                "success_rate": success_rate,
                "validation_pass_rate": validation_rate,
                "average_quality": avg_quality,
                "average_correction_attempts": avg_corrections
            },
            "complexity_analysis": complexity_stats,
            "failure_patterns": {
                "most_common_issues": top_issues,
                "failed_tests": len(failed_tests)
            },
            "recommendations": self._generate_recommendations(successful_tests),
            "detailed_results": results
        }
        
        return report
    
    def _generate_recommendations(self, results: List[Dict[str, Any]]) -> List[str]:
        """
        Generates improvement recommendations based on test results.
        """
        recommendations = []
        
        # Analyze patterns in the results
        low_quality_results = [r for r in results if r.get("response_quality", 0) < 0.6]
        high_correction_results = [r for r in results if r.get("correction_attempts", 0) > 1]
        
        if len(low_quality_results) > len(results) * 0.3:
            recommendations.append("Consider improving base reasoning quality before validation")
        
        if len(high_correction_results) > len(results) * 0.2:
            recommendations.append("Optimize initial reasoning to reduce correction cycles")
        
        # Check domain-specific issues
        domain_stats = {}
        for result in results:
            domain = result.get("test_case", {}).get("domain", "unknown")
            if domain not in domain_stats:
                domain_stats[domain] = []
            domain_stats[domain].append(result.get("response_quality", 0))
        
        for domain, qualities in domain_stats.items():
            avg_quality = sum(qualities) / len(qualities)
            if avg_quality < 0.6:
                recommendations.append(f"Focus on improving {domain} reasoning capabilities")
        
        return recommendations
    
    def export_results(self, results: Dict[str, Any], filepath: str = None):
        """
        Exports test results for further analysis.
        """
        if filepath is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = f"cot_test_results_{timestamp}.json"
        
        try:
            with open(filepath, 'w') as f:
                json.dump(results, f, indent=2, default=str)
            print(f"\nResults exported to: {filepath}")
            
            # Also create a CSV summary for easy analysis
            csv_filepath = filepath.replace('.json', '.csv')
            self._create_csv_summary(results, csv_filepath)
            
        except Exception as e:
            print(f"Error exporting results: {e}")
    
    def _create_csv_summary(self, results: Dict[str, Any], filepath: str):
        """
        Creates a CSV summary of test results for spreadsheet analysis.
        """
        try:
            detailed_results = results.get("detailed_results", [])
            if not detailed_results:
                return
            
            # Prepare data for CSV
            csv_data = []
            for result in detailed_results:
                if result.get("success"):
                    test_case = result.get("test_case", {})
                    csv_data.append({
                        "query": result.get("query", ""),
                        "domain": test_case.get("domain", ""),
                        "complexity": test_case.get("complexity", ""),
                        "response_quality": result.get("response_quality", 0),
                        "validation_passed": result.get("validation_passed", False),
                        "confidence_score": result.get("confidence_score", 0),
                        "num_steps": result.get("num_reasoning_steps", 0),
                        "correction_attempts": result.get("correction_attempts", 0),
                        "processing_time": result.get("processing_time", 0),
                        "issues_detected": "; ".join(result.get("detected_issues", [])),
                        "expected_failures_detected": result.get("expected_failures_detected", 0)
                    })
            
            if csv_data:
                df = pd.DataFrame(csv_data)
                df.to_csv(filepath, index=False)
                print(f"CSV summary exported to: {filepath}")
                
        except ImportError:
            print("pandas not available, skipping CSV export")
        except Exception as e:
            print(f"Error creating CSV summary: {e}")

# Main execution function
async def main():
    """
    Main function to run CoT failure analysis.
    """
    print("Chain of Thought Failure Analysis System")
    print("=" * 50)
    
    tester = CoTFailureTester()
    
    # Test server connectivity first
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("http://127.0.0.1:8000/health") as response:
                if response.status == 200:
                    health_data = await response.json()
                    print("Server Status:")
                    for component, status in health_data.get("components", {}).items():
                        print(f"  {component}: {'✓' if status else '✗'}")
                    
                    if not health_data.get("components", {}).get("cot_rag_agent"):
                        print("\nWarning: CoT RAG agent not available. Make sure you've implemented the CoT agent.")
                        return
                else:
                    print(f"Server health check failed: {response.status}")
                    return
                    
    except Exception as e:
        print(f"Cannot connect to server: {e}")
        print("Make sure your server is running on http://127.0.0.1:8000")
        return
    
    print("\nStarting CoT failure analysis tests...")
    print("This will test various challenging queries to identify failure patterns.")
    print("Expected duration: 5-10 minutes\n")
    
    # Run comprehensive tests
    results = await tester.run_comprehensive_test()
    
    # Display summary
    print("\n" + "=" * 50)
    print("COMPREHENSIVE TEST RESULTS")
    print("=" * 50)
    
    summary = results.get("summary", {})
    print(f"Total Tests: {summary.get('total_tests', 0)}")
    print(f"Success Rate: {summary.get('success_rate', 0):.1%}")
    print(f"Validation Pass Rate: {summary.get('validation_pass_rate', 0):.1%}")
    print(f"Average Quality: {summary.get('average_quality', 0):.2f}")
    print(f"Average Corrections: {summary.get('average_correction_attempts', 0):.1f}")
    
    # Complexity analysis
    print(f"\nComplexity Analysis:")
    complexity_stats = results.get("complexity_analysis", {})
    for complexity, stats in complexity_stats.items():
        print(f"  {complexity.upper()}: Pass Rate {stats.get('pass_rate', 0):.1%}, Avg Quality {stats.get('avg_quality', 0):.2f}")
    
    # Top failure patterns
    print(f"\nMost Common Issues:")
    failure_patterns = results.get("failure_patterns", {})
    top_issues = failure_patterns.get("most_common_issues", [])
    for issue, count in top_issues:
        print(f"  • {issue} ({count} occurrences)")
    
    # Recommendations
    print(f"\nRecommendations:")
    for rec in results.get("recommendations", []):
        print(f"  → {rec}")
    
    # Export results
    tester.export_results(results)
    
    print(f"\nAnalysis complete! Check the exported files for detailed results.")
    print(f"Use the CSV file for spreadsheet analysis and the JSON for programmatic analysis.")

if __name__ == "__main__":
    try:
        import aiohttp
        import pandas as pd
    except ImportError as e:
        print(f"Missing required package: {e}")
        print("Install with: pip install aiohttp pandas")
        exit(1)
    
    asyncio.run(main())