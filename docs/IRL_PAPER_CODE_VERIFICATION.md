# IRL_Extension.pdf ↔ deployed code — verification record

Every equation, constant, and numerical claim in Sections III–IV of `Additional_files/IRL_Extension.pdf` checked against the deployed implementation. Numerical claims were re-derived by executing the deployed functions, not by reading them.

**Verdict: the learner model (Eqs. 12–17, Table II, Lemma 1, Theorem 1, Corollary 1) is exact. Six discrepancies elsewhere, one of them serious.**

---

## A. Verified exact

### Eqs. (12)–(14) — per-tier BKT update
`backend/app/core/bkt_model.py::_bkt_step`

| paper | code | |
|---|---|---|
| Eq. 12 `P(1−p_S) / [P(1−p_S) + (1−P)p_G]` | `(p_l*(1-P_S)) / (p_l*(1-P_S) + (1-p_l)*P_G)` | ✅ |
| Eq. 13 `P·p_S / [P·p_S + (1−P)(1−p_G)]` | `(p_l*P_S) / (p_l*P_S + (1-p_l)*(1-P_G))` | ✅ |
| Eq. 14 transition on `e=1` only | `if is_correct: … + (1-p)*P_T`, else no credit | ✅ |

The asymmetry in Eq. (14) — the point Theorem 1(ii) rests on — is implemented as stated.

### Eq. (15) — composite display estimate
`Σ θ_max = 0.60 + 0.25 + 0.10 = 0.95` ✅ (`MASTERY_THRESHOLD = 0.95`, commented "Σ θ_max = 0.95").

### Eq. (16) — conjunctive certification
`bkt_model.py::check_certification`. All-tiers-≥-θ **and** all-tiers-≥`N_MIN` ✅. `θ_cert = 0.95`, `N_MIN = 3`, `θ_dec = 0.75` ✅. Hysteresis: decertify iff any tier < `θ_dec` ✅. Evidence counters increment **on correct only** (`+ (1 if is_correct else 0)`), matching "N_min *correct* observations" ✅.

### Eq. (17) — decay
`_apply_decay` implements `P̃·e^(−λΔt) + p_L0·(1−e^(−λΔt))` ✅. Half-lives re-derived from the deployed `DECAY_RATES`: `ln2/λ` = **14.0 / 7.0 / 5.0** days exactly ✅. `ease = max(ease_factor, 1.3)` ✅ (`EF ≥ 1.3`).

### Eq. (18) — cognitive load
`learner_model.py:73`: `0.4*cc_n + 0.3*lat_n + 0.3*err`, latency normalised by `20000` ms ✅ (20 s cap). *Formula correct; see D-3 for its use.*

### Table II — all 21 values
Match `EVIDENCE_CONFIG` exactly: guess 0.20/0.10/0.05, slip 0.10/0.15/0.20, prior 0.30/0.05/0.01, transition 0.09/0.09/0.09, half-life 14/7/5, ceiling 0.60/0.25/0.10 ✅.

### Corollary 1 — numerical claim
Executed `_bkt_step` from each tier prior, three correct observations:

```
declarative  0.30 → 0.689268 → 0.917137 → 0.982089   paper: 0.982  ✅
procedural   0.05 → 0.371273 → 0.848822 → 0.981324   paper: 0.981  ✅
applied      0.01 → 0.216609 → 0.832228 → 0.988677   paper: 0.989  ✅
```

### §III-F drift fixed points
Paper: "all-incorrect fixed points being 0.103, 0.108, and 0.114." These are **not** fixed points of the deployed map (which contracts to 0, per Theorem 1(ii)). They are the fixed points of the map with `p_T` applied **unconditionally** — the unfactored counterfactual:

```
declarative 0.102857   procedural 0.108000   applied 0.114000   ✅
```

Correct as written, but the sentence does not say which map it refers to. Add "under the unfactored update in which p_T is applied irrespective of outcome" or a reviewer will check it against Eq. (14) and find 0.

### Lemma 1 / Theorem 1
Consistent with the implementation. `ρ` is total and single-valued (`evidence_type ∈ {quiz, micro, code}`, one column each); no cross-tier term appears in the update; decay is per-tier with per-tier `λ_k`. Theorem 1(i) holds by construction of the `N_MIN` conjunct.

### Eqs. (10), (11) — session risk
`sentinel.py:76,78`: `acc = min(1.0, 0.8*acc + r_t)` ✅ and `risk = 0.5*peak + 0.5*acc` ✅.

### §III-G gate coupling
"REVIEWING relaxes Socratic withholding" and "opens the prerequisite gate": both verified at `cot_rag_agent.py:2126` and `:2170` ✅.

---

## B. CRITICAL — §III-G contradicts the paper's own contribution

> "…the three remaining levels partition the pre-certification range of the **composite estimate of Eq. (15)** at cutoffs of 0.35 and 0.75."

**The deployed rule is not this.** It partitions `min_k P̃^(k)` — the *minimum* over raw per-tier posteriors — and additionally requires evidence counts:

```python
p_min = min(p_q, p_m, p_c)
if p_min >= 0.75 and min(n) >= 2: return "proficient"
if p_min >= 0.35 or  max(n) >= 2: return "developing"
return "novice"
```

Three separate divergences in one sentence: (a) composite vs minimum, (b) no evidence-count conditions are stated, (c) the composite's attainable range is [0, 0.95], so a cutoff of 0.75 sits at 78.9% of range rather than 75%.

**Why this matters more than a transcription error.** Eq. (15) is a *weighted sum* — compensatory by construction. Partitioning it is exactly the pooling the Evidence Diversity Problem names and Theorem 1 forbids. On the paper's own headline archetype the two rules disagree, and they disagree in the direction the paper warns about:

| archetype | P̄ (Eq. 15) | min P̃ | paper's rule | deployed |
|---|---|---|---|---|
| novice | 0.194 | 0.01 | novice | novice |
| developing | 0.610 | 0.40 | developing | developing |
| proficient | 0.837 | 0.82 | proficient | proficient |
| **lopsided** (0.95/0.75/0.55) | **0.813** | **0.55** | **proficient** | **developing** |
| inverse_lopsided | 0.613 | 0.55 | developing | developing |

A quiz-strong, code-weak learner is handed *reduced scaffolding* under the rule as written. §III-E states "no choice of θ_max can admit a topic to K" — true for certification, but §III-G then routes adaptation through those same ceilings.

**Fix:** restate §III-G over `min_k P̃^(k)` with the evidence-count conjuncts, and note that the adaptation policy is conjunctive for the same reason certification is. This strengthens the paper — the code is right and the prose is wrong.

---

## C. Discrepancies in stated constants

### C-1. Eq. (9) — wrong decay constant
Paper: `peak_t ← max(λ peak_{t−1}, r_t)` … `with λ = 0.8`, shared with Eq. (10).

Code uses **two** constants:
```python
TRAJ_ACC_DECAY  = 0.8   # Eq. (10) ✅
TRAJ_PEAK_DECAY = 0.9   # Eq. (9)  ✗ paper says 0.8
```
The code comment gives the rationale ("peak cools over ~5-6 clean turns (usability guard)"), which is a defensible design choice — but Eq. (9) as printed is wrong. Use `λ_peak = 0.9`, `λ_acc = 0.8`.

### C-2. §III-F1 — α = 0.05 describes a different mechanism
Paper: "the latitude the Cognitive Access Control layer allows widens with the **proportion of certified topics**, at a **slope α = 0.05 per certified topic**."

Code (`sentinel.py:416-419`):
```python
mastery_count = SELECT count(*) FROM user_knowledge WHERE username=?
adaptive_threshold = 0.85 - 0.05 * math.log(1 + mastery_count)
```
Three mismatches: the count is **all topics touched**, not certified ones and not a proportion; the relation is **logarithmic**, not a linear per-topic slope; and it modulates the **goal-alignment security threshold** (`S_goal`), not CAC response latitude. Either restate as `τ = τ_base − α·ln(1+n)` with `n` defined as it is in code, or change the code to match the paper.

### C-3. Eq. (19) — indicator is under-specified
Paper: `M_c ← λ_M M_c + (1−λ_M)·1[e=0]`.
Code (`user_knowledge_manager.py:13`): `1[conf ≥ θ_conf AND incorrect]` — a **high-confidence** error, not any error. `λ_M = 0.7` ✅, `θ_M = 0.25` ✅, "two consecutive correct to clear" ✅.

As written the equation describes a general error-rate tracker; as built it tracks confidently-wrong answers, which is the more defensible construct. Add the confidence conjunct to the indicator.

### C-4. REVIEWING ≠ `t ∈ K`
Paper: "the REVIEWING level is entered **exactly when** t is certified (t ∈ K)", and §III-H states K is non-monotone — "a certified topic left unpracticed can decay below θ_dec and be decertified."

Code keys REVIEWING on `ever_certified`, a sticky flag set once and **never reset** (`bkt_model.py:372`, deliberate: "decay cannot re-lock earned prerequisites"). `is_certified` is the one that tracks K. So a decertified topic leaves K but keeps REVIEWING treatment — it stops teaching a learner whose mastery has demonstrably lapsed.

Either restate as "entered when t has ever been certified", or key adaptation on `is_certified`. These are different systems; pick one deliberately.

### C-5. Eq. (16) presents θ_cert as a constant
Deployed certification calls `calibrator.get_threshold(concept, tier)`, not the literal `THETA_CERTIFY`. The calibrator floors at `max(THRESHOLD_PRIORS[tier], 0.75)` with all priors = 0.95, so the effective threshold is **≥ 0.95** and can only tighten. Theorem 1 is unaffected (it quantifies over all parameter assignments), and the direction is conservative — but Eq. (16) should say `θ^(k)_cert ≥ 0.95, per-concept calibrated` rather than `θ_cert = 0.95`.

---

## D. Claims not implemented

### D-1. Cognitive-load guardrail on Π
Paper §III-G: "Π is overridden by a cognitive-load guardrail. When the instantaneous load estimate of Eq. (18) is elevated, the policy is suppressed toward plain-language explanation irrespective of the learner's mastery level."

`_cognitive_load` has exactly one consumer in the entire backend — the learner-model snapshot at `learner_model.py:260`, which feeds the instructor dashboard. **Nothing in the response path reads it.** The formula is correct (§A); the override does not exist.

Remove the claim, or implement it.

### D-2. Table III — DEVELOPING under-described
Table III gives DEVELOPING as "Moderate; builds on prior known concepts". The deployed template is more specific and more defensible: no analogies (analogy is novice-only), explanation by step-by-step mechanism trace, and a dedicated *Common Mistakes* section. Challenge type "multi-concept synthesis" ✅ matches. Worth updating — the current wording is the vague relative phrasing that made this level collapse into NOVICE before it was fixed.

### D-3. §IV placeholders
`[PLACEHOLDER: N = …]` remains in III-intro, IV-A (usability N, instructor N, IRB #), and the six-week beta (N = 10). Section cross-references render as `Section ??` throughout (§I, §II, §III-G, §IV-A). Both must be resolved before submission.

---

## E. Summary table

| item | status |
|---|---|
| Eqs. 12, 13, 14 | ✅ exact |
| Eq. 15 (Σθ = 0.95) | ✅ exact |
| Eq. 16 | ✅ exact (see C-5) |
| Eq. 17 + half-lives + EF | ✅ exact |
| Eq. 18 formula | ✅ exact (see D-1) |
| Eqs. 10, 11 | ✅ exact |
| Table II (21 values) | ✅ exact |
| Corollary 1 numerics | ✅ reproduced |
| §III-F drift fixed points | ✅ reproduced (clarify which map) |
| Lemma 1 / Theorem 1 | ✅ consistent |
| **§III-G partition rule** | ❌ **contradicts code and contradicts the paper's own thesis** |
| Eq. 9 (λ_peak) | ❌ 0.8 stated, 0.9 deployed |
| §III-F1 (α = 0.05) | ❌ different variable, different functional form, different layer |
| Eq. 19 indicator | ⚠️ missing confidence conjunct |
| REVIEWING ↔ K | ⚠️ `ever_certified`, not `is_certified` |
| Eq. 16 θ constant | ⚠️ calibrated, ≥ 0.95 |
| Cognitive-load guardrail | ❌ not implemented |
| Table III DEVELOPING | ⚠️ under-described |

**Priority: fix §III-G first.** It is the one error a reviewer familiar with the Evidence Diversity Problem would catch immediately, because it applies compensatory pooling inside the very section that exists to eliminate it.
