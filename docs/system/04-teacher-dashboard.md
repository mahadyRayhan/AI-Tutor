# Part 4 — The Teacher Dashboard

*SAGE system documentation. Written for any reader: the plain description comes
first in each section, the mechanism after it.*

---

## What the Teacher Dashboard is

This is where the instructor works. It is the largest part of SAGE, and it is
built around one idea:

> **An instructor has limited time. The dashboard's job is to spend it well.**

So it does not present a wall of statistics and leave the instructor to find the
meaning. Nearly every panel takes the same shape:

**what is happening → why the system thinks so → what to do about it.**

That third part is the one that matters. Telling an instructor *"Student 14 is at
38% mastery"* leaves them with all the work still to do: 38% of what? Are they
weak at explaining the idea, or at writing the code? Does this student need the
topic taught again, a practice exercise, or a conversation?

Telling them *"Can explain pointers but can't write them yet — assign a hands-on
coding task"* has already answered those questions.

### Plain words in the teaching tabs, technical terms in one place

The tabs an instructor uses for teaching are written in everyday language. The
system's technical terms appear in a single tab, **Advanced**, and nowhere else.

For example, a student who learned a topic and has since forgotten it is described
as **"Faded"** in the teaching tabs. The Advanced tab describes the same student in
the statistical language of the model. Both are correct — but only one of them is
useful when you are preparing a lecture.

### Who can open it

Teacher role only, checked by the server on every request. The browser's stored
login is treated as a display cache, never as proof — the page asks the server who
it is talking to on load, and every individual data request re-checks the signed
session independently. Editing the browser's stored role does not open the
dashboard.

*Mechanism: `templates/teacher_dashboard.html`. `verifyTeacherSession()` on load;
every endpoint carries `Depends(verify_teacher)`, which reads the role from the
signed session cookie rather than any client-supplied value.*

---

## The six tabs

| tab | the question it answers |
|---|---|
| **Overview** | Who needs me today? |
| **Students** | Where does the class stand? |
| **Planning** | What should I prepare next? |
| **Content** | What are students watching and submitting? |
| **Course setup** | Who is enrolled, and what can they see? |
| **Advanced** | Is the system itself behaving? |

A colour key runs across all of them: **needs action**, **watch soon**,
**information**, **on track**.

---

## Overview — who needs attention

### The triage table

The landing view. One row per student, and each row names **a single specific
problem** rather than a blended risk score.

| column | what it holds |
|---|---|
| **Students** | who (grouped, so one issue is one row) |
| **What's happening** | the named problem |
| **Why we think so** | the evidence, in one line |
| **Do this** | the suggested action |

Four problems can be named, in order of urgency:

| label | what it means | suggested action |
|---|---|---|
| **Faded** | was solid on a topic; has slipped from not practising | a quick refresher |
| **Knows it, can't code it yet** | can explain it but can't write it | a hands-on coding task |
| **Stuck** | many attempts, still not getting it | a 1:1 check-in, a different explanation |
| **Not enough practice** | marked solid on very thin evidence | one more check to confirm |

Each student is flagged for **one** thing — the most urgent one. A student who is
both stuck on loops and thin on arrays appears once, under the more serious of the
two. The instructor reads a list of problems, not a list of students each carrying
a paragraph.

**"Knows it, can't code it yet" is the most useful row here**, because it is the
failure a human marker is least likely to catch. A student who can define a
pointer and cannot use one looks fine in discussion and fails on the assignment.
The system sees the gap directly because it tracks explaining and writing as
separate evidence.

*Mechanism: `/api/v1/analytics/teacher/triage`. Thresholds come from the mastery
model itself, not from hand-picked dashboard constants. Certifications are
reconciled before the table is built, so a student who has decayed below threshold
while idle is reported as faded rather than still-certified.*

### Pending reviews

Work waiting on the instructor — student answers to challenges that have been
submitted and not yet given feedback.

---

## Students — where the class stands

### Class capability by topic

One row per topic, split into the **three things a student can actually do**:

| | |
|---|---|
| **Can explain it** | states the concept correctly |
| **Can complete code** | fills in a missing line |
| **Can write it from scratch** | produces working code |

Each row carries a verdict in teaching language — **Needs re-explaining**, **Needs
a coding session**, **In progress**, **Solid**, or **Not enough data** — and the
rows are sorted so what needs the instructor most sits at the top. Expanding a row
lists the students behind it, weakest first.

The verdict follows the shape of the split rather than an average. Strong on
explaining and weak on writing produces *"Needs a coding session"*; weak across the
board produces *"Needs re-explaining"*. These are different problems with different
fixes, and an averaged score would hide both.

**A topic too few students have touched reports "Not enough data"** rather than a
confident-looking average over three people.

*Mechanism: `/api/v1/analytics/teacher/class_standing`, built from the mastery
model only. Figures are decay-adjusted, so they describe retained knowledge rather
than a peak reached weeks ago.*

### Student roster

The full class in one sortable, searchable table: risk level and its reason,
mastery, active time, growth area, last seen. Sorted by risk by default, with a
period filter (24 hours to all time). Clicking any student opens their detail view.

### Students showing frustration

Who is struggling emotionally rather than academically, drawn from the tutor's
running read of each conversation.

This is reported as **a rate, not a count** — the share of a student's turns
showing confusion or frustration. A student who asks eighty questions should not
be flagged for volume alone.

### Learning path movement

Whether students follow the recommended order, jump ahead, revisit earlier
material, or wander — and which concept they sit on longest. The stall list is the
useful part: a concept many students stop on is a teaching problem, not a student
problem.

### What students are asking

Two charts: the **mix of question types** across the class (concept, problem,
debug, review, quiz) and the **topics** those questions are about.

Greetings and security-related messages are left out. There are a lot of them, and
including them would bury the questions that actually tell you something. They are
counted in the Advanced tab instead.

### The student detail view

Opening a student shows their mastery profile, their progress over time, their
**last two conversations verbatim**, and a box to send them a challenge directly —
which lands in that student's Active Challenges (Part 3).

Reading the actual conversation is often worth more than any metric on the page.
It is the difference between "struggling with pointers" and seeing precisely where
they went wrong.

---

## Planning — what to prepare next

This tab converts the picture of the class into the instructor's to-do list.

### What to prepare next

Per topic: **one** prep action, **the group of students it is for**, and the
matching reading or code from the course resources — assignable to that whole
group in one click.

| action | when |
|---|---|
| **Prepare a re-explainer** | the class is weak across the board |
| **Prepare a coding / lab session** | they can explain it but not write it |
| **Assign first exposure before you lecture it** | barely touched so far |
| **Reinforce — targeted practice** | partly there |
| **On track — no prep needed** | nothing required |

Rows are ranked by **how many students the preparation would help**, so preparation
time goes where it reaches the most people.

*Mechanism: `/api/v1/analytics/teacher/prep_list`, sharing the same underlying
matrix as class standing and readiness — so the three panels cannot disagree.*

### Assignment follow-through

What has been assigned, and how many students have actually opened it. Push again,
or move on.

### Readiness for the next topic

The instructor picks the topic they plan to teach. The system walks its
prerequisites and reports whether the class holds them — and precisely where the
gap is.

This is the panel that answers *"can I teach arrays on Thursday, or do I need
another day on loops first?"* before the lecture rather than during it.

### Mastery likely to fade soon

Certified students projected to drop below the mastery threshold, and roughly
when — calculated from each student's own forgetting curve.

This is the other half of the **Faded** row in the triage table. Faded means the
student has already forgotten it. This panel lists students who are *about* to —
so you can schedule a refresher **before** they lose it rather than after.

*Mechanism: `/api/v1/analytics/teacher/decert_forecast`. Deterministic projection
from stored decay rates and last-practised times — it introduces no new data, it
just reads the existing curve forward.*

---

## Content — lectures and submitted work

### Lecture engagement

Per lecture: who watched, **how much they actually attended**, and how they did on
the in-video checkpoints.

The distinction between elapsed and attended time matters here — the figures come
from the genuine-watch-time measurement described in Part 2, so skipping to the
end does not register as having watched.

The checkpoint breakdown is the other half: a question most of the class gets
wrong is either a badly generated question or a genuinely unclear passage of the
lecture, and both are worth knowing.

### Prelab submissions

Where guided practice work arrives. **Students upload nothing and submit nothing** —
finishing the last step of a prelab in guided mode files the run automatically.

Each submission shows the student, the lecture, the machine grade, any integrity
flags, and — on opening a row — **the entire conversation that produced the code**.

The automatic grade comes from actually compiling the student's code, running it,
and comparing the output to what the handout expects. It reports two kinds of
failure separately:

- **The wording is different** — the program works, but it prints its message
  differently from the handout
- **The values are wrong** — the program produces the wrong answer

The first is usually not a real mistake; the second always is. The instructor can
override the grade, add a note, or run the check again.

Integrity flags mark runs that look unusual — a student who cleared every step
with no failures at all, or who replied faster than reading the questions would
allow. **These are flags for a human to look at, not accusations**, and the full
transcript is one click away so that the instructor can judge for themselves.

### Managing recordings

Uploading a lecture (large files are sent in chunks so a 140 MB recording does not
time out), seeing which uploads are **not yet attached to a chapter** — students
cannot see those — and the chapter list itself, where a slot with no recording
shows as *planned* rather than missing.

---

## Course setup

**User management** — the enrolled roster and account administration.

**Resource manager** — the readings, code samples and documents the tutor can draw
on, with per-type file rules (`.c` for code; `.md`, `.pdf`, `.docx`, `.pptx` for
concept material).

**Topic visibility** — which topics the class can currently see. This is the
instructor's lock: a topic switched off here is refused by the tutor, and it is
the "instructor has locked this topic" check described in Part 1.

---

## Advanced — is the system itself behaving?

Two panels, both about SAGE rather than about students. This tab is deliberately
last and deliberately separate.

### Model health

**The mastery model grading itself.** It records what it predicted before each
answer and compares that against what actually happened.

The headline is a reliability figure with a plain-language label. Underneath, the
predictions are split into three groups, which is where the real information is:

| group | meaning |
|---|---|
| **all** | every prediction |
| **evidence** | the model had seen this student on this topic before |
| **prior** | first encounter — the model was guessing from its starting assumptions |

**Look at the three groups separately, not just the overall number.** The overall
number can be poor for two completely different reasons:

- The model is wrong about students it already knows. That is a serious problem.
- The model is right about students it knows, and only its opening guesses are
  wrong. That is a much smaller problem, and an easier one to fix.

A single combined score looks the same in both cases. Splitting it apart shows you
which one you are actually dealing with.

The calibration curve and the underlying scores sit behind a **Developer detail**
collapse, because they are for whoever maintains the model, not for teaching.

*Mechanism: `/api/v1/analytics/model_health`, reading the prediction log.*

### Security ledger

Turns that were gated, across the whole class — and critically, it **separates two
things that are not alike**:

- **Evasion** — genuine attempts to get around the system
- **Curriculum redirects** — the tutor declining to hand over an answer, which is
  it working as designed

**No student is named.** The panel shows totals for the whole class and nothing
more. Opening with a list of names under a heading about misbehaviour would put
students under suspicion before anyone had looked at what actually happened — and
in practice almost all of these are simply the tutor doing its job.

---

## How students are labelled

Internally, students are anonymised as `Student_01`, `Student_02`. In the tables
the instructor actually sees, rows are **labelled by email address**, because an
anonymous ID cannot be matched against a class list and an instructor who cannot
identify the student cannot act.

Actions are keyed on the underlying account throughout, so the label can change
without anything breaking. If the email lookup fails, tables still render with the
anonymised ID rather than showing nothing.

---

## What the dashboard deliberately does not do

- **It does not report a single risk score.** Every flag names a specific
  mechanism, because "at risk" is not actionable and "can explain it but can't
  write it" is.
- **It does not name students in the security panel.** Aggregate only.
- **It does not average away the three kinds of evidence.** They answer different
  questions and call for different responses.
- **It does not report confident numbers from thin data.** Topics with too few
  students read "Not enough data".
- **It does not treat the browser's stored login as proof of anything.** Every
  request re-checks the signed session on the server.
- **It does not accuse anyone.** Integrity flags point out unusual work and show
  the instructor the full conversation so they can judge it themselves.

---

## Known limitations

- **Integrity flags are heuristics.** "Cleared every step first time" and "replied
  very fast" both have innocent explanations — a strong student, or one returning
  to a problem they had already worked out on paper. They are correctly presented
  as prompts to look, but they will produce false positives.
- **The frustration read comes from an automated judgement** of each conversation's
  emotional tone. It is a useful pointer to who to talk to, not a measurement.
- **Checkpoint results are only as good as the questions.** If the whole class
  fails one checkpoint, that might mean they did not understand the lecture — or it
  might just be a badly written question. The panel cannot tell you which.
- **Model health needs volume to mean anything.** Early in a term, or on a
  little-used topic, the reliability figure moves sharply on small numbers.
- **Some panels only count students who have used the tutor.** A student who has
  never used it is left out of the class averages rather than counted as a zero, so
  those averages look slightly better than the class as a whole. The roster and user
  management still show everyone.
