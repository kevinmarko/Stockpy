from __future__ import annotations

import pandas as pd
import pytest

from pipeline.production_steps import _apply_google_trends_asvi
from settings import settings

def test_disabled_gate_leaves_nan(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_TRENDS_ENABLED", False)
    df = pd.DataFrame({"Symbol": ["AAPL", "MSFT"]})
    _apply_google_trends_asvi(df)
    assert "Google_Trends_ASVI" in df.columns
    assert df["Google_Trends_ASVI"].isna().all()

def test_empty_universe_leaves_nan(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_TRENDS_ENABLED", True)
    df = pd.DataFrame({"Symbol": []})
    _apply_google_trends_asvi(df)
    assert "Google_Trends_ASVI" in df.columns
    assert df["Google_Trends_ASVI"].isna().all()

def test_exception_degrades_to_nan(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_TRENDS_ENABLED", True)
    df = pd.DataFrame({"Symbol": ["AAPL"]})
    
    # Force an exception to simulate failure
    class MockTrendsStore:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("DB Connection Failed")
            
    import pipeline.production_steps
    monkeypatch.setattr(pipeline.production_steps, "TrendsStore", MockTrendsStore, raising=False)
    
    _apply_google_trends_asvi(df)
    assert "Google_Trends_ASVI" in df.columns
    assert df["Google_Trends_ASVI"].isna().all()

def test_normal_success_writes_values(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_TRENDS_ENABLED", True)
    df = pd.DataFrame({"Symbol": ["AAPL", "TSLA"]})
    
    class MockTrendsStore:
        def __init__(self, *args, **kwargs): pass
        def get_stitched_series(self, sym):
            if sym == "AAPL":
                return [{"date": "2026-01-01", "value": 10}, {"date": "2026-01-02", "value": 20}]
            return []
            
    class MockASVICalculator:
        @staticmethod
        def compute_asvi(svi_series):
            if len(svi_series) == 2:
                return pd.Series([0.5, 1.5], index=svi_series.index)
            return pd.Series()
            
    import pipeline.production_steps
    # Because _apply_google_trends_asvi imports them locally, we need to patch the actual module imports,
    # but the easiest is to patch the module sys.modules or just let it import and patch the classes there.
    # Wait, the function does `from data.trends_store import TrendsStore` locally.
    monkeypatch.setattr("data.trends_store.TrendsStore", MockTrendsStore)
    monkeypatch.setattr("data.trends_stitcher.ASVICalculator", MockASVICalculator)
    
    _apply_google_trends_asvi(df)
    
    assert df.loc[df["Symbol"] == "AAPL", "Google_Trends_ASVI"].iloc[0] == 1.5
    assert pd.isna(df.loc[df["Symbol"] == "TSLA", "Google_Trends_ASVI"].iloc[0])
