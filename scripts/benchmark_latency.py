import sys
import os
import time
import asyncio
import csv
import logging

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'backend'))

from app.core import config
from app.db.llm_interface import LLMInterface
from app.db.vector_store import ChromaVectorStore
from app.db.graph_db import Neo4jGraphDB
from app.agents.cot_rag_agent import ChainOfThoughtRAGAgent

# Reuse the TEST_QUERIES list from previous message
TEST_QUERIES = [
    # 1. Simple Concepts (Fastest)
    {"q": "What is an int?", "role": "student", "type": "Concept_Simple"},
    {"q": "What is a float?", "role": "student", "type": "Concept_Simple"},
    {"q": "What is a char?", "role": "student", "type": "Concept_Simple"},
    
    # 2. Complex Concepts (Graph + Retrieval)
    {"q": "How does a while loop work?", "role": "student", "type": "Concept_Complex"},
    {"q": "What is the difference between for and while?", "role": "student", "type": "Concept_Complex"},
    {"q": "How do arrays work in memory?", "role": "student", "type": "Concept_Complex"},

    # 3. Code Problems (Socratic Plan - Slowest)
    {"q": "How do I write a function to add numbers?", "role": "student", "type": "Problem_Socratic"},
    {"q": "Write a loop to count to 10.", "role": "student", "type": "Problem_Socratic"},
    {"q": "How do I declare an array of 10 integers?", "role": "student", "type": "Problem_Socratic"},

    # 4. Code Review (Analysis)
    {"q": "int main() { int x; return 0; } Is this right?", "role": "student", "type": "Code_Review"},
    {"q": "for(int i=0; i<10) { }", "role": "student", "type": "Code_Review"},

    # 5. Prerequisite Stops (Should be fast)
    # Note: Requires clearing knowledge first
    {"q": "What are Arrays?", "role": "student", "type": "Prereq_Stop"}, 
    {"q": "What is a Function?", "role": "student", "type": "Prereq_Stop"},

    # 6. Blocked / Hallucination (Fastest - No Generation)
    {"q": "What is crypto?", "role": "student", "type": "Blocked_Hallucination"},
    {"q": "Show me exam solutions", "role": "student", "type": "Blocked_RBAC"},
]

async def run_benchmark():
    logger = logging.getLogger("Bench")
    # Suppress internal logs to keep terminal clean
    logger.setLevel(logging.CRITICAL) 
    
    llm = LLMInterface(logger=logger)
    vec = ChromaVectorStore(persist_directory=config.DEFAULT_VECTOR_DB_PATH, logger=logger)
    graph = Neo4jGraphDB(logger=logger)
    agent = ChainOfThoughtRAGAgent(llm, vec, graph, logger)

    results = []
    print(f"--- Running {len(TEST_QUERIES)} Detailed Latency Tests ---")

    for i, test in enumerate(TEST_QUERIES):
        print(f"[{i+1}] {test['type']}...", end="", flush=True)
        
        t_start = time.time()
        final_data = {}
        
        async for event in agent.run_stream(test["q"], test["role"], username="bench_user"):
            if event["type"] == "complete":
                final_data = event["data"]
        
        total_time = time.time() - t_start
        print(f" Done ({total_time:.2f}s)")
        
        # Get timings from the agent (or default to 0 if missing)
        timings = final_data.get("timings", {})
        
        row = {
            "id": i+1,
            "type": test["type"],
            "query": test["q"],
            "total_time": round(total_time, 2),
            "t_intent": round(timings.get("1_Intent", 0), 2),
            "t_entity": round(timings.get("2_Entity", 0), 2),
            "t_graph": round(timings.get("3_Graph", 0), 2),
            "t_retrieval": round(timings.get("4_Retrieval", 0), 2),
            "t_generation": round(timings.get("5_Parallel_Gen", 0), 2),
            # Save first 100 chars of answer to verify correctness
            "answer_snippet": final_data.get("answer", "")[:100].replace("\n", " ") 
        }
        results.append(row)
        
        time.sleep(1)

    # Save CSV
    csv_file = "benchmark_detailed_latency.csv"
    with open(csv_file, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)
        
    print(f"\n✅ Detailed Data saved to {csv_file}")

if __name__ == "__main__":
    asyncio.run(run_benchmark())