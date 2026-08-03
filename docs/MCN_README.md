# Metacognitive Calibration Network (MCN)

A small **Bayesian network** in the SRL layer that infers a student's latent
**metacognitive calibration** (over- / well- / under-confident) per concept by
fusing signals the tutor already logs. It upgrades the heuristic blending in
`srl_calibration.py` into a principled probabilistic model.

Status: **Phases 1–6 complete.** Engine + validation (1–4) are offline; integration,
interventions, dashboard panel, and telemetry (5–6) are wired but **gated behind a
feature flag that ships OFF** — deploying changes nothing until you enable it. Phase 7
(offline CPT refit from real data) is future work.

## Enabling the feature

Off by default. To turn it on, set the environment variable and restart:

```bash
export MCN_ENABLED=true
```

With the flag **off** (default): every MCN accessor returns `None`, the tutor behaves
exactly as before, and the dashboard panel stays hidden. With it **on**: the tutor
injects calibration-aware guidance (under → reassure + surface evidence; over → a quick
check question) and the dashboard shows the "🧭 Confidence Check" panel. Certification
is never affected either way — the mastery gate always reads BKT-certified mastery.

---

## Why a Bayesian network (and not a heuristic or a trained model)

- It separates two hidden variables the current heuristic conflates — **K** (true
  knowledge) and **C** (calibration) — and models the self-report as *jointly caused*
  by both. This lets it **explain away** a low self-report from a strong performer as
  *under-confidence* rather than *weakness*, which drives the opposite intervention.
- It works **cold, with zero training data** (expert-elicited CPTs) — essential for a
  classroom deployment where data only arrives *after* go-live and the server can't be
  taken down to iterate. A trained classifier can't do this.
- CPTs are **transparent and auditable** (printable conditional probabilities), which
  matters for a pedagogical system and for paper reproducibility.

## The network

```
        K (knowledge: low|med|high)      C (calibration: over|cal|under)
       /   |    \                              |
      v    v     v                             |
      P    B      \                            |
  (perf)(behav)    +---------------------> S (self-report)
                    (K and C jointly cause S)  |
                                               v (optional)
                                               A (affect)
```

- `K → P`, `K → B`: knowledge causes performance and help-seeking/fluency.
- `{K,C} → S`: **the crux.** cal → S tracks K; over → S skews high; under → S skews low.
- `{K,C} → A`: frustration rises with low K / under-confidence (optional evidence).

Inference is **exact by enumeration** over the 9 latent (K,C) states — no solver, no
`numpy`, no external dependency.

## Files

| File | Role |
|---|---|
| `backend/app/core/mcn.py` | Pure BN engine: states, CPTs, `infer()`. Ships hand-elicited `DEFAULT_CPT`. |
| `backend/app/core/mcn_cpts.py` | Load/serialize CPTs from JSON; **hot-reload** by mtime; graceful fallback to defaults. |
| `backend/app/core/mcn_cpts.json` | The editable CPT store (tune **without a redeploy**). |
| `backend/app/core/mcn_evidence.py` | Read-only adapters: live tables → evidence vector + BKT K-prior; `infer_calibration()`. |
| `tests/test_mcn.py` | 24 unit + integration tests (engine, config store, evidence pipeline). |
| `scripts/mcn_simulate.py` | Synthetic-student validation + confusion-matrix figures. |

## Signal → node mapping (evidence adapter)

| Node | Source | Scope |
|---|---|---|
| K prior | `bkt_model.get_mastery()` — **objective** BKT composite (not the self-blended one, to avoid double-counting S) | per-concept |
| S | `jol_log.confidence_1_5` (latest) → else calibration slider | per-concept |
| P | `evidence_log.is_correct` over recent window | per-concept |
| B | `behavior_log` (hint_request/skip = struggle; copy_code/long-dwell = fluent) | user-recent |
| A | `affect_log.frustration_level` (latest) | user-recent |

B and A are session/user-level (not per-concept) → treated as weak corroborating
evidence; the concept-specific weight rests on K, S, P.

## Usage

```python
from app.core.mcn_evidence import infer_calibration
verdict = infer_calibration(username, concept)
# {'map_C': 'under', 'label': 'Underconfident', 'confidence': 0.80,
#  'map_K': 'high', 'n_signals': 3, 'sufficient': True, 'explanation': '...'}
```

Tune parameters without a redeploy by editing `mcn_cpts.json`; `get_network()` reloads
on the next inference. A malformed edit falls back to the baked-in defaults (logged).

## Validation (Phase 4)

```
python scripts/mcn_simulate.py --n 3000 --seed 7 --out mcn_sim_results
python scripts/mcn_simulate.py --n 3000 --seed 7 --no-k-prior   # behavioural lower bound
```

Generates virtual students with known (K, C), has them emit noisy observations, and
checks the BN recovers the true C. Reports **matched** (generator == inference CPTs)
and **mismatched** (generator perturbed ±15% — robustness to imperfect parameters)
regimes, plus a confusion-matrix PNG.

**Representative results** (n=3000, balanced sampling, flat inference prior so the
score measures the *evidence's* discriminability, not the prior):

| Metric | Deployed (+BKT K-prior) | Notes |
|---|---|---|
| Overall 3-class recovery | ~0.58 | chance = 0.33 |
| Over-confident recall | ~0.70 | the cases we want to catch |
| Under-confident recall | ~0.73 | |
| Well-calibrated recall | ~0.29 | the hard ordinal middle |
| **Direction accuracy on miscalibrated** | **~0.70** | intervention-relevant |
| **Catastrophic over↔under flip** | **~8%** | the network rarely reverses direction |

**Honest reading:** the network reliably recovers calibration *where it is
identifiable* (miscalibration at non-extreme knowledge) and almost never makes the
damaging over↔under reversal. Its errors concentrate in genuinely ambiguous regions
(e.g. under-confidence when knowledge is already at the floor — there is no "lower than
low" to report). Expert CPTs are a starting point; **Phase 5b re-fits them from
`jol_log` after the study**, which should raise these numbers.

## Done in Phases 5–6 (flag-gated, OFF by default)

- **Service layer** (`mcn_service.py`): the single safe accessor — flag-gated,
  sufficiency-gated, fail-safe to `None`; never certifies.
- **Interventions**: `cot_rag_agent.py` computes the verdict per turn (for CONCEPT/
  PROBLEM) and `socratic.py` injects a directive (under → reassure + surface evidence;
  over → a pointed check question). No effect when the flag is off.
- **Endpoint**: `GET /api/v1/mcn/calibration/{username}` → per-topic verdicts
  (`enabled:false` when the flag is off, so the panel self-hides).
- **Dashboard panel**: "🧭 Confidence Check" card (`student_dashboard.html/.js/.css`,
  cache-buster `v=net6`).
- **Telemetry**: `mcn_log` table + `telemetry.log_mcn_verdict` records every acted-on
  verdict for offline evaluation.

## Not yet done (Phase 7)

- Offline CPT refit from real `jol_log`/`mcn_log` after the study
  (`scripts/mcn_refit.py`) — the "expert vs. learned CPTs" analysis.
