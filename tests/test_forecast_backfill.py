"""
tests/test_forecast_backfill.py
================================
Unit tests for the Multi-Horizon Forecast Backfill & Meta-Labeling Engine.
"""

import json
from pathlib import Path
import pytest
import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier

from ml.forecast_backfill import AgenticForecastBackfiller
from settings import settings

# Module-level (not test-local) so it stays picklable -- step_5 persists every
# trained classifier via pickle.dump().
_captured_train_max_date: dict = {}


@pytest.fixture(autouse=True)
def _isolate_output_dir(tmp_path, monkeypatch):
    """Every test in this file that calls export_results() must never write
    into the real, operator-facing output/ directory. AgenticForecastBackfiller
    reads settings.OUTPUT_DIR live (not a cached module-level path), so
    monkeypatching it here is sufficient -- without this, running this file
    clobbers the live output/agentic_forecast_summary.json that
    GET /pilots/forecast_backfill serves verbatim, which is exactly how a
    ZZZZ_NOT_REAL synthetic-fallback ticker used to leak into the webapp's
    Forecast Backfill screen after a local test run."""
    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)


class _RecordingClassifier(RandomForestClassifier):
    def fit(self, X, y):
        _captured_train_max_date["date"] = X.index.get_level_values("Date").max()
        return super().fit(X, y)


@pytest.mark.parametrize(
    "bad_horizon",
    [-1, 0, 3651, 1.5, "10", True, "../../etc/passwd"],
)
def test_backfiller_rejects_invalid_horizons(bad_horizon):
    """`horizons` ends up in a model filename that gets opened for writing
    (ml/forecast_backfill.py's meta_{model_type}_{h}d.pkl) -- CodeQL flagged
    this as uncontrolled data in a path expression, since it is reachable
    from POST /pilots/forecast_backfill/run's request body. Every horizon
    must be constrained to a small positive int before it ever reaches a
    path, regardless of caller (API, CLI script, or direct construction)."""
    with pytest.raises(ValueError):
        AgenticForecastBackfiller(horizons=[10, bad_horizon])


def test_backfiller_initialization():
    """Verify parameters are loaded from settings defaults with zero hardcoded values."""
    engine = AgenticForecastBackfiller()
    assert engine.horizons == settings.FORECAST_BACKFILL_HORIZONS
    assert engine.momentum_window == settings.FORECAST_BACKFILL_MOMENTUM_WINDOW
    assert engine.vol_short_window == settings.FORECAST_BACKFILL_VOL_SHORT_WINDOW
    assert engine.vol_long_window == settings.FORECAST_BACKFILL_VOL_LONG_WINDOW
    assert engine.rsi_window == settings.FORECAST_BACKFILL_RSI_WINDOW
    assert engine.macd_fast == settings.FORECAST_BACKFILL_MACD_FAST
    assert engine.macd_slow == settings.FORECAST_BACKFILL_MACD_SLOW
    assert engine.vol_ratio_window == settings.FORECAST_BACKFILL_VOL_RATIO_WINDOW
    assert engine.train_split == settings.FORECAST_BACKFILL_TRAIN_SPLIT
    assert engine.n_estimators == settings.FORECAST_BACKFILL_N_ESTIMATORS
    assert engine.max_depth == settings.FORECAST_BACKFILL_MAX_DEPTH


def test_forecast_backfill_end_to_end_pipeline(tmp_path):
    """Test full 6-step forecast backfill pipeline using synthetic data."""
    tickers = ["AAPL", "MSFT", "AMZN", "NVDA"]
    horizons = [10, 30, 60, 90]

    engine = AgenticForecastBackfiller(
        tickers=tickers,
        start_date="2018-01-01",
        end_date="2022-01-01",
        horizons=horizons,
        n_estimators=10,
        max_depth=3,
        use_fmp=False,  # force synthetic/fallback
    )

    # Step 1: Data fetching
    prices = engine.step_1_fetch_data()
    assert not prices.empty
    assert set(tickers).issubset(set(prices.columns))

    # Step 2: Technical features
    features = engine.step_2_calculate_technical_features()
    assert not features.empty
    for col in ["Vol_20", "Vol_50", "RSI_14", "MACD", "Vol_Ratio"]:
        assert col in features.columns

    # Step 3: Primary signals
    signals = engine.step_3_generate_primary_signals()
    assert "TSMOM_Signal" in signals.columns
    assert "CSMOM_Signal" in signals.columns
    assert set(signals["TSMOM_Signal"].dropna().unique()).issubset({-1.0, 1.0})

    # Step 4: Meta-targets
    targets = engine.step_4_create_meta_targets()
    for h in horizons:
        assert f"TSMOM_Target_{h}d" in targets.columns
        assert f"CSMOM_Target_{h}d" in targets.columns

    # Step 5: Backtrain meta labelers
    metrics = engine.step_5_backtrain_meta_labelers()
    assert len(metrics) == 8  # 2 models x 4 horizons
    for model_key, m in metrics.items():
        assert "accuracy" in m
        assert "auc" in m
        assert "n_train" in m

    # Step 6: Continuous inference backfill
    backfill_df = engine.step_6_execute_backfill()
    for h in horizons:
        assert f"TSMOM_Meta_Prob_{h}d" in backfill_df.columns
        assert f"CSMOM_Meta_Prob_{h}d" in backfill_df.columns

    # Export results
    out_df, summary = engine.export_results(filename="test_backfill_output.csv")
    assert not out_df.empty
    assert summary["total_rows"] == len(out_df)
    assert len(summary["metrics"]) == 8


def test_step_6_no_model_produces_nan_not_fabricated_confidence():
    """A horizon/model that never trained (e.g. insufficient samples) must
    leave its Meta_Prob column as NaN, never a fabricated placeholder like
    1.0 (CONSTRAINT #4) -- a fake 100%-confidence value would otherwise be
    indistinguishable from a genuine, trained prediction downstream."""
    engine = AgenticForecastBackfiller(
        tickers=["AAPL", "MSFT"],
        start_date="2018-01-01",
        end_date="2022-01-01",
        horizons=[10],
        use_fmp=False,
    )
    engine.step_1_fetch_data()
    engine.step_2_calculate_technical_features()
    engine.step_3_generate_primary_signals()
    engine.step_4_create_meta_targets()
    engine.step_5_backtrain_meta_labelers()

    # Simulate "no model trained for this horizon" (e.g. too few samples).
    engine.models.pop("TSMOM_10d", None)
    engine.step_6_execute_backfill()

    assert engine.data["TSMOM_Meta_Prob_10d"].isna().all()
    assert not (engine.data["TSMOM_Meta_Prob_10d"] == 1.0).any()


def test_synthetic_fallback_is_flagged_not_silently_indistinguishable_from_real():
    """When neither FMP nor CompositeProvider returns data for a ticker, the
    substituted synthetic random-walk panel must be tracked and surfaced in
    the exported summary -- a provider outage must never look like a genuine
    backtest (CONSTRAINT #4)."""
    engine = AgenticForecastBackfiller(
        tickers=["ZZZZ_NOT_REAL"],
        start_date="2020-01-01",
        end_date="2022-01-01",
        horizons=[10],
        use_fmp=False,
    )
    engine.step_1_fetch_data()
    assert "ZZZZ_NOT_REAL" in engine.synthetic_tickers

    engine.step_2_calculate_technical_features()
    engine.step_3_generate_primary_signals()
    engine.step_4_create_meta_targets()
    engine.step_5_backtrain_meta_labelers()
    engine.step_6_execute_backfill()
    _, summary = engine.export_results(filename="test_synthetic_flag_output.csv")
    assert summary["synthetic_tickers"] == ["ZZZZ_NOT_REAL"]


def test_train_test_split_embargoes_overlapping_forward_window(monkeypatch):
    """The last `h` dates before the split boundary must be excluded from
    training, since their target label is derived from a forward return that
    extends past the boundary into the test period -- otherwise test-period
    price moves leak into training (the same overlapping-label leakage class
    validation/purged_cv.py and the CNN-LSTM purged split guard elsewhere in
    this codebase)."""
    import ml.forecast_backfill as fb_module

    horizon = 30
    engine = AgenticForecastBackfiller(
        tickers=["AAPL", "MSFT", "AMZN"],
        start_date="2018-01-01",
        end_date="2022-01-01",
        horizons=[horizon],
        use_fmp=False,
    )
    engine.step_1_fetch_data()
    engine.step_2_calculate_technical_features()
    engine.step_3_generate_primary_signals()
    engine.step_4_create_meta_targets()

    _captured_train_max_date.clear()
    monkeypatch.setattr(fb_module, "RandomForestClassifier", _RecordingClassifier)
    engine.step_5_backtrain_meta_labelers()

    target_col = "TSMOM_Target_30d"
    features = ["Vol_20", "Vol_50", "RSI_14", "MACD", "Vol_Ratio"]
    clean_df = engine.data.dropna(subset=features + [target_col])
    dates = clean_df.index.get_level_values("Date").unique().sort_values()
    split_idx = int(len(dates) * engine.train_split)
    split_date = dates[split_idx]

    # The recorded training set's latest date must be at least `horizon`
    # trading days before split_date -- i.e. its forward-return window never
    # crosses into the test period.
    assert _captured_train_max_date["date"] <= dates[max(0, split_idx - horizon)]
    assert _captured_train_max_date["date"] < split_date


def test_forecast_backfill_api_endpoint(monkeypatch, tmp_path):
    """Test API endpoint response from api.pilots_api."""
    from fastapi.testclient import TestClient
    from api.pilots_api import app

    client = TestClient(app, client=("127.0.0.1", 50000))

    # Mock output file
    summary_file = tmp_path / "agentic_forecast_summary.json"
    mock_payload = {
        "status": "completed",
        "horizons": [10, 30, 60, 90],
        "metrics": {"TSMOM_10d": {"accuracy": 0.52, "auc": 0.54, "n_train": 1000}},
        "tickers": ["AAPL", "MSFT"],
    }

    summary_file.write_text(json.dumps(mock_payload))
    monkeypatch.setattr("settings.settings.OUTPUT_DIR", tmp_path)

    res = client.get("/pilots/forecast_backfill")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "completed"
    assert data["horizons"] == [10, 30, 60, 90]


def test_forecast_backfill_run_endpoint_rejects_invalid_horizons(monkeypatch):
    """POST /pilots/forecast_backfill/run's `horizons` reaches a model
    filename that gets opened for writing -- must 422 (Pydantic validation),
    never reach AgenticForecastBackfiller, for an out-of-range or non-integer
    horizon (CodeQL: uncontrolled data in a path expression)."""
    from unittest import mock
    from fastapi.testclient import TestClient
    from api.pilots_api import app
    from settings import settings as live_settings

    client = TestClient(app, client=("127.0.0.1", 50000))
    with mock.patch.object(live_settings, "FOLLOW_API_TOKEN", "cmd-tok"):
        res = client.post(
            "/pilots/forecast_backfill/run",
            json={"horizons": [10, -1]},
            headers={"Authorization": "Bearer cmd-tok"},
        )
    assert res.status_code == 422
