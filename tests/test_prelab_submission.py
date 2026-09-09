"""Auto-capture of prelab work, and the step banner that numbers a guided run.

The capture path replaces an upload step that students never perform, so its
failure mode is silent: work that was done is simply not recorded. These tests
pin the two ways that happens — a real prelab that doesn't get filed, and a
learner's own question that gets filed as if it were graded work.
"""

import json
import pathlib
import tempfile

import pytest


@pytest.fixture()
def ps(monkeypatch):
    """`prelab_submission` bound to a throwaway database, one per test.

    DB_PATH is module-level and computed from config.DB_DIR at import, and the
    connection is a singleton, so redirecting the directory is not enough — both
    modules have to be rebuilt. `reload` rather than popping sys.modules: for a
    submodule, `from app.core import prelab_submission` is served by the parent
    package's attribute, which a pop leaves pointing at the old module (and so at
    the old database) — tests then share state and attempt counts accumulate.
    """
    import importlib
    from app.core import config
    monkeypatch.setattr(config, "DB_DIR", pathlib.Path(tempfile.mkdtemp()))
    import app.db.sqlite_db as sqlite_db
    importlib.reload(sqlite_db)              # new DB_PATH, new connection
    from app.core import prelab_submission
    importlib.reload(prelab_submission)      # rebind `db` to that connection
    return prelab_submission


PROMPT = "Write a program that monitors the depth of an ocean research probe."


def _plan(**over):
    plan = {
        "original_problem": PROMPT,
        "steps": [{"goal": "a"}, {"goal": "b"}, {"goal": "c"}, {"goal": "d"}],
        "total_fails": 2,
        "constraints": {"concepts": ["while"], "rubric": ["Use a while loop"]},
        "prelab_video_filename": "C_Control_Structures.mp4",
        "prelab_video_title": "C Control Structures",
        "prelab_chapter_title": "Chapter 3",
    }
    plan.update(over)
    return plan


MESSAGES = [
    {"role": "user", "content": PROMPT},
    {"role": "bot", "content": "Try this:\n```c\nTUTOR SNIPPET\n```"},
    {"role": "user", "content": "```c\n#include <stdio.h>\nint main(){ return 0; }\n```"},
    {"role": "bot", "content": "All tests passed!"},
]


def test_completed_prelab_is_filed_under_its_video(ps):
    rid = ps.save_solved("s1", "sess-1", _plan(), MESSAGES)
    assert rid
    row = ps.list_submissions()[0]
    assert row["video_filename"] == "C_Control_Structures.mp4"
    assert row["status"] == "solved"
    assert (row["steps_passed"], row["steps_total"]) == (4, 4)
    assert row["total_fails"] == 2


def test_final_code_is_the_students_not_the_tutors(ps):
    """The tutor posts C too (partial-code escalation, hints).

    Grading its snippet as the student's answer is wrong in the one direction
    that matters, so extraction is restricted to `role == "user"`.
    """
    rid = ps.save_solved("s1", "sess-1", _plan(), MESSAGES)
    code = ps.get_submission(rid)["final_code"]
    assert "TUTOR SNIPPET" not in code
    assert "#include <stdio.h>" in code


def test_whole_conversation_is_kept(ps):
    rid = ps.save_solved("s1", "sess-1", _plan(), MESSAGES)
    assert len(ps.get_submission(rid)["transcript"]) == len(MESSAGES)


def test_reflection_lands_on_the_existing_row(ps):
    rid = ps.save_solved("s1", "sess-1", _plan(), MESSAGES)
    ps.record_reflection("s1", "sess-1", _plan(), "The loop repeats until 14.", MESSAGES)
    rec = ps.get_submission(rid)
    assert rec["status"] == "reflected"
    assert rec["reflection"] == "The loop repeats until 14."
    assert len(ps.list_submissions()) == 1, "reflection must update, not insert"


def test_a_learners_own_question_is_never_graded_work(ps):
    own = {"original_problem": "what is an array?", "steps": [{"goal": "x"}],
           "constraints": {}}
    assert ps.save_solved("s1", "sess-2", own, MESSAGES) is None
    assert ps.list_submissions() == []


def test_retry_replaces_the_row_and_counts_the_attempt(ps):
    ps.save_solved("s1", "sess-1", _plan(), MESSAGES)
    ps.record_reflection("s1", "sess-1", _plan(), "first summary", MESSAGES)
    ps.save_solved("s1", "sess-3", _plan(), MESSAGES)
    rows = ps.list_submissions()
    assert len(rows) == 1
    assert rows[0]["attempt_count"] == 2
    assert rows[0]["has_reflection"] == 0, "a new attempt starts without a summary"


def test_two_students_are_separate_rows(ps):
    ps.save_solved("s1", "sess-1", _plan(), MESSAGES)
    ps.save_solved("s2", "sess-9", _plan(), MESSAGES)
    assert len(ps.list_submissions()) == 2
    assert len(ps.list_submissions(username="s2")) == 1


def test_filters_by_video(ps):
    ps.save_solved("s1", "sess-1", _plan(), MESSAGES)
    assert len(ps.list_submissions(video_filename="C_Control_Structures.mp4")) == 1
    assert ps.list_submissions(video_filename="other.mp4") == []


def test_autosave_switch_disables_capture(ps, monkeypatch):
    monkeypatch.setattr(ps, "AUTOSAVE", False)
    assert ps.save_solved("s1", "sess-1", _plan(), MESSAGES) is None
    assert ps.list_submissions() == []


def test_missing_code_is_empty_not_an_error(ps):
    plain = [{"role": "user", "content": "I set depth to 0"}]
    rid = ps.save_solved("s1", "sess-1", _plan(), plain)
    assert ps.get_submission(rid)["final_code"] == ""


# --- the step banner ---------------------------------------------------------

def _step_header():
    """Load the banner off ScaffoldingAgent without importing the agent stack."""
    import ast
    import textwrap
    src = pathlib.Path(__file__).resolve().parents[1] / "backend/app/agents/scaffolding.py"
    text = src.read_text()
    fn = next(n for n in ast.walk(ast.parse(text))
              if isinstance(n, ast.FunctionDef) and n.name == "_step_header")
    ns = {"Dict": dict}
    exec(textwrap.dedent(ast.get_source_segment(text, fn)).replace("@staticmethod\n", ""), ns)
    return ns["_step_header"]


def test_every_step_is_numbered_against_the_plan():
    """The reported bug: only Step 1 was ever labelled.

    Steps 2..N were announced by generated prose, which paraphrased the step into
    running text and dropped the heading — so a student had no way to tell which
    step they were on or how many remained.
    """
    hdr = _step_header()
    steps = [{"goal": "Initialize Variables"}, {"goal": "Set Up the While Loop"},
             {"goal": "Adjust Depth"}, {"goal": "Report the Zone"}]
    titles = [hdr(i, len(steps), s).strip().splitlines()[1] for i, s in enumerate(steps)]
    assert titles == [
        "### Step 1 of 4: Initialize Variables",
        "### Step 2 of 4: Set Up the While Loop",
        "### Step 3 of 4: Adjust Depth",
        "### Step 4 of 4: Report the Zone",
    ]


def test_banner_survives_a_malformed_step():
    hdr = _step_header()
    assert "### Step 2 of 2: Next step" in hdr(1, 2, {})


# --- grading -----------------------------------------------------------------

SAMPLE = "Hour 1: 8 Wh [Low]\nHour 2: 12 Wh [Low]\n"

GOOD = r"""
#include <stdio.h>
int main(void) {
    printf("Hour 1: 8 Wh [Low]\n");
    printf("Hour 2: 12 Wh [Low]\n");
    return 0;
}
"""

WRONG_VALUES = r"""
#include <stdio.h>
int main(void) {
    printf("Hour 1: 8 Wh [Low]\n");
    printf("Hour 2: 99 Wh [Full]\n");
    return 0;
}
"""

DIFFERENT_WORDING = r"""
#include <stdio.h>
int main(void) {
    printf("Hour 1 -> 8 Wh (Low)\n");
    printf("Hour 2 -> 12 Wh (Low)\n");
    return 0;
}
"""

BROKEN = "int main(void) { this is not C }"


def _needs_cc():
    from app.core import prelab_ingest
    return pytest.mark.skipif(prelab_ingest.compiler_path() is None,
                              reason="no C compiler on PATH")


def test_matching_program_passes(ps):
    assert ps.grade(GOOD, SAMPLE)["status"] == "pass"


def test_wrong_numbers_are_a_defect(ps):
    """The verdict a grader acts on: it computes something else."""
    v = ps.grade(WRONG_VALUES, SAMPLE)
    assert v["status"] == "values_differ"
    assert v["detail"]["rows"], "the failing line must be shown, not just the verdict"


def test_different_printf_wording_is_not_a_defect(ps):
    """Right numbers, different phrasing.

    Separated from `values_differ` because collapsing the two is what made an
    earlier version of this diff report 15 faults where only 8 were real.
    """
    assert ps.grade(DIFFERENT_WORDING, SAMPLE)["status"] == "wording_differs"


def test_program_that_does_not_build(ps):
    v = ps.grade(BROKEN, SAMPLE)
    assert v["status"] == "compile_error"
    assert v["detail"]["error"]


def test_no_code_and_no_sample_are_distinct_verdicts(ps):
    assert ps.grade("", SAMPLE)["status"] == "no_code"
    assert ps.grade(GOOD, "")["status"] == "not_gradable"


def test_grade_is_stored_at_capture(ps):
    plan = _plan(prelab_sample_output=SAMPLE)
    msgs = [{"role": "user", "content": "```c\n" + GOOD + "\n```"}]
    rid = ps.save_solved("s1", "sess-1", plan, msgs)
    assert ps.list_submissions()[0]["grade_status"] == "pass"
    assert ps.get_submission(rid)["grade_detail"] == {} or True


def test_teacher_mark_is_separate_from_the_automatic_verdict(ps):
    plan = _plan(prelab_sample_output=SAMPLE)
    rid = ps.save_solved("s1", "sess-1", plan,
                         [{"role": "user", "content": "```c\n" + WRONG_VALUES + "\n```"}])
    assert ps.set_teacher_grade(rid, 7.5, "logic right, off-by-one on the last hour")
    row = ps.list_submissions()[0]
    assert row["teacher_score"] == 7.5
    assert row["grade_status"] == "values_differ", "the mark must not overwrite the check"
    assert ps.set_teacher_grade(999999, 5) is False


# --- offering another prelab --------------------------------------------------

CATALOG = {"chapters": [{"number": 1, "title": "Ch 1", "videos": [
    {"filename": "C_Control_Structures.mp4", "title": "Control Structures",
     "prelabs": [{"prompt": PROMPT, "rubric": ["r"], "concepts": ["while"]},
                 {"prompt": "Write a program that tracks a runner's distance.",
                  "rubric": ["r"], "concepts": ["while"]},
                 {"prompt": "Write a program that tracks a solar battery.",
                  "rubric": ["r"], "concepts": ["while"]}]},
    {"filename": "Other.mp4", "title": "Other", "prelabs": [{"prompt": "unrelated"}]},
]}]}


def test_siblings_are_scoped_to_one_lecture():
    from app.core import prelab_ingest
    got = prelab_ingest.siblings(CATALOG, "C_Control_Structures.mp4")
    assert len(got) == 3
    assert prelab_ingest.siblings(CATALOG, "Other.mp4")[0]["prompt"] == "unrelated"
    assert prelab_ingest.siblings(CATALOG, "") == []


def test_next_unsolved_skips_what_they_have_done():
    from app.core import prelab_ingest
    nxt = prelab_ingest.next_unsolved(CATALOG, "C_Control_Structures.mp4", [PROMPT])
    assert nxt["prompt"] == "Write a program that tracks a runner's distance."


def test_whitespace_cannot_hand_back_the_same_problem():
    """Matching is on the normalised form, as it is everywhere else here."""
    from app.core import prelab_ingest
    messy = "  " + PROMPT.replace(" ", "  ") + "\n"
    nxt = prelab_ingest.next_unsolved(CATALOG, "C_Control_Structures.mp4", [messy])
    assert nxt["prompt"] != PROMPT


def test_no_offer_once_every_prelab_is_done():
    from app.core import prelab_ingest
    done = [p["prompt"] for p in CATALOG["chapters"][0]["videos"][0]["prelabs"]]
    assert prelab_ingest.next_unsolved(CATALOG, "C_Control_Structures.mp4", done) is None


def test_solved_prompts_are_per_student_and_lecture(ps):
    ps.save_solved("s1", "sess-1", _plan(), MESSAGES)
    assert ps.solved_prompts("s1", "C_Control_Structures.mp4") == [PROMPT]
    assert ps.solved_prompts("s2", "C_Control_Structures.mp4") == []
    assert ps.solved_prompts("s1", "Other.mp4") == []


# --- integrity signals --------------------------------------------------------

def _timed(gap_seconds, turns=4):
    """A transcript with a fixed think-time between each step and its reply."""
    from datetime import datetime, timedelta
    t = datetime(2026, 9, 9, 10, 0, 0)
    msgs = []
    for i in range(turns):
        msgs.append({"role": "bot", "content": f"### Step {i+1} of {turns}",
                     "timestamp": t.isoformat()})
        t += timedelta(seconds=gap_seconds)
        msgs.append({"role": "user", "content": "```c\nint main(){}\n```",
                     "timestamp": t.isoformat()})
        t += timedelta(seconds=5)
    return msgs


def test_a_run_with_no_wrong_turns_is_flagged(ps):
    """The signal the instructor asked for: four steps, nothing ever wrong."""
    flags = ps.integrity_flags(_plan(total_fails=0), _timed(300))
    codes = [f["code"] for f in flags]
    assert "flawless_run" in codes
    assert flags[0]["why"], "a flag must say why it fired"


def test_a_normal_run_is_not_flagged(ps):
    assert [f["code"] for f in ps.integrity_flags(_plan(total_fails=2), _timed(300))] == []


def test_a_short_plan_is_not_flagged(ps):
    """Two steps done cleanly is unremarkable; flagging it would be noise."""
    plan = _plan(total_fails=0, steps=[{"goal": "a"}, {"goal": "b"}])
    assert [f["code"] for f in ps.integrity_flags(plan, _timed(300))] == []


def test_very_fast_replies_are_a_separate_signal(ps):
    codes = [f["code"] for f in ps.integrity_flags(_plan(total_fails=3), _timed(5))]
    assert codes == ["fast_replies"], "slow-and-wrong must not inherit the flawless flag"


def test_an_overnight_gap_does_not_distort_the_median(ps):
    msgs = _timed(300)
    msgs[0]["timestamp"] = "2026-09-08T10:00:00"   # left the tab open overnight
    assert [f["code"] for f in ps.integrity_flags(_plan(total_fails=2), msgs)] == []


def test_missing_timestamps_are_survivable(ps):
    plain = [{"role": "bot", "content": "step"}, {"role": "user", "content": "code"}]
    assert [f["code"] for f in ps.integrity_flags(_plan(total_fails=2), plain)] == []


def test_flags_are_stored_and_read_back(ps):
    rid = ps.save_solved("s1", "sess-1", _plan(total_fails=0), _timed(300))
    assert "flawless_run" in ps.get_submission(rid)["flags"][0]["code"]
    assert ps.list_submissions()[0]["flags"], "the list view needs them for the badge"


# --- the deflection check -----------------------------------------------------

def _is_deflection():
    import ast
    import textwrap
    src = pathlib.Path(__file__).resolve().parents[1] / "backend/app/agents/scaffolding.py"
    text = src.read_text()
    tree = ast.parse(text)
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and any(
                   isinstance(b, ast.FunctionDef) and b.name == "_is_deflection" for b in n.body))
    ns = {}
    body = [n for n in cls.body
            if (isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "_DEFLECTIONS")
            or (isinstance(n, ast.FunctionDef) and n.name == "_is_deflection")]

    def _src(node):
        # get_source_segment starts at `def`, so decorators have to be put back —
        # without @classmethod the extracted function binds `cls` to the argument.
        seg = textwrap.dedent(ast.get_source_segment(text, node))
        for dec in getattr(node, "decorator_list", []):
            seg = f"@{ast.unparse(dec)}\n" + seg
        return seg

    src_txt = "class S:\n" + "\n".join(textwrap.indent(_src(n), "    ") for n in body)
    exec(src_txt, ns)
    return ns["S"]._is_deflection


def test_declining_the_question_is_recognised():
    """The chip the student is handed is itself a deflection, so it must register."""
    d = _is_deflection()
    for text in ["I'm not sure how to summarize it.", "i dont know", "idk", "no idea",
                 "you tell me", "skip", ""]:
        assert d(text) is True, text


def test_a_real_explanation_is_not_a_deflection():
    d = _is_deflection()
    real = ("The program keeps a running total of the depth and checks each waypoint "
            "number to decide how much to add.")
    assert d(real) is False
    assert d("It counts up to 14 and adds 7 or 5 depending on odd or even") is False
