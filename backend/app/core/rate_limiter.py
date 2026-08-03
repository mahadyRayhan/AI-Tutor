# backend/app/core/rate_limiter.py
#
# Lightweight, dependency-free rate limiter (sliding-window log).
# ──────────────────────────────────────────────────────────────
# Why hand-rolled instead of slowapi: no new dependency (nothing to break a running
# --reload server or bloat the image), and it is trivial to reason about at our scale.
#
# Design:
#   • Per-(scope, client-IP) sliding window. "scope" separates independent buckets
#     (e.g. "chat" vs "auth") so a burst of logins doesn't consume the chat budget.
#   • Sliding window (a deque of hit timestamps) — more accurate than a fixed window,
#     which lets 2× the limit fire across a window boundary.
#   • Real client IP is read from X-Forwarded-For (leftmost) because in production the
#     app sits behind nginx/ALB; request.client.host would otherwise be the proxy and
#     every user would share one bucket.
#
# Scope & limits: this is IN-MEMORY and PER-PROCESS. It is correct for a single
# instance (what we run today). If we ever run multiple replicas/workers, the counters
# must move to a shared store (Redis) so the limit is enforced across instances — the
# feature stays the same, only the backend changes.

import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

_lock = threading.Lock()
_hits: "defaultdict[str, deque]" = defaultdict(deque)

# Opportunistic memory sweep so idle IPs don't accumulate empty deques forever.
_SWEEP_EVERY = 5000
_calls_since_sweep = 0


def _client_ip(request: Request) -> str:
    """Best-effort real client IP. Trusts X-Forwarded-For only because we deploy
    behind a known reverse proxy; the leftmost entry is the original client."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def _sweep_locked(now: float, window: float) -> None:
    """Drop buckets whose newest hit is older than the window (caller holds _lock)."""
    stale = [k for k, dq in _hits.items() if not dq or dq[-1] < now - window]
    for k in stale:
        del _hits[k]


def _check(key: str, max_requests: int, window: float):
    """Return (allowed: bool, retry_after_seconds: int)."""
    global _calls_since_sweep
    now = time.time()
    with _lock:
        dq = _hits[key]
        cutoff = now - window
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= max_requests:
            retry = int(dq[0] + window - now) + 1
            return False, max(retry, 1)
        dq.append(now)

        _calls_since_sweep += 1
        if _calls_since_sweep >= _SWEEP_EVERY:
            _calls_since_sweep = 0
            _sweep_locked(now, window)
        return True, 0


def rate_limit(scope: str, max_requests: int, window_seconds: int = 60):
    """
    Build a FastAPI dependency enforcing `max_requests` per `window_seconds` per IP
    for the given scope. Attach via the decorator's `dependencies=[...]` list so the
    endpoint signature is untouched:

        @app.post("/api/v1/chat/stream",
                  dependencies=[Depends(rate_limit("chat", 20, 60))])
    """
    async def _dependency(request: Request):
        ip = _client_ip(request)
        allowed, retry = _check(f"{scope}:{ip}", max_requests, window_seconds)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=f"Too many requests. Please wait {retry}s and try again.",
                headers={"Retry-After": str(retry)},
            )
    return _dependency


def reset():
    """Clear all counters (used by tests)."""
    with _lock:
        _hits.clear()
