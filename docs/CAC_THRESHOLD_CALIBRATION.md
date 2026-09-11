# CAC Threshold Calibration

Automatic, versioned calibration of the cognitive-load thresholds that govern
the Phase 3 horizon signal.

## Why

`LOAD_HORIZON` shipped as `0.7 -> 1 hop`, `0.5 -> 2 hops`. Those numbers were
chosen because `cognitive_load` is scaled 0..1 and 0.5 reads like "half loaded".
The population disagrees:

```
p50   0.029
p90   0.056
p99   0.197
max   0.415        <- no learner ever reached 0.5
```

630 accounts, zero above either threshold. **A number no learner can cross is
not a policy** — the horizon signal was dead on arrival, and no amount of
student behaviour would have changed that.

## What it does

Every `RECALIBRATE_EVERY` (100) CAC-evaluated turns, a background thread
computes candidate thresholds from the population, applies the guards below,
and either promotes a new version or records why it declined. Thresholds are
**never mutated in place**; each run appends to `cac_policy_version`, and every
`cac_access_event` carries the `policy_version` that produced it.

Thresholds are phrased as the policy question — *what fraction should be
contracted?* — rather than as a magnitude:

| | percentile | effect |
|---|---|---|
| `load_t1` | p98 | top ~2% → 1 hop past the frontier |
| `load_t2` | p90 | top ~10% → 2 hops |

The magnitude is an artifact of three arbitrary divisors inside
`learner_model._cognitive_load` and means nothing on its own.

## The attack this is built against

A percentile threshold makes one learner's outcome depend on what other accounts
did. That is a control surface reachable by people who are not the target.

```
attacker floods with high-load traffic
        ↓
   p98 / p90 drag upward
        ↓
attacker's own real load now sits below the threshold
        ↓
_tighten() stops firing → horizon unbounded
```

**What breaks is subtle.** `_signal_cognitive_load` still only ever calls
`_tighten()`, so `M(C,K,S) ⊆ M(C,K)` holds however the threshold moves —
cognitive state cannot widen a decision. What poisoning buys is that the
narrowing *stops firing*, driving `M(C,K,S)` toward equality with `M(C,K)`.
The invariant survives and the mechanism becomes a no-op, which is **harder to
detect than a violation**: every audit row reads "no contraction", which is
also what a correctly-behaving system looks like.

The reverse is an attack too — flood with low-load traffic, the cut point
collapses, and legitimate learners are contracted to a 1-hop horizon. Denial of
service against the curriculum.

## Guards

| Guard | Rule | Closes |
|---|---|---|
| Eligibility | ≥50 students with ≥10 turns each | calibrating on thin data |
| One vote per student | percentile over per-**user** values, never per-turn | one high-volume account setting policy alone |
| Population filter | `users.role='student'`, minus `EXCLUDED_PREFIXES` | the eval harness calibrating the policy it is testing |
| Anchored band | `[base_t2 × 0.5, base_t1 × 1.5]` from the first honest calibration | movement that ends with the mechanism dead |
| Rate limit | ≤0.05 per run | jumps; forces a visible staircase |
| Anomaly hold | candidate >2× the rate limit → reject + log | data incidents and blunt floods |

### Why the band is anchored rather than absolute

The first version of this used a fixed `CLAMP_HI = 0.60`. Simulation showed a
patient attacker reaching it in ~10 runs — and 0.60 sits **above** the observed
population maximum of 0.415, so a "bounded" attack still ended with the
mechanism dead. The bound limited the movement but not the consequence, and
0.60 had been picked by exactly the intuition this module exists to remove.

The operative ceiling is therefore anchored to the **first data-backed
calibration**, which is measured. Deliberately the earliest calibrated version,
not the current one: a ceiling that drifts with the value it bounds bounds
nothing.

Measured against a simulated honest population (`v2 = 0.235/0.179`):

| | fixed `CLAMP_HI` | anchored |
|---|---|---|
| attacker ceiling | 0.600 | **0.352** |
| runs to reach it | 10 | 5 |
| above real population max (0.415)? | **yes — mechanism dead** | no — loaded tail still contracted |

Neither makes poisoning impossible. Together the guards make it slow, bounded,
and legible: the attack above leaves 4 promotions in `cac_policy_version`, each
with its basis and reason.

## Why versions, not a live variable

A silently-moving threshold makes the audit log stop being evidence: replaying
a week-old event against today's constants reports a decision the system never
made. With the stamp, `scripts/cac_replay.py --policy <N>` reads the thresholds
off the row and reconstructs what actually happened.

Rejected runs are recorded too and consume a version number. "We looked and
declined to move" is a finding; omitting it would make the history look
inactive during exactly the period an attack was being refused.

## Current state

`_population()` finds **5** eligible students, against `MIN_STUDENTS = 50`. So
the job runs, declines, and logs:

```
v2  rejected  insufficient basis: 5 students with >=10 turns, need 50
v1  active    seeded default; declared, not measured
```

v1 holds `0.7/0.5` — unchanged, so **landing this alters no decision**. The
eligibility gate handles the thin-data problem by itself: when real students
arrive, calibration starts on its own with no intervention.

For the paper: the thresholds are a *declared default awaiting calibration*,
and the mechanism that will calibrate them is implemented and tested.

## Files

| File | Change |
|---|---|
| `backend/app/core/cac_calibration.py` | new — guards, percentiles, versioning, trigger |
| `backend/app/core/cac_graph.py` | `_active_horizon()`, `_active_policy_version()`, `Decision.policy_version` |
| `backend/app/db/sqlite_db.py` | `cac_policy_version` table; `cac_access_event.policy_version` |
| `backend/app/core/telemetry.py` | writes the version stamp |
| `backend/app/agents/cot_rag_agent.py` | `note_turn()` on each CAC turn |
| `backend/app/main.py` | startup seeds v1 and loads the cache |
| `scripts/cac_replay.py` | `--policy default\|active\|<N>` |
| `backend/tests/test_cac_calibration.py` | new — 18 tests, guard-focused |

## Knobs

| Env var | Default |
|---|---|
| `SAGE_CAC_RECALIBRATE_EVERY` | `100` |
| `SAGE_CAC_CALIB_EXCLUDE` | `irleval_,irl6_,trk,trickle_,gate,qa_,aud_,sec_,cur_` |

## Open

- `EXCLUDED_PREFIXES` is a guess at which accounts are harness-generated.
  Confirm before the first real calibration — a wrong list either admits
  fixtures into the policy or discards real students.
- `CEILING_FACTOR = 1.5` / `FLOOR_FACTOR = 0.5` are judgement, not measurement.
  They bound how far policy may drift from the first honest calibration; the
  right values depend on how much genuine population drift you expect per term.
- Phase 2's `help_seeking` signal is dead for an unrelated reason (the pop-quiz
  trigger matches 0.34% of messages, so `quiz_log` is starved). Calibration does
  not help there — no threshold change rescues a zero denominator.
