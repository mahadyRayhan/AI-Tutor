# Part 2 — Classroom

*SAGE system documentation. Written for any reader: the plain description comes
first in each section, the mechanism after it.*

---

## What the Classroom is

The Classroom is where a student watches a lecture video. It is the second of
SAGE's two views — a student switches between **Chat** and **Classroom** from the
top navigation.

It is not a video player with a chatbot bolted on. The video is the spine of a
structured session: the student states a goal before it starts, answers questions
they cannot skip while it plays, asks the tutor anything at any point, writes down
a takeaway at the end, and is handed a programming problem to go and solve.

The design assumption is that **watching a lecture is a passive act unless
something interrupts it.** Everything in this part exists to interrupt it
productively.

---

## Picking a lecture

The student sees a grid of lecture cards, grouped by chapter. Each card shows the
video's title, its number, a topic icon, its length, and a **✓ Ready** badge when
the lecture has been transcribed and the tutor can answer questions about it.

Only lectures the instructor has enabled appear here. A lecture that exists on the
server but has not been assigned to a topic the class is working on is not shown.

*Mechanism: `/api/v1/video/list`, filtered by teacher-enabled topics. Cards built
by `buildVideoCard()` in `chat.js`.*

### What loads when a lecture is opened

Four things are fetched before the first frame plays, because the player needs all
of them to behave correctly:

| fetched | used for |
|---|---|
| **checkpoints** | the in-video questions, without their answers |
| **prelab problems** | the challenge offered at the end |
| **progress** | where this student left off, and what they already answered |
| **transcript** | the synced side panel |

The progress fetch and the video's own loading race each other. Whichever
finishes second applies the resume position; the operation is written to be safe
to run twice.

*Mechanism: `crLoadCheckpoints()` → `crLoadProgress()`; `crApplyResume()` is
guarded by `crResumeApplied`.*

---

## Before the video starts: the learning goal

The first time a student presses play, the video pauses and asks:

> **🎯 Before you watch**
> What do you want to learn or understand from this video?

They can type an answer or press **Skip**. Either way the video then plays.

This is the *forethought* stage of self-regulated learning: naming a goal before
starting measurably improves what a learner retains, and it gives the system a
statement of intent to compare against what the student actually does. It is
skippable on purpose — a mandatory reflection prompt produces compliance text,
not reflection.

*Mechanism: `onClassroomPlay()` gates on `crIntentionGiven`; the response is
stored via `/api/v1/video/reflection` with `phase: "intention"`.*

---

## While the video plays

### Checkpoints — questions the student cannot skip

At planned points in the lecture, the video stops and a multiple-choice question
appears. It cannot be dismissed. The student must answer to continue.

**Where they fall.** The video is divided into equal segments and a checkpoint sits
at the end of each one, with the last landing at the end of the lecture. The number
of segments scales with length — roughly one every 8½ minutes, never fewer than one
and never more than eight. A 17-minute lecture gets two: one at the midpoint, one at
the end.

**What they ask.** Each question is generated from *its own segment* of the
transcript, so it tests the part just watched rather than the lecture as a whole.
Questions are generated once, the first time anyone opens the lecture, and cached
from then on.

**Answering.** Before choosing, the student rates how confident they are on a 1–5
scale. A wrong answer says *"Not quite — review and try again"* and lets them retry.
A correct one shows the explanation and a **Continue ▶** button that resumes
playback.

**The options are shuffled every time they are shown**, including on a retry. This
is not cosmetic. The generated question bank is badly position-biased — in the
current bank 64% of correct answers sit in the first slot and none in the fourth —
so a student who always picks A would score far above chance, and the mastery model
assumes a 20% guess rate. Shuffling makes the real guess rate match the modelled
one. Reshuffling on a retry means a second attempt is answered by reasoning again,
rather than by remembering that the answer was not B.

**Skipping is not possible by scrubbing.** Dragging the playhead past a checkpoint
triggers it anyway — any unanswered checkpoint before the seek target is enforced
before playback continues.

*Mechanism: `_plan_checkpoint_times()` and `generate_checkpoints()` in
`video_service.py`; `CHECKPOINT_SPACING_SEC = 510`, `CHECKPOINT_MIN = 1`,
`CHECKPOINT_MAX = 8`. Enforcement in `crEnforceCheckpoints()`, called from both
`ontimeupdate` and `onseeking`. Shuffle in `crShuffleOrder()`.*

### The answer is graded on the server

The list of checkpoints sent to the browser **does not include which option is
correct**. The student's choice is posted back and graded server-side.

A correct or incorrect answer feeds the mastery model as *quiz-tier* evidence
against the concept the question tests. Confidence, time taken, and number of
attempts are all recorded alongside it.

*Mechanism: `/api/v1/video/checkpoints/{file}` selects `question, options, concept`
— never `correct_index`. Grading and BKT update in
`/api/v1/video/checkpoint/answer`.*

### Asking the tutor a question

The student can pause at any moment and ask something in the panel beside the
video. Asking a question pauses the video automatically.

What makes this different from ordinary Chat is the context rule:

> **The tutor answers using the transcript up to the exact second the student
> paused — and no further.**

Ask at 4:12 about something the instructor covers at 11:00, and the tutor does not
spoil it. It says the topic has not been covered yet.

Two other things happen around the answer:

- **Course materials as a second source.** If the transcript alone does not answer
  the question, the system retrieves passages from the course material for that
  lecture's topic and says clearly when it is drawing on them rather than on the
  lecture.
- **Timestamp pointers.** After answering, the system searches the rest of the
  transcript for the question's keywords and adds up to two pointers:
  *"⏮️ Already covered: this was discussed at 03:10–03:40"* and *"⏭️ Coming up
  ahead: this is also covered at 11:02–11:31."* A pointer only appears when at
  least two of the question's keywords match — a weak match produces nothing
  rather than a guess.

Questions like *"what is this video about?"* skip the whole pipeline and return a
cached one-sentence summary of the lecture instead.

Every Q&A pair is saved into the student's chat history under a session named
**📹 <lecture title>**, so it is there when they come back.

*Mechanism: `/api/v1/video/chat` — `get_transcript_until()` for the primary
context, topic-filtered vector search (top 4, relevance > 0.25) for the
supplement, `find_timestamp_hints()` for the pointers. Saved via
`/api/v1/history/save-classroom`.*

### The transcript panel

A collapsible panel beside the video shows the full transcript with timestamps.
The line currently being spoken is highlighted and scrolls itself into view;
clicking any line jumps the video to that moment. If the student scrolls the
panel by hand, auto-scrolling stops for five seconds so it does not fight them.

---

## Coming back to a lecture

When a student reopens a lecture they have watched before, two things are restored:

**Their position.** Playback resumes where they stopped, and says so — *"Resumed
from 12:40"*, with a **Start over** link beside it. The notice disappears after
twelve seconds. A player that silently starts twelve minutes in reads as a bug, so
it is always announced.

The position is not restored in three cases: the lecture was finished, less than
30 seconds was watched, or they got past 97%. In all three, starting from the
beginning is more useful than restoring a position.

**Their answers.** Checkpoints they have already answered are not asked again.
Currently one attempt counts, even if it was wrong — the checkpoint has done its
job of interrupting passive watching, and a student who could not get it should not
be walled out of the lecture on every rewatch. This is a single switch and can be
changed to re-ask until correct.

Pressing **Start over** rewinds the video but deliberately does *not* clear the
answered questions: starting a video again is a request to watch it again, not to
re-take the quiz.

*Mechanism: `/api/v1/video/progress/{file}` returns both halves in one call.
`RESUME_MIN_SEC = 30.0`, `RESUME_END_FRAC = 0.97`, `CHECKPOINT_ANY_ATTEMPT = True`.
The endpoint is scoped to the student themselves or a teacher.*

---

## What the system records while watching

Beyond checkpoint answers, the Classroom logs the shape of the viewing session:

| recorded | why it matters |
|---|---|
| play, pause, seek, ended | the rhythm of how the lecture was watched |
| mute / unmute | audio actually on |
| tab hidden / visible | video playing to an unattended tab |
| genuine watch time | time actually watched, not elapsed |
| total hidden time | how much of it was in a background tab |

"Genuine watch time" is measured by accumulating small forward increments only, so
dragging the playhead to the end does not register as having watched the lecture.

This feeds the engagement figures in the teacher dashboard (Part 4).

*Mechanism: `crLogEngagement()` → `/api/v1/video/engagement`; `crPostCoverage()` →
`/api/v1/video/coverage`. Increments above 1.5s are treated as seeks and discarded.*

---

## At the end of the lecture

Reaching the end — or scrubbing past 97% — triggers a fixed sequence:

**1. Any unanswered checkpoint is forced first.** The student cannot skim to the
end to escape the questions.

**2. A reflection prompt.**

> **✨ Nice work — reflect for a moment**
> What was your single most important takeaway from this lecture?

This is the closing half of the self-regulated learning loop that the goal prompt
opened. The pair — intention before, reflection after — is stored together and is
what makes a student's stated goal comparable against what they say they got.

**3. A programming problem.** The student is offered a problem drawn from that
lecture's prelab bank, with the concept it exercises and a sample output they can
expand. Three choices:

- **Maybe later** — dismiss
- **↻ Show a different problem** — draw another from the same lecture
- **🚀 Solve in SAGE** — hand the problem to the Chat tutor

Choosing **Solve in SAGE** switches to the Chat view and submits the problem as a
guided complex problem, which starts the step-by-step scaffolding described in
Part 1. This is the bridge between the two halves of the system: the lecture ends
by putting the student into guided practice on what it just taught.

*Mechanism: `crTriggerEndSequence()` → `crShowReflectionModal()` →
`crShowPrelabModal()` → `crSolveInSage()`, which calls `sendMessage(problem, true)`
in the Chat view.*

---

## Transcription, behind the scenes

Lecture videos are transcribed automatically by a speech-recognition model, and
the result is cached so it is only ever done once per video.

Transcription jobs run **one at a time through a single dedicated worker.** A
teacher can assign twenty lectures in a minute without slowing the site down for
anyone — the jobs queue up and drain in the background, and the dashboard can show
how many are waiting. Re-assigning a lecture that is already transcribed,
running, or queued costs nothing.

*Mechanism: Whisper (`base` model) in `video_service.py`, serialized behind
`_transcribe_lock` and drained by one `whisper-transcriber` thread.*

---

## What the Classroom deliberately does not do

- **It does not let the tutor see ahead.** The context cutoff at the student's
  current timestamp is strict, and it is the point of the feature.
- **It does not send the answer key to the browser.** Checkpoint options arrive
  without their correct index.
- **It does not offer the Sources panel or tutor settings.** Both belong to Chat
  and would do nothing here, so they are hidden rather than left as dead controls.
- **It does not treat a server error as a wrong answer.** If a checkpoint cannot
  be graded, the student is told the answer was not graded — not that they were
  wrong. And if their login has expired, they are told that explicitly, because the
  modal is non-skippable and would otherwise trap them.

---

## Known limitations

Stated plainly, because they affect how the data should be read:

- **Checkpoint questions are machine-generated and not reviewed before students
  see them.** The first student to open a new lecture triggers generation, and
  whatever is produced is cached and served to everyone after them. There is no
  instructor approval step.
- **The generated bank is position-biased.** Shuffling neutralises the effect for
  students, but the underlying generation still favours the first option. The
  symptom is handled; the cause is not.
- **Topic detection reads the filename.** A lecture whose filename contains none
  of the known keywords is classified `General`, which silently disables the
  course-material supplement for every question asked during it. Some lectures in
  the current catalogue are filed under generic names and are affected.
- **Transcription uses the `base` speech model.** It is fast and adequate for
  retrieval, but it makes errors on technical terms — which propagate into both
  the transcript panel and the generated questions.
- **One attempt marks a checkpoint answered, even when wrong.** This is the
  deliberate current setting, but it means "answered" in the data does not mean
  "understood".
