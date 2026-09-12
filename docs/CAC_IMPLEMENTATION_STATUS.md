# CAC — Implementation Status

*State of the Cognitive Access Control build on `IRL-security-extension`.
Written 2026-09-09 against `547be66` (CAC-phase 1). Plan:
[CAC_DYNAMIC_DAG_PLAN.md](CAC_DYNAMIC_DAG_PLAN.md) · visual:
[dynamic-dag.html](dynamic-dag.html).*

---

## Summary

| Phase | Scope | Status |
|---|---|---|
| **0** | Pure decision function, topology, switches, property tests | **done** — `36d667b` |
| **1** | Observe-only access logging | **done** — `547be66` |
| — | Switch surface, shadow mode, replay harness, `ORIENT` rung | **done** — `547be66` (added on request, not in the original plan) |
| **2** | Help-seeking caps the disclosure rung | **not started** |
| 3 | Cognitive load contracts the horizon | not started |
| 4 | Calibration tightens a prerequisite edge | not started |
| 5 | Rushing discounts evidence | not started |
| 6 | Persistence earns break-glass | not started |

**No student's experience has changed.** The layer computes and records a full
decision on every turn; nothing acts on it. Two independent reasons:
`cac_enforce` is off by default, and no agent reads a rung — `grep -rn "rung_cap"
app/agents/` returns nothing.

---

## What Phase 0 built

`backend/app/core/cac_graph.py` — the whole policy, as a pure function.

**Topology.** The nine coarse topics and their prerequisite edges. This module now
owns `SKILL_TOPICS` and `CANONICAL_PREREQ_EDGES`; `main.py` imports them rather
than keeping a second copy, so the skill-network dashboard and the access policy
cannot drift apart. No Neo4j dependency — the coarse graph is fixed curriculum
knowledge, and the policy must work in tests, in offline replay, and when the
graph database is down.

**The frontier.** `frontier(certified)` returns concepts whose prerequisites are
all earned but which are not themselves earned. This set is the Zone of Proximal
Development and the authorization boundary at once; `authorized()` is it plus the
earned set.

**The disclosure ladder.** CAC's output is a rung, not a boolean:

```
NONE  <  ORIENT  <  DIAGRAM  <  HINT  <  EXAMPLE  <  CODE
```

`ORIENT` is a one-or-two-line answer that spends the rest of its space on why the
missing prerequisite matters. It exists because a *full* answer with a
prerequisite note appended does not work — the note is never read once the answer
is already there, so the suggestion only lands if the answer leaves room for it.

**The invariant, enforced structurally.** `_tighten()` is the only function that
may modify a decision, and it takes the minimum: rung can only fall, booleans can
only go True→False. A signal has no vocabulary for widening, so no later phase can
add one by accident. This is *why* the property below is a statement about the
code rather than about the current thresholds.

**Purity.** `decide()` touches no database, calls no model, mutates nothing. All
I/O is in `build_view()`. That is what makes ablation provable, replay possible,
and property tests meaningful.

### Property tests — 1022 passing

Quantified over 200 randomly generated DAGs and learner states, not examples,
because the claims are universal:

| Property | Meaning |
|---|---|
| signals never widen | adding metacognitive state can only tighten |
| certifying more never restricts | learning something never makes a learner worse off |
| `decide()` never mutates its input | purity, on which replay depends |
| `decide()` is deterministic | STRIDE Repudiation / E9 reproducibility |
| region gate only ever tightens | the strict reading is a subset of the loose one |

**The invariant test was verified non-vacuous.** With every signal a stub, a
property test over them could pass trivially. Injecting a signal that widens on
good help-seeking fails 16 of 200 seeds; the injection was then reverted.

---

## What Phase 1 built

Observation, with no behavioural change.

**`cac_access_event` table** (`sqlite_db.py`). One row per turn where CAC formed
an opinion. Migration verified idempotent across three consecutive starts.

Distinct from `path_event`, which classifies against the learner's chosen *goal
path* and therefore only exists once a goal is set. This table records the region
derived from *certification*, which is always defined — so "the learner reached
past what they have earned" is answerable for every student. Both are stored, so
the two notions can be compared empirically.

**`telemetry.log_cac_access()`** writes it; **`telemetry.cac_probe_stats()`**
reads it back as `{turns, probes, probe_rate}`, scoped to a session or
account-wide.

Every turn is logged, not only the probes. A probe count without a turn count is
not a rate: two over-reaches in a hundred turns and two in two are the same
number and completely different students.

Each row stamps `ablation_config`, so a decision stays attributable to the
configuration that produced it.

**Call site** — `main.py`, beside the existing path-deviation logging. The
decision is computed, logged, and not read. Verified structurally: no branch
anywhere touches `.rung_cap`, `.in_horizon`, `.edge_ok`, or `.redirect_to`.

---

## The switch surface

One switch per moving part, so each phase can be connected and disconnected on
its own. All drive off the existing `SAGE_ABLATE` / `SAGE_<NAME>_ENABLED`
machinery and appear in `active_ablation_config()`.

| Switch | Default | Controls |
|---|---|---|
| `cac_graph` | **on** | compute a decision at all |
| `cac_enforce` | **off** | **act** on it — shadow mode otherwise |
| `cac_region_gate` | **on** | beyond-frontier caps at `ORIENT` |
| `cac_rung` | on | Phase 2 · help-seeking |
| `cac_horizon` | on | Phase 3 · cognitive load |
| `cac_edge` | on | Phase 4 · calibration |
| `cac_evidence_weight` | on | Phase 5 · the only switch that can move mastery numbers |
| `cac_breakglass` | on | Phase 6 |

`cac_enforce` defaults **off** so that landing a phase and turning it on are two
separate acts. The layer runs in shadow: a full decision every turn, no learner
affected, which is how the policy gets measured before it is trusted with
traffic. `cac_graph.enforcing()` is the single predicate a consumer must check.

```bash
SAGE_ABLATE=cac_horizon            # disconnect one signal
SAGE_CAC_ENFORCE_ENABLED=true      # let decisions act
```

Verified: enforcement toggles, each signal disconnects individually, and the
master switch makes the layer provably inert (`evaluate()` returns the permissive
no-op with zero reasons).

---

## The replay harness

`scripts/cac_replay.py`. The branch is not deployed, so every pre-deployment
number has to come from replaying the policy over turns that already happened.
Possible only because `decide()` is pure. **Reads only; writes nothing.**

```bash
python scripts/cac_replay.py                       # everything connected
python scripts/cac_replay.py --off cac_horizon     # attribute one signal
python scripts/cac_replay.py --frontier halfway    # sweep the frontier basis
python scripts/cac_replay.py --advisory            # annotate instead of capping
python scripts/cac_replay.py --detail 20           # per-learner
```

State reconstruction has three modes, because replaying a July turn against a
September certification would credit knowledge the learner did not have:
`--state asof` (default, from `bkt_history`), `current` (a floor — over-states K),
`none` (the other bound). The report prints how many turns had reconstructable
state, because on this corpus the answer is 5%.

---

## What replay found

Two findings, both from the 1248 topic-bearing turns in `turn_log`
(630 learners, July–September).

### 1. The Phase 0 baseline was wrong, and was corrected

As first written, being beyond the frontier capped the rung. On real data that
restricted **91% of all turns** — including the 96 learners who asked about
Pointers. Asking about something not yet mastered is what learning looks like,
not an attack, and a rule that fires nine times in ten is measuring the corpus.

The correction: the region *annotates*; whether anything narrows is a separate
switch, and the metacognitive signals are what should decide. The strict reading
stays available behind `--advisory` / `cac_region_gate` so both can be reported.

*(The rung it caps at was then changed from `HINT` to `ORIENT` — see below.)*

### 2. The frontier definition, not the rung, sets the firing rate

Only **3 of 630 learners hold a live certification** (conjunctive, θ=0.95 on all
three tiers, n≥3 each). So the frontier collapses to `{Variables}` for nearly
everyone. What counts as "earned" is therefore the lever:

| "Earned" means | Beyond frontier | `ORIENT` | Full code |
|---|---|---|---|
| `certified` — live certification (current default) | **91.4%** | 1141 | 107 |
| `ever` — sticky, never retreats | 82.7% | 1032 | 216 |
| `halfway` — P̃ ≥ 0.5 | **61.0%** | 761 | 487 |
| `touched` — any evidence at all | 60.6% | 756 | 492 |

`halfway` and `touched` land almost identically (61.0 vs 60.6), so on this data
"has any evidence" and "is halfway there" describe nearly the same students and
the cheaper rule loses nothing. Even the loosest setting is 61%, because most
students have not worked much yet.

**Open decision.** The frontier basis is unresolved and is a pedagogical call.
`halfway` keeps strictness where it matters — no full pointer code with zero
evidence on Variables — without one-lining three-fifths of everything on a
certification bar almost nobody clears.

---

## Design decisions worth preserving

Each of these was arrived at against resistance and is easy to undo by accident.

**`ORIENT` rather than a full answer plus a note.** Rejected: answering fully and
appending "Variables would help first". The note is never read once the answer is
there. The answer has to be short enough that the prerequisite is the point.

**The region annotates; metacognition decides.** The graph says *where* a learner
is. Calibration, load and help-seeking say whether anything should narrow. Coding
it the other way round produced the 91% wall.

**Every turn logged, not only the interesting ones.** Otherwise there is no
denominator and no rate.

**`evaluate()` fails open.** This layer only narrows, so a crash in it must never
become a block. The Sentinel's hard blocks and the K-based prerequisite gate are
untouched and still run.

**`build_view()` degrades to the neutral learner.** A learner-model outage
disables the C narrowing and leaves the K baseline intact — the safe direction
for a layer that can only restrict.

**Cold start is not suspicion.** A learner with no history is unremarkable, never
suspicious. A policy that tightened on missing data would hit the whole class in
week one.

---

## What Phase 2 needs

Two pieces, and the second is the real work:

1. Fill in `_signal_help_seeking()` — cap the rung at `HINT` when
   `help_seeking.quality == "impulsive"` (already computed at
   `learner_model.py:149` from skip rate and immediate give-ups).
2. Teach `ScaffoldingAgent` to respect a `max_disclosure` directive. It has no
   concept of a rung today; its plan generator (`scaffolding.py:384`) needs the
   cap injected into the prompt and code-emitting steps skipped above it.

Testable as a property, not a judgement: under `cap=HINT`, the response contains
no fenced code block. That is a grep.

The single integration point already exists — the `enforcing()` branch in
`main.py`.

---

## Files

| Path | Role |
|---|---|
| `backend/app/core/cac_graph.py` | topology, ladder, decision, switches — the whole policy |
| `backend/app/core/config.py` | switch surface, off-by-default mechanism |
| `backend/app/core/telemetry.py` | `log_cac_access`, `cac_probe_stats` |
| `backend/app/db/sqlite_db.py` | `cac_access_event` table and indexes |
| `backend/app/main.py` | shadow-mode call site; imports the topology |
| `backend/tests/test_cac_graph.py` | 1022 tests |
| `scripts/cac_replay.py` | offline replay and sweeps |

## Verification at this commit

- `1022` CAC tests pass; `134` project tests pass
- One pre-existing failure, `test_user_manager::test_create_and_auth_user`
  (`UserManager` has no `_save_db`), confirmed by stashing the CAC changes — it
  is not from this work
- `PRAGMA integrity_check: ok`; replay writes nothing; no test rows left behind

---

# Addendum — scaffolding gate + Phase 4

## The scaffolding bypass (fixed)

CAC was evaluated inline just above the Socratic hand-off. **Both
`ScaffoldingAgent` entry points return before reaching it** (`cot_rag_agent.py`
lines 2079 and 2273; the evaluation sat at 2344), so the guided track answered
with no rung cap, no horizon check, and **no `cac_access_event` row at all**.

```
"explain pointers"              -> Socratic     -> governed      ✓
"write a program with pointers" -> Scaffolding  -> not evaluated ✗
```

Same learner, same topic, one word apart — and the ungoverned path is the one
that generates code. No sophistication required to reach it; plenty of students
phrase requests the second way by default.

**Fix.** The evaluation moved into `ChainOfThoughtRAGAgent._apply_cac()`, called
in front of every answering path, idempotent via `AgentState.cac_evaluated`.
Idempotence is load-bearing: the horizon redirect rewrites `state.entities`, so
a second pass would read the substituted topic instead of what was asked, and
two agents could otherwise hold different caps in one turn.

`ScaffoldingAgent` now also consumes the cap:

| Site | Behaviour under a cap |
|---|---|
| new plan (`process`) | declined below `EXAMPLE`; falls through to Socratic |
| partial-code escalation | carries `disclosure_directive(cap)` |
| step evaluation | takes `rung_cap`, carries the directive |

A guided plan walks to working code, so starting one discloses at EXAMPLE/CODE
by construction. Declining rather than degrading is deliberate: a "plan" that
may not show code is not a scaffold, and the honest outcome is to teach the
thing that unlocks it.

## Phase 4 — calibration → edge threshold

Implemented, but **not on the input the plan specified**.

The plan sourced "wrong and confident" from `jol_log.confidence_1_5`, a learner
self-report. Two problems:

1. **Structurally starved.** Written only inside the pop-quiz path, which
   requires a ≤4-word acknowledgement turn — 0.34% of student messages. 6 rows
   across 3 learners, and no amount of additional traffic changes that.
2. **Declared by the subject of the policy.** A learner who notices that
   admitting confidence tightens their gate stops admitting it. An inferred
   attribute the subject controls is a weak basis for access control.

`prediction_log.p_bkt_pred` answers the same question from the model's side —
what the system expected *before* the learner answered — and is written on every
evidence event: **152 rows across 26 learners**. The learner never sees it and
cannot declare it.

`learner_model._calibration()` computes it; `build_view` prefers it and falls
back to self-report. `_signal_calibration` fires when `gap_ratio > 0.5` over at
least `GAP_MIN_WRONG = 3` misses, and only on concepts 0–1 hops past the
frontier — tightening everything would be the region gate again, not an edge bar.

### The invariant

`theta_edge` starts at `THETA_BASE = 0.75` (mirroring `bkt_model.THETA_DECERTIFY`)
and may only rise, toward `THETA_OVERCONFIDENT = 0.90`. `_tighten()` takes the
**maximum** and clamps at the floor, so the sign constraint is a property of the
mutator rather than a promise from its callers — a future signal passing a low
theta cannot open a gate. Asserted as a property over 2000 random inputs.

0.90 is short of `THETA_CERTIFY = 0.95` on purpose: "show me more before you
build on this", not a re-certification.

### It fires on real data

The first cognitive signal that does:

```
PathfindersChallenge   48 predictions  12 wrong   7 confident-wrong  gap 0.583  -> theta 0.90
rayhan_bug_test        11 predictions   3 wrong   2 confident-wrong  gap 0.667  -> theta 0.90
TEST001                 6 predictions   2 wrong   2 confident-wrong  gap 1.00   -> held (below evidence floor)
hs_test_user           39 predictions   2 wrong   0 confident-wrong  gap 0.00   -> untouched
```

2 of 26 learners. `TEST001` at gap 1.00 being held back is the evidence floor
working, not a miss.

Contrast: Phase 2 fires on 0 learners (quiz-starved), Phase 3 on 0 (thresholds
above the population maximum — see `CAC_THRESHOLD_CALIBRATION.md`).

## Verification

- `1500` tests pass — `1450` cac_graph, `20` Phase 4, `18` threshold
  calibration, `12` scaffolding gate
- Enforcement still gated on `cac_enforce`, off by default; `rung_cap` defaults
  to CODE, so none of this changes a response until the switch is thrown
- Real-data runs used a scratchpad COPY of the database, guarded by an assert

## Open

- `_signal_calibration` narrows the decision and records `theta_edge`; **no
  consumer enforces it yet.** The plan names
  `user_knowledge_manager.has_certified_prerequisite` as the site. Measurable in
  shadow mode now; wiring the consumer is a separate, riskier change because it
  touches the K gate rather than prompt shaping.
- Phase 2 (`help_seeking`) remains unreachable and has no live substitute.
  Recommend marking it deferred rather than keeping it on the critical path.

## Phase 4 consumer — the gate now reads `theta_edge`

`_signal_calibration` recorded the raised bar; nothing enforced it. It does now,
**behind the enforcement switch**, and it measures on every turn regardless.

| Piece | Role |
|---|---|
| `bkt_model.meets_theta(u, c, θ)` | do all three DECAYED tiers clear θ? read-only |
| `knowledge_manager.has_mastered(u, c, theta=None)` | extra conjunct when θ given |
| `AgentState.theta_edge` | carried on every turn, enforced or not |
| `_check_gatekeeping(..., theta_edge=)` | computes both answers, acts on one |

`meets_theta` is deliberately **read-only**. `is_mastered` self-heals the
`is_certified` column as a side effect, which is right for the certification
question and wrong here: a raised bar is a per-learner access decision, and
letting it clear a shared column would turn one learner's overconfidence into a
global de-certification.

The θ conjunct is `AND`, so a raised bar can only ever **refuse** a prerequisite
the curriculum would have passed — never admit one it would have refused.
`theta=None` (every pre-existing caller) takes the old path exactly.

### Measure first, enforce second

Both answers are computed every turn. `cac_graph.enforcing()` picks which one
the learner gets; the other is written to `event_log` as `cac_edge_threshold`
with `enforced: false`. So the tightened gate accumulates a record of who it
*would* have refused before it is trusted to refuse anyone — a control never
observed refusing the right learners is not ready to refuse any.

### Acceptance criterion — met

The plan: *"an overconfident learner fails the gate at P̃=0.85 while a
calibrated learner passes at the same P̃; property test holds over random
graphs."* Both hold (`test_cac_edge_gate.py`, `test_cac_calibration_signal.py`).
0.85 is the separating value: above the curriculum's 0.75, below the raised 0.90.

### On real data, one concrete refusal

```
PathfindersChallenge  gap 0.583  certified: Strings
  Strings / quiz   0.982 -> decayed 0.942   >= 0.90  ok
  Strings / micro  0.998 -> decayed 0.954   >= 0.90  ok
  Strings / code   0.989 -> decayed 0.839   <  0.90  REFUSED
```

Worth reading closely: the raw posteriors all clear 0.90. The refusal comes from
**decay** — the code tier faded from 0.989 to 0.839 over ~18 days. The learner is
still certified (0.839 > 0.75) and is also overconfident, so the gate says "show
me Strings again before building on it". That is the intended behaviour, not an
artifact: `meets_theta` is decay-aware by construction, and stale-plus-
overconfident is precisely the combination Phase 4 was specified to catch.

`rayhan_bug_test` is overconfident (gap 0.667) with nothing certified, so nothing
to tighten — correctly untouched.

## Verification

- `1514` tests pass (`+14` edge gate)
- Regression guard added: `run()` has its own prerequisite loop and no
  `theta_edge` in scope. The edge block was first applied there by mistake,
  which would have been a `NameError` on the first CONCEPT/PROBLEM turn through
  that entry point. A test now asserts `theta_edge` never appears in `run()`.

## Phase 2 verified by simulation — and two more bypasses found

Phase 2's trigger has never fired on real data (`quiz_log` is starved), so the
mechanism was unverified end to end. Six synthetic learners were seeded into a
COPY of the database with the rows a real examiner would have written, then
traced through `DB → _help_seeking → build_view → decide → prompt`.

### The mechanism is correct

| persona | quizzes | skips | give-ups | quality | cap |
|---|---|---|---|---|---|
| impulsive | 4 | 6 | 3 | impulsive | **hints** |
| impulsive (borderline) | 6 | 4 | 1 | impulsive | **hints** |
| measured | 10 | 1 | 0 | measured | full code |
| skips, never surrenders | 4 | 6 | 0 | measured | full code |
| surrenders, but rarely | 20 | 1 | 2 | measured | full code |
| no history | 0 | 0 | 0 | measured | full code |

Both halves of the conjunction matter and both were verified: skipping without
surrendering does not trigger it, and surrendering at a low rate does not
either. Cold start stays uncapped.

### Bug 1 — twelve sections were ungoverned

`section_allowed` fails OPEN on unrecognised headers, deliberately, so an
unmapped section never blanks a response. The cost is that coverage degrades
**silently** as prompts evolve. `SECTION_MIN_RUNG` had 13 entries; the prompts
emit 25. Unmapped, and therefore surviving at every rung including NONE:

```
diagnostic branch : What's Wrong · Why · How to Fix
planning branches : Strategy · Visual Logic · Implementation Plan ·
                    Guiding Question · Architectural Overview ·
                    System Design (Diagram) · Implementation Phases ·
                    Why this approach works · Starter Skeleton
```

`Starter Skeleton` is the serious one — it emits real C.

### Bug 2 — two of four prompt builders never saw the cap

Same shape as the ScaffoldingAgent door, two more doors:

| builder | intent | before |
|---|---|---|
| `_build_concept_prompt` | CONCEPT | governed |
| `_build_diagnostic_prompt` | **DEBUG** | no `rung_cap` parameter at all |
| `_build_complex_plan_prompt` | **COMPLEX_PROBLEM** | no `rung_cap` parameter at all |

So a learner capped at HINT could reach full disclosure by **pasting code and
asking why it breaks** — the debug path answered with no cap and no directive.
`_build_complex_plan_prompt` emits a `Starter Skeleton` of real C on the same
terms.

Both now take `rung_cap` and apply it: the diagnostic branch filters its section
list and appends the directive, matching the concept branch step for step; the
plan prompt is a single literal, so the directive is appended last.

### The guard against a fourth door

`test_cac_section_coverage.py` scans `socratic.py` for every emitted `## `
header and fails if one is missing from `SECTION_MIN_RUNG`, and asserts every
prompt builder both **accepts** `rung_cap` and **uses** `disclosure_directive`.
That converts the silent runtime fail-open into a loud test failure — which is
what would have caught all three bypasses when they were written.

## Verification

- `1526` tests pass (`+12` section coverage)
- Simulation: 11/11 checks, against a scratchpad COPY guarded by an assert
