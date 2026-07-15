# =============================================================================
# MODULE: BROKERAGE CREDENTIAL WRITER  (LOCAL, SINGLE-OPERATOR ONLY)
# File: data/brokerage_credentials.py
#
# Dedicated, hard-bounded ``.env`` writer for Robinhood portfolio-snapshot
# credentials (RH_USERNAME / RH_PASSWORD / RH_MFA_SECRET). This exists
# SEPARATELY from gui/env_io.py because that module's whole purpose is to
# refuse to write anything in SECRET_KEYS (CONSTRAINT #3) — this module is
# the one deliberate, narrowly-scoped exception, used ONLY by the Pilots API's
# brokerage-connect intake endpoint
# (api/pilots_api.py::POST /brokerage/connect), which is itself gated behind
# settings.BROKERAGE_CONNECT_ENABLED (default False), a fail-closed bearer
# token, and a loopback-only request check.
#
# Scope, deliberately narrow:
#   - Writes/clears ONLY {RH_USERNAME, RH_PASSWORD, RH_MFA_SECRET} — a hard
#     allowlist, not gui/env_io.py's ALLOWED_KEYS (which never contains these).
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
_RH_CREDENTIAL_KEYS: tuple[str, ...] = ("RH_USERNAME", "RH_PASSWORD", "RH_MFA_SECRET")


def rh_credentials_present() -> bool:
    """True if RH_USERNAME and RH_PASSWORD are both set in the live process
    environment. Never returns or logs the credential values themselves."""
    return bool(
        os.environ.get("RH_USERNAME", "").strip()
        and os.environ.get("RH_PASSWORD", "").strip()
    )


def write_rh_credentials(username: str, password: str, mfa_secret: str = "") -> None:
    """Persist Robinhood portfolio-snapshot credentials to ``.env`` and mirror
    them into the live process ``os.environ`` so the current process picks
    them up immediately (no restart required for subsequent
    ``fetch_account_snapshot()`` calls in this same process).

    Callers MUST have already verified these credentials via
    ``data.robinhood_portfolio.verify_credentials`` — this function performs
    no verification of its own, only persistence. ``mfa_secret=""`` clears
    that specific key (interactive MFA fallback resumes for the CLI/GUI path)
    while still writing username/password.

    Never logs credential values — only key names and lengths, matching the
    existing ``gui/env_io.write_setting`` convention.
    """
    username = (username or "").strip()
    password = (password or "").strip()
    mfa_secret = (mfa_secret or "").strip()

    ENV_PATH.touch(exist_ok=True)

    for key, value in (
        ("RH_USERNAME", username),
        ("RH_PASSWORD", password),
        ("RH_MFA_SECRET", mfa_secret),
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
    """Remove RH_USERNAME/RH_PASSWORD/RH_MFA_SECRET from both ``.env`` and the
    live process ``os.environ``. Idempotent — safe to call when nothing is set."""
    if ENV_PATH.exists():
        for key in _RH_CREDENTIAL_KEYS:
            unset_key(str(ENV_PATH), key)
    for key in _RH_CREDENTIAL_KEYS:
        os.environ.pop(key, None)
    logger.info("Cleared Robinhood brokerage credentials from .env and process environment.")
