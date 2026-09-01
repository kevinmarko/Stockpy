"""
tests/test_gui_env_io_etf_transmission_keys.py
================================================
Pins the 19 non-secret ETF volatility-transmission overlay settings (Ben-David,
Franzoni & Moussawi 2018, JF) to ``gui/env_io.py``'s ``ALLOWED_KEYS`` so the
Command Center Settings tab can write them -- holdings ingestion
(``data/etf_holdings.py``), market-residualized measurement columns + portfolio
covariance inflation (``risk/etf_transmission.py``), and the per-name sizing
derate (``sizing/position_sizer.py``):

    Bools:
        ETF_HOLDINGS_ENABLED
        ETF_HOLDINGS_ISSUER_CSV_ENABLED
        ETF_TRANSMISSION_ENABLED
        ETF_TRANSMISSION_SIZING_ENABLED
        ETF_TRANSMISSION_PORTFOLIO_ENABLED

    Ints:
        ETF_HOLDINGS_REFRESH_DAYS
        ETF_HOLDINGS_CIRCUIT_BREAKER_THRESHOLD
        ETF_TRANSMISSION_WINDOW_DAYS
        ETF_TRANSMISSION_MIN_OBS
        ETF_TRANSMISSION_COV_WINDOW_DAYS

    Floats:
        ETF_HOLDINGS_MAX_SECONDS_PER_CYCLE
        ETF_TRANSMISSION_MAX_DERATE
        ETF_TRANSMISSION_OWNERSHIP_REFERENCE
        ETF_TRANSMISSION_MIN_MULTIPLIER
        ETF_TRANSMISSION_COV_INFLATION

    String:
        ETF_HOLDINGS_MARKET_PROXY

    Lists (JSON-encoded in .env):
        ETF_HOLDINGS_TICKERS
        ETF_TRANSMISSION_WRAPPERS
        ETF_TRANSMISSION_EXCLUDED_SYMBOLS

Mirrors tests/test_gui_env_io_cnn_lstm_keys.py's contract and structure.
"""

from __future__ import annotations

import pytest

import shared.env_io as env_io
from shared.env_io import ALLOWED_KEYS, SECRET_KEYS
from legacy.streamlit_command_center.panels.settings_manager import _SETTINGS_LAYOUT

BOOL_KEYS = [
    "ETF_HOLDINGS_ENABLED",
    "ETF_HOLDINGS_ISSUER_CSV_ENABLED",
    "ETF_TRANSMISSION_ENABLED",
    "ETF_TRANSMISSION_SIZING_ENABLED",
    "ETF_TRANSMISSION_PORTFOLIO_ENABLED",
]

INT_KEYS = [
    "ETF_HOLDINGS_REFRESH_DAYS",
    "ETF_HOLDINGS_CIRCUIT_BREAKER_THRESHOLD",
    "ETF_TRANSMISSION_WINDOW_DAYS",
    "ETF_TRANSMISSION_MIN_OBS",
    "ETF_TRANSMISSION_COV_WINDOW_DAYS",
]

FLOAT_KEYS = [
    "ETF_HOLDINGS_MAX_SECONDS_PER_CYCLE",
    "ETF_TRANSMISSION_MAX_DERATE",
    "ETF_TRANSMISSION_OWNERSHIP_REFERENCE",
    "ETF_TRANSMISSION_MIN_MULTIPLIER",
    "ETF_TRANSMISSION_COV_INFLATION",
]

STRING_KEYS = [
    "ETF_HOLDINGS_MARKET_PROXY",
]

LIST_KEYS = [
    "ETF_HOLDINGS_TICKERS",
    "ETF_TRANSMISSION_WRAPPERS",
    "ETF_TRANSMISSION_EXCLUDED_SYMBOLS",
]

NEW_KEYS = BOOL_KEYS + INT_KEYS + FLOAT_KEYS + STRING_KEYS + LIST_KEYS

assert len(NEW_KEYS) == 19, f"expected 19 new keys, got {len(NEW_KEYS)}"


@pytest.fixture()
def temp_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("# fixture\nRISK_FREE_RATE=0.045\n", encoding="utf-8")
    monkeypatch.setattr(env_io, "ENV_PATH", env_file)
    return env_file


@pytest.mark.parametrize("key", NEW_KEYS)
def test_new_key_in_allowlist(key):
    assert key in ALLOWED_KEYS


@pytest.mark.parametrize("key", NEW_KEYS)
def test_new_key_not_secret(key):
    assert key not in SECRET_KEYS
    assert env_io.is_secret(key) is False


@pytest.mark.parametrize("key", LIST_KEYS)
def test_list_key_is_json_encoded(key):
    assert key in env_io._JSON_KEYS


@pytest.mark.parametrize(
    "key", BOOL_KEYS + INT_KEYS + FLOAT_KEYS + STRING_KEYS
)
def test_non_list_key_not_json_encoded(key):
    assert key not in env_io._JSON_KEYS


@pytest.mark.parametrize("key", NEW_KEYS)
def test_new_key_has_settings_layout_widget(key):
    """Every key added to ALLOWED_KEYS also gets a rendered widget in the
    Settings Manager tab (gui/panels/settings_manager.py's _SETTINGS_LAYOUT)."""
    layout_keys = {k for k, _kind in _SETTINGS_LAYOUT}
    assert key in layout_keys, f"{key} missing a Settings-Manager widget"


@pytest.mark.parametrize("key", BOOL_KEYS)
def test_bool_key_widget_kind(key):
    layout = dict(_SETTINGS_LAYOUT)
    assert layout[key] == "bool"


@pytest.mark.parametrize("key", INT_KEYS)
def test_int_key_widget_kind(key):
    layout = dict(_SETTINGS_LAYOUT)
    assert layout[key] == "int"


@pytest.mark.parametrize("key", FLOAT_KEYS)
def test_float_key_widget_kind(key):
    layout = dict(_SETTINGS_LAYOUT)
    assert layout[key] == "number"


@pytest.mark.parametrize("key", LIST_KEYS)
def test_list_key_widget_kind_is_tickers(key):
    layout = dict(_SETTINGS_LAYOUT)
    assert layout[key] == "tickers"


def test_bool_roundtrip(temp_env):
    encoded = env_io.write_setting("ETF_HOLDINGS_ENABLED", True)
    assert encoded == "true"
    assert env_io.get_value("ETF_HOLDINGS_ENABLED") == "true"
    assert "RISK_FREE_RATE=0.045" in temp_env.read_text(encoding="utf-8")


def test_int_roundtrip(temp_env):
    assert env_io.write_setting("ETF_HOLDINGS_REFRESH_DAYS", 14) == "14"
    assert env_io.get_value("ETF_HOLDINGS_REFRESH_DAYS") == "14"


def test_float_roundtrip(temp_env):
    assert env_io.write_setting("ETF_TRANSMISSION_MAX_DERATE", 0.4) == "0.4"
    assert env_io.get_value("ETF_TRANSMISSION_MAX_DERATE") == "0.4"


def test_string_roundtrip(temp_env):
    assert env_io.write_setting("ETF_HOLDINGS_MARKET_PROXY", "QQQ") == "QQQ"
    assert env_io.get_value("ETF_HOLDINGS_MARKET_PROXY") == "QQQ"


def test_list_roundtrip_json_encoded(temp_env):
    encoded = env_io.write_setting("ETF_HOLDINGS_TICKERS", ["SPY", "QQQ"])
    assert encoded == '["SPY", "QQQ"]'
    assert env_io.get_value("ETF_HOLDINGS_TICKERS") == '["SPY", "QQQ"]'
