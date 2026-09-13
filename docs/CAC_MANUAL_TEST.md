# CAC Manual Test — Step by Step

You log in, you type questions, you check what comes back. 12 tests, ~30 minutes.

Every expected output below was measured against a copy of the real database, not
predicted.

---

# Part 1 · Setup

Four test learners get created. **T1, T2 and T3 have identical credentials** —
all three have earned Variables and nothing else. Only their *behaviour* differs.
That is the point: when the same question gets a different answer, the learner
model is the only thing that changed.

### Step 1 — back up

```bash
cd /path/to/AI-Tutor
cp backend/database/ai_tutor.db backend/database/ai_tutor.db.bak
```

### Step 2 — create the four learners

```bash
python scripts/cac_seed_manual_test.py
```

You will see a `(trapped) error reading bcrypt version` traceback. **Ignore it** —
it is passlib noise. The `created user ...` lines are the real result.

### Step 3 — confirm they are in the right state

```bash
python scripts/cac_seed_manual_test.py --verify
```

Check these four lines. **If they are wrong, stop and re-seed** — no UI test
below can be meaningful if the learners are not in the states they should be:

```
cactest_baseline        load 0.048   measured    gap 0.0
cactest_impulsive       load 0.048   impulsive   gap 0.0     <- impulsive
cactest_loaded          load 1.0     measured    gap 0.0     <- load 1.0
cactest_overconfident   load 0.048   measured    gap 0.8     <- gap 0.8
```

### Step 4 — turn enforcement on and restart

Nothing below is visible until you do this. CAC ships in shadow mode: it decides
and logs, and changes nothing.

```bash
sudo docker-compose down
SAGE_CAC_ENFORCE_ENABLED=true sudo docker-compose up --build -d
sudo docker-compose logs -f backend
```

Leave that log running in a second window. It is optional for the tests, but it
tells you *why* when something surprises you.

**Not using Docker?**

```bash
cd backend && SAGE_CAC_ENFORCE_ENABLED=true uvicorn app.main:app --reload
```

The switch is read once at startup, so a restart is required either way.

### Step 5 — confirm it took

In the startup log:

```
🎚️ [CAC] policy v1: load thresholds 0.7/0.5 (declared default)
```

**Password for all four accounts: `cactest`**

---

# Part 2 · The tests

Use these exact topic names — CAC matches on them: `Variables`, `Control Flow`,
`Functions`, `Arrays`, `Strings`, `Pointers`, `Structures`, `Memory Allocation`,
`File I/O`.

---

## Group A — Has this student earned the topic?

### ▢ Test 1 — a topic they HAVE earned

**Log in as** `cactest_baseline`

**Type:**
```
explain control flow
```

**PASS if:** a normal, complete explanation. Code is allowed and expected.

**FAIL if:** the answer is short or refuses. Control Flow is fully authorised for
this learner — if this is capped, something is wrong.

> This is your control case. Everything after this is a comparison against it.

---

### ▢ Test 2 — a topic three steps ahead

**Log in as** `cactest_baseline`

**Type:**
```
explain file I/O
```

**PASS if all three:**
- the answer is **two sentences at most**
- the rest of it is about why **Strings** is worth learning first
- there is a suggestion button like `Explain Strings`

**FAIL if:** you get a full File I/O tutorial, or any C code.

> Note what it does *not* do: it never says "no". A refusal that does not teach
> is a failure, not a security win.

---

### ▢ Test 3 — the ladder is graded, not a door

**Log in as** `cactest_baseline`

**Type:**
```
explain arrays
```

**PASS if:** short answer again, but pointing at **Control Flow** this time — not
Strings.

> Compare with Test 2. One hop away gets a different pointer than three hops
> away. The system names the *nearest* next step, not the safest retreat.

---

## Group B — Is this student working, or grabbing?

`cactest_impulsive` has **exactly the same credentials** as `cactest_baseline`.
They have been skipping quizzes and typing "idk" within two seconds.

### ▢ Test 4 — the same question, a different answer  ★

**Log in as** `cactest_impulsive`

**Type:**
```
explain control flow
```

*(the identical question Test 1 answered in full)*

**PASS if all three:**
- you get explanation, diagrams, guiding questions
- **no C code anywhere** — no ```c block
- a Mermaid diagram is still fine (diagrams sit below hints on the ladder)

**FAIL if:** a C code block appears. See the note at the end of Part 3 before
recording it as a failure — this is the one test where a failure is interesting
rather than broken.

> **★ This is the single most important test.** Put Test 1 and Test 4 side by
> side. Same credentials, same question, different depth. That comparison is the
> whole argument.

---

### ▢ Test 5 — asking for a program instead

**Log in as** `cactest_impulsive`

**Type:**
```
write a program that prints the numbers 1 to 10
```

**PASS if:**
- **no** step-by-step guided plan. No "I'm going to break this down into steps"
- you get a normal answer instead, still without code

**FAIL if:** a guided plan starts, or you get working code.

> Until recently this phrasing skipped CAC completely and produced code. It is a
> different code path from Test 4, reached just by rewording the request.

---

### ▢ Test 6 — pasting broken code

**Log in as** `cactest_impulsive`

**Type:**
```
why does my code crash? int *p; *p = 5;
```

**PASS if:**
- you get the diagnosis in words — the pointer was never pointed at anything
- **no corrected code block**

**FAIL if:** it hands back fixed code.

> Also a live bypass until recently. The debug path took no cap at all, so a
> capped learner could get full disclosure just by pasting code.

---

### ▢ Test 7 — asking for a design

**Log in as** `cactest_impulsive`

**Type:**
```
design a student record system in C
```

**PASS if:** no `Starter Skeleton` code block.

> Third of the three doors that were open.

---

## Group C — Is this student overloaded?

`cactest_loaded` again has the same credentials. This one is struggling badly —
slow turns, complex work, every recent answer wrong.

### ▢ Test 8 — reaching far ahead while drowning

**Log in as** `cactest_loaded`

**Type:**
```
explain file I/O
```

**PASS if both:**
- it teaches **Arrays** instead, and **says so** — names the swap openly
- it does not silently answer a different question

**FAIL if:** you get File I/O content, or the topic changes with no explanation.

**In the log:**
```
↩️ [CAC] horizon redirect ... 'File I/O' → 'Arrays'
```

> Compare with Test 2. `cactest_baseline` asked the same thing and was pointed at
> **Strings**. This learner gets **Arrays** — closer — because being overloaded
> shortens how far ahead they may reach. Same question, same credentials,
> different reach.

---

### ▢ Test 9 — the redirect is real, not just wording

**Log in as** `cactest_loaded` — same turn as Test 8.

**Check the sources / citations** shown with that answer.

**PASS if:** they are **Arrays** material.

**FAIL if:** they cite File I/O documents.

> This is the difference between a redirect and a polite refusal. The retrieval
> was re-run, so the model has no File I/O material in front of it to leak.

---

## Group D — Does this student know what they don't know?

`cactest_overconfident` has earned much more: Variables, Control Flow, Arrays
**and Strings**. But Strings was certified two days ago and has faded — the three
tiers now sit near `0.93 / 0.84 / 0.77`. Still certified (the bar is 0.75), no
longer comfortable.

They are also overconfident: **4 of their 5 wrong answers** were ones the system
expected them to get right.

### ▢ Test 10 — building on a stale prerequisite

**Log in as** `cactest_overconfident`

**Type:**
```
explain file I/O
```

**PASS if:** you are asked to revisit **Strings** first — a roadmap naming
Strings, with an `Explain Strings` button.

**In the log:**
```
📐 [CAC] edge threshold 0.90 REFUSED ['Strings']
```

> **Check the log line before recording a failure.** The visible roadmap comes
> from the gatekeeper, whose prerequisite list is read from **Neo4j**, not from
> CAC's own map. If your graph does not record that File I/O needs Strings, the
> message will not appear even though the mechanism fired correctly. The log
> line is the authoritative check.

---

### ▢ Test 11 — the bar moved, the credential did not

**Log in as** `cactest_overconfident`

**Type:**
```
explain strings
```

**PASS if:** you get a normal, full answer. They have earned Strings and still
have it.

**FAIL if:** it refuses. Phase 4 raises the bar for *building on* Strings — it
does not revoke Strings.

> Test 10 and Test 11 together are the phase: same learner, same topic, and the
> difference is whether they are *using* it or *building on* it.

---

### ▢ Test 12 — a normal learner is untouched

**Log in as** `cactest_baseline`

**Type:**
```
explain control flow
```

**PASS if:** full answer with code — exactly as in Test 1, unchanged.

> The last check is that none of this leaked onto a learner it was not aimed at.

---

# Part 3 · Record your results

| # | Learner | Asked | Expected | Pass? |
|---|---|---|---|---|
| 1 | baseline | explain control flow | full answer, code allowed | |
| 2 | baseline | explain file I/O | 2 sentences + "Strings first" | |
| 3 | baseline | explain arrays | 2 sentences + "Control Flow first" | |
| 4 ★ | impulsive | explain control flow | **no code** — same Q as Test 1 | |
| 5 | impulsive | write a program... | no guided plan | |
| 6 | impulsive | why does my code crash | diagnosis, no fixed code | |
| 7 | impulsive | design a system in C | no starter skeleton | |
| 8 | loaded | explain file I/O | teaches **Arrays**, names the swap | |
| 9 | loaded | (same turn) | sources are Arrays material | |
| 10 | overconfident | explain file I/O | asked to revisit Strings | |
| 11 | overconfident | explain strings | full answer — still certified | |
| 12 | baseline | explain control flow | unchanged from Test 1 | |

### If Test 4, 5, 6 or 7 shows code anyway

**This is a finding, not a broken test — and it is worth writing down.**

Tests 8–11 are enforced in code: Test 8 rewrites what gets retrieved, Test 10
refuses before the model ever runs. If the decision is right, the outcome is
right.

Tests 1–7 work differently. They remove sections from the prompt and add
*"do not output C code in any form"*. **Nothing inspects the answer afterwards.**
So a model that ignores the instruction produces code and nothing catches it.

```
the decision was correct        ← --verify already confirmed this
the prompt said "no code"       ← the code guarantees this
the model wrote code anyway     ← only you, testing by hand, can see this
```

If that happens, note which test and paste the response. The fix is small —
strip C code blocks from the answer when the cap is below "worked example" —
and it would make the rung cap enforced rather than requested.

---

# Part 4 · Clean up

```bash
python scripts/cac_seed_manual_test.py --clean
```

Then restart without the switch, to return to shadow mode:

```bash
sudo docker-compose down && sudo docker-compose up -d
```

To restore the backup instead:

```bash
cp backend/database/ai_tutor.db.bak backend/database/ai_tutor.db
```

---

# If something does not work

| What you see | Why |
|---|---|
| nothing is ever capped, every answer is normal | `SAGE_CAC_ENFORCE_ENABLED` not set, or the server was not restarted |
| no `[CAC]` lines in the log at all | the question did not name a topic CAC knows — use the exact names listed in Part 2 |
| Test 4 behaves like Test 1 | `--verify` shows `measured` instead of `impulsive` — re-run the seed |
| Test 8 does not redirect | `--verify` shows load below 0.5 — re-run the seed |
| Test 10 shows no roadmap | Neo4j has no `Strings → File I/O` edge; check the log line instead |
| answers are capped when they should not be | the region gate is ON by default — that is intended, see Test 2 |
| `--verify` is right but the UI is not | see the note at the end of Part 3 |

**Optional — look at the audit trail.** Every turn is recorded whether or not it
was narrowed:

```sql
SELECT username, concept_asked, in_region, in_horizon, rung_cap, redirect_to, reasons
FROM cac_access_event WHERE username LIKE 'cactest_%' ORDER BY id DESC LIMIT 20;
```

`reasons` names the signal that acted: `K/region`, `C/help-seeking`, `C/load`,
`C/calibration`.
