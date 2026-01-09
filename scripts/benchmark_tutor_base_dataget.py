import sys
import os
import json
import asyncio
from openai import OpenAI
import google.generativeai as genai

# Setup path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'backend'))
from app.core import config

# Clients
openai_client = OpenAI(api_key=config.OPENAI_API_KEY)
genai.configure(api_key=config.GOOGLE_API_KEY)
gemini_model = genai.GenerativeModel('gemini-3-pro-preview')

# PATHS
# We need the OLD file to get "ans_ours" (Your System's answers)
OLD_JSON_PATH = os.path.join(os.path.dirname(__file__), "..", "eval_result", "benchmark_base_comp_datagen.json")
# We will save the NEW comparison here
NEW_JSON_PATH = os.path.join(os.path.dirname(__file__), "..", "eval_result", "benchmark_tutor_prompt_datagen.json")

# THE PROMPT (Ablation Study)
TUTOR_INSTRUCTION = """You are a C programming tutor for beginners.

Rules:
1) Do NOT give a full final solution immediately.
2) Give a short plan, then 2–4 hints or Socratic questions.
3) If code is necessary, keep it minimal (tiny snippets only).
4) If asked for restricted content (exam answers, cheating, hacking/malware), refuse and redirect to learning.

Student question:
"""

async def get_gpt_tutor_response(query):
    try:
        response = openai_client.chat.completions.create(
            model="gpt-5",
            messages=[
                {"role": "system", "content": TUTOR_INSTRUCTION},
                {"role": "user", "content": query}
            ]
        )
        return response.choices[0].message.content
    except Exception as e: return f"GPT Error: {e}"

async def get_gemini_tutor_response(query):
    try:
        # Gemini doesn't always strictly follow 'system' roles in API, 
        # so we prepend the instruction to the user message for strong adherence.
        full_prompt = f"{TUTOR_INSTRUCTION}\n\n{query}"
        response = gemini_model.generate_content(full_prompt)
        return response.text
    except Exception as e: return f"Gemini Error: {e}"

async def run_collection():
    if not os.path.exists(OLD_JSON_PATH):
        print(f"❌ Error: Could not find {OLD_JSON_PATH}.")
        print("   Please run 'scripts/generate_benchmark_data.py' first to generate the baseline 'Ours' data.")
        return

    print("Loading 'Ours' data from previous run...")
    with open(OLD_JSON_PATH, 'r') as f:
        old_data = json.load(f)

    # Create a lookup map for "Ours" answers to ensure alignment
    # Format: { "What is a variable?": "The answer our system gave..." }
    ours_lookup = {item['query']: item['ans_ours'] for item in old_data}

    new_results = []
    
    # We iterate through the OLD data to ensure we use the exact same questions
    print(f"--- Processing {len(old_data)} Questions with New Tutor Prompt ---")

    for i, item in enumerate(old_data):
        q = item['query']
        cat = item['category']
        
        # Retrieve the answer "Ours" gave previously
        ans_ours_existing = ours_lookup.get(q, "Error: Answer not found")

        print(f"[{i+1}/{len(old_data)}] {q}...", end="", flush=True)
        
        # 1. Get GPT (With Tutor Prompt)
        resp_gpt = await get_gpt_tutor_response(q)
        
        # 2. Get Gemini (With Tutor Prompt)
        resp_gemini = await get_gemini_tutor_response(q)
        
        new_results.append({
            "category": cat,
            "query": q,
            "ans_gpt": resp_gpt,       # New (Tutor Mode)
            "ans_gemini": resp_gemini, # New (Tutor Mode)
            "ans_ours": ans_ours_existing # Old (Unchanged)
        })
        print(" Done.")
        
        # Rate limit
        await asyncio.sleep(0.5)

    # Save to NEW file
    with open(NEW_JSON_PATH, "w") as f:
        json.dump(new_results, f, indent=2)
    
    print(f"\n✅ Ablation Data Saved: {NEW_JSON_PATH}")

if __name__ == "__main__":
    asyncio.run(run_collection())