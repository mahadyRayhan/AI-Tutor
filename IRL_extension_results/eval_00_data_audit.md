# Eval 00 — data-availability audit

_Generated 2026-07-30T18:22:00 · `/Users/mhr6wb/Library/CloudStorage/OneDrive-UniversityofMissouri/Projects/AI-Tutor/backend/database/ai_tutor.db`_

## 1. Participant inventory

- total turns: **2007**
- synthetic / test accounts: **321** (2003 turns)
- possibly-real accounts: **4** (4 turns)

| account | turns | first | last |
|---|---:|---|---|
| `f202_8b998` | 1 | 2026-07-21 | 2026-07-21 |
| `f202_856dc` | 1 | 2026-07-21 | 2026-07-21 |
| `f202_cf4e1` | 1 | 2026-07-21 | 2026-07-21 |
| `anonymous` | 1 | 2026-07-23 | 2026-07-23 |

These are *unmatched by the synthetic patterns*, not confirmed participants. Inspect the list before treating any of them as study data.

## 2. Telemetry stream volume

| table | rows | rows from possibly-real accounts |
|---|---:|---:|
| `turn_log` | 2007 | 4 |
| `evidence_log` | 85 | 39 |
| `prediction_log` | 85 | 39 |
| `bkt_history` | 85 | 39 |
| `response_log` | 1559 | 4 |
| `calibration_log` | 2 | 2 |
| `jol_log` | 1 | 0 |
| `mcn_log` | 0 | (no username column) |
| `misconception_log` | 1 | 0 |
| `path_event` | 889 | 2 |
| `event_log` | 777 | 9 |
| `session_log` | 1318 | 4 |

## 3. Section IV-H feasibility — retention and decertification

§IV-H requires (a) certifications that later decayed below θ_dec and were decertified, and (b) retention intervals long enough for Eq. (17) to act. Both need real elapsed calendar time; neither can be produced by a replay harness.

- certification events logged: **0**
- decertification events logged: **0**
- rows with `ever_certified=1`: **25**
- rows with `is_certified=1`: **25**
- longest per-account activity span: **23 days** (`TEST10`)

**Verdict: §IV-H is NOT FEASIBLE from this database.**
No account shows both decertification events and a multi-week span. §IV-H cannot be written from local data. Either the beta ran on a deployed instance whose database has not been merged here, or it has not run. Resolve this before drafting §IV-H or §IV-A's participant counts.

## 4. Section IV-F feasibility — model calibration

- `prediction_log` rows: **85** (possibly-real accounts: **39**)

**Verdict: INSUFFICIENT for a Brier-score claim.** A calibration curve on 39 paired prediction/outcome records from test accounts is not reportable; label §IV-F as requiring classroom deployment.

## 5. Result-file staleness

A result is STALE if a change that alters system OUTPUT landed after it was collected. Judged against the curated change log, not file mtimes: mtime marks docstring edits and telemetry plumbing as breaking changes, which they are not. Telemetry additions (traj_risk/traj_acc/traj_peak persistence, response_log columns) are observational and do not change any answer the system gives.

| result                       | collected        | status   | invalidated_by                     | mtime_flag                      |
|:-----------------------------|:-----------------|:---------|:-----------------------------------|:--------------------------------|
| eval_02_new_responses.json   | 2026-07-30 02:41 | current  |                                    |                                 |
| eval_03_winrate_faithful.csv | 2026-07-26 13:39 | STALE    | 2026-07-27 22:38; 2026-07-28 00:25 |                                 |
| eval_03_winrate_debiased.csv | 2026-07-26 13:41 | STALE    | 2026-07-27 22:38; 2026-07-28 00:25 |                                 |
| eval_04_sessions.json        | 2026-07-30 17:45 | current  |                                    |                                 |
| eval_05_archetypes.csv       | 2026-07-30 18:16 | current  |                                    |                                 |
| eval_05_sweep.csv            | 2026-07-30 18:16 | current  |                                    |                                 |
| eval_05_robustness.csv       | 2026-07-30 18:17 | current  |                                    |                                 |
| eval_06_responses.json       | 2026-07-28 00:38 | current  |                                    | unlogged edit: cot_rag_agent.py |
| eval_06_judge_tier.csv       | 2026-07-27 22:58 | STALE    | 2026-07-28 00:25                   |                                 |

**3 result file(s) are stale** and must be re-collected before their numbers enter a table. Reasons, newest first:

- `2026-07-30 00:00` — sentinel.py: goal-alignment latitude re-keyed from topics touched to certified topics |K|, with a floor. Changes when a security-sensitive query is blocked. → invalidates eval_04
- `2026-07-28 00:25` — socratic.py: four structurally distinct level templates (was three, with novice/developing sharing one), analogies restricted to novice, level definitions restated as jobs. Changes response content, length and code density directly. → invalidates eval_02, eval_03, eval_06
- `2026-07-27 22:38` — cot_rag_agent._classify_mastery_level: scored entity resolution (was first-fuzzy-match), conjunctive min rule (was pooled mean), objective BKT (was self-blended P_eff). Changes the assigned mastery level, hence the response. → invalidates eval_02, eval_03, eval_04, eval_06
- `2026-07-27 22:11` — bkt_model._apply_decay: tz-aware timestamp normalisation. Affects any decayed posterior read; does NOT affect _bkt_step or EVIDENCE_CONFIG, so pure simulations are unaffected. → invalidates eval_06

Rows carrying `unlogged edit` had a dependency touched with no changelog entry. Confirm the edit was behaviour-neutral, or add it to `CODE_CHANGELOG`.

## 6. Per-turn `traj_risk` coverage

- turns with `traj_risk` recorded: **152/2007**
