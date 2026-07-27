"""
tests/test_gui_env_io_cnn_lstm_keys.py
=======================================
Pins the CNN-LSTM subprocess isolation fix's (issue #381, docs/known_issues/
cnn_lstm_tf_deadlock.md) three non-secret settings to ``gui/env_io.py``'s
``ALLOWED_KEYS`` so the Command Center Settings tab can write them:

    CNN_LSTM_SUBPROCESS_ISOLATION_ENABLED  (bool)
    CNN_LSTM_PROCESS_POOL_WORKERS          (int)
    CNN_LSTM_SUBPROCESS_TIMEOUT_SECONDS    (int)

Mirrors tests/test_gui_env_io_forecast_keys.py's contract and structure.
"""

from __future__ import annotations

import pytest

from gui import env_io
from gui.env_io import ALLOWED_KEYS, SECRET_KEYS

NEW_KEYS = [
    "CNN_LSTM_SUBPROCESS_ISOLATION_ENABLED",
    "CNN_LSTM_PROCESS_POOL_WORKERS",
    "CNN_LSTM_SUBPROCESS_TIMEOUT_SECONDS",
]


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


@pytest.mark.parametrize("key", NEW_KEYS)
def test_new_key_not_json_encoded(key):
    assert key not in env_io._JSON_KEYS


def test_isolation_enabled_bool_roundtrip(temp_env):
    encoded = env_io.write_setting("CNN_LSTM_SUBPROCESS_ISOLATION_ENABLED", True)
    assert encoded == "true"
    assert env_io.get_value("CNN_LSTM_SUBPROCESS_ISOLATION_ENABLED") == "true"
    assert "RISK_FREE_RATE=0.045" in temp_env.read_text(encoding="utf-8")


def test_pool_workers_and_timeout_int_roundtrip(temp_env):
    assert env_io.write_setting("CNN_LSTM_PROCESS_POOL_WORKERS", 2) == "2"
    assert env_io.get_value("CNN_LSTM_PROCESS_POOL_WORKERS") == "2"
    assert env_io.write_setting("CNN_LSTM_SUBPROCESS_TIMEOUT_SECONDS", 120) == "120"
    assert env_io.get_value("CNN_LSTM_SUBPROCESS_TIMEOUT_SECONDS") == "120"
