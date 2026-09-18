# Part 5 — Self-Regulated Learning

*Part B of the SAGE documentation: the systems underneath. These are not screens a
student opens — they run inside the features described in Parts 1–4.*

---

## What this is about

Self-regulated learning is a simple idea with a long name:

> **Good learners manage their own learning.** They decide what they are trying to
> achieve, notice while they are working whether it is going well, and afterwards
> work out what they actually understood.

Weaker learners usually skip all three. They start without a goal, do not notice
when they have stopped understanding, and finish without checking what stuck.

The important part is that these are **habits, not talents**. They can be taught,
and mostly they are taught by being asked the right question at the right moment.

SAGE builds those questions into the places where a student would naturally skip
them. That is the whole design: the system asks, every time, whether or not the
student would have thought to.

### The three moments

Everything in this part fits one of three moments:

| moment | the question | where it happens in SAGE |
|---|---|---|
| **Before** | What am I trying to do? | goal, video intention, confidence rating |
| **During** | Is this working? | checkpoints, reasoning questions, mood tracking |
| **After** | What did I actually learn? | takeaway, two-part reflection, self-assessment |

---

## Before: setting up

### The goal

The student writes what they are working towards on their dashboard. That one
sentence is used in three places: the tutor reads it as context on every question,
it generates the learning path, and it is what the off-topic check measures
requests against.

### The intention before a video

The first time a student presses play, the video pauses and asks what they want to
get out of it. They can skip it.

It is skippable on purpose. A question a student is forced to answer produces
whatever gets the box closed. The point is to make them pause for five seconds
before watching, and a student who skips it has still been asked.

### Rating confidence before answering

Before a quiz question and before an in-video checkpoint, the student says how
confident they are on a 1–5 scale — **before** they see whether they were right.

This is the single most useful measurement in the whole SRL layer, because it
separates two things that look identical in a gradebook:

| the student was | and they | which means |
|---|---|---|
| confident | got it wrong | a real gap — they believe something untrue |
| unsure | got it wrong | a careless slip, or an honest guess |
| confident | got it right | solid knowledge |
| unsure | got it right | they know more than they think |

A wrong answer given confidently is a much bigger problem than a wrong answer
given hesitantly, and no score that records only right-or-wrong can tell them
apart.

**The system acts on this.** The scheduler that decides when a topic comes back
for review treats a confident wrong answer as the worst possible outcome — worse
than an unsure wrong answer — so that misplaced certainty is revisited soonest.

*Mechanism: confidence is recorded in `jol_log` (a judgment of learning) alongside
the outcome. `examiner.py` converts the pair into a review-scheduling score:
correct and confident scores highest, wrong and confident scores zero.*

---

## During: noticing while it happens

### Checkpoints

The in-video questions described in Part 2. Their purpose is not assessment — it
is interruption. Watching a lecture is passive unless something stops it, and a
student who has just been made to answer a question about the last eight minutes
knows something about whether they were following.

### The reasoning question between steps

In guided practice, the tutor does not move from one step to the next by
announcing the next step. It asks the student a question about why the next piece
is needed.

This keeps the student doing the thinking. A student who can follow eight
instructions has demonstrated that they can follow instructions.

### Watching how the student feels

The system reads the emotional tone of each message — confusion, frustration,
boredom, or absorption — using a mixture of a trained language model and a list of
plain phrases.

This exists because **frustration and confusion need opposite responses.** A
confused student needs a different explanation. A frustrated student who
understands perfectly well needs the explanation to stop. Treating both as "having
difficulty" gets one of them wrong every time.

### Noticing when a student gives up

When a student answers a quiz question with "I don't know", "skip", or "no idea",
the system records that as **giving up**, which is different from getting it wrong.

A wrong answer is an attempt. Giving up is a decision about one's own learning, and
a student who gives up repeatedly has a different problem from one who keeps trying
and keeps missing.

---

## After: working out what was learned

### The takeaway after a video

At the end of a lecture, one question: what was your single most important
takeaway?

### The two-part reflection after guided practice

When a student finishes a prelab, they are asked **two** questions rather than one,
and the second is the one that matters.

**First, the technical question:** how do the pieces of this program work together?

**Then the harder one:** explain what you built and why, to someone who has never
programmed — no jargon, no "loop", no "variable".

The two questions separate remembering from understanding. A student can answer
the first from the step titles alone: *"a while loop with an if/else inside"* is a
description of the shape of the code, and reciting it proves nothing. But you
cannot explain **why** that was the right shape, in plain words, without having
understood what the program is actually doing.

If the student deflects on the first question — *"I don't know how to summarise
it"* — the tutor does not push. It acknowledges that summarising is genuinely the
hard part and offers the plain-language question instead, as a different way in.

Both answers are kept, because they say different things about the same student.

### Adjusting the system's estimate

The downward-only sliders on the student dashboard, described in Part 3. This is
the student's channel for saying *"you think I know this, and I don't."*

---

## The calibration loop

This is the part of SAGE that is genuinely unusual, and it is worth explaining on
its own.

Most tutoring systems measure what a student knows. SAGE also measures **how well
the student knows what they know** — and then feeds that back into the model.

It works in two layers.

### Layer 1: blending

When a student lowers a slider, the system does not overwrite its own estimate. It
holds both, and the figure it displays is a mix: roughly **60% what the evidence
says, 40% what the student says.**

The blended figure changes what the student sees and how the tutor pitches its
answers. **It never certifies anyone.** Certification always reads the evidence
alone. A student cannot talk their way to mastery, and cannot talk their way out
of a mastery they have earned.

### Layer 2: learning that a student is a guesser

This is the interesting one.

If a student **repeatedly** tells the system it is overestimating them on the same
kind of task, the system eventually concludes something specific: that this
student's correct answers on that kind of task contain more lucky guesses than it
had assumed. So it adjusts how much credit their future correct answers earn.

In plain terms: a student who keeps saying *"I got that right, but I guessed"* is
believed — not about that one answer, but about the pattern. The model raises its
estimate of how often they guess, and future correct answers count for a little
less until backed by more evidence.

Three safeguards keep this from being exploitable:

- It takes **sustained** disagreement, not one adjustment
- The guess rate can only move **up**, never down — a student cannot make their
  answers count for more by claiming confidence
- Each kind of task has a **ceiling**, so the adjustment cannot run away

*Mechanism: `srl_calibration.py`. Layer 1 is `P_eff = 0.6·P_BKT + 0.4·P_self`.
Layer 2 adjusts the per-student guess parameter after at least 3 adjustments with
a consistent direction, bounded per tier (quiz 0.40, micro 0.25, code 0.15).*

---

## What the system works out about a learner

From the confidence ratings and slider adjustments, SAGE derives a few things that
are not directly observable:

| measure | what it means |
|---|---|
| **Gap vs slip** | of the answers this student got wrong, how many were confident errors (real gaps) rather than hesitant ones (slips) |
| **Calibration score** | across all their answers, how closely their confidence has tracked their actual performance |
| **Becoming automatic** | whether they are answering correctly *faster* over time — a sign that a skill has stopped requiring effort |
| **Help-seeking** | how often they give up, and whether they do it at reasonable moments |

---

## What is built but switched off

**The Confidence Check panel** described in Part 3. It would show each student
where they are overrating or underrating themselves, and it would tell the tutor to
slip in a check question for an overconfident student or offer reassurance to an
underconfident one.

It is complete, and it is off. Nothing displays it and nothing acts on it. The one
setting that turns it on changes both the panel *and* the tutor's behaviour, so
turning it on is a teaching decision rather than a display decision.

---

## What this layer deliberately does not do

- **It does not let students certify themselves.** Confidence and self-assessment
  change what is displayed and how the tutor responds. They never grant mastery.
- **It does not force reflection.** The intention prompt can be skipped. A forced
  answer is not a reflection.
- **It does not treat all wrong answers alike.** Confident-wrong, unsure-wrong,
  and gave-up are recorded as three different things.
- **It does not treat frustration as confusion.** They get different responses.
- **It does not punish a student for admitting doubt.** Saying "I guessed" lowers
  how much a *future* correct answer counts — it never removes credit already
  earned.

---

## Known limitations

- **The written reflections are never read back.** Students write an intention
  before each video and a takeaway after it, and those answers are stored and then
  go nowhere. No teacher panel shows them, the student never sees their own again,
  and nothing feeds them into the model. Asking the question is doing the
  pedagogical work either way, but the data is currently collected for nothing.
- **The reflection box can be submitted empty.** Clicking Done with nothing typed
  closes it and stores nothing.
- **The Confidence Check is off**, so the most visible part of this layer is not
  reaching anyone.
- **Confidence ratings are self-reported on a 1–5 scale**, and students differ in
  how they use scales. The calibration figures are comparable for one student over
  time, and shakier between students.
- **Emotional tone is inferred from text**, which is a reasonable signal and not a
  measurement. It is a pointer to who to talk to, not a diagnosis.
