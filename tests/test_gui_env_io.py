"""
tests/test_gui_env_io.py
========================
Unit tests for ``gui/env_io.py`` — the safe, allowlist-bounded ``.env`` read/
write layer behind the Command Center's Settings and Strategy Matrix tabs.

These tests pin the security-critical contract (CONSTRAINT #3):

*   Secret keys are NEVER returned in cleartext and NEVER writable from the GUI.
*   Only allowlisted keys are writable; unknown keys are rejected.
*   List/dict tunables round-trip as JSON so pydantic-settings re-parses them.
*   Writes preserve unrelated lines/comments already in ``.env``.

All writes are redirected to a temporary ``.env`` via monkeypatching
``env_io.ENV_PATH`` so the real project ``.env`` is never touched.
"""

import json

import pytest

from gui import env_io


@pytest.fixture()
def temp_env(tmp_path, monkeypatch):
    """Point env_io at an isolated temp .env seeded with a comment + secret."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# InvestYo config (test fixture)\n"
        "FRED_API_KEY=super-secret-value\n"
        "RISK_FREE_RATE=0.045\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(env_io, "ENV_PATH", env_file)
    return env_file


# ---------------------------------------------------------------------------
# Secret protection
# ---------------------------------------------------------------------------

def test_read_settings_masks_secrets(temp_env):
    display = env_io.read_settings()
    assert display["FRED_API_KEY"] == env_io._MASK_SET  # masked, not cleartext
    assert "super-secret-value" not in json.dumps(display)


def test_get_value_refuses_secret(temp_env):
    with pytest.raises(env_io.SecretWriteError):
        env_io.get_value("FRED_API_KEY")


def test_write_setting_refuses_secret(temp_env):
    with pytest.raises(env_io.SecretWriteError):
        env_io.write_setting("ALPACA_SECRET_KEY", "anything")
    # The secret must not have been written.
    assert "ALPACA_SECRET_KEY" not in temp_env.read_text(encoding="utf-8")


def test_is_secret_classification():
    assert env_io.is_secret("RH_MFA_SECRET") is True
    assert env_io.is_secret("KELLY_FRACTION") is False


# ---------------------------------------------------------------------------
# Allowlist enforcement
# ---------------------------------------------------------------------------

def test_write_setting_rejects_unknown_key(temp_env):
    with pytest.raises(env_io.DisallowedKeyError):
        env_io.write_setting("TOTALLY_MADE_UP_KEY", "1")


def test_write_setting_scalar_roundtrip(temp_env):
    env_io.write_setting("KELLY_FRACTION", 0.33)
    assert env_io.get_value("KELLY_FRACTION") == "0.33"


def test_write_setting_bool_lowercased(temp_env):
    env_io.write_setting("DRY_RUN", True)
    assert env_io.get_value("DRY_RUN") == "true"


# ---------------------------------------------------------------------------
# JSON-encoded structures
# ---------------------------------------------------------------------------

def test_default_tickers_json_roundtrip(temp_env):
    env_io.write_setting("DEFAULT_TICKERS", ["AAPL", "MSFT"])
    raw = env_io.get_value("DEFAULT_TICKERS")
    assert json.loads(raw) == ["AAPL", "MSFT"]


def test_disabled_modules_json_roundtrip(temp_env):
    env_io.write_setting("DISABLED_SIGNAL_MODULES", ["rsi2_mean_reversion"])
    raw = env_io.get_value("DISABLED_SIGNAL_MODULES")
    assert json.loads(raw) == ["rsi2_mean_reversion"]


def test_signal_weights_json_roundtrip(temp_env):
    weights = {"macro_regime": 45.0, "graham_value": 15.0}
    env_io.write_setting("SIGNAL_WEIGHTS", weights)
    raw = env_io.get_value("SIGNAL_WEIGHTS")
    assert json.loads(raw) == weights


# ---------------------------------------------------------------------------
# File preservation + batch writes
# ---------------------------------------------------------------------------

def test_write_preserves_other_lines(temp_env):
    env_io.write_setting("RISK_FREE_RATE", 0.05)
    text = temp_env.read_text(encoding="utf-8")
    # Original comment + secret line are still present.
    assert "# InvestYo config (test fixture)" in text
    assert "FRED_API_KEY=" in text


def test_write_many_returns_written_keys(temp_env):
    written = env_io.write_many({"KELLY_FRACTION": 0.4, "VOL_TARGET": 0.12})
    assert set(written) == {"KELLY_FRACTION", "VOL_TARGET"}
    assert env_io.get_value("KELLY_FRACTION") == "0.4"
    assert env_io.get_value("VOL_TARGET") == "0.12"


def test_allowlisted_keys_nonempty_and_excludes_secrets():
    keys = set(env_io.allowlisted_keys())
    assert "KELLY_FRACTION" in keys
    assert keys.isdisjoint(set(env_io.SECRET_KEYS))
