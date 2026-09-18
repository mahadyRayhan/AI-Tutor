# Part 1 — Chat

*SAGE system documentation. Written for any reader: the plain description comes
first in each section, the mechanism after it.*

---

## What Chat is

Chat is the tutor itself: a conversation window where a student asks a question
and gets an answer shaped to what they are trying to do.

It is deliberately **not** a general chatbot. It answers questions about C
programming for this course, it knows what the student has covered, and it
frequently declines to simply hand over an answer — because for most of what a
student asks, being given the code teaches them less than being walked to it.

That last point is the design decision everything else follows from.

---

## What happens when a student sends a message

Every message passes through the same sequence before a reply comes back. A
student sees none of this; it takes a second or two.

### 1. The Sentinel — should this be answered at all?

The first stage decides whether the request should proceed. It checks, in order:

- is the student asking about **someone else's** data
- are they showing signs of **overload or distress**
- is the request **outside their stated learning goal**
- does it look like a request to be **given** an answer they should work out
- has the conversation **drifted off the subject**
- has the instructor **locked** this topic

If any of these fires, the tutor replies with a redirect instead of an answer —
and says which one and why. Most of these are not security checks. A student who
is overwhelmed and a student probing for a shortcut need very different
responses, so they are handled separately rather than lumped into one "blocked".

*Mechanism: `agents/sentinel.py`. Layered checks, first match wins.*

### 2. The Profiler — who is asking?

The tutor loads what it knows about this student: their goal, what they have
already been assessed on, their stated preferences, and a running read of their
emotional state across the session.

*Mechanism: `agents/profiler.py`, writing into the user's learning profile.*

### 3. The Router — what kind of question is this?

The message is classified into one of a small set of kinds. The classification
decides which specialist handles it:

| kind | example | handled by |
|---|---|---|
| **Concept** | "what is a pointer?" | explanation with sources |
| **Problem** | "how do I write a loop that…" | guided, step by step |
| **Debug** | "why does this crash?" | code reviewer |
| **Review** | pasted code, "is this right?" | code reviewer |
| **Complex problem** | "build me a calculator" | full guided plan |

Two safeguards sit on top of this, because a classifier that guesses wrong is
expensive:

- a "review" with no `;`, `{`, `}` or `=` anywhere in it is **not** code — it is
  downgraded to a plain explanation
- a "complex problem" that never says *build*, *create*, *project* or similar is
  downgraded too

Both exist because the heavier modes are slow and intrusive, and firing one on a
simple question is worse than answering plainly.

### 4. The specialist answers

Each kind of question goes to a different agent with different behaviour —
described in the next section.

### 5. The answer streams back

Text appears word by word rather than arriving in one block, so the student can
start reading immediately. A watchdog ends the turn if the model goes silent for
90 seconds, so a stalled reply can never leave the page stuck on "generating".

---

## The five ways the tutor responds

### Explaining a concept

Retrieves the relevant course material, answers from it, and shows which sources
it used. The student can see the passages the answer was built from rather than
taking it on trust.

### Guided practice — the distinctive one

When a student asks for something they should build themselves, the tutor does
not write it. It runs a structured sequence:

1. **Before any code**, it asks the student to say how they would approach the
   problem in their own words. Thinking first is the point; the tutor waits.
2. It then breaks the problem into **3–6 steps** and presents **one at a time**,
   numbered — "Step 2 of 4" — so the student always knows where they are.
3. The student writes the code for that step only.
4. The tutor checks it and either passes them forward or gives a hint.
5. Between steps it asks a **reasoning question** about the code just written
   ("why did we use `!=` instead of `<` here?") before introducing the next one.
6. At the end it asks the student to **explain the whole program twice** — once
   technically, once in plain language for someone who has never programmed.

If the student gets stuck three times on the same step, or shows signs of
giving up, the tutor stops advancing and offers a choice: partial code with a
heavy hint, or help drafting a message to a human TA. It does not silently keep
pushing.

**Why it is built this way.** A student who is handed working code has learned
that asking produces code. A student walked through four steps has written the
program. The step-by-step structure is also what makes the work *legible* — every
wrong turn is visible, which is what the teacher dashboard reads later.

When the problem is a course prelab, the tutor additionally receives the
instructor's list of allowed concepts and requirements, and will fail a step that
breaks them **even if the code produces the right output** — because a prelab
about loops is not satisfied by fifteen print statements.

### Reviewing code

Reads submitted code and responds to what is actually there — including edge
cases the student did not consider.

### Quizzing

Generates questions to test a concept, and the results feed the mastery model
that both dashboards read.

### Redirecting

When the Sentinel stops a request, the reply explains what happened rather than
refusing flatly.

---

## Controls on every answer

Under each reply:

| control | what it does |
|---|---|
| 👍 / 👎 | records whether the answer helped; 👎 opens a box for what was wrong |
| 👶 **Simplify** | re-answers the same question more simply |
| 🤿 **Deep Dive** | re-answers with more depth |
| 🔊 **Listen** | reads the answer aloud |

Simplify and Deep Dive are not cosmetic — they re-run the question with a
modified instruction, so the student gets a genuinely different explanation
rather than the same text reformatted. This matters for a mixed-ability class:
the same question can be answered at the right level without the student having
to know how to ask for it.

Feedback is stored, not just displayed — it is one of the signals the teacher
dashboard aggregates.

> **Note:** a ⚡ **Profile** link also appears on some answers. It opens a
> performance trace of how that turn was generated. It is a developer tool and
> is currently visible to students.

---

## Suggestion chips

Below many answers are one-tap suggestions — "I'm stuck", "Show me Pseudocode",
"Stop guided mode", "Try another prelab on this topic".

These are context-dependent: the tutor offers what is actually useful at that
moment. During guided practice the options are about the current step; at the end
of a prelab, one of them starts another prelab on the same lecture.

They exist because the hardest part of asking for help is knowing what to ask
for. A stuck student often cannot phrase their problem — a button they can press
is a lower barrier than a blank box.

---

## Sources & Context

A panel beside the conversation shows the course material each answer drew on.

This is an honesty mechanism. The student can check whether the answer came from
their actual course content or from the model's general knowledge, and the
teacher can see whether the material is being retrieved correctly.

---

## Session memory

The sidebar keeps past conversations, each titled from its first question.

Within a session the tutor remembers what is being worked on — an active guided
plan survives across turns, so a student can leave and come back mid-problem.
Starting a **New Chat** clears that working state.

---

## Settings that change how the tutor replies

Reached from the header:

- **Custom instructions** — free text the student writes ("I already know Python,
  compare things to Python"), followed on every response
- **Concise mode** — no filler
- **Accessibility options** — including a high-contrast mode for readability

These are applied to every free-text reply, not only to concept explanations.

---

## What Chat deliberately does not do

- It does not answer general questions. It is a C tutor, and says so.
- It does not hand over solutions to problems a student is meant to build.
- It does not claim mastery on a student's behalf — a solved problem is evidence,
  and only the mastery model decides whether that adds up to mastery.
- It does not silently give up on a stuck student; it escalates to a human.

---

## Known limitations *(honest notes, current as of Sept 2026)*

- The off-topic classifier has a high false-positive rate in deployment — course
  questions about prelabs and quizzes have been misread as off-topic.
- The ⚡ Profile developer link is visible to students.

*These are documented rather than hidden; both are being addressed.*
