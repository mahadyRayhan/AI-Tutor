"""Add (or refresh) the instructor-only exam keys in the vector store.

WHY A SEPARATE SCRIPT
─────────────────────
`ChromaVectorStore.load_or_build` is all-or-nothing: it skips entirely once the
collection has any rows, so the only supported way to add a document was to wipe
the index and rebuild. A rebuild also re-runs `process_and_chunk_documents`,
which calls the LLM to regenerate every concept's quiz bank in Neo4j — the banks
the mastery exam draws its questions from. Refreshing four teacher-only files is
not worth regenerating the question bank, so this script does the narrow thing:

    chunk → embed → upsert into Chroma

and touches neither Neo4j nor any student-facing content. Chunking, metadata and
chunk ids come from `data_processing`, so a later full rebuild produces the same
rows rather than a second, differently-shaped copy.

Embeddings are the same provider the collection was built with; a dimension
mismatch is checked before anything is written.

    python scripts/ingest_exam_solutions.py            # add or refresh
    python scripts/ingest_exam_solutions.py --verify   # report only, no writes
    python scripts/ingest_exam_solutions.py --remove   # delete these chunks
"""
import argparse
import logging
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "backend"))

from langchain_text_splitters import RecursiveCharacterTextSplitter, Language  # noqa: E402

from app.core import config  # noqa: E402
from app.db.llm_interface import LLMInterface  # noqa: E402
from app.processing.data_processing import _create_chunk_id, _determine_metadata  # noqa: E402

import chromadb  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(message)s")
log = logging.getLogger("exam-ingest")
log.setLevel(logging.INFO)

COLLECTION = "ai_tutor_collection"


def _collection():
    client = chromadb.PersistentClient(path=config.DEFAULT_VECTOR_DB_PATH)
    return client.get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})


def _exam_files() -> list[str]:
    path = str(config.EXAMS_PATH)
    if not os.path.isdir(path):
        log.error(f"no exam directory at {path}")
        return []
    return sorted(os.path.join(path, f) for f in os.listdir(path)
                  if f.lower().endswith((".md", ".txt", ".c")))


def _chunk(file_path: str) -> list[tuple[str, dict]]:
    """(text, metadata) per chunk, exactly as a full rebuild would produce them."""
    filename = os.path.basename(file_path)
    ext = os.path.splitext(filename)[1].lower()
    splitter = RecursiveCharacterTextSplitter.from_language(
        language=Language.C if ext == ".c" else Language.MARKDOWN,
        chunk_size=1000, chunk_overlap=100)
    with open(file_path, "r", encoding="utf-8") as fh:
        content = fh.read()
    meta = _determine_metadata(filename)
    out = []
    for i, doc in enumerate(splitter.create_documents([content])):
        out.append((doc.page_content, {
            "chunk_id": _create_chunk_id(filename, i),
            "document_name": filename,
            "type": "code" if ext == ".c" else "concept",
            "access_level": meta["access_level"],
            "topic": meta["topic"],
        }))
    return out


def _existing_ids(col, document_name: str) -> list[str]:
    got = col.get(where={"document_name": document_name}, include=[])
    return got.get("ids", []) or []


def verify(col) -> int:
    """Report what is indexed. Returns the number of teacher-only chunks."""
    got = col.get(include=["metadatas"])
    metas = got.get("metadatas") or []
    teacher = [m for m in metas if m.get("access_level") == "teacher"]
    log.info(f"collection holds {len(metas)} chunks, {len(teacher)} tagged teacher-only")
    by_doc: dict[str, int] = {}
    for m in teacher:
        by_doc[m.get("document_name", "?")] = by_doc.get(m.get("document_name", "?"), 0) + 1
    for doc, n in sorted(by_doc.items()):
        topic = next(m.get("topic") for m in teacher if m.get("document_name") == doc)
        log.info(f"  {doc:<34} {n:>2} chunks   topic={topic}")
    if not teacher:
        log.info("  (none — the access-control evaluation has nothing to withhold)")
    return len(teacher)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true", help="report only, write nothing")
    ap.add_argument("--remove", action="store_true", help="delete these chunks and exit")
    args = ap.parse_args()

    col = _collection()

    if args.verify:
        verify(col)
        return 0

    files = _exam_files()
    if not files:
        return 1

    if args.remove:
        removed = 0
        for path in files:
            ids = _existing_ids(col, os.path.basename(path))
            if ids:
                col.delete(ids=ids)
                removed += len(ids)
        log.info(f"removed {removed} chunks")
        verify(col)
        return 0

    # Match the dimension the collection was built with, or Chroma will accept
    # the write and every later query against it will be meaningless.
    probe = col.get(limit=1, include=["embeddings"])
    existing_dim = len(probe["embeddings"][0]) if probe.get("embeddings") is not None \
        and len(probe["embeddings"]) else None

    llm = LLMInterface(logger=logging.getLogger("llm"))

    total = 0
    for path in files:
        name = os.path.basename(path)
        chunks = _chunk(path)
        if not chunks:
            log.warning(f"{name}: produced no chunks, skipped")
            continue
        if chunks[0][1]["access_level"] != "teacher":
            # The filename rule is what makes this material restricted. A file
            # here that does not trip it would be indexed as student-readable,
            # which is the opposite of the point.
            log.error(f"{name}: tagged '{chunks[0][1]['access_level']}', not teacher — "
                      f"rename it to contain 'exam', 'solution' or 'quiz'. Skipped.")
            continue

        ids, docs, metas, embeddings = [], [], [], []
        for text, meta in chunks:
            vec = llm.get_embedding(text, task_type="RETRIEVAL_DOCUMENT")
            if not vec:
                log.error(f"{name}: embedding failed for {meta['chunk_id']}, skipped")
                continue
            if existing_dim and len(vec) != existing_dim:
                log.error(f"{name}: embedding dim {len(vec)} != collection dim "
                          f"{existing_dim}. Aborting; check LLM_PROVIDER.")
                return 1
            ids.append(meta["chunk_id"])
            docs.append(text)
            metas.append(meta)
            embeddings.append(vec)

        if not ids:
            continue
        stale = _existing_ids(col, name)
        if stale:
            col.delete(ids=stale)
        col.add(ids=ids, embeddings=embeddings, documents=docs, metadatas=metas)
        total += len(ids)
        log.info(f"{name:<34} {len(ids):>2} chunks   topic={metas[0]['topic']}   access={metas[0]['access_level']}")

    log.info(f"\nindexed {total} teacher-only chunks")
    verify(col)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
