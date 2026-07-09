"""
gui/env_io.py
=============
Safe, allowlist-bounded read/write layer for the project ``.env`` file, used by
the Command Center's **Settings Manager** and **Strategy Matrix** tabs.

Why a dedicated module
----------------------
The GUI lets an operator tune non-secret runtime parameters (risk-free rate,
Kelly fraction, default tickers, signal weights, disabled modules, …) without
hand-editing ``.env``.  Doing this safely requires three guarantees that this
module centralizes and enforces:

1.  **Secrets are never written and never echoed in cleartext.**  Keys in
    :data:`SECRET_KEYS` (API keys, passwords, TOTP secrets, webhooks) are
    read-only from the GUI's perspective: :func:`read_settings` returns a masked
    placeholder (``"•••• set"`` / ``"(unset)"``) for them, and
    :func:`write_setting` raises :class:`SecretWriteError` if asked to modify one
    (CONSTRAINT #3).

2.  **Only known tunables are writable.**  :func:`write_setting` rejects any key
    not in :data:`ALLOWED_KEYS`, so a GUI bug or a crafted form value cannot
    inject arbitrary keys into ``.env``.

3.  **Values are serialized exactly as pydantic-settings expects.**  List/dict
    fields (``DEFAULT_TICKERS``, ``SIGNAL_WEIGHTS``, ``DISABLED_SIGNAL_MODULES``)
    are JSON-encoded so ``settings.Settings()`` re-parses them on the next
    launch; scalars are written verbatim.

The module uses ``python-dotenv`` (already a dependency) — ``dotenv_values`` for
reading and ``set_key`` for writing — so existing comments and unrelated keys in
``.env`` are preserved across edits.

Persistence model
------------------
Writes land in ``.env`` and therefore take effect on the **next** orchestrator /
GUI launch (``Settings()`` reads ``.env`` once at process start; there is no
hot-reload).  The Settings tab makes this explicit to the operator.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from dotenv import dotenv_values, set_key

logger = logging.getLogger(__name__)

# Repo root = parent of the gui/ package directory.
_REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = _REPO_ROOT / ".env"

# ---------------------------------------------------------------------------
# Key classification
# ---------------------------------------------------------------------------
# NON-secret tunables the GUI may write. Each maps to a pydantic Settings field.
# Keep this list aligned with settings.py; anything not here is rejected.
ALLOWED_KEYS: tuple[str, ...] = (
    # Financial constants
    "RISK_FREE_RATE",
    "MARKET_RISK_PREMIUM",
    "REQUIRED_RETURN_RATE",
    "MAX_PORTFOLIO_HEAT",
    # Position sizing
    "KELLY_FRACTION",
    "KELLY_CAP",
    "VOL_TARGET",
    "MAX_LEVERAGE",
    "MAX_POSITION_WEIGHT",
    # Risk gate
    "MAX_CORRELATION",
    "DAILY_LOSS_LIMIT_PCT",
    "MAX_ORDER_RATE_PER_MIN",
    "HMM_RISK_OFF_BLOCK_THRESHOLD",
    "RISK_GATE_ENFORCE_MARKET_HOURS",
    "MACRO_REGIME_GATE_ENABLED",
    # Meta-labeling
    "META_LABEL_MIN_CONFIDENCE",
    # Observability / runtime
    "DASHBOARD_REFRESH_SECONDS",
    "LOG_LEVEL",
    "DRY_RUN",
    # Persistent orchestrator daemon cutover flag. Non-secret (no credential
    # material); the command token that actually guards the daemon's
    # POST /run is ORCHESTRATOR_DAEMON_TOKEN, which stays in SECRET_KEYS.
    "ORCHESTRATOR_DAEMON_ENABLED",
    # Execution mode toggle — paper sandbox vs. live endpoint. Writeable from
    # the Strategy Matrix tab's global Simulation/Paper/Live selector. Never a
    # secret: the broker keys themselves are SECRET_KEYS.
    "ALPACA_PAPER",
    "MARKET_DATA_PROVIDER",
    "MARKET_DATA_QUOTE_TTL_SECONDS",
    # Forecasting / fundamentals tunables (non-secret; see forecasting_engine.py
    # + data/market_data.py). FINNHUB_API_KEY stays in SECRET_KEYS below.
    "FORECAST_USE_GARCH_SIGMA",   # bool — GJR-GARCH sigma into Monte Carlo (rollback lever)
    "FORECAST_PROPHET_WEIGHT",    # float [0,1] — Prophet ensemble overlay weight
    "FORECAST_SKILL_WEIGHTING_ENABLED",  # bool — opt-in inverse-RMSE skill-weighted blend
    "FORECAST_SKILL_WINDOW_DAYS", # int — rolling RMSE window (days) for skill weighting
    "FUNDAMENTALS_SOURCE",        # "yahoo" | "yfinance_info"
    "BETA_LOOKBACK_DAYS",         # int — beta computation lookback (days)
    # Universe / signals (JSON-encoded)
    "DEFAULT_TICKERS",
    "SIGNAL_WEIGHTS",
    "DISABLED_SIGNAL_MODULES",
    # Sector->model/horizon forecast config (JSON-encoded; see _JSON_KEYS).
    # GUI-writable. Empty dict/default path preserves today's hardcoded
    # per-sector forecast heuristic (backward-compatible).
    "SECTOR_FORECAST_CONFIG_PATH",
    "SECTOR_FORECAST_CONFIGS",
    # State API CORS policy — non-secret list of allowed browser origins
    # (JSON-encoded; see _JSON_KEYS). GUI-writable.
    "CORS_ALLOWED_ORIGINS",
    # Prompt Registry tunables (non-secret; credentials live in SECRET_KEYS below).
    # See docs/PROMPT_REGISTRY_PLAN.md §8 and settings.PROMPT_REGISTRY_*.
    "PROMPT_REGISTRY_ENABLED",   # bool master switch (baseline-only when False)
    "PROMPT_REGISTRY_BACKEND",   # "http" | "local" | "firestore"
    "PROMPT_REGISTRY_PINS",      # JSON dict {"prompt_id": "version"} — rollback lever
    # Tier 9 — Claude + Gemini commentary toggles (non-secret).  Credentials
    # (ANTHROPIC_API_KEY / GEMINI_API_KEY) live in SECRET_KEYS below per
    # CONSTRAINT #3 — they are NEVER GUI-writable.
    "LLM_COMMENTARY_ENABLED",            # bool master switch (default False)
    "LLM_COMMENTARY_RATIONALE_PROVIDER", # "claude" | "none"
    "LLM_COMMENTARY_ALERT_PROVIDER",     # "gemini" | "none"
    # AI Control Center toggles (non-secret).  These master switches were
    # previously settable only by hand-editing .env; the Control Center tab
    # surfaces them.  Provider credentials (ANTHROPIC/GEMINI/OPENAI keys) stay
    # in SECRET_KEYS below — CONSTRAINT #3, never GUI-writable.
    "GRAVITY_AI_RUNNER_ENABLED",         # bool — Gravity AI runner (Claude+Gemini)
    "OPAL_RESEARCH_ENABLED",             # bool — Opal research agent (OpenAI or Gemini)
    "OPAL_RESEARCH_PROVIDER",            # "openai" | "gemini" | "none"
    "OPAL_RESEARCH_MODEL",               # e.g. "gpt-4o" or "gemini-2.5-flash"
)

# Keys whose VALUES must never be returned in cleartext nor written by the GUI.
# These are credentials / webhooks; they remain editable only by hand-editing
# .env outside the app (CONSTRAINT #3).
SECRET_KEYS: tuple[str, ...] = (
    "FRED_API_KEY",
    "ALPACA_API_KEY",
    "ALPACA_SECRET_KEY",
    "FINNHUB_API_KEY",
    "ROBINHOOD_USERNAME",
    "ROBINHOOD_PASSWORD",
    "RH_USERNAME",
    "RH_PASSWORD",
    "RH_MFA_SECRET",
    "ALERT_WEBHOOK_URL",
    # Bearer token for the read-only State API (api/state_api.py). Treated like a
    # webhook/token secret — masked, never GUI-writable (CONSTRAINT #3).
    "STATE_API_TOKEN",
    # Bearer token guarding POST /run on the orchestrator Control API
    # (api/control_api.py). Same secret treatment as STATE_API_TOKEN.
    "ORCHESTRATOR_DAEMON_TOKEN",
    "DISCORD_WEBHOOK_URL",
    "SLACK_WEBHOOK_URL",
    # ntfy.sh push topic (alerting.notify(), also used by the Tier 8 Robinhood
    # execution-queue notifier in execution/queue_builder.py). Functions like a
    # bearer token: anyone who knows the topic name can publish to or read it —
    # alerting.py's own docstring says to "keep the topic unguessable" — so it
    # is classified alongside the webhook URLs, never GUI-writable.
    "NTFY_TOPIC",
    "ALERT_EMAIL_FROM",
    "ALERT_EMAIL_TO",
    "ALERT_SMTP_HOST",
    "ALERT_SMTP_USER",
    "ALERT_SMTP_PASSWORD",
    # Prompt Registry credentials — 4 separate roles (read / publish / sign / url).
    # Never GUI-writable; edit .env by hand only (CONSTRAINT #3).
    "PROMPT_REGISTRY_URL",           # protected HTTPS manifest endpoint
    "PROMPT_REGISTRY_TOKEN",         # bearer read-token
    "PROMPT_REGISTRY_PUBLISH_TOKEN", # higher-privilege publish credential
    "PROMPT_REGISTRY_SIGNING_KEY",   # HMAC-SHA256 verification key
    # Tier 9 — Claude + Gemini commentary credentials.  CONSTRAINT #3 — these
    # are NEVER GUI-writable; hand-edit .env to set / rotate them.
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    # OpenAI credential for Opal, the research agent (Tier 9 Scope 4,
    # llm/research.py).  CONSTRAINT #3 — never GUI-writable; hand-edit .env.
    "OPENAI_API_KEY",
)

# Keys whose values are JSON-encoded structures (lists/dicts) in .env.
_JSON_KEYS: frozenset[str] = frozenset(
    {
        "DEFAULT_TICKERS",
        "SIGNAL_WEIGHTS",
        "DISABLED_SIGNAL_MODULES",
        "SECTOR_FORECAST_CONFIGS",  # dict[str, dict] per-sector forecast overrides
        "CORS_ALLOWED_ORIGINS",  # list[str] of allowed browser origins
        "PROMPT_REGISTRY_PINS",  # dict[str, str] {"prompt_id": "version"}
    }
)

_MASK_SET = "•••• set"
_MASK_UNSET = "(unset)"


class SecretWriteError(RuntimeError):
    """Raised when the GUI attempts to write a key classified as a secret."""


class DisallowedKeyError(RuntimeError):
    """Raised when the GUI attempts to write a key outside :data:`ALLOWED_KEYS`."""


def _raw_env() -> Dict[str, Optional[str]]:
    """Return the raw ``.env`` key→value mapping (empty dict if no file)."""
    if not ENV_PATH.exists():
        return {}
    try:
        return dict(dotenv_values(ENV_PATH))
    except Exception as exc:  # pragma: no cover - dotenv parse failure is rare
        logger.warning("Failed to parse %s: %s", ENV_PATH, exc)
        return {}


def mask_secret(value: Optional[str]) -> str:
    """Return a masked placeholder for a secret value (never the cleartext)."""
    return _MASK_SET if value else _MASK_UNSET


def read_settings() -> Dict[str, str]:
    """Read displayable settings from ``.env``.

    Secret keys are masked; allowlisted (non-secret) keys are returned verbatim.
    Keys present in ``.env`` but in neither list are returned verbatim too, so
    the operator can still see them — but :func:`write_setting` will refuse to
    edit anything outside :data:`ALLOWED_KEYS`.

    Returns
    -------
    dict[str, str]
        Mapping of env key → display string.  Always safe to render in the GUI:
        no secret cleartext is ever included.
    """
    raw = _raw_env()
    display: Dict[str, str] = {}
    for key, value in raw.items():
        if key in SECRET_KEYS:
            display[key] = mask_secret(value)
        else:
            display[key] = "" if value is None else str(value)
    return display


def get_value(key: str, default: str = "") -> str:
    """Return the cleartext value of a NON-secret allowlisted key from ``.env``.

    Raises
    ------
    SecretWriteError
        If ``key`` is a secret — secret cleartext must never leave this module.
    """
    if key in SECRET_KEYS:
        raise SecretWriteError(
            f"Refusing to return cleartext for secret key '{key}'."
        )
    raw = _raw_env()
    value = raw.get(key)
    return default if value is None else str(value)


def is_secret(key: str) -> bool:
    """True if ``key`` is classified as a secret (masked, never GUI-writable)."""
    return key in SECRET_KEYS


def _encode_value(key: str, value: Any) -> str:
    """Serialize a Python value to its ``.env`` string form for ``key``.

    JSON keys (lists/dicts) are ``json.dumps``-encoded so pydantic-settings
    re-parses them; booleans become lowercase ``true``/``false``; everything
    else is ``str()``-coerced.
    """
    if key in _JSON_KEYS:
        return json.dumps(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def write_setting(key: str, value: Any) -> str:
    """Write a single NON-secret tunable to ``.env`` (preserving other lines).

    Parameters
    ----------
    key:
        Must be in :data:`ALLOWED_KEYS`; must NOT be in :data:`SECRET_KEYS`.
    value:
        Python value.  JSON keys accept list/dict; scalars accept str/number/bool.

    Returns
    -------
    str
        The encoded string actually written to ``.env`` (handy for confirmation
        messages / tests).

    Raises
    ------
    SecretWriteError
        If ``key`` is a secret.
    DisallowedKeyError
        If ``key`` is not in the allowlist.
    """
    if key in SECRET_KEYS:
        raise SecretWriteError(
            f"Refusing to write secret key '{key}' from the GUI. "
            "Edit secrets directly in .env (CONSTRAINT #3)."
        )
    if key not in ALLOWED_KEYS:
        raise DisallowedKeyError(
            f"Key '{key}' is not in the GUI-writable allowlist (ALLOWED_KEYS)."
        )

    encoded = _encode_value(key, value)
    # Ensure the file exists so set_key can operate on it.
    ENV_PATH.touch(exist_ok=True)
    # quote_mode="auto" keeps simple scalars unquoted and quotes JSON/space values.
    set_key(str(ENV_PATH), key, encoded, quote_mode="auto")
    logger.info("Wrote .env setting %s (value length=%d).", key, len(encoded))
    return encoded


def write_many(updates: Dict[str, Any]) -> List[str]:
    """Write multiple allowlisted settings; returns the keys successfully written.

    Each entry is validated independently by :func:`write_setting`; a single bad
    key raises before any subsequent writes, so callers should pre-validate with
    :func:`is_secret` / membership in :data:`ALLOWED_KEYS` if partial writes are
    undesirable.  This dead-letter-free behavior is intentional: settings writes
    are cheap to retry and we prefer a loud failure over silent partial state.
    """
    written: List[str] = []
    for key, value in updates.items():
        write_setting(key, value)
        written.append(key)
    return written


def allowlisted_keys() -> Iterable[str]:
    """Return the GUI-writable keys (stable order) for rendering the form."""
    return ALLOWED_KEYS
