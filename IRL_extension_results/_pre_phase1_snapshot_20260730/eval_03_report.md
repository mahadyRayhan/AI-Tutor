# Overall Win Rate — Conference Judge Protocol

_Generated 2026-07-26T13:36:52 · judge=gpt-4o_

Judge prompt, rubrics, model and ordering copied verbatim from `scripts/benchmark_row_tutor_judge.py`. Only the SAGE slot differs between the two columns; baselines are the stored conference responses.

```

====================================================================================
CONDITION: faithful   (n=50)
====================================================================================
system               conference slot    extension slot
GPT (Raw)                       0.0%              6.0%
GPT (Tutor)                    10.0%             12.0%
Gemini (Raw)                    6.0%             14.0%
Gemini (Tutor)                  2.0%              0.0%
SAGE                           30.0%             14.0%   <-- SAGE
------------------------------------------------------------------------------------
SAGE  conference 30.0% [19.1, 43.8]   ->   extension 14.0% [7.0, 26.2]
paired McNemar  +5 / -13  p=0.09625

SAGE wins by category (conf -> ext):
  Boundary    8/10  ->  1/10
  Concept     0/15  ->  0/15
  Problem     0/15  ->  3/15
  Security    7/10  ->  3/10
```

```

====================================================================================
CONDITION: debiased   (n=50)
====================================================================================
system               conference slot    extension slot
GPT (Raw)                       6.0%              8.0%
GPT (Tutor)                    18.0%             20.0%
Gemini (Raw)                   24.0%             22.0%
Gemini (Tutor)                 20.0%             16.0%
SAGE                           32.0%             34.0%   <-- SAGE
------------------------------------------------------------------------------------
SAGE  conference 32.0% [20.8, 45.8]   ->   extension 34.0% [22.4, 47.8]
paired McNemar  +6 / -5  p=1.00000

SAGE wins by category (conf -> ext):
  Boundary    8/10  ->  7/10
  Concept     0/15  ->  4/15
  Problem     0/15  ->  1/15
  Security    8/10  ->  5/10
```

## Reading the two conditions

`faithful` keeps the conference protocol's biases: the judge is told which system is the authors' (`ours`), position is fixed, and on Security/Boundary items the rubric states refusal is the only correct answer. `debiased` changes ONLY the labelling and ordering — same rubric, same model, same responses. The gap between them is the size of the methodological bias, not a difference between systems.
