# Paper Improvement Guide — SRL Conference & IRL Extension

Every recommendation below is grounded in the deployed code (file:line references included),
so nothing proposed here requires claiming something the system does not do. Items marked
**[NOVEL — SOTA]** are new ideas beyond the current implementation; each is designed so that
it can be added with small code changes *and* preserves the existing guarantees.

Priority legend: 🔴 must-do before submission · 🟡 strongly recommended · 🟢 optional polish

---

# PART A — SRL Conference Paper (`SRL_conference.pdf`)

The methodology (§III) currently carries the whole paper because §IV is placeholder.
These items turn §III from ~1.5 pages of mechanism into a full, reproducible methodology.

---

## A1. 🔴 Specify the elicitation protocol (§III-C, new subsection "Elicitation")

**The gap.** The paper never says when/how the learner expresses disagreement, what they see,
or how one interaction maps onto the three tier posteriors. This is the first question any
reviewer asks, and every answer already exists in code.

**What the code actually does** (write this into the paper):

| Design question | Deployed answer | Source |
|---|---|---|
| Where is the control? | Student dashboard, per-concept expandable card | `static/js/student_dashboard.js:193-260` |
| Granularity | **One slider per tier** (declarative / procedural / applied) — self-assessment is tier-specific, not a single global judgment | `student_dashboard.js:215-220` |
| What the learner sees | The tier's current BKT estimate as the slider's **maximum**; the learner can only drag left | `student_dashboard.js:254-255` (`slider.max` = current P_BKT) |
| Downward-only enforcement | **Client-side** (slider max) *and* **server-side**: `record_self_assessment` raises `ValueError` if `p_self >= p_bkt_current` | `core/srl_calibration.py:52-56` |
| When | Learner-initiated, any time, from the dashboard (not a forced prompt) | `POST /api/v1/mastery/self-assess`, `main.py:1400` |
| What is stored | `(username, concept, tier, p_self, n_adjustments, direction_ema, adapted_P_G)` | `user_bkt_calibration` table |
| What is returned | `p_bkt`, `p_self`, `p_effective`, whether Layer 2 fired | `srl_calibration.py:122-131` |

**Draft paper text:**

> *Elicitation.* Self-assessment is elicited through the learner-facing dashboard. Each
> topic exposes three sliders, one per cognitive tier, initialized at the model's current
> tier estimate. The estimate acts as the slider's upper bound: the learner may drag it
> down but not up, an asymmetry enforced both in the interface and by the server, which
> rejects any submission with P_self ≥ P_BKT. Self-assessment is therefore tier-specific —
> a learner may report "I can recall this but I could not code it" by lowering only the
> applied tier — and learner-initiated rather than prompted, so every event is a deliberate
> metacognitive act rather than compliance with a dialog.

**Why tier-specific elicitation matters (add one sentence):** it makes the self-assessment
*commensurable* with the factored model — the learner disagrees with a specific posterior,
not with an aggregate score, which is what allows Layer 2 to adapt the specific `P_G^(k)`.
This is a distinguishing detail vs. OLM work, where the learner contests one global skill
estimate.

---

## A2. 🔴 Justify P_G as the adaptation target (§III-C, one paragraph)

**The gap.** The paper adapts the guess parameter but never says why *that* parameter, and a
reviewer will ask "why not P_S, P_L0, or P_T?"

**The argument (semantic, and it is exactly what the code does):**

- A learner who says "the model over-credits me" is asserting: *my correct answers were not
  all demonstrations of knowledge* — i.e., some were lucky guesses. The BKT parameter that
  encodes "probability of a correct answer without knowledge" **is** P_G. The mechanism
  therefore translates the learner's claim into the model's own vocabulary, changing no
  other semantics.
- Adapting **P_S** (slip) would encode "my *wrong* answers didn't mean anything," which is an
  *upward* / self-serving signal — precisely what the design excludes.
- Adapting **P_L0 or P_T** would rewrite the learner's history or learning speed, an
  unbounded and retroactive effect. Raising P_G is *prospective only*: it re-weights future
  evidence and never edits the accumulated posterior (`srl_calibration.py` writes only
  `adapted_P_G`; posteriors in `user_concept_mastery` are untouched — the read happens in
  `bkt_model._get_user_params`, `bkt_model.py:145-157`).

**Draft paper text:**

> The guess rate is the unique BKT parameter whose meaning matches the learner's claim. A
> report of over-credited mastery asserts that past correct responses over-stated knowledge
> — that is, that they were more likely guesses — which is precisely the event P_G models.
> Adapting the slip rate would instead discount *errors* (an inflating signal we exclude by
> construction), and adapting the prior or learning rate would retroactively rewrite the
> learner's history. Raising P_G is prospective: it re-weights evidence not yet seen, and
> never edits a stored posterior.

---

## A3. 🔴 Bounded-delay corollary (§III-D, after Proposition 1)

**The gap.** Proposition 1 says self-report "can delay but never accelerate" certification.
*Quantify the delay.* This converts the qualitative guarantee into a number a reviewer can
check, and it directly answers the "isn't this mechanism too small to matter?" objection —
in both directions (it matters, and it is bounded).

**Computed from the deployed constants**
(`P_S = {0.10, 0.15, 0.20}`, `P_T = 0.09`, `P_L0 = {0.30, 0.05, 0.01}`,
`θ_cert = 0.95`, defaults `P_G = {0.20, 0.10, 0.05}` from `bkt_model.py:48-57`,
caps `P_G,max = {0.40, 0.25, 0.15}` from `srl_calibration.py:27`):

Number of *consecutive correct responses from the prior* required to reach P̃ ≥ 0.95:

| Tier | at default P_G | at cap P_G,max | extra evidence demanded |
|---|---|---|---|
| quiz (declarative) | 3 (0.30→0.689→0.917→0.982) | 5 (…→0.949→0.979) | **+2** |
| micro (procedural) | 3 (0.05→0.371→0.848→0.981) | 5 (…→0.945→0.985) | **+2** |
| code (applied) | 3 (0.01→0.217→0.833→0.989) | 4 (…→0.859→0.973) | **+1** |

*(Recompute these with a 5-line script before camera-ready — the sequences above use the
exact `_bkt_step` update: posterior evidence step then learning transition. The default-P_G
columns already match the comments at `bkt_model.py:18-20`.)*

**Draft corollary:**

> **Corollary 1 (Bounded delay).** Under the deployed parameters, full saturation of the
> calibration loop (P_G at its per-tier cap) increases the minimum number of consecutive
> correct responses required for a tier to reach θ_cert from its prior by at most two
> (quiz: 3→5, micro: 3→5, code: 3→4). The maximum cost a learner's self-assessment can
> impose on their own certification is therefore two additional demonstrations per tier —
> the model asks a self-reported non-master to show their competence at most twice more.

This is a *feature* framing: the worst case of the whole mechanism is "prove it twice more."

---

## A4. 🔴 Expand §III-B: the multidimensional learner model needs to be visible

**The gap.** §III-B is one paragraph claiming a multidimensional model the reader never sees,
while `core/learner_model.py` implements **four dimensions with ~20 indicators**, each with a
formula. A conference reviewer cannot credit an invisible contribution.

**Add (½–¾ page):**

1. **A figure** — the architecture diagram from `Additional_files/SAGE_LEARNER_MODEL.md` §1
   (signals → telemetry → four dimensions → perception–inference–adaptation cycle). Redraw
   as a proper figure, not ASCII.

2. **An indicator table** (this is the paper's evidence that "multidimensional" is real):

| Dimension | Indicator | Formula / rule (as implemented) |
|---|---|---|
| Cognitive | concept mastery | factored BKT, `P^(k)=θ_max^(k)·P̃^(k)` |
| Cognitive | cognitive load | `L = clip(0.4·ĉ + 0.3·ℓ̂ + 0.3·ê)`, ℓ̂ capped at 20 s (`learner_model.py:72-73`) |
| Cognitive | slip vs. gap | wrong ∧ conf ≥ 4 ⇒ gap; wrong ∧ conf ≤ 2 ⇒ slip |
| Cognitive | automatization | OLS slope of answer latency over repeated items |
| Cognitive | misconceptions | EMA `M_c ← 0.7·M_c + 0.3·1[confident ∧ wrong]`, flag at 0.25 |
| Metacognitive | self-monitoring | `jol_cal = 1 − mean(conf − correct)²` + slider-adjustment history |
| Metacognitive | help-seeking | skip rate + impulsive-surrender detection (< 5 s) |
| Metacognitive | persistence | `grit = 1 − give_up_rate` |
| Metacognitive | pacing | rushing = latency < 4 s ∧ mastery < 0.4 |
| Affective | frustration trajectory | RoBERTa affect + ΔF per turn |
| Affective | confusion / boredom | lexical + model heuristics per turn (`profiler.py`) |
| Affective | flow | `1 − |challenge − skill|` |
| Motivational | self-efficacy / interest / goal orientation | Likert survey (se1–3, in1–2, go1–2) |
| Motivational | engagement | session envelopes, time-on-task |

3. **One honest scoping sentence** (keep the paper's existing "no novelty per construct"
   stance — it is a strength): the contribution is that all indicators are computed from the
   *same telemetry stream the cognitive model consumes* (a pure read model,
   `learner_model.py`, exposed at `GET /api/v1/learner-model/{username}`), so the
   multidimensional state and the certified record can never disagree about what happened.

---

## A5. 🟡 Address non-reversion of adapted P_G (§III-C or Limitations)

**The fact (verify in code):** once `adapted_P_G` is raised, nothing ever lowers it
(`srl_calibration.py` only writes `min(base + step, cap)`; there is no decay path). A learner
whose calibration *improves* keeps the elevated evidence requirement forever.

**Two options — pick one before the pilot (this is a pre-registration decision):**

- **(a) Defend permanence** (no code change): the penalty is bounded (Corollary 1, at most
  two extra demonstrations) and conservative-by-design; a learner exits it simply by
  answering correctly slightly more often. One paragraph in Limitations.
- **(b) [NOVEL — SOTA] Add a forgiveness rule** (small code change): decay adapted P_G toward
  the default on sustained *agreement* — e.g., after N consecutive sessions with no downward
  adjustment and `direction_ema` above −DIRECTION_THRESHOLD,
  `P_G ← max(P_G − β·κ, P_G_default)`. **Proposition 1 is preserved** because the guarantee
  only requires `P_G ≥ P_G_default` at all times (self-report can then never make evidence
  count *more* than the no-self-report baseline). This mirrors how the system already treats
  memory (SM-2 ease-factor relaxation, `bkt_model.py:28-29`), giving the paper a pleasing
  symmetry: *both* knowledge and calibration estimates are non-stationary with bounded,
  monotone-safe dynamics.

Recommendation: (b) if there is time to test it before the class; otherwise (a), with (b)
named as future work.

---

## A6. 🟡 Parameter transparency (§III-C, 3–4 sentences + one §IV pointer)

State every constant and its role in one table — they are all in
`srl_calibration.py:20-30` — and commit to the sensitivity sweep in §IV-F:

| Constant | Value | Role | Chosen because |
|---|---|---|---|
| α | 0.6 | Layer-1 blend weight (model trust) | majority weight to the evidence-based estimate; learner input softens but cannot dominate display |
| λ_cal | 0.4 | EMA smoothing of adjustment direction | ≈ last 3–4 adjustments dominate; matches N_TRIGGER window |
| N_TRIGGER | 3 | min. adjustments before Layer 2 | single reports are mood; three is a pattern |
| D threshold | −0.3 | sustained-direction requirement | filters mixed/oscillating signals |
| β | 0.10 | adaptation step | reaches the quiz cap in ≈ 2–3 firings; no single event saturates |
| P_G,max | .40/.25/.15 | hard per-tier ceilings | keeps `p_S + p_G < 1` (0.50/0.40/0.35): correct answers stay diagnostic |

The last row is already argued in the paper (§III-C end) — keep it, it's the strongest one.

---

## A7. 🟡 [NOVEL — SOTA] JOL as a second, *implicit* calibration channel (§III-E or Future Work)

**The idea.** The system already collects per-question confidence judgments (JOLs) at quiz
time (`agents/examiner.py` → `jol_log`) and already computes calibration from them
(`jol_cal` in `learner_model.py`). This is a *passive* analogue of the slider:

> a **correct answer given with low confidence** is the learner implicitly saying
> "that was a guess" — the same downward-only, epistemically-valid signal the slider
> elicits explicitly.

**Proposal.** Feed `(correct ∧ confidence ≤ 2/5)` events into the same direction-EMA that
drives Layer 2. The guarantee survives untouched: the signal is downward-only by
construction (only low-confidence-*correct* events count; low-confidence-*wrong* is already
ordinary BKT evidence), and P_G still only rises to the same caps.

**Why this strengthens the paper.** (i) It answers "what if students never touch the
slider?" — the loop still fires from ordinary quiz-taking, which de-risks the deployment's
engagement question (§IV-B). (ii) It connects to the metacognition literature (JOLs are the
standard laboratory instrument for monitoring accuracy — Dunlosky & Metcalfe), letting you
claim *convergent* explicit + implicit measurement of the same construct. (iii) It is ~30
lines of code: a hook in `examiner.py` calling a variant of `record_self_assessment` with
`source="jol"`, plus a `source` column in `user_bkt_calibration`.

If not implemented before submission, add it as a designed extension in §V — it is specific
enough to show the mechanism generalizes beyond a UI widget.

---

## A8. 🔴 §IV — what to write *now*, before the pilot

§IV is currently all placeholders. Two of its six subsections are honest about needing the
classroom; the rest can carry real content today:

1. **§IV-F (simulated learners) — run it now and move it forward.** Build the harness the
   section already describes: synthetic learners with ground-truth per-tier mastery and
   *overconfident profiles* (report high, perform low ⇒ never touch the slider; and the
   target profile: perform high on declarative, self-report low on applied). Run with/without
   the loop. Report: direction of P_G movement, over-crediting reduction, no-harm to
   well-calibrated learners, and the β/λ_cal/α sensitivity sweep. This is the paper's only
   quantitative section until the pilot lands, so present it as *mechanism validation*,
   clearly separated from the (upcoming) *ecological validation*.
2. **§IV-A** — the study-design text can be finalized now: instruments exist
   (`scripts/import_study_data.py`: pretest/posttest/MAI + se/in/go survey), telemetry tables
   exist (~20, all `study_id`-linked), and the anti-circularity artifact is real: the
   held-out graded component is *never ingested* — nothing in `srl_calibration.py` or
   `bkt_model.is_mastered` reads `assessment` rows. State that as a verifiable code property.
3. **Fill the abstract** (currently the literal placeholder "SRL ABS") and conclusion last.

---

# PART B — IRL Extension Paper (`IRL_Extension.pdf`)

§III is strong. These items harden it and fill the runnable parts of §IV.

---

## B1. 🔴 Formalize Theorem 1 + a quantitative false-certification bound

**The gap.** The contributions list promises an "Evidence Diversity Guarantee … a property of
the architecture itself" plus "quantitative bounds on false certification," but a formal
statement risks reading as true-by-construction ("we require N_min of each type, therefore…").
Defuse this by *pairing* the structural theorem with a probabilistic bound.

**Structural part (state honestly as an architectural invariant):**

> **Theorem 1 (Evidence Diversity Guarantee).** For every topic t, t ∈ K only if for every
> tier k the system has processed at least N_min = 3 observations of evidence type k with
> P̃_t^(k) ≥ θ_cert. In particular no sequence of observations of fewer than all three
> evidence types can certify t, independently of the calibrated parameter values.

Follows directly from the conjunction at `bkt_model.py:382` (`n_q, n_m, n_c ≥ N_MIN` and all
posteriors ≥ θ). Present it as *the property the single-scalar baseline provably lacks* —
that comparison is what makes it non-trivial.

**Probabilistic part (the actual bound — add as Theorem 2 or a corollary):**

For a **zero-knowledge guesser**, the probability of certifying within the minimal
3-per-tier evidence window is bounded by the product of per-tier guess runs:

- per tier: P(3 consecutive lucky corrects) = P_G³ → quiz 0.8 %, micro 0.1 %, code 0.0013 %
  (these are already the comments at `bkt_model.py:23`)
- **jointly (all three tiers): ≈ 10⁻⁹**

Two honest caveats to include: (i) with more attempts the union over sequences grows, so
report the *simulated* false-certification rate over realistic session lengths (the §IV-C
harness gives this for free); (ii) wrong answers actively *lower* the posterior, so the
analytic minimal-window bound is optimistic for the attacker — the simulation will show the
empirical rate is far below even these numbers. A "bound + simulation agreement" pairing is
much stronger than either alone.

---

## B2. 🔴 Justify θ_max ceilings (0.60/0.25/0.10) and half-lives (14/7/5 days)

These constants (`bkt_model.py:9-11, 66-68`) currently look hand-picked. Three moves:

1. **Reframe the ceilings as display weights, not model parameters.** This is exactly what
   the code does — posteriors are unconstrained in [0,1]; ceilings only scale the *composite
   display* `S = Σ θ_max^(k) P̃^(k)` (`bkt_model.py:5-6, 192-194`) and are excluded from
   certification, which reads raw P̃. Once stated, the "you tuned it" objection mostly
   dissolves: certification behavior is invariant to θ_max. Say this explicitly — it is
   currently only implied.
2. **Give the pedagogical rationale for the ordering**: declarative recall is necessary but
   least sufficient, applied competence is scarcest and hardest to fake — so the display
   deliberately caps what recall alone can show (60 %) and reserves the top of the scale for
   demonstrated application. One sentence, cite the Bloom framing already in the paper.
3. **Half-lives**: anchor 14/7/5 to the retention literature ordering (recognition decays
   slower than skill execution) and note the SM-2 mechanism makes them *adaptive* — the
   defaults matter only for unpracticed material (`λ ← λ/EF` after each successful review,
   `bkt_model.py:28-29`). Then point to §IV-G (parameter sweep) for robustness.

---

## B3. 🟡 Evidence-routing validity (the implicit Q-matrix) — one paragraph in §III-D

The mapping {quiz → declarative, micro-challenge → procedural, code review → applied} is the
paper's implicit Q-matrix, and CDM reviewers will look for it. State:

- routing is **structural, not inferred**: the evidence type is determined by the
  *interaction channel* that produced it (`bkt_model.update(..., evidence_type=...)` is
  called with a hard-coded type per channel — quiz flow, micro-challenge check, code review),
  so misrouting cannot occur at runtime;
- the construct claim (channel ⇒ cognitive tier) is grounded in Bloom's taxonomy and is a
  *design assumption*, validated in deployment by the per-tier calibration study (§IV-F:
  per-tier Brier from `prediction_log`) — if a channel were measuring the wrong construct,
  its tier-specific reliability would degrade. That gives the assumption a falsifiable check
  instead of an appeal to authority.

---

## B4. 🟢 Decay × certification: state it as a design decision

A certified topic left unpracticed can decay below θ_dec = 0.75 and be *decertified with no
new evidence* (`_apply_decay` runs inside `is_mastered`, `bkt_model.py:337-358`). The paper
already notes K is non-monotone (§III-G) — add one sentence owning the consequence: the gate
reflects *current* rather than historical competence, decertification re-locks downstream
topics until a retention check clears, and SM-2 protects well-rehearsed material from
spurious decay. Also note `ever_certified` is latched (never unset), which is what the
adaptation policy uses for the REVIEWING level — so decertified learners get refreshers,
not novice-level scaffolding. (That nuance is in the code — `cot_rag_agent.py:912-914` — and
currently absent from the paper.)

---

## B5. 🔴 Fix the generator model name

§III-I says "Gemini 2.5 flash [18]" but the deployed generator is `gemini-3.1-flash-lite`
(`core/config.py:32`). Update the text (and re-check the TTFT numbers 13.1 s → 2.2 s were
measured on the model you finally name).

---

## B6. 🟡 §IV — run the "runnable now" studies before submission

The section itself marks five studies runnable without a classroom. Minimum credible set:

1. **§IV-C Over-crediting resistance** (the headline): lopsided synthetic learners through a
   single-scalar BKT vs. the factored model — baseline certifies many, factored certifies
   none, true masters certify under both. Empirically realizes Theorem 1 *and* provides the
   simulated false-certification rate for B1.
2. **§IV-F Model calibration**: per-tier Brier + reliability diagram directly from the
   existing `prediction_log` (populated by the telemetry hook in `bkt_model.update`).
3. **§IV-G Parameter sweep**: sweep guess/slip; show the certification decision is stable —
   closes the loop with B2.

The same synthetic-learner harness serves §IV-C here and §IV-F of the SRL paper — build it
once, parameterize the learner profiles (lopsided vs. overconfident).

---

# PART C — Shared infrastructure both papers need

| Item | Serves | Status |
|---|---|---|
| Synthetic-learner simulation harness (profiles: balanced, lopsided, overconfident, miscalibrated; with/without calibration loop; with factored vs. single-scalar model) | IRL §IV-C/E/G · SRL §IV-F | **does not exist yet** — highest-value build |
| Reliability-diagram + Brier script over `prediction_log` | IRL §IV-F · SRL §IV-C | data path exists (`telemetry.log_prediction`), script needed |
| Bounded-delay recomputation script (Corollary 1 numbers) | SRL §III-D | 5 lines, do at camera-ready |
| Learner-model architecture figure (redrawn from `SAGE_LEARNER_MODEL.md`) | SRL §III-B (and reusable in IRL §III-H) | ASCII exists, needs a real figure |

**Suggested order of work:**
1. SRL §III text additions (A1–A6) — no code, changes what the simulation must show.
2. Simulation harness (Part C) — fills IRL §IV-C and SRL §IV-F with real numbers.
3. Decide A5 (forgiveness rule) **before** the pilot — pre-registration-sensitive.
4. IRL text hardening (B1–B5), then the Brier/reliability scripts.
