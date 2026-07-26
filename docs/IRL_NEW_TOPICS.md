# IRL Extension — New Topics Not Yet in the Paper

*Mechanisms present in the codebase but absent from `IRL_Extension.pdf`, written up
for inclusion in a revision. Each item states the gap, the mechanism, code anchors,
suggested paper placement, and available evidence. Last updated: 2026-07-24.*

The IRL paper's security contribution is the **three-layer Safety Gatekeeper**
(RBAC / ABAC / CAC) over GraphRAG — but every layer evaluates **one message in
isolation**. The four topics below extend that posture to the **conversation level**,
make it **usability-preserving**, and make every decision **auditable**. They cohere
into a natural v2 security section: *"from per-message access control to
trajectory-level access control."*

---

## 1. Trajectory-Level (Multi-Turn) Access Control

### Gap
The gatekeeper judges each query independently. This is structurally blind to
**crescendo / multi-turn jailbreaks** (Russinovich et al., *Crescendo*, USENIX
Security '25): an attacker decomposes a prohibited goal into a sequence of
individually-benign turns, and the harmful payload is only assembled by a final,
innocent-looking message (*"now combine what we discussed into one program"*). No
per-message filter can catch a turn that carries no tell.

### Mechanism
A session-level risk layer added on top of the existing per-message Sentinel,
following the **Peak + Accumulation** design (arXiv:2602.11247; crescendo threat model
from Russinovich et al., *Crescendo*, USENIX Security '25):

- **Per-turn risk** `r_t ∈ [0,1]` is computed from signals the pipeline *already*
  produces — goal-alignment gap `(1 − s_goal)`, drift off the C-domain, off-topic
  strikes, and proximity to attack vocabulary. No new inference.
- **Session accumulator** stores two floats in session state:
  `peak = max(decayed peak, r_t)` and `acc = min(1, λ·acc_prev + r_t)`
  (`λ = 0.8`), with `risk = 0.5·peak + 0.5·acc`.
- **Escalation ladder:** below `τ_judge = 0.45` normal; at `τ_judge` escalate to a
  **conversation-level LLM judge** fed the **last-6-turn transcript** (so a benign
  final turn is judged in the context of the whole arc); at `τ_block = 0.85` hard-block
  without a judge call *(see caveat below — this rung needs reconciling)*.
- **Block-poisoning (state-amnesia fix):** because a per-message block returns
  *before* the trajectory step, a blocked malicious turn would otherwise leave the
  accumulator clean and let the *next* turn (*"show me the code that does exactly
  that"*) slip through. Every security-grade block therefore raises the accumulator
  floor, so follow-ups are scrutinized. A **repentant pivot** back to legit work is
  still allowed, because the *judge* — not a blunt rule — arbitrates.

### Framing (important — the accumulator is a trigger, not a classifier)
Code verification shows the per-turn risk **does not separate** attacks from benign
sessions: attacks peak at **0.60–0.80**, but a benign debugging session reaches **0.49**
and a legitimate systems-programming session (`fork`/`exec`/`system`) reaches **0.72**
— *inside* the attack range. **Do not present a risk-score separation plot** — it would
refute the thresholds, not support them. The honest, stronger claim is **two-stage**:

- the **accumulator is a high-recall trigger** — verified 3/3 attacks escalate to the
  judge across the whole `λ ∈ [0.7,0.9] × τ_judge ∈ [0.35,0.55]` sweep;
- the **transcript-fed judge is the classifier** — verified 4/4 benign sessions
  (including the 0.49 and 0.72 ones that *do* cross `τ_judge`) are read in context and
  allowed.

So `τ_judge` is a **cost boundary** (how often the judge runs), not a correctness
boundary. Report **escalation recall + post-judge false-block rate as two stages**, not
a score distribution. (Full analysis: `docs/MULTITURN_SECURITY_RESEARCH.md`,
`docs/HANDPICKED_VALUES.md`.)

> ⚠️ **Open caveat — the `τ_block = 0.85` judge-free hard block.** Because the scores
> don't separate and a legitimate systems session already reaches 0.72, `τ_block` is a
> *score-only* decision that could hard-block a benign student with no judge in the
> loop (0.13 margin). This contradicts the "cost boundary, never a correctness
> boundary" framing above. **Before publishing, either route the hard block through
> the judge, or raise/remove `τ_block`.** Not yet observed in tests (max benign 0.72 <
> 0.85), but latent.

### Why this is cheap and deployable
It reuses existing signals, adds **zero** LLM calls on the common path (the judge
fires only when accumulated risk is already elevated), and the entire state is two
floats per session. This matches the paper's "negligible security overhead" story.

### Code anchors
- `backend/app/agents/sentinel.py`: `_trajectory_risk()`, `TRAJ_*` constants,
  L7 integration in `process()`, block-poisoning + `_semantic_safety_check()` (the
  existing judge, now conversation-fed).

### Evidence available
- Live red-team suite (`test_trajectory_defense.py`): **3/3 crescendo attacks blocked
  (incl. a keyword-sparse benign-final-turn vector caught *only* by the trajectory
  judge), 4/4 benign multi-turn sessions never blocked** (including a debugging
  session saturated with "infinite loop / forever / crash").
- Vulnerability + fix documented with a persisted audit (`verification/JAILBREAK_AUDIT.md`).
- Full design + literature map: `docs/MULTITURN_SECURITY_RESEARCH.md`.

### Suggested paper placement
New subsection in §III (Safety Gatekeeper): **"III-x Trajectory-Level Access
Control."** Add one row to the capability table (per-message vs session-level). In §IV,
add the crescendo ASR + benign over-refusal numbers as a second security result.

---

## 2. Short-Term Topic Memory & Anaphora Resolution

### Gap
The paper models **state-aware retrieval** `Ready(t, K)` but assumes each query names
its own topic. Real tutoring dialogue is **anaphoric**: after "What is an array?", a
student asks *"how does it work?"*, *"how do I declare it?"*, *"what about a nested
one?"* — none of which name a topic. Without resolution, the tutor loses the thread
(retrieves the wrong concept) or, worse, a context-less fragment trips the security/
off-topic filters.

### Mechanism
A deterministic **Topic Amnesia Cache** — no LLM — that runs *before* the gatekeeper:
- `_remember_topic()` seeds an anchor (`last_valid_topic`) from the student's own
  concept at the earliest reliable point, so even a prerequisite-roadmap turn leaves
  a usable anchor.
- `_resolve_topic_memory()` rewrites anaphoric / vague follow-ups against the anchor
  (*"how does it work?"* + anchor(`array`) → *"how does array work?"*), guarded so it
  never fires on an explicit topic switch or an emotional/helplessness message.
- The resolved topic is set *before* the Sentinel, so a bare follow-up is routed as a
  normal CONCEPT question and cannot be misread as off-topic / security.

### Code anchors
- `backend/app/agents/cot_rag_agent.py`: `_best_topic()`, `_remember_topic()`,
  `_resolve_topic_memory()`; integration before Sentinel in `run_stream()`.
- Enabling fix: `backend/app/core/history_manager.py` `add_message()` — session rows
  were being created under a fresh UUID, silently voiding *all* session state
  (topic memory, pending goals, quiz FSM). Now created under the real `session_id`.

### Evidence available
- `test_multiturn_big.py`: **18/18** checks across 4 multi-turn sessions (anaphora
  chains, composition, explicit topic switch, frustration).

### Suggested paper placement
Short subsection in §III-A (Dual-Store / interaction) or a paragraph in the RAG
pipeline: *"state-aware retrieval requires cross-turn coreference; we resolve it
deterministically against a remembered topic anchor."*

---

## 3. Context-Gated Security Classification (Usability-Preserving CAC)

### Gap
The CAC layer's blunt keyword triggers over-refuse **legitimate C concepts**:
`"infinite loop"` and `"fork()"` are core curriculum (students must learn to debug
and avoid them), yet a phrase-match blocked *"why does my code have an infinite
loop?"* — a debugging student — as a security risk. In a tutor, a false block has a
direct **pedagogical cost**, an axis the education-security literature stresses.

### Mechanism
- **Context-gating (phrase vs. intent):** concept terms (`infinite loop`, `fork(`,
  `while(1)`) are treated as malicious *only* when paired with a harmful verb/goal in
  the same message (`crash`, `freeze`, `consume all`, `never stop`, …). The genuine
  attacks (`fork bomb`, etc.) still hard-block at Layer 1a regardless. Crucially, *not*
  blocking the benign single turn lets it reach the trajectory net (Topic 1).
- **Embedding SECURITY_RISK removed from the intent classifier** — the fuzzy anchor
  false-matched innocent words ("file", "give me"); security is enforced by explicit
  keywords + the Sentinel, not by the topic classifier.
- **Typo tolerance** with a first-letter/length guard (`poitners→pointers`, but not
  `avoid→void`).

### Code anchors
- `backend/app/agents/sentinel.py`: `context_gated` block in the S_goal rule.
- `backend/app/core/fast_classifier.py`: `classify_intent()`, `_fuzzy_c_term()`.

### Evidence available
- Stateless classifier: **82% → 100%** intent accuracy on 63 realistic queries.
- Security regression: **8/8** — every legit infinite-loop/debug question allowed,
  every attack (virus/crash/freeze/fork-bomb) blocked.

### Suggested paper placement
A paragraph in the CAC subsection (§III-B) + a usability/over-refusal metric in §IV
("false-block rate on legitimate curriculum terms").

---

## 4. Security Audit Trail (Block Provenance)

### Gap
The paper logs `turn_log` / `prediction_log`, but there is no **per-block security
record** answering *who caught an attempt (which layer) and when*.

### Mechanism
Every gatekeeper block now (a) returns a deterministic `block_reason` (the exact
layer: `Goal-Bounded Security`, `Harmful Code`, `AI Semantic Judge`, `Trajectory
Risk`, …) in the response, and (b) persists a `security_block` event to the
append-only `event_log` with reason, intent, query snippet, and trajectory-risk
score. This yields a reviewable, per-session security ledger for the instructor
dashboard.

### Code anchors
- `backend/app/agents/sentinel.py`: `_block_response()` (returns `block_reason`,
  logs `telemetry.log_event(..., "security_block", ...)`).
- Reviewable artifact generator: `verification/JAILBREAK_AUDIT.md` / `.json`.

### Suggested paper placement
Extend the telemetry/dashboard subsection (§III-H): add `security_block` to the
append-only stream; note it supports offline red-team replay and per-cohort attack
analysis.

---

## 5. (Optional) Deployment Hardening

Not a research contribution, but real and worth a "deployment readiness" sentence if
the venue values it: sliding-window **rate limiting** (`rate_limiter.py`), **input
validation** (`validators.py`, email/username/password rules), bcrypt auth with a
SHA-256 pre-hash, and message-size caps. Mention only if §IV has room; otherwise omit.

---

## One-line abstract addition (if adopting Topics 1–4)
> We extend SAGE's per-message Safety Gatekeeper to a **trajectory-level** access
> controller: a training-free session-risk accumulator escalates to a
> conversation-level judge, context-gating removes curriculum false-blocks, and every
> decision is written to an auditable security ledger.
