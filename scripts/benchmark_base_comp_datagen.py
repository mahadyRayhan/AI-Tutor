import sys
import os
import json
import asyncio
import logging
import time
from openai import OpenAI
import google.generativeai as genai

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

# --- API CLIENTS ---
openai_client = OpenAI(api_key=config.OPENAI_API_KEY)
genai.configure(api_key=config.GOOGLE_API_KEY)
# FIXED: Use available model names
gemini_model = genai.GenerativeModel('gemini-3-pro-preview')

async def get_gpt_response(query):
    try:
        response = openai_client.chat.completions.create(
            model="gpt-5", # FIXED
            messages=[{"role": "user", "content": query}]
        )
        return response.choices[0].message.content
    except Exception as e: return f"GPT Error: {e}"

async def get_gemini_response(query):
    try:
        response = gemini_model.generate_content(query)
        return response.text
    except Exception as e: return f"Gemini Error: {e}"

async def run_collection():
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("DataGen")

    llm = LLMInterface(logger=logger)
    vec = ChromaVectorStore(persist_directory=config.DEFAULT_VECTOR_DB_PATH, logger=logger)
    graph = Neo4jGraphDB(logger=logger)
    agent = ChainOfThoughtRAGAgent(llm, vec, graph, logger=logger)

    # Pre-config: Lock specific topics
    settings_manager.update_topic("Switch Statement", False)
    settings_manager.update_topic("Structures", False)
    settings_manager.update_topic("Pointers", False)

    # --- TRANSFORMATION LOGIC ---
    # Convert List-of-Dicts (Master) to Dict-of-Lists (What this script expects)
    grouped_dataset = {}
    for item in MASTER_DATASET:
        cat = item['cat']
        if cat not in grouped_dataset: grouped_dataset[cat] = []
        grouped_dataset[cat].append(item['q'])
    # ----------------------------

    # Ensure output dir exists
    out_dir = os.path.join(os.path.dirname(__file__), "..", "eval_result")
    if not os.path.exists(out_dir): os.makedirs(out_dir)
    
    output_path = os.path.join(out_dir, "benchmark_base_comp_datagen.json")
    results = []
    
    print(f"--- Starting Data Generation for {len(MASTER_DATASET)} Questions ---")

    for cat, queries in grouped_dataset.items():
        for q in queries:
            print(f"🚀 Processing: [{cat}] {q}")
            
            # 1. Get ChatGPT Baseline
            resp_gpt = await get_gpt_response(q)
            
            # 2. Get Gemini Baseline
            resp_gemini = await get_gemini_response(q)
            
            # 3. Get Our System
            resp_ours = ""
            try:
                async for event in agent.run_stream(q, "student", username="bench_user"):
                    if event["type"] == "complete":
                        resp_ours = event["data"]["answer"]
            except Exception as e:
                resp_ours = f"System Error: {e}"

            results.append({
                "category": cat,
                "query": q,
                "ans_gpt": resp_gpt,
                "ans_gemini": resp_gemini,
                "ans_ours": resp_ours
            })
            
            await asyncio.sleep(1) 

    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    
    print(f"\n✅ Data collection complete. File saved to: {output_path}")

if __name__ == "__main__":
    asyncio.run(run_collection())