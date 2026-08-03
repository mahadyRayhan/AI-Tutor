# SRL Conference — New Topics Not Yet in the Paper

*Mechanisms present in the codebase but absent from `SRL_conference.pdf`, written up
for inclusion in a revision. Last updated: 2026-07-24.*

> **Reverses an earlier note.** The older `SRL_NEW_CONTRIBUTIONS.md` (2026-07-21)
> states *"We did NOT implement a Bayesian network."* That was true then. It is **no
> longer true**: the **MCN** below is a genuine discrete Bayesian network. BKT remains
> a dynamic Bayesian model / HMM (not a BN); the MCN is the BN. Keep both statements
> straight in the paper.

The SRL paper's metacognitive channel is currently a **heuristic**: `jol_cal = 1 −
|conf − correct|`, slip-vs-gap, and a downward-only `p_G` calibration loop. The code
contains a **principled latent-variable model** of that same signal — the MCN — which
is the one substantial SRL topic missing from the paper.

---

## 1. MCN — Metacognitive Calibration Network (a Bayesian network)

### Gap
The paper *acts* on metacognitive disagreement (adapting `p_G`) but never *infers a
calibration state* under a probabilistic model. `jol_cal` is a point statistic; it
conflates "the student is miscalibrated" with "the signal is noisy," and it cannot
combine multiple weak, partially-observed cues (self-report, performance, behavior,
affect) coherently. It also can't express *uncertainty* about the verdict.

### What the MCN is
A small **discrete Bayesian network** that infers a learner's calibration on a concept
by exact inference. Two latent nodes and four observable leaves:

| Node | Type | States |
|---|---|---|
| **K** — knowledge | latent | `low / med / high` |
| **C** — calibration | latent (**query target**) | `over / cal / under` |
| **S** — self-report | observed | `low / med / high` |
| **P** — performance | observed | `poor / mixed / good` |
| **B** — behavior | observed | `struggling / normal / fluent` |
| **A** — affect | observed | `frustrated / neutral / engaged` |

**Structure (the crux):** `S ← (K, C)`, `P ← (K)`, `B ← (K)`, `A ← (K, C)`.
Self-report `S` depends on **both** true knowledge and calibration — that is exactly
what makes `C` identifiable: a `high` self-report with `poor` performance is evidence
for `over`; a `low` self-report with `good` performance is evidence for `under`.

**Inference:** exact enumeration over the 9 `(K, C)` joint states; the posterior over
`C` is read off by marginalizing `K`. No sampling, no external library, dependency-free.

**Formally:** posterior
`P(C | evidence) ∝ Σ_K P(K)·P(C)·Π_leaf P(leaf | parents)`,
over observed leaves only (unobserved leaves drop out — the network degrades
gracefully when affect or behavior is missing).

### How real signals become evidence
An evidence pipeline maps the same telemetry the cognitive model consumes onto the
observable states, so the MCN adds **no new data collection**:

- **S** (self-report) ← the learner's JOL / confidence slider on the concept.
- **P** (performance) ← objective BKT mastery on the concept (uses the *objective*
  `get_mastery`, **not** the self-blended `P_eff`, to avoid circularity).
- **B** (behavior) ← hint-requests / skip-challenge / thumbs-down → `struggling`;
  code-copy / long dwell → `fluent`.
- **A** (affect) ← the existing frustration/affect signal → `frustrated / neutral /
  engaged`.
- **K prior** ← seeded from objective BKT mastery (`k_prior_from_bkt`).

### How it feeds the tutor
`get_calibration()` returns a verdict (`over / cal / under`) with a posterior
probability. `prompt_directive()` injects a directive **only** for `over` (demand more
evidence, gently probe) or `under` (reassure, surface the evidence they already
have) — a **downward-safe** use that never inflates certification, consistent with the
paper's no-self-certification guarantee.

### Relationship to the paper's calibration loop — state this explicitly
They are **complementary, two-layer**, not competing:

| | MCN (new) | SRL–BKT calibration loop (in paper) |
|---|---|---|
| Role | **Inference** — *what* the calibration state is | **Actuation** — *acts* on disagreement |
| Output | Posterior over `C ∈ {over,cal,under}` | Adapted per-learner `p_G` |
| Method | Bayesian network, exact inference | EMA direction + step on `p_G` |
| Signal | Fuses S, P, B, A | Sustained downward self-report |

Recommended framing: **the MCN infers the metacognitive state; the calibration loop
actuates on it.** `jol_cal` becomes the MCN's `S`-evidence, not a competing verdict.

### Deployment posture (honest, and good for the paper)
- **Flag-gated OFF by default** (`MCN_ENABLED=false`) — safe to present as
  "deployed-optional," activated for study cohorts.
- **CPTs are hand-specified priors now**, with a **Dirichlet counting** path to
  recalibrate them from classroom data post-deployment (count observed
  `(K,C)→leaf` co-occurrences, update tables) — no server downtime, no retraining of
  any LLM.
- Fail-safe: returns `None` (no directive) when evidence is insufficient, so a
  cold-start learner is never mis-nudged.

### Code anchors
- `backend/app/core/mcn.py` — `STATES`, `PARENTS`, `CptSet`, `infer()`, `MCNResult`,
  `default_network()` (the engine).
- `backend/app/core/mcn_evidence.py` — `gather_evidence()`, `infer_calibration()`,
  `k_prior_from_bkt()`, `_self_report_state / _performance_state / _behavior_state /
  _affect_state`.
- `backend/app/core/mcn_service.py` — `is_enabled()`, `get_calibration()`,
  `prompt_directive()` (flag + sufficiency + fail-safe).
- `backend/app/core/mcn_cpts.py`, `mcn_cpts.json` — CPT store + Dirichlet recalibration hook.

### Evidence available
- 24 unit tests (`tests/test_mcn.py`): engine correctness, CPT round-trip, evidence
  pipeline. Live pipeline inferred *Underconfident* p≈0.80 on a seeded profile.
- Identifiability simulation: recovers the confidence tails (~70%), rare catastrophic
  flip (~8%) — usable as a "mechanism works in principle" figure before the classroom study.

### Suggested paper placement
New methodology subsection, **"III-x Metacognitive Calibration Network,"** placed
before the SRL–BKT calibration loop (the MCN is the inference the loop consumes).
Add a row to Table II's Metacognitive dimension: *"calibration state — Bayesian
network over (K,C) from S/P/B/A."* In §IV, add the identifiability simulation now, and
the classroom calibration-vs-held-out result later.

---

## 2. (Secondary) Behavior-signal completeness

The MCN's `B` (behavior) channel required closing a small tracking gap so that
hint-requests, skip-challenge, dwell, and thumbs-down are all logged per turn and
mapped to `struggling / normal / fluent`. This makes the behavioral column of the
paper's multidimensional model (Table II help-seeking / persistence rows) fully
**instrumented from live telemetry** rather than partially. Worth a sentence in §III-B
noting the behavioral indicators are computed from the same append-only stream.

Code anchors: `backend/app/core/mcn_evidence.py` `_behavior_state()`; behavior
emission in the chat client + `telemetry.log_behavior()`.

---

## One-line abstract addition (if adopting the MCN)
> We add a **Metacognitive Calibration Network** — a discrete Bayesian network that
> infers a learner's over/under-confidence from self-report, performance, behavior,
> and affect — as the inference layer beneath the SRL–BKT calibration loop, preserving
> the downward-only, no-self-certification guarantee.
