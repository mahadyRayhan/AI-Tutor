import sys
import os
import time
import asyncio
import csv
import logging

# 1. Setup Paths
# Add 'backend' and current dir to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'backend'))
sys.path.append(os.path.dirname(__file__))

from app.core import config
from app.db.llm_interface import LLMInterface
from app.db.vector_store import ChromaVectorStore
from app.db.graph_db import Neo4jGraphDB
from app.agents.cot_rag_agent import ChainOfThoughtRAGAgent
from app.core.settings_manager import settings_manager

# Import Unified Dataset
from test_suite_master import MASTER_DATASET

# Map Master Dataset to the format expected by this script
TEST_QUERIES = [
    {
        "q": item["q"],
        "role": item["role"],
        "type": item["cat"] # Use the Category as the Type
    }
    for item in MASTER_DATASET
]

async def run_benchmark():
    # Setup Logger
    logging.basicConfig(level=logging.CRITICAL)
    logger = logging.getLogger("DetailedBench")
    
    # Init System
    llm = LLMInterface(logger=logger)
    vec = ChromaVectorStore(persist_directory=config.DEFAULT_VECTOR_DB_PATH, logger=logger)
    graph = Neo4jGraphDB(logger=logger)
    agent = ChainOfThoughtRAGAgent(llm, vec, graph, logger)

    # PRE-CONFIG: Ensure consistent behavior for boundary tests
    settings_manager.update_topic("Switch Statement", False)

    results = []
    print(f"--- Running Detailed Latency on {len(TEST_QUERIES)} queries ---")
    print(f"{'ID':<4} | {'Type':<15} | {'Total (s)':<10} | {'Status'}")
    print("-" * 60)

    for i, test in enumerate(TEST_QUERIES):
        # Unique username to reset prereq checks if needed
        username = f"bench_user_{i}"
        
        t_start = time.time()
        final_data = {}
        
        try:
            # Run Agent Stream
            async for event in agent.run_stream(test["q"], test["role"], username=username):
                if event["type"] == "complete":
                    final_data = event["data"]
        except Exception as e:
            print(f"Error on {test['q']}: {e}")
            continue
        
        total_time = time.time() - t_start
        
        # Get timings (Default to 0 if missing)
        timings = final_data.get("timings", {})
        
        # Updated Keys to match cot_rag_agent.py
        # Uses .get() with fallback keys just in case
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
            # Save snippet to verify if it was a refusal or answer
            "answer_snippet": final_data.get("answer", "")[:50].replace("\n", " ") 
        }
        results.append(row)
        
        print(f"{i+1:<4} | {test['type']:<15} | {total_time:.2f}s       | Done")
        
        # Slight delay to prevent rate limits
        await asyncio.sleep(0.5)

    # Ensure output directory exists
    out_dir = os.path.join(os.path.dirname(__file__), "..", "eval_result")
    if not os.path.exists(out_dir):
        os.makedirs(out_dir)

    # Save CSV
    csv_file = os.path.join(out_dir, "benchmark_detailed_latency.csv")
    with open(csv_file, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)
        
    print(f"\n✅ Detailed Data saved to {csv_file}")

if __name__ == "__main__":
    asyncio.run(run_benchmark())