# backend/app/core/auth.py
"""
Server-side authentication and authorization.

WHY THIS EXISTS
---------------
Identity used to be whatever the browser claimed. Two concrete holes were reported
by a security reviewer and confirmed in the code:

  1. `verify_teacher` compared a client-supplied `X-User-Role` header against the
     string "teacher". The frontend read that value out of localStorage, so editing
     `c_tutor_user` to {"role":"teacher"} in devtools granted the teacher dashboard
     AND satisfied the backend check.

  2. Login issued no credential at all. ~13 endpoints took `{username}` straight from
     the URL path with no auth, so requesting another student's username returned
     their data — no tampering required, just a different URL.

The fix: a signed JWT, set as an httpOnly cookie the page's JavaScript cannot read
or forge. Every protected endpoint derives BOTH the username and the role from that
token. A request never gets to say who it is.

COOKIE, NOT localStorage
------------------------
An httpOnly cookie is unreadable from JS, so an XSS bug or a curious student with
devtools cannot lift or alter it. The signature means a forged token fails
verification even if someone hand-crafts one.

SECRET KEY
----------
Read from SAGE_JWT_SECRET. If unset, a random key is generated per process — which
is FAIL-SAFE, not fail-open: tokens simply stop validating when the process
restarts, logging everyone out. Set it in .env for a real deployment; a warning is
logged when it is missing.
"""
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, Request

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"
COOKIE_NAME = "sage_session"
def _env(name: str, default: str) -> str:
    """Env var, treating EMPTY as absent.

    os.getenv(name, default) returns the default only when the variable is
    missing — an empty value comes back as "". The deploy workflow writes every
    key unconditionally, so an unset GitHub variable lands in .env as
    `SAGE_SESSION_HOURS=`, Compose passes it through as "", and int("") crashed
    the container on import in a restart loop. Anything read here must survive
    being present-but-empty.
    """
    return (os.getenv(name) or default).strip()


try:
    TOKEN_TTL_HOURS = int(_env("SAGE_SESSION_HOURS", "12"))
    if TOKEN_TTL_HOURS <= 0:
        raise ValueError("must be positive")
except ValueError:
    logger.warning("SAGE_SESSION_HOURS=%r is not a positive integer; using 12.",
                   os.getenv("SAGE_SESSION_HOURS"))
    TOKEN_TTL_HOURS = 12

_SECRET = _env("SAGE_JWT_SECRET", "")
if not _SECRET:
    _SECRET = secrets.token_urlsafe(48)
    logger.warning(
        "SAGE_JWT_SECRET is not set — generated an ephemeral key. All sessions will "
        "be invalidated on restart. Set SAGE_JWT_SECRET in .env for deployment."
    )

# Secure flag is only meaningful over TLS, and setting it on plain HTTP makes the
# browser drop the cookie entirely — which would lock everyone out. Driven by env so
# it flips on the moment HTTPS is in front of the app.
COOKIE_SECURE = _env("SAGE_COOKIE_SECURE", "false").lower() in ("1", "true", "yes")


def create_token(username: str, role: str, name: str = "") -> str:
    """Mint a signed session token."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": username,
        "role": role,
        "name": name,
        "iat": now,
        "exp": now + timedelta(hours=TOKEN_TTL_HOURS),
    }
    return jwt.encode(payload, _SECRET, algorithm=ALGORITHM)


def set_session_cookie(response, token: str) -> None:
    """Attach the session cookie to a response."""
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,               # unreadable from JavaScript
        secure=COOKIE_SECURE,        # see note above
        samesite="lax",              # blocks the cookie on cross-site POSTs (CSRF)
        max_age=TOKEN_TTL_HOURS * 3600,
        path="/",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/")


def _decode(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, _SECRET, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def get_current_user(request: Request) -> dict:
    """Resolve the caller from their session cookie. 401 if absent or invalid.

    This is the ONLY sanctioned source of identity. Never take a username from a
    path parameter, query string, or request body and treat it as authenticated —
    that was the original vulnerability.
    """
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    claims = _decode(token)
    if not claims:
        raise HTTPException(status_code=401, detail="Session expired or invalid")
    return {
        "username": claims.get("sub", ""),
        "role": claims.get("role", "student"),
        "name": claims.get("name", ""),
    }


def get_current_user_optional(request: Request) -> Optional[dict]:
    """Same, but returns None instead of raising — for endpoints that tailor their
    response to a known user but do not require one."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    claims = _decode(token)
    if not claims:
        return None
    return {
        "username": claims.get("sub", ""),
        "role": claims.get("role", "student"),
        "name": claims.get("name", ""),
    }


def require_teacher(user: dict = Depends(get_current_user)) -> dict:
    """Authorize a teacher/admin. The role comes from the SIGNED TOKEN, so a student
    cannot grant it to themselves by editing browser storage."""
    if user["role"] not in ("teacher", "admin"):
        raise HTTPException(status_code=403, detail="Teacher access required")
    return user


def require_self_or_teacher(target_username: str, user: dict) -> None:
    """Guard a per-student resource.

    Students may only reach their own records; teachers may reach anyone's. Call
    this in every endpoint that still takes a `{username}` path parameter — the
    parameter selects WHICH record, while `user` decides whether that is allowed.
    """
    if user["role"] in ("teacher", "admin"):
        return
    if (target_username or "").strip().lower() != user["username"].strip().lower():
        raise HTTPException(status_code=403, detail="You can only access your own data")
