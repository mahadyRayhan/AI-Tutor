# Answer Grading Mechanism: Binary Correct/Incorrect Signal
**AI-Tutor System**

---

## Overview

The system grades student answers using a **hybrid two-tier approach**:

1. **Tier 1 (Fast)**: Vector similarity matching (cosine similarity on embeddings)
2. **Tier 2 (Smart)**: LLM semantic grading with rubric evaluation

This produces a binary signal: `is_correct` = **True** or **False** (1 or 0).

---

## Complete Grading Pipeline

### 1. QUIZ INITIATION & SETUP

**File**: `backend/app/agents/examiner.py` (_start_quiz method)

- Question fetched from Neo4j Knowledge Graph (with Q&A pairs stored as `quiz_data`)
- Question is encoded into a vector:
  ```
  correct_vector_google  # Via Google Gemini API (for backup)
  correct_vector_local   # Via local FastEmbed model (preferred)
  ```
- State saved: `quiz_vector`, `quiz_correct_text`, `quiz_topic`

#### Vector Encoding Process
```python
# Local (preferred): 
from app.core.fast_classifier import fast_classifier
correct_vector_local = fast_classifier.intent_model.encode(correct_answer_text)
# Result: 384-dimensional embedding (all-MiniLM-L6-v2 model)

# API backup:
correct_vector_google = await llm.get_embedding(correct_answer_text)
# Result: varies by provider (Google/OpenAI)
```

---

### 2. STUDENT CONFIDENCE RATING (NEW in Implementation)

**File**: `examiner.py` (lines 20-30)

Before grading, system asks:
> "On a scale of 1 to 5, how confident are you that you understand [topic]?"

**Why**: Enables **calibration accuracy analysis** (see SAGE paper p.2)
- Low confidence + Pass = Self-efficacy boost
- High confidence + Fail = Misconception flagged

Confidence rating stored: `student_confidence` (integer 1-5)

---

### 3. STUDENT ANSWER SUBMISSION

**File**: `examiner.py` (_grade_quiz method)

When student submits answer, system:

```
User Input: state.query
↓
Extract confidence score from prior state
↓
Check for "surrender" phrases ("I don't know", "idk", "skip")
↓
If surrender → is_correct = False, exit early
```

---

### 4. TIER 1: VECTOR SIMILARITY GRADING (Fast Path)

**File**: `examiner.py` (_smart_grade_answer method, lines 360-390)

#### Step 1: Encode Student Answer
```python
# LOCAL PRIORITY (CPU-based, ~20ms):
if config.INTENT_CLASSIFIER_MODE == "fast" and vec_local:
    from app.core.fast_classifier import fast_classifier
    s_vec = fast_classifier.intent_model.encode(student_answer)
    # Result: 384-dimensional embedding
    
# FALLBACK (API-based, slower):
else:
    s_vec = await llm.get_embedding(student_answer)
```

#### Step 2: Compute Cosine Similarity
```python
from numpy import dot
from numpy.linalg import norm

# Cosine Similarity: (A · B) / (||A|| * ||B||)
cos_sim = dot(s_vec, correct_vector) / (norm(s_vec) * norm(correct_vector))

# Result: float in [0, 1] where:
# 1.0  = identical vectors (perfect match)
# 0.5  = moderate semantic overlap
# 0.0  = orthogonal (no overlap)

score_pct = int(cos_sim * 100)  # Display: 0-100%
```

#### Step 3: Apply Thresholds
```python
DECISION LOGIC (from _smart_grade_answer):

if cos_sim > 0.75:
    ✅ is_correct = TRUE
    feedback = f"Spot on! ({score_pct}%)"
    
elif 0.40 ≤ cos_sim ≤ 0.75:
    ⚠️ AMBIGUOUS ZONE → Fall through to Tier 2 (LLM Judge)
    
else:  # cos_sim < 0.40
    ❌ Likely FALSE, but still check LLM if answer is substantial
```

**Threshold Tuning** (A3 FIX): Lowered from 0.85 to reduce false negatives

---

### 5. TIER 2: LLM SEMANTIC GRADING (Smart Path)

**File**: `examiner.py` (_llm_grade_fallback method, lines 410-480)

Triggered when:
- Vector similarity is ambiguous (0.40-0.75), OR
- Correct answer text is available for semantic comparison

#### Grading Rubric (LLM Prompt)

The system sends this prompt to the LLM:

```
You are a friendly, encouraging C Programming Tutor grading a pop quiz.

**Quiz Context:**
- Correct Answer: "{correct_answer}"
- Student Answer: "{student_answer}"

**Task:**
Determine if the Student Answer is semantically correct.

**CRITICAL RULES:**
1. Detect Answers vs Questions
2. Lenient Grading for Short Answers
   - If student captures CORE TRUTH, mark TRUE
   - Example: If correct is "No, it skips the rest" 
     and student says "No" → TRUE
   - Do NOT penalize brevity

3. Conversational Tone (MANDATORY)
   - Speak directly: "You got this right..."
   - NOT: "The student is correct..."

4. Integer Division
   - For division: 7/2 = 3 (truncation required)
   - 3.5 = WRONG

**Classification:**
- TRUE: Conceptually correct (even if short)
- PARTIAL: Mostly right but missing key constraint
- FALSE: Factually wrong
- QUESTION: Explicit request for help

**Respond JSON:**
{
    "status": "TRUE" | "PARTIAL" | "FALSE" | "QUESTION",
    "feedback": "Conversational, direct feedback to student."
}
```

#### LLM Response Processing

```python
# LLM returns JSON
response = await llm.generate_response(prompt)

# Parse JSON
data = json.loads(response)
status = data['status'].upper()

# Map to binary
if status == "TRUE":
    is_correct = True
    feedback = f"✅ {data['feedback']}"
    
elif status == "PARTIAL":
    is_correct = False
    feedback = f"⚠️ {data['feedback']}"
    
elif status == "QUESTION":
    is_correct = False
    feedback = "🤔 Good question! Let me explain..."
    
else:  # "FALSE"
    is_correct = False
    feedback = f"❌ {data['feedback']}"

return {"is_correct": is_correct, "feedback": feedback}
```

---

### 6. CONFIDENCE CALIBRATION ACCURACY

**File**: `examiner.py` (_grade_quiz method, lines 230-310)

Once `is_correct` is determined, system analyzes **calibration**:

```python
confidence_score = int(match.group(r'\d+', student_confidence))

# SM-2 Quality Score (for spaced repetition)
if is_correct:
    sm2_quality = 5 if confidence_score >= 4 else 4
    # True Positive (TP): Confidence accurate
    
else:
    sm2_quality = 0 if confidence_score >= 4 else (1 if confidence_score <= 2 else 2)
    # False Positive (FP): Overconfident
    # True Negative (TN): Appropriately cautious

# Update spaced repetition (SM-2)
knowledge_manager.update_sm2(user_id, topic, sm2_quality)
```

**Calibration Scenarios**:

| Confidence | Actual Result | Type | Response |
|-----------|---------------|------|----------|
| Low (1-2) | ✅ Correct | True Negative | Self-efficacy boost: "You were more capable than you thought!" |
| High (4-5) | ✅ Correct | True Positive | Validation: "Your confidence was justified!" |
| Low (1-2) | ❌ Wrong | Vulnerability | Reassurance: "Self-awareness is a superpower!" |
| High (4-5) | ❌ Wrong | False Positive | **Misconception flagged** (high confidence + failure) |

---

## 7. KNOWLEDGE STATE UPDATES

**File**: `examiner.py`, `core/bkt_model.py`

Once binary signal is determined, system updates learning state:

### BKT Update (Bayesian Knowledge Tracing)
```python
from app.core.bkt_model import bkt

# Always 1 update per question (not 3 guesses counted as 1)
p_mastery = bkt.update(
    user_id=user_id,
    concept=topic,
    is_correct=is_correct,  # Binary: True/False
    evidence_type="quiz"
)

# Result: posterior probability ∈ [0, 1]
# P(L) ≥ 0.95 AND n_evidence ≥ 3 → Mastery certified
```

### Mastery Unlock (Certification Gate)
```python
if is_correct and bkt.is_mastered(user_id, topic):
    knowledge_manager.mark_concept_as_known(user_id, topic)
    logger.info(f"🏆 Mastery unlocked: '{topic}' P(L)={p_mastery:.3f}")
    
else:
    logger.info(f"📐 '{topic}' P(L)={p_mastery:.3f} (threshold 0.95 not yet reached)")
```

### Misconception Tracking
```python
if not is_correct and confidence_score >= 4:
    # High confidence + failure = misconception (not just a gap)
    knowledge_manager.store_misconception(
        user_id, topic, student_answer, correct_answer
    )
```

---

## 8. VERIFICATION QUIZ (3-Question Credibility Test)

**File**: `examiner.py` (lines 125-145)

For final mastery certification, system can require **3 consecutive correct answers**:

```python
is_verification_quiz = session_state.get("is_verification_quiz", False)
verification_q_num = session_state.get("verification_q_num", 1)  # 1, 2, or 3

# If Q1 and Q2 are correct, ask Q3
if is_verification_quiz and is_correct and verification_q_num < 3:
    # Fetch next question
    # Update state: verification_q_num += 1
    # Loop back to grading
    
# If all 3 correct + high confidence:
elif is_correct and verification_q_num == 3 and confidence_score >= 4:
    is_verified_credit = True  # 3 credibility points
```

---

## 9. COMPLETE GRADING FLOW (Diagram)

```
┌─────────────────────────────────────────────────────────┐
│ Student Submits Answer (e.g., "int x = 5;")            │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
            ┌────────────────────┐
            │ SURRENDER CHECK     │
            │ ("I don't know"?)   │
            └────┬───────────┬────┘
                 │Yes        │No
                 ▼           ▼
         Return  FALSE   TIER 1: Vector Match
                 │       ┌────────────────────┐
                 │       │ Encode both vectors│
                 │       │ Compute cos_sim    │
                 │       └────┬───────┬───────┘
                 │            │       │
              ┌──┴──┐   >75%  │  40-75% <40%
              │     │         │   │      │
              │     │         ▼   ▼      ▼
              │     │        TRUE │   TIER 2: LLM Judge
              │     │             │   ┌──────────────────┐
              │     │             └─→ │ LLM Semantic Grade│
              │     │                  │ vs Correct Answer│
              │     │                  └─────┬────────────┘
              │     │                        │
              │     │                ┌───┬───┴───┬───┐
              │     │                │   │   │   │   │
              │     │              TRUE PARTIAL FALSE QUESTION
              │     │                │   │   │   │   │
              └─────┼────────────────┴───┴───┴───┴───┘
                    │
                    ▼
        ┌──────────────────────────────┐
        │ CALIBRATION ACCURACY CHECK   │
        │ Compare confidence_score     │
        │ to is_correct result         │
        │                              │
        │ Update SM-2 quality score    │
        └──────────┬───────────────────┘
                   │
                   ▼
        ┌──────────────────────────────┐
        │ BKT UPDATE (Bayesian KT)     │
        │ Update P(L) posterior        │
        │ Evidence type: "quiz"        │
        └──────────┬───────────────────┘
                   │
                   ▼
        ┌──────────────────────────────┐
        │ CHECK MASTERY THRESHOLD      │
        │ P(L) ≥ 0.95 AND n ≥ 3?      │
        │                              │
        │ YES → Mark as KNOWN          │
        │ NO → Keep practicing         │
        └──────────┬───────────────────┘
                   │
                   ▼
        ┌──────────────────────────────┐
        │ CHECK FOR MISCONCEPTION      │
        │ (High confidence + FALSE)    │
        │ → Store for targeted fix     │
        └──────────┬───────────────────┘
                   │
                   ▼
        Return Grading Result + Feedback
        (is_correct, feedback, suggestions)
```

---

## 10. CODE FILES & Line References

| Component | File | Lines | Purpose |
|-----------|------|-------|---------|
| Quiz Flow | `agents/examiner.py` | 1-80 | Initiate quiz, ask confidence |
| Grade Quiz | `agents/examiner.py` | 200-310 | Main grading orchestrator |
| Smart Grade | `agents/examiner.py` | 360-410 | Vector similarity + LLM fallback |
| LLM Fallback | `agents/examiner.py` | 410-480 | Semantic rubric grading |
| Fast Grade | `agents/examiner.py` | 500-550 | Pure vector matching (legacy) |
| BKT Update | `core/bkt_model.py` | 150-200 | Update posterior probability |
| Misconception | `core/user_knowledge_manager.py` | 100-150 | Store high-confidence errors |
| Knowledge Tracking | `core/user_knowledge_manager.py` | 50-100 | Mark mastery, track concepts |

---

## 11. KEY THRESHOLDS & PARAMETERS

```python
# Vector Similarity Thresholds (examiner.py)
TIER1_HIGH_CONFIDENCE = 0.75   # Above this → auto-pass
TIER1_AMBIGUOUS_LOW = 0.40     # Below this → often fails
TIER1_AMBIGUOUS_HIGH = 0.75    # Between low/high → use LLM

# SM-2 Quality Score (examiner.py)
QUALITY_TRUE_POS = 5      # Correct, high confidence
QUALITY_TRUE_NEG = 4      # Correct, low confidence
QUALITY_FALSE_NEG = 2     # Wrong, medium confidence
QUALITY_FALSE_POS = 0     # Wrong, high confidence
QUALITY_UNKNOWN = 1       # Wrong, low confidence (less bad)

# BKT Thresholds (bkt_model.py)
THETA_CERTIFY = 0.95      # P(L) threshold for mastery
THETA_DECERTIFY = 0.75    # Hysteresis: fall below this → lose mastery
N_MIN = 3                  # Minimum evidence count per tier for certification
```

---

## 12. Special Cases & Edge Handling

### Short Answers
```python
# Example: Question "Is this correct?" → Answer "No"
# Vector similarity might be low (short text)
# BUT LLM rubric explicitly captures "CORE TRUTH"
# Result: Marked TRUE (not penalized for brevity)
```

### Integer Division
```python
# LLM prompt explicitly checks:
# For division: 7/2 = 3 (truncation required)
# 3.5 = WRONG (must be integer in C)
```

### Surrender Phrase Handling
```python
if student_answer.lower() in ["i don't know", "idk", "skip", "pass"]:
    return {"is_correct": False, "feedback": "That's okay! Better to be honest than guess."}
```

### Verification Quiz (Knowledge Credibility)
```python
# Instead of counting 1 answer as 3 evidence points,
# system asks 3 separate real questions
# Each question = 1 BKT update
# Only if ALL 3 correct + high confidence → verified credit
```

---

## 13. Data Flow: Student Input → Binary Output

```
INPUT: User submission (string)
   ↓
1. Encode to vector (local or API)
   ↓
2. Compare to correct_vector (cosine similarity)
   ↓
3a. If cos_sim > 0.75 → is_correct = TRUE
3b. If cos_sim < 0.40 → is_correct = FALSE
3c. If 0.40-0.75 → Use LLM semantic grading
   ↓
4. LLM returns JSON with status ∈ {TRUE, PARTIAL, FALSE, QUESTION}
   ↓
5. Map status to binary:
   - TRUE → is_correct = True
   - PARTIAL, FALSE, QUESTION → is_correct = False
   ↓
6. Extract confidence_score from prior state
   ↓
7. Update SM-2 (spaced repetition quality score)
   ↓
8. Update BKT posterior P(L)
   ↓
9. Check mastery threshold (P(L) ≥ 0.95 AND n ≥ 3)
   ↓
10. Store misconception if (is_correct=False AND confidence>=4)
   ↓
OUTPUT: {
    "is_correct": True/False,
    "feedback": "...",
    "suggestions": [...],
    "p_mastery": 0.XX,
    "mastered": True/False
}
```

---

## 14. Summary: Binary Signal Generation

The **binary 0/1 correct/incorrect signal** is produced by:

1. **Primary Method (75% of cases)**:
   - Vector similarity cosine matching
   - Threshold-based decision (>0.75 pass, <0.40 fail)

2. **Secondary Method (gray zone, 20% of cases)**:
   - LLM semantic rubric evaluation
   - Handles nuance, short answers, edge cases

3. **Hybrid Fallback (5% of cases)**:
   - If vector fails (encoding error, model unavailable)
   - If LLM judge needed but unavailable
   - Conservative: Default to FALSE (safer than false positive)

**Result**: Binary signal feeds directly into:
- BKT mastery tracking
- Misconception detection
- Calibration accuracy analysis
- Spaced repetition scheduling (SM-2)

