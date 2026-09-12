"""Phase 4 acceptance — the edge threshold actually gates.

Run: /opt/anaconda3/envs/agent/bin/python -m pytest backend/tests/test_cac_edge_gate.py -q

The plan's acceptance criterion, verbatim: "an overconfident learner on Loops
fails the Loops->Arrays gate at P̃=0.85 while a calibrated learner passes at the
same P̃". 0.85 is the interesting value because it clears the curriculum's floor
(0.75) but not the raised bar (0.90) — so it separates the two learners on
nothing but their calibration.
"""
import pytest

from app.core import bkt_model
from app.core.cac_graph import THETA_BASE, THETA_OVERCONFIDENT


P_TILDE = 0.85   # certified under the curriculum, marginal under a raised bar


class _Row(dict):
    def __getitem__(self, k):
        return self.get(k)


def _row(p):
    return _Row(p_mastery_quiz=p, p_mastery_micro=p, p_mastery_code=p,
                last_quiz_at=None, last_micro_at=None, last_code_at=None,
                decay_quiz_lam=None, decay_micro_lam=None, decay_code_lam=None,
                ever_certified=1, is_certified=1)


@pytest.fixture
def at_085(monkeypatch):
    monkeypatch.setattr(bkt_model, "_read_row", lambda u, c: _row(P_TILDE))
    monkeypatch.setattr(bkt_model, "_apply_decay",
                        lambda p, kind, ts, lam=None: p)


# ── The acceptance criterion ────────────────────────────────────────────────

def test_calibrated_learner_passes_at_085(at_085):
    """The curriculum's own bar: 0.85 >= 0.75, so the edge is open."""
    assert bkt_model.meets_theta("calibrated", "Loops", THETA_BASE) is True


def test_overconfident_learner_fails_at_085(at_085):
    """CAC's raised bar: 0.85 < 0.90, so the same mastery no longer suffices."""
    assert bkt_model.meets_theta("overconfident", "Loops",
                                 THETA_OVERCONFIDENT) is False


def test_the_two_learners_differ_on_calibration_alone(at_085):
    """Same P̃, same concept, same decay — only the bar moved."""
    same_mastery = P_TILDE
    assert bkt_model.meets_theta("a", "Loops", THETA_BASE) != \
           bkt_model.meets_theta("b", "Loops", THETA_OVERCONFIDENT)
    assert THETA_BASE <= same_mastery < THETA_OVERCONFIDENT


# ── The raised bar may only refuse, never admit ─────────────────────────────

@pytest.mark.parametrize("p", [0.0, 0.3, 0.74, 0.749])
def test_raised_bar_cannot_admit_what_the_curriculum_refuses(p, monkeypatch):
    """The conjunct is AND, so a weak prerequisite stays refused at any theta."""
    monkeypatch.setattr(bkt_model, "_read_row", lambda u, c: _row(p))
    monkeypatch.setattr(bkt_model, "_apply_decay", lambda v, k, t, lam=None: v)
    assert bkt_model.meets_theta("u", "Loops", THETA_BASE) is False
    assert bkt_model.meets_theta("u", "Loops", THETA_OVERCONFIDENT) is False


def test_conjunctive_across_tiers(monkeypatch):
    """One weak tier fails the whole edge — mastery is conjunctive."""
    r = _row(0.95); r["p_mastery_code"] = 0.80
    monkeypatch.setattr(bkt_model, "_read_row", lambda u, c: r)
    monkeypatch.setattr(bkt_model, "_apply_decay", lambda v, k, t, lam=None: v)
    assert bkt_model.meets_theta("u", "Loops", THETA_BASE) is True
    assert bkt_model.meets_theta("u", "Loops", THETA_OVERCONFIDENT) is False


def test_missing_row_does_not_fail_closed(monkeypatch):
    """Absence of evidence is the certification gate's question, not this one.

    Failing closed here would make an UNKNOWN concept stricter than a
    known-weak one, which inverts the meaning of the bar.
    """
    monkeypatch.setattr(bkt_model, "_read_row", lambda u, c: None)
    assert bkt_model.meets_theta("u", "Nonexistent", THETA_OVERCONFIDENT) is True


def test_meets_theta_does_not_write(monkeypatch):
    """Read-only: a per-learner bar must not clear a shared certification flag.

    `is_mastered` self-heals is_certified as a side effect, which is right for
    the certification question and wrong here — it would turn one learner's
    overconfidence into a global de-certification.
    """
    import app.db.sqlite_db as sq
    monkeypatch.setattr(bkt_model, "_read_row", lambda u, c: _row(0.5))
    monkeypatch.setattr(bkt_model, "_apply_decay", lambda v, k, t, lam=None: v)
    monkeypatch.setattr(sq.db, "execute",
                        lambda *a, **k: pytest.fail("meets_theta wrote to the DB"))
    bkt_model.meets_theta("u", "Loops", THETA_OVERCONFIDENT)


# ── The gate consumer ───────────────────────────────────────────────────────

def test_has_mastered_theta_is_an_extra_conjunct(monkeypatch):
    from app.core.user_knowledge_manager import knowledge_manager as km
    import app.db.sqlite_db as sq

    monkeypatch.setattr(sq.db, "fetch_all", lambda *a, **k: [{"concept": "Loops"}])
    monkeypatch.setattr("app.core.bkt_model.is_current_certified", lambda u, c: True)
    monkeypatch.setattr("app.core.bkt_model.meets_theta",
                        lambda u, c, t: t <= P_TILDE)

    assert km.has_mastered("u", "Loops") is True                       # curriculum
    assert km.has_mastered("u", "Loops", theta=THETA_BASE) is True      # 0.75
    assert km.has_mastered("u", "Loops", theta=THETA_OVERCONFIDENT) is False


def test_default_call_is_unchanged(monkeypatch):
    """Every existing caller passes no theta and must behave exactly as before."""
    from app.core.user_knowledge_manager import knowledge_manager as km
    import app.db.sqlite_db as sq
    seen = []
    monkeypatch.setattr(sq.db, "fetch_all", lambda *a, **k: [{"concept": "Loops"}])
    monkeypatch.setattr("app.core.bkt_model.is_current_certified", lambda u, c: True)
    monkeypatch.setattr("app.core.bkt_model.meets_theta",
                        lambda u, c, t: seen.append(t) or True)
    assert km.has_mastered("u", "Loops") is True
    assert seen == [], "meets_theta must not run when no theta is supplied"


def test_gatekeeper_measures_before_it_enforces():
    """Shadow mode: the tightened gate is logged but does not refuse."""
    import inspect
    from app.agents import cot_rag_agent as cra
    src = inspect.getsource(cra.ChainOfThoughtRAGAgent._check_gatekeeping)
    assert "cac_edge_threshold" in src, "the shadow outcome must be logged"
    assert "if _enforced:" in src, "enforcement must be gated on the switch"
    i_log = src.index("cac_edge_threshold")
    i_act = src.index("unknown.extend(")
    assert i_log < i_act, "measurement must happen whether or not it enforces"


def test_run_entrypoint_does_not_reference_theta_edge():
    """`run()` has its own prerequisite loop and no theta_edge in scope.

    Regression: the edge-threshold block was first applied there by mistake,
    where `theta_edge` is undefined — a NameError on the first CONCEPT or
    PROBLEM turn through that entry point.
    """
    import inspect
    from app.agents import cot_rag_agent as cra
    src = inspect.getsource(cra.ChainOfThoughtRAGAgent.run)
    assert "theta_edge" not in src
