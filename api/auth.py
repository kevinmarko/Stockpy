"""
api/auth.py
===========
Centralized authentication and token verification middleware across Stockpy APIs.
Enforces constant-time bearer token validation and fail-closed posture for non-loopback bindings.
"""

from __future__ import annotations

import hmac
import logging
from typing import Optional

from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from settings import settings

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)


def _is_loopback(request: Request) -> bool:
    """Return True if the request client is on loopback (127.0.0.1 or ::1)."""
    client_host = request.client.host if request.client else ""
    return client_host in ("127.0.0.1", "::1", "localhost")


def require_read_token(
    credentials: Optional[HTTPAuthorizationCredentials] = None,
    request: Optional[Request] = None,
) -> None:
    """Validate bearer token for read operations.

    Fail-open for loopback requests when STATE_API_TOKEN is unset.
    Fail-closed (503) for non-loopback requests when STATE_API_TOKEN is unset.
    """
    token = settings.STATE_API_TOKEN
    is_loop = _is_loopback(request) if request else True

    if not token or not token.strip():
        if not is_loop:
            raise HTTPException(
                status_code=503,
                detail="STATE_API_TOKEN is unset. Non-loopback requests require an explicit token."
            )
        return  # Fail-open for loopback local dev

    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Missing authorization header.")

    if not hmac.compare_digest(credentials.credentials.strip(), token.strip()):
        raise HTTPException(status_code=401, detail="Invalid token.")


def require_command_token(
    credentials: Optional[HTTPAuthorizationCredentials] = None,
) -> None:
    """Validate bearer token for command/write operations. Always FAIL-CLOSED."""
    token = settings.ORCHESTRATOR_DAEMON_TOKEN or settings.FOLLOW_API_TOKEN
    if not token or not token.strip():
        raise HTTPException(
            status_code=403,
            detail="Command token is unset. Endpoint is disabled."
        )

    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Missing authorization header.")

    if not hmac.compare_digest(credentials.credentials.strip(), token.strip()):
        raise HTTPException(status_code=401, detail="Invalid token.")
