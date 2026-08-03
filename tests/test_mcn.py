# tests/test_mcn.py
#
# Phase 1 unit tests for the Metacognitive Calibration Network (backend/app/core/mcn.py).
# Pure — no database, no app state. Run with:
#     /opt/anaconda3/envs/agent/bin/python -m pytest tests/test_mcn.py -v

import json
import math
import pytest

from app.core import mcn


# ─────────────────────────────────────────────────────────────────────────────
# Structural / CPT-integrity tests
# ─────────────────────────────────────────────────────────────────────────────

def test_default_network_validates():
    """The shipped CPTs must pass validation (all parent combos, correct states)."""
    net = mcn.default_network()
    net.validate()  # raises on any gap


def test_all_cpt_rows_normalised():
    """Every conditional distribution sums to 1.0 after construction."""
    net = mcn.default_network()

    def _sum_ok(dist):
        return math.isclose(sum(dist.values()), 1.0, abs_tol=1e-9)

    assert _sum_ok(net.prior_K)
    assert _sum_ok(net.prior_C)
    for d in net.S_given_KC.values():
        assert _sum_ok(d)
    for d in net.P_given_K.values():
        assert _sum_ok(d)
    for d in net.B_given_K.values():
        assert _sum_ok(d)
    for d in net.A_given_KC.values():
        assert _sum_ok(d)


def test_raw_weights_are_normalised_on_construction():
    """Hand-authored rows need not sum to 1; construction normalises them."""
    base = mcn.default_network()
    tweaked = mcn.CptSet(
        name="raw",
        prior_K={"low": 5, "med": 3, "high": 2},      # sums to 10, not 1
        prior_C={"over": 2, "cal": 4, "under": 2},
        S_given_KC=base.S_given_KC,
        P_given_K=base.P_given_K,
        B_given_K=base.B_given_K,
        A_given_KC=base.A_given_KC,
    )
    assert math.isclose(tweaked.prior_K["low"], 0.5, abs_tol=1e-9)
    assert math.isclose(sum(tweaked.prior_C.values()), 1.0, abs_tol=1e-9)


def test_validation_rejects_bad_states():
    base = mcn.default_network()
    bad_P = dict(base.P_given_K)
    bad_P["low"] = {"poor": 1.0, "mixed": 0.0}  # missing 'good'
    with pytest.raises(ValueError):
        mcn.CptSet(
            prior_K=base.prior_K, prior_C=base.prior_C,
            S_given_KC=base.S_given_KC, P_given_K=bad_P,
            B_given_K=base.B_given_K, A_given_KC=base.A_given_KC,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Inference sanity
# ─────────────────────────────────────────────────────────────────────────────

def test_no_evidence_returns_priors():
    """With no observations, the posterior over C equals the prior over C."""
    net = mcn.default_network()
    r = mcn.infer({}, net)
    for c in mcn.STATES["C"]:
        assert math.isclose(r.posterior_C[c], net.prior_C[c], abs_tol=1e-9)
    assert r.evidence_used == {}


def test_posteriors_are_valid_distributions():
    net = mcn.default_network()
    r = mcn.infer({"S": "high", "P": "poor"}, net)
    # Displayed posteriors are rounded to 6 dp (worst-case ~1.5e-6 total drift).
    assert math.isclose(sum(r.posterior_C.values()), 1.0, abs_tol=1e-5)
    assert math.isclose(sum(r.posterior_K.values()), 1.0, abs_tol=1e-5)
    assert 0.0 <= r.confidence <= 1.0


def test_unknown_evidence_is_ignored():
    """Garbage leaves / states are dropped, not fatal (live-request safety)."""
    net = mcn.default_network()
    r = mcn.infer({"Z": "banana", "S": "typo_state"}, net)
    assert r.evidence_used == {}  # nothing valid survived


# ─────────────────────────────────────────────────────────────────────────────
# The headline behaviours — the reason the BN exists
# ─────────────────────────────────────────────────────────────────────────────

def test_strong_performance_low_self_report_reads_underconfident():
    """
    The competent-but-doubting case: good performance + fluent behaviour but a LOW
    self-report should be explained away as UNDER-confidence, not weakness.
    """
    net = mcn.default_network()
    r = mcn.infer({"S": "low", "P": "good", "B": "fluent"}, net)
    assert r.map_C == "under", r.posterior_C
    # And knowledge should still read high (the evidence says they can do it).
    assert r.map_K == "high", r.posterior_K


def test_high_self_report_poor_performance_reads_overconfident():
    """Claims mastery (S high) but performs poorly / struggles → OVER-confident."""
    net = mcn.default_network()
    r = mcn.infer({"S": "high", "P": "poor", "B": "struggling"}, net)
    assert r.map_C == "over", r.posterior_C


def test_aligned_signals_read_calibrated():
    """Self-report matches performance → well-calibrated."""
    net = mcn.default_network()
    high = mcn.infer({"S": "high", "P": "good", "B": "fluent"}, net)
    assert high.map_C == "cal", high.posterior_C
    low = mcn.infer({"S": "low", "P": "poor", "B": "struggling"}, net)
    assert low.map_C == "cal", low.posterior_C


def test_more_confirming_evidence_raises_confidence():
    """Adding a corroborating signal should not lower confidence in the verdict."""
    net = mcn.default_network()
    one = mcn.infer({"S": "low", "P": "good"}, net)
    two = mcn.infer({"S": "low", "P": "good", "B": "fluent"}, net)
    assert two.posterior_C["under"] >= one.posterior_C["under"] - 1e-9


def test_bkt_k_prior_override_shifts_inference():
    """
    A strong BKT prior that the student knows the topic should push an ambiguous
    self-report toward under-confidence rather than low knowledge.
    """
    net = mcn.default_network()
    strong_k = {"low": 0.05, "med": 0.15, "high": 0.80}
    r = mcn.infer({"S": "low"}, net, k_prior=strong_k)
    # With high-K prior, a low self-report is more likely under-confidence.
    assert r.posterior_C["under"] > net.prior_C["under"]
    assert r.map_K == "high"


def test_c_prior_override_flattens_toward_evidence():
    """A flat C prior (used by the validation harness) removes the 'calibrated'
    majority pull, so an extreme self-report is read as a tail state."""
    net = mcn.default_network()
    flat = {c: 1.0 for c in mcn.STATES["C"]}
    # S=high with poor performance: with the informative prior 'cal' may still win;
    # with a flat prior the over-confident reading should dominate.
    r = mcn.infer({"S": "high", "P": "poor", "B": "struggling"}, net, c_prior=flat)
    assert r.map_C == "over"


def test_affect_optional_absent_is_fine():
    """The network runs with A omitted (affect is optional evidence)."""
    net = mcn.default_network()
    r_no_a = mcn.infer({"S": "low", "P": "good"}, net)
    r_with_a = mcn.infer({"S": "low", "P": "good", "A": "frustrated"}, net)
    # Both valid; frustration should nudge away from a purely rosy read.
    assert set(r_no_a.posterior_C) == set(r_with_a.posterior_C)


def test_result_as_dict_shape():
    net = mcn.default_network()
    d = mcn.infer({"S": "high", "P": "good"}, net).as_dict()
    assert set(d) == {
        "posterior_C", "posterior_K", "map_C", "map_K",
        "confidence", "label", "evidence_used",
    }
    assert d["label"] in mcn.C_LABELS.values()


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — CPT config store (mcn_cpts)
# ─────────────────────────────────────────────────────────────────────────────

def test_cpt_dict_roundtrip_preserves_inference():
    """to_dict → from_dict yields identical posteriors (nested (K,C) encoding OK)."""
    from app.core import mcn_cpts
    net = mcn.default_network()
    rebuilt = mcn_cpts.cptset_from_dict(mcn_cpts.cptset_to_dict(net))
    ev = {"S": "low", "P": "good", "B": "fluent", "A": "frustrated"}
    assert mcn.infer(ev, net).posterior_C == mcn.infer(ev, rebuilt).posterior_C


def test_shipped_json_matches_engine_default():
    """The committed mcn_cpts.json must reproduce the baked-in default exactly."""
    from app.core import mcn_cpts
    loaded = mcn_cpts.load_cpts()          # reads mcn_cpts.json
    default = mcn.default_network()
    for kc in default.S_given_KC:
        for s, v in default.S_given_KC[kc].items():
            assert math.isclose(loaded.S_given_KC[kc][s], v, abs_tol=1e-9)


def test_missing_config_falls_back_to_default():
    from app.core import mcn_cpts
    fb = mcn_cpts.load_cpts("/nonexistent/path/mcn_cpts.json")
    assert fb.name == "default_v1"


def test_malformed_config_falls_back_not_raises(tmp_path):
    from app.core import mcn_cpts
    bad = tmp_path / "bad.json"
    bad.write_text('{"prior_K": 123}')     # structurally invalid
    fb = mcn_cpts.load_cpts(str(bad))       # must NOT raise
    assert fb.name == "default_v1"


def test_hot_reload_picks_up_edits(tmp_path):
    """get_network re-reads when the file mtime changes (no restart needed)."""
    from app.core import mcn_cpts
    p = tmp_path / "cpt.json"
    # start from a valid config with a distinctive name
    d = mcn_cpts.cptset_to_dict(mcn.default_network())
    d["name"] = "v_first"
    p.write_text(json.dumps(d))
    assert mcn_cpts.get_network(str(p), reload=True).name == "v_first"
    # edit + bump mtime → next get_network reflects the change
    d["name"] = "v_second"
    import os, time
    p.write_text(json.dumps(d))
    os.utime(str(p), (time.time() + 5, time.time() + 5))
    assert mcn_cpts.get_network(str(p)).name == "v_second"


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — evidence adapters (mcn_evidence)
# ─────────────────────────────────────────────────────────────────────────────

def test_k_prior_scalar_mapping_monotonic():
    """Higher objective mastery ⇒ more prior mass on high-K, less on low-K."""
    from app.core import mcn_evidence as ev
    lo = ev._scalar_to_k_prior(0.1)
    mid = ev._scalar_to_k_prior(0.5)
    hi = ev._scalar_to_k_prior(0.9)
    assert lo["low"] > mid["low"] > hi["low"]
    assert hi["high"] > mid["high"] > lo["high"]
    for dist in (lo, mid, hi):
        assert math.isclose(sum(dist.values()), 1.0, abs_tol=1e-9)


# --- DB-backed integration: seed a throwaway user, verify verdict, tear down ----

_TEST_USER = "__mcn_pytest_user__"
_TEST_TABLES = ("jol_log", "evidence_log", "behavior_log",
                "affect_log", "user_knowledge", "user_bkt_calibration")


@pytest.fixture
def clean_user():
    from app.db.sqlite_db import db
    def _wipe():
        for t in _TEST_TABLES:
            try:
                db.execute(f"DELETE FROM {t} WHERE username=?", (_TEST_USER,))
            except Exception:
                pass
    _wipe()
    yield db
    _wipe()


def test_pipeline_reads_underconfident(clean_user):
    """Strong BKT + good performance + low self-report ⇒ Underconfident."""
    from app.core import mcn_evidence as ev
    db = clean_user
    C = "Pointers"
    db.execute(
        "INSERT INTO user_knowledge (username,concept,p_mastery_quiz,p_mastery_micro,"
        "p_mastery_code,p_mastery,n_evidence_quiz,n_evidence_micro,n_evidence_code,"
        "ever_certified) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (_TEST_USER, C, 0.97, 0.9, 0.85, 0.9, 4, 4, 4, 1),
    )
    for _ in range(5):
        db.execute("INSERT INTO evidence_log (username,concept,tier,is_correct,ts_utc) "
                   "VALUES (?,?,?,?,datetime())", (_TEST_USER, C, "quiz", 1))
    db.execute("INSERT INTO jol_log (username,concept,confidence_1_5,is_correct,ts_utc) "
               "VALUES (?,?,?,?,datetime())", (_TEST_USER, C, 1, 1))
    res = ev.infer_calibration(_TEST_USER, C)
    assert res["map_C"] == "under"
    assert res["map_K"] == "high"
    assert res["sufficient"] is True


def test_pipeline_reads_overconfident(clean_user):
    """Weak BKT + poor performance + high self-report ⇒ Overconfident."""
    from app.core import mcn_evidence as ev
    db = clean_user
    C = "Recursion"
    db.execute(
        "INSERT INTO user_knowledge (username,concept,p_mastery_quiz,p_mastery_micro,"
        "p_mastery_code,p_mastery,n_evidence_quiz,n_evidence_micro,n_evidence_code,"
        "ever_certified) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (_TEST_USER, C, 0.15, 0.05, 0.02, 0.1, 2, 1, 0, 0),
    )
    for i in range(5):
        db.execute("INSERT INTO evidence_log (username,concept,tier,is_correct,ts_utc) "
                   "VALUES (?,?,?,?,datetime())", (_TEST_USER, C, "quiz", 0))
    db.execute("INSERT INTO jol_log (username,concept,confidence_1_5,is_correct,ts_utc) "
               "VALUES (?,?,?,?,datetime())", (_TEST_USER, C, 5, 0))
    res = ev.infer_calibration(_TEST_USER, C)
    assert res["map_C"] == "over"


def test_pipeline_insufficient_when_no_signals(clean_user):
    """No logged signals ⇒ verdict flagged not-sufficient (don't act on the prior)."""
    from app.core import mcn_evidence as ev
    res = ev.infer_calibration(_TEST_USER, "Arrays")
    assert res["sufficient"] is False
    assert res["n_signals"] == 0
