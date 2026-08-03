"""Populate the quiz bank (Module-B F2-05).

The examiner serves quiz questions from a `quiz_data` JSON property on Neo4j Concept
nodes, but the bank is empty — so no student can earn quiz-tier evidence through the
tutor (only via one-shot video MCQs). This script generates N high-quality Q&A pairs
per core concept (with the same Google + local answer embeddings the grader uses) and
attaches them to the matching existing Concept node.

- Idempotent: re-running overwrites quiz_data for the listed concepts.
- Only MATCHes existing nodes (never invents concepts).
- N=5 so a student can reach n>=4 correct without repeating a question.

Run:  python scripts/seed_quiz_bank.py
"""
import os
import sys
import json
import logging

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.core import config
from app.core.utils import setup_logging
from app.db.graph_db import Neo4jGraphDB
from app.db.llm_interface import LLMInterface
from app.processing.data_processing import local_embedder

N_PER_CONCEPT = 5

# Concept node name (must exist in Neo4j) -> short grounding context for the LLM.
CONCEPTS = {
    "Variables and Types": "A variable is a named, typed storage location in memory (int, char, float, double). Declared before use; holds a value that can be read or changed.",
    "Operators": "C operators: arithmetic (+ - * / %), relational (< > == !=), logical (&& || !), assignment (=, +=), and increment/decrement (++ --).",
    "Conditionals": "Conditional statements (if, else if, else, switch) let a program choose between paths based on boolean expressions.",
    "Loops": "Loops (for, while, do-while) repeat a block of code. A for loop has init, condition, and update; while checks a condition before each pass.",
    "Functions": "A function is a named, reusable block of code with a return type, parameters, and a body. Declared with a prototype; called by name.",
    "Arrays": "An array is a fixed-size, contiguous collection of same-type elements accessed by a zero-based index (e.g., int a[5]; a[0]).",
    "Strings": "In C a string is a null-terminated ('\\0') array of char. Handled with functions like strlen, strcpy, strcmp from <string.h>.",
    "Pointers": "A pointer is a variable that stores the memory address of another variable. Declared with *, address taken with &, value read by dereferencing with *.",
    "Memory Allocation": "Dynamic memory is requested at runtime from the heap with malloc/calloc, resized with realloc, and released with free to avoid leaks.",
    "Recursion": "Recursion is a function calling itself to solve a problem, with a base case that stops the recursion and a recursive case that reduces the problem.",
    "Structures": "A struct groups related variables (members) of different types under one name, accessed with the dot operator (s.member) or arrow (p->member).",
}


def generate_qa(concept_name: str, context: str, llm: LLMInterface, n: int, logger) -> list:
    prompt = f"""
        Context: {context}

        Task: Generate exactly {n} specific Q&A pairs to test a student's understanding of "{concept_name}" in C.

        CRITICAL RULES:
        1. SELF-CONTAINED: each question must make sense on its own (no "the code above").
        2. NO REFERENCING phrases like "provided code" or "as shown".
        3. Answerable in one sentence or a short code snippet.
        4. NO multiple choice.
        5. Mix conceptual and small code-reading/-writing questions.
        6. Return STRICTLY a JSON list of objects: [{{ "q": "Question?", "a": "Correct answer" }}]
    """
    resp = llm.generate_response(prompt)
    clean = resp.replace("```json", "").replace("```", "").strip()
    pairs = json.loads(clean[clean.find("["): clean.rfind("]") + 1])
    out = []
    for p in pairs:
        if not p.get("q") or not p.get("a"):
            continue
        emb_google = llm.get_embedding(p["a"])
        emb_local = local_embedder.encode(p["a"]).tolist()
        if emb_google:
            out.append({"q": p["q"], "a": p["a"], "a_vector": emb_google, "a_vector_local": emb_local})
    return out


def main():
    logger = setup_logging("INFO", "quiz_seed.log")
    graph = Neo4jGraphDB(logger)
    llm = LLMInterface(
        llm_provider=config.DEFAULT_GENERATIVE_LLM_PROVIDER,
        google_api_key=config.GOOGLE_API_KEY,
        openai_api_key=config.OPENAI_API_KEY,
        logger=logger,
    )

    total = 0
    for concept, context in CONCEPTS.items():
        # Verify the node exists (never invent concepts)
        exists = graph.execute_query(
            "MATCH (n:Concept) WHERE toLower(n.name)=toLower($c) RETURN n.name AS name LIMIT 1",
            {"c": concept})
        if not exists:
            logger.warning(f"⚠️  node not found, skipping: {concept}")
            continue
        try:
            qa = generate_qa(concept, context, llm, N_PER_CONCEPT, logger)
        except Exception as e:
            logger.error(f"❌ generation failed for {concept}: {e}")
            continue
        if not qa:
            logger.warning(f"⚠️  no Q&A produced for {concept}")
            continue
        graph.execute_query(
            "MATCH (n:Concept) WHERE toLower(n.name)=toLower($c) SET n.quiz_data=$data",
            {"c": concept, "data": json.dumps(qa)})
        logger.info(f"✅ {len(qa):2d} Qs  →  {concept}")
        total += len(qa)

    logger.info(f"Done. Seeded {total} quiz questions across {len(CONCEPTS)} concepts.")
    graph.close()


if __name__ == "__main__":
    main()
