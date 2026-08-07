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

import pilots.watchlist_writer as watchlist_writer
from ml.backfill.GlobalBackfillEngine import GlobalBackfillEngine
from settings import settings


@pytest.fixture(autouse=True)
def _isolate_output_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(watchlist_writer, "DEFAULT_WATCHLIST_PATH", tmp_path / "watchlist.txt")


@pytest.mark.anyio
@pytest.mark.network
async def test_global_backfill_engine_end_to_end():
    from ml.backfill.registry import backfill_engine
    engine = backfill_engine
    
    async def _status_callback(task_id: str, status: str, progress: int, message: str):
        pass

    results = await engine.run_full_system_backfill(
        task_id="test_run", 
        status_callback=_status_callback,
        tickers=["AAPL"],
        start_date="2018-01-01",
        end_date="2022-01-01",
        use_fmp=False
    )

    assert "TSMOM" in results
    assert "CSMOM" in results

    tsmom_metrics = results["TSMOM"]
    assert len(tsmom_metrics) == 4
    for metric in tsmom_metrics:
        assert "accuracy" in metric
        assert "roc_auc" in metric
        assert "train_n" in metric

    # Verify that the summary JSON is written
    summary_path = settings.OUTPUT_DIR / "agentic_forecast_summary.json"
    assert summary_path.exists()
    
    with open(summary_path, "r") as f:
        summary = json.load(f)
        assert summary["status"] == "completed"
        assert summary["metrics"] == results
        assert summary["tickers"] == ["AAPL"]


def test_api_backfill_run_endpoint(monkeypatch):
    from unittest import mock
    from fastapi.testclient import TestClient
    from api.pilots_api import app
    from settings import settings as live_settings

    client = TestClient(app, client=("127.0.0.1", 50000))
    with mock.patch.object(live_settings, "FOLLOW_API_TOKEN", "cmd-tok"):
        res = client.post(
            "/api/backfill/run",
            headers={"Authorization": "Bearer cmd-tok"},
        )
    assert res.status_code == 200
    data = res.json()
    assert "job_id" in data
    assert data["status"] == "PENDING"
