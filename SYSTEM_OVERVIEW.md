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
                              σ = S_cog · S_goal · S_acad · S_spam
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

#### 3.4.1 K_t — Mastery State (Tiered Bayesian Knowledge Tracing)

**What it does:** Tracks the probability that a student has mastered a concept, separately for three evidence tiers:

| Tier | Evidence Type | Bloom's Level | Ceiling |
|------|--------------|---------------|---------|
| p_quiz | Quiz (declarative) | Remember / Understand | 0.60 |
| p_micro | Micro-challenge (procedural) | Apply | 0.25 |
| p_code | Code review (applied) | Analyze / Evaluate | 0.10 |

Composite mastery: `P(L) = p_quiz + p_micro + p_code ≤ 0.95`

The BKT update equations per tier:
```
P(L|✓) = P(L)·(1−Ps) / [P(L)·(1−Ps) + (1−P(L))·Pg]
P(L|✗) = P(L)·Ps   / [P(L)·Ps + (1−P(L))·(1−Pg)]
P(L_next) = P(L|obs) + (1−P(L|obs))·Pt
```

**Why it exists:** Bayesian Knowledge Tracing (BKT) was introduced by Corbett & Anderson (1994) and remains one of the most validated models for tracking student knowledge in intelligent tutoring systems [7]. Standard BKT uses a single probability per concept. The tiered extension here is motivated by Bloom's Taxonomy (1956), which distinguishes remembering facts, applying procedures, and analyzing/evaluating — three qualitatively different cognitive levels that a single probability cannot capture [8].

The ceiling caps are the key novelty: a student who answers quiz questions correctly can approach 0.60 mastery but cannot cross 0.95 without also demonstrating procedural skill (micro-challenge) and applied reasoning (code review). This structurally prevents the system from declaring mastery based on one-dimensional evidence — a known weakness of standard BKT in practice.

The ceiling caps (0.60, 0.25, 0.10) are hyperparameters whose sum (0.95) matches the standard BKT mastery threshold [7]. Their distribution reflects the relative epistemic weight of each evidence tier, consistent with Evidence-Centered Design (ECD) principles [28]: quiz evidence is weighted most heavily (0.60) because it is the most frequent form of assessment and maps to lower Bloom's levels (Remember/Understand); micro-challenge evidence is intermediate (0.25) as it requires procedural Application; code review contributes least (0.10) because it is the rarest and most subjective evidence type, targeting higher Bloom's levels (Analyze/Evaluate). The specific proportions are design hyperparameters; the constraint that they sum to 0.95 is theoretically grounded in the BKT mastery criterion.

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

**What it does:** Selects the teaching style (analogy, example, visual, Socratic, direct) that maximizes student engagement, based on thumbs-up/down feedback:
```
score_s = w_s / n_s  +  √(2 · ln(N) / n_s)
style*  = argmax_s [score_s]
```

**Why it exists:** Students differ in how they best receive new information — some respond to analogies, others to worked examples, others to direct definitions. Rather than choosing a style arbitrarily or using a static profile, this system treats style selection as an exploration-exploitation problem. The Upper Confidence Bound (UCB1) algorithm, introduced by Auer et al. (2002), provides a principled balance between exploiting known-good styles and exploring underused ones [13].

Clement et al. (2015) demonstrated that multi-armed bandit algorithms outperform fixed-curriculum approaches in intelligent tutoring systems by adapting to individual learner responses in real time [14]. The win-rate signal here comes directly from explicit student feedback (thumbs up/down), making it one of the few tutoring systems where the pedagogical style policy is updated by student-expressed preference rather than inferred from implicit signals alone.

**Literature:**
- [13] Auer, P., Cesa-Bianchi, N., & Fischer, P. (2002). Finite-time analysis of the multiarmed bandit problem. *Machine Learning, 47*(2-3), 235–256.
- [14] Clement, B., Roy, D., Oudeyer, P. Y., & Lopes, M. (2015). Multi-armed bandits for intelligent tutoring systems. *Journal of Educational Data Mining, 7*(2), 20–48.

---

#### 3.4.4 M_t — Misconception Tracking

**What it does:** Flags and stores misconceptions when a student demonstrates high confidence but answers incorrectly:
```
if conf ≥ 4 ∧ correct = False  →  store(M_t)
if correct = True               →  resolve(M_t)
```

The threshold `conf ≥ 4` (on a 0–5 scale) targets the top 40% of expressed confidence ratings. This operationalizes "high confidence" as defined in metacognitive monitoring research — Nelson & Narens (1990) distinguish between low-confidence errors (often noise or genuine uncertainty) and high-confidence errors (indicative of systematic, stable misconceptions) [12]. A threshold of ≤ 3 would generate excessive false positives, flagging ordinary uncertainty rather than confident-but-wrong beliefs; a threshold of 5 only would be too restrictive, as students rarely report maximum confidence. The value 4 is a design hyperparameter calibrated to capture the top two ratings on a six-point scale, consistent with Likert-scale "agree/strongly agree" conventions in educational measurement.

**Why it exists:** Confrey (1990) established that misconceptions are not random errors — they are systematic, stable beliefs that resist correction precisely because the student is confident in them [15]. Ohlsson (1994) formalized constraint-based student modeling around the detection of such incorrect but stable knowledge states [16]. A system that treats high-confidence wrong answers the same as low-confidence wrong answers cannot provide targeted remediation. By tagging misconceptions and surfacing them in subsequent interactions, the system can apply the specific intervention strategies recommended for misconception correction (contrast with correct examples, direct confrontation of the incorrect belief).

**Literature:**
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

**What it does:** Acts as a product gate — every interaction must pass all four binary checks before reaching the pedagogical engine:
```
σ = S_cog · S_goal · S_acad · S_spam,   each Sᵢ ∈ {0, 1}
σ = 0 → BLOCK,   σ = 1 → PASS
```

| Signal | Condition for 0 (BLOCK) | Literature basis |
|--------|------------------------|-----------------|
| S_cog | Topic not in enabled_topics | Sweller (1988): cognitive overload [19] |
| S_goal | G_t < θ_goal | Pintrich (2000): goal alignment [2] |
| S_acad | Academic integrity pattern match | Course policy |
| S_spam | N_t ≥ N_max | Off-topic abuse prevention |
| S_sem | LLM semantic judge (async, flags only) | Does not block σ |

**Why it exists:** Cognitive Load Theory (Sweller, 1988) demonstrates that presenting material beyond a student's current capacity actively impairs learning [19]. The S_cog gate implements this structurally — content gating is not just an administrative choice but a pedagogical one. The product formulation (multiplication rather than OR/AND logic) ensures that any single failed check blocks the interaction regardless of the others, producing a conservative, fail-safe behavior.

**Literature:**
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

### 4.1 Tiered BKT with Bloom's Taxonomy Ceiling Caps *(primary contribution)*

Standard BKT uses a single probability per concept. Multi-skill BKT extensions (e.g., DKVMN, Deep Knowledge Tracing) use neural networks but lose interpretability. This system introduces **hard ceiling caps per Bloom's level** as a structural constraint: `p_quiz ≤ 0.60`, `p_micro ≤ 0.25`, `p_code ≤ 0.10`, composite `≤ 0.95`. A student who does only quizzes is provably bounded below mastery. This makes the mastery definition multi-dimensional and verifiable — a student cannot "quiz their way" to mastery without procedural and applied evidence.

### 4.2 UCB1 Pedagogical Style Selection with Live Student Feedback

While bandit algorithms have been applied to content sequencing in ITS (Clement et al., 2015 [14]), applying UCB1 to **teaching style** (how content is delivered) rather than **content selection** (what is delivered) is less explored. The feedback signal here is explicit (thumbs up/down) rather than inferred from response time or quiz performance, making the update semantically clean.

### 4.3 IRL/SRL Dual-Track Architecture

Most ITS architectures treat the instructor as a one-time configuration step (setting up the system). This architecture maintains IRL and SRL as parallel, continuously active tracks that converge at the Security Model. Instructor topic locks propagate in real time — if a teacher disables "Pointers" mid-semester, the gate fires immediately for all students without requiring a system restart or per-student reconfiguration.

### 4.4 Bi-Directional Video Transcript Timestamp Hints

Video-integrated chat tutoring typically answers based on watched content or defers to future content. This system introduces **bi-directional hints** — suggesting both future segments ("keep watching, this is covered at 3:00") and past segments ("you may want to revisit 0:56") based on keyword matching between the student's question and the full transcript. This transforms the video player from a passive viewing tool into an active navigation aid.

### 4.5 Cross-Modal Classroom-to-Chat Bridge

When a student receives a concept explanation in the chat interface and there is a relevant lecture video in the classroom, the system appends a contextual video suggestion. This bridges two interaction modes that are typically siloed, allowing the system to reinforce text-based explanations with video-based demonstrations — directly supporting Dual Coding Theory [26].

### 4.6 Security as an Interpretable Product Gate

Safety/appropriateness filtering in LLM systems is typically implemented as a single opaque classifier or a fixed list of blocked topics. Framing it as `σ = S_cog · S_goal · S_acad · S_spam` makes the gate **compositional and interpretable**: each signal has an explicit semantic meaning, the product structure means any single failure blocks the interaction, and the reason for blocking is always attributable to a specific signal.

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
