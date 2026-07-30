"""
api/_redact.py
==============
Log scrubbing utility to ensure tracebacks, log lines, and config dumps never
leak sensitive credential material or tokens to the web UI or SSE streams.
"""

from __future__ import annotations

import os
import re
from typing import List

from gui.env_io import SECRET_KEYS, read_settings

# Generic patterns for API keys, Bearer tokens, secrets, and auth headers
_GENERIC_PATTERNS = [
    re.compile(r"(?i)(bearer\s+)[a-zA-Z0-9_\-\.]{8,}"),
    re.compile(r"(?i)(api[_-]?key|secret|token|password|auth|mfa)[:=]\s*['\"]?([a-zA-Z0-9_\-\.]{8,})['\"]?"),
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),
]


def _get_active_secret_values() -> List[str]:
    """Retrieve actual secret string values currently present in environment / .env."""
    secret_vals = []
    for k in SECRET_KEYS:
        val = os.environ.get(k)
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
