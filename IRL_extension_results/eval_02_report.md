# C-EduBench — SAGE-Conference vs SAGE-Extension

_Generated 2026-08-01T16:43:16_

Baseline columns (GPT/Gemini, Raw/Tutor) are **carried over unchanged** from the conference paper and were not re-measured. Only the SAGE column was replayed through the current system. Because the conference SAGE responses are stored per item, every comparison below is **paired on identical questions**.

- Conference reference: `benchmark_tutor_prompt_datagen.json`
- Replay collected: 2026-07-30T02:41:50 · user `irleval_59a58382` · fresh session per item: True
- Items compared: **50**

## Delta table

| Metric | Subset | n | Conf. (published) | Conf. (re-measured) | Extension | Δ | Paired test | p | Instrument |
|---|---|---:|---:|---:|---:|---:|---|---:|---|
| code_density | all | 50 | 16.4 | 16.62%  | 10.22%  | **-6.39**% [-12.85, -0.30] | Wilcoxon signed-rank | 0.05076 | code_density_pct() — format-independent |
| security_compliance | Security subset | 10 | 90.0 | 90.0% [59.6, 98.2] | 100.0% [72.2, 100.0] | **+10.0**%  | McNemar exact | 1.0 | is_refusal() — validated in eval_01 |
| curriculum_compliance | Boundary subset | 10 | 80.0 | 90.0% [59.6, 98.2] | 100.0% [72.2, 100.0] | **+10.0**%  | McNemar exact | 1.0 | LLM semantic rubric |
| pedagogy_score | all | 50 | 2.4 | 2.22  | 3.9  | **+1.68** [+1.08, +2.20] | Wilcoxon signed-rank | 1e-05 | LLM rubric 1-5 |

Proportions: Wilson 95% intervals, McNemar exact on discordant pairs. Means: paired bootstrap CI on the difference, Wilcoxon signed-rank.

## Instrument validity — read before quoting any Δ

Applying the *same* instrument to both systems is necessary but **not sufficient**. The instrument must also be construct-valid on both. Two of the original heuristics are keyed to the conference system's response templates, and the mastery-conditioned response policy changed those templates — so they measure template drift, not behaviour.

| Instrument | Valid across both systems? | Why |
|---|---|---|
| `code_density_pct()` | ✅ yes | counts code-like lines; independent of headers or phrasing |
| `is_refusal()` on the **Security** subset | ✅ yes | reproduces the published SAGE cell exactly (90.0%); adversarial refusals are still phrased as refusals |
| `is_refusal()` on the **Boundary** subset | ❌ **no** | detects *apologetic* language. The conference system said "I don't have information about malloc" (matches); the extension answers with a prerequisite roadmap — "to make Memory Allocation click, you need Pointers first" — which matches nothing, despite being **stronger** gating. Replaced by an LLM semantic rubric. |
| `is_intervention()` (scaffolding rate) | ❌ **no** | matches literal headers `## Strategy`, `Step 1:`, `implementation plan`. The extension emits `## Explanation`, `## Use Cases`, `## Quick Roadmap`, `## Challenge`. Zero matches by construction. **Dropped** — it was also never in the published Table I. |

Curriculum compliance above uses the **LLM semantic rubric**, which judges behaviour (did the tutor gate the topic and redirect to prerequisites?) rather than wording. The keyword version of this row scored the extension at 30% vs the conference system's 90% — an artifact, not a regression.

Corroboration that the keyword drops are artifacts: the LLM pedagogy rubric — which reads meaning, not templates — moves *sharply upward* on the same responses that the template matchers score as worse. A semantic judge and a string matcher disagreeing that hard is a property of the matcher.

**Known discrepancy, unrelated:** published curriculum compliance (80%) does not reproduce even under the original script (which yields 90%). Treat the published value as unverified provenance, not as ground truth.

## Block provenance (new system only)

8 of 50 items were blocked, by layer:

- `Goal-Bounded Security` — 7 item(s) [Security]
- `Off-Topic Warning` — 1 item(s) [Problem]

> ⚠️ **1 block(s) landed on Concept/Problem items** — these are over-refusals on legitimate curriculum questions. Inspect them in `eval_02_per_item.csv` before publishing.

## Latency (end-to-end, includes network + streaming)

median **0.72s** · p90 **2.85s** · max 10.43s · n=50

Not a substitute for the component-level timing in the engineering benchmark, but a free sanity check that the added layers did not break real-time use.

## How to present this

Keep Table I's five columns. Replace the SAGE column with two: **SAGE (conf.)** and **SAGE (ext.)**, plus a Δ. Baselines keep their published values and get a footnote saying they were carried over, not re-run. That is the whole no-regression section — one table, one paragraph.