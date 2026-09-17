"""Resume position and already-answered checkpoints.

Both were being logged to the server all along; the player reset them to zero on
every load, so a rewatch restarted at 0:00 and re-asked every question. These tests
pin the decisions that are easy to get subtly wrong — which positions are worth
restoring, and which ones would actively annoy a student if restored.
"""

import asyncio
import pathlib
import tempfile

import pytest


@pytest.fixture()
def api(monkeypatch):
    """The endpoint bound to a throwaway database."""
    import importlib
    from app.core import config
    monkeypatch.setattr(config, "DB_DIR", pathlib.Path(tempfile.mkdtemp()))
    import app.db.sqlite_db as sqlite_db
    importlib.reload(sqlite_db)
    import app.main as main
    monkeypatch.setattr(main, "db", sqlite_db.db)
    monkeypatch.setattr(main.auth, "require_self_or_teacher", lambda *a, **k: None)
    return main, sqlite_db.db


CALLER = {"username": "s1", "role": "student"}
VID = "Chapter_1_Part_1.mp4"


def _get(main, username=CALLER["username"], video=VID):
    return asyncio.run(main.get_video_progress(video, username, caller=CALLER))


def _watch(db, pos, event="pause", user="s1", video=VID, ts="2026-09-17 10:00:00"):
    db.execute("INSERT INTO video_engagement (username, video_filename, event, "
               "position_sec, ts_utc) VALUES (?,?,?,?,?)", (user, video, event, pos, ts))


def _answer(db, cp_time, correct=1, user="s1", video=VID):
    db.execute("INSERT INTO video_mcq_response (username, video_filename, "
               "checkpoint_time, selected_index, is_correct) VALUES (?,?,?,?,?)",
               (user, video, cp_time, 0, correct))


def _coverage(db, duration, completed=0, user="s1", video=VID):
    db.execute("INSERT INTO video_coverage (username, video_filename, duration_sec, "
               "completed) VALUES (?,?,?,?)", (user, video, duration, completed))


def test_a_fresh_video_starts_at_the_beginning(api):
    main, db = api
    d = _get(main)
    assert d["position_sec"] == 0.0
    assert d["answered"] == []


def test_the_latest_position_is_restored(api):
    main, db = api
    _watch(db, 120.0, ts="2026-09-17 10:00:00")
    _watch(db, 754.2, ts="2026-09-17 10:05:00")
    assert _get(main)["position_sec"] == 754.2


def test_a_barely_started_video_is_not_resumed(api):
    """Restoring 8 seconds helps nobody and looks like a glitch."""
    main, db = api
    _watch(db, 8.0)
    assert _get(main)["position_sec"] == 0.0


def test_a_finished_video_restarts_from_zero(api):
    """Resuming at 99% would drop the student on the end card every time."""
    main, db = api
    _coverage(db, duration=1000.0)
    _watch(db, 985.0)
    assert _get(main)["position_sec"] == 0.0


def test_explicitly_completed_restarts_from_zero(api):
    main, db = api
    _coverage(db, duration=1000.0, completed=1)
    _watch(db, 400.0)
    assert _get(main)["position_sec"] == 0.0
    assert _get(main)["completed"] is True


def test_the_ended_event_is_never_used_as_a_position(api):
    """'ended' always reports the final second.

    Honouring it would resume every completed video at its end, which is the one
    position guaranteed to be useless.
    """
    main, db = api
    _watch(db, 400.0, event="pause", ts="2026-09-17 10:00:00")
    _watch(db, 1000.0, event="ended", ts="2026-09-17 10:09:00")
    assert _get(main)["position_sec"] == 400.0


def test_answered_checkpoints_come_back(api):
    main, db = api
    _answer(db, 120.0)
    _answer(db, 305.5)
    assert sorted(_get(main)["answered"]) == [120.0, 305.5]


def test_a_wrong_answer_still_counts_as_answered(api):
    """Default policy: one attempt is enough.

    The checkpoint has already done its job of interrupting passive watching, and
    a student who cannot get it should not be walled out of the lecture on every
    rewatch. Flip CHECKPOINT_ANY_ATTEMPT to require a correct answer.
    """
    main, db = api
    _answer(db, 120.0, correct=0)
    assert _get(main)["answered"] == [120.0]


def test_the_strict_policy_re_asks_until_correct(api, monkeypatch):
    main, db = api
    monkeypatch.setattr(main, "CHECKPOINT_ANY_ATTEMPT", False)
    _answer(db, 120.0, correct=0)
    _answer(db, 305.5, correct=1)
    assert _get(main)["answered"] == [305.5]


def test_repeat_attempts_are_reported_once(api):
    main, db = api
    _answer(db, 120.0, correct=0)
    _answer(db, 120.0, correct=1)
    assert _get(main)["answered"] == [120.0]


def test_another_students_progress_is_not_returned(api):
    """The position and answer history are per learner, not per video."""
    main, db = api
    _watch(db, 754.2, user="s2")
    _answer(db, 120.0, user="s2")
    d = _get(main)
    assert d["position_sec"] == 0.0 and d["answered"] == []


def test_progress_is_per_video(api):
    main, db = api
    _watch(db, 754.2, video="Other.mp4")
    _answer(db, 120.0, video="Other.mp4")
    d = _get(main)
    assert d["position_sec"] == 0.0 and d["answered"] == []


def test_ownership_is_enforced(api, monkeypatch):
    """The guard is the whole reason this endpoint takes a username.

    Without it, any classmate's watch position and answer history is a URL away.
    """
    main, db = api
    called = {}
    monkeypatch.setattr(main.auth, "require_self_or_teacher",
                        lambda target, caller: called.setdefault("target", target))
    _get(main, username="s2")
    assert called["target"] == "s2"
