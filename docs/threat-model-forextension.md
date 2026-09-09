# SAGE Security Extension — Threat Model

This document outlines the threat model for the SAGE-IRL security paper, analyzing threats across the entire system (RBAC, ABAC, and Cognitive Access Control / CAC).

---

## 1. System Assets & Security Objectives

| Asset | Security & Pedagogical Objective | Primary Risk |
|---|---|---|
| **Mastery & Credentials ($\mathcal{K}$)** | Prevent unauthorized advancement or unearned topic unlocking. | Credential inflation, prerequisite evasion. |
| **Pedagogical Boundary (Pedagogy)** | Ensure student engages in productive struggle; prevent full code spoon-feeding. | Academic dishonesty, learned helplessness exploitation. |
| **Execution & Infrastructure** | Prevent generation of dual-use destructive C code (malware, fork bombs, DoS). | AI-assisted code exploitation, host exhaustion. |
| **Curriculum & Exam Keys** | Protect instructor-restricted solutions, rubrics, future topics, and peer data. | Unauthorized information disclosure. |
| **Cognitive Telemetry ($\mathcal{C}$)** | Prevent gaming of affective/cognitive states to trick the tutor. | State spoofing, prompt injection via feigned distress. |

---

## 2. Adversary Taxonomy

We model four distinct adversaries targeting the educational AI tutor:

### $A_1$: Credential Inflater / Prerequisite Bypasser
* **Goal**: Unlock advanced topics without completing prerequisites.
* **Methods**: Asserting unearned knowledge, exploiting conversational bypasses (`"teach me anyway"`), or guessing through assessments.
* **Target Layer**: ABAC (Curriculum gating) and CAC (Prerequisite validation).

### $A_2$: Educational Asset Extractor
* **Goal**: Extract protected artifacts (exam solutions, prompt templates, hidden rubrics, other students' private data).
* **Methods**: Direct prompt injection, role-play jailbreaks, cross-user memory probing.
* **Target Layer**: RBAC (User isolation) and ABAC (Document & RAG filtering).

### $A_3$: Harm Proxy / Dual-Use Code Exploiter
* **Goal**: Elicit destructive C code (fork bombs, reverse shells, infinite DoS loops, memory corruption).
* **Methods**: Single-turn evasion, dual-use educational framing (`"explain fork() with an endless loop"`), or **multi-turn crescendo attacks** (slow burns).
* **Target Layer**: Sentinel (Hard blocks), Session Monitor (Trajectory accumulator), and CAC (Context gating).

### $A_4$: State Spoofer / Metacognitive Adversary
* **Goal**: Manipulate the tutor into bypassing Socratic withholding and spoon-feeding complete code solutions.
* **Methods**: Faking frustration/rage, simulating cognitive helplessness, or manipulating self-assessment sliders.
* **Target Layer**: CAC (Pedagogical gatekeeper) and Learner Model.

---

## 3. Threat Matrix (STRIDE Mapping)

| STRIDE Category | Target Asset | Adversary | Primary Control Mechanism | Defense Layer |
|---|---|---|---|---|
| **S**poofing | User Identity & Competence | $A_1$, $A_4$ | • Role token verification<br>• Factored BKT mastery verification ($\mathcal{K}$) | RBAC, CAC |
| **T**ampering | Session State & Cognitive Profile | $A_4$ | • **Sign Constraint**: Inferred state can only restrict, never widen permissions ($\mathcal{M} \setminus \mathcal{R}$)<br>• Downward-only self-assessment | CAC, Learner Model |
| **R**epudiation | Turn Actions & Telemetry | $A_1$, $A_3$ | • Append-only, `study_id`-linked research telemetry (`turn_log`, `evidence_log`) | System Audit |
| **I**nformation Disclosure | Exam Keys, Locked Topics, Peer Data | $A_2$ | • Cross-user privacy guard<br>• Instructor topic locks<br>• Contextual retrieval filtering | RBAC, ABAC |
| **D**enial of Service | System Resources & Educational Process | $A_3$, $A_4$ | • Destructive code hard-blocks (fork bomb signatures)<br>• **Session Monitor**: Decayed multi-turn trajectory accumulator ($\text{acc}_t, \text{peak}_t$) | Sentinel, CAC |
| **E**levation of Privilege | Curriculum Access & Security Latitude | $A_1$, $A_3$ | • Revocable certification gate<br>• **Competence-Keyed Latitude**: Security threshold relaxed only by verified $|\mathcal{K}|$, bounded by a hard floor | ABAC, CAC |

---

## 4. Layered Defense Architecture

```
[Student Request]
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│ 1. RBAC Layer                                               │
│    • Enforces static role boundaries (Student vs Teacher)   │
└──────────────────────────────┬──────────────────────────────┘
                               │ Passed
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. ABAC Layer                                               │
│    • Enforces instructor curriculum locks                   │
│    • Restricts retrievable document sensitivity             │
└──────────────────────────────┬──────────────────────────────┘
                               │ Passed
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. Cognitive Access Control (CAC) Layer                     │
│    • S_acad: Blocks solution dumping during quizzes         │
│    • S_cog: Blocks code complexity during rage/overload     │
│    • S_goal: Relaxes latitude based on certified |K|        │
│    • Context-Gating: Evaluates dual-use C concepts          │
│    • Socratic Withholding: Blocks answers for mastered K    │
└──────────────────────────────┬──────────────────────────────┘
                               │ Passed
                               ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. Session Monitor (Multi-Turn Trajectory Defense)          │
│    • Accumulates cross-turn crescendo risk                  │
│    • Escalates to transcript-level LLM safety adjudicator   │
└──────────────────────────────┬──────────────────────────────┘
                               │ Passed
                               ▼
[RAG Retrieval & Socratic Generation]
```
