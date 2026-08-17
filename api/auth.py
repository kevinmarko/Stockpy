"""
api/auth.py
===========
Shared bearer-token auth dependencies for every standalone service in
``api/*.py``. Single source of truth for the ``hmac.compare_digest`` /
fail-open / fail-closed logic that used to be hand-copied into
``api/state_api.py``, ``api/data_api.py``, ``api/metrics_api.py``,
``api/pilots_api.py``, and ``api/control_api.py`` independently.

Each command-scoped guard is bound to exactly ONE settings field. A token
minted for one write surface (e.g. ``FOLLOW_API_TOKEN`` for the Pilots API's
follow endpoints) must never also unlock an unrelated one (e.g.
``ORCHESTRATOR_DAEMON_TOKEN``'s daemon Control API) just because both went
through this shared module — see :func:`make_command_token_guard`.
"""

from __future__ import annotations

import hmac
import logging
from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from settings import settings

logger = logging.getLogger(__name__)

# The single HTTPBearer scheme every api/*.py service depends on. Every guard
# below binds `credentials` via Depends(bearer_scheme) — a bare `= None`
# default (with no Depends/Security) is never populated by FastAPI at all,
# which would make every check below reject a request no matter what
# Authorization header it actually carried.
bearer_scheme = HTTPBearer(auto_error=False)

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def is_loopback_host(host: Optional[str]) -> bool:
    """True if *host* is a loopback address, or ``None`` (some ASGI
    transports don't expose a client address at all — treated as loopback so
    today's zero-config local/test behavior is unaffected; every fail-closed
    branch built on this only ever tightens things for a REAL non-loopback
    client). The shared definition behind :func:`_is_loopback` (HTTP
    ``Request``) AND ``api/ws_api.py``'s WebSocket auth gate
    (``_check_ws_token``) — a WebSocket upgrade has no ``Request`` object,
    only ``WebSocket.client.host``, so it can't call ``_is_loopback``
    directly; this is the piece both protocols share instead, so the
    definition of "loopback" can't drift between them."""
    return host is None or host in _LOOPBACK_HOSTS


def _is_loopback(request: Request) -> bool:
    """True if the request's client host is loopback. ``request.client`` can
    be ``None`` under some ASGI transports — see :func:`is_loopback_host`."""
    return is_loopback_host(request.client.host if request.client else None)


def require_read_token(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> None:
    """Read-endpoint guard shared by every api/*.py service.

    FAIL-OPEN when STATE_API_TOKEN is unset AND the request is loopback
    (zero-config local use — today's exact behavior). FAIL-CLOSED (503) when
    STATE_API_TOKEN is unset and the request arrives on a non-loopback
    interface (LAN/Tailscale) — an unset token must never mean "open" once
    the API is reachable from outside this machine. When a token IS
    configured, every request (loopback or not) must present a matching
    bearer token. Constant-time compare (never ==); token never logged
    (CONSTRAINT #3)."""
    token = settings.STATE_API_TOKEN
    if not token:
        if not _is_loopback(request):
            raise HTTPException(
                status_code=503,
                detail=(
                    "STATE_API_TOKEN is unset — refusing a non-loopback request. "
                    "Set STATE_API_TOKEN before exposing this API beyond localhost."
                ),
            )
        return
    presented = credentials.credentials if credentials else ""
    if not hmac.compare_digest(presented, token):
        raise HTTPException(status_code=401, detail="Invalid or missing bearer token")


def require_stream_token(
    request: Request,
    token: Optional[str] = None,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> None:
    """Like require_read_token, but also accepts the token as a ``?token=``
    query parameter. The browser's native ``EventSource`` API (used for SSE
    log streaming) cannot set an ``Authorization`` header at all — there is
    no headers option in that API — so an SSE endpoint that only checked the
    header would be unreachable from a real browser the moment a token is
    configured. Same fail-open-on-loopback / fail-closed-otherwise posture as
    require_read_token when unset."""
    st_token = settings.STATE_API_TOKEN
    if not st_token:
        if not _is_loopback(request):
            raise HTTPException(
                status_code=503,
                detail=(
                    "STATE_API_TOKEN is unset — refusing a non-loopback request. "
                    "Set STATE_API_TOKEN before exposing this API beyond localhost."
                ),
            )
        return
    presented = (credentials.credentials if credentials else "") or (token or "")
    if not hmac.compare_digest(presented, st_token):
        raise HTTPException(status_code=401, detail="Invalid or missing bearer token")


def require_write_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> None:
    """FAIL-CLOSED variant of the STATE_API_TOKEN check, for write/compute
    endpoints that must never be reachable with no token configured at all —
    unlike require_read_token, which fails OPEN on the same setting for
    zero-config local reads. Use this for any api/*.py endpoint that mutates
    state or triggers real compute, when the service has no dedicated
    command-token setting of its own (contrast api/control_api.py's
    ORCHESTRATOR_DAEMON_TOKEN / api/pilots_api.py's FOLLOW_API_TOKEN, which
    use make_command_token_guard instead)."""
    token = settings.STATE_API_TOKEN
    if not token:
        raise HTTPException(
            status_code=403,
            detail="Write endpoint disabled: STATE_API_TOKEN not configured.",
        )
    presented = credentials.credentials if credentials else ""
    if not hmac.compare_digest(presented, token):
        raise HTTPException(status_code=401, detail="Invalid or missing bearer token")


def make_command_token_guard(token_setting_name: str, disabled_detail: str):
    """Build a FAIL-CLOSED command-token dependency bound to one named
    settings field (e.g. "ORCHESTRATOR_DAEMON_TOKEN", "FOLLOW_API_TOKEN").
    Deliberately NOT a single generic "any command token will do" check —
    each caller binds its own dedicated setting so scopes never bleed into
    each other."""

    def _guard(
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    ) -> None:
        token = getattr(settings, token_setting_name, None)
        if not token:
            raise HTTPException(status_code=403, detail=disabled_detail)
        presented = credentials.credentials if credentials else ""
        if not hmac.compare_digest(presented, token):
            raise HTTPException(status_code=401, detail="Invalid or missing bearer token")

    _guard.__name__ = f"require_{token_setting_name.lower()}_guard"
    return _guard


# api/control_api.py's command surface (POST /run, /daemon/restart, /jobs*).
require_orchestrator_command_token = make_command_token_guard(
    "ORCHESTRATOR_DAEMON_TOKEN",
    "Command endpoint disabled: ORCHESTRATOR_DAEMON_TOKEN not configured.",
)

# api/pilots_api.py's fail-closed command surface. Originally bound only to
# the follow write-path (a follow produces a gated order queue), it has since
# been reused as the auth tier for ~20 other command endpoints in that file
# (forecast backfill trigger, /automation/run|pause, /decisions, brokerage
# connect, etc. -- see api/pilots_api.py's module docstring). disabled_detail
# is deliberately endpoint-agnostic ("Command endpoint", not "Follow
# endpoints") so it doesn't mislead operators hitting it from any of those
# other, unrelated endpoints.
require_follow_command_token = make_command_token_guard(
    "FOLLOW_API_TOKEN",
    "Command endpoint disabled: FOLLOW_API_TOKEN not configured.",
)
