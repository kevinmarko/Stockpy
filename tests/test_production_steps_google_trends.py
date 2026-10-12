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
            
    # _apply_google_trends_asvi does a LOCAL `from data.trends_store import
    # TrendsStore` inside its own body, so patching the name onto
    # pipeline.production_steps's module namespace has no effect -- the local
    # import re-resolves the name fresh from data.trends_store every call.
    # Patch the real source module instead (matches
    # test_normal_success_writes_values below).
    monkeypatch.setattr("data.trends_store.TrendsStore", MockTrendsStore)

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


def _store_returning(rows_by_sym):
    class _Store:
        def __init__(self, *args, **kwargs):
            pass

        def get_stitched_series(self, sym):
            return rows_by_sym.get(sym, [])

    return _Store


def test_dashboard_value_is_last_row_of_real_asvi(monkeypatch):
    """With the real ASVICalculator, the dashboard value is the ASVI of the
    last stored day (the daemon no longer stores the partial current day, so
    that is the last complete day)."""
    from data.trends_stitcher import ASVICalculator

    monkeypatch.setattr(settings, "GOOGLE_TRENDS_ENABLED", True)
    dates = pd.date_range("2026-06-01", periods=120, freq="D")
    values = [40.0 + (i % 9) for i in range(120)]
    rows = [{"date": d.date(), "value": v} for d, v in zip(dates, values)]
    monkeypatch.setattr("data.trends_store.TrendsStore", _store_returning({"AAL": rows}))

    df = pd.DataFrame({"Symbol": ["AAL"]})
    _apply_google_trends_asvi(df)

    expected = ASVICalculator.compute_asvi(pd.Series(values, index=[r["date"] for r in rows])).iloc[-1]
    assert df["Google_Trends_ASVI"].iloc[0] == pytest.approx(expected, abs=0)


def test_series_shorter_than_warmup_yields_nan_not_a_made_up_value(monkeypatch):
    """ASVI warm-up rows are NaN, so a term with only a few stored days has
    no ASVI rather than one computed from a forward-looking baseline."""
    monkeypatch.setattr(settings, "GOOGLE_TRENDS_ENABLED", True)
    rows = [{"date": d.date(), "value": 50.0} for d in pd.date_range("2026-06-01", periods=5, freq="D")]
    monkeypatch.setattr("data.trends_store.TrendsStore", _store_returning({"AAL": rows}))

    df = pd.DataFrame({"Symbol": ["AAL"]})
    _apply_google_trends_asvi(df)
    assert pd.isna(df["Google_Trends_ASVI"].iloc[0])
