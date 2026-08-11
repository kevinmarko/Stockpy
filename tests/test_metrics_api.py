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

# Starlette's TestClient defaults request.client.host to the literal
# string "testclient" -- NOT loopback -- which would trip
# api.auth.require_read_token's new fail-closed-when-non-loopback branch
# on every one of this file's existing zero-config-behavior assertions.
# An explicit loopback host here is what these tests have always meant.
client = TestClient(metrics_api.app, client=("127.0.0.1", 54123))


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
        # Mirrors the real ForecastingEngine's __init__-set attribute the
        # endpoint reads after generate_forecast() -- see
        # api/metrics_api.py::get_forecast's "attention" field.
        last_bert_lla_attention = None

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


def test_forecast_attention_null_by_default(monkeypatch):
    """CONSTRAINT #4: BERT_LLA_ENABLED defaults False, so 'attention' must
    be present-but-null, never omitted (a frontend reading it unconditionally
    would otherwise get undefined) and never a fabricated series."""
    bars = _synthetic_bars()
    monkeypatch.setattr(metrics_api, "_fetch_bars", lambda sym, lb: bars)
    monkeypatch.setattr(metrics_api, "get_provider", lambda: _FakeProvider(quote=_quote()))

    class _FakeFE:
        last_bert_lla_attention = None

        def generate_forecast(self, row, current_price, history_series=None, history_df=None, **kw):
            return {"Forecast_30": 110.0}

    monkeypatch.setattr(metrics_api, "ForecastingEngine", _FakeFE)
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/metrics/forecast/AAPL")
    assert resp.status_code == 200
    body = resp.json()
    assert "attention" in body
    assert body["attention"] is None


def test_forecast_attention_surfaced_when_populated(monkeypatch):
    """When ForecastingEngine.run_bert_lla_forecast actually ran the
    'bert_lla' ablation this request, its attention payload (set on the
    engine instance by generate_forecast) must be read back and surfaced
    verbatim in the response."""
    bars = _synthetic_bars()
    monkeypatch.setattr(metrics_api, "_fetch_bars", lambda sym, lb: bars)
    monkeypatch.setattr(metrics_api, "get_provider", lambda: _FakeProvider(quote=_quote()))

    attention_payload = {
        "model": "bert_lla",
        "window_size": 22,
        "weights": [{"date": "2026-07-21", "alpha": 0.09}],
    }

    class _FakeFE:
        def __init__(self):
            self.last_bert_lla_attention = None

        def generate_forecast(self, row, current_price, history_series=None, history_df=None, **kw):
            self.last_bert_lla_attention = attention_payload
            return {"Forecast_30": 110.0}

    monkeypatch.setattr(metrics_api, "ForecastingEngine", _FakeFE)
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/metrics/forecast/AAPL")
    assert resp.status_code == 200
    body = resp.json()
    assert body["attention"] == attention_payload


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


def test_options_passes_macro_proxy_and_vrp_none_from_snapshot(monkeypatch):
    """Finding 6 regression: previously this endpoint called
    ``build_premium_directive(symbol, bars, spot_price=..., is_stale=...)``
    with NO ``macro_dto``/``vrp`` -- the VRP regime gate (VIX>=30 / CREDIT
    EVENT) inside the engine silently never fired, matching the 4 other
    production callers is what fixes this. Verify the endpoint now threads a
    ``_MacroProxy`` built from the persisted state snapshot's vix/market_regime,
    plus an explicit ``vrp=None``, exactly like
    options_ondemand.py/reporting/options_snapshot.py/gui/panels/options_matrix.py."""
    bars = _synthetic_bars()
    monkeypatch.setattr(metrics_api, "_fetch_bars", lambda sym, lb: bars)
    monkeypatch.setattr(metrics_api, "get_provider", lambda: _FakeProvider(quote=_quote()))
    monkeypatch.setattr(
        metrics_api, "load_snapshot", lambda: {"vix": 35.0, "market_regime": "CREDIT EVENT"}
    )

    captured = {}

    def _fake_directive(symbol, df, *, spot_price, is_stale=False, **kw):
        captured.update(kw)
        return {"Strategy": "Cash", "Action": "Wait", "Net_Premium": 0.0}

    monkeypatch.setattr(metrics_api, "build_premium_directive", _fake_directive)
    monkeypatch.setattr(
        metrics_api, "validate_directive_integrity",
        lambda d: {"ok": True, "issues": []},
    )
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/metrics/options/AAPL")
    assert resp.status_code == 200
    assert "macro_dto" in captured
    macro_dto = captured["macro_dto"]
    assert macro_dto.vix == 35.0
    assert macro_dto.market_regime == "CREDIT EVENT"
    assert captured.get("vrp") is None


def test_options_macro_proxy_neutral_default_without_snapshot(monkeypatch):
    """No persisted snapshot -> neutral defaults (vix=15.0/"RISK ON"), matching
    ``options_ondemand.py``'s MACRO_DEFAULT_VIX/MACRO_DEFAULT_REGIME -- "no
    override" rather than silently gating everything to Cash/Wait."""
    bars = _synthetic_bars()
    monkeypatch.setattr(metrics_api, "_fetch_bars", lambda sym, lb: bars)
    monkeypatch.setattr(metrics_api, "get_provider", lambda: _FakeProvider(quote=_quote()))
    monkeypatch.setattr(metrics_api, "load_snapshot", lambda: None)

    captured = {}

    def _fake_directive(symbol, df, *, spot_price, is_stale=False, **kw):
        captured.update(kw)
        return {"Strategy": "Put Credit Spread", "Net_Premium": 1.0}

    monkeypatch.setattr(metrics_api, "build_premium_directive", _fake_directive)
    monkeypatch.setattr(
        metrics_api, "validate_directive_integrity",
        lambda d: {"ok": True, "issues": []},
    )
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/metrics/options/AAPL")
    assert resp.status_code == 200
    macro_dto = captured["macro_dto"]
    assert macro_dto.vix == 15.0
    assert macro_dto.market_regime == "RISK ON"
    assert captured.get("vrp") is None


def test_options_real_engine_gates_high_vix_snapshot_to_cash_wait(monkeypatch):
    """End-to-end (no mocked engine): the persisted snapshot's VIX >= 30 must
    genuinely gate the real ``technical_options_engine.build_premium_directive``
    call to Cash/Wait, proving the wiring fix has a real effect and not just a
    passed-but-ignored kwarg."""
    bars = _synthetic_bars()
    monkeypatch.setattr(metrics_api, "_fetch_bars", lambda sym, lb: bars)
    monkeypatch.setattr(metrics_api, "get_provider", lambda: _FakeProvider(quote=_quote()))
    monkeypatch.setattr(
        metrics_api, "load_snapshot", lambda: {"vix": 35.0, "market_regime": "RISK ON"}
    )
    # Force the HIGH IVR REGIME branch deterministically regardless of the
    # synthetic bars' realized-vol-derived IVR proxy, so the only thing that
    # can be varying the outcome is the VRP regime gate under test.
    from technical_options_engine import build_premium_directive as _real_directive

    def _real_directive_low_threshold(*args, **kwargs):
        kwargs.setdefault("ivr_sell_threshold", 0.0)
        return _real_directive(*args, **kwargs)

    monkeypatch.setattr(metrics_api, "build_premium_directive", _real_directive_low_threshold)
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/metrics/options/AAPL")
    assert resp.status_code == 200
    body = resp.json()
    assert body["Strategy"] == "Cash"
    assert body["Action"] == "Wait"


# ---------------------------------------------------------------------------
# GET /metrics/models/comparison  (honesty: no fabricated performance curve)
# ---------------------------------------------------------------------------


def test_model_comparison_reports_unavailable_not_fabricated():
    """SF-GARCH-LSTM/Bond-BERT are undeployed ridge-regression stand-ins
    (ml/models/sf_garch_lstm.py, ml/models/bond_bert.py) with no registry
    entry or production caller -- there is no real backtested return history
    to compare, so the endpoint must report empty/synthetic rather than the
    previous hardcoded fixed curve."""
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/metrics/models/comparison")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["is_synthetic"] is True


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
# GET /metrics/signals/importance  (universe-wide driver weights — NOT SHAP)
# ---------------------------------------------------------------------------


def test_signal_importance_shape_and_sort_order(monkeypatch):
    """Real SignalAggregator run (via _module_breakdown, same fakes as the
    per-symbol endpoint) across 2 symbols — proves the aggregation wraps the
    real per-symbol machinery rather than a hand-rolled duplicate."""
    bars = _synthetic_bars()
    monkeypatch.setattr(metrics_api, "_fetch_bars", lambda sym, lb: bars)
    monkeypatch.setattr(metrics_api, "get_provider", lambda: _FakeProvider(quote=_quote()))

    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/metrics/signals/importance?symbols=AAPL,MSFT")
    assert resp.status_code == 200
    body = resp.json()
    assert body["n_symbols_requested"] == 2
    assert isinstance(body["rows"], list) and len(body["rows"]) > 0
    row0 = body["rows"][0]
    assert set(row0.keys()) == {"name", "mean_abs_contribution", "n_symbols_scored"}
    scored = [r for r in body["rows"] if r["mean_abs_contribution"] is not None]
    assert scored == sorted(scored, key=lambda r: -r["mean_abs_contribution"])


def test_signal_importance_docstring_disclaims_shap_never_claims_it():
    """Machine-checkable guard for the honesty rule in AGENTS.md §2: the
    function's own docstring must disclaim SHAP/feature-importance rather
    than silently drifting toward claiming it — catches a future edit that
    relabels this metric without re-reading why it was deliberately not
    called SHAP in the first place."""
    import inspect

    doc = inspect.getdoc(metrics_api._signal_importance) or ""
    assert "NOT SHAP" in doc or "not SHAP" in doc.lower()
    assert "Shapley" in doc


def test_signal_importance_excludes_none_scores_from_mean_not_counted_as_zero(monkeypatch):
    """A module that scored on only 1 of 2 requested symbols must average
    over that 1 real score, never treat the missing symbol as a 0
    contribution (which would silently pull the mean toward zero)."""
    def _fake_breakdown(symbol, provider):
        if symbol == "AAPL":
            return {
                "final_score": 10,
                "modules": [{"name": "mod_a", "score": 0.5, "weight": 10.0, "contribution": 5.0}],
            }
        # MSFT: mod_a did not run this cycle.
        return {
            "final_score": 10,
            "modules": [{"name": "mod_a", "score": None, "weight": 10.0, "contribution": None}],
        }

    monkeypatch.setattr(metrics_api, "_module_breakdown", _fake_breakdown)
    monkeypatch.setattr(metrics_api, "get_provider", lambda: _FakeProvider())

    result = metrics_api._signal_importance(["AAPL", "MSFT"])
    row = next(r for r in result["rows"] if r["name"] == "mod_a")
    assert row["n_symbols_scored"] == 1
    assert row["mean_abs_contribution"] == pytest.approx(5.0)  # NOT (5.0 + 0.0) / 2 = 2.5


def test_signal_importance_module_with_zero_scored_symbols_is_null_not_zero(monkeypatch):
    """A registered module that scores NOTHING in this batch still appears
    in rows (union with the real registry), with mean_abs_contribution: None
    and n_symbols_scored: 0 — never a fabricated 0.0 (CONSTRAINT #4)."""
    monkeypatch.setattr(
        metrics_api, "_module_breakdown", lambda symbol, provider: {"final_score": 10, "modules": []}
    )
    monkeypatch.setattr(metrics_api, "get_provider", lambda: _FakeProvider())

    result = metrics_api._signal_importance(["AAPL"])
    assert len(result["rows"]) > 0  # union with the real registry, not empty
    for row in result["rows"]:
        assert row["mean_abs_contribution"] is None
        assert row["n_symbols_scored"] == 0
    assert result["n_symbols_scored"] == 0


def test_signal_importance_caps_symbol_count(monkeypatch):
    seen_symbols = []

    def _fake_breakdown(symbol, provider):
        seen_symbols.append(symbol)
        return {"final_score": 10, "modules": []}

    monkeypatch.setattr(metrics_api, "_module_breakdown", _fake_breakdown)
    monkeypatch.setattr(metrics_api, "get_provider", lambda: _FakeProvider())

    many = [f"SYM{i}" for i in range(metrics_api._MAX_IMPORTANCE_SYMBOLS + 10)]
    result = metrics_api._signal_importance(many)
    assert result["n_symbols_requested"] == metrics_api._MAX_IMPORTANCE_SYMBOLS
    assert len(seen_symbols) == metrics_api._MAX_IMPORTANCE_SYMBOLS


def test_signal_importance_dedupes_case_insensitively(monkeypatch):
    seen_symbols = []

    def _fake_breakdown(symbol, provider):
        seen_symbols.append(symbol)
        return {"final_score": 10, "modules": []}

    monkeypatch.setattr(metrics_api, "_module_breakdown", _fake_breakdown)
    monkeypatch.setattr(metrics_api, "get_provider", lambda: _FakeProvider())

    result = metrics_api._signal_importance(["aapl", "AAPL", " Aapl "])
    assert result["n_symbols_requested"] == 1
    assert seen_symbols == ["AAPL"]


def test_signal_importance_one_symbol_failure_does_not_abort_batch(monkeypatch):
    """A single symbol's compute failure (CONSTRAINT #6) is logged and
    skipped — the batch still aggregates the symbols that succeeded."""
    def _fake_breakdown(symbol, provider):
        if symbol == "BAD":
            raise RuntimeError("simulated failure")
        return {
            "final_score": 10,
            "modules": [{"name": "mod_a", "score": 0.5, "weight": 10.0, "contribution": 5.0}],
        }

    monkeypatch.setattr(metrics_api, "_module_breakdown", _fake_breakdown)
    monkeypatch.setattr(metrics_api, "get_provider", lambda: _FakeProvider())

    result = metrics_api._signal_importance(["BAD", "AAPL"])
    row = next(r for r in result["rows"] if r["name"] == "mod_a")
    assert row["n_symbols_scored"] == 1
    assert row["mean_abs_contribution"] == pytest.approx(5.0)


def test_signal_importance_endpoint_requires_symbols_param():
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/metrics/signals/importance")
    assert resp.status_code == 422


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


def _canned_catalyst_details() -> dict:
    return {
        "symbol": "AAPL",
        "headlines": [
            {
                "title": "Apple beats and raises guidance",
                "publisher": "example.com",
                "url": "https://example.com/aapl-1",
                "published_at": "2026-08-01T09:30:00+00:00",
                "score": 0.6,
                "probabilities": {"positive": 0.8, "neutral": 0.15, "negative": 0.05},
            },
        ],
        "earnings_catalyst": {
            "next_earnings_date": "2026-09-01",
            "hours_to_earnings": 500.0,
            "status": "normal",
            "multiplier": 1.0,
        },
        "provider_used": "fmp",
        "source_breakdown": {"example.com": 1},
        "raw_sentiment_avg": 0.6,
        "dampened_sentiment_score": 0.6,
    }


def test_sentiment_includes_news_catalyst_fields(monkeypatch):
    """GET /metrics/sentiment/{symbol} merges
    get_symbol_news_catalyst_details() (run concurrently via asyncio.gather)
    into the response -- headlines / earnings_catalyst / provider_used (and
    the supporting source_breakdown / raw_sentiment_avg /
    dampened_sentiment_score fields) all surface."""
    from sentiment_risk_engine import SentimentResult

    bars = _synthetic_bars()
    monkeypatch.setattr(metrics_api, "_fetch_bars", lambda sym, lb: bars)

    canned_sentiment = SentimentResult(
        ticker="AAPL",
        date=datetime(2026, 7, 20, tzinfo=timezone.utc),
        sentiment_score=0.4,
        sentiment_intensity=0.7,
        credibility_score=0.85,
        volatility_persistence=0.9,
        source="antigravity_agent",
    )
    monkeypatch.setattr(metrics_api, "SentimentRiskEngine", lambda: _FakeSentimentEngine(canned_sentiment))
    monkeypatch.setattr(
        metrics_api, "get_symbol_news_catalyst_details", lambda symbol: _canned_catalyst_details()
    )

    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/metrics/sentiment/AAPL")
    assert resp.status_code == 200
    body = resp.json()

    # Antigravity-agent fields untouched by the merge.
    assert body["sentiment_score"] == 0.4
    assert body["source"] == "antigravity_agent"

    # New news-catalyst fields.
    assert len(body["headlines"]) == 1
    assert body["headlines"][0]["publisher"] == "example.com"
    assert body["headlines"][0]["probabilities"]["positive"] == 0.8
    assert body["earnings_catalyst"]["status"] == "normal"
    assert body["earnings_catalyst"]["multiplier"] == 1.0
    assert body["provider_used"] == "fmp"
    assert body["source_breakdown"] == {"example.com": 1}
    assert body["raw_sentiment_avg"] == 0.6
    assert body["dampened_sentiment_score"] == 0.6


def test_sentiment_news_catalyst_failure_degrades_gracefully(monkeypatch):
    """A news-catalyst-helper failure must NOT break the whole response --
    it degrades to headlines: [], earnings_catalyst: None, provider_used:
    "none", while the Antigravity-agent sentiment fields are unaffected."""
    from sentiment_risk_engine import SentimentResult

    bars = _synthetic_bars()
    monkeypatch.setattr(metrics_api, "_fetch_bars", lambda sym, lb: bars)

    canned_sentiment = SentimentResult(
        ticker="AAPL",
        date=datetime(2026, 7, 20, tzinfo=timezone.utc),
        sentiment_score=0.4,
        sentiment_intensity=0.7,
        credibility_score=0.85,
        volatility_persistence=0.9,
        source="antigravity_agent",
    )
    monkeypatch.setattr(metrics_api, "SentimentRiskEngine", lambda: _FakeSentimentEngine(canned_sentiment))

    def _raise(symbol):
        raise RuntimeError("simulated news catalyst failure")

    monkeypatch.setattr(metrics_api, "get_symbol_news_catalyst_details", _raise)

    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/metrics/sentiment/AAPL")
    assert resp.status_code == 200
    body = resp.json()

    assert body["sentiment_score"] == 0.4
    assert body["source"] == "antigravity_agent"
    assert body["headlines"] == []
    assert body["earnings_catalyst"] is None
    assert body["provider_used"] == "none"


def test_sentiment_agent_failure_still_404s_even_with_catalyst_ok(monkeypatch):
    """A SentimentRiskEngine failure still 404s (existing contract)
    regardless of whether the concurrently-run news-catalyst call succeeds."""
    bars = _synthetic_bars()
    monkeypatch.setattr(metrics_api, "_fetch_bars", lambda sym, lb: bars)

    class _FailingEngine:
        async def get_live_sentiment(self, ticker, date, returns):
            raise RuntimeError("simulated engine failure")

    monkeypatch.setattr(metrics_api, "SentimentRiskEngine", lambda: _FailingEngine())
    monkeypatch.setattr(
        metrics_api, "get_symbol_news_catalyst_details", lambda symbol: _canned_catalyst_details()
    )

    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/metrics/sentiment/AAPL")
    assert resp.status_code == 404


class TestCORSLanTailscale:
    """LAN/Tailscale origins are allowed via api.cors.LAN_TAILSCALE_ORIGIN_REGEX
    (additive to the explicit CORS_ALLOWED_ORIGINS list), scoped to the Pilots
    PWA dev server's port (5173, per webapp/vite.config.ts's
    ``server: { host: true, port: 5173 }``)."""

    def test_lan_origin_is_reflected(self):
        resp = client.get("/health", headers={"Origin": "http://192.168.1.42:5173"})
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == "http://192.168.1.42:5173"

    def test_tailscale_range_origin_is_reflected(self):
        resp = client.get("/health", headers={"Origin": "http://100.101.102.5:5173"})
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == "http://100.101.102.5:5173"

    def test_lan_origin_wrong_port_not_reflected(self):
        resp = client.get("/health", headers={"Origin": "http://192.168.1.42:5174"})
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") != "http://192.168.1.42:5174"

    def test_public_ip_not_reflected(self):
        resp = client.get("/health", headers={"Origin": "http://8.8.8.8:5173"})
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") != "http://8.8.8.8:5173"
