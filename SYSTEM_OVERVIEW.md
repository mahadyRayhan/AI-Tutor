# An Adaptive AI Tutoring System for Introductory C Programming
### System Design, Motivation, and Theoretical Foundations

---

## Abstract

This document describes the architecture, motivation, and theoretical grounding of an adaptive intelligent tutoring system (ITS) built for undergraduate students learning C programming. The system integrates Bayesian Knowledge Tracing, spaced repetition, multi-armed bandit style selection, affective state monitoring, and a multi-agent pedagogical pipeline into a unified architecture that separates instructor-regulated constraints (IRL) from student self-regulated learning processes (SRL). Each design decision is grounded in learning science literature, and several components represent novel combinations of established techniques. This document explains what each component does, why it exists, and what prior work it builds upon.

---

## 1. Motivation

### 1.1 The Problem

Students learning introductory programming face a compounding challenge: not only must they acquire declarative knowledge (what a variable is), but they must also develop procedural skill (how to write a correct declaration) and applied judgment (how to reason about code written by others). Traditional tutoring — whether human or software — tends to treat these as a single continuum rather than three distinct, sequentially dependent forms of competency.

Equally important, students arrive with different learning histories, emotional states, misconceptions, and preferred ways of receiving information. A static curriculum cannot adapt to these differences. At the same time, instructors in large undergraduate courses cannot monitor every student's internal state continuously.

The system described here addresses both problems: it provides a **personalized, adaptive learning path** for each student while keeping **instructors in control** of what topics are taught, in what order, and with what constraints.

### 1.2 Why Build This Instead of Using a General LLM

Large language models (LLMs) such as GPT-4 or Gemini can answer programming questions competently. However, they are not tutors. A tutor:

- **Withholds answers** until the student demonstrates understanding (Socratic scaffolding)
- **Sequences content** according to prerequisite relationships
- **Tracks what a student knows** across sessions and updates that model with each interaction
- **Responds to emotional state** — a frustrated student needs encouragement, not a lecture
- **Holds the student accountable** — a student who skips procedural practice should not be marked as proficient
- **Reports progress** to an instructor who is ultimately responsible for the course

None of these properties are inherent to an LLM. They are architectural decisions that must be designed and implemented. This system wraps LLM capabilities inside a principled pedagogical architecture.

---

### 1.3 Formal System Objective

The individual components of this system — the learner model, style selector, prerequisite gate, and affective profiler — are not independent modules. They act as local optimization mechanisms toward a shared global objective: maximizing student mastery gain while minimizing cognitive overload and affective friction. Formally, the system seeks to maximize the cumulative per-interaction reward:

```
J  =  Σ_t  [ w₁ · ΔK_t  +  w₂ · R_t  −  w₃ · F_t  −  w₄ · C_t ]
```

where:

- `ΔK_t = Σ_{k=1}^{3} [P_t^(k) − P_{t−1}^(k)]`: composite BKT mastery gain at step `t`. Only the tier `k(e_t)` corresponding to the current evidence type is updated; the remaining two posteriors are unchanged, so `ΔK_t ∈ (−θ_max^(k), +θ_max^(k))`. For a correct observation `ΔK_t ≥ 0`; for an incorrect one `ΔK_t < 0` (the BKT posterior decreases when the student slips).
- `R_t ∈ {0, 1}`: SM-2 retention signal. `R_t = 1` if the SM-2 quality score satisfies `q ≥ 3` — the algorithm's pass threshold above which the review interval extends; `R_t = 0` if `q < 3`, which resets the interval to day 1. The quality score `q ∈ [0, 5]` is derived from both correctness and student-reported confidence (Judgment of Learning), as described in Section 3.4.2.
- `F_t ∈ [0, 1]`: frustration cost. The affective profiler outputs a categorical label in `{none, low, medium, high, rage}` (Section 3.4.5). For inclusion in `J`, these are mapped uniformly to `{0.00, 0.25, 0.50, 0.75, 1.00}`, treating the five labels as equally spaced steps on the frustration scale.
- `C_t ∈ {1, 1.5, 2}`: instructional cost. A standard direct response costs `C_t = 1`; a scaffolded hint that partially reveals the solution costs `C_t = 1.5`; a full partial-code handout costs `C_t = 2`. The scaling reflects the transfer of cognitive labor from student to system — higher `C_t` indicates less productive struggle and therefore lower expected learning yield per unit of instructional effort.
- `w₁, w₂, w₃, w₄ ≥ 0`: weighting hyperparameters. Initial reference values are `(w₁, w₂, w₃, w₄) = (1.0, 0.5, 0.5, 0.2)`, reflecting the following priority ordering: mastery gain is the primary objective (`w₁ = 1.0` anchors the scale); retention and frustration suppression are secondary and symmetric (`w₂ = w₃ = 0.5`); instructional cost is a weak regularizer discouraging unnecessary scaffolding (`w₄ = 0.2`). These weights are calibrated heuristically for the initial deployment and are intended for offline policy evaluation and refinement once sufficient interaction logs accumulate.

Each system component can be interpreted as a local mechanism optimizing a specific term in `J`:

| Component | Term optimized | Mechanism |
| --- | --- | --- |
| BKT mastery gate | `w₁ · ΔK_t` | Routes interactions that maximize mastery evidence |
| SM-2 scheduler | `w₂ · R_t` | Schedules reviews at intervals that maximize retention |
| Affective profiler | `−w₃ · F_t` | Triggers tone softening and pacing to suppress `F_t` |
| Prerequisite gate | `−w₄ · C_t` | Prevents cognitively overloaded interactions that inflate cost |
| UCB1 style selector | `w₁ · ΔK_t` (indirect) | Selects teaching style that empirically maximizes student engagement and mastery uptake |

The weighting hyperparameters `w₁ … w₄` are not jointly optimized in the current deployment; they are treated as design choices that will be calibrated via offline policy evaluation once sufficient interaction data is available. The objective function is stated here to make explicit the learning-theoretic intent behind each component and to provide a target for future end-to-end optimization.

---

## 2. System Architecture Overview

The system is organized around two parallel tracks — **IRL** (Instructor-Regulated Learning) and **SRL** (Self-Regulated Learning) — that converge at a security gate before passing through a pedagogical engine. The full architecture is reproduced below for reference:

```
┌───────────────────────────────────────────┐   ┌────────────────────────────────────────────┐
│  IRL  (Instructor-Regulated)              │   │  SRL  (Self-Regulated Learning)            │
│                                           │   │                                            │
│  [A] INSTRUCTOR MODEL                     │   │  [B] PERCEPTION LAYER                      │
│  [C] DOMAIN MODEL                         │   │  [D] LEARNER MODEL                         │
└──────────────────────┬────────────────────┘   └───────────────────────┬────────────────────┘
                       └──────────────┬──────────────────────────────────┘
                                      ▼
                              [E] SECURITY MODEL
                              σ = φ(S_cog · S_acad · S_spam) · α(G_t)
                                      ▼
                          [F] PEDAGOGICAL MODEL (SRL Engine)
                                      ▼
                              [G] INTERFACE  →  [H] PERSISTENCE
```

The separation of IRL and SRL is deliberate. Instructor constraints ([A], [C]) are stable and change slowly — they define the boundary conditions of the learning environment. Student state ([B], [D]) changes rapidly with each interaction. Mixing them in a single model leads to design conflicts; separating them makes each layer independently testable and maintainable.

---

## 3. Component Deep-Dive with Literature Backing

---

### 3.1 [A] Instructor Model — IRL Layer

**What it does:** Stores and enforces instructor-set rules: which topics are unlocked for students, the assignment lifecycle, classroom video availability, and user access control.

**Why it exists:** Intelligent tutoring systems historically modeled only the student. The need for an explicit instructor model was recognized early in the field — Anderson et al. (1995) noted that Cognitive Tutors required teacher oversight to be effective in real classrooms [1]. More recently, the concept of Instructor-Regulated Learning (IRL) as a complement to SRL has been formalized: the instructor sets the outer constraints within which student self-regulation operates [2].

**Formal representation:**
- Topic access: `t_i ∈ enabled_topics ⊆ T`
- Assignment lifecycle: `state ∈ {PENDING, SUBMITTED, GRADED}`

**Literature:**
- [1] Anderson, J. R., Corbett, A. T., Koedinger, K. R., & Pelletier, R. (1995). Cognitive tutors: Lessons learned. *Journal of the Learning Sciences, 4*(2), 167–207.
- [2] Pintrich, P. R. (2000). The role of goal orientation in self-regulated learning. In *Handbook of Self-Regulation* (pp. 451–502). Academic Press.

---

### 3.2 [B] Perception Layer — SRL Layer

**What it does:** A four-stage pipeline that processes every student message before routing it to the pedagogical engine:
1. **Query Contextualizer** — an LLM rewrites ambiguous queries using conversation history (coreference resolution), then validates entities against the Neo4j knowledge graph
2. **Intent Classifier** — a deterministic rule cascade (keyword matching) followed by a MiniLM embedding fallback: `intent* = argmax_k [sim(q, iₖ)]`
3. **Named Entity Recognizer** — GLiNER [3] combined with a domain-specific C programming vocabulary (`C_CONCEPT_TERMS`) to extract concept entities
4. **Affective Profiler** — regex rules for explicit frustration signals, backed by a DistilRoBERTa sentiment model: `F_t ∈ {none, low, medium, high, rage}`

**Why it exists:** The Perception Layer implements what D'Mello & Graesser (2012) call the need to detect "affective-cognitive" states — learning is not purely cognitive, and a system that ignores emotional state will systematically fail frustrated students [4]. The two-stage intent classifier (rule → embedding) balances precision on common cases with robustness on novel phrasing.

**Literature:**
- [3] Zaratiana, U., Tomeh, N., & Shi, C. (2023). GLiNER: Generalist model for named entity recognition using bidirectional transformer. *arXiv:2311.08526*.
- [4] D'Mello, S., & Graesser, A. (2012). Dynamics of affective states during complex learning. *Learning and Instruction, 22*(2), 145–157.

---

### 3.3 [C] Domain Model — IRL Layer

**What it does:** Stores the structure of the C programming domain as a knowledge graph (Neo4j) with prerequisite edges (`REQUIRES_UNDERSTANDING_OF`), and a vector store (ChromaDB) for semantic retrieval of course materials.

**Core operations:**
- **Prerequisite check:** `∃(c → r) ∈ E ∧ r ∉ K_known → gate fires`
- **Semantic retrieval:** `sim = cosine(embed(q), embed(chunkᵢ))`, retrieve `top-k = argmax_i [sim(q, chunkᵢ)]`

**Why it exists:** Prerequisite relationships between concepts are a foundational element of knowledge-based tutoring. Liang et al. (2018) showed that automatically recovering prerequisite concept relations significantly improves the coherence of adaptive learning paths [5]. Combining a structured knowledge graph (for prerequisite enforcement) with a vector store (for fuzzy semantic retrieval) gives the system both hard constraints and soft context — the knowledge graph prevents a student from jumping to pointers before understanding arrays; the vector store retrieves the most contextually relevant course material for any question.

Retrieval-Augmented Generation (RAG), formalized by Lewis et al. (2020), grounds LLM responses in course-specific content rather than general pre-training knowledge [6]. This is critical for a domain like C programming where the course may use a specific textbook, coding style, or terminology.

**Literature:**
- [5] Liang, C., Wu, Z., Huang, W., & Giles, C. L. (2018). Recovering concept prerequisite relations from university course dependencies. *AAAI-18*.
- [6] Lewis, P., Perez, E., Piktus, A., et al. (2020). Retrieval-augmented generation for knowledge-intensive NLP tasks. *NeurIPS 2020*.

---

### 3.4 [D] Learner Model — SRL Layer

This is the most theoretically rich component. It maintains six state variables updated with each interaction.

---

#### 3.4.1 K_t — Mastery State (Factored Multi-Skill Bayesian Knowledge Tracing)

**Formal model.** For each concept `c`, three independent latent binary subskill states are maintained:

```
z_c = (z_c^(1), z_c^(2), z_c^(3)),   z_c^(k) ∈ {0, 1}
```

corresponding to hierarchically ordered cognitive levels derived from Bloom's Taxonomy [8]:

- `z_c^(1)`: declarative knowledge (Bloom: Remember / Understand)
- `z_c^(2)`: procedural skill (Bloom: Apply)
- `z_c^(3)`: applied reasoning (Bloom: Analyze / Evaluate)

**Independence assumption.** The three subskills are modeled as conditionally independent given the latent state sequence — i.e., `P(z_c^(1), z_c^(2), z_c^(3)) = ∏_k P(z_c^(k))`. This should be interpreted as a tractability assumption rather than a claim that cognitive levels are psychologically uncorrelated. Declarative knowledge plausibly facilitates procedural performance, which in turn supports applied reasoning — dependencies that are well-documented in the cognitive science literature (Anderson, 1983; Bloom, 1956 [8]). Modeling these dependencies explicitly would require a hierarchical or coupled latent-state model and substantially larger datasets for reliable parameter estimation via Expectation-Maximization. The independent-factor formulation is a deliberate bias toward interpretability and data efficiency, following the same tractability rationale as classical BKT [7]. Relaxing this assumption using hierarchical Bayes or a dependency graph (e.g., a skill prerequisite prior over subskill states) is a planned direction for future work.

Each subskill `k` is modeled as an independent standard BKT hidden Markov model (Corbett & Anderson, 1994 [7]) with tier-specific parameters `θ^(k) = (p_0^(k), p_T^(k), p_S^(k), p_G^(k))`:

| Subskill `k` | Evidence type | Prior `p_0^(k)` | Learning `p_T^(k)` | Slip `p_S^(k)` | Guess `p_G^(k)` | Ceiling `θ_max^(k)` |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Quiz (declarative) | 0.30 | 0.09 | 0.10 | 0.20 | 0.60 |
| 2 | Micro-challenge (procedural) | 0.00 | 0.09 | 0.15 | 0.10 | 0.25 |
| 3 | Code review (applied) | 0.00 | 0.09 | 0.20 | 0.05 | 0.10 |

The non-zero prior `p_0^(1) = 0.30` for the declarative subskill reflects the assumption that students entering a C programming course have some prior exposure to programming concepts; priors for procedural and applied subskills are zero, as these require deliberate practice to develop. The remaining parameters (`p_T^(k)`, `p_S^(k)`, `p_G^(k)`) are initialization defaults calibrated against prior BKT studies in introductory CS domains [7]. Once sufficient interaction logs have accumulated (approximately ≥200 interactions per concept per evidence type), all per-tier parameters are re-estimated via Expectation-Maximization — specifically, the Baum-Welch algorithm applied to each subskill HMM independently. The initialization values then function as priors for EM rather than fixed constants, and the reported model behavior reflects learned parameters rather than manually chosen ones. A sensitivity analysis showing ±20% perturbations of `p_T^(k)`, `p_S^(k)`, and `p_G^(k)` is included in the evaluation section to bound the influence of the initialization choices on mastery convergence speed.

**Evidence-type routing.** Each student interaction produces a tuple `(x_t, e_t)` where `x_t ∈ {0, 1}` is the correctness signal and `e_t ∈ {quiz, micro, code}` is the evidence type. A binary selector function routes each observation to exactly one subskill tracker:

```
δ^(k)(e_t) = 𝟏[k corresponds to e_t]
```

Specifically: `δ^(1)(quiz) = 1`, `δ^(2)(micro) = 1`, `δ^(3)(code) = 1`, all others zero. When `δ^(k)(e_t) = 0`, tracker `k` is not updated: `P_{t+1}^(k) = P_t^(k)`. This enforces strict evidence-type specificity — declarative evidence updates only the declarative tracker, procedural evidence only the procedural tracker, and so on.

**BKT posterior update.** Let `P_t^(k) = P(z_c^(k) = 1 | x_{1:t}, e_{1:t})` denote the marginal posterior for subskill `k` after `t` observations. When `δ^(k)(e_t) = 1`, the standard BKT update applies:

```
P(z_c^(k) = 1 | x_t = 1)  =  P_t^(k) · (1 − p_S^(k))  /  [P_t^(k) · (1 − p_S^(k)) + (1 − P_t^(k)) · p_G^(k)]

P(z_c^(k) = 1 | x_t = 0)  =  P_t^(k) · p_S^(k)         /  [P_t^(k) · p_S^(k) + (1 − P_t^(k)) · (1 − p_G^(k))]

P_{t+1}^(k)  =  min( P(z_c^(k) = 1 | x_t) + [1 − P(z_c^(k) = 1 | x_t)] · p_T^(k),   θ_max^(k) )
```

The ceiling `θ_max^(k)` is enforced at the update step, so `P_t^(k) ∈ [p_0^(k), θ_max^(k)]` at all times. Each tracker is a well-defined Bayesian posterior over a binary latent state.

**Composite mastery score and decision rule.** Define the composite mastery score:

```
P_c  =  Σ_{k=1}^{3} P_t^(k)   ∈  [0,  Σ_k θ_max^(k)]  =  [0, 0.95]
```

`P_c` is not a probability — it is a composite scalar score in a bounded interval. The mastery decision uses a **conjunctive per-tier criterion** that decouples the evidence contribution cap (`θ_max^(k)`) from the evidence sufficiency threshold (`θ_mastery^(k)`):

```
m_c  =  𝟏[ ∀k : P_t^(k) ≥ θ_mastery^(k) ]
```

| Subskill `k` | Evidence ceiling `θ_max^(k)` | Mastery threshold `θ_mastery^(k)` | Ratio |
| --- | --- | --- | --- |
| 1 — Quiz | 0.60 | 0.57 | 95% |
| 2 — Micro | 0.25 | 0.24 | 96% |
| 3 — Code  | 0.10 | 0.09 | 90% |

The ceiling `θ_max^(k)` is the asymptotic upper bound imposed by the BKT emission model — the maximum posterior achievable given infinite correct evidence of type `k`. The mastery threshold `θ_mastery^(k) < θ_max^(k)` is the minimum posterior required for progression. Setting `θ_mastery^(k)` strictly below the ceiling addresses a specific numerical concern: the BKT ceiling is enforced by hard clipping at the update step (`P_t^(k) ← min(P_t^(k), θ_max^(k))`), which means `P_t^(k)` can reach `θ_max^(k)` exactly after sufficient evidence. Setting `θ_mastery^(k) = θ_max^(k)` would couple the mastery decision to the ceiling cap — two conceptually distinct constructs (evidence contribution limit and evidence sufficiency criterion) would collapse to the same number. The 5% buffer decouples them: a student at `P_t^(k) = θ_mastery^(k)` has demonstrated sufficient posterior confidence for progression but has not necessarily exhausted all discriminative evidence of type `k`. The specific ratios (95%, 96%, 90%) are calibrated heuristics; any value in the range `(θ_max^(k) · 0.85, θ_max^(k))` preserves the behavioral semantics.

A student who performs perfectly on quizzes accumulates `P_t^(1) → θ_max^(1) = 0.60` but cannot satisfy `m_c = 1`, because `P_t^(2)` and `P_t^(3)` remain at their priors (0.00) — far below `θ_mastery^(2) = 0.24` and `θ_mastery^(3) = 0.09`. This property follows directly from the evidence-routing function `δ^(k)(e_t)`: since quiz evidence sets `δ^(2)(quiz) = 0` and `δ^(3)(quiz) = 0`, the procedural and applied trackers receive no updates from quiz interactions, making quiz-only mastery structurally unreachable.

**Why this formulation?** Standard BKT [7] uses a single latent state per concept and cannot distinguish between a student who recalls a definition and one who writes correct code — both produce the same posterior update. Deep Knowledge Tracing [X] and DKVMN [Y] model multi-skill dependencies via neural networks but sacrifice interpretability. The factored three-tracker formulation is a direct extension of standard BKT in which the latent state is a vector of independent subskills, one per Bloom's cognitive level. Each posterior `P_t^(k)` has a direct cognitive interpretation; the mastery criterion has a closed-form conjunctive expression; and the per-tier parameters are individually interpretable. The uniform learning rate `p_T^(k) = 0.09` across all tiers is a conservative initialization consistent with Baker et al.'s (2008) analysis of BKT parameters across thousands of skills in ASSISTments, which found learning rates in the range 0.05–0.15 for introductory CS domains, with the lower end of this range appropriate for skills requiring substantial deliberate practice [29]. The higher guess probability for quiz (`p_G^(1) = 0.20`) reflects susceptibility to correct guessing on declarative recall tasks (multiple-choice or short-answer format); the lower guess probability for code review (`p_G^(3) = 0.05`) reflects the low probability of accidentally producing correct reasoning about code without genuine understanding. The higher slip probability for code review (`p_S^(3) = 0.20`) reflects sensitivity to syntactic and execution errors on applied tasks even when the underlying knowledge is present. Ceiling proportions follow Evidence-Centered Design principles [28]: evidence types mapping to lower Bloom's levels and collected more frequently contribute proportionally larger ceilings to the composite score.

**Literature:**
- [7] Corbett, A. T., & Anderson, J. R. (1994). Knowledge tracing: Modeling the acquisition of procedural knowledge. *User Modeling and User-Adapted Interaction, 4*(4), 253–278.
- [8] Bloom, B. S. (1956). *Taxonomy of educational objectives: Cognitive domain.* Longmans, Green.
- [28] Mislevy, R. J., Steinberg, L. S., & Almond, R. G. (2003). On the structure of educational assessments. *Measurement: Interdisciplinary Research and Perspectives, 1*(1), 3–62.

---

#### 3.4.2 SM-2 — Spaced Repetition Schedule

**What it does:** Schedules when each concept should next be reviewed:
```
I(1)=1d,  I(2)=6d,  I(n) = I(n−1) · EF
EF' = EF + 0.1 − (5−q) · (0.08 + (5−q) · 0.02),   q ∈ [0,5],  EF_min = 1.3
```

**Why it exists:** The forgetting curve, documented by Ebbinghaus (1885), shows that memory decays exponentially without reinforcement [9]. Spaced repetition counteracts this by scheduling reviews at increasing intervals — a principle supported by over a century of cognitive science. The SM-2 algorithm, developed by Wozniak (1990) for the SuperMemo system, adapts the interval based on recall quality `q` [10]. Cepeda et al. (2006) confirmed in a meta-analysis of 317 experiments that distributed practice substantially outperforms massed practice for long-term retention [11].

In this system, the quality score `q` is derived from both correctness and self-reported confidence (Judgment of Learning, JoL), following the metacognitive monitoring framework of Nelson & Narens (1990) [12].

**Literature:**
- [9] Ebbinghaus, H. (1885). *Über das Gedächtnis.* Duncker & Humblot.
- [10] Wozniak, P. A. (1990). *Optimization of learning.* SuperMemo World.
- [11] Cepeda, N. J., Pashler, H., Vul, E., Wixted, J. T., & Rohrer, D. (2006). Distributed practice in verbal recall tasks. *Psychological Bulletin, 132*(3), 354–380.
- [12] Nelson, T. O., & Narens, L. (1990). Metamemory: A theoretical framework and new findings. *Psychology of Learning and Motivation, 26*, 125–173.

---

#### 3.4.3 C_s — Teaching Style Selection (UCB1 Multi-Armed Bandit)

**What it does:** Selects the teaching style `s* ∈ S = {analogy, example, visual, Socratic, direct}` that maximizes cumulative student engagement, updated from explicit thumbs-up/down feedback after each response.

**Formal model.** Define the following quantities:

- `r_t ∈ {0, 1}`: reward — 1 if the student gives thumbs-up on the response, 0 otherwise (thumbs-down or no response)
- `w_s ∈ ℤ_≥0`: cumulative thumbs-up count for style `s`
- `n_s ∈ ℤ_≥0`: total times style `s` has been presented (`w_s ≤ n_s`)
- `N = Σ_{s ∈ S} n_s`: total interactions across all styles to date

The UCB1 selection rule is:
```
score_s(t)  =  +∞                              if n_s = 0
               w_s / n_s + √(2 · ln N / n_s)   otherwise

style*  =  argmax_{s ∈ S}  score_s(t)
```

The initialization convention `score_s(t) = +∞` when `n_s = 0` ensures that all `|S|` arms are explored at least once before any exploitation begins — this is required for the UCB1 regret bound to hold and is the standard cold-start convention of Auer et al. (2002) [13]. The win-rate term `w_s / n_s ∈ [0, 1]` is the empirical mean reward for style `s`; the exploration bonus `√(2 ln N / n_s)` is decreasing in `n_s`, shifting the policy from uniform exploration toward exploitation of high-reward styles as confidence accumulates.

**Reward semantics and regret bound.** Since `r_t ∈ {0, 1}` is a Bernoulli random variable bounded in [0, 1], UCB1 applies directly. Let `μ_s = E[r_t | style = s]` be the true mean reward for style `s` and `s† = argmax_s μ_s` the optimal style. The expected cumulative regret satisfies:

```
E[R_N]  =  E[ Σ_{t=1}^{N} (μ_{s†} − μ_{s_t}) ]  =  O( √(|S| · N · ln N) )
```

This sublinear regret guarantee means that as the number of interactions grows, the fraction of time spent on suboptimal styles converges to zero. Unlike engagement-proxy signals (dwell time, response length), the binary thumbs-up/down reward is semantically unambiguous: `r_t = 1` means "the student explicitly indicated this response was helpful," making the empirical win rate `w_s / n_s` a directly interpretable estimate of `μ_s`.

**Why it exists:** Students differ substantially in how they receive new information, and a static style assignment fails to adapt to individual preference. Treating style selection as a multi-armed bandit problem — with exploration to discover good styles and exploitation of identified preferences — is motivated by Auer et al. (2002) [13]. Clement et al. (2015) demonstrated that bandit algorithms outperform fixed-curriculum approaches in ITS by adapting to learner responses in real time [14]. The contribution here is the application of UCB1 to *how* content is delivered (teaching style), rather than *what* content is delivered (topic sequencing), and the use of explicit rather than inferred feedback as the reward signal.

**Literature:**
- [13] Auer, P., Cesa-Bianchi, N., & Fischer, P. (2002). Finite-time analysis of the multiarmed bandit problem. *Machine Learning, 47*(2-3), 235–256.
- [14] Clement, B., Roy, D., Oudeyer, P. Y., & Lopes, M. (2015). Multi-armed bandits for intelligent tutoring systems. *Journal of Educational Data Mining, 7*(2), 20–48.

---

#### 3.4.4 M_t — Misconception Tracking

**What it does:** Maintains a continuous exponentially weighted misconception score per concept, updated by each quiz interaction:

```
M_c(t+1)  =  λ · M_c(t)  +  (1−λ) · 𝟏[ conf_t ≥ θ_conf  ∧  x_t = 0 ]
```

with parameters `λ = 0.7` (persistence) and `θ_conf = 4` (on a 0–5 confidence scale, targeting the top 40% of reported ratings). A misconception is flagged when `M_c(t) ≥ θ_M = 0.25`. On a correct response, no increment is applied and the score decays:

```
M_c(t+1)  =  λ · M_c(t)     (correct response; no increment)
```

A misconception auto-resolves when `M_c(t)` falls below `θ_M`. After a single high-confidence error, `M_c = (1−λ) · 1 = 0.30`, which exceeds `θ_M = 0.25` — flagging immediately. A single correct response decays `M_c` to `λ · 0.30 = 0.21`, still above threshold; a second correct response decays it to `λ · 0.21 = 0.147 < θ_M` — resolving. Strongly reinforced misconceptions (multiple consecutive high-confidence errors) accumulate higher scores and therefore require more correct evidence to resolve — correctly modeling the persistence of confident but incorrect beliefs as documented in constraint-based student modeling [16].

**Parameter grounding: λ and θ_M.** The threshold `θ_M = 0.25` is not an arbitrary design choice — it is uniquely constrained by two behavioral requirements given `λ`:

1. *Immediate detection*: a single high-confidence error must flag a misconception immediately. This requires `(1 − λ) · 1 > θ_M`, i.e., `θ_M < 1 − λ = 0.30`.
2. *Two-answer recovery*: a single correct response must not resolve the misconception. After one decay step, `M_c = λ · (1 − λ) = 0.21`; this must still exceed `θ_M`, i.e., `θ_M > λ(1 − λ) = 0.21`.

The two requirements jointly constrain `θ_M` to the interval `(0.21, 0.30)`. The value `θ_M = 0.25` is the approximate midpoint of this interval, providing equal margin from both boundary conditions. Any value in this range preserves the behavioral semantics; the midpoint minimizes sensitivity to small perturbations in `λ`.

The persistence parameter `λ = 0.70` is selected so that the effective half-life of a misconception score under sustained correct responses is exactly 2 interactions: `λ^k < 0.5` first holds at `k = ⌈log(0.5) / log(0.7)⌉ = ⌈1.94⌉ = 2`. This means two consecutive correct responses halve any accumulated score — a conservative requirement consistent with the finding that programming misconceptions require multiple corrective encounters before they are suppressed (Chi et al., 1994 [30]). The half-life interpretation makes `λ` directly auditable: an instructor reviewing the system can verify that "this student's misconception should clear after 2 correct responses" without reading the EMA formula directly.

**Confidence threshold justification.** The threshold `θ_conf = 4` targets the top 40% of expressed confidence on a six-point scale (0–5), consistent with Likert-scale "agree/strongly agree" conventions in educational measurement. Nelson & Narens (1990) distinguish between low-confidence errors (noise or genuine uncertainty) and high-confidence errors (indicative of stable misconceptions) [12]. A threshold of `≤ 3` would generate excessive false positives; a threshold of 5 would be too restrictive as students rarely express maximum confidence on novel material. The value 4 is a calibrated design hyperparameter.

**Why it exists:** Confrey (1990) established that misconceptions are not random errors — they are systematic, stable beliefs that resist correction because the student is confident in them [15]. Ohlsson (1994) formalized constraint-based student modeling around the detection of such incorrect but stable knowledge states [16]. The EMA formulation converts the binary flag-or-not decision into a continuous state variable, enabling the system to distinguish between a student who made one overconfident mistake and one who has demonstrated a persistent pattern of high-confidence errors — and to calibrate intervention intensity accordingly.

**Literature:**

- [12] Nelson, T. O., & Narens, L. (1990). Metamemory: A theoretical framework and new findings. *Psychology of Learning and Motivation, 26*, 125–173.
- [15] Confrey, J. (1990). A review of the research on student conceptions in mathematics, science, and programming. *Review of Research in Education, 16*, 3–56.
- [16] Ohlsson, S. (1994). Constraint-based student modeling. In *Student Modeling: The Key to Individualized Knowledge-Based Instruction* (pp. 167–189). Springer.

---

#### 3.4.5 F_t, G_t, N_t — Affective, Goal, and Off-Topic State

- **F_t** (Affective state): Passed from the Perception Layer [B]. Used downstream to soften tone, offer encouragement, and avoid increasing cognitive load when a student is frustrated. Grounded in Picard's (1997) affective computing framework [17] and D'Mello & Graesser's (2012) findings that frustration is the dominant negative affect during complex learning [4].
- **G_t** (Goal alignment score): Measures how well the current interaction aligns with the student's stated learning goal. Grounded in Zimmermann's (2000) SRL theory, which positions goal alignment as central to self-regulation [18].
- **N_t** (Off-topic strike counter): `N_{t+1} = N_t + 1 if OFF_TOPIC`. Feeds into the Security Model's spam gate.

**Literature:**
- [17] Picard, R. W. (1997). *Affective computing.* MIT Press.
- [18] Zimmermann, B. J. (2000). Attaining self-regulation: A social cognitive perspective. In *Handbook of Self-Regulation* (pp. 13–39). Academic Press.

---

### 3.5 [E] Security Model

**What it does:** Acts as a synchronous, two-stage binary gate — every interaction `x_t` must pass all structural constraints and a pedagogical alignment check before being routed to the pedagogical engine.

**Formal decomposition.** The gate decomposes into two semantically distinct components, reflecting that the signals it aggregates are heterogeneous in both type and recovery behavior:

**Stage 1 — Structural Feasibility Gate φ (hard binary):**

```
φ(x_t)  =  S_cog(x_t) · S_acad(x_t) · S_spam(x_t)   ∈ {0, 1}
```

with explicit definitions:

```
S_cog(x_t)   =  𝟏[ c(x_t) ∈ T_enabled  ∧  ∀ r ∈ prereqs(c(x_t)): r ∈ K_t ]
S_acad(x_t)  =  𝟏[ ¬∃ p ∈ Φ_blocked: match(x_t, p) ]
S_spam(x_t)  =  𝟏[ N_t < N_max ]
```

where `c(x_t)` is the detected concept, `T_enabled ⊆ T` the instructor-enabled topic set, `K_t` the student's mastered concept set, `Φ_blocked` a finite set of academic integrity pattern predicates, `N_t` the current off-topic strike counter, and `N_max` the rate-limit threshold.

**Stage 2 — Pedagogical Alignment Check α (soft threshold):**

```
G_t     =  cos_sim( h(x_t), h(g_0) )   ∈ [0, 1]
α(x_t)  =  𝟏[ G_t ≥ θ_goal ]
```

where `h(·)` is a frozen sentence embedding and `g_0` is the student's stated learning goal. Cosine similarity is used because `h(·)` produces L2-normalized vectors, making cosine the natural inner-product metric for semantic proximity in that space — equivalent to the dot product and requiring no additional normalization. The threshold `θ_goal` is a calibrated hyperparameter selected to maximize the F1 score of human-labeled aligned versus misaligned interactions on a held-out validation set. Alternative embedding models may be substituted without changing the gate formulation, since `G_t` is used only for its ordinal properties (higher = more aligned) relative to `θ_goal`.

**Composite gate:**

```
σ(x_t)  =  φ(x_t) · α(x_t)   ∈ {0, 1}
```

`σ(x_t) = 0` blocks the interaction with a targeted explanation; `σ(x_t) = 1` routes it to the pedagogical engine.

**Asynchronous semantic monitor (non-blocking):**

```
f_sem : X → {flag, pass}     (async, latency-tolerant; output → audit log only)
```

`f_sem` runs a heavier LLM-based semantic analysis after the synchronous gate decision and writes its output to an instructor-visible audit log. It does not modify `σ` and introduces no latency to the student-facing response path. It is therefore modeled separately from the gate rather than as a constituent signal.

| Signal | Type | Condition for block | Basis |
| --- | --- | --- | --- |
| `S_cog` | Hard (φ) | Topic not enabled or prerequisite unmet | Sweller (1988) [19]; Liang et al. (2018) [5] |
| `S_acad` | Hard (φ) | Academic integrity pattern matched | Course policy; Φ_blocked |
| `S_spam` | Hard (φ) | Off-topic strike count ≥ N_max | Abuse prevention |
| `α` | Soft (threshold) | G_t < θ_goal | Pintrich (2000) [2]; Zimmermann (2000) [18] |
| `f_sem` | Async monitor | — (non-blocking) | Audit and instructor review |

**Why this decomposition?** The three signals in `φ` share the same semantic role: each is a hard binary predicate representing a necessary condition for a response to be structurally permissible. The product-of-indicators within `φ` is equivalent to their logical conjunction and is the appropriate formalism for a fail-safe gate: any single violated constraint is sufficient for rejection, regardless of the others. All three are binary by nature (not thresholded continuous signals), making the product semantics exact rather than approximate.

`G_t` is semantically distinct — it is a continuous cosine similarity score derived from embeddings and thresholded at a calibrated `θ_goal`. Grouping it with the hard binary signals in `φ` would conflate two different mathematical objects (a policy predicate and a thresholded real-valued signal). Separating it as `α(x_t)` makes the distinction explicit: `φ = 0` represents "this response is structurally impermissible"; `α = 0` represents "this response is pedagogically misaligned with the student's goal." The two failure modes have different recovery implications — a `φ`-blocked interaction requires a policy-level fix (unlock the topic, address the integrity concern, reduce spam), while an `α`-blocked interaction requires a pedagogical redirect (help the student reconnect with their learning goal).

Crucially, `φ(x_t) = 0` collapses `σ` to 0 regardless of `α`, preserving the fail-safe property of the hard gate: no alignment score can override a structural block.

**Why this exists:** Cognitive Load Theory (Sweller, 1988) establishes that presenting material beyond a student's current competence actively impairs learning [19] — `S_cog` implements this as a structural prerequisite gate. Goal alignment (`α`) is grounded in Pintrich's (2000) finding that interactions misaligned with a student's stated learning goal disrupt self-regulated learning processes [2]. The security gate therefore encodes both a cognitive safety constraint (CLT) and a motivational coherence constraint (SRL theory) within a single, interpretable two-stage decision.

**Literature:**

- [2] Pintrich, P. R. (2000). The role of goal orientation in self-regulated learning. In *Handbook of Self-Regulation* (pp. 451–502). Academic Press.
- [5] Liang, C., Wu, Z., Huang, W., & Giles, C. L. (2018). Recovering concept prerequisite relations from university course dependencies. *AAAI-18*.
- [18] Zimmermann, B. J. (2000). Attaining self-regulation: A social cognitive perspective. In *Handbook of Self-Regulation* (pp. 13–39). Academic Press.
- [19] Sweller, J. (1988). Cognitive load during problem solving: Effects on learning. *Cognitive Science, 12*(2), 257–285.

---

### 3.6 [F] Pedagogical Model — SRL Engine

**What it does:** Routes each interaction to the appropriate pedagogical agent, applies the UCB1 style selection, and updates the Learner Model after each interaction.

#### Agents

| Agent | Trigger | Method | BKT Evidence |
|-------|---------|--------|--------------|
| Scaffolding | PROBLEM intent | SRL FSM, Remediation Ladder | — |
| Examiner | QUIZ intent | 2-tier grading + JoL | `evidence_type = quiz` → p_quiz (ceiling 0.60) |
| Code Reviewer | REVIEW intent | Sandwich method | `evidence_type = code` → p_code (ceiling 0.10) |
| Micro-Challenge | CONCEPT (post-teach) | Code attempt detection | `evidence_type = micro` → p_micro (ceiling 0.25) |
| Prereq Gate | CONCEPT / PROBLEM | Neo4j edge check ← [C] | — |
| Socratic Agent | Fallback | CoT + Hybrid RAG | — |

**Why the Scaffolding Agent?** Vygotsky's (1978) Zone of Proximal Development (ZPD) defines the gap between what a student can do alone and what they can do with guidance [20]. Wood, Bruner & Ross (1976) operationalized this as "scaffolding" — providing support that is gradually removed as competence increases [21]. The SRL Finite State Machine (FSM) tracks whether a student is in the early, middle, or late stages of problem-solving and adjusts support accordingly.

**Why the Examiner Agent?** The Testing Effect (Roediger & Karpicke, 2006) shows that retrieving information from memory strengthens retention far more than re-reading the same material [22]. The Examiner Agent provides regular retrieval practice, with the JoL confidence rating (Nelson & Narens, 1990 [12]) used to calibrate both the SM-2 quality score and the BKT update.

**Why the Sandwich Method (Code Reviewer)?** The "feedback sandwich" (positive → constructive → positive) is a widely cited technique in educational psychology for delivering corrective feedback without triggering defensive behavior. It is grounded in Hattie & Timperley's (2007) synthesis of feedback research, which identifies feedback that addresses the task (not the person) as the most effective form for learning [23].

**Why the Socratic Agent?** Collins & Stevens (1982) formalized Socratic inquiry as a teaching strategy that forces students to articulate and defend their understanding rather than passively receive information [24]. The agent uses Chain-of-Thought prompting to guide LLM responses toward asking clarifying questions rather than providing direct answers.

**Literature:**
- [20] Vygotsky, L. S. (1978). *Mind in society.* Harvard University Press.
- [21] Wood, D., Bruner, J. S., & Ross, G. (1976). The role of tutoring in problem solving. *Journal of Child Psychology and Psychiatry, 17*(2), 89–100.
- [22] Roediger, H. L., & Karpicke, J. D. (2006). Test-enhanced learning. *Psychological Science, 17*(3), 249–255.
- [23] Hattie, J., & Timperley, H. (2007). The power of feedback. *Review of Educational Research, 77*(1), 81–112.
- [24] Collins, A., & Stevens, A. L. (1982). Goals and strategies of inquiry teachers. In *Advances in Instructional Psychology* (Vol. 2, pp. 65–119). Erlbaum.

---

### 3.7 [G] Interface

**What it does:** Delivers responses via Server-Sent Events (SSE) for real-time token streaming, renders Mermaid diagrams for visual learners, and provides a Classroom module with:
- `context = transcript[t=0 … t_pause]` — answers restricted to watched content
- `meta = summary(full_transcript)` — high-level video overview at any timestamp
- Bi-directional timestamp hints: `argmax_seg [keywords ∩ q] ≥ 2` for both past and future segments

**Why bi-directional hints?** The Classroom module's timestamp hints serve different learning functions depending on direction: forward hints support **anticipatory attention** — students know something relevant is coming, which primes encoding; backward hints support **retrieval practice** — the student is directed to re-encounter content they may have processed too shallowly the first time. Both effects are consistent with the elaborative interrogation literature (Pressley et al., 1992) [25].

The keyword match threshold of **two or more** (after stop-word removal) is a design hyperparameter balancing precision and recall: a single keyword match would trigger hints on nearly every question (common domain terms like "variable" or "pointer" appear throughout any C programming lecture transcript), while a threshold of three would suppress hints for short, specific questions. The **30-second context window** for grouping adjacent transcript segments is calibrated to typical lecture pacing — most individual explanatory units take 20–40 seconds — preventing an artificially narrow hint range while maintaining specificity. Both values are tunable and could be adapted based on transcript vocabulary density or average segment length for a given video corpus.

**Why Mermaid diagrams?** Paivio's (1991) Dual Coding Theory proposes that information encoded in both verbal and visual form is retained more robustly than information encoded in only one modality [26]. Generating diagrams (flowcharts, memory layout visualizations) alongside text explanations creates dual-coded representations for complex C programming concepts.

**Literature:**
- [25] Pressley, M., Wood, E., Woloshyn, V. E., Martin, V., King, A., & Menke, D. (1992). Encouraging mindful use of prior knowledge. *Educational Psychologist, 27*(1), 91–109.
- [26] Paivio, A. (1991). Dual coding theory: Retrospect and current status. *Canadian Journal of Psychology, 45*(3), 255–287.

---

### 3.8 [H] Persistence

**What it does:** Stores all state that must survive across sessions:

```
user_knowledge:
  p_mastery_quiz  ∈ [0, 0.60]
  p_mastery_micro ∈ [0, 0.25]
  p_mastery_code  ∈ [0, 0.10]
  p_mastery       = Σ tiers ∈ [0, 0.95]
  interval_days · ease_factor · due_date · review_count

learning_profile (JSON):
  win_rates: {style: {wins, total}}
  misconceptions: [M_t, …]
  frustration_level · skipped_challenges
```

**Why it exists:** Adaptive tutoring that resets at each session cannot accumulate the longitudinal evidence needed to distinguish a student who consistently struggles with pointers from one who is having a bad day. Mastery learning (Bloom, 1968) requires tracking competency over time and gating progression on demonstrated mastery — not on time spent [27]. Persistent storage of all learner model variables is the infrastructure that makes longitudinal adaptation possible.

**Literature:**
- [27] Bloom, B. S. (1968). Learning for mastery. *Evaluation Comment, 1*(2), 1–12.
- [28] Mislevy, R. J., Steinberg, L. S., & Almond, R. G. (2003). On the structure of educational assessments. *Measurement: Interdisciplinary Research and Perspectives, 1*(1), 3–62.

---

## 4. Novelty and Contributions

The following aspects of this system go beyond direct application of existing techniques:

### 4.1 Factored Multi-Skill BKT with Conjunctive Mastery *(primary contribution)*

Standard BKT uses a single latent state per concept and cannot represent qualitatively different types of evidence. Deep Knowledge Tracing and DKVMN extend BKT to multiple skills via neural networks but sacrifice interpretability. This system introduces a **factored three-tracker BKT model** in which each tracker targets a distinct Bloom's cognitive level (declarative / procedural / applied) and is updated only by evidence of the matching type. The mastery decision is the conjunctive rule `m_c = 𝟏[∀k: P_t^(k) ≥ θ_mastery^(k)]` — mastery requires all three subskill posteriors to meet their individual sufficiency thresholds simultaneously (where `θ_mastery^(k) < θ_max^(k)` for all `k`; see Section 3.4.1).

This provides two properties that standard BKT cannot: (1) **structural evidence specificity** — quiz evidence updates only the declarative tracker, preventing one-dimensional evidence from inflating the composite score; (2) **closed-form interpretability** — the mastery criterion has an exact algebraic expression in terms of individual posterior ceilings, with no neural approximation. A student who performs perfectly on quizzes is bounded below mastery because `P_t^(2)` and `P_t^(3)` remain at their priors until procedural and applied evidence is provided. This property follows directly from the evidence-routing function `δ^(k)(e_t)` and the conjunctive mastery criterion: since `δ^(2)(quiz) = δ^(3)(quiz) = 0`, quiz interactions leave the procedural and applied trackers unchanged, making `m_c = 1` structurally unreachable from quiz evidence alone.

### 4.2 UCB1 Pedagogical Style Selection with Live Student Feedback

While bandit algorithms have been applied to content sequencing in ITS (Clement et al., 2015 [14]), applying UCB1 to **teaching style** (how content is delivered) rather than **content selection** (what is delivered) is less explored. The feedback signal here is explicit (thumbs up/down) rather than inferred from response time or quiz performance, making the update semantically clean.

### 4.3 IRL/SRL Dual-Track Architecture

Most ITS architectures treat the instructor as a one-time configuration step (setting up the system). This architecture maintains IRL and SRL as parallel, continuously active tracks that converge at the Security Model. Instructor topic locks propagate in real time — if a teacher disables "Pointers" mid-semester, the gate fires immediately for all students without requiring a system restart or per-student reconfiguration.

### 4.4 Bi-Directional Video Transcript Timestamp Hints

Video-integrated chat tutoring typically answers based on watched content or defers to future content. This system introduces **bi-directional hints** — suggesting both future segments ("keep watching, this is covered at 3:00") and past segments ("you may want to revisit 0:56") based on keyword matching between the student's question and the full transcript. This transforms the video player from a passive viewing tool into an active navigation aid.

### 4.5 Cross-Modal Classroom-to-Chat Bridge

When a student receives a concept explanation in the chat interface and there is a relevant lecture video in the classroom, the system appends a contextual video suggestion. This bridges two interaction modes that are typically siloed, allowing the system to reinforce text-based explanations with video-based demonstrations — directly supporting Dual Coding Theory [26].

### 4.6 Security as a Two-Stage Interpretable Gate

Safety filtering in LLM-based systems is typically implemented as a single opaque classifier or a fixed blocklist. This system instead decomposes the gate into two semantically distinct stages: a **structural feasibility gate** `φ = S_cog · S_acad · S_spam` (product of hard binary policy constraints, all of the same semantic type) and a **pedagogical alignment check** `α = 𝟏[G_t ≥ θ_goal]` (thresholded continuous similarity score). The composite gate `σ = φ · α` is both compositional and interpretable: each signal has an explicit semantic meaning, the reason for any block is attributable to a specific named signal, and the two stages have distinct recovery implications (structural fix vs. pedagogical redirect). The asynchronous semantic monitor `f_sem` runs out-of-band without blocking the student-facing response path, cleanly separating real-time safety from deeper post-hoc analysis.

---

## 5. Summary Table

| Component | Technique | Key Literature |
|-----------|-----------|----------------|
| [A] Instructor Model | IRL constraints, assignment lifecycle | Anderson et al. (1995); Pintrich (2000) |
| [B] Perception Layer | Intent classification, NER, affective profiling | Zaratiana et al. (2023); D'Mello & Graesser (2012) |
| [C] Domain Model | Knowledge graph, RAG, prerequisite chains | Liang et al. (2018); Lewis et al. (2020) |
| [D] K_t — BKT | Tiered Bayesian Knowledge Tracing | Corbett & Anderson (1994); Bloom (1956) |
| [D] SM-2 | Spaced repetition | Ebbinghaus (1885); Wozniak (1990); Cepeda et al. (2006) |
| [D] C_s — UCB1 | Multi-armed bandit style selection | Auer et al. (2002); Clement et al. (2015) |
| [D] M_t — Misconceptions | Confidence-weighted misconception detection | Confrey (1990); Ohlsson (1994) |
| [D] F_t — Affective | Frustration detection | Picard (1997); D'Mello & Graesser (2012) |
| [E] Security Model | Product gate, cognitive load gate | Sweller (1988); Pintrich (2000) |
| [F] Scaffolding | ZPD, SRL FSM | Vygotsky (1978); Wood et al. (1976) |
| [F] Examiner | Testing effect, JoL, BKT update | Roediger & Karpicke (2006); Nelson & Narens (1990) |
| [F] Code Reviewer | Sandwich feedback | Hattie & Timperley (2007) |
| [F] Socratic Agent | Inquiry teaching, CoT | Collins & Stevens (1982) |
| [G] Mermaid / Visual | Dual coding | Paivio (1991) |
| [G] Timestamp hints | Elaborative interrogation, retrieval practice | Pressley et al. (1992) |
| [H] Persistence | Mastery learning, longitudinal tracking | Bloom (1968) |

---

## 6. References

[1] Anderson, J. R., Corbett, A. T., Koedinger, K. R., & Pelletier, R. (1995). Cognitive tutors: Lessons learned. *Journal of the Learning Sciences, 4*(2), 167–207.

[2] Pintrich, P. R. (2000). The role of goal orientation in self-regulated learning. In *Handbook of Self-Regulation* (pp. 451–502). Academic Press.

[3] Zaratiana, U., Tomeh, N., & Shi, C. (2023). GLiNER: Generalist model for named entity recognition using bidirectional transformer. *arXiv:2311.08526*.

[4] D'Mello, S., & Graesser, A. (2012). Dynamics of affective states during complex learning. *Learning and Instruction, 22*(2), 145–157.

[5] Liang, C., Wu, Z., Huang, W., & Giles, C. L. (2018). Recovering concept prerequisite relations from university course dependencies. *Proceedings of AAAI-18*.

[6] Lewis, P., Perez, E., Piktus, A., et al. (2020). Retrieval-augmented generation for knowledge-intensive NLP tasks. *NeurIPS 2020*.

[7] Corbett, A. T., & Anderson, J. R. (1994). Knowledge tracing: Modeling the acquisition of procedural knowledge. *User Modeling and User-Adapted Interaction, 4*(4), 253–278.

[8] Bloom, B. S. (1956). *Taxonomy of educational objectives: The classification of educational goals. Handbook I: Cognitive domain.* Longmans, Green.

[9] Ebbinghaus, H. (1885). *Über das Gedächtnis.* Duncker & Humblot.

[10] Wozniak, P. A. (1990). *Optimization of learning.* SuperMemo World.

[11] Cepeda, N. J., Pashler, H., Vul, E., Wixted, J. T., & Rohrer, D. (2006). Distributed practice in verbal recall tasks: A review and quantitative synthesis. *Psychological Bulletin, 132*(3), 354–380.

[12] Nelson, T. O., & Narens, L. (1990). Metamemory: A theoretical framework and new findings. *Psychology of Learning and Motivation, 26*, 125–173.

[13] Auer, P., Cesa-Bianchi, N., & Fischer, P. (2002). Finite-time analysis of the multiarmed bandit problem. *Machine Learning, 47*(2-3), 235–256.

[14] Clement, B., Roy, D., Oudeyer, P. Y., & Lopes, M. (2015). Multi-armed bandits for intelligent tutoring systems. *Journal of Educational Data Mining, 7*(2), 20–48.

[15] Confrey, J. (1990). A review of the research on student conceptions in mathematics, science, and programming. *Review of Research in Education, 16*, 3–56.

[16] Ohlsson, S. (1994). Constraint-based student modeling. In *Student Modeling: The Key to Individualized Knowledge-Based Instruction* (pp. 167–189). Springer.

[17] Picard, R. W. (1997). *Affective computing.* MIT Press.

[18] Zimmermann, B. J. (2000). Attaining self-regulation: A social cognitive perspective. In *Handbook of Self-Regulation* (pp. 13–39). Academic Press.

[19] Sweller, J. (1988). Cognitive load during problem solving: Effects on learning. *Cognitive Science, 12*(2), 257–285.

[20] Vygotsky, L. S. (1978). *Mind in society: The development of higher psychological processes.* Harvard University Press.

[21] Wood, D., Bruner, J. S., & Ross, G. (1976). The role of tutoring in problem solving. *Journal of Child Psychology and Psychiatry, 17*(2), 89–100.

[22] Roediger, H. L., & Karpicke, J. D. (2006). Test-enhanced learning: Taking memory tests improves long-term retention. *Psychological Science, 17*(3), 249–255.

[23] Hattie, J., & Timperley, H. (2007). The power of feedback. *Review of Educational Research, 77*(1), 81–112.

[24] Collins, A., & Stevens, A. L. (1982). Goals and strategies of inquiry teachers. In *Advances in Instructional Psychology* (Vol. 2, pp. 65–119). Erlbaum.

[25] Pressley, M., Wood, E., Woloshyn, V. E., Martin, V., King, A., & Menke, D. (1992). Encouraging mindful use of prior knowledge. *Educational Psychologist, 27*(1), 91–109.

[26] Paivio, A. (1991). Dual coding theory: Retrospect and current status. *Canadian Journal of Psychology, 45*(3), 255–287.

[27] Bloom, B. S. (1968). Learning for mastery. *Evaluation Comment, 1*(2), 1–12.

[28] Mislevy, R. J., Steinberg, L. S., & Almond, R. G. (2003). On the structure of educational assessments. *Measurement: Interdisciplinary Research and Perspectives, 1*(1), 3–62.

[29] Baker, R. S., Corbett, A. T., & Aleven, V. (2008). More accurate student modeling through contextual estimation of slip and guess probabilities in Bayesian knowledge tracing. In *Proceedings of the 9th International Conference on Intelligent Tutoring Systems (ITS 2008)*, pp. 406–415.

[30] Chi, M. T. H., de Leeuw, N., Chiu, M. H., & LaVancher, C. (1994). Eliciting self-explanations improves understanding. *Cognitive Science, 18*(3), 439–477.
