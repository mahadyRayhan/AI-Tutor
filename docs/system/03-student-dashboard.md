# Part 3 — The Student Dashboard

*SAGE system documentation. Written for any reader: the plain description comes
first in each section, the mechanism after it.*

---

## What the Student Dashboard is

**My Learning Journey** is the student's own view of what the system believes
about them. It answers four questions:

1. What am I working towards?
2. What do I actually know — and how does the system know that?
3. What has my instructor asked me to do?
4. What should I do next?

Two things make it unusual for a progress page.

**It shows its reasoning rather than a score.** Nothing here is a single grade.
Every number is broken into the evidence behind it, and the student can see how
far they are from the next step.

**It lets the student disagree with it.** If the system thinks a student knows
something they do not, they can say so, and it changes how future evidence is
weighed. This is the only student-facing control in SAGE that edits the learner
model directly.

Every panel on this page is private to the student. The data belongs to one
learner, and the server checks on every request that the person asking is either
that student or a teacher.

*Mechanism: `templates/student_dashboard.html` + `static/js/student_dashboard.js`.
Every per-student endpoint is guarded by `auth.require_self_or_teacher(username,
caller)` — the URL says which record, the signed session decides whether you may
see it.*

---

## 1. The goal

At the top, the student's current objective — *"Build a calculator"*, *"Master
pointers"* — with an **Edit Goal** button.

This is not decoration. The goal is used in two places outside this page: the
tutor loads it as context on every question, and the off-topic check in Chat
(Part 1) measures requests against it.

Saving a new goal does something visible: the system generates a **learning path**
to reach it, which appears directly below.

*Mechanism: `/api/v1/user/goal`, then `fetchLearningPath()`.*

---

## 2. The Learning Path

A horizontal chain of the concepts needed to reach the stated goal, in
prerequisite order. Each step carries one of three states:

| state | icon | meaning |
|---|---|---|
| **mastered** | ✓ | already certified |
| **next** | ⭐ | the one to work on now — clickable |
| **locked** | 🔒 | its prerequisites are not done yet |

Only the **next** step is clickable. Clicking it opens Chat and starts a session
on that concept, carrying the goal along so the tutor knows why the student is
there.

The chain is generated for the stated goal rather than being a fixed course
syllabus — two students with different goals get different paths through the same
material. The card hides itself entirely when no goal has been set.

*Mechanism: `/api/v1/analytics/learning_path/{username}`. The click sends
`[START_TOPIC] <concept> [GOAL] <goal>` into Chat — a tagged message so the tutor
treats it as a fresh topic start, not as a complex build request.*

---

## 3. Active Challenges

Work the instructor has assigned to this student specifically. The card is hidden
when there is nothing assigned. Two kinds appear here.

**Assigned material** — a reading, a code file, or an uploaded document. The
student opens it and marks it done.

**Assigned questions**, which move through three states:

| state | what the student sees |
|---|---|
| **Pending** | the question, and a box to write an answer |
| **Submitted** | their answer, read-only — *"Under Review"* |
| **Graded** | their answer plus the instructor's written feedback |

Pending items are sorted to the top.

This is the one part of the dashboard that is not automated: a human wrote the
question and a human writes the feedback. It exists so an instructor can reach one
student directly without going through the tutor.

*Mechanism: `/api/v1/assignments/student/{username}`, `/assignments/submit`,
`/assignments/material_done`.*

---

## 4. Interaction Habits

A donut chart of **how** the student uses the tutor, by question kind:

- **Concept** — "what is a pointer?"
- **Problem** — "how do I write a loop that…"
- **Review** — pasting code to be checked
- **Debug** — "why does this crash?"

Read this as a description of behaviour, not of ability. A student whose chart is
almost entirely *Debug* is working differently from one who is almost entirely
*Concept*, and that difference is worth noticing — but neither shape means they
know more.

*Mechanism: intent counts from the student's own history, returned by
`/api/v1/analytics/student/{username}`.*

---

## 5. Topic Proficiency Radar

Nine spokes, one per course topic, each showing verified mastery. A balanced shape
means even progress; spikes mean specialisation.

**The radar and the skill graph below it show the same numbers.** Both read from
the mastery model, so a topic always reports the same figure wherever it appears
on this page.

**A topic you have never worked on shows 0%.** The model always starts with a
rough guess about how likely a student is to already know a topic — it needs
somewhere to begin before it has seen any evidence. That guess is not progress, so
the dashboard does not display it. A topic only shows a number once the student
has actually done something on it.

---

## 6. Skill Progress — the knowledge graph

The centrepiece. Each of the nine topics is a node; arrows point from a topic to
the topics that build on it. Each node shows a percentage and a coloured ring.

| colour | meaning |
|---|---|
| 🟢 **green** | certified — verified across all three evidence tiers |
| 🔵 **blue** | in progress — some evidence gathered |
| 🟡 **amber** | head start — a mastered prerequisite is lending credit |
| ⚪ **grey** | not started |

A certified topic always draws a **full** ring, whatever its percentage says.
Certification is a finished state, so the ring shows it as complete rather than
part-filled.

**The amber flow.** When a certified topic feeds a dependent topic the student has
not touched, the arrow between them lights up amber and the dependent gets a head
start. Mastering *Variables* gives *Arrays* a running start, and the graph shows
you that happening rather than just adjusting a number quietly.

---

## 7. Clicking a node: the three tiers of evidence

Clicking any topic opens its evidence panel — the most important thing on the
page, because it is where "you know 40% of pointers" becomes an actual claim.

Mastery is not one number. It is **three separate kinds of evidence**, and a topic
is certified only when all three are:

| tier | what it means | how a student earns it |
|---|---|---|
| **Quiz** (declarative) | can you state it? | answer quiz questions, or press 🎯 Verify Mastery |
| **Micro-Challenge** (procedural) | can you write one line of it? | answer the tutor's "Your Turn" challenges |
| **Code Review** (applied) | can you use it in real code? | submit code and ask for a review |

For each tier the panel shows what the model currently believes, how much evidence
it rests on (**evidence 2/3**), and — the genuinely useful part — **roughly how
many correct answers in a row are still needed to certify it.**

A hint underneath names the weakest tier, says exactly which action feeds it, and
lists any prerequisite that is not certified yet.

A tier with no evidence yet says **"not started"** instead of showing a number —
the same rule the radar follows.

---

## 8. Disagreeing with the model

Each tier has a slider — and it **only moves down**.

A student who feels the tutor is overestimating them can lower a tier. The server
enforces this: an attempt to raise a value is rejected, not merely disabled in the
browser.

**Why downward-only.** Self-report is good evidence of doubt and poor evidence of
competence. A student who says "I know less than this" is telling you something
the system cannot otherwise observe, and acting on it costs nothing. A student who
says "I know more than this" may be right, but accepting it would make the whole
model self-certifying.

So there are two directions with two different rules:

- **Down** is *stated* — move the slider, save.
- **Up** is *earned* — press **🎯 Verify Mastery**, which opens Chat and starts a
  quiz on that topic.

**What a downgrade actually does.** It does not overwrite the model's belief. The
effective figure becomes a blend — roughly 60% what the evidence says, 40% what
the student says — and the adjustment feeds back into how future evidence on that
tier is weighed. Lowering a slider never certifies anyone and never removes a
certification already earned.

**Doubt flows downhill.** Because a topic's mastery lends a head start to the
topics built on it, lowering one topic can withdraw head starts from its
dependents. The graph re-draws after saving so the student sees that happen.

*Mechanism: `/api/v1/mastery/{username}/{concept}` for the panel;
`/api/v1/mastery/self-assess` to save. `record_self_assessment()` in
`srl_calibration.py` raises if `p_self >= p_bkt_current`. Effective probability is
`0.6·p_bkt + 0.4·p_self`.*

---

## 9. Confidence Check

> **Built, but switched off.** This panel does not currently appear for anyone.
> The feature is behind a flag that is off by default and is not set in the
> deployment, so the card stays hidden. It is described here because the code is
> complete and the panel would appear the moment the flag is turned on.
>
> That same flag also changes how the tutor replies — see the note at the end of
> this section.

For each topic the student has worked on, this compares **how confident they feel**
against **how they actually perform**:

- 💪 **Underrating** — "you know this better than you think"
- 🔎 **Overrating** — "worth a quick double-check"
- ✅ **Calibrated** — confidence matches performance

Knowing what you do and don't understand is a skill of its own, and this panel is
where a student can see how good they are at it. A student who keeps overrating
one topic has found a real problem — and a clear thing to do about it.

To reach a verdict the system looks at four things together: what the student
said about their own confidence, how they performed, how they behaved, and what
the mastery model believes. **A topic with too little information gets no verdict
at all** — the panel leaves it out rather than guessing. The whole panel is hidden
when the feature is turned off.

**The flag does more than show a panel.** When it is on, an "overrating" verdict
also tells the tutor to work a check question into its next reply, and an
"underrating" verdict tells it to affirm what the student has already demonstrated
and skip the basics. Turning this feature on therefore changes the tutoring
itself, not just what the dashboard displays.

*Mechanism: `/api/v1/mcn/calibration/{username}`, backed by `mcn_service`.
Gated on `MCN_ENABLED`, which defaults to false and is not set in the deployment.
The same flag gates `mcn_service.prompt_directive()`, consumed by `socratic.py`.*

---

## 10. Personal Tutor Assessment

A short written summary in four parts — **Current Focus**, **Strengths**, **Needs
Work**, and **Recommended** topics as chips.

Unlike everything above it, this is written by a language model reading the
student's recent history, not computed from the mastery model. It is the
softest-edged panel on the page and should be read as a prompt for reflection
rather than as a measurement.

*Mechanism: `/api/v1/analytics/report/{username}`, generated by the larger model
from the student's last 15 interactions. Loaded separately from everything else so
a slow generation never delays the charts.*

---

## How the page loads

Two speeds, deliberately:

- **Fast** — goal, charts, skill graph, assignments, learning path. These are
  database reads and appear immediately.
- **Slow** — the written assessment, which needs a language model. It shows
  *"Generating insights…"* and fills in when ready.

If the slow half fails, the fast half is unaffected — the page says the analysis
is unavailable and everything else still works.

---

## What the dashboard deliberately does not do

- **It does not show a single overall grade.** There is no one number, because the
  three tiers of evidence answer different questions and averaging them away would
  hide exactly what the student needs to act on.
- **It does not let a student raise their own mastery.** Up is earned, not stated.
- **It does not show untouched topics as partially known.** A topic the student
  has not worked on reads zero.
- **It does not show any other student's data.** No class ranking, no comparison,
  no cohort position. The server refuses per-student requests from anyone but that
  student or a teacher.
- **It does not invent a verdict from thin evidence.** Where the Confidence Check
  cannot judge a topic, it leaves the topic out rather than guessing.

---

## Known limitations

- **The written assessment is regenerated on every visit** from the last 15
  interactions, so it can shift between two page loads without the student having
  done anything. It also truncates each logged question to its first 50 characters,
  so its reading of "what you asked about" is coarse.
- **"Interaction Habits" is activity, not learning.** It is clearly labelled as
  such, but it sits beside two panels that *are* mastery, and the adjacency invites
  the wrong reading.
- **The learning path is model-generated.** The prerequisite ordering is not a
  fixed, reviewed curriculum, and an instructor does not approve it before the
  student follows it.
- **"≈ N correct answers in a row to certify"** is an estimate from the current
  model state, not a promise. It moves as evidence accumulates.
- **The Confidence Check is currently switched off**, so no student sees that
  panel at all.
