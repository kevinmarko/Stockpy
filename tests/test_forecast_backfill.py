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

from ml.forecast_backfill import AgenticForecastBackfiller
from settings import settings


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
