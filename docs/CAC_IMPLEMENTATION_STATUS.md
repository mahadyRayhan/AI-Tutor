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
