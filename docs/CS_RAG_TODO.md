# CS / RAG Engineering TODO

Prioritized additions for moving SAGE from a classroom prototype toward a reliable, scalable RAG system.

## P0 — Retrieval correctness

- [ ] Fix the empty-retrieval context overwrite in `agents/socratic.py`.
- [ ] Add explicit abstention when course evidence is missing.
- [ ] Configure the Chroma distance metric explicitly.
- [ ] Calibrate the retrieval score threshold on labeled queries.
- [ ] Add BM25 keyword retrieval alongside dense retrieval.
- [ ] Fuse BM25 and dense results with Reciprocal Rank Fusion (RRF).
- [ ] Add a reranker; retrieve 15–25 candidates and keep the best 4–6.
- [ ] Add MMR/diversity filtering and a context token budget.
- [ ] Add section/page/chapter metadata to every chunk.
- [ ] Return claim-level citations, not only a list of retrieved chunks.
- [ ] Add a post-generation grounding check before returning an answer.

## P0 — RAG evaluation

- [ ] Create an instructor-labeled test set of questions, relevant chunks, and answers.
- [ ] Measure Recall@k, Precision@k, MRR, and nDCG for retrieval.
- [ ] Measure faithfulness, answer relevance, citation precision, and citation recall.
- [ ] Add tests for teacher-only and locked-content leakage.
- [ ] Compare dense-only, dense+graph, BM25+dense, and reranked retrieval.
- [ ] Make the best measured pipeline a regression test in CI.

## P1 — Indexing

- [ ] Separate ingestion from API startup.
- [ ] Add document versions and content hashes.
- [ ] Support incremental add, update, and delete operations.
- [ ] Use atomic index versions with rollback.
- [ ] Improve chunking around headings, functions, examples, and explanations.
- [ ] Batch embedding requests and record failed chunks.
- [ ] Add index-health and stale-index checks.

## P1 — Reliability

- [ ] Add generation timeouts and retries with exponential backoff and jitter.
- [ ] Add provider concurrency limits and circuit breakers.
- [ ] Add a fallback model/provider policy.
- [ ] Prevent raw provider errors from appearing in student responses.
- [ ] Split `/health` into liveness and dependency-aware readiness checks.
- [ ] Make grading and mastery updates idempotent.
- [ ] Use transactions or optimistic locking for learner-state updates.
- [ ] Make transcription and ingestion jobs durable across restarts.

## P1 — Scalability

- [ ] Replace SQLite with PostgreSQL and connection pooling.
- [ ] Move rate limits and shared caches to Redis.
- [ ] Move videos and generated files to object storage.
- [ ] Move background work to a durable queue and worker service.
- [ ] Use a shared vector service or PostgreSQL with pgvector.
- [ ] Remove process-local session and learning-path state.
- [ ] Run stateless API replicas behind a load balancer.
- [ ] Load-test concurrent chat, grading, dashboard, and upload traffic.
- [ ] Define p50/p95 latency, error-rate, throughput, and cost targets.

## P1 — Observability

- [ ] Add request IDs and trace IDs across API, retrieval, LLM, and database calls.
- [ ] Record retrieval latency, generation latency, token usage, and cost per turn.
- [ ] Record no-result rate, abstention rate, grounding failures, and reranker scores.
- [ ] Add metrics dashboards and alerts for dependency failures and latency.
- [ ] Define availability and response-latency SLOs.

## P1 — Security and privacy

- [ ] Remove the fixed Neo4j password and rotate it.
- [ ] Store all credentials in managed secrets.
- [ ] Restrict Neo4j ports to the internal network.
- [ ] Add retrieval prompt-injection tests.
- [ ] Add authorization tests at retrieval and API layers.
- [ ] Define retention, deletion, export, and audit policies for student data.
- [ ] Redact sensitive data from logs and model prompts where possible.

## P2 — Code quality

- [ ] Split `main.py` into routers, services, repositories, and schemas.
- [ ] Remove stale and duplicate orchestration paths.
- [ ] Add database migrations.
- [ ] Add static typing, linting, and dependency security checks to CI.
- [ ] Add integration tests with temporary SQLite/PostgreSQL, Chroma, and Neo4j instances.
- [ ] Document architecture decisions and failure behavior.

## P2 — Research and product validation

- [ ] Compare standard RAG, pedagogically routed RAG, and full adaptive SAGE.
- [ ] Run a real-student pre-test/post-test study with a delayed retention test.
- [ ] Measure transfer-task performance and false mastery certification.
- [ ] Control for prior programming ability.
- [ ] Validate LLM-judge scores against human raters.
- [ ] Measure teacher intervention accuracy and workload.
- [ ] Report effect sizes and confidence intervals.

## Add only after evidence supports it

- [ ] Self-RAG or corrective retrieve-critique-retrieve loops.
- [ ] Full community-summary GraphRAG.
- [ ] Tutor-model fine-tuning.
- [ ] Autonomous agent planning.
- [ ] Multimodal vector retrieval for video, slides, and diagrams.
