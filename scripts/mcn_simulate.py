#!/usr/bin/env python
# scripts/mcn_simulate.py
#
# Phase 4 — Synthetic-student validation of the Metacognitive Calibration Network.
# ────────────────────────────────────────────────────────────────────────────────
# Validates the BN with ZERO real data, and produces a pre-deployment figure for the
# paper. The idea:
#
#   1. Sample a virtual student with a KNOWN latent (K, C) — knowledge & calibration.
#   2. Have them emit noisy observations (S, P, B, A) from a GENERATIVE CPT.
#   3. Feed those observations to the BN and read its inferred calibration Ĉ.
#   4. Score whether Ĉ recovers the true C, across many students.
#
# Two regimes are reported:
#   • matched    — generator == inference CPTs  (best-case identifiability).
#   • mismatched — generator = inference CPTs perturbed by noise  (guards against the
#                  "it only works because generator==model" critique a reviewer WILL
#                  raise; shows the verdict is robust to imperfect parameters).
#
# Also reports recovery vs. how many signals were observed (missing-data robustness).
#
# Dependency-free: stdlib `random` only. Saves a CSV always; saves a PNG confusion
# matrix only if matplotlib is importable. Deterministic given --seed.
#
# Usage:
#   python scripts/mcn_simulate.py --n 2000 --seed 7 --mismatch 0.15 --out mcn_sim_results

from __future__ import annotations

import argparse
import csv
import os
import random
import sys
from collections import defaultdict

# Make `app` importable when run from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.core import mcn  # noqa: E402
from app.core.mcn_evidence import _scalar_to_k_prior  # noqa: E402

# Mean objective-mastery scalar the BKT layer would report for each true K level.
_K_MEAN = {"low": 0.20, "med": 0.50, "high": 0.85}


# ─────────────────────────────────────────────────────────────────────────────
# Sampling helpers (stdlib only)
# ─────────────────────────────────────────────────────────────────────────────

def sample_categorical(dist: dict, rng: random.Random) -> str:
    """Draw a state from a normalised categorical distribution {state: prob}."""
    r = rng.random()
    cum = 0.0
    for state, p in dist.items():
        cum += p
        if r <= cum:
            return state
    return next(reversed(dist))  # float-safety fallback


def perturb_cpt(cpt: mcn.CptSet, noise: float, rng: random.Random) -> mcn.CptSet:
    """
    Return a copy of `cpt` with every conditional row multiplicatively perturbed by
    ~U(1-noise, 1+noise) then renormalised (CptSet re-normalises on construction).
    noise=0 returns an identical network.
    """
    def jitter(dist):
        return {s: max(1e-6, w * (1.0 + rng.uniform(-noise, noise)))
                for s, w in dist.items()}

    return mcn.CptSet(
        name=f"{cpt.name}+noise{noise}",
        prior_K=jitter(cpt.prior_K),
        prior_C=jitter(cpt.prior_C),
        S_given_KC={kc: jitter(d) for kc, d in cpt.S_given_KC.items()},
        P_given_K={k: jitter(d) for k, d in cpt.P_given_K.items()},
        B_given_K={k: jitter(d) for k, d in cpt.B_given_K.items()},
        A_given_KC={kc: jitter(d) for kc, d in cpt.A_given_KC.items()},
    )


def generate_observations(true_k: str, true_c: str, gen: mcn.CptSet,
                          rng: random.Random, p_missing: float) -> dict:
    """Emit observed evidence for a student with latent (true_k, true_c).
    Each leaf is independently dropped with probability p_missing (missing data)."""
    ev = {}
    if rng.random() >= p_missing:
        ev["S"] = sample_categorical(gen.S_given_KC[(true_k, true_c)], rng)
    if rng.random() >= p_missing:
        ev["P"] = sample_categorical(gen.P_given_K[true_k], rng)
    if rng.random() >= p_missing:
        ev["B"] = sample_categorical(gen.B_given_K[true_k], rng)
    if rng.random() >= p_missing:
        ev["A"] = sample_categorical(gen.A_given_KC[(true_k, true_c)], rng)
    return ev


# ─────────────────────────────────────────────────────────────────────────────
# Simulation
# ─────────────────────────────────────────────────────────────────────────────

# Uniform prior over C — the sampling distribution used below. Inferring with this
# (rather than the informative production prior) makes recovery a fair test of the
# EVIDENCE's discriminative power instead of a measure of the prior.
_FLAT_C = {c: 1.0 for c in mcn.STATES["C"]}


def sim_bkt_k_prior(true_k: str, rng: random.Random, k_noise: float):
    """Simulate the noisy objective K estimate the BKT layer supplies in deployment:
    a mastery scalar centred on the true K level + Gaussian noise, mapped to a soft
    K prior via the same kernel the real evidence adapter uses."""
    m = max(0.0, min(1.0, _K_MEAN[true_k] + rng.gauss(0, k_noise)))
    return _scalar_to_k_prior(m)


def run(n: int, gen: mcn.CptSet, inf: mcn.CptSet, rng: random.Random,
        p_missing: float, use_k_prior: bool, k_noise: float) -> list:
    """Run n students; return per-student records."""
    records = []
    K_states, C_states = mcn.STATES["K"], mcn.STATES["C"]
    for _ in range(n):
        true_k = rng.choice(K_states)
        true_c = rng.choice(C_states)          # uniform over archetypes → balanced eval
        ev = generate_observations(true_k, true_c, gen, rng, p_missing)
        # Deployed condition: BKT supplies a noisy K prior. Flat C prior keeps the
        # recovery a fair test of discriminability against the uniform sampling.
        kp = sim_bkt_k_prior(true_k, rng, k_noise) if use_k_prior else None
        res = mcn.infer(ev, inf, k_prior=kp, c_prior=_FLAT_C)
        records.append({
            "true_K": true_k, "true_C": true_c,
            "pred_C": res.map_C, "pred_K": res.map_K,
            "conf": round(res.confidence, 4),
            "n_signals": len(ev),
            "correct_C": int(res.map_C == true_c),
        })
    return records


def confusion(records: list) -> dict:
    """3×3 confusion matrix cm[true_C][pred_C]."""
    cm = {t: {p: 0 for p in mcn.STATES["C"]} for t in mcn.STATES["C"]}
    for r in records:
        cm[r["true_C"]][r["pred_C"]] += 1
    return cm


def print_report(title: str, records: list) -> float:
    C = mcn.STATES["C"]
    acc = sum(r["correct_C"] for r in records) / max(1, len(records))
    cm = confusion(records)

    print(f"\n=== {title} ===")
    print(f"N = {len(records)}   overall calibration-recovery accuracy = {acc:.3f}")

    # confusion matrix
    print("\nconfusion matrix  (rows = true C, cols = predicted C):")
    header = "true\\pred |" + "".join(f"{c:>12}" for c in C)
    print(header)
    print("-" * len(header))
    for t in C:
        row_total = sum(cm[t].values()) or 1
        cells = "".join(f"{cm[t][p]:>6}({cm[t][p]/row_total*100:>3.0f}%)" for p in C)
        print(f"{t:>9} |{cells}")

    # per-true-class recall
    print("\nper-class recall:")
    for t in C:
        tot = sum(cm[t].values()) or 1
        print(f"  {mcn.C_LABELS[t]:<16}: {cm[t][t]/tot:.3f}  (n={tot})")

    # accuracy by number of observed signals
    by_sig = defaultdict(lambda: [0, 0])
    for r in records:
        by_sig[r["n_signals"]][0] += r["correct_C"]
        by_sig[r["n_signals"]][1] += 1
    print("\naccuracy by #signals observed:")
    for s in sorted(by_sig):
        hit, tot = by_sig[s]
        print(f"  {s} signal(s): {hit/tot:.3f}  (n={tot})")

    # Intervention-relevant metrics: the system's real decision is (a) is the student
    # MIScalibrated at all, and (b) in which direction — never confusing over↔under.
    def mis(c):
        return c != "cal"
    detect = sum(int(mis(r["true_C"]) == mis(r["pred_C"])) for r in records) / len(records)
    tail = [r for r in records if mis(r["true_C"])]
    dir_acc = (sum(r["correct_C"] for r in tail) / len(tail)) if tail else 0.0
    flip = sum(int({r["true_C"], r["pred_C"]} == {"over", "under"}) for r in records)
    print("\nintervention-relevant metrics:")
    print(f"  miscalibration detection (cal vs. not): {detect:.3f}")
    print(f"  direction accuracy on truly miscalibrated: {dir_acc:.3f}")
    print(f"  over↔under confusions (worst error): {flip} / {len(records)} "
          f"({flip/len(records)*100:.1f}%)")
    return acc


def save_csv(path: str, records: list) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(records[0].keys()))
        w.writeheader()
        w.writerows(records)


def save_confusion_png(path: str, records: list, title: str) -> bool:
    """Save a confusion-matrix heatmap if matplotlib is available; else skip."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return False
    C = mcn.STATES["C"]
    cm = confusion(records)
    mat = [[cm[t][p] for p in C] for t in C]
    row_tot = [sum(r) or 1 for r in mat]
    norm = [[mat[i][j] / row_tot[i] for j in range(len(C))] for i in range(len(C))]

    fig, ax = plt.subplots(figsize=(5, 4.2))
    im = ax.imshow(norm, cmap="Blues", vmin=0, vmax=1)
    ax.set_xticks(range(len(C)), [mcn.C_LABELS[c] for c in C], rotation=20, ha="right")
    ax.set_yticks(range(len(C)), [mcn.C_LABELS[c] for c in C])
    ax.set_xlabel("Predicted calibration")
    ax.set_ylabel("True calibration")
    ax.set_title(title)
    for i in range(len(C)):
        for j in range(len(C)):
            ax.text(j, i, f"{norm[i][j]*100:.0f}%", ha="center", va="center",
                    color="white" if norm[i][j] > 0.5 else "black", fontsize=10)
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04, label="row-normalised")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return True


# ─────────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Synthetic-student validation of the MCN.")
    ap.add_argument("--n", type=int, default=2000, help="students per regime")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--mismatch", type=float, default=0.15,
                    help="CPT perturbation for the mismatched regime (0=identical)")
    ap.add_argument("--p-missing", type=float, default=0.15,
                    help="per-signal dropout probability (missing data)")
    ap.add_argument("--k-noise", type=float, default=0.15,
                    help="stddev of the simulated BKT K-prior estimate")
    ap.add_argument("--no-k-prior", action="store_true",
                    help="behavioural-only lower bound (withhold the BKT K prior)")
    ap.add_argument("--out", type=str, default="mcn_sim_results")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    rng = random.Random(args.seed)
    net = mcn.default_network()
    use_kp = not args.no_k_prior
    cond = "behavioural-only" if args.no_k_prior else "deployed (+BKT K-prior)"

    # Matched regime: generator == inference model.
    rec_matched = run(args.n, net, net, rng, args.p_missing, use_kp, args.k_noise)
    acc_m = print_report(f"MATCHED — {cond}", rec_matched)
    save_csv(os.path.join(args.out, "matched.csv"), rec_matched)
    png_m = save_confusion_png(os.path.join(args.out, "confusion_matched.png"),
                               rec_matched, f"MCN recovery — matched (acc={acc_m:.2f})")

    # Mismatched regime: generate from a perturbed network, infer with the clean one.
    gen = perturb_cpt(net, args.mismatch, rng)
    rec_mis = run(args.n, gen, net, rng, args.p_missing, use_kp, args.k_noise)
    acc_x = print_report(
        f"MISMATCHED (generator perturbed ±{args.mismatch}) — {cond}",
        rec_mis)
    save_csv(os.path.join(args.out, "mismatched.csv"), rec_mis)
    png_x = save_confusion_png(os.path.join(args.out, "confusion_mismatched.png"),
                               rec_mis, f"MCN recovery — mismatched (acc={acc_x:.2f})")

    print("\n" + "=" * 60)
    print(f"SUMMARY  matched acc={acc_m:.3f}   mismatched acc={acc_x:.3f}")
    print(f"CSV written to {args.out}/  "
          f"({'PNG saved' if png_m and png_x else 'PNG skipped — matplotlib not installed'})")
    print("=" * 60)


if __name__ == "__main__":
    main()
