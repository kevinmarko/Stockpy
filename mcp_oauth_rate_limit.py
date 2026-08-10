"""In-process sliding-window rate limiting for the InvestYo MCP OAuth 2.1
authorization server's unauthenticated HTTP surface.

Why this exists: RFC 7591 dynamic client registration (``/register``) is
unauthenticated by design, the ``/login`` form is the human-facing trust
boundary that ``mcp_oauth_store.py``'s own docstring already calls out as the
"real perimeter", and ``/token`` is reachable by anyone holding a valid
authorization code or refresh token. None of the three has any per-IP request
budget of their own -- ``mcp_oauth_store.McpOAuthStore.record_login_failure``
/ ``is_locked_out`` is a durable, PASSWORD-FAILURE-COUNT lockout (5 failures /
15 min, global, not per-IP), a different and complementary control. This
module is defense-in-depth on raw REQUEST RATE, per source IP, on top of that
-- it does not replace or duplicate the login lockout.

Scope (endpoints wrapped): ``/register``, ``/login`` (GET+POST share one
bucket), ``/token``. Deliberately NOT wrapped: ``/mcp`` (already gated by the
MCP SDK's own ``RequireAuthMiddleware`` bearer check once a token is issued),
``/authorize`` and ``/revoke`` (out of scope for this change), and any
``/.well-known/*`` metadata route (must stay always-reachable for OAuth
discovery -- see ``RATE_LIMIT_RULES``, which simply never has an entry for it).

Algorithm: a plain in-process, in-memory sliding-window counter -- no Redis,
matching this module's own in-process login-lockout precedent
(``mcp_oauth_store.py``). ``SlidingWindowLimiter`` holds a
``Dict[str, Deque[float]]`` of hit timestamps keyed by ``f"{bucket}:{ip}"``;
``check()`` prunes anything older than the window, checks whether the pruned
window is already at capacity, and otherwise records the new hit and allows
it. This runs synchronously with no ``await`` between the prune-and-check and
the record step, so it is safe under asyncio's single-threaded event loop
without an explicit lock -- there is no yield point for a concurrent request
to interleave through. Not persisted across restarts (a fresh process starts
every bucket empty); accepted trade-off, since the durable, security-critical
control (login lockout) already lives in ``mcp_oauth_store.py``.

Concrete limits (hardcoded module constants, deliberately NOT ``settings``
fields -- matching ``mcp_oauth_store.py``'s ``LOGIN_LOCKOUT_THRESHOLD`` /
``LOGIN_LOCKOUT_SECONDS`` precedent):

- ``register``: 10 requests / 3600s per IP.
- ``login`` (GET+POST shared): 30 requests / 300s per IP -- defense-in-depth
  on top of (never duplicating) the store's existing 5-failure/15-min
  *global* password lockout; this bounds request RATE, not attempt COUNT.
- ``token``: 60 requests / 3600s per IP -- higher because legitimate
  steady-state refresh traffic lands here (``ACCESS_TOKEN_TTL_SECONDS =
  3600`` in ``mcp_oauth_store.py``, so roughly one refresh per hour per live
  session).

CF-Connecting-IP / X-Forwarded-For trust decision (verified empirically
against this deployment, not re-derived here):

- ``~/.cloudflared/config.yml``'s named tunnel ingress is
  ``service: http://localhost:8080`` -- ``cloudflared`` connects to the
  origin over loopback.
- The ``uvicorn`` version in this venv defaults to ``proxy_headers=True`` and
  ``forwarded_allow_ips="127.0.0.1"`` when neither is overridden, and
  ``investyo_mcp_server.py``'s ``uvicorn.run(app, host=args.host,
  port=args.port, ...)`` call never overrides either -- so uvicorn's own
  ``ProxyHeadersMiddleware`` already wraps outside whatever app is passed in,
  trusting only a loopback direct peer to supply ``X-Forwarded-For``.
  ``~/Library/Logs/investyo-mcp-oauth-server.log`` already shows genuine
  external client IPs directly in uvicorn's own access log, confirming
  ``cloudflared`` sets ``X-Forwarded-For`` on its loopback hand-off and that
  uvicorn's default trust config already honors it.
- ``CF-Connecting-IP`` is used as PRIMARY (Cloudflare-edge-set, single-value,
  more reliable than ``X-Forwarded-For``, which has a documented cloudflared
  double-append bug); the left-most entry of ``X-Forwarded-For`` is the
  fallback.
- Trust gate: either header is honored ONLY when the direct ASGI peer
  (``scope["client"][0]``) is loopback (``127.0.0.1`` / ``::1``) -- this
  mirrors uvicorn's own default exactly, and is self-sufficient (it does not
  rely on uvicorn's ``ProxyHeadersMiddleware`` having actually run, which
  matters because this repo's ``TestClient``-based tests never go through
  ``uvicorn.run()``).

Residual risk (must stay documented here, not just in the design doc):
``--host 0.0.0.0`` means the port is reachable other ways than through
Cloudflare (LAN, or a port-forwarded public path). A request arriving that
way has a non-loopback direct peer, so the header is correctly ignored and
the attacker's own real ``scope["client"][0]`` is used instead -- safe and
un-spoofable, just not "through Cloudflare". NOT covered: something already
running LOCALLY (loopback) forging ``CF-Connecting-IP`` directly against the
port, bypassing ``cloudflared`` entirely -- that requires local code
execution already, which is explicitly out of scope for this control.
"""

from __future__ import annotations

import json
import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Optional

_LOOPBACK_ADDRESSES = {"127.0.0.1", "::1"}


def _resolve_client_ip(scope) -> str:  # noqa: ANN001 - raw ASGI scope dict
    """Resolves the "real" client IP for a single ASGI ``http`` scope,
    applying the trust gate documented in this module's docstring.

    Falls back to ``"unknown"`` only when the ASGI server supplies no
    ``scope["client"]`` at all (e.g. some non-standard test harness) -- never
    raises, since a rate limiter must never be the reason a request fails.
    """
    client = scope.get("client")
    direct_peer = client[0] if client else None

    if direct_peer in _LOOPBACK_ADDRESSES:
        headers = dict(scope.get("headers") or [])

        cf_ip = headers.get(b"cf-connecting-ip")
        if cf_ip:
            candidate = cf_ip.decode("latin-1").strip()
            if candidate:
                return candidate

        xff = headers.get(b"x-forwarded-for")
        if xff:
            first = xff.decode("latin-1").split(",")[0].strip()
            if first:
                return first

    return direct_peer or "unknown"


@dataclass(frozen=True)
class RateLimitRule:
    """A per-IP budget: at most ``limit`` requests per ``window_seconds``."""

    limit: int
    window_seconds: float


# Bucket name -> rule. Bucket name is also the key namespace used by
# SlidingWindowLimiter (f"{bucket}:{client_ip}"), so "login" being shared by
# both GET and POST /login is what gives them one combined budget.
RATE_LIMIT_RULES: Dict[str, RateLimitRule] = {
    "register": RateLimitRule(limit=10, window_seconds=3600),
    "login": RateLimitRule(limit=30, window_seconds=300),
    "token": RateLimitRule(limit=60, window_seconds=3600),
}

# Path -> bucket. Deliberately does NOT include /mcp, /authorize, /revoke, or
# any /.well-known/* route -- see this module's docstring "Scope" section.
_PATH_BUCKETS: Dict[str, str] = {
    "/register": "register",
    "/login": "login",
    "/token": "token",
}


def _bucket_for_path(path: str) -> Optional[str]:
    return _PATH_BUCKETS.get(path)


class SlidingWindowLimiter:
    """Plain in-process, in-memory sliding-window request counter.

    Synchronous and lock-free by design -- see this module's docstring
    "Algorithm" section for why that's safe under asyncio's single-threaded
    event loop. Not persisted across restarts.
    """

    def __init__(self) -> None:
        self._windows: Dict[str, Deque[float]] = {}

    def check(
        self,
        bucket: str,
        client_ip: str,
        rule: RateLimitRule,
        now: Optional[float] = None,
    ) -> tuple[bool, float]:
        """Prunes expired hits, checks capacity, and -- if allowed -- records
        this hit. Returns ``(allowed, retry_after_seconds)``; ``retry_after``
        is ``0.0`` when ``allowed`` is ``True``.
        """
        now = now if now is not None else time.time()
        key = f"{bucket}:{client_ip}"
        window = self._windows.setdefault(key, deque())

        cutoff = now - rule.window_seconds
        while window and window[0] < cutoff:
            window.popleft()

        if len(window) >= rule.limit:
            retry_after = (window[0] + rule.window_seconds) - now
            return False, max(retry_after, 0.0)

        window.append(now)
        return True, 0.0


def rate_limit_asgi_middleware(app, limiter: Optional[SlidingWindowLimiter] = None, rules: Optional[Dict[str, RateLimitRule]] = None):  # noqa: ANN001 - raw ASGI app
    """Wraps a Starlette/ASGI ``app`` with the per-IP sliding-window limits
    described in this module's docstring.

    ``limiter``/``rules`` are injectable specifically so tests can supply
    tiny limits/windows instead of waiting on real wall-clock windows, and so
    a fresh ``SlidingWindowLimiter`` (independent budgets) can be constructed
    per test. Passes non-``http`` scopes (``lifespan``, etc.) straight
    through untouched, matching ``_bearer_auth_asgi_middleware``'s contract
    in ``investyo_mcp_server.py``.
    """
    limiter = limiter if limiter is not None else SlidingWindowLimiter()
    rules = rules if rules is not None else RATE_LIMIT_RULES

    async def middleware(scope, receive, send):  # noqa: ANN001 - raw ASGI signature
        if scope["type"] != "http":
            await app(scope, receive, send)
            return

        path = scope.get("path", "")
        bucket = _bucket_for_path(path)
        rule = rules.get(bucket) if bucket else None
        if rule is None:
            await app(scope, receive, send)
            return

        client_ip = _resolve_client_ip(scope)
        allowed, retry_after = limiter.check(bucket, client_ip, rule)
        if not allowed:
            retry_after_seconds = max(1, math.ceil(retry_after))
            body = json.dumps(
                {
                    "error": "rate_limited",
                    "message": "Too many requests. Please slow down and try again later.",
                    "retry_after": retry_after_seconds,
                }
            ).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 429,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"retry-after", str(retry_after_seconds).encode("latin-1")),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        await app(scope, receive, send)

    return middleware
