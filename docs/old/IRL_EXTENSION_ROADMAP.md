# IRL Extension — master roadmap

The single index for the journal extension. Every other document is linked from here.
Written 2026-07-31 because Phases 3–5 existed only in conversation, and one plan item
("item 7") had already been lost that way.

**Confidence markers.** ✅ verified against code or written to a file. ⚠️ reconstructed
from a review pasted into conversation and no longer available verbatim — the label is
right, the detail may not be. ⛔ blocked, with the blocker named. Treat ⚠️ items as prompts
to re-check against the reviewer's actual text, not as the text itself.

---

## Documents

**One report per phase**, each self-contained:

| Report | Phase | Contains |
|---|---|---|
| `IRL_extension_results/PHASE0_REPORT.md` | 0 | instrumentation + data audit; why §IV-H and §IV-F are blocked |
| `IRL_extension_results/PHASE1_REPORT.md` | 1 | what was re-collected, the numbers, what is safe to quote — ⚠️ multi-turn figures superseded, banner points to Phase 5 |
| `IRL_extension_results/PHASE2_REPORT.md` | 2 | the 12 correctness findings, the two open decisions — ⚠️ containment figure superseded, banner points to Phase 5 |
| `IRL_extension_results/PHASE3_REPORT.md` | 3 | §V drafted; proof the trial has not run; Option A/B decision |
| `IRL_extension_results/PHASE4_REPORT.md` | 4 | M2/M3 paragraphs, SM-2 cite, Fig. 4 bugs, style pass — ⚠️ `tab:multiturn` numbers superseded, banner points to Phase 5 |
| `IRL_extension_results/PHASE5_REPORT.md` | 5 | **current multi-turn numbers live here**: slow-burn fix, held-out 25, 3-run variance, both ablations |

**Multi-turn containment, current (2026-08-02):** core **100%** (3/3 runs), held-out 25
**96%** mean [92–100]. All pre-fix figures (`14/15`, `93%`, `62/63`) in Phases 1/1B/2/4 carry
supersession banners. Pull paper numbers from `PHASE5_REPORT.md`.

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
| 1 | Re-collect against current code | ✅ complete; re-judged on gpt-4o 2026-08-01 (`PHASE1B_GPT4O_REJUDGE.md`) |
| 2 | Paper correctness pass | ⏳ prepared — 10 items ready to paste, 2 decisions open |
| 3 | Write the missing sections | ⏳ §V drafted; §IV-H/§IV-I blocked — trial has not run |
| 4 | Reviewer response + style | ⏳ ready: SM-2, Fig.4, style. M2/M3 partly in `.tex` already. 🔴 `tab:multiturn` holds stale numbers. M4/M6/m1/m2/m5 need review text |
| 5 | Strengthening experiments | ⏳ ablation ✅, slow-burn fix ✅ (93%→100%), re-ablation ✅ (null flipped 100%→80%, but McNemar **p=0.25** at n=15), **held-out 25 ✅ (25/25=100% [87,100]; fix generalises to unseen slow-burns, but X11–X14 contained incidentally not by security — PHASE5 §2)**. **3 runs ✅ (core 100% 3/3; held-out 96% mean [92–100], nondeterministic — the single-run 100% was optimistic, PHASE5 §3)**. **ext ablation ✅ (96%→88%, but only X05 cleanly layer-attributable; single run, noisy — PHASE5 §3c)**. Open: human rater (needs a real 2nd person) |

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

**D4. RESOLVED — all judged figures are now `gpt-4o`.** Credits restored 2026-08-01;
eval_02 and eval_04 re-judged. Gemini results are retained alongside in `PHASE1_REPORT.md`
for comparison. Publish figures: pedagogy **+1.68 pooled / +2.22 taught**, curriculum
compliance **100%**, containment **14/15 = 93%**. A defect in the delivery-judge prompt was
found and fixed in the process — `PHASE1B_GPT4O_REJUDGE.md` §3′.

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
| M2 | Parameter misspecification sweep | ✅ **paragraph ready** — `eval_05 --jitter` already exists; aggregated, the factored rule false-certifies **2/16000** across 8 parameterizations vs canonical 15813/16000. PHASE4_REPORT §1. |
| M3 | Attribution — which layer does the work | ✅ **paragraph ready** — Table 6: all 7 semantic-judge blocks at risk 0.50–0.80, all 18 per-message blocks at 0.000. The old elimination argument is superseded by direct measurement. PHASE4_REPORT §2. |
| M4 | A limitation the paper does not concede | ⛔ detail not recorded — see PHASE4_REPORT §6 |
| M6 | A direct question to answer | ⛔ detail not recorded |
| m1, m2, m5 | Minor items | ⛔ detail not recorded |
| — | Style pass | ✅ **done** — "Crucially" ×9 at lines 153/391/507/570/664/747/803/935/967; "viz." ×2 at 148/464 |
| — | SM-2 citation missing | ✅ **done** — line 696, bibentry supplied |
| — | Fig. 4 float | ✅ **done** — two bugs: `[H]` with `float` commented out (line 29), and a two-panel figure at `\columnwidth`. Use `figure*`. |

🔴 **`tab:multiturn` (lines 875–885) reports the 2026-07-26 collection.** Two rows moved
materially: adversarial turns deflected 60/63 → **62/63**, sessions with no harmful delivery
12/15 → **14/15**. A third, "gate misses recovered at generation", goes 3/5 → **39/40** but
also changes meaning. PHASE4_REPORT §6a. This is a results table in the paper carrying
numbers from a system that has since changed — fix before anything else in Phase 4.

⚠️ **M4, M6, m1, m2, m5 need their content restored from the original review.** Paste it
back and I will fold the detail in here.

⚠️ **The `.tex` is ahead of the PDF the verification record was built from.** Phase 2 items
1, 2, 5, 6 and the D1 guardrail are already fixed in source. The Phase 2 checklist needs
re-verification against the `.tex` before being worked through.

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
