"""
tests/test_pilots_lstm_attention_forecast_sector_proxy.py
===========================================================
Regression test for `POST /pilots/ml/lstm-attention-forecast`'s sector-proxy
resolution (api/pilots_api.py::run_lstm_attention_forecast_endpoint).

Prior to this fix, the endpoint constructed `data.trends_stitcher.FMPDataLoader`
and called a `.get_fundamentals(symbol)` method that class never had (it only
implements `fetch_historical_ohlcv`/`compute_technical_indicators` for the
standalone SVI-stitching demo/tests) -- every real call raised `AttributeError`,
silently swallowed by a bare `except Exception: sector = None`, so
`resolve_sector_proxy` was unconditionally called with `None` and every real
forecast quietly used SPY as its sector proxy regardless of the target
symbol's actual sector. This test proves the real fundamentals provider
(`data.market_data.get_provider()`) is now used, and that a real sector value
resolves to its documented SPDR ETF proxy (Technology -> XLK), not a silent
SPY fallback.
"""

from __future__ import annotations

from unittest import mock

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

import api.pilots_api as pilots_api

client = TestClient(pilots_api.app, client=("127.0.0.1", 54123))


def _fake_bars(n: int = 400) -> pd.DataFrame:
    dates = pd.date_range("2023-01-01", periods=n, freq="D")
    rng = np.random.default_rng(42)
    close = 100.0 + np.cumsum(rng.normal(0, 1, n))
    return pd.DataFrame(
        {
            "Open": close,
            "High": close + 1,
            "Low": close - 1,
            "Close": close,
            "Volume": rng.integers(1_000_000, 5_000_000, n),
        },
        index=dates,
    )


class _FakeHistoricalStore:
    def __init__(self, *args, **kwargs):
        pass

    def get_bars(self, symbol: str, lookback_days: int = 1095) -> pd.DataFrame:
        return _fake_bars()


class _FakeTrendsStore:
    def __init__(self, *args, **kwargs):
        pass

    def get_stitched_series(self, query_term: str):
        return None


def _fake_lstm_diagnostic(**kwargs):
    return {"prediction": 0.01, "attention_weights": [0.1, 0.2, 0.7]}


class TestLstmAttentionForecastSectorProxy:
    def test_real_sector_resolves_to_documented_etf_not_a_silent_spy_fallback(self):
        """A real 'Technology' sector from the fundamentals provider must
        resolve to XLK (per ml/asvi_feature_engineering.py's documented
        mapping) -- not silently collapse to SPY via a swallowed AttributeError."""
        fake_provider = mock.Mock()
        fake_provider.get_fundamentals.return_value = {"sector": "Technology"}

        with mock.patch("api.pilots_api.get_provider", return_value=fake_provider), \
             mock.patch("data.historical_store.HistoricalStore", _FakeHistoricalStore), \
             mock.patch("data.trends_store.TrendsStore", _FakeTrendsStore), \
             mock.patch("pilots.lstm_diagnostic.run_lstm_diagnostic", side_effect=_fake_lstm_diagnostic):
            resp = client.post(
                "/pilots/ml/lstm-attention-forecast", params={"symbol": "AAPL"}
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["sector_proxy_used"] == "XLK"
        # The real fundamentals provider was actually consulted -- not the
        # broken FMPDataLoader.get_fundamentals path this regression guards
        # against.
        fake_provider.get_fundamentals.assert_called_once_with("AAPL")

    def test_unresolvable_sector_degrades_to_spy_honestly(self):
        """A provider failure (or a genuinely unmapped/missing sector) must
        still degrade to the documented SPY fallback -- CONSTRAINT #6, never
        an unhandled 500."""
        fake_provider = mock.Mock()
        fake_provider.get_fundamentals.side_effect = RuntimeError("provider down")

        with mock.patch("api.pilots_api.get_provider", return_value=fake_provider), \
             mock.patch("data.historical_store.HistoricalStore", _FakeHistoricalStore), \
             mock.patch("data.trends_store.TrendsStore", _FakeTrendsStore), \
             mock.patch("pilots.lstm_diagnostic.run_lstm_diagnostic", side_effect=_fake_lstm_diagnostic):
            resp = client.post(
                "/pilots/ml/lstm-attention-forecast", params={"symbol": "AAPL"}
            )

        assert resp.status_code == 200, resp.text
        assert resp.json()["sector_proxy_used"] == "SPY"

    def test_no_longer_imports_the_nonexistent_fmpdataloader_get_fundamentals_path(self):
        """Regression guard for the exact bug: data.trends_stitcher.FMPDataLoader
        has no get_fundamentals method, and must no longer be constructed by
        this endpoint at all."""
        import inspect

        source = inspect.getsource(pilots_api.run_lstm_attention_forecast_endpoint)
        assert "from data.trends_stitcher import FMPDataLoader" not in source
        assert "FMPDataLoader()" not in source
        assert "get_provider().get_fundamentals" in source
