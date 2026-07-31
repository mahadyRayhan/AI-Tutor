# IRL Extension — evaluation suite

How to reproduce every result in the paper. Six scripts, four result families, plus a
data-availability audit that runs first.

_Last verified 2026-07-30 (Phase 1). Current state of every result:
`IRL_extension_results/PHASE1_REPORT.md`._

| # | Result family | Script(s) | Metrics |
|---|---|---|---|
| 0 | Data audit / staleness | `eval_00` | what data exists, which stored results are stale |
| 1 | C-EduBench quality | `eval_01`, `eval_02`, `eval_03` | code density (**lower** is better), security compliance, curriculum compliance, pedagogy score, win rate |
| 2 | Multi-turn jailbreak | `eval_04_multiturn_jailbreak` | containment / ASR, escalation recall, false-block rate, defense depth, **trajectory attribution** |
| 3 | Evidence Diversity Problem | `eval_05` | false certification of lopsided learners |
| 4 | Mastery-based adaptation | `eval_06` | Π-fidelity, level separation, weak-tier ID |

Code density is lower-is-better (less spoon-feeding). Pedagogy, security and curriculum
compliance are higher-is-better.

---

## Prerequisites

| Need | For | Notes |
|---|---|---|
| Backend running | any `--collect` phase | `uvicorn app.main:app --reload` |
| `GOOGLE_API_KEY` | the backend itself | response generation is Gemini |
| `OPENAI_API_KEY` | any `--judge` phase | judging is a separate account from generation |

Both keys live in `.env`. **The server generating fine tells you nothing about whether
judging will work** — they are different providers. Phase 1 hit exactly this: collection
succeeded while every judge call returned `429 — You have no credits remaining`.

### Judge provider

The judge is an instrument, so which model produced a verdict is recorded next to it
(`judge_model` column). Default is `gpt-4o`; publish on that.

```bash
# default — no env var needed
python IRL_extension_script/eval_02_sage_delta.py --judge

# fallback when the OpenAI account is dry
JUDGE_PROVIDER=gemini python IRL_extension_script/eval_02_sage_delta.py --judge --fresh
```

`gemini-flash-latest` reaches the same Chat Completions API through Google's
OpenAI-compatibility endpoint, so rubrics and parsing are unchanged. Two caveats:

- It spends output tokens on internal reasoning and needs `max_tokens=800`; the factory
  sets this per provider. At the original 120 it returns truncated JSON and **every
  verdict silently becomes `None`**.
- **Never use it for eval_03.** `gemini_raw` and `gemini_tutor` are two of the five
  systems being ranked, so a Gemini judge scores its own family.

---

## 0. Data audit — run this first

```bash
python IRL_extension_script/eval_00_data_audit.py
python IRL_extension_script/eval_00_data_audit.py --list-accounts
```

Offline, free. Answers two questions before you write anything: does real participant data
exist (for §IV-H / §IV-F), and were the stored results produced by the code deployed now.
Writes `eval_00_data_audit.md` and `eval_00_staleness.csv`.

Staleness is judged against a curated `CODE_CHANGELOG` inside the script, not file mtimes.
**When you make a behaviour-affecting change, add an entry.** A missed entry puts a number
from a dead system into a table; an over-eager one only costs a re-run.

---

## 1. C-EduBench quality

### 1a — CIs on the published conference results

```bash
python IRL_extension_script/eval_01_cedubench_ci.py
```

Offline, free, no server. Also validates the instruments (`is_refusal`, `code_density_pct`)
against published Table I values.

### 1b — replay the 50 questions through the current system

```bash
python IRL_extension_script/eval_02_sage_delta.py --collect --fresh   # server; ~5 min
python IRL_extension_script/eval_02_sage_delta.py --judge --fresh     # $$ ~120 calls
python IRL_extension_script/eval_02_sage_delta.py --score             # free
```

Order matters — `--score` reads what `--collect` and `--judge` wrote. Run `--score` last,
not in the middle.

⚠️ **Known confound, unresolved.** `--collect` uses a fresh account, which has nothing
certified, so the prerequisite gate fires on 23 of 50 items (all 10 Boundary, 9 of 15
Problem) and returns a roadmap instead of an answer. Roadmaps contain no code, so **89% of
the code-density improvement is the gate, not less spoon-feeding**. Pedagogy survives the
same stratification (+1.96 restricted to taught responses); code density does not. See
PHASE1_REPORT §2 for the decomposition and three options. Do not quote the pooled
code-density delta without it.

### 1c — win rate

```bash
python IRL_extension_script/eval_03_winrate_conference_protocol.py --both --fresh   # $$ ~500 calls
```

⚠️ **Deliberately not run in Phase 1.** It replays the same 50 questions on a fresh
account, so it inherits the gating confound above — a judge ranking a prerequisite roadmap
against a full GPT-4 answer scores the roadmap as a loss. It is the most expensive script
in the suite and as configured it would manufacture a spurious win-rate drop. Settle the
protocol question first. gpt-4o only (see judge-provider note).

---

## 2. Multi-turn jailbreak

```bash
python IRL_extension_script/eval_04_multiturn_jailbreak.py --fresh --judge   # collect + judge
python IRL_extension_script/eval_04_multiturn_jailbreak.py --judge           # judge cached sessions
python IRL_extension_script/eval_04_multiturn_jailbreak.py --report-only     # free, regenerates
```

23 sessions (15 attack, 8 benign, 1 hard-benign), ~96 turns, ~10 min to collect.

Outputs, beyond the report: `eval_04_risk_series.csv` (per-turn trajectory risk) and
`eval_04_threshold_sweep.csv` (escalation rate vs τ). These feed **Table 6 — trajectory
attribution**, which is the strongest result in the suite and only became computable once
blocked turns started persisting `traj_risk`.

Requires per-turn telemetry. If `traj_risk` is NULL, Table 6 degrades to a "no telemetry"
note rather than failing — check the coverage line in the report (it should read 100%).

---

## 3. Evidence Diversity Problem

```bash
python IRL_extension_script/eval_05_evidence_diversity.py
```

Offline, free, no server, ~2 min. Pure simulation — unaffected by server state or judges.

Includes a `--jitter` misspecification sweep, which is the answer to the reviewer's
parameter-sensitivity request (M2); it already exists, so don't rebuild it.

---

## 4. Mastery-based adaptation

```bash
python IRL_extension_script/eval_06_mastery_adaptation.py --policy            # offline, free
python IRL_extension_script/eval_06_mastery_adaptation.py --seed              # writes 6 learners
python IRL_extension_script/eval_06_mastery_adaptation.py --collect --fresh   # server; ~20 min
python IRL_extension_script/eval_06_mastery_adaptation.py --score             # free — CHECK THIS
python IRL_extension_script/eval_06_mastery_adaptation.py --judge --fresh     # $$ 160 calls
python IRL_extension_script/eval_06_mastery_adaptation.py --report
```

**Stop at `--score` and confirm two things before paying for the judge:** classifier
integrity OK on all six archetypes, and manipulation check 0/120. Both have failed
silently before and both invalidate the judge run.

`--seed` writes BKT state directly, in one transaction. If it fails with `disk I/O error`,
pause OneDrive sync and re-run — nothing is partially written.

Tier steering was **not supported** (χ² p=1.0) and `eval_06_judge_tier.csv` was not
re-judged in Phase 1; paying to re-confirm a negative buys nothing. Report the existing
finding as a limitation.

---

## Five things that have actually gone wrong

1. **Stale judge verdicts silently reused.** Verdicts key on `query` / `(id, turn)`, which
   survive a re-collection even though the response changes completely. This reproduced an
   old pedagogy mean and an old containment rate *to the digit*. Both scripts now store a
   SHA of the judged response plus the `judge_model`, and drop any verdict whose input or
   model changed. Verdict files predating the guard are rejected wholesale. **Nothing to
   do — but if you see "dropped N verdicts", that is the guard working, not an error.**

2. **Two scripts write to the same path.** `eval_04_multiturn_jailbreak.py` produced every
   result; `eval_04_multiturn_redteam.py` is a superseded draft writing to the same
   `eval_04_sessions.json`. Running the wrong one silently overwrites. Delete it.

3. **Re-collecting without `--fresh` resumes from cache.** This is what mixed old and new
   responses in an earlier run. Use `--fresh` whenever the system has changed.

4. **Result files vanish.** Several disappeared mid-session, most likely OneDrive.
   `--policy`, `--score` and `--report-only` regenerate for free; `--collect` and `--judge`
   do not. `IRL_extension_results/` is still not in git — worth fixing before the deadline.

5. **The server can serve stale code.** `--reload` over OneDrive is racy, so a run can hit
   a mix of old and new code. Before any collection that matters, confirm the deployed
   behaviour directly — e.g. that a blocked turn persists `traj_risk` and that a novice
   response carries its level-specific section (`Visual Model`, `Your Turn`).

---

## Full reproduction, in order

```bash
# 0. what exists, what is stale
python IRL_extension_script/eval_00_data_audit.py

# 1. C-EduBench (server must be up)
python IRL_extension_script/eval_01_cedubench_ci.py
python IRL_extension_script/eval_02_sage_delta.py --collect --fresh
python IRL_extension_script/eval_02_sage_delta.py --judge --fresh
python IRL_extension_script/eval_02_sage_delta.py --score

# 2. multi-turn jailbreak
python IRL_extension_script/eval_04_multiturn_jailbreak.py --fresh --judge

# 3. evidence diversity
python IRL_extension_script/eval_05_evidence_diversity.py

# 4. mastery adaptation
python IRL_extension_script/eval_06_mastery_adaptation.py --policy
python IRL_extension_script/eval_06_mastery_adaptation.py --seed
python IRL_extension_script/eval_06_mastery_adaptation.py --collect --fresh
python IRL_extension_script/eval_06_mastery_adaptation.py --score      # gate: check before judging
python IRL_extension_script/eval_06_mastery_adaptation.py --judge --fresh
python IRL_extension_script/eval_06_mastery_adaptation.py --report

# 5. re-audit — every result should now read `current`
python IRL_extension_script/eval_00_data_audit.py
```

eval_03 is intentionally absent; see §1c.
