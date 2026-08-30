"""
tests/test_gui_env_io_new_keys.py
==================================
Consolidated pin for a batch of non-secret, plain-scalar settings keys added
to ``gui/env_io.py``'s ``ALLOWED_KEYS`` in separate incremental PRs — the
CNN-LSTM subprocess isolation fix (issue #381), the Wave-1 forecasting/
fundamentals tunables, the Launcher progress-poll interval, and the daemon
graceful-shutdown budget:

    CNN_LSTM_SUBPROCESS_ISOLATION_ENABLED  (bool)
    CNN_LSTM_PROCESS_POOL_WORKERS          (int)
    CNN_LSTM_SUBPROCESS_TIMEOUT_SECONDS    (int)
    FORECAST_USE_GARCH_SIGMA               (bool — GJR-GARCH sigma into Monte Carlo)
    FORECAST_PROPHET_WEIGHT                (float [0,1] — Prophet ensemble overlay weight)
    FUNDAMENTALS_SOURCE                    ("yahoo" | "yfinance_info")
    BETA_LOOKBACK_DAYS                     (int — beta computation lookback)
    PROGRESS_POLL_SECONDS                  (int — Launcher progress-bar poll interval)
    DAEMON_SHUTDOWN_TIMEOUT_SECONDS        (float — daemon graceful-shutdown budget)

Each key previously had its own near-identical file re-asserting the same
four-part contract (allowlisted, not secret, not JSON-encoded, round-trips).
Consolidated here as one parametrized table per this repo's own convention
already established in ``test_gui_env_io.py``'s
``test_reclassified_flags_are_now_gui_writable`` — every key keeps its own
individually-reported test case (nothing here reduces coverage, only the
duplicated boilerplate). Formerly:
tests/test_gui_env_io_cnn_lstm_keys.py, tests/test_gui_env_io_forecast_keys.py,
tests/test_gui_env_io_progress_key.py,
tests/test_gui_env_io_daemon_shutdown_timeout_key.py.

``ETF_TRANSMISSION_*``/``ETF_HOLDINGS_*`` (19 keys, mixed scalar + JSON-list
types, with their own widget-kind assertions) deliberately stay in their own
``tests/test_gui_env_io_etf_transmission_keys.py`` — that file is a
substantial, self-contained feature suite, not template boilerplate.

All writes are redirected to a temp ``.env`` via monkeypatching
``env_io.ENV_PATH`` so the real project ``.env`` is never touched.
"""

from __future__ import annotations

import pytest

import env_io
from gui.env_io import ALLOWED_KEYS, SECRET_KEYS, DisallowedKeyError, SecretWriteError

# key -> (value to write, expected .env-encoded string)
NEW_KEYS: dict[str, tuple[object, str]] = {
    "CNN_LSTM_SUBPROCESS_ISOLATION_ENABLED": (True, "true"),
    "CNN_LSTM_PROCESS_POOL_WORKERS": (2, "2"),
    "CNN_LSTM_SUBPROCESS_TIMEOUT_SECONDS": (120, "120"),
    "FORECAST_USE_GARCH_SIGMA": (False, "false"),
    "FORECAST_PROPHET_WEIGHT": (0.35, "0.35"),
    "FUNDAMENTALS_SOURCE": ("yfinance_info", "yfinance_info"),
    "BETA_LOOKBACK_DAYS": (252, "252"),
    "PROGRESS_POLL_SECONDS": (10, "10"),
    "DAEMON_SHUTDOWN_TIMEOUT_SECONDS": (30.0, "30.0"),
}


@pytest.fixture()
def temp_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("# fixture\nRISK_FREE_RATE=0.045\n", encoding="utf-8")
    monkeypatch.setattr(env_io, "ENV_PATH", env_file)
    return env_file


@pytest.mark.parametrize("key", list(NEW_KEYS))
def test_new_key_in_allowlist(key):
    assert key in ALLOWED_KEYS


@pytest.mark.parametrize("key", list(NEW_KEYS))
def test_new_key_not_secret(key):
    assert key not in SECRET_KEYS
    assert env_io.is_secret(key) is False


@pytest.mark.parametrize("key", list(NEW_KEYS))
def test_new_key_not_json_encoded(key):
    # All nine are plain scalars, never JSON-serialized structures.
    assert key not in env_io._JSON_KEYS


@pytest.mark.parametrize("key,value_and_encoded", NEW_KEYS.items())
def test_new_key_roundtrip(temp_env, key, value_and_encoded):
    value, expected_encoded = value_and_encoded
    encoded = env_io.write_setting(key, value)
    assert encoded == expected_encoded
    assert env_io.get_value(key) == expected_encoded
    # Unrelated line preserved by set_key.
    assert "RISK_FREE_RATE=0.045" in temp_env.read_text(encoding="utf-8")


def test_disallowed_key_still_raises(temp_env):
    # None of these additions loosened the allowlist boundary itself.
    with pytest.raises(DisallowedKeyError):
        env_io.write_setting("SOME_RANDOM_UNLISTED_KEY", "value")
    assert "SOME_RANDOM_UNLISTED_KEY" not in temp_env.read_text(encoding="utf-8")


def test_secret_key_still_raises(temp_env):
    # A real secret must still be rejected.
    with pytest.raises(SecretWriteError):
        env_io.write_setting("FINNHUB_API_KEY", "should-never-write")
    assert "FINNHUB_API_KEY" not in temp_env.read_text(encoding="utf-8")


def test_daemon_shutdown_timeout_has_a_settings_manager_widget():
    """Per this codebase's convention (CLAUDE.md's gui/env_io.py bullet):
    never add a GUI-writable setting without both ALLOWED_KEYS AND a
    _SETTINGS_LAYOUT widget."""
    from gui.panels.settings_manager import _SETTINGS_LAYOUT

    layout = dict(_SETTINGS_LAYOUT)
    assert "DAEMON_SHUTDOWN_TIMEOUT_SECONDS" in layout
    assert layout["DAEMON_SHUTDOWN_TIMEOUT_SECONDS"] == "number"


def test_daemon_shutdown_timeout_widget_bounds_match_the_real_validator():
    """Regression guard: the Settings Manager widget's min/max for this key
    must stay in lockstep with settings.py's own field_validator bounds.
    Before this, the widget accepted any float -- submitting e.g. 0 would
    write successfully to .env, then fail Settings() construction on the
    daemon's next launch, potentially locking the operator out of this very
    UI needing to fix it."""
    from gui.panels.settings_manager import _NUMBER_BOUNDS
    from settings import (
        DAEMON_SHUTDOWN_TIMEOUT_MAX_SECONDS,
        DAEMON_SHUTDOWN_TIMEOUT_MIN_SECONDS,
    )

    assert _NUMBER_BOUNDS["DAEMON_SHUTDOWN_TIMEOUT_SECONDS"] == (
        DAEMON_SHUTDOWN_TIMEOUT_MIN_SECONDS,
        DAEMON_SHUTDOWN_TIMEOUT_MAX_SECONDS,
    )
