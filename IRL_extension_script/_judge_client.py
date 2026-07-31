"""Shared judge-client factory for the IRL extension evaluation suite.

WHY THIS EXISTS
───────────────
The judge is an *instrument*. Which model produced a verdict is part of the measurement,
not an implementation detail, so it has to be (a) switchable without editing rubrics and
(b) recorded next to every verdict it produces.

The suite's default judge is `gpt-4o`, which is what the conference-paper comparison uses
and what published numbers should come from. `gemini-flash-latest` is supported as a
fallback for when the OpenAI account is out of credit; it reaches the same Chat Completions
API through Google's OpenAI-compatibility endpoint, so no rubric or parsing code changes.

TWO THINGS THAT WILL BITE YOU IF CHANGED CARELESSLY
───────────────────────────────────────────────────
1. `gemini-flash-latest` spends output tokens on internal reasoning before emitting JSON.
   At the suite's original `max_tokens=120` it returns a truncated fragment, every verdict
   fails to parse, and the callers' `except` turns that into a silent column of `None` —
   the exact failure that produced a table of NaNs earlier in this project. Hence
   JUDGE_MAX_TOKENS, which defaults per provider rather than per call site.
2. Do NOT use the Gemini judge for eval_03 win rate. `gemini_raw` and `gemini_tutor` are
   two of the five systems being ranked there, so a Gemini judge scores its own family —
   self-preference bias on the exact comparison the result rests on. gpt-4o only.

USAGE
─────
    from _judge_client import make_judge          # noqa
    client, model, max_tokens = make_judge()

    JUDGE_PROVIDER=gemini python IRL_extension_script/eval_02_sage_delta.py --judge --fresh
"""

from __future__ import annotations

import os

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

_DEFAULTS = {
    # provider: (default model, default max_tokens)
    "openai": ("gpt-4o", 120),
    "gemini": ("gemini-flash-latest", 800),
}


def make_judge(provider: str | None = None):
    """Return (client, model, max_tokens) for the configured judge provider.

    provider  — "openai" (default) or "gemini"; env JUDGE_PROVIDER overrides.
    model     — env OPENAI_JUDGE_MODEL overrides the per-provider default.
    max_tokens— env JUDGE_MAX_TOKENS overrides the per-provider default.
    """
    from openai import OpenAI

    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, ".."))
    sys.path.append(os.path.join(root, "backend"))
    from app.core import config  # noqa: E402

    provider = (provider or os.getenv("JUDGE_PROVIDER", "openai")).strip().lower()
    if provider not in _DEFAULTS:
        raise SystemExit(f"ERROR: unknown JUDGE_PROVIDER={provider!r} "
                         f"(expected one of {sorted(_DEFAULTS)})")

    default_model, default_tokens = _DEFAULTS[provider]
    model = os.getenv("OPENAI_JUDGE_MODEL", default_model)
    max_tokens = int(os.getenv("JUDGE_MAX_TOKENS", default_tokens))

    if provider == "gemini":
        key = getattr(config, "GOOGLE_API_KEY", None)
        if not key:
            raise SystemExit("ERROR: GOOGLE_API_KEY not set")
        client = OpenAI(api_key=key, base_url=GEMINI_BASE_URL)
    else:
        key = getattr(config, "OPENAI_API_KEY", None)
        if not key:
            raise SystemExit("ERROR: OPENAI_API_KEY not set")
        client = OpenAI(api_key=key)

    print(f"[judge] provider={provider} model={model} max_tokens={max_tokens}")
    return client, model, max_tokens
