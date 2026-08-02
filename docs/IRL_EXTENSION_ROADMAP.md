# IRL Extension — master roadmap

The single index for the journal extension. Every other document is linked from here.
Written 2026-07-31 because Phases 3–5 existed only in conversation, and one plan item
("item 7") had already been lost that way.

**Confidence markers.** Items marked ✅ are verified against code or written to a file.
Items marked ⚠️ are reconstructed from a review that was pasted into conversation and is
no longer available verbatim — the label is right, the detail may not be complete. Treat
⚠️ items as prompts to re-check against the reviewer's actual text, not as the text itself.

---

## Documents

**One report per phase**, each self-contained:

| Report | Phase | Contains |
|---|---|---|
| `IRL_extension_results/PHASE0_REPORT.md` | 0 | instrumentation + data audit; why §IV-H and §IV-F are blocked |
| `IRL_extension_results/PHASE1_REPORT.md` | 1 | what was re-collected, the numbers, what is safe to quote |
| `IRL_extension_results/PHASE2_REPORT.md` | 2 | the 12 correctness findings, the two open decisions |
| `IRL_extension_results/PHASE3_REPORT.md` | 3 | §V drafted; proof the trial has not run; Option A/B decision |
| _(Phase 4–5 reports to follow)_ | 4–5 | — |

Supporting documents:

| Document | Contains |
|---|---|
| `docs/PHASE2_CHECKLIST.md` | Phase 2's working doc — paste-ready replacement text per item |
| `docs/IRL_PAPER_CODE_VERIFICATION.md` | every equation/constant in §III–IV checked against deployed code |
| `IRL_extension_results/eval_00_data_audit.md` | raw audit output behind the Phase 0 report |
| `IRL_extension_script/README.md` | how to reproduce every result |
| **this file** | phase order, open decisions, known gaps |

---

## Phase status

| Phase | What | State |
|---|---|---|
| 0 | Instrumentation + data audit | ✅ complete |
| 1 | Re-collect against current code | ✅ complete (judged on Gemini; gpt-4o re-run pending) |
| 2 | Paper correctness pass | ⏳ prepared — 10 items ready to paste, 2 decisions open |
| 3 | Write the missing sections | ⏳ §V drafted; §IV-H/§IV-I blocked — trial has not run |
| 4 | Reviewer response + style | ⚠️ partially reconstructed |
| 5 | Strengthening experiments | ⚠️ partially reconstructed |

---

## Open decisions — nothing downstream is safe until these are made

**D1. Cognitive-load guardrail** (Phase 2 item 4). §III-G claims Eq. (18) overrides Π.
It does not — `_cognitive_load` feeds only the instructor dashboard.
*Recommendation: remove the claim.* Implementing adds an untested behavioural path plus an
evaluation obligation, for a mechanism no result depends on.

**D2. REVIEWING ↔ K** (Phase 2 item 7). REVIEWING keys on sticky `ever_certified`, so a
decertified topic keeps REVIEWING treatment — and §III-H's decay mechanism therefore has no
behavioural consequence at all.
*Recommendation: split the flag* — prerequisites keep `ever_certified`, the REVIEWING
response level moves to `is_certified`. Two lines, and it makes §III-H true.

**D3. RESOLVED — the class trial has not run.** Phase 3 checked every telemetry table:
`assessment` 0 rows, decertification events 0, the only 2 certification events from a test
fixture, `calibration_log` 2, `jol_log` 1. `CLASS_DATA_COLLECTION_PLAN.md` is a pre-trial
plan with unticked checkboxes. §IV-H and §IV-I cannot be written.

**D3′. Submit before or after the trial?** The decision this forces.
*Option A* — cut §IV-H/§IV-I, reframe as a systems-and-simulation paper around the
factored learner model, Theorem 1, and the Phase 1 trajectory attribution result.
*Option B* — hold for a semester and write both sections from real data.
*Recommendation: A*, unless the trial is imminent. See `PHASE3_REPORT.md` §2.

**D4. Judge model.** All current judged numbers are `gemini-flash-latest` because the
OpenAI account is out of credit. Re-run on gpt-4o before submission, or name the judge in
the paper. Commands: PHASE1_REPORT §6.

---

## Phase 3 — write the missing sections  ⏳ partially done

Full detail and the §V draft: `IRL_extension_results/PHASE3_REPORT.md`.

- **§V — discussion.** ✅ **drafted**, written for Option A. Five subsections: what the
  evidence supports, what it does not, design implications, threats to validity, future
  work.
- §IV-H — retention / decertification over the six-week beta. ⛔ **trial has not run.**
- §IV-I — usability sessions. ⛔ **trial has not run.**
- Numeric placeholders — §III intro, §IV-A (usability N, instructor N, IRB #), six-week
  beta (N = 10). ⛔ not fillable; removed under Option A.
- `Section ??` cross-references — §I, §II, §III-G, §IV-A. ✅ **the only placeholders
  fixable now**; they are LaTeX `\ref` targets, not missing data.

Everything remaining here waits on **D3′**.

---

## Phase 4 — reviewer response and style

Reconstructed from the external review. Labels are reliable; detail marked ⚠️ should be
checked against the reviewer's actual wording before you rely on it.

| ID | Item | Status |
|---|---|---|
| M2 | Parameter misspecification sweep | ✅ **already exists** — `eval_05 --jitter`. Results were 0/2000, 1/2000, 0/2000, 0/2000. Do not rebuild it; write the paragraph pointing at it. |
| M3 | Attribution — which layer does the work | ✅ **answered by Phase 1**. Table 6: all 7 semantic-judge blocks at risk 0.50–0.80, all 18 per-message blocks at 0.000. |
| M4 | A limitation the paper does not concede | ⚠️ detail not recorded |
| M6 | A direct question to answer | ⚠️ detail not recorded |
| m1, m2, m5 | Minor items | ⚠️ detail not recorded |
| — | Style pass | ✅ "Crucially" ×9, "viz." ×2 — reduce |
| — | SM-2 citation missing | ✅ verified |
| — | Fig. 4 should be `figure*` | ✅ verified |

⚠️ **M4, M6, m1, m2, m5 need their content restored from the original review.** Paste it
back and I will fold the detail in here.

---

## Phase 5 — strengthening experiments

| Item | Why | Cost |
|---|---|---|
| Trajectory ablation: τ_judge = τ_block = ∞ | isolates the trajectory layer's contribution; the natural companion to Table 6 | one collection run |
| Expand red-team to ~40 attacks | n=15 gives CIs of ±20pp; 40 roughly halves them | collection + judging |
| 3 repeated runs | variance estimate; reviewers asked | 3× the above |
| Human-validate a judge subsample | second rater on ~20% of verdicts, report κ | recruit a rater during Phase 2 |

Phase 1 made the ablation cheaper than it was: `traj_risk` is now persisted on every turn
including blocked ones, so the counterfactual can be read from `eval_04_risk_series.csv`
for the *escalation* stage without re-collecting. Only the block-outcome arm needs a run.

---

## Housekeeping

- ☐ `IRL_extension_script/eval_04_multiturn_redteam.py` is a superseded draft that writes
  to the same `eval_04_sessions.json` as the real script. Delete it before it overwrites
  something.
- ☐ `IRL_extension_results/` is not in git. Result files have vanished mid-session twice,
  most likely OneDrive. `--collect` and `--judge` outputs cannot be regenerated for free.
- ☐ Add a `CODE_CHANGELOG` entry in `eval_00_data_audit.py` whenever you make a
  behaviour-affecting change — staleness detection depends on it.

---

## Known gap

The earlier plan's **"item 7, all three parts"**, described as a prerequisite for drafting
§IV-H, was never written to a file and did not survive context compaction. It is not in the
verification record. It is not recoverable from this repo. Supply it if you still have it;
otherwise §IV-H is blocked on D3 regardless.
