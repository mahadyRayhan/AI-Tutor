# SAGE Teacher Dashboard: Rebuild Plan

Target: an instructor opens this to answer three questions.
1. Who needs me today?
2. Where does my class stand?
3. What should I teach and prepare next?

Everything that does not serve one of those moves to an admin or system tab.

---

## PHASE 0: Correctness blockers

Do not build any new panel until these resolve. Every downstream panel inherits
whichever aggregation is wrong.

### B1. Composite vs conjunctive mastery
Student_14 displays 59% while tiers read quiz 91.2 / micro 5 / code 1.
59% matches no conjunctive aggregation. Certification is defined over
`min_k P_tilde^(k)`.

- Find every place a single mastery number is computed for display.
- Replace with `min_k` or remove the single number entirely.
- Colour by `min_k`. Student_14 is red, not green.
- Audit `_classify_mastery_level` and the modal endpoint for two separate paths.

### B2. Learner-facing mastery claim
The tutor greeting says "Last time, you successfully mastered Variables in C"
to a learner at code tier 1%. The badge fix corrected the instructor view only.
The greeting is a separate code path. Trace what feeds it. If it reads a
compensatory composite, the contradiction moved to the student rather than
being fixed.

### B3. Decay not running
- Class view: triage reports 7 decertified, forecast reports 0 lapsing and 12 certified.
- Student_14: last active 7/21, today is 8/10 (20 days) against a 14-day
  declarative half-life, quiz still reads 91.2%.

Both point at Eq. (18) not being applied on read, or `last_*_at` being NULL so
`delta_t` is never computed. Fix the timestamp population first, then verify
against the new trajectory chart: pick one dated certification and confirm the
curve bends toward the prior.

### B4. Posterior clamped at 1.0
Trajectory proof reports "code 21.7 to 100". A BKT posterior is asymptotic and
never reaches 1.0. If a genuine 1.0 is stored, no finite `delta_t` will ever
carry it below `theta_dec`, which explains B3. Check for a clamp or a rounding
in the write path, not just the display.

### B5. Two read paths for the same posterior
Triage says quiz 0.98, modal says 0.912, for the same learner and concept.
Collapse to one read path.

### B6. Evasion classifier false positives
Security ledger reports 587 evasion attempts across 290 of 506 learners.
Fifty-seven percent of a cohort did not attempt evasion. Sample 30 flagged
turns by hand before this number is shown to anyone or written into a paper.

### B7. The "General" bucket
Roughly 85% of queries land in General, which makes both bar charts
uninformative. This is an intent or topic-mapping problem upstream of the
dashboard. Fix the mapping or the charts cannot be salvaged.

### B8. One identity scheme
`Student_55`, `Student_01`, and `rt_059e93f7` are the same population in three
notations. Pick one display ID and use it everywhere, with the internal UUID
carried in the row payload for linking.

---

## PHASE 1: Information architecture

Five tabs. Sticky left nav. Default landing is NOW.

| Tab | Question | Panels |
|---|---|---|
| **NOW** | Who needs me today | Attention list, Roster |
| **CLASS** | Where does everyone stand | Topic standing, Distributions, Common mistakes |
| **NEXT** | What should I teach and prepare | Lecture effect, Readiness, What they're asking, Confusion pairs, Content gaps, What fades, Pacing |
| **SETUP** | Course administration | Course calendar, Users, Materials, Topic visibility, Pending reviews |
| **SYSTEM** | Is the model trustworthy | Model health, Security ledger |

This removes roughly 40% of the current single-page scroll from the teaching
view and separates the three tasks that currently interleave.

---

## PHASE 2: Vocabulary

No model vocabulary appears anywhere in NOW, CLASS, NEXT, or SETUP.
The three tiers stay, because they are the answer to "where are they weak,"
but they are renamed.

| Internal | Displayed |
|---|---|
| quiz tier (declarative) | Can explain it |
| micro tier (procedural) | Can complete code |
| code tier (applied) | Can write it from scratch |
| BKT posterior | (never shown as a raw number) |
| certified | Solid |
| decertified / below theta_dec | Faded |
| tier imbalance | Knows the idea, can't code it yet |
| thin evidence (N near N_min) | Not enough practice to judge |
| stalled | Stuck |
| evidence count N^(k) | practice attempts |
| Brier score | how often our predictions matched what happened |
| evasion attempt | off-topic or misuse attempt |
| curriculum redirect | tutor guided instead of answering |
| prerequisite gate block | waiting on an earlier topic |
| cognitive load estimate | working at the edge of what they can handle |

Rule: if a label needs the paper to understand it, rewrite the label.

---

## PHASE 3: Panel specifications

Every panel carries three things:
1. **Title** in teacher language.
2. **Subtitle**: one sentence stating the question it answers.
3. **"How to read this"** link: 2 to 3 sentences including what the thresholds
   mean and what the panel cannot tell you.

Plus an honest empty state. Never render a prior or a default as an
observation. "31.6%" repeated down ten rows must read "Not enough activity yet."

---

### NOW / Panel 1: Who needs me today
*Students the system thinks need a person, not more tutoring. Each row says why,
and suggests one thing to do.*

- One row per **reason cluster**, not per student. Seven identical Variables
  rows collapse to "7 students have faded on Variables and Types" with an
  expander and one batch action. This is what currently buries Student_14.
- Columns: Student(s) | What's happening | Why we think so | Do this
- "Why we think so" is a plain sentence with the numbers in it:
  "Can explain it (91%) but has never written it (1%, 0 attempts)."
- Action buttons wired and logged. Log the **flag state at press time**
  alongside the action, or the actionability analysis is unrecoverable.
- Reasons, in priority order: Faded, Stuck, Knows-but-can't-code,
  Not enough practice, Waiting on a prerequisite, Repeated same mistake.

### NOW / Panel 2: Roster
*Everyone in the class, searchable, sorted by who needs attention.*

Replaces Class Progress Monitor. One risk vocabulary only, matching Panel 1.
Columns: Student | Status | Solid on | Weak on | Last active | Open.
Delete the Critical/High/Watch scale. Two disagreeing rankings is worse than one.

### NOW / Student drill-down (modal)
Already partly built. Required additions:

- Three tier bars with **practice counts**, not one composite bar.
  "Can explain it 91% (12 attempts) · Can complete code 5% (1) ·
  Can write it 1% (0 attempts)". Zero attempts is not weakness, it is
  absence of evidence, and it changes the intervention from reteach to assess.
- **All topics**, not only the one with evidence. Pointers is this learner's
  stated goal, appears three times in history, and has no row.
- Why-flagged line at top, carried verbatim from the attention list.
- Trajectory chart (shipped) with the `theta_dec` line labelled "fade line".
- Session summaries (shipped) rather than "Last 2 Conversations".
  Transcript behind expand, with the access-log event finished.
- Action bar (shipped), plus the flag-state-at-press-time field.

---

### CLASS / Panel 1: Where the class stands
*What everyone has learned so far, split by whether they can explain it,
complete it, or write it from scratch.*

Replaces the scatter, Topic Popularity, and Class Struggle Areas as the headline.

| Topic | Can explain | Can complete | Can write | Practiced | Status |
|---|---|---|---|---|---|
| Variables | 78% | 45% | 12% | 41/50 | Needs a coding session |
| Pointers | 22% | 8% | 3% | 9/50 | Not started |

Sorted by attention needed. Status is derived and prescriptive:
"Needs a coding session" (strong left, weak right),
"Needs re-explaining" (weak across),
"Not started", "Solid", "Not enough data".

Keep the existing scatter directly below as an optional second view. It is the
best visual on the current page but it is a second look, not the headline.

### CLASS / Panel 2: How the class is spread
*Whether the class is together or split on each topic.*

Histogram per topic, not a mean. A bimodal class and a uniformly weak class
have the same average and opposite teaching responses (split the room versus
reteach). Every current panel reports a mean and hides this.

Subgroup comparison where IRB permits. This is where the equity commitment
becomes measurable rather than aspirational.

### CLASS / Panel 3: What the class is getting wrong
*The mistakes we're seeing most often, and how many students made each.*

Ranked error patterns from the misconception EMA. Per row: the mistake in plain
language, one concrete example, student count, trend arrow.
Action: "Build a warm-up from the top 3" generating a short drill set.

Blocked on the detector firing (`misconception_log` has 1 row). **Ship the empty
state now with the real copy in it**, so the panel exists the day data arrives.

---

### NEXT / Prerequisite: Course calendar

Ten minutes of instructor setup per semester, in SETUP.
Fields: date, topic(s) covered, activity type, assignment due.

Without this, every signal is floating in time and the dashboard can only
describe. With it, interaction data aligns to instruction. This single input
unlocks Panels 1, 2, and 7 below. Build it first.

### NEXT / Panel 1: Did my last class land?
*Whether mastery moved on the topics you taught, and how much.*

Per calendar entry: mastery before, +3 days, +7 days, per tier.
This is the only panel that evaluates the teaching rather than the students,
and it is the strongest research claim available from this dashboard.

### NEXT / Panel 2: Ready for what's coming
*Whether the class holds the prerequisites for your next topic.*

Forward walk of the Neo4j DAG from the next calendar entry.
"You plan to teach Pointers on Tuesday. 34% of the class holds the
prerequisites. The gap is Arrays, at the writing-it tier."
Uses only data you already have. Highest value-per-effort panel in NEXT.

### NEXT / Panel 3: What they're actually asking
*The questions students keep asking, in their own words.*

Clustered query corpus per topic, top question forms shown verbatim.
Twenty students asking "what does the star mean in a declaration versus in an
expression" is a lecture slide. No mastery percentage will ever say that.
Needs clustering over `turn_log`.

### NEXT / Panel 4: What they mix up
*Concepts students confuse with each other.*

Pairwise co-failure and directional substitution: arrays with pointers,
`=` with `==`, pass-by-value with pass-by-reference.
Prescribes contrastive teaching, which is a different action from reteaching
either concept alone. Computed from co-occurrence in error events.

### NEXT / Panel 5: Where your material has holes
*Topics students keep asking about that your materials don't cover well.*

Two signals joined: sessions where the tutor answered but mastery did not move,
and topics where retrieval returned weak or no supporting document.
That combination means students are asking, the material is thin, and the tutor
is improvising. Tells the instructor exactly what to write next.
Requires the retrieval log wired to outcomes. Unique to a RAG-grounded tutor.

### NEXT / Panel 6: What fades fastest
*Which topics the class is losing, and when to review them.*

Class-level decay ranking from the retention model, with a
"review before the midterm" list. Turns per-student decay into an exam-prep plan.
Depends on B3.

### NEXT / Panel 7: What actually took longer than planned
*How long topics really took, against what you allocated.*

Median attempts and elapsed time to first success per topic, against the
syllabus. Pacing evidence for the next offering of the course.

### NEXT / Weekly digest
Emailed and printable: three things that went well, three needing attention,
two suggested actions, one prepared warm-up quiz.
Instructors act away from the screen. A dashboard nobody opens produces nothing.

---

### SETUP
Course calendar (new), User management, Resource manager, Topic visibility,
Pending reviews. Move Topic visibility next to the topic standing table's
controls, since it is a curriculum decision.

Fix: Pending Reviews is currently showing test junk ("svf ehg") in what reads
as a live panel.

### SYSTEM
**Model health.** Front of panel is one sentence: "Our predictions matched what
actually happened X% of the time, across N observations." Reliability diagram
behind an expander with per-bin counts and error bars. Suppress the curve
entirely below ~500 scored predictions. Current state (Brier 0.515 on 85
predictions) is worse than a coin flip and within noise; it should render as
"not enough data yet," not as a chart.

**Security ledger.** Aggregate first, no learner names on the default view.
Two blocks with independent scales: security events and pedagogical redirects.
Currently one red full-width bar sits directly above greeting redirects,
visually equating them. Blocked on B6.

---

## PHASE 4: Global UX rules

- **Colour means one thing.** Red = act now. Amber = watch. Grey = no data.
  Currently red appears on Decertified chips, struggle bars, and the evasion
  bar, meaning three different things.
- **Five numbers per panel maximum.**
- **Sort by urgency**, never by ID or alphabetically.
- **Normalize per student.** Raw counts are why "General" dominates every chart.
- **Empty states everywhere**, in the panel's own voice.
- **Every number is clickable** through to the students behind it.
- **Sticky section nav.** The page should be navigable, not scrolled.
- **Print/export on CLASS and NEXT.** Instructors bring these to meetings.

---

## PHASE 5: Build order

**Sprint 1 (unblocks everything)**
1. B1 through B5. Correctness.
2. Tab structure and nav.
3. Vocabulary pass across all existing panels.
4. Flag-state-at-press-time added to the instructor action log.

**Sprint 2 (highest value per effort, no new data needed)**
5. Course calendar in SETUP.
6. CLASS / Where the class stands.
7. NOW / attention list clustered, actions wired.
8. NEXT / Ready for what's coming (DAG forward walk).
9. Student drill-down additions (tier bars with counts, all topics, why-flagged).

**Sprint 3 (needs the calendar plus a few weeks of live data)**
10. NEXT / Did my last class land?
11. NEXT / What actually took longer than planned.
12. CLASS / How the class is spread.
13. NEXT / What fades fastest (after B3 confirmed).

**Sprint 4 (needs new instrumentation)**
14. NEXT / What they're actually asking (query clustering).
15. NEXT / What they mix up (co-failure).
16. NEXT / Where your material has holes (retrieval log joined to outcomes).
17. CLASS / What the class is getting wrong (after detector fires).
18. Weekly digest.

**Deferred to SYSTEM, low priority**
19. Security ledger rework, after B6.
20. Model health rework, after enough predictions accumulate.

---

## PHASE 6: Validate before building Sprint 3

The most reliable finding in the teacher-dashboard literature is that designers
guess wrong about what teachers need. The NEXT tab in particular is my
inference, not a finding.

Run the instructor co-design sessions you already need for the dashboard paper
**before** Sprint 3. Put this panel list in front of 5 to 8 instructors, ask
them to rank and cut. The panels they reject are as publishable as the ones
they keep.

Open question for those sessions: does the tab split work, or do triage and
instructional planning want to be two separate tools? The split is a hypothesis
here, not a conclusion.

