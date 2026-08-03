# backend/app/core/mcn_cpts.py
#
# CPT config store for the Metacognitive Calibration Network (Phase 2).
# ─────────────────────────────────────────────────────────────────────
# The engine (mcn.py) is pure math with hand-elicited default CPTs baked in. This
# module externalises those numbers to a JSON file so they can be tuned WITHOUT a
# code redeploy — the operational constraint of a classroom deployment that can't be
# taken down mid-study. It also provides the serialization used by the Phase-5b
# offline refit (learning CPTs from jol_log after the study).
#
# JSON has no tuple keys, so the two conditional-on-(K,C) tables (S, A) are encoded
# as nested objects  K -> C -> {state: weight}  and flattened back to {(k,c): dist}
# on load. All weights are RAW (need not sum to 1); CptSet normalises on construction.
#
# Hot reload: get_network() caches by file mtime, so editing mcn_cpts.json is picked
# up on the next inference — no restart. Any load/validation error falls back to the
# baked-in default, so a malformed edit can NEVER take the live SRL layer down.

from __future__ import annotations

import json
import logging
import os
from typing import Optional

from app.core import mcn
from app.core.mcn import CptSet, STATES

logger = logging.getLogger(__name__)

# Default location of the editable CPT file (sits next to this module).
CPT_PATH = os.path.join(os.path.dirname(__file__), "mcn_cpts.json")


# ─────────────────────────────────────────────────────────────────────────────
# (de)serialization
# ─────────────────────────────────────────────────────────────────────────────

def cptset_to_dict(cpt: CptSet) -> dict:
    """Serialize a CptSet to a JSON-safe dict (nested K->C for the (K,C) tables)."""
    def nest(flat):
        out: dict = {}
        for (k, c), dist in flat.items():
            out.setdefault(k, {})[c] = dist
        return out

    return {
        "name": cpt.name,
        "prior_K": cpt.prior_K,
        "prior_C": cpt.prior_C,
        "S_given_KC": nest(cpt.S_given_KC),
        "P_given_K": cpt.P_given_K,
        "B_given_K": cpt.B_given_K,
        "A_given_KC": nest(cpt.A_given_KC),
    }


def cptset_from_dict(d: dict) -> CptSet:
    """Build a CptSet from a config dict. Raises ValueError on structural problems
    (missing keys, wrong states) — surfaced by the loader's fallback."""
    required = {"prior_K", "prior_C", "S_given_KC", "P_given_K", "B_given_K", "A_given_KC"}
    missing = required - set(d)
    if missing:
        raise ValueError(f"CPT config missing keys: {sorted(missing)}")

    def flatten(nested, table):
        flat = {}
        for k in STATES["K"]:
            if k not in nested:
                raise ValueError(f"{table} missing K-row '{k}'")
            for c in STATES["C"]:
                if c not in nested[k]:
                    raise ValueError(f"{table} missing ({k},{c})")
                flat[(k, c)] = nested[k][c]
        return flat

    return CptSet(
        name=d.get("name", "loaded"),
        prior_K=d["prior_K"],
        prior_C=d["prior_C"],
        S_given_KC=flatten(d["S_given_KC"], "S_given_KC"),
        P_given_K=d["P_given_K"],
        B_given_K=d["B_given_K"],
        A_given_KC=flatten(d["A_given_KC"], "A_given_KC"),
    )  # CptSet.__post_init__ normalises + validates


def write_default_json(path: str = CPT_PATH) -> None:
    """(Re)generate the JSON store from the engine's baked-in default CPTs."""
    with open(path, "w") as f:
        json.dump(cptset_to_dict(mcn.default_network()), f, indent=2)
    logger.info(f"[MCN] wrote default CPTs → {path}")


# ─────────────────────────────────────────────────────────────────────────────
# loading + hot-reload cache
# ─────────────────────────────────────────────────────────────────────────────

def load_cpts(path: str = CPT_PATH) -> CptSet:
    """
    Load a CptSet from JSON. On ANY problem (missing file, bad JSON, failed
    validation) log a warning and return the baked-in default — the live SRL layer
    must never break because of a bad config edit.
    """
    try:
        with open(path) as f:
            data = json.load(f)
        cpt = cptset_from_dict(data)
        logger.info(f"[MCN] loaded CPTs '{cpt.name}' from {path}")
        return cpt
    except FileNotFoundError:
        logger.warning(f"[MCN] no CPT file at {path}; using baked-in default.")
        return mcn.default_network()
    except Exception as e:
        logger.warning(f"[MCN] CPT load/validate failed ({e}); using baked-in default.")
        return mcn.default_network()


# module-level cache keyed by file mtime, so edits are picked up without a restart
_CACHE: dict = {"cpt": None, "mtime": None, "path": None}


def get_network(path: str = CPT_PATH, reload: bool = False) -> CptSet:
    """
    Return the active CptSet, reloading only when the JSON file changes on disk
    (or reload=True). Falls back to the default when the file is absent.
    """
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = None

    fresh = (
        reload
        or _CACHE["cpt"] is None
        or _CACHE["path"] != path
        or _CACHE["mtime"] != mtime
    )
    if fresh:
        _CACHE["cpt"] = load_cpts(path)
        _CACHE["mtime"] = mtime
        _CACHE["path"] = path
    return _CACHE["cpt"]
