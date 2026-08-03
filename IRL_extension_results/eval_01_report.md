# C-EduBench — Confidence Intervals and Paired Tests

_Generated 2026-07-26T00:06:21 · B=10000 bootstrap resamples · seed=7_

Reanalysis of the **existing** conference results. No API calls, no server, no new generation — this only adds uncertainty quantification to numbers that were previously reported as bare point estimates.

## ⚠️ Read this before using any number below

The published Table I **does not reproduce exactly** from any single file in `eval_result/`. Candidate sources disagree with each other and with the paper. In particular `benchmark_5_way_results.csv` looks degenerate: its `winner` column is a single value on every row, and its baseline `*_sec` columns are near-zero across the board.

`eval_01_provenance_audit.csv` lists every recomputed cell beside the published value with a delta. **Decide which source is authoritative before pasting a CI into the paper.** A tight interval on the wrong file is worse than no interval.

### Largest disagreements with published Table I

| Metric | System | Source | Recomputed | Published | Δ |
|---|---|---|---|---|---|
| security_compliance | Gemini (Tutor) | `benchmark_tutor_prompt_datagen.json (heuristic is_refusal, n=10)` | 90.0 | 30.0 | **+60.0** |
| win_rate | SAGE | `benchmark_5_way_results.csv (n=50)` | 100.0 | 44.0 | **+56.0** |
| win_rate | GPT (Tutor) | `benchmark_5_way_results.csv (n=50)` | 0.0 | 42.0 | **-42.0** |
| security_compliance | Gemini (Raw) | `benchmark_base_comp_datagen.json (heuristic is_refusal, n=10)` | 60.0 | 20.0 | **+40.0** |
| security_compliance | Gemini (Tutor) | `benchmark_5_way_results.csv/gemini_tutor_sec (Security subset n=10)` | 0.0 | 30.0 | **-30.0** |
| security_compliance | GPT (Tutor) | `benchmark_5_way_results.csv/gpt_tutor_sec (Security subset n=10)` | 0.0 | 30.0 | **-30.0** |
| security_compliance | Gemini (Raw) | `benchmark_5_way_results.csv/gemini_raw_sec (Security subset n=10)` | 0.0 | 20.0 | **-20.0** |
| security_compliance | GPT (Raw) | `benchmark_5_way_results.csv/gpt_raw_sec (Security subset n=10)` | 0.0 | 20.0 | **-20.0** |
| security_compliance | SAGE | `benchmark_5_way_results.csv/ours_sec (Security subset n=10)` | 100.0 | 90.0 | **+10.0** |
| security_compliance | GPT (Tutor) | `benchmark_tutor_prompt_datagen.json (heuristic is_refusal, n=10)` | 20.0 | 30.0 | **-10.0** |
| code_density | GPT (Raw) | `benchmark_OP_GE_AT_comp.csv + benchmark_tutor_metrics.csv` | 0.0 | 10.0 | **-10.0** |
| win_rate | Gemini (Tutor) | `benchmark_balanced_results.csv (n=49)` | 10.204 | 4.0 | **+6.204** |

## Estimates with 95% intervals

Proportions use the **Wilson score interval**. The percentile bootstrap is shown alongside only to demonstrate why it is unsuitable here — it collapses to a single point whenever a system scores 0% or 100%, which happens often on this benchmark. Rows where that occurred are flagged `boot_degenerate`.

### win_rate

| System | n | Estimate | 95% CI | Method |
|---|---:|---:|---|---|
| GPT (Raw) | 49 | 4.08% | [1.13, 13.71] | Wilson 95% |
| GPT (Tutor) | 49 | 38.78% | [26.43, 52.75] | Wilson 95% |
| Gemini (Raw) | 49 | 0.0% | [0.0, 7.27] | Wilson 95% |
| Gemini (Tutor) | 49 | 10.2% | [4.44, 21.76] | Wilson 95% |
| SAGE | 49 | 46.94% | [33.7, 60.62] | Wilson 95% |

### security_compliance

| System | n | Estimate | 95% CI | Method |
|---|---:|---:|---|---|
| GPT (Raw) | 10 | 0.0% | [0.0, 27.75] | Wilson 95% |
| GPT (Tutor) | 10 | 0.0% | [0.0, 27.75] | Wilson 95% |
| Gemini (Raw) | 10 | 0.0% | [0.0, 27.75] | Wilson 95% |
| Gemini (Tutor) | 10 | 0.0% | [0.0, 27.75] | Wilson 95% |
| SAGE | 10 | 100.0% | [72.25, 100.0] | Wilson 95% |

### pedagogy_score

| System | n | Estimate | 95% CI | Method |
|---|---:|---:|---|---|
| GPT (Raw) | 50 | 3.0 | [3.0, 3.0] | BCa bootstrap 95% (B=10000) |
| GPT (Tutor) | 50 | 4.98 | [4.86, 5.0] | BCa bootstrap 95% (B=10000) |
| Gemini (Raw) | 50 | 3.0 | [3.0, 3.0] | BCa bootstrap 95% (B=10000) |
| Gemini (Tutor) | 50 | 4.98 | [4.86, 5.0] | BCa bootstrap 95% (B=10000) |
| SAGE | 50 | 4.02 | [4.0, 4.06] | BCa bootstrap 95% (B=10000) |

### code_density

| System | n | Estimate | 95% CI | Method |
|---|---:|---:|---|---|
| GPT (Raw) | 50 | 0.0% | [0.0, 0.0] | BCa bootstrap 95% (B=10000) |
| GPT (Tutor) | 50 | 5.331% | [3.837, 7.198] | BCa bootstrap 95% (B=10000) |
| Gemini (Raw) | 50 | 22.788% | [17.054, 28.593] | BCa bootstrap 95% (B=10000) |
| Gemini (Tutor) | 50 | 9.411% | [7.097, 12.095] | BCa bootstrap 95% (B=10000) |
| SAGE | 50 | 13.464% | [8.364, 19.746] | BCa bootstrap 95% (B=10000) |

## Paired comparisons (SAGE vs each baseline, identical items)

McNemar's exact test for paired binary outcomes; Wilcoxon signed-rank for ordinal/continuous. Holm-Bonferroni corrected within each metric family. An unpaired test would be invalid here — all systems answer the same items.

| Metric | Comparison | Test | Effect | n | p | p (Holm) | sig. |
|---|---|---|---|---:|---:|---:|:--:|
| security_compliance | SAGE vs GPT (Raw) | McNemar exact (paired binary) | +100.0 pp | 10 | 0.001953 | 0.007812 | ✅ |
| pedagogy_score | SAGE vs GPT (Raw) | Wilcoxon signed-rank + paired bootstrap | +1.020 [+1.000, +1.060] | 50 | 0.0 | 0.0 | ✅ |
| security_compliance | SAGE vs GPT (Tutor) | McNemar exact (paired binary) | +100.0 pp | 10 | 0.001953 | 0.007812 | ✅ |
| pedagogy_score | SAGE vs GPT (Tutor) | Wilcoxon signed-rank + paired bootstrap | -0.960 [-1.000, -0.880] | 50 | 0.0 | 0.0 | ✅ |
| security_compliance | SAGE vs Gemini (Raw) | McNemar exact (paired binary) | +100.0 pp | 10 | 0.001953 | 0.007812 | ✅ |
| pedagogy_score | SAGE vs Gemini (Raw) | Wilcoxon signed-rank + paired bootstrap | +1.020 [+1.000, +1.060] | 50 | 0.0 | 0.0 | ✅ |
| security_compliance | SAGE vs Gemini (Tutor) | McNemar exact (paired binary) | +100.0 pp | 10 | 0.001953 | 0.007812 | ✅ |
| pedagogy_score | SAGE vs Gemini (Tutor) | Wilcoxon signed-rank + paired bootstrap | -0.960 [-1.000, -0.880] | 50 | 0.0 | 0.0 | ✅ |
| code_density | SAGE vs GPT (Raw) | Wilcoxon signed-rank + paired bootstrap | +13.464 [+8.136, +19.172]  (lower is better) | 50 | 0.000638 | 0.002552 | ✅ |
| code_density | SAGE vs GPT (Tutor) | Wilcoxon signed-rank + paired bootstrap | +8.133 [+2.152, +14.467]  (lower is better) | 50 | 0.442053 | 0.884106 | — |
| code_density | SAGE vs Gemini (Raw) | Wilcoxon signed-rank + paired bootstrap | -9.324 [-17.740, -0.934]  (lower is better) | 50 | 0.048542 | 0.145626 | — |
| code_density | SAGE vs Gemini (Tutor) | Wilcoxon signed-rank + paired bootstrap | +4.053 [-1.739, +9.886]  (lower is better) | 50 | 0.581149 | 0.884106 | — |

## Red-team audit (N=36) — per-category Wilson intervals

The conference paper reports category-level rates including several 100% cells. With n=2–5 per category those intervals are extremely wide. Reporting them is what justifies the expanded adversarial suite in the extension.

| Category | n | Safe | Rate | 95% CI | Note |
|---|---:|---:|---:|---|---|
| ALL | 36 | 35 | 97.2% | [85.8, 99.5] |  |
| Data Leakage | 5 | 5 | 100.0% | [56.6, 100.0] | 100% with tiny n is NOT a guarantee — see CI width |
| Jailbreak | 5 | 5 | 100.0% | [56.6, 100.0] | 100% with tiny n is NOT a guarantee — see CI width |
| Social Eng | 5 | 5 | 100.0% | [56.6, 100.0] | 100% with tiny n is NOT a guarantee — see CI width |
| Infrastructure | 4 | 4 | 100.0% | [51.0, 100.0] | 100% with tiny n is NOT a guarantee — see CI width |
| Output Safety | 4 | 3 | 75.0% | [30.1, 95.4] |  |
| Privacy | 4 | 4 | 100.0% | [51.0, 100.0] | 100% with tiny n is NOT a guarantee — see CI width |
| Tool Abuse | 4 | 4 | 100.0% | [51.0, 100.0] | 100% with tiny n is NOT a guarantee — see CI width |
| Indirect Inj | 3 | 3 | 100.0% | [43.9, 100.0] | 100% with tiny n is NOT a guarantee — see CI width |
| RAG Leak | 2 | 2 | 100.0% | [34.2, 100.0] | 100% with tiny n is NOT a guarantee — see CI width |

## What to do with this

1. Resolve provenance first. Pick the authoritative source per metric and record
   that choice in the paper's artifact appendix.
2. Report intervals, not bare point estimates, in the carried-over table.
3. Expect the security subset (n=10) to give a very wide interval. State it. It
   is the cleanest available argument for why the extension needs a larger
   adversarial suite.
4. Where a paired test is non-significant after correction, say so rather than
   leaning on non-overlapping CIs — overlapping CIs and paired significance are
   different questions, and reviewers know it.
