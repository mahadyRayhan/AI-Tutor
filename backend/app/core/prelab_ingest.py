"""Turn an instructor's prelab handout into verified practice problems.

The instructor uploads the plain-text export of a prelab (`.txt`). One handout
carries three different things, and they are NOT interchangeable:

    prompt         the task statement, shown to the student after the lecture
    rubric         the requirements, used to judge and guide — never shown up front
    sample_output  the expected run, which is the answer key

Splitting them is deterministic — ALL-CAPS lines delimit sections, blank lines
delimit paragraphs, and the first paragraph of the description is the prompt —
so no model is involved in reading the handout. A model is used only to invent
*variants* of it.

WHY THIS VERIFIES BY EXECUTION
------------------------------
The handout that motivated this module (CS1050 Prelab 3) states "if the marker
number is odd, increase the altitude by 5" while its own sample output increases
odd markers by 3. Eight of fifteen output lines disagree with the text. Since the
rubric is what grades the student, a learner who follows it exactly would have
been marked wrong on more than half the output.

No amount of prompt engineering reliably catches that: asking a model whether a
spec matches a 15-step trace is asking it to be an interpreter. So we use a real
one. `verify_spec` has a model write a C solution from the prompt and rubric ALONE
— it never sees the sample output — then compiles and runs it and diffs the real
stdout against the handout. Agreement is evidence; disagreement is a defect the
teacher is shown before anything is saved.

Generated variants get a stronger guarantee. Their sample output is not written
by the model at all: it is the captured stdout of the variant's own verified
reference solution. A variant therefore cannot contradict itself by construction.

RUNNING MODEL-WRITTEN C
-----------------------
This compiles and executes code a language model wrote, which is worth being
explicit about. It is reachable only by an authenticated teacher, each program is
built and run in a throwaway directory, and the child gets a wall-clock timeout, a
CPU limit, an address-space limit, and a capped stdout with no stdin. These are
prelab solutions for a first-semester C course — loops and printf — so the limits
are generous relative to the work and still bound a runaway.

Where no compiler exists, verification reports `status="unavailable"` rather than
passing silently: an unchecked spec is never reported as a checked one.
"""
from __future__ import annotations

import json
import logging
import os
import re
import resource
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass, field, asdict
from typing import Any, Callable

logger = logging.getLogger(__name__)

# --- limits for the compile-and-run sandbox -------------------------------
COMPILE_TIMEOUT_S = 20
RUN_TIMEOUT_S = 5
RUN_CPU_SECONDS = 5           # SIGXCPU if the program spins
RUN_ADDRESS_SPACE = 512 << 20  # 512 MB
MAX_OUTPUT_BYTES = 64 << 10    # 64 KB of stdout is far past any prelab
MAX_UPLOAD_BYTES = 256 << 10   # a handout is ~2 KB; this is pure sanity

# A section heading in these handouts is a short ALL-CAPS line of its own.
# Digits and colons are excluded so a sample-output line ("TOTAL: 59") cannot be
# mistaken for one.
_SECTION_RE = re.compile(r"^[A-Z][A-Z /&'’-]{1,60}$")

# Heading synonyms, matched as substrings of the uppercase heading.
_CONCEPT_KEYS = ("CONCEPT", "OBJECTIVE", "TOPIC", "SKILL")
_DESC_KEYS = ("DESCRIPTION", "PROBLEM", "TASK", "ASSIGNMENT", "INSTRUCTION")
_SAMPLE_KEYS = ("SAMPLE", "EXPECTED", "OUTPUT")


class PrelabParseError(ValueError):
    """The handout is missing something a prelab cannot do without."""


@dataclass
class PrelabSpec:
    """One practice problem, split by who is allowed to see each part."""
    prompt: str                                   # shown to the student
    rubric: list[str] = field(default_factory=list)   # tutor-only
    sample_output: str = ""                       # tutor-only (answer key)
    concepts: list[str] = field(default_factory=list)
    title: str = ""
    reference_solution: str = ""                  # tutor-only, filled by verify
    origin: str = "upload"                        # "upload" | "generated"
    verification: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ══════════════════════════════════════════════════════════════════════════
# 1. Parsing — deterministic, no model
# ══════════════════════════════════════════════════════════════════════════

def _split_sections(raw: str) -> tuple[list[str], dict[str, list[str]]]:
    """Split the handout on ALL-CAPS heading lines.

    Returns the preamble lines (title block) and heading -> body lines. Once the
    sample-output section starts, heading detection stops: an answer key is
    verbatim text and must not be re-segmented by something that happens to be
    capitalised inside it.
    """
    preamble: list[str] = []
    sections: dict[str, list[str]] = {}
    current: str | None = None
    frozen = False

    for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = line.strip()
        if not frozen and stripped and _SECTION_RE.fullmatch(stripped):
            current = stripped
            sections.setdefault(current, [])
            if any(k in current for k in _SAMPLE_KEYS):
                frozen = True
            continue
        (sections[current] if current is not None else preamble).append(line)

    return preamble, sections


def _find_section(sections: dict[str, list[str]], keys: tuple[str, ...]) -> list[str]:
    for heading, body in sections.items():
        if any(k in heading for k in keys):
            return body
    return []


def _paragraphs(lines: list[str]) -> list[str]:
    """Blank-line-separated paragraphs, each collapsed to a single line."""
    text = "\n".join(lines)
    out = []
    for block in re.split(r"\n\s*\n", text):
        block = " ".join(w for w in block.split())
        if block:
            out.append(block)
    return out


def parse_prelab_txt(raw: str) -> PrelabSpec:
    """Parse the plain-text export of a prelab handout.

    The prompt is the FIRST paragraph of the description; every later paragraph
    is rubric. That is a convention, not a law — an instructor who writes a
    two-paragraph setup would have their second paragraph filed as a
    requirement. It is deterministic and it is shown to the teacher for approval
    before anything is written, which is a better guarantee than a model's guess
    at where the setup ends, because the teacher can see and correct it.
    """
    if not raw or not raw.strip():
        raise PrelabParseError("The file is empty.")

    preamble, sections = _split_sections(raw)

    desc_lines = _find_section(sections, _DESC_KEYS)
    if not desc_lines:
        raise PrelabParseError(
            "No DESCRIPTION section found. The handout needs an all-caps heading "
            "such as DESCRIPTION, PROBLEM, or TASK on a line of its own."
        )

    paras = _paragraphs(desc_lines)
    if not paras:
        raise PrelabParseError("The DESCRIPTION section is empty.")

    sample_lines = _find_section(sections, _SAMPLE_KEYS)
    sample_output = "\n".join(sample_lines).strip("\n")

    concepts = [ln.strip() for ln in _find_section(sections, _CONCEPT_KEYS) if ln.strip()]
    title = next((ln.strip() for ln in preamble if ln.strip()), "")

    return PrelabSpec(
        prompt=paras[0],
        rubric=paras[1:],
        sample_output=sample_output,
        concepts=concepts,
        title=title,
        origin="upload",
    )


# ══════════════════════════════════════════════════════════════════════════
# 2. The compile-and-run sandbox
# ══════════════════════════════════════════════════════════════════════════

def compiler_path() -> str | None:
    for name in ("cc", "gcc", "clang"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _limit_child() -> None:  # pragma: no cover - runs in the forked child
    """Apply what this kernel supports, best effort.

    Not every limit exists everywhere: macOS rejects RLIMIT_AS outright, and a
    raise there would kill the child before exec with a message that says nothing
    about which limit failed. Each is therefore applied on its own and a refusal
    is skipped, so the limits that DO work still apply. RLIMIT_FSIZE is the one
    that matters most and is honoured on both platforms — stdout is redirected to
    a file, so it is what bounds a runaway printf.
    """
    for res_name, limit in (
        ("RLIMIT_FSIZE", MAX_OUTPUT_BYTES),
        ("RLIMIT_CPU", RUN_CPU_SECONDS),
        ("RLIMIT_AS", RUN_ADDRESS_SPACE),
        ("RLIMIT_NOFILE", 64),
        ("RLIMIT_CORE", 0),
    ):
        which = getattr(resource, res_name, None)
        if which is None:
            continue
        try:
            resource.setrlimit(which, (limit, limit))
        except (ValueError, OSError):
            continue


def compile_and_run_c(source: str) -> dict:
    """Build and execute one C program. Never raises; reports what happened.

    -> {ok, stdout, stage, error} where stage is "compiler"|"compile"|"run"|"" .

    stdout goes to a file rather than a pipe. A pipe would be read into the
    parent's memory in full and only trimmed afterwards, so a program printing a
    million lines would cost real memory in the web process before anything
    noticed. Writing to a file lets RLIMIT_FSIZE stop the child at the cap, and
    the parent then reads only as much as it is willing to keep.
    """
    cc = compiler_path()
    if not cc:
        return {"ok": False, "stdout": "", "stage": "compiler",
                "error": "No C compiler on PATH (looked for cc, gcc, clang)."}

    with tempfile.TemporaryDirectory(prefix="prelab_verify_") as tmp:
        src = os.path.join(tmp, "main.c")
        exe = os.path.join(tmp, "main")
        out_path = os.path.join(tmp, "stdout.txt")
        with open(src, "w", encoding="utf-8") as fh:
            fh.write(source)

        try:
            build = subprocess.run(
                [cc, "-O0", "-std=c11", "-o", exe, src, "-lm"],
                capture_output=True, text=True, timeout=COMPILE_TIMEOUT_S, cwd=tmp,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "stdout": "", "stage": "compile",
                    "error": f"Compilation timed out after {COMPILE_TIMEOUT_S}s."}
        if build.returncode != 0:
            return {"ok": False, "stdout": "", "stage": "compile",
                    "error": (build.stderr or "compilation failed")[:2000]}

        truncated = False
        try:
            with open(out_path, "wb") as out_fh:
                run = subprocess.run(
                    [exe], stdout=out_fh, stderr=subprocess.PIPE,
                    timeout=RUN_TIMEOUT_S, cwd=tmp, stdin=subprocess.DEVNULL,
                    preexec_fn=_limit_child, env={"PATH": "/usr/bin:/bin"},
                )
        except subprocess.TimeoutExpired:
            return {"ok": False, "stdout": "", "stage": "run",
                    "error": f"Program did not finish within {RUN_TIMEOUT_S}s "
                             "(likely an unterminated loop)."}
        except Exception as exc:  # OSError from spawning the child
            return {"ok": False, "stdout": "", "stage": "run", "error": str(exc)[:500]}

        try:
            with open(out_path, "rb") as fh:
                data = fh.read(MAX_OUTPUT_BYTES + 1)
            truncated = len(data) > MAX_OUTPUT_BYTES
            out = data[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        except OSError:
            out = ""

        if truncated or os.path.getsize(out_path) > MAX_OUTPUT_BYTES:
            return {"ok": False, "stdout": out, "stage": "run",
                    "error": f"Program printed more than {MAX_OUTPUT_BYTES // 1024} KB, "
                             "which no prelab should."}
        if run.returncode != 0:
            # A child stopped by RLIMIT_FSIZE dies on SIGXFSZ, reported as -25.
            signal_note = (" It was stopped for exceeding the output limit."
                           if run.returncode == -getattr(signal, "SIGXFSZ", 25) else "")
            return {"ok": False, "stdout": out, "stage": "run",
                    "error": (f"Program exited with status {run.returncode}."
                              f"{signal_note} "
                              f"{(run.stderr or b'').decode('utf-8', 'replace')[:500]}").strip()}
        return {"ok": True, "stdout": out, "stage": "", "error": ""}


# ══════════════════════════════════════════════════════════════════════════
# 3. Verification
# ══════════════════════════════════════════════════════════════════════════

def _normalise(text: str) -> list[str]:
    """Trailing whitespace and blank tail lines are not real differences."""
    lines = [ln.rstrip() for ln in (text or "").replace("\r\n", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return lines


def _line_signature(line):
    """What a line MEANS, stripped of how it was printed.

    "Marker 2: altitude = 8 meters, Base zone" and
    "Marker 2: Altitude 8 meters, Zone: Base" carry identical information: the
    same numbers in the same order and the same words. Only the punctuation and
    word order differ, and neither is something the requirements specify.

    This matters because the solver is asked for a program, not for a string. If
    a wording difference counted as a contradiction, every correct handout whose
    author phrased printf differently from the model would be blocked and the
    check would be worthless. Numbers stay ORDERED because their order is the
    computation; words are sorted because their arrangement is prose.
    """
    if line is None:
        return ()
    nums = tuple(re.findall(r"-?\d+(?:\.\d+)?", line))
    words = tuple(sorted(w.lower() for w in re.findall(r"[A-Za-z]+", line)))
    return (nums, words)


def format_template(sample_output: str, lines: int = 2) -> str:
    """The SHAPE of the expected output, with every number blanked out.

    Handed to the solver so it prints in the handout's format. Digits become '#',
    so this leaks no computed value — and the values are the only thing under
    test. Without it the model invents its own wording and the comparison drowns
    in differences that say nothing about whether the requirements are right.
    """
    return "\n".join(re.sub(r"\d+", "#", ln)
                     for ln in _normalise(sample_output)[:lines])


def diff_output(expected: str, actual: str) -> dict:
    """Compare a run against the handout, separating meaning from wording.

    Two verdicts, because they call for different actions:
      values_match False -> the requirements compute something else. A defect.
      values_match True but text differs -> only the printf wording differs.
                                            Worth showing, not worth blocking.
    """
    exp, act = _normalise(expected), _normalise(actual)

    rows, value_rows = [], []
    for i in range(max(len(exp), len(act))):
        e = exp[i] if i < len(exp) else None
        a = act[i] if i < len(act) else None
        if e == a:
            continue
        row = {"line": i + 1, "expected": e, "actual": a}
        rows.append(row)
        if _line_signature(e) != _line_signature(a):
            value_rows.append(row)

    # A different number of lines is always a real disagreement: the requirements
    # ran a different number of times.
    length_differs = len(exp) != len(act)

    return {
        "match": not rows,
        "values_match": not value_rows and not length_differs,
        "expected_lines": len(exp),
        "actual_lines": len(act),
        "mismatches": rows[:40],
        "mismatch_count": len(rows),
        "value_mismatches": value_rows[:40],
        "value_mismatch_count": len(value_rows),
    }


_SOLVE_PROMPT = """You are writing a reference solution for a first-semester C course.

Write a complete C program that satisfies the task and every requirement below.

TASK:
{prompt}

REQUIREMENTS:
{rubric}
{format_block}
Rules:
- Standard C11. The program takes NO input; it only prints.
- Follow the requirements exactly and literally. If a requirement seems odd,
  implement what it SAYS, not what you think was intended.
- Print exactly what is asked, nothing else: no banner, no prompts, no trailing
  commentary.
- Output ONLY the C source code. No markdown fences, no explanation.
"""

# Numbers are replaced with '#' so the solver learns the print format without
# being told a single computed value — the values are what is under test.
_FORMAT_BLOCK = """
OUTPUT FORMAT — match this wording, spacing and punctuation exactly. Every '#'
stands for a number you must compute yourself; the '#' marks are NOT values and
must not appear in your output:
{template}
"""


def _strip_code_fence(text: str) -> str:
    text = (text or "").strip()
    fence = re.match(r"^```[a-zA-Z]*\n(.*?)\n?```$", text, re.S)
    return fence.group(1).strip() if fence else text


def verify_spec(spec: PrelabSpec, generate: Callable[[str], str]) -> PrelabSpec:
    """Check a spec against its own sample output by executing it.

    The solver is shown the prompt and rubric ONLY. Withholding the sample output
    is the entire point: a model that can see the expected text will reproduce it
    and the check proves nothing. What we want to know is what the requirements
    actually compute.
    """
    if not spec.sample_output.strip():
        spec.verification = {
            "status": "no_sample",
            "message": "The handout has no SAMPLE OUTPUT section, so the "
                       "requirements could not be checked against an expected run.",
        }
        return spec

    if not compiler_path():
        spec.verification = {
            "status": "unavailable",
            "message": "No C compiler is available on the server, so this prelab "
                       "was NOT verified.",
        }
        return spec

    rubric_text = "\n".join(f"- {r}" for r in spec.rubric) or "- (none stated)"
    template = format_template(spec.sample_output)
    try:
        source = _strip_code_fence(generate(_SOLVE_PROMPT.format(
            prompt=spec.prompt, rubric=rubric_text,
            format_block=_FORMAT_BLOCK.format(template=template) if template else "")))
    except Exception as exc:
        logger.warning(f"[prelab] solver call failed: {exc}")
        spec.verification = {"status": "error",
                             "message": f"Could not generate a reference solution: {exc}"}
        return spec

    result = compile_and_run_c(source)
    if not result["ok"]:
        spec.verification = {
            "status": "error",
            "message": f"The reference solution failed to {result['stage'] or 'run'}: "
                       f"{result['error']}",
        }
        return spec

    spec.reference_solution = source
    comparison = diff_output(spec.sample_output, result["stdout"])
    if comparison["match"]:
        spec.verification = {
            "status": "verified",
            "message": "A program written from the requirements alone reproduces "
                       "the sample output exactly.",
            "diff": comparison,
        }
    elif comparison["values_match"]:
        # Every number and word agrees; only the printf wording differs. The
        # requirements are sound, so this is reported and NOT blocked — treating
        # a phrasing difference as a contradiction would reject correct handouts.
        spec.verification = {
            "status": "format_differs",
            "message": (
                f"The requirements produce the right values on all "
                f"{comparison['expected_lines']} lines, but the reference program "
                f"worded its output differently from the handout. The problem is "
                f"sound; only the print format is unstated."
            ),
            "diff": comparison,
            "actual_output": result["stdout"],
        }
    else:
        spec.verification = {
            "status": "mismatch",
            "message": (
                f"The requirements and the sample output disagree on "
                f"{comparison['value_mismatch_count']} line(s). A student who "
                f"follows the requirements exactly would be marked wrong. Fix the "
                f"handout before saving."
            ),
            "diff": comparison,
            "actual_output": result["stdout"],
        }
    return spec


# ══════════════════════════════════════════════════════════════════════════
# 4. Variant generation
# ══════════════════════════════════════════════════════════════════════════

_VARIANT_PROMPT = """You write practice problems for a first-semester C course.

Here is an existing prelab problem.

TASK:
{prompt}

REQUIREMENTS:
{rubric}

CONCEPTS BEING PRACTISED:
{concepts}

Write {n} NEW problems that practise exactly the same concepts and have the same
structure and difficulty, but a different scenario and different numbers. Do not
reuse the original's setting.

For each new problem give:
  "prompt"   - one paragraph: the task statement a student sees. It must be
               self-contained and must state the starting conditions.
  "rubric"   - a list of short requirement sentences: the loop to use, the
               branching rule, what to print, any category thresholds, and any
               restriction (e.g. "do not hard-code the output").
               These must be complete enough that a programmer who reads ONLY
               the prompt and rubric can write the program with no ambiguity.
  "concepts" - a list of the C concepts practised.

Do NOT write the expected output; it will be computed by running a solution.

Return ONLY a JSON array of {n} objects with those three keys. No markdown fences.
"""


def _parse_json_array(text: str) -> list[dict]:
    cleaned = _strip_code_fence(text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", cleaned, re.S)
        if not match:
            raise
        data = json.loads(match.group(0))
    if isinstance(data, dict):
        data = [data]
    return [d for d in data if isinstance(d, dict)]


def generate_variants(spec: PrelabSpec, generate: Callable[[str], str],
                      n: int = 3) -> list[PrelabSpec]:
    """Invent `n` similar problems, each with a sample output it cannot contradict.

    The model is asked for a prompt and rubric but NOT for an expected output.
    Each variant's reference solution is written from its own requirements, then
    compiled and run, and its real stdout BECOMES the sample output. So a variant
    is self-consistent by construction — the failure that made this module
    necessary cannot occur in one.

    A variant whose solution will not build or run is dropped rather than saved
    unverified.
    """
    concepts = "\n".join(f"- {c}" for c in spec.concepts) or "- (not stated)"
    rubric_text = "\n".join(f"- {r}" for r in spec.rubric) or "- (none stated)"

    raw = generate(_VARIANT_PROMPT.format(
        prompt=spec.prompt, rubric=rubric_text, concepts=concepts, n=n))
    try:
        candidates = _parse_json_array(raw)
    except Exception as exc:
        logger.warning(f"[prelab] variant JSON unparseable: {exc}")
        return []

    out: list[PrelabSpec] = []
    for cand in candidates[:n]:
        prompt = (cand.get("prompt") or "").strip()
        if not prompt:
            continue
        rubric = [str(r).strip() for r in (cand.get("rubric") or []) if str(r).strip()]
        variant = PrelabSpec(
            prompt=prompt,
            rubric=rubric,
            concepts=[str(c).strip() for c in (cand.get("concepts") or spec.concepts)
                      if str(c).strip()],
            title=spec.title,
            origin="generated",
        )

        try:
            source = _strip_code_fence(generate(_SOLVE_PROMPT.format(
                prompt=variant.prompt,
                rubric="\n".join(f"- {r}" for r in rubric) or "- (none stated)",
                format_block="")))
        except Exception as exc:
            logger.warning(f"[prelab] variant solver call failed: {exc}")
            continue

        result = compile_and_run_c(source)
        if not result["ok"] or not result["stdout"].strip():
            logger.info(f"[prelab] dropped a variant: "
                        f"{result['stage']}: {result['error'][:200]}")
            continue

        variant.reference_solution = source
        variant.sample_output = result["stdout"].rstrip("\n")
        variant.verification = {
            "status": "verified",
            "message": "Sample output was produced by running a solution written "
                       "from this variant's own requirements, so the two agree.",
        }
        out.append(variant)

    return out


# ══════════════════════════════════════════════════════════════════════════
# 5. Storage shape
# ══════════════════════════════════════════════════════════════════════════

def normalise_stored(entry: Any) -> dict:
    """Read one stored prelab, old shape or new.

    Before this feature a prelab was a bare string, and eleven of them are still
    stored that way. A string becomes a prompt with an empty rubric so old and new
    entries can sit in the same list and be rendered by the same code.
    """
    if isinstance(entry, str):
        return {"prompt": entry, "rubric": [], "sample_output": "",
                "concepts": [], "origin": "legacy", "verification": {}}
    if isinstance(entry, dict):
        return {
            "prompt": (entry.get("prompt") or "").strip(),
            "rubric": list(entry.get("rubric") or []),
            "sample_output": entry.get("sample_output") or "",
            "concepts": list(entry.get("concepts") or []),
            "reference_solution": entry.get("reference_solution") or "",
            "origin": entry.get("origin") or "upload",
            "verification": entry.get("verification") or {},
        }
    return {"prompt": "", "rubric": [], "sample_output": "",
            "concepts": [], "origin": "unknown", "verification": {}}


def student_view(entry: Any) -> dict | None:
    """The part of a prelab a STUDENT may see.

    The rubric and the reference solution are the answer key for grading; they are
    dropped here so no endpoint reachable by a learner can leak them. The sample
    output is kept because the classroom modal already renders it behind a
    collapsed "Sample output" disclosure, which is how the handout presents it too.
    """
    full = normalise_stored(entry)
    if not full["prompt"]:
        return None
    samples = ([{"label": "Sample output", "text": full["sample_output"]}]
               if full["sample_output"].strip() else [])
    return {
        "prompt": full["prompt"],
        "concept": "; ".join(full["concepts"]),
        "samples": samples,
    }
