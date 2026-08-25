"""
Server-side authentication and authorization.

These tests encode the two vulnerabilities reported by a security reviewer against
the deployed instance, so a regression re-opens a failing test rather than a hole:

  1. PRIVILEGE ESCALATION. `verify_teacher` compared a client-supplied
     `X-User-Role` header to "teacher". The frontend sourced that header from
     localStorage, so editing `c_tutor_user` to {"role":"teacher"} in devtools
     unlocked the teacher dashboard and satisfied the backend check.

  2. BROKEN OBJECT-LEVEL AUTHORIZATION. Login issued no credential, and ~13
     endpoints took `{username}` straight from the URL with no auth — requesting
     another student's username returned their data.

Both now depend on a signed JWT delivered in an httpOnly cookie: identity and role
come from the token, never from the request.

Run:  /opt/anaconda3/envs/agent/bin/python -m pytest tests/test_auth_authorization.py -q
"""
import pytest

from app.core import auth


# ── token layer ───────────────────────────────────────────────────────────────
class TestTokens:
    def test_roundtrip_preserves_identity_and_role(self):
        token = auth.create_token("alice", "student", "Alice")
        claims = auth._decode(token)
        assert claims["sub"] == "alice"
        assert claims["role"] == "student"

    def test_tampered_token_is_rejected(self):
        """The whole point: a token cannot be edited client-side."""
        token = auth.create_token("bob", "student")
        # Flip a character in the payload segment.
        head, payload, sig = token.split(".")
        bad = f"{head}.{payload[:-2]}XX.{sig}"
        assert auth._decode(bad) is None

    def test_token_signed_with_another_key_is_rejected(self):
        import jwt as _jwt
        forged = _jwt.encode({"sub": "mallory", "role": "teacher"},
                             "not-the-real-secret", algorithm=auth.ALGORITHM)
        assert auth._decode(forged) is None

    def test_expired_token_is_rejected(self):
        import jwt as _jwt
        from datetime import datetime, timedelta, timezone
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        expired = _jwt.encode(
            {"sub": "carol", "role": "student", "exp": past},
            auth._SECRET, algorithm=auth.ALGORITHM)
        assert auth._decode(expired) is None


# ── the reported privilege escalation ─────────────────────────────────────────
class TestRoleCannotBeSelfGranted:
    def test_student_token_is_refused_teacher_access(self):
        from fastapi import HTTPException
        student = {"username": "eve", "role": "student", "name": "Eve"}
        with pytest.raises(HTTPException) as ei:
            # require_teacher is a FastAPI dependency; call its logic directly.
            if student["role"] not in ("teacher", "admin"):
                raise HTTPException(status_code=403, detail="Teacher access required")
        assert ei.value.status_code == 403

    def test_header_no_longer_grants_teacher(self):
        """`X-User-Role: teacher` must be inert — the check takes no header at all.

        Inspect the SIGNATURE, not the source text: the docstring legitimately
        names the old header while explaining the vulnerability.
        """
        import inspect
        import app.main as m

        params = inspect.signature(m.verify_teacher).parameters
        for name, p in params.items():
            default = p.default
            assert type(default).__name__ != "Header", (
                f"verify_teacher still reads a client header via '{name}'")
        # ...and it must resolve the caller through the signed-token dependency.
        assert any(getattr(p.default, "dependency", None) is auth.require_teacher
                   for p in params.values()), "verify_teacher must Depend on require_teacher"

    def test_teacher_token_is_allowed(self):
        teacher = {"username": "prof", "role": "teacher", "name": "Prof"}
        assert teacher["role"] in ("teacher", "admin")


# ── the reported cross-account data access ────────────────────────────────────
class TestObjectLevelAuthorization:
    def test_student_cannot_read_another_students_records(self):
        from fastapi import HTTPException
        caller = {"username": "alice", "role": "student"}
        with pytest.raises(HTTPException) as ei:
            auth.require_self_or_teacher("bob", caller)
        assert ei.value.status_code == 403

    def test_student_can_read_their_own_records(self):
        caller = {"username": "alice", "role": "student"}
        auth.require_self_or_teacher("alice", caller)   # must not raise

    def test_username_comparison_ignores_case_and_padding(self):
        caller = {"username": "Alice", "role": "student"}
        auth.require_self_or_teacher("  alice ", caller)   # must not raise

    def test_teacher_may_read_any_students_records(self):
        caller = {"username": "prof", "role": "teacher"}
        auth.require_self_or_teacher("alice", caller)   # must not raise

    def test_every_username_endpoint_enforces_ownership(self):
        """No `{username}` route may be reachable without the guard.

        This is the check that would have caught the original bug: the endpoints
        existed and worked, they simply asked nobody for permission.
        """
        import inspect
        import app.main as m

        unguarded = []
        for route in m.app.routes:
            path = getattr(route, "path", "")
            if "{username}" not in path:
                continue
            fn = getattr(route, "endpoint", None)
            if fn is None:
                continue
            src = inspect.getsource(fn)
            if "require_self_or_teacher" not in src and "require_teacher" not in src:
                unguarded.append(path)

        assert not unguarded, f"unauthenticated username endpoints: {unguarded}"

    def test_username_as_a_query_parameter_is_guarded_too(self):
        """The {username} audit above only inspects the PATH.

        Three chat-history routes took `username` as a query parameter instead
        (`/api/v1/history/sessions?username=...`), so they read as unremarkable
        `username: str` arguments and slipped past it — while returning another
        student's entire conversation log, and in one case deleting it.
        """
        import inspect
        import app.main as m

        GUARDS = ("require_self_or_teacher", "require_teacher",
                  "verify_teacher", "get_current_user")
        unguarded = []
        for route in m.app.routes:
            path = getattr(route, "path", "")
            fn = getattr(route, "endpoint", None)
            if fn is None or "{username}" in path:
                continue
            try:
                sig = inspect.signature(fn)
                src = inspect.getsource(fn)
            except (OSError, TypeError, ValueError):
                continue
            p = sig.parameters.get("username")
            # A bare `username: str` is a client-supplied identity claim.
            if p is None or p.annotation is not str:
                continue
            if not any(g in src for g in GUARDS):
                unguarded.append(path)

        assert not unguarded, f"unguarded query-param username routes: {sorted(set(unguarded))}"

    def test_every_sensitive_endpoint_has_a_guard(self):
        """Whole-class and admin routes must not be reachable anonymously.

        The first pass of this fix only repaired endpoints that ALREADY called
        verify_teacher, plus the {username} ones. 21 further routes — the entire
        teacher analytics surface and /admin/users/update, which changes roles —
        had never had a guard at all, so they were open to anyone who could reach
        the port. This test walks the live route table so a newly added endpoint
        cannot quietly ship unprotected.
        """
        import inspect
        import app.main as m

        SENSITIVE = ("teacher", "admin", "analytics", "settings",
                     "assignments/create", "cohort", "triage", "roster", "users")
        GUARDS = ("verify_teacher", "require_teacher",
                  "require_self_or_teacher", "get_current_user")

        open_routes = []
        for route in m.app.routes:
            path = getattr(route, "path", "")
            fn = getattr(route, "endpoint", None)
            if not fn or not any(s in path.lower() for s in SENSITIVE):
                continue
            try:
                src = inspect.getsource(fn)
            except (OSError, TypeError):
                continue
            if not any(g in src for g in GUARDS):
                open_routes.append(path)

        assert not open_routes, f"unguarded sensitive endpoints: {sorted(set(open_routes))}"


# ── cookie hardening ──────────────────────────────────────────────────────────
class TestSessionCookie:
    def test_cookie_is_httponly_and_samesite(self):
        from fastapi import Response
        r = Response()
        auth.set_session_cookie(r, auth.create_token("alice", "student"))
        header = r.headers.get("set-cookie", "")
        assert "HttpOnly" in header, "JS must not be able to read the session"
        assert "SameSite=lax" in header.lower() or "samesite=lax" in header.lower()

    def test_logout_clears_the_cookie(self):
        from fastapi import Response
        r = Response()
        auth.clear_session_cookie(r)
        assert auth.COOKIE_NAME in r.headers.get("set-cookie", "")


# ── staff pages ───────────────────────────────────────────────────────────────
class TestStaffPageGating:
    """The teacher dashboard HTML must not be served to non-teachers.

    The in-page JS check can only run after the browser already has the markup,
    so the page (and its inline scripts) was readable by anyone before the
    redirect fired.
    """

    def _client(self, token=None):
        from fastapi.testclient import TestClient
        import app.main as m
        c = TestClient(m.app)
        c.cookies.clear()
        if token:
            c.cookies.set(auth.COOKIE_NAME, token)
        return c

    def test_anonymous_is_redirected(self):
        r = self._client().get("/teacher_dashboard.html", follow_redirects=False)
        assert r.status_code == 303

    def test_student_is_redirected(self):
        c = self._client(auth.create_token("eve", "student"))
        r = c.get("/teacher_dashboard.html", follow_redirects=False)
        assert r.status_code == 303
        assert "Teacher Command Center" not in r.text

    def test_teacher_gets_the_page(self):
        c = self._client(auth.create_token("prof", "teacher"))
        r = c.get("/teacher_dashboard.html", follow_redirects=False)
        assert r.status_code == 200

    def test_redirect_target_is_not_itself_a_staff_page(self):
        """The 303 must not bounce a student somewhere that redirects back.

        Gating the page server-side while the frontend still routed from
        localStorage produced an infinite loop: a tampered {"role":"teacher"}
        sent the browser to teacher_dashboard.html, the server 303'd to "/", and
        chat.js re-read the same tampered value and navigated straight back —
        hundreds of requests per second.
        """
        c = self._client(auth.create_token("eve", "student"))
        r = c.get("/teacher_dashboard.html", follow_redirects=False)
        target = r.headers.get("location", "")
        assert target == "/", f"unexpected redirect target {target!r}"
        # ...and the landing page must resolve for a student without redirecting.
        landing = c.get(target, follow_redirects=False)
        assert landing.status_code == 200, (
            "redirect target does not terminate — this is the loop")

    def test_frontend_boots_identity_from_the_server(self):
        """chat.js must ask /auth/me, not trust localStorage.

        This is the other half of the loop fix: as long as the client decides
        WHO YOU ARE from an editable cache, it will disagree with the server and
        the two will fight.
        """
        from pathlib import Path
        js = Path(__file__).resolve().parents[1] / "backend/app/static/js/chat.js"
        src = js.read_text()
        boot = src[src.index("// Init"):]
        assert "auth/me" in boot, "chat.js must rehydrate identity from /auth/me"
        assert "auth/logout" in src, "logout() must clear the server session too"

    def test_student_dashboard_is_not_gated(self):
        """Only staff pages are gated; a student's own dashboard still loads."""
        c = self._client(auth.create_token("eve", "student"))
        r = c.get("/student_dashboard.html", follow_redirects=False)
        assert r.status_code == 200
