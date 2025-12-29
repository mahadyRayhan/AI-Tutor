import sys
import os
import time
import asyncio
import csv
import logging
import statistics

# 1. Setup Path for Backend
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'backend'))
# 2. Setup Path for Sibling Scripts (to load test_suite_master)
sys.path.append(os.path.dirname(__file__))

from app.core import config
from app.db.llm_interface import LLMInterface
from app.db.vector_store import ChromaVectorStore
from app.db.graph_db import Neo4jGraphDB
from app.agents.cot_rag_agent import ChainOfThoughtRAGAgent
from app.core.settings_manager import settings_manager

# Import the Single Source of Truth
from test_suite_master import MASTER_DATASET

async def run_benchmark():
    # Setup - Silence logs
    logging.basicConfig(level=logging.CRITICAL)
    logger = logging.getLogger("PerfBench")
    
    llm = LLMInterface(logger=logger)
    vec = ChromaVectorStore(persist_directory=config.DEFAULT_VECTOR_DB_PATH, logger=logger)
    graph = Neo4jGraphDB(logger=logger)
    agent = ChainOfThoughtRAGAgent(llm, vec, graph, logger)

    # Use master dataset
    TEST_SET = [
        {
            "q": item["q"], 
            "role": item["role"], 
            "type": item["cat"]
        } 
        for item in MASTER_DATASET
    ]

    # Ensure output dir exists
    out_dir = os.path.join(os.path.dirname(__file__), "..", "eval_result")
    if not os.path.exists(out_dir): os.makedirs(out_dir)

    # Update CSV path
    csv_file = os.path.join(out_dir, "benchmark_engineering_performance.csv")

    # PRE-CONFIG: Ensure specific topics are locked to trigger Blocks
    # This aligns with the "Boundary" category tests
    settings_manager.update_topic("Switch Statement", False) 
    
    results = []
    print(f"{'Type':<20} | {'Security (ms)':<15} | {'Retrieval (ms)':<15} | {'Generation (s)':<15}")
    print("-" * 70)

    for i, test in enumerate(TEST_SET):
        # Clean user state for consistent prereq checks
        username = f"bench_user_{i}" 
        
        t_start = time.time()
        final_data = {}
        
        # Run Agent
        try:
            async for event in agent.run_stream(test["q"], "student", username=username):
                if event["type"] == "complete":
                    final_data = event["data"]
        except Exception as e:
            print(f"Error on {test['q']}: {e}")
            continue
        
        timings = final_data.get("timings", {})
        
        # --- CALCULATE METRICS ROBUSTLY ---
        # 1. Security Overhead (Intent + Entity + Graph)
        t_intent = timings.get("1_Intent_Class", 0) or timings.get("1_Intent", 0)
        t_entity = timings.get("2_Entity_Extract", 0) or timings.get("2_Entity", 0)
        t_graph = timings.get("3_Graph_Check", 0) or timings.get("3_Graph", 0)
        
        t_sec_total = (t_intent + t_entity + t_graph) * 1000 # ms
        
        # 2. Retrieval Time
        t_ret = (timings.get("4_Retrieval", 0)) * 1000 # ms
        
        # 3. Generation Time
        t_gen = timings.get("5_Parallel_Gen", 0) # Seconds
        
        print(f"{test['type']:<20} | {t_sec_total:.2f} ms       | {t_ret:.2f} ms       | {t_gen:.2f} s")
        
        results.append({
            "type": test["type"],
            "security_overhead_ms": t_sec_total,
            "retrieval_ms": t_ret,
            "generation_s": t_gen
        })
        
        await asyncio.sleep(0.5) 

    # --- AGGREGATE RESULTS ---
    print("\n" + "="*40)
    print("AVERAGE PERFORMANCE BY PATHWAY")
    print("-" * 40)
    
    categories = set(r['type'] for r in results)
    summary_rows = []
    
    for cat in categories:
        subset = [r for r in results if r['type'] == cat]
        avg_sec = statistics.mean(r['security_overhead_ms'] for r in subset)
        avg_ret = statistics.mean(r['retrieval_ms'] for r in subset)
        avg_gen = statistics.mean(r['generation_s'] for r in subset)
        
        print(f"[{cat}]")
        print(f"   Security Check: {avg_sec:.2f} ms")
        print(f"   Retrieval     : {avg_ret:.2f} ms")
        print(f"   AI Generation : {avg_gen:.2f} s")
        summary_rows.append({"Category": cat, "Security_ms": avg_sec, "Retrieval_ms": avg_ret, "AI_s": avg_gen})

    # Save CSV
    with open(csv_file, "w", newline='') as f:
        writer = csv.DictWriter(f, fieldnames=summary_rows[0].keys())
        writer.writeheader()
        writer.writerows(summary_rows)
        
    print(f"\n✅ Results saved to {csv_file}")

if __name__ == "__main__":
    asyncio.run(run_benchmark())