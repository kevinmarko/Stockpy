"""
tests/test_metrics_api.py
=========================
Fully-offline tests for the standalone ``api/metrics_api.py`` FastAPI service
(port 8604). Bars are synthesized (no live fetch); the fast engines
(ProcessingEngine, SignalAggregator) run for real to PROVE the fixed engine
calls (``{symbol: df}`` dict, ``res[symbol]`` read, ``SignalAggregator``-based
per-module breakdown), while the heavy/slow engines (ForecastingEngine,
``build_premium_directive``) are mocked for determinism.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from settings import settings
import api.metrics_api as metrics_api
from data.market_data import MarketDataError

client = TestClient(metrics_api.app)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _synthetic_bars(n: int = 260) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    rng = np.random.default_rng(0)
    close = 100 + np.cumsum(rng.normal(0.1, 1.0, n))
    return pd.DataFrame(
        {
            "Open": close,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": [1_000_000.0] * n,
        },
        index=idx,
    )


class _FakeProvider:
    def __init__(self, fundamentals=None, quote=None):
        self._fundamentals = fundamentals if fundamentals is not None else {
            "trailingPE": 25.0,
            "sector": "Technology",
            "returnOnEquity": 0.30,
        }
        self._quote = quote

    def get_fundamentals(self, symbol):
        return self._fundamentals

    def get_latest_quote(self, symbol):
        if self._quote is None:
            raise MarketDataError("no quote")
        return self._quote


def _quote(price=105.0):
    return SimpleNamespace(
        symbol="AAPL",
        price=price,
        bid=price - 0.1,
        ask=price + 0.1,
        timestamp=datetime.now(timezone.utc),
        is_stale=True,
        source="test",
    )


@pytest.fixture
def bars_and_provider(monkeypatch):
    """Point _fetch_bars at synthetic bars and get_provider at a fake provider."""
    bars = _synthetic_bars()
    monkeypatch.setattr(metrics_api, "_fetch_bars", lambda sym, lb: bars)
    provider = _FakeProvider(quote=_quote())
    monkeypatch.setattr(metrics_api, "get_provider", lambda: provider)
    return bars, provider


# ---------------------------------------------------------------------------
# /health + auth
# ---------------------------------------------------------------------------


def test_health_open_no_auth():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "metrics_api"}


def test_401_with_wrong_token():
    with mock.patch.object(settings, "STATE_API_TOKEN", "secret"):
        resp = client.get("/metrics/technicals/AAPL", headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /metrics/technicals/{symbol}  (real ProcessingEngine)
# ---------------------------------------------------------------------------


def test_technicals_real_last_row_dict(bars_and_provider):
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/metrics/technicals/AAPL")
    assert resp.status_code == 200
    body = resp.json()
    # Real ProcessingEngine last-row indicator dict — proves {symbol: df} + res[symbol].
    assert isinstance(body, dict)
    assert "RSI" in body and "ATR" in body and "MACD_Line" in body


def test_technicals_404_no_bars(monkeypatch):
    monkeypatch.setattr(metrics_api, "_fetch_bars", lambda sym, lb: None)
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/metrics/technicals/ZZZZ")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /metrics/forecast/{symbol}  (ForecastingEngine mocked for speed)
# ---------------------------------------------------------------------------


def test_forecast_shape_and_call_signature(monkeypatch):
    bars = _synthetic_bars()
    monkeypatch.setattr(metrics_api, "_fetch_bars", lambda sym, lb: bars)
    monkeypatch.setattr(metrics_api, "get_provider", lambda: _FakeProvider(quote=_quote()))

    captured = {}

    class _FakeFE:
        def generate_forecast(self, row, current_price, history_series=None, history_df=None, **kw):
            captured["row"] = row
            captured["current_price"] = current_price
            captured["history_df"] = history_df
            return {"Forecast_30": 110.0, "MC_Upper": float("nan")}

    monkeypatch.setattr(metrics_api, "ForecastingEngine", _FakeFE)
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/metrics/forecast/AAPL")
    assert resp.status_code == 200
    body = resp.json()
    assert body["Forecast_30"] == 110.0
    assert body["MC_Upper"] is None  # NaN → null
    # Proves the FIX: row is a pd.Series (not tech_df.iloc[-1]) + real history_df passed.
    assert isinstance(captured["row"], pd.Series)
    assert captured["history_df"] is bars
    assert captured["current_price"] == 105.0


def test_forecast_404_no_bars(monkeypatch):
    monkeypatch.setattr(metrics_api, "_fetch_bars", lambda sym, lb: None)
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/metrics/forecast/ZZZZ")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /metrics/options/{symbol}  (build_premium_directive mocked)
# ---------------------------------------------------------------------------


def test_options_uses_build_premium_directive(monkeypatch):
    bars = _synthetic_bars()
    monkeypatch.setattr(metrics_api, "_fetch_bars", lambda sym, lb: bars)
    monkeypatch.setattr(metrics_api, "get_provider", lambda: _FakeProvider(quote=_quote()))

    captured = {}

    def _fake_directive(symbol, df, *, spot_price, is_stale=False, **kw):
        captured["symbol"] = symbol
        captured["spot_price"] = spot_price
        return {"Strategy": "Put Credit Spread", "Net_Premium": 1.25, "ATM_Vega": float("nan")}

    monkeypatch.setattr(metrics_api, "build_premium_directive", _fake_directive)
    monkeypatch.setattr(
        metrics_api, "validate_directive_integrity",
        lambda d: {"ok": True, "issues": []},
    )
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/metrics/options/AAPL")
    assert resp.status_code == 200
    body = resp.json()
    assert body["Strategy"] == "Put Credit Spread"
    assert body["ATM_Vega"] is None  # NaN → null
    assert body["Integrity_OK"] is True
    assert captured["symbol"] == "AAPL"
    assert captured["spot_price"] == 105.0


def test_options_404_no_bars(monkeypatch):
    monkeypatch.setattr(metrics_api, "_fetch_bars", lambda sym, lb: None)
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/metrics/options/ZZZZ")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /metrics/signals/registry  (real registry)
# ---------------------------------------------------------------------------


def test_signal_registry_real_fields():
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/metrics/signals/registry")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == len(body["registry"])
    assert body["count"] > 0
    entry = body["registry"][0]
    # Only real fields — SignalModule has no signal_type/description.
    assert set(entry.keys()) == {"id", "weight", "disabled"}
    assert isinstance(entry["id"], str)


# ---------------------------------------------------------------------------
# GET /metrics/signals/{symbol}  (advisory mocked, real aggregator)
# ---------------------------------------------------------------------------


def test_symbol_signals_breakdown_shape(monkeypatch):
    bars = _synthetic_bars()
    monkeypatch.setattr(metrics_api, "_fetch_bars", lambda sym, lb: bars)
    monkeypatch.setattr(metrics_api, "get_provider", lambda: _FakeProvider(quote=_quote()))
    # advisory.evaluate is authoritative for action + conviction — stub it.
    monkeypatch.setattr(
        metrics_api.engine.advisory, "evaluate",
        lambda **kw: SimpleNamespace(action="BUY", conviction=0.7),
    )
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/metrics/signals/AAPL")
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "AAPL"
    assert body["action"] == "BUY"
    assert body["conviction"] == 0.7
    assert isinstance(body["final_score"], int)
    assert isinstance(body["modules"], list) and len(body["modules"]) > 0
    m0 = body["modules"][0]
    # Frozen module shape — proves Recommendation.score/.signals were NOT used.
    assert set(m0.keys()) == {"name", "score", "weight", "contribution"}


def test_symbol_signals_no_bars_empty_modules(monkeypatch):
    monkeypatch.setattr(metrics_api, "_fetch_bars", lambda sym, lb: None)
    monkeypatch.setattr(metrics_api, "get_provider", lambda: _FakeProvider())
    monkeypatch.setattr(
        metrics_api.engine.advisory, "evaluate",
        lambda **kw: SimpleNamespace(action="HOLD", conviction=0.55),
    )
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/metrics/signals/ZZZZ")
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "HOLD"
    assert body["final_score"] is None  # honest: not computable → null
    assert body["modules"] == []


# ---------------------------------------------------------------------------
# GET /metrics/sentiment/{symbol}  (SentimentRiskEngine mocked for determinism)
# ---------------------------------------------------------------------------


class _FakeSentimentEngine:
    """Stand-in for SentimentRiskEngine — returns a canned SentimentResult."""

    def __init__(self, result):
        self._result = result

    async def get_live_sentiment(self, ticker, date, returns):
        return self._result


def test_sentiment_unavailable_returns_honest_200_not_exception(monkeypatch):
    """Agent unavailable is a legitimate, expected state (matching the
    api/pilots_api.py cold-start-degrades-to-honest-empty-shape convention):
    an honest 200 with null sentiment fields + source, NEVER an HTTPException."""
    from sentiment_risk_engine import SentimentResult

    bars = _synthetic_bars()
    monkeypatch.setattr(metrics_api, "_fetch_bars", lambda sym, lb: bars)

    canned = SentimentResult(
        ticker="AAPL",
        date=datetime(2026, 7, 20, tzinfo=timezone.utc),
        sentiment_score=None,
        sentiment_intensity=None,
        credibility_score=None,
        # Independent GARCH computation — can be real even when the agent
        # itself is unavailable.
        volatility_persistence=0.93,
        source="unavailable",
    )
    monkeypatch.setattr(metrics_api, "SentimentRiskEngine", lambda: _FakeSentimentEngine(canned))

    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/metrics/sentiment/AAPL")
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "unavailable"
    assert body["sentiment_score"] is None
    assert body["sentiment_intensity"] is None
    assert body["credibility_score"] is None
    assert body["volatility_persistence"] == 0.93


def test_sentiment_agent_success_returns_populated_shape(monkeypatch):
    from sentiment_risk_engine import SentimentResult

    bars = _synthetic_bars()
    monkeypatch.setattr(metrics_api, "_fetch_bars", lambda sym, lb: bars)

    canned = SentimentResult(
        ticker="AAPL",
        date=datetime(2026, 7, 20, tzinfo=timezone.utc),
        sentiment_score=0.4,
        sentiment_intensity=0.7,
        credibility_score=0.85,
        volatility_persistence=0.9,
        source="antigravity_agent",
    )
    monkeypatch.setattr(metrics_api, "SentimentRiskEngine", lambda: _FakeSentimentEngine(canned))

    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/metrics/sentiment/AAPL")
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "antigravity_agent"
    assert body["sentiment_score"] == 0.4
    assert body["sentiment_intensity"] == 0.7
    assert body["credibility_score"] == 0.85


def test_sentiment_404_no_bars(monkeypatch):
    """Genuinely missing bar data (can't compute returns at all) still 404s."""
    monkeypatch.setattr(metrics_api, "_fetch_bars", lambda sym, lb: None)
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/metrics/sentiment/ZZZZ")
    assert resp.status_code == 404
