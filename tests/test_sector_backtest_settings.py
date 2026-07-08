"""
tests/test_sector_backtest_settings.py
=======================================
Unit tests for the settings.py / gui.env_io wiring behind the empirical
per-sector forecast model/horizon feature (replacing the hardcoded
per-sector heuristic in forecasting_engine.py with one derived from an
offline walk-forward backtest — see validation/sector_forecast_backtest.py).

Covers:
* Default values of ``Settings.SECTOR_FORECAST_CONFIG_PATH`` /
  ``Settings.SECTOR_FORECAST_CONFIGS``.
* The ``_validate_sector_forecast_configs`` field validator's fail-safe
  filtering behavior (valid entries survive, invalid ones are dropped).
* The validator's graceful degradation to ``{}`` when
  ``validation.sector_config_io`` is unavailable/broken — it must never raise.
* ``gui/env_io.py``'s allowlist/JSON-key wiring for the two new keys, plus a
  round-trip write of ``SECTOR_FORECAST_CONFIGS`` through the established
  temp-``.env`` fixture pattern (see tests/test_gui_env_io.py).

All ``Settings()`` instances are constructed with ``_env_file=None`` so a
developer's local .env file cannot influence the assertions, matching the
convention in tests/test_settings.py. The real
``validation/sector_config_io.py`` (owned by a concurrently-authored agent)
is never imported directly here — it is patched via
``unittest.mock.patch("validation.sector_config_io.validate_sector_config_entry", ...)``
so this test file is hermetic and does not depend on that file's presence or
correctness. End-to-end integration against the real module is exercised by
a separate cross-cutting test.
"""

from __future__ import annotations

import json
import sys
import types
from unittest.mock import patch

import pytest

from settings import Settings


def _fresh_settings(**overrides) -> Settings:
    """Construct a Settings() instance ignoring any real .env file."""
    return Settings(_env_file=None, **overrides)


# =============================================================================
# 1. Defaults
# =============================================================================
def test_sector_forecast_config_path_default(monkeypatch):
    monkeypatch.delenv("SECTOR_FORECAST_CONFIG_PATH", raising=False)
    monkeypatch.delenv("SECTOR_FORECAST_CONFIGS", raising=False)
    s = _fresh_settings()
    assert s.SECTOR_FORECAST_CONFIG_PATH == "forecasting/sector_configs.json"


def test_sector_forecast_configs_default_empty(monkeypatch):
    monkeypatch.delenv("SECTOR_FORECAST_CONFIG_PATH", raising=False)
    monkeypatch.delenv("SECTOR_FORECAST_CONFIGS", raising=False)
    s = _fresh_settings()
    assert s.SECTOR_FORECAST_CONFIGS == {}


# =============================================================================
# 2. Validator — filters invalid entries via a mocked sector_config_io
# =============================================================================
def _install_fake_sector_config_io(fn):
    """Install a fake ``validation.sector_config_io`` module exposing
    ``validate_sector_config_entry = fn`` and return it, so the validator's
    ``from validation.sector_config_io import validate_sector_config_entry``
    resolves to our mock regardless of whether the real module exists yet.
    """
    fake_module = types.ModuleType("validation.sector_config_io")
    fake_module.validate_sector_config_entry = fn
    return fake_module


def test_validator_keeps_only_valid_entries(monkeypatch):
    def fake_validate(entry):
        # "Technology" -> valid, normalized; "Garbage" -> invalid -> None.
        if isinstance(entry, dict) and entry.get("model") in {"MC", "ARIMA", "HW"}:
            return {"days": int(entry.get("days", 30)), "model": entry["model"]}
        return None

    fake_module = _install_fake_sector_config_io(fake_validate)
    with patch.dict(sys.modules, {"validation.sector_config_io": fake_module}):
        s = _fresh_settings(
            SECTOR_FORECAST_CONFIGS={
                "Technology": {"days": 30, "model": "MC"},
                "Energy": {"days": 60, "model": "ARIMA"},
                # Semantically invalid (no recognized "model") but still a
                # dict, so it passes pydantic's own dict[str, dict] type
                # check upstream of our field_validator and reaches
                # fake_validate, which rejects it (returns None).
                "Garbage": {"nonsense": True},
            }
        )
    assert s.SECTOR_FORECAST_CONFIGS == {
        "Technology": {"days": 30, "model": "MC"},
        "Energy": {"days": 60, "model": "ARIMA"},
    }


def test_validator_all_invalid_yields_empty_dict(monkeypatch):
    fake_module = _install_fake_sector_config_io(lambda entry: None)
    with patch.dict(sys.modules, {"validation.sector_config_io": fake_module}):
        s = _fresh_settings(
            SECTOR_FORECAST_CONFIGS={"Technology": {"days": 30, "model": "MC"}}
        )
    assert s.SECTOR_FORECAST_CONFIGS == {}


def test_validator_empty_input_stays_empty(monkeypatch):
    fake_module = _install_fake_sector_config_io(lambda entry: {"days": 30, "model": "MC"})
    with patch.dict(sys.modules, {"validation.sector_config_io": fake_module}):
        s = _fresh_settings(SECTOR_FORECAST_CONFIGS={})
    assert s.SECTOR_FORECAST_CONFIGS == {}


# =============================================================================
# 3. Graceful degradation — import failure never raises, collapses to {}
# =============================================================================
def test_validator_import_error_degrades_to_empty_dict(monkeypatch):
    """Simulate validation.sector_config_io being unavailable/broken (e.g.
    Agent C's file doesn't exist yet, or raises on import) by forcing the
    import to fail. The validator's ``except Exception: return {}`` branch
    must swallow this — a malformed/missing validation module can never
    crash Settings() construction.
    """
    real_import = __import__

    def _raising_import(name, *args, **kwargs):
        if name == "validation.sector_config_io" or name.startswith(
            "validation.sector_config_io"
        ):
            raise ImportError("simulated missing module")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=_raising_import):
        s = _fresh_settings(
            SECTOR_FORECAST_CONFIGS={"Technology": {"days": 30, "model": "MC"}}
        )
    assert s.SECTOR_FORECAST_CONFIGS == {}


def test_validator_import_error_via_missing_sys_modules_entry(monkeypatch):
    """Alternate reproduction of the missing-module case: ensure no stale
    ``validation.sector_config_io`` module lingers in sys.modules and that
    the real package (if present) doesn't happen to expose the symbol in a
    way that would make this test accidentally pass/fail on unrelated state.
    """
    sys.modules.pop("validation.sector_config_io", None)
    real_import = __import__

    def _raising_import(name, *args, **kwargs):
        if name == "validation.sector_config_io":
            raise ModuleNotFoundError("no module named validation.sector_config_io")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=_raising_import):
        s = _fresh_settings(
            SECTOR_FORECAST_CONFIGS={"Energy": {"days": 90, "model": "HW"}}
        )
    assert s.SECTOR_FORECAST_CONFIGS == {}


# =============================================================================
# 4. gui/env_io.py wiring
# =============================================================================
class TestEnvIoAllowlist:
    def test_config_path_in_allowed_keys(self):
        from gui.env_io import ALLOWED_KEYS

        assert "SECTOR_FORECAST_CONFIG_PATH" in ALLOWED_KEYS

    def test_configs_in_allowed_keys(self):
        from gui.env_io import ALLOWED_KEYS

        assert "SECTOR_FORECAST_CONFIGS" in ALLOWED_KEYS

    def test_configs_is_json_key(self):
        from gui.env_io import _JSON_KEYS

        assert "SECTOR_FORECAST_CONFIGS" in _JSON_KEYS

    def test_config_path_is_not_json_key(self):
        from gui.env_io import _JSON_KEYS

        assert "SECTOR_FORECAST_CONFIG_PATH" not in _JSON_KEYS

    def test_neither_key_is_secret(self):
        from gui.env_io import SECRET_KEYS

        assert "SECTOR_FORECAST_CONFIG_PATH" not in SECRET_KEYS
        assert "SECTOR_FORECAST_CONFIGS" not in SECRET_KEYS


@pytest.fixture()
def temp_env(tmp_path, monkeypatch):
    """Point env_io at an isolated temp .env (mirrors tests/test_gui_env_io.py)."""
    from gui import env_io

    env_file = tmp_path / ".env"
    env_file.write_text(
        "# InvestYo config (test fixture)\nRISK_FREE_RATE=0.045\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(env_io, "ENV_PATH", env_file)
    return env_file


def test_sector_forecast_configs_json_roundtrip(temp_env):
    from gui import env_io

    configs = {"Technology": {"days": 30, "model": "MC"}, "Energy": {"days": 60, "model": "ARIMA"}}
    env_io.write_setting("SECTOR_FORECAST_CONFIGS", configs)
    raw = env_io.get_value("SECTOR_FORECAST_CONFIGS")
    assert json.loads(raw) == configs


def test_sector_forecast_config_path_scalar_roundtrip(temp_env):
    from gui import env_io

    env_io.write_setting("SECTOR_FORECAST_CONFIG_PATH", "forecasting/custom_configs.json")
    assert env_io.get_value("SECTOR_FORECAST_CONFIG_PATH") == "forecasting/custom_configs.json"


def test_write_many_includes_both_new_keys(temp_env):
    from gui import env_io

    written = env_io.write_many(
        {
            "SECTOR_FORECAST_CONFIG_PATH": "forecasting/sector_configs.json",
            "SECTOR_FORECAST_CONFIGS": {"Technology": {"days": 30, "model": "MC"}},
        }
    )
    assert set(written) == {"SECTOR_FORECAST_CONFIG_PATH", "SECTOR_FORECAST_CONFIGS"}
    assert env_io.get_value("SECTOR_FORECAST_CONFIG_PATH") == "forecasting/sector_configs.json"
    assert json.loads(env_io.get_value("SECTOR_FORECAST_CONFIGS")) == {
        "Technology": {"days": 30, "model": "MC"}
    }
