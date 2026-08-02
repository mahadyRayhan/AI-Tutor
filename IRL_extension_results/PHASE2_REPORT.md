# Phase 2 — paper-correctness pass

_Run 2026-07-31. Index: `docs/IRL_EXTENSION_ROADMAP.md`._

> ⚠️ **The `14/15 = 93%` containment figure below is SUPERSEDED (2026-08-02).** Current:
> **core 100% (3/3 runs), held-out 96% mean [92–100]** after the Phase 5 slow-burn fix.
> See `PHASE5_REPORT.md` §1a/§2/§3. Curriculum-compliance 100% is unaffected.
_Working document with paste-ready replacement text: `docs/PHASE2_CHECKLIST.md`._
_Item 12 figures updated 2026-08-01 for the gpt-4o re-judge; both judges retained._

> **Judged figures for item 12** (§IV wording), gpt-4o as publish, Gemini retained:
> pedagogy **+1.68 pooled / +2.22 taught** (Gemini +2.28 / +1.96); curriculum compliance
> **100%** under both; containment **14/15 = 93%** under both, once a defect in the
> delivery-judge prompt was corrected. Item 12 gains a fifth bullet — the prompt defect and
> the post-correction judge agreement make a measured threats-to-validity statement.
> See `PHASE1B_GPT4O_REJUDGE.md`.

Phase 2 makes Section III describe the system that is actually deployed. Every equation,
constant and numerical claim in §III–IV was re-checked against the code as it stands
**after** the Phase 0 and Phase 1 changes — not against the earlier verification pass,
because several constants moved in between.

---

## 0. Status

| | |
|---|---|
| Items identified | **12** (10 from the verification record, 2 added by Phase 1) |
| Verified against current code | ✅ all 12 |
| Replacement text written | ✅ 10 prose items |
| Applied to the manuscript | ❌ not done by design — this report states where and what; you apply it |
| Open decisions | **2** (items 4 and 7) |
| Code changed in Phase 2 | **none** — the only code-touching items are the two decisions |

The paper exists in this repo only as `Additional_files/IRL_Extension.pdf`. Phase 2's
deliverable is therefore the exact text to paste, not an edited manuscript.

---

## 1. The headline finding

**§III-G contradicts the paper's own central contribution.**

The paper states the three pre-certification levels partition the *composite estimate of
Eq. (15)* at cutoffs 0.35 and 0.75. Eq. (15) is a weighted sum — compensatory by
construction. Partitioning it is precisely the pooling that the Evidence Diversity Problem
is named for and that Theorem 1 forbids, applied inside the section that exists to
eliminate it.

The deployed rule is not that. It partitions `min_k P̃⁽ᵏ⁾` with evidence-count conjuncts.

Re-derived by executing both rules against the deployed `EVIDENCE_CONFIG` ceilings
(quiz 0.60, micro 0.25, code 0.10):

| archetype | P̄ (Eq. 15) | min P̃ | paper's rule | deployed | |
|---|---:|---:|---|---|---|
| novice | 0.194 | 0.01 | NOVICE | NOVICE | |
| developing | 0.598 | 0.40 | DEVELOPING | DEVELOPING | |
| proficient | 0.835 | 0.82 | PROFICIENT | PROFICIENT | |
| **lopsided** (0.95/0.75/0.55) | **0.812** | **0.55** | **PROFICIENT** | **DEVELOPING** | ← |
| inverse_lopsided | 0.613 | 0.55 | DEVELOPING | DEVELOPING | |

The single disagreement is the archetype the whole paper is about, and it goes the wrong
way: a quiz-strong, code-weak learner is handed *reduced* scaffolding under the rule as
printed.

A third divergence, smaller: the composite's attainable range is [0, 0.95], so a cutoff
printed as 0.75 actually sits at **78.9%** of range.

**The code is right and the prose is wrong**, so fixing this strengthens the paper — the
conjunctive adaptation rule is a second instance of the thesis, currently presented as a
violation of it. This is the one error a reviewer who understood Theorem 1 would catch
immediately.

---

## 2. What re-verification changed

Three findings differ from the earlier verification record. All three were re-read from
code on 2026-07-31.

**Eq. (19) — the record was half right.** It reported the code implements
`1[conf ≥ θ_conf ∧ incorrect]`. In fact `user_knowledge_manager.py:268` applies the
indicator as `1.0` *unconditionally*; the confidence gate lives at the call site,
`examiner.py:372`:

```python
if confidence_score >= 4:          # self-reported 1–5 Likert  → θ_conf = 4
    knowledge_manager.store_misconception(...)
```

So **θ_conf = 4 on a five-point self-report scale** — a value the paper needs and that was
not recorded anywhere before this pass.

**§III-F1 α is already fixed in code** (Phase 0c). Only the prose is outstanding, which
downgrades it from a code-and-prose item to a prose item.

**Cognitive-load guardrail — confirmed still absent.** `_cognitive_load` has exactly two
references backend-wide: its definition and one call feeding the instructor dashboard.
Nothing in the response path reads it.

---

## 3. All 12 items

| # | Item | Kind | Verified at |
|---|---|---|---|
| 1 | §III-G partitions the composite, not `min_k P̃` | prose | `cot_rag_agent::_classify_mastery_level` |
| 2 | Eq. (9) prints λ = 0.8; code uses λ_peak = 0.9 | prose | `sentinel.py:30-31` |
| 3 | §III-F1 α — wrong variable, form and layer | prose | `sentinel.py:44-46, 433-441` |
| 4 | Cognitive-load guardrail claimed but absent | **decision** | `learner_model.py:59, 260` |
| 5 | Eq. (19) missing confidence conjunct; θ_conf = 4 | prose | `examiner.py:372` |
| 6 | Eq. (16) θ_cert is calibrated ≥ 0.95, not constant | prose | `bkt_model.py:405-407` |
| 7 | REVIEWING keys on `ever_certified`, not `is_certified` | **decision** | `cot_rag_agent.py:1149` |
| 8 | §III-F fixed points are for the *unfactored* map | prose | reproduced numerically |
| 9 | Table III DEVELOPING under-described | prose | `socratic.py:459-491` |
| 10 | Theorem 1(i) is definitional → corollary | prose | holds by `N_min` construction |
| 11 | τ_block never fires | prose | Phase 1, `eval_04_report.md` |
| 12 | §IV result wording (density, pedagogy, judge, threats) | prose | Phase 1, 1b |

Nothing in items 1–10 is a bug in the system. The learner model verified **exact** — Eqs.
12–17, all 21 Table II values, Corollary 1 numerics, Lemma 1, Theorem 1(ii). These are
prose-vs-code drift.

---

## 4. The two open decisions

### D1 — cognitive-load guardrail (item 4)

§III-G claims Π is overridden when Eq. (18) load is elevated. That override does not exist.
Eq. (18)'s formula is correct and verified; only its claimed *use* is fictional.

**Recommendation: remove the claim.** Implementing means adding an untested behavioural
path to the response pipeline and then owing an evaluation that it does something — new
work immediately before submission, for a mechanism no current result depends on. Eq. (18)
still earns its place as an instructor-facing diagnostic.

### D2 — REVIEWING ↔ K (item 7)

`ever_certified` is sticky, set once and never reset. So a **decertified** topic keeps
REVIEWING treatment: the system stops teaching a learner whose mastery has demonstrably
lapsed. Beyond contradicting §III-G's "exactly when t ∈ K", this means **§III-H's decay and
decertification mechanism has no behavioural consequence at all** — its only effect is a
database flag. That materially weakens the section.

**Recommendation: split the two uses of the flag** — they are already separate code paths.
Prerequisite gating keeps `ever_certified` (sound: decay should not re-lock a prerequisite
and trap the learner); the REVIEWING *response level* moves to `is_certified`, so decay has
a visible pedagogical consequence. Two lines in `_classify_mastery_level`, and it makes the
paper's own decay story true.

The cheaper alternative is to restate the prose as "entered when t has ever been certified"
and accept that decertification does not affect adaptation.

---

## 5. What remains

| | Owner |
|---|---|
| Paste 10 items of replacement text into the manuscript | you — no `.tex` here |
| Decide D1 and D2 | you |
| Implement the two-line change for D2, if chosen | me |
| Restore reviewer text for M4, M6, m1, m2, m5 | you (not recoverable from repo) |

**Estimated effort for the prose items:** the six mechanical ones (2, 3, 5, 6, 8, 10) are
roughly an hour together. Item 1 needs care because it rewrites a paragraph that carries
the paper's argument. Items 9, 11 and 12 need the section lists and Phase 1 numbers, all of
which are in the checklist.

**Not in scope for Phase 2, still blocking overall:** §IV-H has no data (roadmap D3). No
amount of prose fixing changes that.

---

## 6. Known gap

The earlier plan's **"item 7, all three parts"**, described as a prerequisite for drafting
§IV-H, was never written to a file and did not survive context compaction. It is not in the
verification record and is not recoverable from this repo. Since §IV-H is blocked on D3
regardless, it is not currently on the critical path.
