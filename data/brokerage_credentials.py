"""LOCAL, SINGLE-OPERATOR ONLY hard-scoped .env writer for Robinhood portfolio-snapshot credentials (RH_USERNAME / RH_PASSWORD). Used only by the Pilots API brokerage-connect flow, never by the GUI. Writes and clears exactly those two keys (in both .env and os.environ) and reports presence, never returning or logging the credential values themselves. Deliberately never touches RH_MFA_SECRET — that key belongs solely to the main pipeline's own operator-set .env credential and must never be written or cleared by this webapp-facing intake path."""

# =============================================================================
# MODULE: BROKERAGE CREDENTIAL WRITER  (LOCAL, SINGLE-OPERATOR ONLY)
# File: data/brokerage_credentials.py
#
# Dedicated, hard-bounded ``.env`` writer for Robinhood portfolio-snapshot
# credentials (RH_USERNAME / RH_PASSWORD). This exists SEPARATELY from
# gui/env_io.py because that module's whole purpose is to refuse to write
# anything in SECRET_KEYS (CONSTRAINT #3) — this module is the one
# deliberate, narrowly-scoped exception, used ONLY by the Pilots API's
# brokerage-connect intake endpoint
# (api/pilots_api.py::POST /brokerage/connect), which is itself gated behind
# settings.BROKERAGE_CONNECT_ENABLED (default False), a fail-closed bearer
# token, and a loopback-only request check.
#
# Scope, deliberately narrow:
#   - Writes/clears ONLY {RH_USERNAME, RH_PASSWORD} — a hard allowlist, not
#     gui/env_io.py's ALLOWED_KEYS (which never contains these).
#   - NEVER touches RH_MFA_SECRET. The connect flow verifies with a one-time
#     6-digit authenticator code (see data.robinhood_portfolio.verify_credentials)
#     that is never persisted; RH_MFA_SECRET remains exclusively an operator-set
#     .env value for the main pipeline's own unattended login (data/robinhood_portfolio.py's
#     _login()). This module must never write OR clear that key, so a webapp
#     reconnect can never silently wipe an operator's existing pipeline credential.
#   - Local single-operator model: one .env file on one machine, exactly the
#     model the rest of this codebase already assumes (see AGENTS.md).  This
#     is NOT a multi-user encrypted vault.
#   - Credential VALUES are never logged, never returned by any function here,
#     and never echoed back to a caller (CONSTRAINT #3).
#   - Callers MUST verify credentials (see
#     data.robinhood_portfolio.verify_credentials) BEFORE calling
#     write_rh_credentials — this module does not verify anything itself, it
#     only persists.
# =============================================================================

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import set_key, unset_key

logger = logging.getLogger(__name__)

# Repo root = parent of the data/ package directory (mirrors gui/env_io.py).
_REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = _REPO_ROOT / ".env"

# Hard allowlist — the ONLY keys this module will ever write or clear.
# RH_MFA_SECRET is deliberately excluded — see module header comment above.
_RH_CREDENTIAL_KEYS: tuple[str, ...] = ("RH_USERNAME", "RH_PASSWORD")


def rh_credentials_present() -> bool:
    """True if RH_USERNAME and RH_PASSWORD are both set in the live process
    environment. Never returns or logs the credential values themselves."""
    return bool(
        os.environ.get("RH_USERNAME", "").strip()
        and os.environ.get("RH_PASSWORD", "").strip()
    )


def write_rh_credentials(username: str, password: str) -> None:
    """Persist Robinhood portfolio-snapshot credentials to ``.env`` and mirror
    them into the live process ``os.environ`` so the current process picks
    them up immediately (no restart required for subsequent
    ``fetch_account_snapshot()`` calls in this same process).

    Callers MUST have already verified these credentials via
    ``data.robinhood_portfolio.verify_credentials`` (a one-time 6-digit
    authenticator code, never persisted) — this function performs no
    verification of its own, only persistence of username/password.
    Deliberately never touches RH_MFA_SECRET (see module header comment) —
    an operator-set value for the main pipeline's own login is never read,
    written, or cleared by this function.

    Never logs credential values — only key names and lengths, matching the
    existing ``gui/env_io.write_setting`` convention.
    """
    username = (username or "").strip()
    password = (password or "").strip()

    ENV_PATH.touch(exist_ok=True)

    for key, value in (
        ("RH_USERNAME", username),
        ("RH_PASSWORD", password),
    ):
        if value:
            set_key(str(ENV_PATH), key, value, quote_mode="auto")
            os.environ[key] = value
        else:
            unset_key(str(ENV_PATH), key)
            os.environ.pop(key, None)

    logger.info(
        "Wrote Robinhood brokerage credentials to .env (keys=%s; values never logged).",
        [k for k in _RH_CREDENTIAL_KEYS if os.environ.get(k)],
    )


def clear_rh_credentials() -> None:
    """Remove RH_USERNAME/RH_PASSWORD from both ``.env`` and the live process
    ``os.environ``. Idempotent — safe to call when nothing is set. Never
    touches RH_MFA_SECRET (see module header comment)."""
    if ENV_PATH.exists():
        for key in _RH_CREDENTIAL_KEYS:
            unset_key(str(ENV_PATH), key)
    for key in _RH_CREDENTIAL_KEYS:
        os.environ.pop(key, None)
    logger.info("Cleared Robinhood brokerage credentials from .env and process environment.")
