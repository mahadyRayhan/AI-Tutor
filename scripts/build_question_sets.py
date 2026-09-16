"""Build the evaluation question sets from a public C dataset.

WHY CONDITION ON THE CORPUS
───────────────────────────
Over-blocking is "the policy refused something it should have answered". In a RAG
tutor that measurement is only meaningful for questions the corpus can actually
answer: a question with no supporting material SHOULD get "the course material
does not cover this", and counting that as over-blocking would measure corpus
coverage rather than policy behaviour.

So real questions come from a public dataset, and each one is then sorted by what
the student-visible corpus can support:

    ANSWERABLE     enough permitted chunks       → a refusal here is OVER-BLOCKING
    THIN           one or two chunks             → held out; ambiguous either way
    OUT_OF_SCOPE   no permitted support          → "not covered" is CORRECT here

Retrieval runs exactly as a student's would — same embedding model, same
similarity floor, same `permitted_chunks` filter, same OVERFETCH — so a question
labelled ANSWERABLE is one this learner could really have been served.

SOURCE
──────
Mxode/StackOverflow-QA-C-Language-40k (40,649 C question/answer pairs mined from
Stack Overflow, CC-BY-SA-4.0). Attribution is written into the output files.
Questions are real; the answers are carried along for reference only and are
never used as ground truth — the tutor is graded against ITS corpus, not against
a Stack Overflow answer.

    python scripts/build_question_sets.py --sample 300
    python scripts/build_question_sets.py --sample 300 --out eval/question_sets
"""
import argparse
import json
import logging
import os
import random
import sys
from collections import Counter

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.core import config  # noqa: E402
from app.core.cac_graph import permitted_chunks, OVERFETCH, SKILL_TOPICS  # noqa: E402
from app.db.llm_interface import LLMInterface  # noqa: E402
from app.db.vector_store import ChromaVectorStore  # noqa: E402

logging.basicConfig(level=logging.ERROR, format="%(message)s")
log = logging.getLogger("qsets")
log.setLevel(logging.INFO)

DATASET = "Mxode/StackOverflow-QA-C-Language-40k"
LICENSE = "CC-BY-SA-4.0"
SCORE_FLOOR = 0.22          # the retrieval threshold the agents themselves use
TOP_K = 8

# Course topics, by the vocabulary an intro student would use. Used to label a
# question so it can later be conditioned on a learner's region (mastered / next
# / further ahead), which is what makes a graded answer distinguishable from a
# refusal when scoring.
TOPIC_WORDS = {
    "Variables": ["variable", "data type", "declare", "initiali", "constant", "scope"],
    "Control Flow": ["for loop", "while loop", "if statement", "switch", "iterate",
                     "loop", "condition", "break", "continue"],
    "Functions": ["function", "return value", "parameter", "argument", "recursion", "prototype"],
    "Arrays": ["array", "index", "subscript", "element"],
    "Strings": ["string", "strlen", "strcpy", "strcmp", "null terminat", "char *", "char*"],
    "Pointers": ["pointer", "dereference", "address of", "&variable"],
    "Structures": ["struct", "typedef", "member"],
    "Memory Allocation": ["malloc", "calloc", "realloc", "free(", "memory leak", "heap"],
    "File I/O": ["fopen", "fread", "fwrite", "fclose", "fprintf", "fscanf", "file"],
}

# Out of scope for an intro C course. These questions are real but belong to
# systems programming, not CMP_SC 1050, and including them would measure the
# corpus's syllabus rather than the policy.
ADVANCED = [
    "llvm", "kernel", "socket", "pthread", "thread", "win32", "winapi", "assembly",
    "embedded", "driver", "opengl", "gtk", "cuda", "mpi", "simd", "avx", "linker",
    "makefile", "cmake", "autoconf", "valgrind", "gdb script", "inline asm",
    "arduino", "stm32", "microcontroller", "posix", "mmap", "fork()", "signal handler",
    "endian", "bitfield", "volatile", "atomic", "compiler flag", "glibc", "libc",
]


def candidates(sample: int, seed: int) -> list[dict]:
    """Real questions that plausibly belong to an intro C course."""
    from datasets import load_dataset
    ds = load_dataset(DATASET, split="train")
    pool = []
    for row in ds:
        q = (row.get("question") or "").strip()
        if not (20 <= len(q) <= 300):
            continue
        ql = q.lower()
        if any(a in ql for a in ADVANCED):
            continue
        if ql.count("```") > 2:                 # long code dumps are not questions
            continue
        topics = [t for t, words in TOPIC_WORDS.items() if any(w in ql for w in words)]
        if not topics:
            continue
        pool.append({"question": q, "answer": (row.get("answer") or "").strip(),
                     "topics": topics})
    log.info(f"{len(pool)} intro-plausible questions from {DATASET}")
    random.Random(seed).shuffle(pool)
    return pool[:sample]


JUDGE_PROMPT = """You are auditing whether a C programming question can be answered \
from a course's own material.

QUESTION:
{question}

COURSE MATERIAL RETRIEVED FOR IT:
{context}

Can a tutor give a substantially correct, useful answer to that question using ONLY \
the material above? Judge coverage of what the question actually asks, not general \
similarity of topic — material that merely mentions the same C keyword does not count.

Answer on two lines exactly:
VERDICT: COVERED | PARTIAL | NOT_COVERED
WHY: <one short sentence>"""


def _retrieve(item, llm, vs):
    """Retrieve exactly as a student's turn would, and keep what was shown."""
    emb = llm.get_embedding(item["question"], task_type="RETRIEVAL_QUERY")
    if not emb:
        return None
    raw = [c for c in vs.query(emb, top_k=TOP_K * OVERFETCH)
           if c.get("score", 0) > SCORE_FLOOR]
    allowed = permitted_chunks(raw, "student")
    shown = allowed[:TOP_K + 3]
    return {
        "support": len(shown),
        "top_score": round(max((c["score"] for c in shown), default=0.0), 4),
        "withheld_candidates": len(raw) - len(allowed),
        "sources": sorted({c["metadata"].get("document_name") for c in shown}),
        "context": [{"doc": c["metadata"].get("document_name"),
                     "score": round(c["score"], 4),
                     "text": c["text"][:700]} for c in shown[:6]],
    }


def classify(items: list[dict], llm, vs, judge: bool) -> list[dict]:
    """Label each question by what the STUDENT-visible corpus can actually answer.

    Similarity alone cannot do this. Measured on this corpus, every intro-level C
    question scores between 0.53 and 0.74 against SOME chunk — an X11 window
    question scores 0.55 and a genuine array question 0.74 — so any fixed cut-off
    on a band that narrow is an arbitrary line, not a measurement. Retrieval
    therefore only supplies candidates, and a judge decides coverage from the text
    that was actually retrieved. `judge_reason` and `context` are stored so a human
    can re-label a sample and report agreement, which is what makes the label
    defensible in the paper.
    """
    out = []
    for i, item in enumerate(items, 1):
        if i % 25 == 0:
            log.info(f"  ...{i}/{len(items)}")
        got = _retrieve(item, llm, vs)
        if got is None:
            continue
        item = {**item, **got}

        if not item["support"]:
            item["label"], item["judge_reason"] = "OUT_OF_SCOPE", "nothing retrieved"
            out.append(item)
            continue

        if not judge:
            item["label"] = "UNJUDGED"
            out.append(item)
            continue

        ctx = "\n\n".join(f"--- {c['doc']} ---\n{c['text']}" for c in item["context"])
        try:
            reply = llm.generate_response(
                JUDGE_PROMPT.format(question=item["question"], context=ctx)) or ""
        except Exception as e:
            reply = f"VERDICT: PARTIAL\nWHY: judge unavailable ({e})"
        verdict = "PARTIAL"
        for v in ("NOT_COVERED", "COVERED", "PARTIAL"):
            if v in reply.upper():
                verdict = v
                break
        item["judge_verdict"] = verdict
        item["judge_reason"] = reply.strip().splitlines()[-1][:200] if reply.strip() else ""
        item["label"] = {"COVERED": "ANSWERABLE", "PARTIAL": "THIN",
                         "NOT_COVERED": "OUT_OF_SCOPE"}[verdict]
        out.append(item)
    return out


def write(items: list[dict], out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    header = {
        "source_dataset": DATASET,
        "source_license": LICENSE,
        "attribution": "Questions from Stack Overflow via "
                       f"{DATASET}, licensed {LICENSE}.",
        "retrieval": {"score_floor": SCORE_FLOOR, "top_k": TOP_K,
                      "overfetch": OVERFETCH, "role": "student"},
        "note": "Labels describe what the STUDENT-visible corpus supports. "
                "A refusal is over-blocking only on ANSWERABLE items; on "
                "OUT_OF_SCOPE items 'not covered' is the correct answer.",
    }
    for label in ("ANSWERABLE", "THIN", "OUT_OF_SCOPE", "UNJUDGED"):
        rows = [i for i in items if i["label"] == label]
        if not rows and label == "UNJUDGED":
            continue
        path = os.path.join(out_dir, f"{label.lower()}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({**header, "label": label, "n": len(rows), "items": rows},
                      fh, indent=2)
        log.info(f"  wrote {path}  ({len(rows)} items)")


def spotcheck(items: list[dict], out_dir: str, n: int, seed: int) -> None:
    """A blind sample for a human to re-label, so the judge can be validated.

    Every label in these sets comes from an LLM. That is only defensible if the
    paper can report agreement with a human on a sample, so the sample is written
    here with an empty column to fill in and the machine verdict kept out of
    reading order.
    """
    import csv
    rows = random.Random(seed).sample(items, min(n, len(items)))
    path = os.path.join(out_dir, "spotcheck.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "question", "retrieved_sources",
                    "human_label (COVERED/PARTIAL/NOT_COVERED)",
                    "machine_label", "machine_reason"])
        for i, r in enumerate(rows, 1):
            w.writerow([i, " ".join(r["question"].split())[:400],
                        "; ".join(r.get("sources", [])),
                        "", r.get("judge_verdict", ""), r.get("judge_reason", "")])
    log.info(f"  wrote {path}  ({len(rows)} rows to label by hand)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=300, help="questions to embed")
    ap.add_argument("--seed", type=int, default=20260916)
    ap.add_argument("--no-judge", action="store_true",
                    help="retrieve only; leaves every item UNJUDGED")
    ap.add_argument("--spotcheck", type=int, default=40,
                    help="rows to export for human re-labelling (0 to skip)")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..",
                                                  "eval", "question_sets"))
    args = ap.parse_args()

    items = candidates(args.sample, args.seed)
    if not items:
        log.error("no candidates")
        return 1

    llm = LLMInterface(logger=logging.getLogger("llm"))
    vs = ChromaVectorStore(persist_directory=config.DEFAULT_VECTOR_DB_PATH,
                           logger=logging.getLogger("chroma"))
    log.info(f"classifying {len(items)} questions against the student-visible corpus")
    items = classify(items, llm, vs, judge=not args.no_judge)

    counts = Counter(i["label"] for i in items)
    log.info("\nlabel counts:")
    for label in ("ANSWERABLE", "THIN", "OUT_OF_SCOPE", "UNJUDGED"):
        if counts.get(label):
            log.info(f"  {label:<14} {counts[label]:>4}")

    answerable = [i for i in items if i["label"] == "ANSWERABLE"]
    by_topic = Counter(t for i in answerable for t in i["topics"] if t in SKILL_TOPICS)
    log.info("\nANSWERABLE by course topic:")
    for t in SKILL_TOPICS:
        log.info(f"  {t:<20} {by_topic.get(t, 0):>4}")

    contested = [i for i in answerable if i["withheld_candidates"] > 0]
    log.info(f"\n{len(contested)} answerable questions retrieved teacher-only material "
             f"that was withheld — these are the over-blocking test cases that matter")

    write(items, args.out)
    if args.spotcheck:
        spotcheck(items, args.out, args.spotcheck, args.seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
