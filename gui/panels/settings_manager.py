"""InvestYo Command Center — Settings Manager tab. Edits the allowlisted non-secret tunables through gui.env_io (secrets are masked and never writable); changes take effect on the next launch."""

from __future__ import annotations

from __future__ import annotations
import io
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
import streamlit as st
from settings import settings
from gui import env_io, orchestrator_runner, help_widgets
from gui.symbol_search import filter_by_symbol
from gui.orchestrator_runner import StageStatus
from gui.panels._shared import (  # noqa: E402
    GICS_SECTORS,
    _BF_EDITOR_COLUMNS,
    _REPO_ROOT,
    _active_symbols,
    _held_symbols,
    _kill_switch,
    _signal_symbols,
    _watchlist_symbols,
    load_block_log,
    logger,
)
from gui.progress_ui import busy


# Render hints: (key, widget_kind). Unlisted allowlist keys default to text.
_SETTINGS_LAYOUT: List[tuple[str, str]] = [
    ("RISK_FREE_RATE", "number"),
    ("MARKET_RISK_PREMIUM", "number"),
    ("REQUIRED_RETURN_RATE", "number"),
    ("MAX_PORTFOLIO_HEAT", "number"),
    ("KELLY_FRACTION", "number"),
    ("KELLY_CAP", "number"),
    ("VOL_TARGET", "number"),
    ("MAX_LEVERAGE", "number"),
    ("MAX_POSITION_WEIGHT", "number"),
    ("MAX_PORTFOLIO_GROSS", "number"),
    ("SIZING_CAP_ESCALATION_ENABLED", "bool"),
    ("SIZING_CAP_ESCALATION_THRESHOLD_CYCLES", "int"),
    ("SIZING_CAP_ESCALATION_FACTOR", "number"),
    ("SIZING_CAP_AUDIT_ENABLED", "bool"),
    ("SIZING_CAP_ALERT_ENABLED", "bool"),
    ("SIZING_CAP_ALERT_THRESHOLD_PCT", "number"),
    ("MAX_CORRELATION", "number"),
    ("DAILY_LOSS_LIMIT_PCT", "number"),
    ("HMM_RISK_OFF_BLOCK_THRESHOLD", "number"),
    ("META_LABEL_MIN_CONFIDENCE", "number"),
    ("DASHBOARD_REFRESH_SECONDS", "int"),
    ("MAX_ORDER_RATE_PER_MIN", "int"),
    ("MARKET_DATA_QUOTE_TTL_SECONDS", "int"),
    ("MARKET_DATA_BARS_TTL_SECONDS", "int"),
    ("DRY_RUN", "bool"),
    ("RISK_GATE_ENFORCE_MARKET_HOURS", "bool"),
    ("MARKET_DATA_PROVIDER", "text"),
    ("LOG_LEVEL", "text"),
    ("FORECAST_USE_GARCH_SIGMA", "bool"),
    ("FORECAST_PROPHET_WEIGHT", "number"),
    ("FORECAST_SKILL_WEIGHTING_ENABLED", "bool"),
    ("FORECAST_SKILL_WINDOW_DAYS", "int"),
    ("FORECAST_MODEL_PERSISTENCE_ENABLED", "bool"),
    ("FORECAST_MODEL_RETRAIN_DAYS", "int"),
    ("CNN_LSTM_SUBPROCESS_ISOLATION_ENABLED", "bool"),
    ("CNN_LSTM_PROCESS_POOL_WORKERS", "int"),
    ("CNN_LSTM_SUBPROCESS_TIMEOUT_SECONDS", "int"),
    ("ADVISORY_REUSE_PIPELINE_COMPUTE", "bool"),
    ("FUNDAMENTALS_SOURCE", "text"),
    ("BETA_LOOKBACK_DAYS", "int"),
    ("SECTOR_FORECAST_CONFIG_PATH", "text"),
    ("SECTOR_FORECAST_CONFIGS", "json"),
    # Prompt Registry (non-secret toggles; credentials live in .env only)
    ("PROMPT_REGISTRY_ENABLED", "bool"),
    ("PROMPT_REGISTRY_BACKEND", "text"),
    # Persistent orchestrator daemon + State API CORS policy
    ("ORCHESTRATOR_DAEMON_ENABLED", "bool"),
    ("PILOTS_API_ENABLED", "bool"),
    ("CORS_ALLOWED_ORIGINS", "json"),
    ("DEFAULT_TICKERS", "tickers"),
    # ETF volatility-transmission overlay (data/etf_holdings.py,
    # risk/etf_transmission.py, sizing/position_sizer.py). All 19 settings
    # default to today's exact no-op behavior; see gui/env_io.py's ALLOWED_KEYS
    # block for the full family docstring. Deliberate departure from the
    # SECTOR_HEAT_*/ATTENTION_* precedent (those stay .env-hand-edit-only,
    # neither ALLOWED_KEYS nor _SETTINGS_LAYOUT) -- this feature family's
    # tunables are exposed here intentionally.
    ("ETF_HOLDINGS_ENABLED", "bool"),
    ("ETF_HOLDINGS_ISSUER_CSV_ENABLED", "bool"),
    ("ETF_TRANSMISSION_ENABLED", "bool"),
    ("ETF_TRANSMISSION_SIZING_ENABLED", "bool"),
    ("ETF_TRANSMISSION_PORTFOLIO_ENABLED", "bool"),
    ("ETF_HOLDINGS_REFRESH_DAYS", "int"),
    ("ETF_HOLDINGS_CIRCUIT_BREAKER_THRESHOLD", "int"),
    ("ETF_TRANSMISSION_WINDOW_DAYS", "int"),
    ("ETF_TRANSMISSION_MIN_OBS", "int"),
    ("ETF_TRANSMISSION_COV_WINDOW_DAYS", "int"),
    ("ETF_HOLDINGS_MAX_SECONDS_PER_CYCLE", "number"),
    ("ETF_TRANSMISSION_MAX_DERATE", "number"),
    ("ETF_TRANSMISSION_OWNERSHIP_REFERENCE", "number"),
    ("ETF_TRANSMISSION_MIN_MULTIPLIER", "number"),
    ("ETF_TRANSMISSION_COV_INFLATION", "number"),
    ("ETF_HOLDINGS_MARKET_PROXY", "text"),
    ("ETF_HOLDINGS_TICKERS", "tickers"),
    ("ETF_TRANSMISSION_WRAPPERS", "tickers"),
    ("ETF_TRANSMISSION_EXCLUDED_SYMBOLS", "tickers"),
]


def _current_scalar(key: str, fallback: Any) -> Any:
    """Best-effort current value of ``key`` (from .env, else live settings)."""
    try:
        raw = env_io.get_value(key, "")
    except Exception:
        raw = ""
    if raw != "":
        return raw
    return getattr(settings, key, fallback)



def render_settings_manager() -> None:
    """Edit NON-secret tunables and persist them to ``.env`` (secrets masked)."""
    help_widgets.explain("settings")
    st.subheader("⚙️ Dynamic Settings Manager")
    st.caption(
        "Edit non-secret runtime tunables. Changes are written to `.env` and take "
        "effect on the **next** launch. Secrets are masked and read-only here "
        "(edit them directly in `.env`)."
    )

    updates: Dict[str, Any] = {}
    with st.form("settings_form"):
        for key, kind in _SETTINGS_LAYOUT:
            cur = _current_scalar(key, getattr(settings, key, ""))
            if kind == "number":
                try:
                    val = st.number_input(key, value=float(cur), step=0.01, format="%.4f")
                except Exception:
                    val = st.number_input(key, value=0.0, step=0.01, format="%.4f")
                updates[key] = val
            elif kind == "int":
                try:
                    val = st.number_input(key, value=int(float(cur)), step=1)
                except Exception:
                    val = st.number_input(key, value=0, step=1)
                updates[key] = int(val)
            elif kind == "bool":
                truthy = str(cur).strip().lower() in {"1", "true", "yes", "on"}
                updates[key] = st.checkbox(key, value=truthy)
            elif kind == "json":
                # JSON list/dict tunable (env_io JSON-encodes on write, so we
                # hand write_many a parsed Python object, not a string).
                obj: Any = cur
                if isinstance(cur, str) and cur != "":
                    try:
                        obj = json.loads(cur)
                    except Exception:
                        obj = cur  # fall back to raw string for display
                try:
                    default_text = json.dumps(obj, indent=2)
                except Exception:
                    default_text = "" if cur is None else str(cur)
                text = st.text_area(key, value=default_text)
                try:
                    updates[key] = json.loads(text)
                except Exception:
                    st.warning(
                        f"'{key}' is not valid JSON — skipping this field "
                        "(other settings will still be saved)."
                    )
            elif kind == "tickers":
                # `cur` is either the live list (settings default / no .env
                # entry yet) or a raw JSON-encoded string (already written to
                # .env by a prior save) -- parse the latter case explicitly
                # rather than falling back to a hardcoded, key-agnostic
                # default (a fallback to settings.DEFAULT_TICKERS here would
                # silently mis-render any OTHER ticker-list field, e.g.
                # ETF_HOLDINGS_TICKERS, once .env has its own saved value).
                if isinstance(cur, list):
                    default_list = cur
                else:
                    parsed: Any = None
                    if isinstance(cur, str) and cur != "":
                        try:
                            parsed = json.loads(cur)
                        except Exception:
                            parsed = None
                    default_list = (
                        parsed if isinstance(parsed, list)
                        else list(getattr(settings, key, []))
                    )
                text = st.text_input(
                    key, value=", ".join(str(t) for t in default_list),
                    help="Comma-separated tickers; stored as a JSON array.",
                )
                updates[key] = [t.strip().upper() for t in text.split(",") if t.strip()]
            else:  # text
                updates[key] = st.text_input(key, value="" if cur is None else str(cur))

        submitted = st.form_submit_button("💾 Save to .env", type="primary")

    if submitted:
        try:
            with busy("Saving settings to .env…"):
                written = env_io.write_many(updates)
            st.success(f"Saved {len(written)} setting(s) to .env. Re-launch to apply.")
        except env_io.SecretWriteError as exc:
            st.error(f"Refused to write a secret: {exc}")
        except Exception as exc:
            st.error(f"Failed to write settings: {exc}")

    # Masked view of secrets so the operator can confirm what's configured.
    with st.expander("🔒 Secrets (masked, read-only)"):
        secret_rows = []
        for key in env_io.SECRET_KEYS:
            try:
                raw = dict(env_io._raw_env()).get(key)  # noqa: SLF001 - internal read for display
            except Exception:
                raw = None
            secret_rows.append({"Key": key, "Status": env_io.mask_secret(raw)})
        st.dataframe(pd.DataFrame(secret_rows), width="stretch")


# ===========================================================================
# Tab 4 — Strategy Matrix & Risk Gating
# ===========================================================================


