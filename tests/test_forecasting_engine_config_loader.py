"""Tests for ForecastingEngine's per-sector forecast config loader.

This is THE critical backward-compatibility gate for the empirical
per-sector forecast config feature: when no committed artifact and no
settings override exist (today's repo state), ``ForecastingEngine()`` must
produce byte-identical ``sector_configs`` to the pre-backtest hardcoded
heuristic. These tests also exercise the artifact-overlay and
settings-override paths end-to-end through the real
``validation.sector_config_io.load_sector_configs`` (not mocked), and prove
the loader never raises even when pointed at a corrupt/missing path.
"""

import json
from pathlib import Path

import pandas as pd
import pytest

import settings as settings_module
from forecasting_engine import ForecastingEngine, _DEFAULT_SECTOR_CONFIGS


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch):
    """Ensure every test starts from a clean, known settings state so tests
    never leak state into each other via the shared settings singleton."""
    monkeypatch.setattr(
        settings_module.settings, "SECTOR_FORECAST_CONFIG_PATH", "forecasting/sector_configs.json"
    )
    monkeypatch.setattr(settings_module.settings, "SECTOR_FORECAST_CONFIGS", {})
    yield


class TestDefaultBehaviorNoArtifact:
    def test_no_artifact_no_override_matches_hardcoded_default(self, monkeypatch, tmp_path):
        # Robust regardless of whether some other agent has committed the
        # real artifact yet: point at a definitely-nonexistent path.
        nonexistent = tmp_path / "definitely_does_not_exist" / "sector_configs.json"
        monkeypatch.setattr(
            settings_module.settings, "SECTOR_FORECAST_CONFIG_PATH", str(nonexistent)
        )
        monkeypatch.setattr(settings_module.settings, "SECTOR_FORECAST_CONFIGS", {})

        engine = ForecastingEngine()
        assert engine.sector_configs == _DEFAULT_SECTOR_CONFIGS

    def test_repo_state_matches_hardcoded_default(self):
        """If the repo genuinely has no artifact committed yet at the
        default path, constructing with zero overrides must reproduce the
        hardcoded default exactly."""
        artifact_path = Path("forecasting/sector_configs.json")
        if artifact_path.exists():
            pytest.skip(
                "forecasting/sector_configs.json now exists in this repo state; "
                "covered instead by test_no_artifact_no_override_matches_hardcoded_default."
            )
        engine = ForecastingEngine()
        assert engine.sector_configs == _DEFAULT_SECTOR_CONFIGS


class TestGenerateForecastTargetDays:
    def test_known_sector_uses_configured_days(self, monkeypatch, tmp_path):
        nonexistent = tmp_path / "nope.json"
        monkeypatch.setattr(
            settings_module.settings, "SECTOR_FORECAST_CONFIG_PATH", str(nonexistent)
        )
        monkeypatch.setattr(settings_module.settings, "SECTOR_FORECAST_CONFIGS", {})

        engine = ForecastingEngine()
        results = engine.generate_forecast(
            row=pd.Series({"sector": "Technology", "Symbol": "TEST"}),
            current_price=100.0,
        )
        assert results["Target_Days"] == 30

    def test_unknown_sector_falls_back_to_60_days(self, monkeypatch, tmp_path):
        nonexistent = tmp_path / "nope.json"
        monkeypatch.setattr(
            settings_module.settings, "SECTOR_FORECAST_CONFIG_PATH", str(nonexistent)
        )
        monkeypatch.setattr(settings_module.settings, "SECTOR_FORECAST_CONFIGS", {})

        engine = ForecastingEngine()
        results = engine.generate_forecast(
            row=pd.Series({"sector": "Nonexistent Sector", "Symbol": "TEST"}),
            current_price=100.0,
        )
        assert results["Target_Days"] == 60


class TestCorruptOrMissingArtifactNeverRaises:
    def test_nonexistent_path_never_raises(self, monkeypatch, tmp_path):
        nonexistent = tmp_path / "no" / "such" / "file.json"
        monkeypatch.setattr(
            settings_module.settings, "SECTOR_FORECAST_CONFIG_PATH", str(nonexistent)
        )
        monkeypatch.setattr(settings_module.settings, "SECTOR_FORECAST_CONFIGS", {})

        engine = ForecastingEngine()  # must not raise
        assert engine.sector_configs == _DEFAULT_SECTOR_CONFIGS

    def test_corrupt_json_never_raises(self, monkeypatch, tmp_path):
        bad_path = tmp_path / "corrupt.json"
        bad_path.write_text("{ this is not valid json ]", encoding="utf-8")
        monkeypatch.setattr(
            settings_module.settings, "SECTOR_FORECAST_CONFIG_PATH", str(bad_path)
        )
        monkeypatch.setattr(settings_module.settings, "SECTOR_FORECAST_CONFIGS", {})

        engine = ForecastingEngine()  # must not raise
        assert engine.sector_configs == _DEFAULT_SECTOR_CONFIGS


class TestArtifactOverlay:
    def test_valid_artifact_overlays_only_touched_sectors(self, monkeypatch, tmp_path):
        artifact_path = tmp_path / "sector_configs.json"
        artifact = {
            "schema_version": 1,
            "generated_at": "2026-07-08T00:00:00+00:00",
            "backtest": {},
            "sector_configs": {"Technology": {"days": 90, "model": "ARIMA"}},
            "grid": [],
        }
        artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
        monkeypatch.setattr(
            settings_module.settings, "SECTOR_FORECAST_CONFIG_PATH", str(artifact_path)
        )
        monkeypatch.setattr(settings_module.settings, "SECTOR_FORECAST_CONFIGS", {})

        engine = ForecastingEngine()
        assert engine.sector_configs["Technology"] == {"days": 90, "model": "ARIMA"}
        # Untouched sector must still equal its hardcoded default value.
        assert engine.sector_configs["Real Estate"] == _DEFAULT_SECTOR_CONFIGS["Real Estate"]


class TestSettingsOverride:
    def test_settings_override_wins_with_no_artifact(self, monkeypatch, tmp_path):
        nonexistent = tmp_path / "no_artifact.json"
        monkeypatch.setattr(
            settings_module.settings, "SECTOR_FORECAST_CONFIG_PATH", str(nonexistent)
        )
        monkeypatch.setattr(
            settings_module.settings,
            "SECTOR_FORECAST_CONFIGS",
            {"Real Estate": {"days": 30, "model": "ARIMA"}},
        )

        engine = ForecastingEngine()
        assert engine.sector_configs["Real Estate"] == {"days": 30, "model": "ARIMA"}
        # Other sectors unaffected.
        assert engine.sector_configs["Technology"] == _DEFAULT_SECTOR_CONFIGS["Technology"]
        assert engine.sector_configs["Healthcare"] == _DEFAULT_SECTOR_CONFIGS["Healthcare"]
