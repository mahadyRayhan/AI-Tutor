# IRL Extension — Evaluation Scripts

Scripts for the IRL Extension evaluation (§IV). Numbered in the order they were built,
easiest first. All results are written to `../IRL_extension_results/`.

| # | Script | Needs | Cost |
|---|---|---|---|
| 01 | `eval_01_cedubench_ci.py` | nothing — reads existing `eval_result/*.csv` | seconds |

## Running

```bash
/opt/anaconda3/envs/agent/bin/python IRL_extension_script/eval_01_cedubench_ci.py
```

Every script is offline and deterministic unless its docstring says otherwise. Scripts
that need the live server or an API key state so at the top.

## Conventions

- Results go to `IRL_extension_results/eval_NN_*` — never overwrite `eval_result/`,
  which holds the conference-paper artifacts.
- Every script writes a `eval_NN_report.md` meant to be read first.
- Randomised procedures take `--seed` and default to a fixed value so numbers in the
  paper are reproducible.
