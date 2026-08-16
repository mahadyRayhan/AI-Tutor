# backend/app/core/prereq_headstart.py
#
# Prerequisite-Coupled Priors  ("Head Start")
# ───────────────────────────────────────────
# A student who has *earned* mastery of a prerequisite (e.g. Variables) is not a
# total beginner at a topic that depends on it (e.g. Loops). This module gives such
# a topic a small, capped boost on its *starting* BKT prior — never on the finish line.
#
# Design invariants (each maps to a rule in the spec):
#   1. Only CERTIFIED prerequisites propagate  (ever_certified == 1). A head start
#      contributes zero evidence, so it can never itself become a certified source —
#      the boost cannot snowball down the chain.
#   2. The boost is proportional to the prerequisite's *effective* mastery and capped
#      per tier, well below θ_cert = 0.95.
#   3. Applied once, at the first genuine BKT touch of the new topic (row creation).
#   4. Real performance takes over: wrong answers and between-session decay both pull
#      toward the DEFAULT prior (bkt_model._apply_decay anchors to P_L0, not the seed),
#      so a head start can never hold a score up against real failure.
#   5. A head start can NEVER certify — certification needs n_evidence ≥ N_MIN real
#      answers on the topic itself. The seed moves the starting line, not the finish.
#   6. Only doubt flows downhill: a downward self-assessment on a prerequisite lowers
#      that prerequisite's effective mastery, which lowers the head start it passes on —
#      but only while the dependent has zero evidence of its own (clawback is monotone
#      and can only ever reduce, never raise, a neighbour's prior).
#   7. The prerequisite GATE reads certified mastery (ever_certified / is_mastered),
#      never the head-started posterior, so a head start can never unlock content.
#
# Certification therefore remains exactly the evidence-gated quantity it was; this
# module only changes the *provenance of the initial prior*.

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Per-prerequisite transfer coefficient per tier. Near-transfer of declarative
# knowledge is real (quiz); procedural transfer is modest (micro); writing working
# code does not transfer from adjacent recall (code ≈ none).
KAPPA = {"quiz": 0.15, "micro": 0.06, "code": 0.01}

# Hard ceiling on the RESULTING seeded prior per tier. All are far below θ_cert=0.95,
# and the code tier is left essentially at its cold-start prior.
#   quiz : default 0.30 → cap 0.50  (max head start +0.20)
#   micro: default 0.05 → cap 0.15  (max head start +0.10)
#   code : default 0.01 → cap 0.02  (max head start +0.01)
HS_PRIOR_CAP = {"quiz": 0.50, "micro": 0.15, "code": 0.02}

TIERS = ("quiz", "micro", "code")

# Lazily-registered Neo4j handle (set by main.py on startup). If absent — e.g. in
# offline scripts or tests — the module degrades gracefully to "no head start".
_graph_db = None


def set_graph_db(gdb) -> None:
    """Register the shared Neo4j graph handle (called once at app startup)."""
    global _graph_db
    _graph_db = gdb


# ─────────────────────────────────────────────────────────────────────────────
# Graph queries
# ─────────────────────────────────────────────────────────────────────────────

def _prerequisites_of(concept: str) -> list[str]:
    """Names of the direct prerequisites of `concept` (topics it REQUIRES)."""
    if _graph_db is None:
        return []
    cypher = (
        "MATCH (target) WHERE (toLower(target.name) = toLower($name) "
        "  OR toLower(target.name) CONTAINS toLower($name) "
        "  OR toLower($name) CONTAINS toLower(target.name)) AND NOT target:Section "
        "MATCH (target)-[:REQUIRES_UNDERSTANDING_OF]->(req) "
        "RETURN DISTINCT req.name AS name"
    )
    try:
        rows = _graph_db.execute_query(cypher, {"name": concept})
        return [r["name"] for r in rows if r.get("name")]
    except Exception as e:
        logger.warning(f"[HEADSTART] prereq query failed for '{concept}': {e}")
        return []


def _dependents_of(concept: str) -> list[str]:
    """Names of topics that directly REQUIRE `concept` (the reverse edge)."""
    if _graph_db is None:
        return []
    cypher = (
        "MATCH (prereq) WHERE (toLower(prereq.name) = toLower($name) "
        "  OR toLower(prereq.name) CONTAINS toLower($name) "
        "  OR toLower($name) CONTAINS toLower(prereq.name)) AND NOT prereq:Section "
        "MATCH (dependent)-[:REQUIRES_UNDERSTANDING_OF]->(prereq) "
        "RETURN DISTINCT dependent.name AS name"
    )
    try:
        rows = _graph_db.execute_query(cypher, {"name": concept})
        return [r["name"] for r in rows if r.get("name")]
    except Exception as e:
        logger.warning(f"[HEADSTART] dependent query failed for '{concept}': {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Boost computation
# ─────────────────────────────────────────────────────────────────────────────

def _prereq_strength(username: str, prereq: str) -> float:
    """
    Transfer strength s ∈ [0, 1] a certified prerequisite contributes.

    Rule 1 gate: only ever_certified prerequisites count at all.
    Rule 2/6 scaling: strength scales with the prerequisite's *effective* mastery
    (P_eff composite, which a downward slider can only lower), normalised by the
    composite ceiling 0.95. So doubt on a prerequisite reduces what it passes on.
    """
    from app.core import bkt_model

    row = bkt_model._read_row(username, prereq)
    # Rule 1 gate through the single source of truth: only a CURRENTLY-certified
    # prerequisite transfers a head start, so a decayed prerequisite stops seeding
    # its dependents (consistent with the revocable access gate). Under the
    # earned_credentials ablation this reverts to the sticky ever_certified flag.
    if not row or not bkt_model.is_current_certified(username, prereq):
        return 0.0
    eff = bkt_model.get_effective_mastery(username, prereq)
    composite = eff.get("composite", 0.0)  # ∈ [0, 0.95]
    return max(0.0, min(1.0, composite / 0.95))


def compute_seeds(username: str, concept: str) -> dict:
    """
    Compute the head-started starting priors for `concept`.

    Returns {seeds:{tier:prior}, hs:{tier:boost}, sources:[[prereq, strength], ...]}.
    With no certified prerequisites, seeds fall back to the default P_L0 and hs=0.
    """
    from app.core.bkt_model import EVIDENCE_CONFIG

    contributors: list[tuple[str, float]] = []
    for p in _prerequisites_of(concept):
        s = _prereq_strength(username, p)
        if s > 0.0:
            contributors.append((p, round(s, 4)))

    seeds, hs = {}, {}
    for tier in TIERS:
        default = EVIDENCE_CONFIG[tier]["P_L0"]
        boost = sum(KAPPA[tier] * s for _, s in contributors)
        seeded = min(default + boost, HS_PRIOR_CAP[tier])
        seeds[tier] = round(seeded, 6)
        hs[tier] = round(max(0.0, seeded - default), 6)

    return {"seeds": seeds, "hs": hs, "sources": contributors}


# ─────────────────────────────────────────────────────────────────────────────
# Apply at first touch (rule 3)
# ─────────────────────────────────────────────────────────────────────────────

def seeds_for_new_row(username: str, concept: str) -> dict | None:
    """
    Called by bkt_model.update() at row creation. Returns the seed/hs dict if a
    head start applies (any tier boosted), else None (caller uses plain defaults).
    Logs the seed event. Never raises.
    """
    try:
        result = compute_seeds(username, concept)
        if not result["sources"] or sum(result["hs"].values()) <= 0.0:
            return None
        _log(username, concept, "seed", result)
        logger.info(
            f"🌱 [HEADSTART] {username}/{concept}: "
            f"seeds={result['seeds']} hs={result['hs']} "
            f"from {[p for p, _ in result['sources']]}"
        )
        return result
    except Exception as e:
        logger.warning(f"[HEADSTART] seed computation failed for '{concept}': {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Clawback on prerequisite doubt (rule 6)
# ─────────────────────────────────────────────────────────────────────────────

def reduce_on_prereq_doubt(username: str, prereq_concept: str) -> list[str]:
    """
    A downward self-assessment landed on `prereq_concept`. Recompute the head start
    of every dependent that still has ZERO evidence of its own, lowering (never
    raising) its seeded priors. Returns the list of dependents actually clawed back.

    Frozen-once-earned (rule 3): a dependent with any real evidence is never touched.
    Monotone (rule 6): because effective mastery only drops on a downward slider, the
    recomputed seed is ≤ the stored prior; we additionally guard with an explicit min.
    """
    from app.core import bkt_model
    from app.db.sqlite_db import db

    touched: list[str] = []
    try:
        dependents = _dependents_of(prereq_concept)
    except Exception:
        return touched

    for dep in dependents:
        try:
            row = bkt_model._read_row(username, dep)
            if not row:
                continue
            n_total = (row["n_evidence_quiz"] or 0) + (row["n_evidence_micro"] or 0) + (row["n_evidence_code"] or 0)
            if n_total > 0:
                continue  # rule 3: earned evidence is frozen
            hs_now = (row["hs_quiz"] or 0) + (row["hs_micro"] or 0) + (row["hs_code"] or 0)
            if hs_now <= 0:
                continue  # nothing was ever headstarted here

            recomputed = compute_seeds(username, dep)
            # Only ever lower each tier's stored prior toward the recomputed value.
            cur = {
                "quiz": row["p_mastery_quiz"], "micro": row["p_mastery_micro"], "code": row["p_mastery_code"],
            }
            new_seed, new_hs, changed = {}, {}, False
            from app.core.bkt_model import EVIDENCE_CONFIG
            for tier in TIERS:
                lowered = min(cur[tier], recomputed["seeds"][tier])
                new_seed[tier] = round(lowered, 6)
                new_hs[tier] = round(max(0.0, lowered - EVIDENCE_CONFIG[tier]["P_L0"]), 6)
                if lowered < cur[tier] - 1e-9:
                    changed = True
            if not changed:
                continue

            composite = round(
                new_seed["quiz"] * EVIDENCE_CONFIG["quiz"]["ceiling"]
                + new_seed["micro"] * EVIDENCE_CONFIG["micro"]["ceiling"]
                + new_seed["code"] * EVIDENCE_CONFIG["code"]["ceiling"],
                6,
            )
            db.execute(
                "UPDATE user_knowledge SET p_mastery_quiz=?, p_mastery_micro=?, p_mastery_code=?, "
                "p_mastery=?, hs_quiz=?, hs_micro=?, hs_code=? WHERE username=? AND concept=?",
                (new_seed["quiz"], new_seed["micro"], new_seed["code"], composite,
                 new_hs["quiz"], new_hs["micro"], new_hs["code"], username, dep),
            )
            _log(username, dep, "clawback",
                 {"seeds": new_seed, "hs": new_hs, "sources": recomputed["sources"]})
            touched.append(dep)
            logger.info(
                f"⬇️  [HEADSTART] clawback {username}/{dep} after doubt on "
                f"'{prereq_concept}': seeds→{new_seed}"
            )
        except Exception as e:
            logger.warning(f"[HEADSTART] clawback failed for dependent '{dep}': {e}")

    return touched


# ─────────────────────────────────────────────────────────────────────────────
# Explain (endpoint + visual)
# ─────────────────────────────────────────────────────────────────────────────

def explain(username: str, concept: str) -> dict:
    """
    Human-/UI-readable account of the head start for one topic: its prerequisites,
    which are certified, the resulting seeds, and the certification wall that the
    head start can never cross.
    """
    from app.core import bkt_model
    from app.core.bkt_model import EVIDENCE_CONFIG, N_MIN, THETA_CERTIFY

    prereqs = _prerequisites_of(concept)
    prereq_states = []
    for p in prereqs:
        r = bkt_model._read_row(username, p)
        certified = bool(r["ever_certified"]) if r else False
        strength = _prereq_strength(username, p)
        prereq_states.append({
            "concept": p,
            "certified": certified,
            "strength": round(strength, 4),
            "contributes": certified and strength > 0,
        })

    computed = compute_seeds(username, concept)
    row = bkt_model._read_row(username, concept)
    n_evidence = {
        "quiz": (row["n_evidence_quiz"] or 0) if row else 0,
        "micro": (row["n_evidence_micro"] or 0) if row else 0,
        "code": (row["n_evidence_code"] or 0) if row else 0,
    }
    return {
        "concept": concept,
        "prerequisites": prereq_states,
        "seeds": computed["seeds"],
        "head_start": computed["hs"],
        "default_priors": {t: EVIDENCE_CONFIG[t]["P_L0"] for t in TIERS},
        "prior_caps": HS_PRIOR_CAP,
        "n_evidence": n_evidence,
        "certification": {
            "theta_cert": THETA_CERTIFY,
            "n_min": N_MIN,
            "note": "A head start seeds the prior only; certification still requires "
                    f"{N_MIN} real correct answers per tier and P̃ ≥ {THETA_CERTIFY}.",
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Telemetry
# ─────────────────────────────────────────────────────────────────────────────

def _log(username: str, concept: str, action: str, result: dict) -> None:
    try:
        from app.core import telemetry
        telemetry.log_headstart(
            username, concept, action,
            result["hs"], result["seeds"], result["sources"],
        )
    except Exception as e:
        logger.debug(f"[HEADSTART] telemetry log skipped: {e}")
