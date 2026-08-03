# backend/app/core/mcn.py
#
# Metacognitive Calibration Network (MCN)
# ───────────────────────────────────────
# A small, discrete Bayesian network that infers a student's latent metacognitive
# CALIBRATION state per concept by fusing signals the system already collects:
# self-report, performance, help-seeking behaviour, and affect.
#
# The modelling move that makes this a *Bayesian network* (not another heuristic):
# two hidden variables are separated and the self-report is modelled as JOINTLY
# caused by both —
#
#     K = true KNOWLEDGE          (low | med | high)   ← soft prior from BKT
#     C = CALIBRATION             (over | cal | under)  ← the SRL construct we infer
#
# DAG:
#         K ──► P   (performance:  quiz/micro/code correctness)
#         K ──► B   (behaviour:    help-seeking / fluency)
#     (K,C) ──► S   (self-report:  JoL confidence / dashboard slider)   ← the crux
#     (K,C) ──► A   (affect:       frustration level)                   [optional]
#
# Calibration is encoded as the BIAS of the self-report relative to true knowledge:
#   • C=cal   → S tracks K
#   • C=over  → S skews HIGH regardless of K
#   • C=under → S skews LOW  regardless of K
#
# This lets the network "explain away" a low self-report from a strong performer as
# UNDER-confidence rather than weakness — the distinction a linear blend cannot make,
# and the one that drives the right SRL intervention (reassure vs. keep practising).
#
# Inference is EXACT by enumeration over the 3×3 = 9 latent (K,C) assignments — no
# solver, no numpy, no external dependency. CPTs are supplied by a CptSet (see
# mcn_cpts.py / mcn_cpts.json in Phase 2); this module ships with hand-elicited
# defaults so it works cold, with zero training data.
#
# This module is PURE: it reads no database and touches no app state. The bridge to
# live data lives in mcn_evidence.py (Phase 3).

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# Model topology (states + parents). CPT *values* live in the CptSet below.
# ─────────────────────────────────────────────────────────────────────────────

STATES: Dict[str, List[str]] = {
    "K": ["low", "med", "high"],            # latent knowledge
    "C": ["over", "cal", "under"],          # latent calibration (query target)
    "S": ["low", "med", "high"],            # observed self-report
    "P": ["poor", "mixed", "good"],         # observed performance
    "B": ["struggling", "normal", "fluent"],# observed behaviour
    "A": ["frustrated", "neutral", "engaged"],  # observed affect (optional)
}

LATENT: Tuple[str, ...] = ("K", "C")
OBSERVABLE: Tuple[str, ...] = ("S", "P", "B", "A")

# Parents of each observable leaf (latent nodes have no parents).
PARENTS: Dict[str, Tuple[str, ...]] = {
    "S": ("K", "C"),
    "P": ("K",),
    "B": ("K",),
    "A": ("K", "C"),
}

# Human-readable labels for the calibration verdict.
C_LABELS = {
    "over": "Overconfident",
    "cal": "Well-calibrated",
    "under": "Underconfident",
}


# ─────────────────────────────────────────────────────────────────────────────
# CPT container
# ─────────────────────────────────────────────────────────────────────────────

def _normalize(dist: Dict[str, float]) -> Dict[str, float]:
    """Normalise a categorical distribution to sum to 1.0 (raw weights allowed)."""
    total = float(sum(dist.values()))
    if total <= 0:
        # Degenerate row → uniform, so inference never divides by zero.
        n = len(dist)
        return {k: 1.0 / n for k in dist}
    return {k: v / total for k, v in dist.items()}


@dataclass
class CptSet:
    """
    A complete parameterisation of the MCN. All tables are stored as *raw weights*
    and normalised on construction, so hand-authored numbers need not sum to 1.

      prior_K : {k_state: w}
      prior_C : {c_state: w}
      S_given_KC : {(k, c): {s_state: w}}
      P_given_K  : {k: {p_state: w}}
      B_given_K  : {k: {b_state: w}}
      A_given_KC : {(k, c): {a_state: w}}
    """
    prior_K: Dict[str, float]
    prior_C: Dict[str, float]
    S_given_KC: Dict[Tuple[str, str], Dict[str, float]]
    P_given_K: Dict[str, Dict[str, float]]
    B_given_K: Dict[str, Dict[str, float]]
    A_given_KC: Dict[Tuple[str, str], Dict[str, float]]
    name: str = "default"

    def __post_init__(self):
        self.prior_K = _normalize(self.prior_K)
        self.prior_C = _normalize(self.prior_C)
        self.S_given_KC = {kc: _normalize(d) for kc, d in self.S_given_KC.items()}
        self.P_given_K = {k: _normalize(d) for k, d in self.P_given_K.items()}
        self.B_given_K = {k: _normalize(d) for k, d in self.B_given_K.items()}
        self.A_given_KC = {kc: _normalize(d) for kc, d in self.A_given_KC.items()}
        self.validate()

    # -- validation ----------------------------------------------------------
    def validate(self) -> None:
        """Raise ValueError if any table is missing a required parent combination
        or references an unknown state. Called on construction and by Phase-2 loader."""
        for k in STATES["K"]:
            if k not in self.prior_K:
                raise ValueError(f"prior_K missing state '{k}'")
            _check_states("P_given_K", self.P_given_K.get(k), "P")
            _check_states("B_given_K", self.B_given_K.get(k), "B")
        for c in STATES["C"]:
            if c not in self.prior_C:
                raise ValueError(f"prior_C missing state '{c}'")
        for k in STATES["K"]:
            for c in STATES["C"]:
                _check_states("S_given_KC", self.S_given_KC.get((k, c)), "S", (k, c))
                _check_states("A_given_KC", self.A_given_KC.get((k, c)), "A", (k, c))

    def leaf_prob(self, leaf: str, value: str, k: str, c: str) -> float:
        """P(leaf = value | K=k, C=c) for a single observed leaf."""
        if leaf == "S":
            return self.S_given_KC[(k, c)][value]
        if leaf == "P":
            return self.P_given_K[k][value]
        if leaf == "B":
            return self.B_given_K[k][value]
        if leaf == "A":
            return self.A_given_KC[(k, c)][value]
        raise KeyError(f"Unknown leaf '{leaf}'")


def _check_states(table: str, dist: Optional[Dict[str, float]], node: str,
                  ctx=None) -> None:
    if dist is None:
        raise ValueError(f"{table} missing row for {ctx if ctx else node}")
    want = set(STATES[node])
    got = set(dist.keys())
    if got != want:
        raise ValueError(
            f"{table}[{ctx if ctx else ''}] states {sorted(got)} != expected {sorted(want)}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Inference
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MCNResult:
    posterior_C: Dict[str, float]        # P(C | evidence)
    posterior_K: Dict[str, float]        # P(K | evidence)
    map_C: str                           # argmax calibration state
    map_K: str                           # argmax knowledge state
    confidence: float                    # P of the MAP calibration state
    label: str                           # human label of map_C
    evidence_used: Dict[str, str]        # the evidence that was actually observed
    joint: Dict[Tuple[str, str], float] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "posterior_C": self.posterior_C,
            "posterior_K": self.posterior_K,
            "map_C": self.map_C,
            "map_K": self.map_K,
            "confidence": round(self.confidence, 6),
            "label": self.label,
            "evidence_used": self.evidence_used,
        }


def infer(evidence: Dict[str, str],
          cpt: "CptSet",
          k_prior: Optional[Dict[str, float]] = None,
          c_prior: Optional[Dict[str, float]] = None) -> MCNResult:
    """
    Exact posterior inference over the latent (K, C) given observed evidence.

    Args:
      evidence : subset of {"S","P","B","A"} → observed state. Unobserved leaves are
                 marginalised out automatically (they simply drop from the product).
      cpt      : the CptSet parameterising the network.
      k_prior  : OPTIONAL soft prior over K supplied by the BKT layer (evidence
                 adapter). When given it REPLACES cpt.prior_K — this is how the
                 student's tracked knowledge enters the network. Normalised here.
      c_prior  : OPTIONAL prior over C replacing cpt.prior_C. Used by the synthetic
                 validation to match the (uniform) sampling distribution, so recovery
                 measures the evidence's discriminative power rather than the prior.
                 Left as cpt.prior_C in production.

    Returns an MCNResult with posteriors over C and K.
    """
    # Validate/clean evidence: ignore unknown leaves or out-of-vocab states rather
    # than crashing a live request.
    ev: Dict[str, str] = {}
    for leaf, val in (evidence or {}).items():
        if leaf in OBSERVABLE and val in STATES[leaf]:
            ev[leaf] = val

    pk = _normalize(dict(k_prior)) if k_prior else cpt.prior_K
    pc = _normalize(dict(c_prior)) if c_prior else cpt.prior_C

    joint: Dict[Tuple[str, str], float] = {}
    for k in STATES["K"]:
        for c in STATES["C"]:
            w = pk[k] * pc[c]
            for leaf, val in ev.items():
                w *= cpt.leaf_prob(leaf, val, k, c)
            joint[(k, c)] = w

    total = sum(joint.values())
    if total <= 0:
        # No evidence favoured any hypothesis (shouldn't happen with Cromwell priors);
        # fall back to the prior product so we always return a valid distribution.
        joint = {kc: pk[kc[0]] * pc[kc[1]] for kc in joint}
        total = sum(joint.values())
    joint = {kc: w / total for kc, w in joint.items()}

    post_C = {c: 0.0 for c in STATES["C"]}
    post_K = {k: 0.0 for k in STATES["K"]}
    for (k, c), w in joint.items():
        post_C[c] += w
        post_K[k] += w

    map_C = max(post_C, key=post_C.get)
    map_K = max(post_K, key=post_K.get)

    return MCNResult(
        posterior_C={c: round(v, 6) for c, v in post_C.items()},
        posterior_K={k: round(v, 6) for k, v in post_K.items()},
        map_C=map_C,
        map_K=map_K,
        confidence=post_C[map_C],
        label=C_LABELS[map_C],
        evidence_used=ev,
        joint={kc: round(w, 6) for kc, w in joint.items()},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Hand-elicited default CPTs  (Phase 1 — externalised to JSON in Phase 2)
# ─────────────────────────────────────────────────────────────────────────────
#
# Values are raw weights (normalised on construction). They encode the qualitative
# story documented at the top of the file; they are NOT yet fit to data. Phase 5b
# re-estimates them from jol_log after the study.

DEFAULT_CPT = CptSet(
    name="default_v1",
    # Cold-start prior over knowledge (usually overridden by the BKT adapter).
    prior_K={"low": 0.50, "med": 0.30, "high": 0.20},
    # Most students roughly calibrated; symmetric tails.
    prior_C={"over": 0.25, "cal": 0.50, "under": 0.25},

    # P(S | K, C) — the crux. Calibrated tracks K; over skews high; under skews low.
    S_given_KC={
        # Calibrated: self-report tracks true knowledge.
        ("low", "cal"):   {"low": 0.70, "med": 0.25, "high": 0.05},
        ("med", "cal"):   {"low": 0.20, "med": 0.60, "high": 0.20},
        ("high", "cal"):  {"low": 0.05, "med": 0.25, "high": 0.70},
        # Overconfident: reports high regardless of knowledge.
        ("low", "over"):  {"low": 0.15, "med": 0.35, "high": 0.50},
        ("med", "over"):  {"low": 0.05, "med": 0.25, "high": 0.70},
        ("high", "over"): {"low": 0.02, "med": 0.18, "high": 0.80},
        # Underconfident: reports low regardless of knowledge (incl. competent-but-doubting).
        ("low", "under"): {"low": 0.85, "med": 0.13, "high": 0.02},
        ("med", "under"): {"low": 0.60, "med": 0.35, "high": 0.05},
        ("high", "under"):{"low": 0.50, "med": 0.40, "high": 0.10},
    },

    # P(P | K) — performance reflects knowledge (guess/slip-shaped).
    P_given_K={
        "low":  {"poor": 0.65, "mixed": 0.28, "good": 0.07},
        "med":  {"poor": 0.20, "mixed": 0.55, "good": 0.25},
        "high": {"poor": 0.05, "mixed": 0.30, "good": 0.65},
    },

    # P(B | K) — help-seeking / fluency reflects knowledge.
    B_given_K={
        "low":  {"struggling": 0.60, "normal": 0.33, "fluent": 0.07},
        "med":  {"struggling": 0.25, "normal": 0.55, "fluent": 0.20},
        "high": {"struggling": 0.08, "normal": 0.37, "fluent": 0.55},
    },

    # P(A | K, C) — frustration rises with low knowledge and under-confidence,
    # falls with (false) over-confidence.
    A_given_KC={
        ("low", "cal"):   {"frustrated": 0.50, "neutral": 0.35, "engaged": 0.15},
        ("med", "cal"):   {"frustrated": 0.25, "neutral": 0.50, "engaged": 0.25},
        ("high", "cal"):  {"frustrated": 0.10, "neutral": 0.40, "engaged": 0.50},
        ("low", "over"):  {"frustrated": 0.30, "neutral": 0.45, "engaged": 0.25},
        ("med", "over"):  {"frustrated": 0.15, "neutral": 0.45, "engaged": 0.40},
        ("high", "over"): {"frustrated": 0.07, "neutral": 0.38, "engaged": 0.55},
        ("low", "under"): {"frustrated": 0.65, "neutral": 0.27, "engaged": 0.08},
        ("med", "under"): {"frustrated": 0.45, "neutral": 0.42, "engaged": 0.13},
        ("high", "under"):{"frustrated": 0.30, "neutral": 0.45, "engaged": 0.25},
    },
)


def default_network() -> CptSet:
    """Return the shipped default parameterisation."""
    return DEFAULT_CPT
