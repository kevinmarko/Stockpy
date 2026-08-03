"""LOCAL, SINGLE-OPERATOR ONLY hard-scoped .env writer for Robinhood portfolio-snapshot credentials (RH_USERNAME / RH_PASSWORD). Used only by the Pilots API brokerage-connect flow, never by the GUI. Writes and clears exactly those two keys (in both .env and os.environ) and reports presence, never returning or logging the credential values themselves. Robinhood login is device-approval push (the operator taps "approve" in the Robinhood app), so there is no third MFA/TOTP-secret key for this module to touch or protect in the first place — the hard two-key allowlist below is enforced unconditionally, not as a carve-out around some other key."""

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
#   - Robinhood login is device-approval push: the operator approves a login
#     attempt by tapping "approve" in the Robinhood app on their phone, not by
#     typing a one-time code, so there is no MFA/TOTP-secret settings key of
#     any kind in this codebase for this module to avoid touching. The actual
#     verification step (did this username/password/device-approval attempt
#     succeed) happens via the killable-subprocess login-job flow
#     (data/robinhood_login.py, api/_rh_login.py) BEFORE this module is ever
#     called — see write_rh_credentials's own docstring below.
#   - Local single-operator model: one .env file on one machine, exactly the
#     model the rest of this codebase already assumes (see AGENTS.md).  This
#     is NOT a multi-user encrypted vault.
#   - Credential VALUES are never logged, never returned by any function here,
#     and never echoed back to a caller (CONSTRAINT #3).
#   - Callers MUST have already confirmed the login attempt succeeded (see
#     api/_rh_login.py::start_connect_job, which only calls
#     write_rh_credentials once a device-approval login job reaches
#     state="succeeded") BEFORE calling write_rh_credentials — this module
#     does not verify anything itself, it only persists.
# =============================================================================

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import set_key, unset_key

from settings import ENV_PATH, settings as _settings

logger = logging.getLogger(__name__)

# Repo root = parent of the data/ package directory (mirrors gui/env_io.py).
# ENV_PATH itself now lives in settings.py — see that module's comment for
# why every `.env` locator in the codebase must import it rather than
# re-deriving its own copy.
_REPO_ROOT = Path(__file__).resolve().parent.parent

# Hard allowlist — the ONLY keys this module will ever write or clear. There
# is no third settings key to deliberately exclude here: device-approval
# login has no MFA/TOTP secret at all — see module header comment above.
_RH_CREDENTIAL_KEYS: tuple[str, ...] = ("RH_USERNAME", "RH_PASSWORD")


def rh_credentials_present() -> bool:
    """True if RH_USERNAME and RH_PASSWORD are both set on the live settings
    singleton. Never returns or logs the credential values themselves.

    Reads via ``settings.settings.X`` rather than ``os.environ`` — pydantic-
    settings loads ``.env`` into the Settings model only, it never copies
    values into the real process environment, so an ``os.environ`` read here
    would report "absent" for any credential that only ever came from
    ``.env`` (the exact class of bug documented on
    ``data.robinhood_portfolio._require_setting``).
    """
    return bool(
        (_settings.RH_USERNAME or "").strip()
        and (_settings.RH_PASSWORD or "").strip()
    )


def write_rh_credentials(username: str, password: str) -> None:
    """Persist Robinhood portfolio-snapshot credentials to ``.env`` and mirror
    them into both the live process ``os.environ`` and the live ``settings``
    singleton, so the current process picks them up immediately (no restart
    required for subsequent ``fetch_account_snapshot()`` calls in this same
    process). The settings-singleton assignment is the one that actually
    matters for in-process reads — every RH credential consumer reads via
    ``settings.settings.X``, not ``os.environ`` (see ``rh_credentials_present``
    above); the ``os.environ`` mirror is kept for any external tooling that
    still shells out and expects it.

    Callers MUST have already confirmed the login attempt succeeded — in
    practice, ``api/_rh_login.py::start_connect_job``'s background watcher
    thread, which only calls this function once a device-approval login job
    (``data/robinhood_login.py``) reaches ``state="succeeded"``, i.e. the
    operator actually tapped "approve" in the Robinhood app for this exact
    username/password. This function performs no verification of its own,
    only persistence of username/password. There is no third MFA/TOTP-secret
    settings key for this function to avoid touching — device-approval login
    doesn't have one (see module header comment).

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
            setattr(_settings, key, value)
        else:
            unset_key(str(ENV_PATH), key)
            os.environ.pop(key, None)
            setattr(_settings, key, None)

    logger.info(
        "Wrote Robinhood brokerage credentials to .env (keys=%s; values never logged).",
        [k for k in _RH_CREDENTIAL_KEYS if getattr(_settings, k, None)],
    )


def clear_rh_credentials() -> None:
    """Remove RH_USERNAME/RH_PASSWORD from ``.env``, the live process
    ``os.environ``, and the live ``settings`` singleton. Idempotent — safe to
    call when nothing is set. Touches no other key — the hard two-key
    allowlist above is the only thing this function ever writes or clears
    (see module header comment)."""
    if ENV_PATH.exists():
        for key in _RH_CREDENTIAL_KEYS:
            unset_key(str(ENV_PATH), key)
    for key in _RH_CREDENTIAL_KEYS:
        os.environ.pop(key, None)
        setattr(_settings, key, None)
    logger.info("Cleared Robinhood brokerage credentials from .env and process environment.")
