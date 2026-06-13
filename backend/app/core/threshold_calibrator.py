# backend/app/core/threshold_calibrator.py
#
# Empirical Bayes Adaptive Threshold Calibration
# ─────────────────────────────────────────────
# Mastery thresholds are initialized from literature-backed defaults and
# recalibrated every N_RECAL_MIN new observations using a two-component
# Gaussian Mixture Model (GMM) fitted to the population of observed BKT
# posteriors for each (concept, tier) pair.
#
# Bayesian blending formula:
#   θ_new  =  (1 − w) · θ_prior  +  w · θ_GMM
#   w      =  1 − noise_level
#   noise_level  =  1 / (1 + Fisher_ratio)
#   Fisher_ratio =  (μ_mastered − μ_other)² / (σ_mastered² + σ_other²)
#
# When clusters are well-separated (low noise), w → 1 and the GMM estimate
# dominates.  When clusters overlap (high noise), w → 0 and the system
# falls back to the literature prior — the prior is never discarded, only
# down-weighted as evidence accumulates.
#
# See SYSTEM_OVERVIEW.md §3.10 for full mathematical derivation.

import logging
import numpy as np
from datetime import datetime
from typing import Optional, Tuple
from app.db.sqlite_db import db

logger = logging.getLogger(__name__)

# ── Recalibration schedule ────────────────────────────────────────────────────
N_RECAL_MIN    = 50    # minimum observations to attempt first calibration
N_RECAL_STEP   = 50   # recalibrate every additional N_RECAL_STEP observations
N_RECAL_WINDOW = 500  # sliding window: keep only the last N posteriors per tier/concept

# ── Unified mastery threshold prior (Corbett & Anderson, 1995) ───────────────
# All tiers default to 0.95 — the standard BKT mastery criterion applied to
# P̃^(k) ∈ [0,1] (unconstrained proper posteriors, no ceiling clip).
# The calibrator may adjust this per concept/tier once 50+ observations
# accumulate; it falls back to 0.95 until then.
THRESHOLD_PRIORS = {
    "quiz":  0.95,
    "micro": 0.95,
    "code":  0.95,
}

# BKT tier ceilings — used for composite display score only (P^(k) = ceiling × P̃^(k))
# NOT used for mastery decisions; mastery is decided purely on P̃^(k) ∈ [0,1].
TIER_CEILINGS = {
    "quiz":  0.60,
    "micro": 0.25,
    "code":  0.10,
}

# z-score for 10th percentile of a standard normal (used for θ_GMM)
# θ_GMM = μ_mastered − Z_10 · σ_mastered  →  90% of mastered cluster is above θ_GMM
_Z_10 = 1.2816


class ThresholdCalibrator:
    """
    Population-level adaptive mastery threshold calibration.

    Maintains a sliding window of BKT posterior observations per
    (concept, tier) pair.  Every N_RECAL_STEP new observations the
    calibrator fits a two-component GMM via EM, measures cluster
    separation via Fisher's discriminant ratio, and updates the
    threshold using the Bayesian blending formula above.

    Usage:
        calibrator.record_posterior(concept, tier, posterior_value)
        threshold = calibrator.get_threshold(concept, tier)
    """

    def __init__(self):
        self._ensure_tables()

    # ── Public API ────────────────────────────────────────────────────────────

    def record_posterior(self, concept: str, tier: str, posterior: float):
        """
        Record one BKT posterior observation for (concept, tier) and trigger
        recalibration if the observation count crosses the next N_RECAL_STEP
        boundary.
        """
        db.execute(
            "INSERT INTO calibration_buffer (concept, tier, posterior, recorded_at) "
            "VALUES (?, ?, ?, ?)",
            (concept, tier, round(posterior, 6), datetime.now())
        )
        self._prune_buffer(concept, tier)

        count = self._buffer_count(concept, tier)
        if count >= N_RECAL_MIN and count % N_RECAL_STEP == 0:
            self.recalibrate(concept, tier)

    def recalibrate(self, concept: str, tier: str) -> Optional[float]:
        """
        Run one calibration cycle for (concept, tier).
        Fits a two-component GMM, computes noise level, blends with prior,
        persists the result, and returns the new threshold.
        Returns None if calibration cannot proceed (too few samples).
        """
        posteriors = self._load_buffer(concept, tier)
        if len(posteriors) < N_RECAL_MIN:
            return None

        θ_prior = self.get_threshold(concept, tier)

        θ_gmm, noise_level, fisher_ratio = self._fit_gmm(posteriors, tier)
        if θ_gmm is None:
            logger.warning(
                f"[Calibrator] GMM fit failed for '{concept}/{tier}' "
                f"(n={len(posteriors)}) — keeping prior θ={θ_prior:.4f}"
            )
            return θ_prior

        w = 1.0 - noise_level
        θ_new = round((1.0 - w) * θ_prior + w * θ_gmm, 4)

        # Fix #4 — Calibrator guardrails: clamp so the calibrated threshold can never
        # drift below the literature prior (0.95) or the decertify boundary (0.75).
        # This preserves the n_min=3 guarantee and prevents silent guarantee-breaking.
        # The two-correct posterior (quiz≈0.917, micro≈0.849, code≈0.832) is also < 0.95,
        # so clamping at the prior is the binding constraint.
        θ_floor = max(THRESHOLD_PRIORS[tier], 0.75)
        θ_new = max(θ_new, θ_floor)

        self._save_threshold(
            concept, tier, θ_new, θ_prior,
            noise_level, fisher_ratio, w, len(posteriors)
        )

        logger.info(
            f"[Calibrator] '{concept}/{tier}': "
            f"θ {θ_prior:.4f} → {θ_new:.4f}  "
            f"(θ_GMM={θ_gmm:.4f}, noise={noise_level:.3f}, "
            f"Fisher={fisher_ratio:.2f}, w={w:.3f}, n={len(posteriors)})"
        )
        return θ_new

    def get_threshold(self, concept: str, tier: str) -> float:
        """
        Return the current calibrated threshold for (concept, tier).
        Falls back to the literature prior if no calibration has run yet.
        """
        row = db.fetch_one(
            "SELECT threshold FROM threshold_history "
            "WHERE concept = ? AND tier = ? "
            "ORDER BY calibrated_at DESC LIMIT 1",
            (concept, tier)
        )
        return float(row["threshold"]) if row else THRESHOLD_PRIORS[tier]

    def get_calibration_status(self, concept: str, tier: str) -> dict:
        """
        Returns diagnostic info: current threshold, noise level, sample count,
        and whether at least one calibration has run.
        """
        row = db.fetch_one(
            "SELECT threshold, noise_level, fisher_ratio, weight, n_samples, calibrated_at "
            "FROM threshold_history "
            "WHERE concept = ? AND tier = ? "
            "ORDER BY calibrated_at DESC LIMIT 1",
            (concept, tier)
        )
        n_buffer = self._buffer_count(concept, tier)
        if row:
            return {
                "calibrated": True,
                "threshold": float(row["threshold"]),
                "prior": THRESHOLD_PRIORS[tier],
                "noise_level": float(row["noise_level"]),
                "fisher_ratio": float(row["fisher_ratio"]),
                "weight": float(row["weight"]),
                "n_samples_at_last_cal": int(row["n_samples"]),
                "n_buffer_current": n_buffer,
                "last_calibrated_at": row["calibrated_at"],
            }
        return {
            "calibrated": False,
            "threshold": THRESHOLD_PRIORS[tier],
            "prior": THRESHOLD_PRIORS[tier],
            "n_buffer_current": n_buffer,
            "n_until_first_calibration": max(0, N_RECAL_MIN - n_buffer),
        }

    # ── Core GMM fitting ──────────────────────────────────────────────────────

    def _fit_gmm(
        self, posteriors: np.ndarray, tier: str
    ) -> Tuple[Optional[float], float, float]:
        """
        Fit a two-component Gaussian Mixture Model to `posteriors` via EM
        (pure NumPy, no external ML dependency).

        Returns:
            θ_gmm       — 10th percentile of the higher-mean (mastered) cluster,
                          i.e. the value above which 90% of mastered-cluster
                          students fall.
            noise_level — 1 / (1 + Fisher_ratio) ∈ (0, 1); 0 = clean separation.
            fisher_ratio — (μ₁ − μ₀)² / (σ₁² + σ₀²); higher = better separation.
        """
        try:
            mu, sigma, pi = self._em_gmm(posteriors)
        except Exception as e:
            logger.error(f"[Calibrator] EM error: {e}")
            return None, 1.0, 0.0

        mastered = int(np.argmax(mu))
        other    = 1 - mastered

        μ1, σ1 = mu[mastered], sigma[mastered]
        μ0, σ0 = mu[other],    sigma[other]

        # Fisher's discriminant ratio
        fisher_ratio = float((μ1 - μ0) ** 2 / (σ1 ** 2 + σ0 ** 2 + 1e-8))
        noise_level  = float(1.0 / (1.0 + fisher_ratio))

        # 10th percentile of mastered cluster: μ₁ − z₀.₁₀ · σ₁
        θ_gmm = float(μ1 - _Z_10 * σ1)

        # Clamp to valid P̃ range: strictly positive, strictly below 1.
        # Upper bound 0.99 (not ceiling) — posteriors are now unconstrained in [0,1]
        # and the censoring-at-ceiling pathology is eliminated by the reparameterization.
        θ_gmm = float(np.clip(θ_gmm, 1e-4, 0.99))

        return θ_gmm, noise_level, fisher_ratio

    @staticmethod
    def _em_gmm(
        posteriors: np.ndarray,
        n_init: int = 5,
        max_iter: int = 200,
        tol: float = 1e-6,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Two-component Gaussian Mixture Model via Expectation-Maximization.
        Runs n_init random restarts and returns the parameters of the best
        (highest log-likelihood) solution.

        Returns: (mu [2], sigma [2], pi [2])
        """
        best_ll   = -np.inf
        best_params = None

        rng = np.random.default_rng(42)
        p25, p75 = np.percentile(posteriors, 25), np.percentile(posteriors, 75)
        std_init  = max((p75 - p25) / 2.0, 1e-3)

        for _ in range(n_init):
            # Random initialization around 25th and 75th percentiles
            mu    = np.array([p25 + rng.normal(0, std_init * 0.2),
                               p75 + rng.normal(0, std_init * 0.2)])
            sigma = np.array([std_init, std_init])
            pi    = np.array([0.5, 0.5])

            prev_ll = -np.inf
            for _ in range(max_iter):
                # E-step
                r = ThresholdCalibrator._e_step(posteriors, mu, sigma, pi)

                # M-step
                N_k   = r.sum(axis=0) + 1e-8
                mu    = (r.T @ posteriors) / N_k
                sigma = np.sqrt(
                    np.array([
                        (r[:, k] * (posteriors - mu[k]) ** 2).sum() / N_k[k]
                        for k in range(2)
                    ])
                ) + 1e-6
                pi = N_k / N_k.sum()

                # Log-likelihood for convergence check
                ll = ThresholdCalibrator._log_likelihood(posteriors, mu, sigma, pi)
                if abs(ll - prev_ll) < tol:
                    break
                prev_ll = ll

            if prev_ll > best_ll:
                best_ll     = prev_ll
                best_params = (mu.copy(), sigma.copy(), pi.copy())

        if best_params is None:
            raise ValueError("EM did not converge")
        return best_params

    @staticmethod
    def _e_step(x, mu, sigma, pi):
        """Compute responsibilities r[n, k] = P(z=k | x_n)."""
        r = np.stack([
            pi[k] * np.exp(-0.5 * ((x - mu[k]) / sigma[k]) ** 2) / (sigma[k] * np.sqrt(2 * np.pi))
            for k in range(2)
        ], axis=1)
        r_sum = r.sum(axis=1, keepdims=True) + 1e-8
        return r / r_sum

    @staticmethod
    def _log_likelihood(x, mu, sigma, pi):
        """Compute GMM log-likelihood for convergence monitoring."""
        ll = np.log(
            sum(
                pi[k] * np.exp(-0.5 * ((x - mu[k]) / sigma[k]) ** 2)
                / (sigma[k] * np.sqrt(2 * np.pi))
                for k in range(2)
            ) + 1e-8
        )
        return float(ll.sum())

    # ── Database helpers ──────────────────────────────────────────────────────

    def _ensure_tables(self):
        """Create calibration tables if they do not exist."""
        db.execute("""
            CREATE TABLE IF NOT EXISTS calibration_buffer (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                concept     TEXT    NOT NULL,
                tier        TEXT    NOT NULL,
                posterior   REAL    NOT NULL,
                recorded_at TIMESTAMP NOT NULL
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_cal_buf_concept_tier
            ON calibration_buffer (concept, tier)
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS threshold_history (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                concept         TEXT    NOT NULL,
                tier            TEXT    NOT NULL,
                threshold       REAL    NOT NULL,
                prior_threshold REAL    NOT NULL,
                noise_level     REAL    NOT NULL,
                fisher_ratio    REAL    NOT NULL,
                weight          REAL    NOT NULL,
                n_samples       INTEGER NOT NULL,
                calibrated_at   TIMESTAMP NOT NULL
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_thresh_hist_concept_tier
            ON threshold_history (concept, tier)
        """)

    def _prune_buffer(self, concept: str, tier: str):
        """Keep only the last N_RECAL_WINDOW rows per (concept, tier)."""
        db.execute("""
            DELETE FROM calibration_buffer
            WHERE concept = ? AND tier = ?
              AND id NOT IN (
                  SELECT id FROM calibration_buffer
                  WHERE concept = ? AND tier = ?
                  ORDER BY recorded_at DESC
                  LIMIT ?
              )
        """, (concept, tier, concept, tier, N_RECAL_WINDOW))

    def _buffer_count(self, concept: str, tier: str) -> int:
        row = db.fetch_one(
            "SELECT COUNT(*) AS n FROM calibration_buffer WHERE concept = ? AND tier = ?",
            (concept, tier)
        )
        return int(row["n"]) if row else 0

    def _load_buffer(self, concept: str, tier: str) -> np.ndarray:
        rows = db.fetch_all(
            "SELECT posterior FROM calibration_buffer "
            "WHERE concept = ? AND tier = ? ORDER BY recorded_at DESC",
            (concept, tier)
        )
        return np.array([r["posterior"] for r in rows], dtype=float)

    def _save_threshold(
        self, concept: str, tier: str, threshold: float, prior: float,
        noise_level: float, fisher_ratio: float, weight: float, n_samples: int
    ):
        db.execute("""
            INSERT INTO threshold_history
                (concept, tier, threshold, prior_threshold,
                 noise_level, fisher_ratio, weight, n_samples, calibrated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (concept, tier, threshold, prior,
              noise_level, fisher_ratio, weight, n_samples,
              datetime.now()))


# Module-level singleton
calibrator = ThresholdCalibrator()
