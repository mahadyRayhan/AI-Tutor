# SAGE-SRL — New Contributions (Latest Work)

*Inventory of the mechanisms added in the recent work cycle, for the SRL paper.
Each item is code-anchored so claims can be traced to implementation.
Last updated: 2026-07-21.*

---

## ⚠️ Terminology first: "Bayesian Network" vs. what we built

**We did NOT implement a Bayesian network.** We implemented **Bayesian Knowledge
Tracing (BKT)**. Keep these distinct in the paper — a reviewer will flag the
conflation.

| | Definition | In SAGE-SRL? |
|---|---|---|
| **Bayesian Knowledge Tracing (BKT)** | A latent (hidden) binary knowledge state per skill, updated with **Bayes' rule** as evidence arrives. Formally a 2-state **dynamic Bayesian model / HMM**. | ✅ Yes — `backend/app/core/bkt_model.py` (tiered, factored) |
| **Bayesian Network** | A DAG of variables with **conditional probability tables (CPTs)** encoding how one variable *probabilistically* influences another (e.g. mastering "Pointers" raises belief in "Memory Allocation"). | ❌ No |
| **Prerequisite structure** (the role a BN *would* play) | How concepts depend on each other. | ✅ Present, but as a **deterministic Neo4j knowledge graph with hard gating**, not probabilistic CPTs. |

- **Accurate paper phrasing:** *"tiered Bayesian Knowledge Tracing over a
  knowledge-graph curriculum, with prerequisite-coupled priors."*
- **No `pgmpy` / `BayesianNetwork` / `TabularCPD` anywhere in the codebase** (verified).

### The closest thing to a Bayesian network: prerequisite-coupled priors

The **head-start mechanism** (`backend/app/core/prereq_headstart.py`, see §8 below)
propagates *belief along the prerequisite graph* — a certified parent concept lifts
the *starting prior* of its dependents. This is the same **intuition** a Bayesian
network encodes, and it is the closest the system comes to one. But it is **NOT** a
Bayesian network, and the design deliberately breaks BN semantics:

| Bayesian network | Head-start (`prereq_headstart.py`) |
|---|---|
| Edges carry conditional probability tables `P(child\|parents)` | Edges carry a **hand-set linear transfer coefficient** `KAPPA={quiz:0.15, micro:0.06, code:0.01}` |
| Belief propagation keeps all nodes jointly consistent | **One-shot prior seed** at row creation, not ongoing inference |
| Evidence flows **both** directions | **One-directional**: only parent→child boosts; only "doubt flows downhill" |
| Beliefs are the output | The seed **can never certify** — moves the starting line, not the finish |

**Do not call it a Bayesian network in the paper.** Call it a *prerequisite-coupled
prior* / *graph-structured knowledge-transfer* mechanism — inspired by belief
propagation, but a constrained heuristic with no CPTs, no joint distribution, and no
inference.

- **Future work (a *real* Bayesian network):** replace the linear `KAPPA` transfer
  with proper conditional probabilities `P(child_prior | parent_mastery)` and run
  principled (bidirectional) inference over the curriculum DAG — promoting today's
  heuristic prior-seeding into a genuine probabilistic graphical model.

---

## The new mechanisms

### 1. Multi-tier (factored) Bayesian Knowledge Tracing
**`backend/app/core/bkt_model.py`** — *"Reparameterized Tiered Bayesian Knowledge
Tracing with Forgetting Decay"*

- **Factored, not scalar:** each concept tracks **3 independent posteriors** (P̃):
  `quiz` (conceptual recall), `micro` (applied synthesis), `code` (production).
- **Conjunctive certification:** "mastered" requires **all 3 tiers** at
  P̃ ≥ `THETA_CERTIFY` (0.95) **and** n ≥ `N_MIN` (3) evidence each
  (≈4 correct/tier, ≈12 interactions per concept).
- **Per-tier ceilings** θ_max = (0.60, 0.25, 0.10); composite display cap 0.95.
- **Forgetting decay** + **decertify hysteresis** (`THETA_DECERTIFY` 0.75) so a
  single slip doesn't revoke mastery.
- **`ever_certified` latch** — a permanent record that the student *has* proven
  the concept (drives the "reviewing" state below).

**Why it's a contribution:** mastery is defined as competence demonstrated across
multiple *representations*, resisting the classic "game the BKT" critique — one
lucky channel cannot certify a concept.

### 2. Knowledge Verification — "Verify Mastery" exam
**`backend/app/agents/cot_rag_agent.py:1040` (`_process_mastery_exam`)**

- **Learner-initiated, repeatable, comprehensive** 3-stage exam triggered by the
  student (`[MASTERY_EXAM]` intent) when they believe they've mastered a topic.
- **Surprise-quiz trickle:** the system also slips retrieval-practice quizzes into
  normal conversation on in-progress concepts (testing effect + distributed
  practice).
- **Dual-initiative design** (learner-initiated exam + system-initiated quizzes)
  is the notable design point — it spans SRL's *forethought* (strategic
  self-testing) and *self-reflection* (self-evaluation) phases.

### 3. Agency-Transfer Mechanism (ATM) — adaptive scaffolding & fading
**`cot_rag_agent.py:873` (`_classify_mastery_level`), `socratic.py:346`**

- Classifies the student from live BKT state into
  **novice / developing / proficient / reviewing**.
- Drives **scaffolding *and fading*** at the prompt level:
  heavy analogies (novice) → build-on-prior (developing) → edge-cases only
  (proficient) → concise refresher (reviewing).
- **Reviewers bypass** both the prerequisite gate **and** the Socratic
  withholding — control is handed back to the learner.
- **Challenge type adapts:** guided Micro-Challenge → advanced Challenge →
  Quick-Check recall.

**Why it's a contribution:** operationalizes VanLehn/Graesser *transfer of control
from tutor to student*, grounded in a running learner model rather than a fixed
script. This is the core ITS-meets-SRL novelty.

### 4. Transparency / mastery ledger (Open Learner Model)
**`bkt_model.py:453` (`mastery_ledger`)**

- Renders **per-tier progress bars (▰▱)** + "~N more to certify" + **how to earn
  the next tier**, inline after each interaction.
- Tier-decomposed and **actionable** — tells the student *which kind of evidence*
  to produce next, not just a percentage.

**Why it's a contribution:** an Open Learner Model (OLM) that supports
metacognitive monitoring and calibration (judgments of learning), decomposed by
evidence type.

### 5. Knowledge-graph prerequisite gating
**`cot_rag_agent.py` + Neo4j (`scripts/ingest_manual_graph.py`)**

- **Concept aliasing/canonicalization** (malloc/calloc/realloc → "Memory
  Allocation", recursion → "Recursion") so prerequisite gates fire reliably.
- **Direct-edge gating** from the curated `resources/metadata/c_knowledge_graph.json`
  curriculum.
- Gate is **adaptive in concert with ATM** — enforced for novices, bypassed for
  proven reviewers.

### 6. Per-concept quiz bank
**`scripts/seed_quiz_bank.py`**

- Generates **5 Q&A per core concept (55 total)** with embeddings, stored as
  `quiz_data` on Neo4j Concept nodes.
- Feeds tier-1 (quiz) evidence and the mastery exam. *(The bank was previously
  empty, which made mastery literally unreachable — this closes that gap.)*

### 7. Skill-network dashboard graph
**`backend/app/main.py`, `backend/app/static/js/student_dashboard.js`**

- A **prerequisite skill map** on the student dashboard: nodes colored by live BKT
  mastery, connected by canonical prerequisite edges.

### 8. Prerequisite-coupled priors ("Head Start")
**`backend/app/core/prereq_headstart.py`** — wired into `bkt_model.py:231`

A student who has *earned* mastery of a prerequisite (e.g. Variables) is not a total
beginner at a dependent topic (e.g. Loops), so that topic gets a small, capped boost
to its **starting** BKT prior — never to the finish line. Design invariants:

1. **Only certified prerequisites propagate** (`ever_certified == 1`); a head start
   contributes zero evidence, so boosts can't snowball down the chain.
2. **Proportional and capped** per tier — `KAPPA={quiz:0.15, micro:0.06, code:0.01}`,
   result capped by `HS_PRIOR_CAP={quiz:0.50, micro:0.15, code:0.02}`, all far below
   θ_cert=0.95.
3. **Applied once**, at the first real BKT touch of the new topic (row creation).
4. **Real performance takes over** — decay anchors to the default prior `P_L0`, not
   the seed, so a head start can't prop a score up against real failure.
5. **A head start can NEVER certify** — certification still needs n ≥ N_MIN real
   answers per tier on the topic itself.
6. **Only doubt flows downhill** — a downward self-assessment on a prerequisite
   *lowers* the head start it passes to dependents (`reduce_on_prereq_doubt`,
   monotone: can only reduce, never raise); students can never drag a neighbor up.
7. **The gate reads certified mastery, not the seeded posterior** — a head start can
   never unlock content.

**Why it's a contribution:** models *near-transfer of prior knowledge* along the
curriculum, so mastery reduces redundant re-teaching of adjacent topics — while the
guardrails preserve the hard mastery guarantee. This is the system's closest analogue
to a Bayesian network (see terminology section above), and an `explain()` endpoint
makes the transfer inspectable on the dashboard (SRL transparency).

---

## Mapping to Zimmerman's SRL phases (for the framing section)

| SRL phase | Mechanism |
|---|---|
| **Forethought** (goal-setting, planning) | Learner-initiated Verify Mastery; goal-awareness prompts; KG prerequisite roadmap |
| **Performance** (self-control, self-observation) | ATM adaptive scaffolding/fading; transparency ledger during practice; Socratic withholding |
| **Self-reflection** (self-judgment, self-evaluation) | Mastery-exam results; ledger calibration; surprise-quiz retrieval practice |

---

## Supporting work (reliability, NOT pedagogical claims)

Frame these as *system reliability that underwrites the eval's internal validity*,
not as contributions:

- Sentinel safety layers (harmful-code block, cross-user privacy, external-resource
  blocking).
- Streaming-stall watchdog + guaranteed terminal events (fixes "stuck on
  generating").
- Unified mastery authority + concept-matching normalization (no false/
  inconsistent mastery).
- Preference directives (tone / no-diagrams).
- Mermaid "Visual Model" text fix (`htmlLabels: false` so labels survive DOMPurify).

---

## ⚠️ Open item before writing the ATM claims

ATM is **implemented and wired**, but the adaptation-quality evaluation has **not**
been run in this cycle. Before claiming *"ATM produces measurably differentiated
scaffolding,"* run `SRL-script/eval_1_1_mastery_adaptation.py`
(60 interactions = 20 concepts × 3 states; target 4+/5 per state). Otherwise the
strongest contribution rests on an unmeasured claim.
