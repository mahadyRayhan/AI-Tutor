# CAC — Dynamic DAG Implementation Plan

*Companion to [dynamic-dag.html](dynamic-dag.html) (the visual explanation) and
[threat-model-forextension.md](threat-model-forextension.md). Drafted 2026-09-08
against commit `73989d6`.*

---

## The idea in one paragraph

Mastery (K, from BKT) is a **state** — a property of a topic — so it lives on the
**nodes** of the curriculum DAG and is what ABAC reads. Metacognition (C, from the
learner model) is **regulation** — a property of how the learner *moves* — so it
lives on the **edges** and on the **traversal**, and is what CAC reads. "Dynamic
DAG" therefore does not mean time-varying node labels. It means a static topology
(the instructor's), slow node dynamics (BKT, days), and fast edge-and-traversal
dynamics (metacognition, minutes). Three timescales, three owners.

One invariant, stated once and tested as a property: **nothing metacognitive ever
opens a node the map keeps closed — C can only narrow.**

## The structural fact that shapes the plan

`get_learner_profile()` in `backend/app/core/learner_model.py` computes sixteen
learner dimensions. It is called from **exactly one place** — the dashboard API
endpoint at `main.py:3251`. No agent reads it. The security filter in
`sentinel.py` reads `|K|`, frustration, `is_in_quiz` and `m_state`, and nothing
else.

So this is not a build-from-scratch. It is wiring outputs that already exist into
a decision that currently ignores them.

## Which signal acts where

| Signal | Field in `learner_model.py` | Acts on | What changes | Layer |
|---|---|---|---|---|
| mastery | `is_current_certified` (bkt) | nodes | which topics are open | ABAC |
| calibration | `_slip_vs_gap.gap_ratio` | an edge | how hard a prerequisite binds | CAC |
| cognitive load | `_cognitive_load.index` | the horizon | how far ahead is reachable | CAC |
| help-seeking | `_help_seeking.quality` | a rung | how much is shown at a node | CAC |
| rushing | `_pacing.rushing` | the evidence | whether a node's colour can be trusted | CAC → ABAC |
| path deviation | `classify_path_deviation` → `skip_ahead` | the log | an access event, recorded | audit |
| persistence | `_persistence.grit_index` | break-glass | who is offered a way through | CAC |

Selection criterion (this is what makes it a security paper, not a pedagogy one):
**a sensor's weight in the policy is inversely proportional to attacker control.**
Self-report may only restrict. Derived residuals — calibration is the gap between
confidence and outcome, tier imbalance is the gap between tiers — carry the most
weight because nobody types them in.

---

## Phases

Ordered by value-per-risk, not by panel number. The two phases that touch BKT
numbers come last.

### Phase 0 — One pure decision function  *(foundation · ~3 days)*

New module `backend/app/core/cac_graph.py`. Everything else plugs into this.

- `load_topology()` — read `REQUIRES_UNDERSTANDING_OF` edges from Neo4j once and
  cache in memory. Fall back to a static JSON so tests and offline scripts do not
  need the graph DB (same pattern as `prereq_headstart._graph_db`).
- `LearnerView` — the nine coarse nodes with their current K state from
  `bkt_model.is_current_certified`, plus the C signals from `get_learner_profile()`.
- `decide(view, query_concepts, session) -> Decision` — a **pure function**
  returning `{rung_cap, in_horizon, edge_ok, reasons: list[str]}`. No DB writes,
  no LLM call. Purity is what makes ablation and offline replay possible.
- Add `"cac_graph"` to `SECURITY_FEATURE_NAMES` in `config.py` (line ~195) so it
  rides the existing `SAGE_ABLATE` machinery and appears in
  `active_ablation_config()`.
- Call it from `sentinel.py` immediately after Rule 3 (`S_acad`) and before the
  L7 trajectory step. When the switch is off, `decide()` returns the no-op
  decision and behaviour is byte-identical to today.

Every entry in `Decision.reasons` is written to `turn_log`. That is the E9 audit
trail with no extra work.

**Done when:** switch off → no diff in any existing test; switch on → every turn
carries a `Decision` record.

### Phase 1 — Path deviation → access log  *(cheapest · ~1 day)*

`classify_path_deviation` in `telemetry.py:336` already emits `skip_ahead` when
the asked concept is a *locked* node. Promote it: log a `cac_access_event` with
`layer="CAC"`, and keep a per-session A1 counter that the consistency detector
(E7) can read.

Zero behavioural change — pure observability. Do it first because it produces
data on how often students actually probe locked nodes *before* anything reacts.

**Done when:** a locked-node query produces one audit row and increments the
counter; an on-path query produces neither.

### Phase 2 — Help-seeking → rung cap  *(highest visible value · ~4 days)*

This is the original sentence ("diagrams and hints only, no code"), built properly.

- Define the disclosure ladder as an enum: `DIAGRAM < HINT < EXAMPLE < CODE`.
- `decide()` sets `rung_cap = HINT` when `help_seeking.quality == "impulsive"`
  (computed at `learner_model.py:149` from skip rate and immediate give-ups).
- `ScaffoldingAgent` has no level concept today. Its plan generator
  (`scaffolding.py:384`) needs a `max_disclosure` directive injected into the
  prompt, and code-emitting steps skipped above the cap.

**Test it as a property, not a judgement:** for any query, the response under
`cap=HINT` contains no fenced code block. That is a grep.

**Done when:** the property test passes across the E4 benign corpus and the cap
is visible in the audit row.

### Phase 3 — Cognitive load → horizon  *(~3 days)*

`_cognitive_load.index` (`learner_model.py:59`) → `d_max`:
index > 0.7 → 1 step · > 0.5 → 2 steps · else unbounded.

`decide()` computes graph distance from the query concept to the current
frontier. Beyond `d_max` → **redirect** to the nearest in-horizon prerequisite.
Never refuse: a refusal here is a pedagogical failure, not a security success.

The redirect message is where policy leakage (X1 in the research plan) lives.
Log what each redirect reveals — which edge, which node — so E15 can be measured
later without re-instrumenting.

**Done when:** a depth-2 query under high load returns the depth-1 prerequisite,
and the audit row names the revealed edge.

### Phase 4 — Calibration → edge threshold  *(~4 days · touches the gate)*

The prerequisite gate in `user_knowledge_manager.has_certified_prerequisite`
compares decayed mastery against the fixed `θ_decert = 0.75`. Make θ
edge-specific: for each outgoing edge from a node where `gap_ratio`
(`learner_model.py:78` — wrong *and* confident) is high, raise θ on that edge
toward 0.90.

**Invariant, tested as a property:** `θ_edge ≥ 0.75` always. C can tighten an
edge, never loosen it below the curriculum.

Use `gap_ratio` rather than the MCN for the first cut: it is live today, while
`MCN_ENABLED` defaults to false and is a dependency not yet needed.

**Done when:** an overconfident learner on Loops fails the Loops→Arrays gate at
P̃=0.85 while a calibrated learner passes at the same P̃; property test holds over
random graphs.

### Phase 5 — Rushing → evidence weight  *(riskiest · ~4 days · last)*

`bkt_model.update()` (`bkt_model.py:246`) has no weight parameter. Add
`weight: float = 1.0` and scale the posterior step by it. When
`pacing.rushing` is true — already a boolean at `learner_model.py:174`,
`latency < 4000 ms ∧ mastery < 0.4` — pass `weight = 0.5`.

This is the **only** phase that changes mastery numbers. It affects
certification, the dashboard, and the BKT paper's data, which is why it is last.

- Gate it behind its own switch, `cac_evidence_weight`, separate from `cac_graph`.
- Before enabling in production, replay the semester's `evidence_log` with and
  without it and list exactly which students' certifications would have moved.

**Done when:** the replay report exists and has been reviewed; no certification
changes without a named reason.

### Phase 6 — Persistence → break-glass  *(~3 days)*

A "I really need this" control on the redirect message, offered only when
`grit_index` (`learner_model.py:164`) is above a floor. Grants one rung above the
cap, logs it as an override, and lands in the teacher dashboard's action queue.

Small work; it supplies the human-in-the-loop story the evaluation plan already
promises (the instructor review surface).

**Done when:** an override appears in the teacher queue with the learner, node,
rung granted, and the C state at the time.

---

## Cross-cutting — do alongside the phases

- **Property tests for the sign constraint.** For random graphs and random C:
  `A(K, C) ⊆ A(K)` and every edge `θ ≥ curriculum θ`. Hypothesis-style
  generation over |V|, edge density and |K| — the claim is universal, so the test
  must sample the space, not enumerate examples.
- **Teacher dashboard.** The mastery-ring graph already exists. Overlay the edge /
  horizon / rung state per student. This is the review surface E7 needs, and it
  is the same drawing as `dynamic-dag.html`.
- **Replay harness.** Because `decide()` is pure, run it over historical
  `turn_log` rows to obtain E14 (region dynamics over the semester) before any
  student sees the feature. Expect — and be willing to report — that it is nearly
  inert under honest traffic.

## Timeline

| Phase | Work | Cumulative |
|---|---|---|
| 0 | pure decision function + switch | ~3 days |
| 1 | access log | ~4 days |
| 2 | rung cap | ~1.5 weeks |
| 3 | horizon | ~2 weeks |
| 4 | edge threshold | ~3 weeks |
| 5 | evidence weight | ~4 weeks |
| 6 | break-glass | ~4.5 weeks |

Phases 0–2 alone — about a week and a half — produce a working, ablatable,
auditable version of the original sentence, plus the logging to see how often
the remaining signals would have fired.

## Evaluation hooks this creates

| Track | Enabled by |
|---|---|
| E9 determinism & audit | Phase 0 `reasons` log |
| E7 consistency detector input | Phase 1 A1 counter · Phase 5 rushing flag |
| E4 over-block (rung) | Phase 2 no-code property test |
| E15 policy leakage | Phase 3 redirect logging |
| E8 structural invariants | Phase 4 θ floor · cross-cutting sign-constraint tests |
| E14 region dynamics | replay harness |
