"""
tests/test_gui_env_io_forecast_keys.py
======================================
Pins the Wave-1 addition of four non-secret forecasting / fundamentals tunables
to ``gui/env_io.py``'s ``ALLOWED_KEYS`` so the Command Center Settings tab can
write them (see forecasting_engine.py + data/market_data.py):

    FORECAST_USE_GARCH_SIGMA   (bool — GJR-GARCH sigma into Monte Carlo)
    FORECAST_PROPHET_WEIGHT    (float [0,1] — Prophet ensemble overlay weight)
    FUNDAMENTALS_SOURCE        ("yahoo" | "yfinance_info")
    BETA_LOOKBACK_DAYS         (int — beta computation lookback)

Contract asserted:
* All four are in ``ALLOWED_KEYS`` and NONE are secrets.
* None are JSON-encoded (they are plain scalars).
* A scalar/bool round-trip through a temp ``.env`` works and preserves
  unrelated lines.
* A genuine secret key still raises ``SecretWriteError`` (CONSTRAINT #3).

All writes are redirected to a temp ``.env`` via monkeypatching
``env_io.ENV_PATH`` so the real project ``.env`` is never touched.
"""

from __future__ import annotations

import pytest

from gui import env_io
from gui.env_io import ALLOWED_KEYS, SECRET_KEYS, SecretWriteError

NEW_KEYS = [
    "FORECAST_USE_GARCH_SIGMA",
    "FORECAST_PROPHET_WEIGHT",
    "FUNDAMENTALS_SOURCE",
    "BETA_LOOKBACK_DAYS",
]


@pytest.fixture()
def temp_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# fixture\nRISK_FREE_RATE=0.045\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(env_io, "ENV_PATH", env_file)
    return env_file


@pytest.mark.parametrize("key", NEW_KEYS)
def test_new_key_in_allowlist(key):
    assert key in ALLOWED_KEYS


@pytest.mark.parametrize("key", NEW_KEYS)
def test_new_key_not_secret(key):
    assert key not in SECRET_KEYS
    assert env_io.is_secret(key) is False


@pytest.mark.parametrize("key", NEW_KEYS)
def test_new_key_not_json_encoded(key):
    # These are plain scalars, never JSON-serialized structures.
    assert key not in env_io._JSON_KEYS


def test_prophet_weight_float_roundtrip(temp_env):
    encoded = env_io.write_setting("FORECAST_PROPHET_WEIGHT", 0.35)
    assert encoded == "0.35"
    assert env_io.get_value("FORECAST_PROPHET_WEIGHT") == "0.35"
    # Unrelated line preserved by set_key.
    assert "RISK_FREE_RATE=0.045" in temp_env.read_text(encoding="utf-8")


def test_garch_sigma_bool_roundtrip(temp_env):
    encoded = env_io.write_setting("FORECAST_USE_GARCH_SIGMA", False)
    assert encoded == "false"  # bools serialize lowercase for pydantic-settings
    assert env_io.get_value("FORECAST_USE_GARCH_SIGMA") == "false"


def test_fundamentals_source_and_beta_lookback_roundtrip(temp_env):
    assert env_io.write_setting("FUNDAMENTALS_SOURCE", "yfinance_info") == "yfinance_info"
    assert env_io.get_value("FUNDAMENTALS_SOURCE") == "yfinance_info"
    assert env_io.write_setting("BETA_LOOKBACK_DAYS", 252) == "252"
    assert env_io.get_value("BETA_LOOKBACK_DAYS") == "252"


def test_secret_key_still_raises(temp_env):
    # A real secret must still be rejected — the new keys did not loosen this.
    with pytest.raises(SecretWriteError):
        env_io.write_setting("FINNHUB_API_KEY", "should-never-write")
    assert "FINNHUB_API_KEY" not in temp_env.read_text(encoding="utf-8")
