# What's New in SAGE

*A plain-language brief — conference base → learner-modeling extension. No math.*

The first version of SAGE was a **safe** AI tutor — it kept students on-topic and out of
trouble. The extension turns it into a tutor that actually **understands how each student
learns**, and can prove it. Here's what changed, and why three of the changes are genuinely new.

---

## Where it started

The published version focused on **safety and access control**: it decided what a student was
allowed to ask, kept them on the curriculum, and blocked misuse. But it had no memory between
sessions, no picture of the individual learner, and treated every topic as simply **locked or
unlocked**. It knew the *question* — not the *person asking it*.

---

## Part 1 — The new capabilities

None of these existed in the first version. Together they replace a stateless, one-size-fits-all
tutor with one that remembers, models, and adapts to each student.

### 1. It remembers you
SAGE now keeps a short-term and long-term memory of each student — what they're working on, which
challenges they've skipped, the misconceptions they've shown, and how they like to be taught. It
no longer forgets you the moment a session ends.

### 2. It builds a picture of who you are as a learner
Instead of reading only the words of a question, SAGE reads the *learner* along five dimensions at
once: their **emotion** (is frustration rising?), their **mental workload and attention**, their
**self-awareness**, their **long-term goal**, and their **knowledge**. The upshot: two students
asking the exact same question can get two completely different answers.

### 3. It teaches with a team of specialists
Rather than one chatbot, SAGE runs several specialized agents: a **security** gatekeeper, a
**scaffolding** coach that refuses to just hand over code and instead walks you through it step by
step, an **examiner** that pops surprise warm-up quizzes on topics you're starting to forget, a
**code reviewer** that audits what you paste, and a **Socratic** explainer that draws diagrams.

### 4. It measures real skill, not a checkbox
Mastery is no longer "done / not done." SAGE tracks how well you can **explain** a concept,
**complete** a piece of code, and **write** code from scratch — as three separate skills. And that
skill **fades over time** if you don't practice, just like real memory, prompting a review before
you've quietly forgotten it.

### 5. It lets you correct it
If SAGE thinks you've mastered something but you feel like you were guessing, you can tell it so.
Sustained disagreement doesn't just adjust one number — it retrains how the system grades *you*
specifically, making it a stricter, more honest judge over time.

### 6. It adapts its whole teaching style to you
SAGE experiments with how it explains things — analogy, plain technical, or visual — and leans
into whatever *you* respond to best. A frustrated beginner gets a patient, no-code,
one-step-at-a-time tutor; a calm, proficient student gets a concise expert focused on edge cases.

### 7. It protects your focus and your integrity — not just the prompt
Beyond blocking obvious misuse, SAGE refuses to dump finished code (academic integrity), eases off
when a student is clearly overwhelmed (cognitive protection), and keeps sessions on the curriculum
instead of drifting off-topic.

---

## Part 2 — What's genuinely novel

Many of the features above are strong engineering. These three are the research contributions —
the things SAGE does that prior tutoring systems don't.

### Novelty 1 (Core) — Mastery that can't be faked
Most tutors judge mastery from a single overall score. The problem: a student who's great at
*explaining* a topic but can't actually *write* the code can still look "mastered." SAGE splits
every skill into three parts and requires the student to clear **all three independently** — being
strong in one area can never cover a weakness in another.

> **Evidence:** in a 2,000-student test, a single-score system wrongly passed **nearly all**
> under-prepared students. SAGE's three-part rule wrongly passed **none**.

**Why it's new:** it closes the "illusion of competence" loophole **by design** — not by tuning a
threshold, but structurally, so no combination of strengths can ever paper over a gap. In a
head-to-head against the closest existing multi-part model, SAGE was equal or safer on every kind
of student.

### Novelty 2 (Core) — A tutor the student helps calibrate
SAGE is the first to let a student's honest self-assessment **retrain the grading engine itself.**
When a student repeatedly admits "I was just guessing," the system permanently adjusts how it
scores that individual — and, separately, watches the whole class to keep the passing bar
consistent for everyone.

- **For the individual:** repeated honest self-corrections make the tutor a stricter, better-tuned
  judge of that one student.
- **For the class:** class-wide performance nudges the global passing bar, holding difficulty
  steady across the cohort.

**Why it's new:** the learner isn't just measured — they actively **train the model of
themselves.** Metacognition (thinking about your own thinking) becomes a signal the system learns
from, producing a more accurate picture than behavior alone ever could.

### Novelty 3 (Applied) — The same question, a different tutor
Adapting to a student's level is an old idea in tutoring. What's new is *the quality of the signal
doing the adapting.* Because SAGE's picture of mastery is multi-part and student-calibrated, its
adjustments are far sharper than tutors driven by a rough single score.

> Ask the same question twice, and a frustrated beginner and a confident expert get two entirely
> different tutors — because the tutor's instructions physically change with the student's mastery.

**Why it matters:** better scaffolding follows from a better measurement. The contribution isn't
"we adapt" — it's "we adapt on a signal accurate enough to make the adaptation actually work."

---

## The short version

SAGE grew from a tutor that **guards** students into one that **understands** them. It remembers
each learner, models them on five dimensions, and measures skill in a way that can't be faked. Its
three genuinely new ideas: mastery you must earn across three independent tiers, a grading engine
the student helps calibrate through honest self-reflection, and teaching that adapts on a signal
precise enough to make the adaptation land.
