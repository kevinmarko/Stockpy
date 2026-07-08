"""Tests for validation/sector_config_io.py.

Covers: validate_sector_config_entry (normalization + rejection rules),
derive_sector_configs (argmin-mase selection + tiebreaks + fallback),
build_artifact/write_artifact (shape + determinism of data payload), and
load_sector_configs (the safety-critical runtime loader: never raises,
overlay semantics, partial-artifact-corruption resilience).
"""

import json
import math

import pytest

from validation.sector_config_io import (
    SCHEMA_VERSION,
    build_artifact,
    derive_sector_configs,
    load_sector_configs,
    validate_sector_config_entry,
    write_artifact,
)
from validation.sector_forecast_types import BacktestConfig, CellResult

FALLBACK = {
    "Technology": {"days": 30, "model": "MC"},
    "Healthcare": {"days": 90, "model": "MC"},
    "Financial Services": {"days": 60, "model": "ARIMA"},
    "Real Estate": {"days": 90, "model": "HW"},
}


# ---------------------------------------------------------------------------
# validate_sector_config_entry
# ---------------------------------------------------------------------------


class TestValidateSectorConfigEntry:
    def test_valid_entry_passes_and_normalizes(self):
        result = validate_sector_config_entry({"days": 30, "model": "MC"})
        assert result == {"days": 30, "model": "MC"}

    def test_valid_entry_all_models_and_horizons(self):
        for days in (30, 60, 90):
            for model in ("MC", "ARIMA", "HW"):
                assert validate_sector_config_entry({"days": days, "model": model}) == {
                    "days": days,
                    "model": model,
                }

    def test_rejects_invalid_days(self):
        assert validate_sector_config_entry({"days": 45, "model": "MC"}) is None

    def test_rejects_invalid_model(self):
        assert validate_sector_config_entry({"days": 30, "model": "LSTM"}) is None

    def test_rejects_non_dict_none(self):
        assert validate_sector_config_entry(None) is None

    def test_rejects_non_dict_string(self):
        assert validate_sector_config_entry("foo") is None

    def test_rejects_non_dict_int(self):
        assert validate_sector_config_entry(123) is None

    def test_rejects_non_dict_list(self):
        assert validate_sector_config_entry([]) is None

    def test_rejects_missing_model_key(self):
        assert validate_sector_config_entry({"days": 30}) is None

    def test_rejects_missing_days_key(self):
        assert validate_sector_config_entry({"model": "MC"}) is None

    def test_rejects_string_days_no_coercion(self):
        # Design choice: REJECT wrongly-typed values rather than coerce.
        # A string "30" is not accepted as the int 30 -- see the module
        # docstring rationale (shared validator between the runtime loader
        # and settings.py's pydantic field_validator; silent coercion could
        # mask a malformed .env override, whereas rejecting + falling back
        # to the caller's default is always safe).
        assert validate_sector_config_entry({"days": "30", "model": "MC"}) is None

    def test_rejects_bool_days(self):
        # bool is a subclass of int in Python -- must be explicitly rejected.
        assert validate_sector_config_entry({"days": True, "model": "MC"}) is None

    def test_rejects_non_string_model(self):
        assert validate_sector_config_entry({"days": 30, "model": 123}) is None

    def test_never_raises_on_weird_getitem(self):
        class Explodes:
            def __contains__(self, key):
                return True

            def __getitem__(self, key):
                raise RuntimeError("boom")

        assert validate_sector_config_entry(Explodes()) is None


# ---------------------------------------------------------------------------
# derive_sector_configs
# ---------------------------------------------------------------------------


def _cell(sector, model, horizon, mase, rmse, n_forecasts=50, n_symbols=10):
    return CellResult(
        sector=sector,
        model=model,
        horizon=horizon,
        mase=mase,
        rmse=rmse,
        n_forecasts=n_forecasts,
        n_symbols=n_symbols,
    )


class TestDeriveSectorConfigs:
    def test_unambiguous_argmin_mase(self):
        results = [
            _cell("Technology", "MC", 30, mase=0.8, rmse=1.0),
            _cell("Technology", "ARIMA", 60, mase=1.2, rmse=0.5),
            _cell("Energy", "HW", 90, mase=0.5, rmse=2.0),
            _cell("Energy", "MC", 30, mase=0.9, rmse=0.1),
        ]
        out = derive_sector_configs(results, fallback={}, min_forecasts=30)
        assert out["Technology"] == {"days": 30, "model": "MC"}
        assert out["Energy"] == {"days": 90, "model": "HW"}

    def test_rmse_breaks_near_mase_tie(self):
        results = [
            _cell("Technology", "MC", 30, mase=1.000001, rmse=2.0),
            _cell("Technology", "ARIMA", 60, mase=1.000001, rmse=1.0),
        ]
        out = derive_sector_configs(results, fallback={}, min_forecasts=30)
        assert out["Technology"] == {"days": 60, "model": "ARIMA"}

    def test_horizon_breaks_mase_and_rmse_tie(self):
        results = [
            _cell("Technology", "MC", 90, mase=1.0, rmse=1.0),
            _cell("Technology", "ARIMA", 30, mase=1.0, rmse=1.0),
            _cell("Technology", "HW", 60, mase=1.0, rmse=1.0),
        ]
        out = derive_sector_configs(results, fallback={}, min_forecasts=30)
        # Lowest horizon (30) should win the three-way tie.
        assert out["Technology"] == {"days": 30, "model": "ARIMA"}

    def test_sector_below_min_forecasts_falls_back(self):
        results = [
            _cell("Technology", "MC", 30, mase=0.1, rmse=0.1, n_forecasts=5),
        ]
        out = derive_sector_configs(results, fallback=FALLBACK, min_forecasts=30)
        assert out["Technology"] == FALLBACK["Technology"]

    def test_sector_absent_from_results_falls_back(self):
        results = [
            _cell("Technology", "MC", 30, mase=0.1, rmse=0.1, n_forecasts=50),
        ]
        out = derive_sector_configs(results, fallback=FALLBACK, min_forecasts=30)
        assert out["Healthcare"] == FALLBACK["Healthcare"]
        assert out["Financial Services"] == FALLBACK["Financial Services"]
        assert out["Real Estate"] == FALLBACK["Real Estate"]

    def test_all_nan_mase_falls_back(self):
        results = [
            _cell("Technology", "MC", 30, mase=float("nan"), rmse=1.0, n_forecasts=50),
            _cell("Technology", "ARIMA", 60, mase=float("nan"), rmse=float("nan"), n_forecasts=50),
        ]
        out = derive_sector_configs(results, fallback=FALLBACK, min_forecasts=30)
        assert out["Technology"] == FALLBACK["Technology"]

    def test_sector_with_no_fallback_and_no_qualifying_cell_is_omitted(self):
        results = [
            _cell("Utilities", "MC", 30, mase=0.5, rmse=0.5, n_forecasts=5),  # below min_forecasts
        ]
        out = derive_sector_configs(results, fallback=FALLBACK, min_forecasts=30)
        assert "Utilities" not in out

    def test_union_of_sectors_from_results_and_fallback(self):
        results = [
            _cell("Energy", "HW", 90, mase=0.5, rmse=2.0, n_forecasts=50),
        ]
        out = derive_sector_configs(results, fallback=FALLBACK, min_forecasts=30)
        # Energy came only from results, Technology/Healthcare/etc only from fallback.
        assert set(out.keys()) == {"Energy"} | set(FALLBACK.keys())


# ---------------------------------------------------------------------------
# build_artifact / write_artifact
# ---------------------------------------------------------------------------


class TestBuildAndWriteArtifact:
    def _sample(self):
        results = [
            _cell("Technology", "MC", 30, mase=0.8, rmse=1.0, n_forecasts=50, n_symbols=12),
            _cell("Energy", "HW", 90, mase=0.5, rmse=2.0, n_forecasts=40, n_symbols=8),
        ]
        derived = derive_sector_configs(results, fallback=FALLBACK, min_forecasts=30)
        config = BacktestConfig()
        population_meta = {"n_symbols": 500, "source": "forecasting/data/ticker_sectors.csv@deadbeef"}
        return results, derived, config, population_meta

    def test_artifact_shape(self, tmp_path):
        results, derived, config, population_meta = self._sample()
        artifact = build_artifact(results, derived, config, population_meta)

        assert artifact["schema_version"] == SCHEMA_VERSION
        assert isinstance(artifact["generated_at"], str) and artifact["generated_at"]
        assert isinstance(artifact["backtest"], dict)
        assert artifact["backtest"]["method"] == "expanding_window_walk_forward"
        assert artifact["backtest"]["models"] == list(config.models)
        assert artifact["backtest"]["horizons"] == list(config.horizons)
        assert artifact["backtest"]["n_symbols"] == 500
        assert isinstance(artifact["sector_configs"], dict)
        assert artifact["sector_configs"]["Technology"] == {"days": 30, "model": "MC"}
        assert isinstance(artifact["grid"], list)
        assert artifact["grid"][0]["sector"] == "Technology"
        assert artifact["grid"][0]["model"] == "MC"
        assert artifact["grid"][0]["horizon"] == 30
        assert artifact["grid"][0]["mase"] == 0.8

    def test_write_artifact_round_trip(self, tmp_path):
        results, derived, config, population_meta = self._sample()
        artifact = build_artifact(results, derived, config, population_meta)

        out_path = tmp_path / "nested" / "sector_configs.json"
        write_artifact(out_path, artifact)

        assert out_path.exists()
        raw_text = out_path.read_text(encoding="utf-8")
        assert raw_text.endswith("\n")

        reloaded = json.loads(raw_text)
        assert reloaded["schema_version"] == SCHEMA_VERSION
        assert reloaded["sector_configs"] == artifact["sector_configs"]
        assert reloaded["grid"] == artifact["grid"]

    def test_two_runs_produce_identical_data_payload(self, tmp_path):
        results, derived, config, population_meta = self._sample()

        artifact_1 = build_artifact(results, derived, config, population_meta)
        artifact_2 = build_artifact(results, derived, config, population_meta)

        path_1 = tmp_path / "artifact_1.json"
        path_2 = tmp_path / "artifact_2.json"
        write_artifact(path_1, artifact_1)
        write_artifact(path_2, artifact_2)

        reloaded_1 = json.loads(path_1.read_text(encoding="utf-8"))
        reloaded_2 = json.loads(path_2.read_text(encoding="utf-8"))

        # generated_at may legitimately differ (non-deterministic timestamp);
        # everything else must be byte-for-byte reproducible.
        assert reloaded_1["sector_configs"] == reloaded_2["sector_configs"]
        assert reloaded_1["grid"] == reloaded_2["grid"]
        assert reloaded_1["backtest"] == reloaded_2["backtest"]
        assert reloaded_1["schema_version"] == reloaded_2["schema_version"]


# ---------------------------------------------------------------------------
# load_sector_configs
# ---------------------------------------------------------------------------


class TestLoadSectorConfigs:
    def test_path_none_returns_fallback_copy(self):
        out = load_sector_configs(None, FALLBACK)
        assert out == FALLBACK
        assert out is not FALLBACK  # must be a copy

    def test_nonexistent_path_returns_fallback(self, tmp_path):
        missing = tmp_path / "does_not_exist.json"
        out = load_sector_configs(missing, FALLBACK)
        assert out == FALLBACK

    def test_invalid_json_returns_fallback_no_raise(self, tmp_path):
        bad_file = tmp_path / "corrupt.json"
        bad_file.write_text("{not valid json!!!", encoding="utf-8")
        out = load_sector_configs(bad_file, FALLBACK)
        assert out == FALLBACK

    def test_valid_json_missing_sector_configs_key_returns_fallback(self, tmp_path):
        f = tmp_path / "no_sector_configs.json"
        f.write_text(json.dumps({"schema_version": 1, "backtest": {}, "grid": []}), encoding="utf-8")
        out = load_sector_configs(f, FALLBACK)
        assert out == FALLBACK

    def test_valid_artifact_overlays_and_preserves_untouched_sectors(self, tmp_path):
        results = [
            _cell("Technology", "ARIMA", 60, mase=0.3, rmse=0.4, n_forecasts=50, n_symbols=10),
        ]
        derived = {"Technology": {"days": 60, "model": "ARIMA"}}
        config = BacktestConfig()
        artifact = build_artifact(results, derived, config, {"n_symbols": 500, "source": "x"})
        path = tmp_path / "sector_configs.json"
        write_artifact(path, artifact)

        out = load_sector_configs(path, FALLBACK)
        # Technology overridden by the artifact.
        assert out["Technology"] == {"days": 60, "model": "ARIMA"}
        # Sectors absent from the artifact keep the fallback value.
        assert out["Healthcare"] == FALLBACK["Healthcare"]
        assert out["Financial Services"] == FALLBACK["Financial Services"]
        assert out["Real Estate"] == FALLBACK["Real Estate"]

    def test_overrides_take_final_precedence_over_artifact(self, tmp_path):
        results = [
            _cell("Technology", "ARIMA", 60, mase=0.3, rmse=0.4, n_forecasts=50, n_symbols=10),
        ]
        derived = {"Technology": {"days": 60, "model": "ARIMA"}}
        config = BacktestConfig()
        artifact = build_artifact(results, derived, config, {"n_symbols": 500, "source": "x"})
        path = tmp_path / "sector_configs.json"
        write_artifact(path, artifact)

        overrides = {"Technology": {"days": 90, "model": "HW"}}
        out = load_sector_configs(path, FALLBACK, overrides=overrides)
        assert out["Technology"] == {"days": 90, "model": "HW"}

    def test_overrides_alone_apply_without_artifact(self):
        overrides = {"Technology": {"days": 90, "model": "HW"}}
        out = load_sector_configs(None, FALLBACK, overrides=overrides)
        assert out["Technology"] == {"days": 90, "model": "HW"}
        assert out["Healthcare"] == FALLBACK["Healthcare"]

    def test_invalid_override_entry_is_skipped(self):
        overrides = {"Technology": {"days": 999, "model": "MC"}}
        out = load_sector_configs(None, FALLBACK, overrides=overrides)
        assert out["Technology"] == FALLBACK["Technology"]

    def test_partial_artifact_corruption_does_not_discard_whole_overlay(self, tmp_path):
        # One sector's entry is malformed; the other sector's valid entry in
        # the same artifact must still be applied.
        raw_artifact = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": "2026-01-01T00:00:00+00:00",
            "backtest": {},
            "sector_configs": {
                "Technology": {"days": 999, "model": "MC"},  # malformed
                "Energy": {"days": 60, "model": "ARIMA"},  # valid, not in FALLBACK
            },
            "grid": [],
        }
        path = tmp_path / "partial_corrupt.json"
        path.write_text(json.dumps(raw_artifact), encoding="utf-8")

        out = load_sector_configs(path, FALLBACK)

        # Technology falls back since its artifact entry was malformed.
        assert out["Technology"] == FALLBACK["Technology"]
        # Energy (new sector, valid entry) is applied from the artifact.
        assert out["Energy"] == {"days": 60, "model": "ARIMA"}
        # Untouched fallback sectors remain.
        assert out["Healthcare"] == FALLBACK["Healthcare"]

    def test_never_raises_on_garbage_path_type(self):
        # Passing a clearly wrong type for path must not raise.
        out = load_sector_configs(12345, FALLBACK)
        assert out == FALLBACK

    def test_never_raises_on_garbage_overrides_type(self):
        # overrides is not a mapping of mappings -- values are garbage.
        out = load_sector_configs(None, FALLBACK, overrides={"Technology": "garbage"})
        assert out["Technology"] == FALLBACK["Technology"]

    def test_does_not_mutate_fallback_input(self, tmp_path):
        fallback_copy = dict(FALLBACK)
        overrides = {"Technology": {"days": 90, "model": "HW"}}
        load_sector_configs(None, fallback_copy, overrides=overrides)
        assert fallback_copy == FALLBACK
