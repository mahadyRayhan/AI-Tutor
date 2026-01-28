import google.generativeai as genai
import os
from dotenv import load_dotenv
from pathlib import Path

# Load environment variables
env_path = Path(__file__).resolve().parents[1] / '.env'
load_dotenv(dotenv_path=env_path)

api_key = os.getenv("GOOGLE_API_KEY")

if not api_key:
    print("❌ Error: GOOGLE_API_KEY not found in .env")
    exit()

print(f"🔑 Using API Key: {api_key[:5]}...{api_key[-5:]}")

try:
    genai.configure(api_key=api_key)
    
    print("\n--------- EMBEDDING MODELS (For RAG) ---------")
    found_any = False
    for m in genai.list_models():
        if 'embedContent' in m.supported_generation_methods:
            print(f"✅ {m.name}")
            found_any = True
    
    if not found_any:
        print("❌ No embedding models found! Check your API key permissions.")

    print("\n--------- GENERATION MODELS (For Chat) ---------")
    for m in genai.list_models():
        if 'generateContent' in m.supported_generation_methods:
            print(f"🔹 {m.name}")

except Exception as e:
    print(f"\n❌ API Error: {e}")