# Abstract Base Class Agent
import asyncio
import json
import re
from abc import ABC, abstractmethod
from typing import AsyncGenerator
import logging
from app.agents.schema import AgentState
from app.db.llm_interface import LLMInterface

class BaseAgent(ABC):
    def __init__(self, llm: LLMInterface, logger: logging.Logger):
        self.llm = llm
        self.logger = logger

    @abstractmethod
    async def process(self, state: AgentState) -> AsyncGenerator[dict, None]:
        """
        Processes the state.
        Yields chunks: {"type": "token", "text": "..."} or {"type": "status", ...}
        Returns updated state (implicitly via object mutation or return).
        """
        pass

    def _preference_directive(self, profile: dict) -> str:
        """Build a shared tone/format directive from the student's saved preferences.

        F7-02: preferences (custom tone, no-diagrams, concise, literal) were only
        applied in the concept-teaching path, so other LLM-generated responses ignored
        them. Append this to any FREE-TEXT prose prompt so every agent honors the same
        preferences. Do NOT append to prompts that must return structured JSON.
        Returns "" when no preferences are set.
        """
        prefs = (profile or {}).get("tutor_preferences", {}) or {}
        parts = []
        custom = (prefs.get("custom_instructions") or "").strip()
        if custom:
            parts.append(f"Follow the student's custom instructions verbatim: {custom}")
        if prefs.get("concise_mode"):
            parts.append("Be extremely concise — no filler.")
        if prefs.get("literal_mode"):
            parts.append("Be literal and precise; avoid metaphors and analogies.")
        if prefs.get("show_visual_model") is False:
            parts.append("Do NOT include any diagrams, ASCII art, or Mermaid charts.")
        if not parts:
            return ""
        return "\n\n**STUDENT PREFERENCES (MUST FOLLOW):**\n- " + "\n- ".join(parts)

    # Minimum plausible length for a code answer, in non-whitespace characters.
    # "i;" is 2 — anything at or below this cannot be a real answer to either the
    # micro ("one line of C") or code ("2-4 line snippet") prompt.
    _MIN_CODE_CHARS = 6

    async def _llm_grade_code(self, student_code: str, concept: str, tier: str = "micro") -> dict:
        """Grade a free-form code answer for the micro / code evidence tiers.

        Unlike the quiz tier there is no stored answer key — these prompts are
        open-ended ("write one line of C that uses Variables"), so the rubric IS
        the concept. The judge is asked two independent questions: is this valid
        C, and does it genuinely exercise `concept`. Both must hold.

        This replaces two weaker checks that let a student certify without writing
        real C: a punctuation test (`any(c in q for c in ";{}=()")`) in the mastery
        exam, and a keyword scan of the reviewer's own prose in the code tier.

        Lives on BaseAgent because both the Examiner (mastery exam, micro
        challenges) and the Reviewer (code submissions) grade the same artifact.

        Returns {"is_correct": True|False|None, "feedback": str}. `None` means the
        judge could not be reached — the caller MUST then record no evidence at
        all rather than a failure, so an LLM outage never penalises a student who
        actually answered.
        """
        code = (student_code or "").strip()

        # Cheap structural rejects — no need to spend an LLM call on these.
        if len("".join(code.split())) < self._MIN_CODE_CHARS:
            return {"is_correct": False,
                    "feedback": "That's too short to be C code — give it a real try!"}
        if not any(c in code for c in [";", "{", "}", "=", "(", ")"]):
            return {"is_correct": False,
                    "feedback": "That didn't look like C code. Try writing an actual statement."}

        expectation = ("a single line of C" if tier == "micro"
                       else "a short C snippet of about 2-4 lines")

        # Line-oriented verdict, NOT JSON. Models comply with this far more reliably
        # than with a JSON schema, and a brittle parser here is not a cosmetic issue:
        # an unparseable reply means "ungradable", which silently records no evidence
        # at all. That failure mode shipped once — a student pasting correct code saw
        # the ledger stick at the same "~2 more" no matter how many times they tried.
        prompt = f"""
        You are a C Programming Tutor grading a student's code answer.

        **Task given to the student:** write {expectation} that demonstrates **{concept}**.
        **Student's code:**
        ```c
        {code}
        ```

        Judge TWO things independently:
        1. VALID_C — is this syntactically plausible C? (A single statement need not
           compile standalone; judge the fragment on its own terms. Pseudocode,
           another language, prose, or stray punctuation is NOT valid C. A missing
           #include is a compile hint, not a syntax error — still valid C.)
        2. USES_CONCEPT — does it genuinely exercise **{concept}**? It must actually
           use the concept, not merely mention it in a comment or a variable name.

        Do not award credit for a token answer: bare punctuation, a lone keyword, an
        empty statement, a placeholder, or code unrelated to {concept}.

        Reply in EXACTLY this format, nothing else:

        VALID_C: yes
        USES_CONCEPT: yes
        FEEDBACK: one or two sentences spoken directly to the student using "You"

        Use "yes" or "no" for the first two lines. Never say "the student".
        """

        raw = ""
        try:
            raw = await asyncio.to_thread(self.llm.generate_response, prompt) or ""
        except Exception as e:
            self.logger.error(f"LLM Code Grade call failed ({tier}/{concept}): {e}")
            return self._ungradable()

        # generate_response does NOT raise on provider failure — it returns a string
        # beginning "LLM_ERROR:". Treat that as ungradable rather than parsing it.
        if raw.strip().startswith("LLM_ERROR"):
            self.logger.error(f"LLM Code Grade provider error ({tier}/{concept}): {raw[:120]}")
            return self._ungradable()

        verdict = self._parse_code_verdict(raw)
        if verdict is None:
            self.logger.error(
                f"LLM Code Grade unparseable ({tier}/{concept}): {raw[:200]!r}"
            )
            return self._ungradable()

        valid_c, uses_concept, fb = verdict
        ok = valid_c and uses_concept
        if not fb:
            fb = "Nice work!" if ok else "Not quite yet — check it against the task."
        return {"is_correct": ok, "feedback": ("✅ " if ok else "❌ ") + fb}

    @staticmethod
    def _ungradable() -> dict:
        """No verdict could be obtained → caller records NO evidence."""
        return {"is_correct": None,
                "feedback": "⚠️ I couldn't check that one just now, so it wasn't "
                            "recorded — please try submitting it again."}

    @staticmethod
    def _parse_code_verdict(raw: str):
        """Pull (valid_c, uses_concept, feedback) out of a judge reply.

        Accepts the line format first, then falls back to a JSON object for
        compatibility with any model that insists on emitting one. Returns None if
        neither yields both booleans — callers must treat that as ungradable rather
        than guessing, since a wrong guess either fabricates evidence or destroys it.
        """
        def _yes(text):
            return text.strip().lower().startswith(("yes", "true"))

        m_valid = re.search(r"VALID_C\s*[:=]\s*(\w+)", raw, re.IGNORECASE)
        m_uses = re.search(r"USES_CONCEPT\s*[:=]\s*(\w+)", raw, re.IGNORECASE)
        if m_valid and m_uses:
            m_fb = re.search(r"FEEDBACK\s*[:=]\s*(.+)", raw, re.IGNORECASE | re.DOTALL)
            fb = m_fb.group(1).strip().split("\n")[0].strip() if m_fb else ""
            return _yes(m_valid.group(1)), _yes(m_uses.group(1)), fb

        # JSON fallback.
        try:
            cleaned = raw.replace("```json", "").replace("```", "").strip()
            start, end = cleaned.find("{"), cleaned.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(cleaned[start:end])
                if "valid_c" in data and "uses_concept" in data:
                    return (bool(data["valid_c"]), bool(data["uses_concept"]),
                            str(data.get("feedback") or ""))
        except Exception:
            pass

        return None