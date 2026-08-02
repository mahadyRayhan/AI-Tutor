# Phase 4 — reporting gaps and reviewer response

_Run 2026-08-01. Index: `docs/IRL_EXTENSION_ROADMAP.md`._

Phase 4 closes the reporting gaps the external review raised, plus the style and formatting
items. This document states **where** in `Additional_files/IRL_extension.tex` to change
things and **what** to write. The `.tex` is not modified.

Line numbers are from the `.tex` as of 2026-08-01.

> ⚠️ **Re-verified against the `.tex` on 2026-08-01, and the picture changed.** The original
> Phase 4 pass was built from `IRL_Extension.pdf` (2026-07-29). The `.tex` is substantially
> newer and already contains part of what M2 and M3 asked for. More importantly it contains
> a **results table with pre-Phase-1 numbers** (§6a). Read §1a, §2a and §6a before acting on
> anything else here.

---

## 0. Status

| Item | State |
|---|---|
| M2 — parameter misspecification | ⏳ **partly already in the `.tex`** — one arm missing (§1a) |
| M3 — layer attribution | ⏳ **a different attribution argument already present** (§2a) |
| **Table `tab:multiturn` holds stale numbers** | 🔴 **NEW — must fix (§6a)** |
| SM-2 citation | ✅ located, reference supplied (§3) |
| Fig. 4 float | ✅ **two bugs found**, fix given (§4) |
| Style pass — "Crucially" ×9, "viz." ×2 | ✅ all lines listed (§5) |
| M4 — limitation | ⛔ **blocked** — review text not recorded |
| M6 — direct question | ⛔ **blocked** — review text not recorded |
| m1, m2, m5 — minor items | ⛔ **blocked** — review text not recorded |

Three items are fully ready (SM-2, Fig. 4, style). Two are partly pre-empted by the `.tex`
and need repositioning rather than drafting (M2, M3). One is newly discovered and is the
most urgent thing in this phase (`tab:multiturn`). Three are blocked on the reviewer's
original message, which was pasted into a conversation since compacted and is not
recoverable from this repo — **paste it back and they close quickly**.

---

## 1a. What the `.tex` already has for M2

Two pieces are already written:

- **§III-K (line 735)** states the general position on constants — that values are defended
  by lying in a principled range with stable conclusions across it, and points at
  `sec:eval-deferral` for the stability evidence. This is the framing M2 wanted.
- **Line 995** reports a real sweep over the transition rate: `P_T ∈ {0, 0.03, 0.09, 0.15}`,
  canonical certifying 9.0 / 27.4 / 48.9 / 56.0 % of uniformly weak learners, pooled
  2.1 / 21.0 / 40.1 / 46.2 %.

**What is missing:** the *guess/slip* arm, and the aggregate across both sweeps. The
paragraph in §1 below should therefore be positioned as **extending line 995**, not as a new
subsection — otherwise you will have two sensitivity discussions that do not reference each
other.

---

## 1. M2 — parameter misspecification  ✅ the experiment already exists

The reviewer asked whether the certification result survives misspecified parameters. It
does, and `eval_05 --jitter` already measures it. **Do not build a new experiment** — write
the paragraph and point at the existing table.

Aggregated from `eval_05_robustness.csv`, lopsided (recall-strong, code-weak non-master)
learners, 2000 per cell:

| Sweep | Perturbations | Factored false-certification |
|---|---|---:|
| guess/slip | 0.00, 0.15, 0.30, 0.45 | **1 / 8000** |
| transition rate `P_T` | 0.00, 0.03, 0.09, 0.15 | **1 / 8000** |
| **combined** | 8 parameterizations | **2 / 16000** |
| canonical rule, same cells | | 15813 / 16000 |
| pooled `N ≥ 9`, same cells | | 15776 / 16000 |

Theorem violations across all cells: **0**.

**Where:** §IV-C (over-crediting resistance), after the archetype table — around line 914,
where the certification-rate caption sits.

**What to write:**

> The guarantee is a property of the decision rule rather than of a fortunate
> parameterization. We perturb the two parameter families the result could plausibly depend
> on — the guess and slip rates, and the transition rate — over four settings each, and
> re-run every archetype at 2000 learners per cell. Across the eight resulting
> parameterizations the factored rule false-certifies recall-strong, code-weak learners
> twice in sixteen thousand trials, while the canonical rule certifies 15,813 of the same
> 16,000 and a quantity-matched pooled rule certifies 15,776. No configuration produced a
> violation of the certification inequality. The separation is therefore not sensitive to
> the parameter values in Table~\ref{tab:bktparams}; it follows from requiring evidence in
> every tier, which no parameter assignment can relax.

The last clause matters — it ties the empirical sweep back to Theorem 1 rather than leaving
it as a standalone robustness check.

---

## 2a. What the `.tex` already has for M3

**Lines 890–892 already contain an attribution paragraph** — but it makes a *different*
argument: that the access-control and generation layers fail on disjoint sets (three attacks
reached an unblocked payload turn and were refused at generation; one blocked attack had
disclosed an example earlier). That is a valid complementarity argument at the
gate-vs-generation level.

The Table 6 result below is a **second, sharper** attribution at the *enforcement-layer*
level — which Sentinel layer fired, and at what session risk. The two are compatible and
mutually reinforcing. Add the paragraph in §2 alongside the existing one rather than
replacing it; together they say the layers are complementary in two independent senses.

---

## 2. M3 — layer attribution  ✅ now measured, not argued

**The elimination argument is superseded and should be dropped.** The earlier defence of
attribution was indirect: all nine `AI Semantic Judge` blocks had responses of ≤15 words, so
`is_suspiciously_long` could not have fired, so trajectory risk must have been the trigger.
That reasoning was sound but circumstantial, and a reviewer could reasonably decline to
accept it.

Phase 1's per-turn risk telemetry replaces it with direct measurement. From
`eval_04_report.md` Table 6 — risk at the moment of each block:

| Blocking layer | blocks | median risk | min | max | all ≥ τ_judge? |
|---|---:|---:|---:|---:|:--:|
| Goal-Bounded Security | 10 | 0.000 | 0.000 | 0.000 | no |
| **AI Semantic Judge** | **7** | **0.661** | **0.503** | **0.797** | **yes** |
| Off-Topic Warning | 6 | 0.000 | 0.000 | 0.000 | no |
| Harmful Code | 2 | 0.000 | 0.000 | 0.000 | no |

**Where:** §IV-D (multi-turn enforcement), around line 861–868.

**What to write:**

> Because per-turn trajectory state is recorded on every turn, including those that end in
> a block, the contribution of each enforcement layer can be attributed directly rather
> than inferred. Of the twenty-five interceptions observed, the seven produced by the
> escalated transcript judge occurred at session risk between 0.50 and 0.80, every one of
> them above the escalation threshold; the eighteen produced by per-message layers occurred
> at a recorded risk of exactly zero, since those layers fire on the first turn, before any
> trajectory has accumulated. The layers are therefore complementary rather than redundant:
> the conversation-level net accounts for the interceptions that per-message filtering
> cannot reach, and for no others.
>
> Pooled across all turns the risk score does not separate attack conversations from benign
> ones (AUC 0.491). We report this because it is the correct summary of what a single-turn
> reading of the score can achieve, and because conditioning on the blocking layer explains
> it: the pooled statistic averages a conversation-level mechanism over turns at which that
> mechanism is definitionally inactive.

Reporting the unflattering pooled AUC alongside the conditioned result is what makes this
credible. Omitting it invites exactly the objection M3 raised.

---

## 3. SM-2 citation  ✅

**Where:** line 696. The text reads *"each successful retrieval updates an SM-2 ease factor
$EF \ge 1.3$ from a quality score"* — with no citation. SM-2 is a named published algorithm
and needs one.

**What to add** — the canonical reference:

```bibtex
@article{wozniak1994optimization,
  author  = {Wo\'{z}niak, Piotr A. and Gorzela\'{n}czyk, Edward J.},
  title   = {Optimization of repetition spacing in the practice of learning},
  journal = {Acta Neurobiologiae Experimentalis},
  volume  = {54},
  number  = {1},
  pages   = {59--62},
  year    = {1994}
}
```

Cite at first mention of SM-2 on line 696. The `EF \ge 1.3` floor is from the same
algorithm and is covered by the same citation — worth noting in the text that the floor is
SM-2's own, not a choice of ours, since it is otherwise another unexplained constant.

---

## 4. Fig. 4 — **two bugs**, one of which breaks compilation  ✅

**Where:** lines 982–992, `\label{fig:diversity}`, referenced at lines 961 and 976.

```latex
\begin{figure}[H]
\centering
\includegraphics[width=\columnwidth]{images/fig_evidence_diversity.png}
```

**Bug 1 — `[H]` without the `float` package.** Line 29 reads `% \usepackage{float}` — it is
**commented out**. The `H` float specifier is provided by that package; without it LaTeX
raises `Unknown float option 'H'` and falls back to default placement. Either uncomment the
package or stop using `[H]`.

**Bug 2 — a two-panel figure confined to one column.** The caption describes *"Left: false
certification … Right: proportion of true masters certified …"* — two panels — yet the
graphic is set at `width=\columnwidth` inside a single-column `figure` in a two-column
IEEEtran layout. Both panels are compressed into roughly 3.5 inches, which will be
unreadable in print.

**What to write:**

```latex
\begin{figure*}[t]
\centering
\includegraphics[width=\textwidth]{images/fig_evidence_diversity.png}
```

`figure*` spans both columns and takes `[t]`, which needs no extra package — fixing both
bugs at once. Note the paper already uses `figure*` correctly at lines 155 and 273, so this
is consistent with the existing style rather than a new convention.

This is the figure carrying the paper's headline result. It is worth getting right.

---

## 5. Style pass  ✅

### "Crucially" — 9 occurrences

Lines **153, 391, 507, 570, 664, 747, 803, 935, 967**.

Nine uses of a single emphasis adverb reads as a verbal tic and, worse, flattens emphasis:
if everything is crucial, nothing is. Keep at most one or two where the emphasis is doing
real work, and delete the rest — in most cases the sentence is stronger without any adverb,
because the claim carries its own weight. Where genuine contrast is intended, prefer a
construction that says *what* the contrast is ("Unlike the pooled rule, …") over an adverb
that only asserts importance.

### "viz." — 2 occurrences

Lines **148** and **464**. Archaic and frequently misused for *namely*. Replace with
"namely" or restructure:

- Line 148: *"materials, viz. answer keys or exam materials can be exposed"* → *"materials
  such as answer keys or exam papers can be exposed"*. (This sentence also repeats
  "materials" twice — worth fixing at the same time.)
- Line 464: *"its central idea, viz. that conversation-level risk should combine…"* → *"its
  central idea, namely that conversation-level risk should combine…"*

---

## 6a. 🔴 Table `tab:multiturn` holds pre-Phase-1 numbers

**Where:** lines 875–885, `\label{tab:multiturn}`.

The table reports the 2026-07-26 collection. Phase 1 re-collected these sessions and Phase 1b
re-judged them on gpt-4o with the corrected delivery prompt. Every row moved except two:

| Row | In the `.tex` | **Current** |
|---|---|---|
| Adversarial turns deflected ↑ | 60/63 (95.2%) | **62/63 (98.4%)** |
| Sessions with no harmful delivery ↑ | 12/15 (80.0%) | **14/15 (93.3%)** |
| Complete artifact delivered ↓ | 0/15 (0.0%) | **0/15 (0.0%)** — unchanged |
| Restricted-asset containment ↑ | 2/2 (100%) | **2/2 (100%)** — unchanged |
| Benign sessions never blocked ↑ | 8/9 (88.9%) | **8/9 (88.9%)** — unchanged |
| Gate misses recovered at generation ↑ | 3/5 (60.0%) | **39/40 (97.5%)** |

The last row changes meaning as well as value: with per-turn judging over all 63 attack
turns, 40 turns passed the gate unblocked and 39 of them delivered nothing harmful. The old
3/5 was computed over a much smaller set of identified gate misses. Decide which quantity you
want before pasting — the new one is stronger but is not the same measurement.

Source: `eval_04_delivery_judged.csv`, `eval_04_sessions.json` (both 2026-08-01).

⚠️ This is the single most important item in Phase 4: it is a results table in the paper
carrying numbers from a system that has since changed.

---

## 6. Blocked — need the reviewer's original text

| ID | What is known | What is missing |
|---|---|---|
| M4 | a limitation the paper does not concede | which limitation |
| M6 | a direct question to answer | the question |
| m1, m2, m5 | minor items | all detail |

These were pasted into conversation and lost to compaction. They are not in
`docs/IRL_PAPER_CODE_VERIFICATION.md` and not recoverable from the repo.

Worth noting: Phase 1–3 surfaced three candidate limitations the paper does not currently
concede, any of which may be M4 — τ_block never firing, the code-density gating confound,
and the absence of any classroom evaluation. All three now have drafted text (Phase 2 item
11, Phase 3 §V-B). If M4 turns out to be one of them it is already answered.

---

## 7. What remains

| | Owner |
|---|---|
| **Update `tab:multiturn` (§6a)** | you — highest priority |
| Paste §1–§5 into the `.tex`, positioned per §1a and §2a | you |
| Supply the reviewer text for M4, M6, m1, m2, m5 | you |
| Uncomment `float` **or** switch Fig. 4 to `figure*` | you (recommend `figure*`) |
| Add the SM-2 bibentry | you |

No code changes and no evaluation runs were needed for this phase — M2's experiment already
existed and M3's measurement came from Phase 1.
