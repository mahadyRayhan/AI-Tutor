# Phase 0 — instrumentation and data audit

_Run 2026-07-30. Index: `docs/IRL_EXTENSION_ROADMAP.md`._

Phase 0 answered one question before any writing began: **does the data the paper claims to
report actually exist, and were the stored results produced by the code deployed now?**

It produced almost nothing quotable. That is the correct outcome — its value was telling
you which numbers to stop trusting and which sections cannot be written.

---

## 0. Status

| Item | What | State |
|---|---|---|
| 0a | Data-availability audit (`eval_00_data_audit.py`) | ✅ complete |
| 0b | Persist per-turn trajectory risk | ⚠️ **incomplete — see §3** |
| 0c | Re-key goal-alignment latitude to certified topics | ✅ complete |

---

## 1. Data audit — the headline is that two sections have no data

`eval_00_data_audit.py` (new, offline, free) classifies accounts as synthetic or
possibly-real by generator prefix, embedded hex, and this repo's manual test conventions.
It never silently decides an account is a participant — anything unmatched is listed
individually, because over-counting here would license writing a section with nothing
behind it.

### Participant inventory

- total turns: **2007**
- synthetic / test accounts: **321** (2003 turns)
- possibly-real accounts: **4** (**4 turns**) — `f202_8b998`, `f202_856dc`, `f202_cf4e1`,
  `anonymous`

Three of those are single-turn fragments from one day; `anonymous` is not an account.

### §IV-H — retention and decertification: **NOT FEASIBLE**

§IV-H needs certifications that later decayed below θ_dec and were decertified, plus
retention intervals long enough for Eq. (17) to act. Both need real elapsed calendar time,
which a replay harness cannot manufacture.

| | |
|---|---:|
| certification events logged | **0** |
| decertification events logged | **0** |
| rows with `ever_certified=1` | 25 |
| rows with `is_certified=1` | 25 |
| longest per-account activity span | **23 days** (`TEST10`) |

No account shows both decertification events and a multi-week span. **§IV-H cannot be
written from this database.** Either the beta ran on a deployed instance whose database has
not been merged here, or it has not run. This is decision **D3** in the roadmap and it is
still open — it is the largest single risk to the paper.

### §IV-F — model calibration: **INSUFFICIENT**

85 `prediction_log` rows, 39 of them from possibly-real accounts. A calibration curve on 39
paired prediction/outcome records from test accounts is not a Brier-score claim. Label
§IV-F as requiring classroom deployment.

### Result-file staleness

Staleness is judged against a curated `CODE_CHANGELOG` inside the script, **not** file
mtimes. An earlier mtime-based version flagged 9 files, including eval_05 against a
`bkt_model` change it never touches and eval_06 against a telemetry-only edit. Curation
brought that to the 5 genuinely-stale files that Phase 1 then re-collected.

The rule that matters going forward: **when you make a behaviour-affecting change, add a
changelog entry.** A missed entry puts a number from a dead system into a table; an
over-eager one only costs a re-run.

---

## 2. 0c — goal-alignment latitude re-keyed  ✅

`sentinel.py`. The latitude a security-sensitive query is granted was keyed on the number
of topics *touched*, which meant latitude was bought with engagement rather than mastery.
Re-keyed to the certified set:

```python
GOAL_TAU_BASE  = 0.85
GOAL_TAU_ALPHA = 0.05
GOAL_TAU_FLOOR = 0.60          # added in this change
n_cert = SELECT count(*) FROM user_knowledge WHERE username=? AND is_certified=1
adaptive_threshold = max(GOAL_TAU_FLOOR,
                         GOAL_TAU_BASE - GOAL_TAU_ALPHA * math.log(1 + n_cert))
```

The floor is not cosmetic: a linear α of 0.05 would drive the threshold **negative after 17
certified topics** of the 26 in the curriculum, disabling the check entirely for the
learners furthest along. The logarithmic form plus the floor is the fix.

**Paper consequence:** §III-F1 currently describes the old mechanism — a linear per-topic
slope on topics touched, applied to the wrong layer. Replacement text is Phase 2 item 3.

---

## 3. 0b — per-turn trajectory risk  ⚠️ incomplete, corrected in Phase 1

The intent was to snapshot `traj_risk` / `traj_acc` / `traj_peak` on **every** turn, not
only blocked ones, so the trajectory layer's contribution becomes measurable rather than
inferable. Schema, sentinel, `_state`, `log_turn` and `turn_log` were all wired, and the
migration ran.

**It did not achieve that.** Phase 1 found the orchestrator has nineteen early-return paths
and only the Socratic path attached the state payload, so:

```
was_blocked=0   n=970   empty mastery_level=0
was_blocked=1   n=885   empty mastery_level=885     ← 48% of all stored turns
```

`traj_risk` was NULL for precisely the turns where the defense fires. The Phase 0 change
was necessary but not sufficient; the fix landed in Phase 1 (state injection on every
terminal event, block signal moved to an explicit flag). Coverage is now 100% on new runs.

Recorded here rather than quietly amended, because the failure is instructive: the
instrumentation was verified to be *present* but never verified to be *populated on the
path that mattered*.

---

## 4. What Phase 0 contributes to the paper

Very little as results, which was expected:

1. **§III-F1 restatement** — the α mechanism, now keyed on earned mastery (Phase 2 item 3).
2. **§III-I** — one sentence adding `traj_risk`/`traj_acc`/`traj_peak` to the per-turn
   telemetry record. This is what later made Phase 1's attribution table computable.
3. **§IV-F relabel** — 39 paired records, all from test accounts; requires classroom
   deployment.
4. **§IV-H decision** — cannot be written without locating or running the beta.

**Not for the paper:** the audit itself, the staleness table, the changelog. Those are
internal QA. Their value was telling you which existing numbers to stop trusting.

---

## 5. Reproduce

```bash
python IRL_extension_script/eval_00_data_audit.py
python IRL_extension_script/eval_00_data_audit.py --list-accounts
```

Offline, free, no server. Outputs `eval_00_data_audit.md` and `eval_00_staleness.csv`.
0b and 0c are code changes; they take effect on server restart.
