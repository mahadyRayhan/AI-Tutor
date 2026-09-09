# backend/app/core/prelab_submission.py
"""Auto-captured prelab work.

A guided-practice run that reaches the last step IS the submission. The student
uploads nothing; the grader reads the conversation that produced the code, which
is strictly more than a file would have shown — it carries the wrong turns, the
hints spent, and the student's own explanation at the end.

Two write points, deliberately separate:

  * `save_solved` fires the moment the last step passes. A student who closes the
    tab before writing their reflection has still done the work, and must not be
    recorded as having done nothing.
  * `record_reflection` fills in the summary afterwards, if it comes.

Capture is behind `PRELAB_AUTOSAVE` so it can be switched off without touching
the tutor, matching how the other data-collection paths in this codebase are
gated.
"""

import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.db.sqlite_db import db

# Off switch. Set PRELAB_AUTOSAVE=0 to run the tutor with no capture at all.
AUTOSAVE = os.getenv("PRELAB_AUTOSAVE", "1") not in ("0", "false", "False", "")

# A fenced block, language tag optional. The student's C is what a grader opens
# first, so it is lifted out of the transcript rather than left to be hunted for.
_FENCE = re.compile(r"```(?:c|cpp|C|C\+\+)?\s*\n(.*?)```", re.DOTALL)


def extract_last_code(messages: List[Dict[str, Any]]) -> str:
    """The last fenced block the STUDENT sent, or "".

    Student messages only: the tutor also posts code (partial-code escalation,
    pseudocode hints), and grading the tutor's own snippet as the student's
    answer would be wrong in the one direction that matters.
    """
    for m in reversed(messages or []):
        if (m.get("role") or "").lower() != "user":
            continue
        blocks = _FENCE.findall(m.get("content") or "")
        if blocks:
            return blocks[-1].strip()
    return ""


def _plan_stats(plan: Dict[str, Any]) -> Dict[str, int]:
    steps = plan.get("steps") or []
    return {
        "steps_total": len(steps),
        "steps_passed": len(steps),          # this path is only reached on completion
        "total_fails": int(plan.get("total_fails") or 0),
    }


def save_solved(username: str, session_id: str, plan: Dict[str, Any],
                messages: List[Dict[str, Any]]) -> Optional[int]:
    """Record a completed prelab. Returns the row id, or None if not captured.

    Returns None rather than raising when the plan is not a prelab: most guided
    runs are a learner's own question and have nothing to file under a video.
    """
    if not AUTOSAVE:
        return None
    prompt = (plan.get("original_problem") or "").strip()
    if not prompt:
        return None
    # Only work that matched a stored handout is graded work.
    if not plan.get("prelab_video_filename") and not plan.get("constraints"):
        return None

    stats = _plan_stats(plan)
    now = datetime.now()
    video = plan.get("prelab_video_filename") or ""
    code = extract_last_code(messages)
    # Graded here, once, rather than when the dashboard opens: compiling on page
    # load would make a class list slow and would re-run every refresh.
    verdict = grade(code, plan.get("prelab_sample_output") or "")
    flags = integrity_flags(plan, messages)

    prior = db.fetch_one(
        "SELECT id, attempt_count FROM prelab_submission "
        "WHERE username = ? AND video_filename = ? AND prelab_prompt = ?",
        (username, video, prompt))

    payload = (
        plan.get("prelab_video_title") or "",
        plan.get("prelab_chapter_title") or "",
        session_id,
        "solved",
        stats["steps_total"], stats["steps_passed"], stats["total_fails"],
        code,
        json.dumps(messages or []),
        verdict["status"], json.dumps(verdict["detail"]), json.dumps(flags),
        now, now,
    )

    if prior:
        # A retry replaces the record but keeps the count of how many it took.
        db.execute(
            "UPDATE prelab_submission SET video_title = ?, chapter_title = ?, "
            "session_id = ?, status = ?, steps_total = ?, steps_passed = ?, "
            "total_fails = ?, final_code = ?, transcript = ?, grade_status = ?, "
            "grade_detail = ?, flags = ?, solved_at = ?, updated_at = ?, "
            "attempt_count = ?, reflection = NULL WHERE id = ?",
            payload + (int(prior["attempt_count"] or 1) + 1, prior["id"]))
        return int(prior["id"])

    cur = db.execute(
        "INSERT INTO prelab_submission (username, video_filename, prelab_prompt, "
        "video_title, chapter_title, session_id, status, steps_total, steps_passed, "
        "total_fails, final_code, transcript, grade_status, grade_detail, flags, "
        "solved_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (username, video, prompt) + payload)
    return int(cur.lastrowid)


def record_reflection(username: str, session_id: str, plan: Dict[str, Any],
                      reflection: str, messages: List[Dict[str, Any]]) -> None:
    """Attach the student's own summary, and refresh the transcript to include it."""
    if not AUTOSAVE:
        return
    prompt = (plan.get("original_problem") or "").strip()
    if not prompt:
        return
    video = plan.get("prelab_video_filename") or ""
    db.execute(
        "UPDATE prelab_submission SET reflection = ?, status = 'reflected', "
        "transcript = ?, updated_at = ? "
        "WHERE username = ? AND video_filename = ? AND prelab_prompt = ?",
        (reflection or "", json.dumps(messages or []), datetime.now(),
         username, video, prompt))




# --- integrity signals --------------------------------------------------------

# A guided prelab is meant to be got wrong on the way through. The scaffolding
# exists because a first-semester student does not write four correct steps in a
# row from a standing start; when one does, that is worth a second look. These
# are SIGNALS, not verdicts — they say "read this transcript", never "this student
# cheated", and the dashboard is worded to match.
FLAWLESS_MIN_STEPS = 3      # below this, a clean run is unremarkable
FAST_REPLY_SECONDS = 40     # median think-time under this is quick for writing C


def _median(xs):
    xs = sorted(xs)
    if not xs:
        return None
    mid = len(xs) // 2
    return xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2


def _reply_gaps(messages: List[Dict[str, Any]]) -> List[float]:
    """Seconds between each tutor message and the student's reply to it."""
    gaps, last_bot = [], None
    for m in messages or []:
        ts = m.get("timestamp")
        if not ts:
            continue
        try:
            when = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except Exception:
            continue
        if (m.get("role") or "").lower() == "user":
            if last_bot is not None:
                delta = (when - last_bot).total_seconds()
                if 0 <= delta < 3600:      # ignore a session left open overnight
                    gaps.append(delta)
            last_bot = None
        else:
            last_bot = when
    return gaps


def integrity_flags(plan: Dict[str, Any], messages: List[Dict[str, Any]]) -> List[Dict]:
    """What about this run is worth an instructor's attention, and why."""
    out = []
    steps = len(plan.get("steps") or [])
    fails = int(plan.get("total_fails") or 0)

    if fails == 0 and steps >= FLAWLESS_MIN_STEPS:
        out.append({
            "code": "flawless_run",
            "label": f"No wrong turns across {steps} steps",
            "why": ("Every step was accepted on the first try. That is uncommon for a "
                    "first-semester student working without help — worth reading the "
                    "transcript before grading."),
        })

    gaps = _reply_gaps(messages)
    med = _median(gaps)
    if med is not None and len(gaps) >= FLAWLESS_MIN_STEPS and med < FAST_REPLY_SECONDS:
        out.append({
            "code": "fast_replies",
            "label": f"Median reply {int(med)}s",
            "why": ("Little time between being shown a step and answering it, which is "
                    "quick for reading a step and writing C."),
        })
    return out


# --- grading -----------------------------------------------------------------

# What each verdict means to a grader:
#   pass            the program compiles and prints exactly the handout's output
#   wording_differs the numbers are all right; only printf phrasing differs
#   values_differ   it computes something else — a real defect
#   compile_error   it does not build
#   no_code         they finished the steps without ever pasting a full program
#   not_gradable    the handout stores no sample output to compare against
def grade(code: str, sample_output: str) -> dict:
    """Run the student's program against the handout's own sample output.

    Deterministic on purpose. The rubric could be checked by asking a model
    whether the code "uses a while loop", but a grade a student can contest is
    worth less than one that can be re-derived by running the program. Wording
    is separated from values because a different printf phrasing is a style note
    and a different number is a defect — collapsing them into one verdict is the
    mistake this diff was built to avoid.
    """
    from app.core import prelab_ingest

    if not (code or "").strip():
        return {"status": "no_code", "detail": {}}
    if not (sample_output or "").strip():
        return {"status": "not_gradable",
                "detail": {"reason": "The handout stores no sample output."}}

    run = prelab_ingest.compile_and_run_c(code)
    if not run.get("ok"):
        return {"status": "compile_error",
                "detail": {"stage": run.get("stage", ""),
                           "error": (run.get("error") or "")[:4000]}}

    d = prelab_ingest.diff_output(sample_output, run.get("stdout", ""))
    if d.get("match"):
        status = "pass"
    elif d.get("values_match"):
        status = "wording_differs"
    else:
        status = "values_differ"

    # When the values are wrong, show the lines whose VALUES disagree — the wording
    # differences alongside them are noise a grader does not need to read through.
    rows = d.get("value_mismatches") if status == "values_differ" else d.get("mismatches")
    return {"status": status,
            "detail": {"expected_lines": d.get("expected_lines"),
                       "actual_lines": d.get("actual_lines"),
                       "mismatch_count": d.get("mismatch_count", 0),
                       "value_mismatch_count": d.get("value_mismatch_count", 0),
                       "rows": (rows or [])[:40]}}


def set_teacher_grade(sub_id: int, score: float = None, note: str = None) -> bool:
    """The instructor's mark. The automatic verdict is evidence, not the grade."""
    row = db.fetch_one("SELECT id FROM prelab_submission WHERE id = ?", (sub_id,))
    if not row:
        return False
    db.execute("UPDATE prelab_submission SET teacher_score = ?, teacher_note = ?, "
               "updated_at = ? WHERE id = ?",
               (score, note or "", datetime.now(), sub_id))
    return True


# --- read side, for the teacher dashboard -------------------------------------

def list_submissions(video_filename: str = None, username: str = None) -> List[Dict]:
    """Submission rows without their transcripts — the list view loads many."""
    where, params = [], []
    if video_filename:
        where.append("s.video_filename = ?")
        params.append(video_filename)
    if username:
        where.append("s.username = ?")
        params.append(username)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    rows = db.fetch_all(
        "SELECT s.id, s.username, u.email, u.name, s.video_filename, s.video_title, "
        "s.chapter_title, s.prelab_prompt, s.status, s.steps_total, s.steps_passed, "
        "s.total_fails, s.attempt_count, s.grade_status, s.flags, s.teacher_score, "
        "s.teacher_note, s.solved_at, s.updated_at, "
        "length(s.final_code) AS code_len, "
        "CASE WHEN s.reflection IS NULL OR s.reflection = '' THEN 0 ELSE 1 END AS has_reflection "
        f"FROM prelab_submission s LEFT JOIN users u ON u.username = s.username {clause} "
        "ORDER BY s.updated_at DESC", tuple(params))
    return [dict(r) for r in rows]


def solved_prompts(username: str, video_filename: str = None) -> List[str]:
    """Prelab prompts this student has already finished."""
    if video_filename:
        rows = db.fetch_all(
            "SELECT prelab_prompt FROM prelab_submission WHERE username = ? "
            "AND video_filename = ?", (username, video_filename))
    else:
        rows = db.fetch_all(
            "SELECT prelab_prompt FROM prelab_submission WHERE username = ?", (username,))
    return [r["prelab_prompt"] for r in rows]


def get_submission(sub_id: int) -> Optional[Dict]:
    """One submission with its full transcript, for the grading view."""
    row = db.fetch_one(
        "SELECT s.*, u.email, u.name FROM prelab_submission s "
        "LEFT JOIN users u ON u.username = s.username WHERE s.id = ?", (sub_id,))
    if not row:
        return None
    rec = dict(row)
    try:
        rec["transcript"] = json.loads(rec.get("transcript") or "[]")
    except Exception:
        rec["transcript"] = []
    try:
        rec["grade_detail"] = json.loads(rec.get("grade_detail") or "{}")
    except Exception:
        rec["grade_detail"] = {}
    try:
        rec["flags"] = json.loads(rec.get("flags") or "[]")
    except Exception:
        rec["flags"] = []
    return rec
