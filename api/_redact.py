"""
api/_redact.py
==============
Log scrubbing utility to ensure tracebacks, log lines, and config dumps never
leak sensitive credential material or tokens to the web UI or SSE streams.
"""

from __future__ import annotations

import re
from typing import List

from shared.env_io import SECRET_KEYS
from settings import settings

# Generic patterns for API keys, Bearer tokens, secrets, and auth headers
_GENERIC_PATTERNS = [
    re.compile(r"(?i)(bearer\s+)[a-zA-Z0-9_\-\.]{8,}"),
    re.compile(r"(?i)(api[_-]?key|secret|token|password|auth|mfa)[:=]\s*['\"]?([a-zA-Z0-9_\-\.]{8,})['\"]?"),
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),
]


def _get_active_secret_values() -> List[str]:
    """Retrieve actual secret string values currently configured.

    Reads through ``settings.<KEY>``, never ``os.environ`` directly.
    pydantic-settings loads ``.env`` into the ``Settings`` singleton but does
    NOT also copy it into the real process ``os.environ`` — the same gap this
    codebase already hit once in ``signals/news_catalyst.py::build_finnhub_client``
    (see CLAUDE.md). Reading ``os.environ`` here would leave every secret that
    lives only in ``.env`` (the normal, documented setup) invisible to this
    filter's direct-value match — exactly the case a log-redaction filter
    exists to cover.
    """
    secret_vals = []
    for k in SECRET_KEYS:
        val = getattr(settings, k, None)
        if val and isinstance(val, str) and len(val.strip()) >= 4:
            secret_vals.append(val.strip())
    return secret_vals


def redact_line(line: str) -> str:
    """Scrub sensitive credentials and pattern matches from a log line."""
    if not line:
        return line

    redacted = line

    # 1. Direct replacement of active secret values from environment
    for secret in _get_active_secret_values():
        if secret in redacted:
            redacted = redacted.replace(secret, "••••[REDACTED]••••")

    # 2. Pattern-based redaction for formatted headers / key-value logs
    for pattern in _GENERIC_PATTERNS:
        def _repl(match: re.Match) -> str:
            if len(match.groups()) == 1:
                prefix = match.group(1)
                return f"{prefix}••••[REDACTED]••••"
            elif len(match.groups()) == 2:
                prefix = match.group(1)
                return f"{prefix}=••••[REDACTED]••••"
            return "••••[REDACTED]••••"
        redacted = pattern.sub(_repl, redacted)

    return redacted


def install_redacting_exception_handler(app) -> None:
    """Register a global ``HTTPException`` handler that redacts ``detail``.

    Individual endpoints that raise ``HTTPException(detail=redact_line(str(exc)))``
    remain the primary, explicit fix — but that pattern relies on every future
    author remembering to wrap ``str(exc)`` at every new call site. This handler
    is the structural backstop: it redacts every response's ``detail`` (string
    or the ``{"error": ..., "message": ...}``-shaped dicts used throughout this
    codebase) right before serialization, so a future endpoint that raises
    ``HTTPException(detail=str(exc))`` directly is covered automatically.
    Idempotent against detail values already scrubbed by an explicit
    ``redact_line()`` call site — redacting an already-redacted string is a
    no-op.
    """
    from fastapi import HTTPException
    from fastapi.responses import JSONResponse

    @app.exception_handler(HTTPException)
    async def _redact_http_exception(request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, str):
            detail = redact_line(detail)
        elif isinstance(detail, dict):
            detail = {
                k: (redact_line(v) if isinstance(v, str) else v)
                for k, v in detail.items()
            }
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": detail},
            headers=getattr(exc, "headers", None),
        )
