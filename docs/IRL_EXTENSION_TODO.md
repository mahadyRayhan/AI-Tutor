# IRL Extension — Implementation Plan

*Plain-English build plan for the CAC / earned-credential extension. Every stage ships
with an on/off switch (for the ablation table) and a test that fails today and passes
after (fail-before/pass-after).*

---

## The two problems we're fixing

1. **The tutor gives advice, not rules.** When a student isn't ready for something, the
   system just writes "go easy, don't give the full code" into the prompt. The model
   usually listens — but it can be talked out of it. Nothing actually *stops* the full
   answer from reaching the student.

2. **The "mastered" stamp is too easy to earn.** A student can rack up enough right
   answers to get certified even if every one was produced with hints, copied, or rushed.
   The stamp says "mastered," but the skill is hollow.

We fix both — but first we restore some safety pieces that were reverted out of the
current code. The work runs in three stages, in order.

---

## Stage 0 — Put the foundations back  *(~½ day)*

Three safety features were built earlier but have been reverted out of the working code.
Everything else depends on them, so they go back first.

- [ ] **Revocable certification** — a "mastered" stamp that can be *taken back* when a
  skill goes stale. (The whole story depends on the stamp being revocable.)
- [ ] **On/off switches for each security feature** — so we can turn each piece off and
  measure how much it helps. (Every experiment we report needs these.)
- [ ] **The fail-safe judge** — when the safety check errors out, it *blocks* instead of
  quietly letting things through.
- [ ] Re-apply the small reverted chat fix (pop-quiz auto-resume).

**Done when:** the old tests for these pass again and the full test suite is green.

---

## Stage 1 — Make the tutor actually withhold material  *(~3–4 days)*

**Today** it *asks* the model to hold back. **After**, material a student isn't ready for
**never reaches the model**, so it can't be leaked.

- [ ] **Label the teaching material** by what it is — plain explanation, hint, worked
  example, full solution. (Start simple: "contains a full code solution" or not, detected
  automatically from the existing code-block regex.)
- [ ] **Write the rule for who may see what** — beginner → explanations + hints;
  intermediate → also worked examples; certified → also full solutions. (The student's
  level already comes from the mastery graph — no new work there.)
- [ ] **Filter before answering** — when gathering material, drop anything above the
  student's level. If there's no evidence yet, default to the *most restrictive* setting.
- [ ] **Add an on/off switch** so we can measure the effect and get a clean "before"
  baseline.

**Done when:** a test proves a beginner's answer material has the full solution *removed*;
a certified student is unaffected; with the switch off, behavior is exactly like today.
**Also measured:** how often it blocks something it *shouldn't* (over-restriction of honest learners).

---

## Stage 2 — Make the "mastered" stamp hard to fake  *(~3–4 days)*

**Today** five right answers count as five, no matter how they were produced. **After**, an
answer that was hinted, copied, or rushed **counts for less**, and a student must show the
skill under **more than one condition** before certifying.

> **Core safety idea (the paper's real point):** the extra signals can only ever count
> *against* a student. Faking "I was struggling" or gaming the timing can only *lower*
> your own credit — never raise it. That is what makes it safe to use noisy,
> student-controlled signals in a security decision at all.

- [ ] **Record the conditions with each answer** — for every graded answer, store how it
  was produced, using signals we *already* capture (answer speed; whether it was an
  unassisted verification check). Answers are write-once, so weak evidence can't be
  laundered later.
- [ ] **Weight each answer by how honest the conditions were** — a clean, unassisted
  answer counts fully; a rushed or assisted one counts less. The weight can only go
  *down*, never up.
- [ ] **Require variety before certifying** — the student must show the skill under more
  than one condition, not just repeatedly in the easy one.
- [ ] **Add on/off switches** for the weighting and the variety rule, so with both off the
  system certifies *exactly* as it does today (baseline + safety check).

**Done when:** a student with lots of answers but all in one easy condition certifies today
but not after; a student whose answers were all hint-assisted drops below the bar; a
student with clean, varied answers is unaffected; with the switches off, nothing changes.

---

## Deliberately NOT doing now

- **Catching outside cheating (e.g., ChatGPT).** The signals above catch *in-system*
  shortcuts, not answers copied from an external tool — that's a separate piece (the
  consistency detector), later.
- **The full set of condition-signals.** Start with the two we already record; adding
  per-answer hint/scaffold tracking is more plumbing — only if the minimal version proves out.
- **Claiming we model emotion/motivation.** We *use* those signals as inputs; we don't
  claim to model them in this paper.

---

## Order, time, and method

| Stage | What it delivers | Time |
|-------|------------------|------|
| **0** | Put the reverted safety features back | ½ day |
| **1** | Tutor actually withholds material it shouldn't show | 3–4 days |
| **2** | The "mastered" stamp becomes hard to fake | 3–4 days |
| | **Total** | **~1.5–2 weeks** |

Every stage is built the same disciplined way: **an on/off switch** so we can measure its
effect, and **a test that fails today and passes after**, so every change is provable — not
just asserted.

**Recommended start: Stage 0** — nothing can be tested or measured until the reverted
safety features are back.
