"""
tests/test_perf_hotpath.py — PR A performance invariants (behavior-preserving)
==============================================================================
Locks in the hot-path optimizations so they can't silently regress:

* CompositeProvider caches intraday bars (one backend fetch per (symbol, lookback)
  within the TTL) — the "bars fetched twice per symbol" fix.
* ForecastTracker / HistoricalStore reuse ONE sqlite connection instead of opening
  a fresh connection (+ WAL PRAGMA) on every call (~12/ticker/cycle before).
* engine.advisory builds its heavy engines ONCE (module singletons) instead of
  reconstructing them per symbol, AND is safe to call concurrently (the orchestrator
  now parallelizes the advisory-overlay loop over shared singletons).
"""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import numpy as np
import pandas as pd
import pytest


# ── bars cache ───────────────────────────────────────────────────────────────

def _bars_df(n=60):
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "Volume": 1000},
        index=idx,
    )


def test_bars_cache_ttl_semantics():
    from data.market_data import _BarsCache

    c = _BarsCache(ttl_seconds=300)
    assert c.get("AAPL", 252) is None                 # cold
    c.put("AAPL", 252, _bars_df())
    hit = c.get("AAPL", 252)
    assert hit is not None and len(hit) == 60
    # Returns a defensive copy — mutating the returned frame can't poison the cache.
    hit.iloc[0, 0] = -999.0
    assert c.get("AAPL", 252).iloc[0, 0] == 100.0
    # Different lookback is a distinct key.
    assert c.get("AAPL", 100) is None


def test_bars_cache_expiry_returns_none(monkeypatch):
    from data.market_data import _BarsCache

    c = _BarsCache(ttl_seconds=300)
    c.put("AAPL", 252, _bars_df())
    # Jump the monotonic clock past the TTL → miss.
    import data.market_data as md
    real = md.time.monotonic()
    monkeypatch.setattr(md.time, "monotonic", lambda: real + 301)
    assert c.get("AAPL", 252) is None


def test_composite_get_intraday_bars_hits_cache_once():
    """Two calls within the TTL → exactly one backend fetch."""
    from data.market_data import CompositeProvider

    prov = CompositeProvider.__new__(CompositeProvider)  # bypass __init__ network
    backend = mock.MagicMock()
    backend.get_intraday_bars.return_value = _bars_df()
    prov._quote_provider = backend

    a = prov.get_intraday_bars("AAPL", 252)
    b = prov.get_intraday_bars("AAPL", 252)
    assert backend.get_intraday_bars.call_count == 1   # second served from cache
    assert len(a) == len(b) == 60


# ── connection reuse ─────────────────────────────────────────────────────────

def test_forecast_tracker_reuses_one_connection(tmp_path):
    from forecasting.forecast_tracker import ForecastTracker

    t = ForecastTracker(db_path=str(tmp_path / "ft.db"))
    with t._lock:
        c1 = t._get_conn()
        c2 = t._get_conn()
    assert c1 is c2                                    # single cached handle


def test_historical_store_reuses_one_connection(tmp_path):
    from data.historical_store import HistoricalStore

    s = HistoricalStore(db_path=str(tmp_path / "hs.db"))
    with s._lock:
        c1 = s._get_conn()
        c2 = s._get_conn()
    assert c1 is c2


# ── advisory: singletons built once + concurrent-safe ────────────────────────

def _fake_market(price=100.0):
    m = mock.MagicMock()
    m.get_latest_quote.return_value = mock.MagicMock(price=price, is_stale=False)
    m.get_intraday_bars.return_value = _bars_df(300)
    m.get_fundamentals.return_value = {}
    return m


def test_advisory_engine_singletons_build_once():
    import engine.advisory as adv

    # Reset any cached singletons so the test is order-independent.
    for attr in ("_PROCESSING_ENGINE", "_TECHNICAL_OPTIONS_ENGINE",
                 "_FORECASTING_ENGINE", "_TRANSACTIONS_STORE"):
        if hasattr(adv, attr):
            setattr(adv, attr, None)
    a = adv._get_processing_engine()
    b = adv._get_processing_engine()
    assert a is b                                      # reused, not rebuilt
    assert adv._get_transactions_store() is adv._get_transactions_store()


def test_advisory_evaluate_concurrent_is_safe():
    """The orchestrator parallelizes evaluate() over shared singletons — running
    it from many threads must not raise and must return valid Recommendations."""
    from engine.advisory import evaluate
    from dto_models import MacroEconomicDTO
    from transactions_store import TransactionsStore

    macro = MacroEconomicDTO(
        yield_curve_10y_2y=0.5, high_yield_oas=3.0, inflation_rate=2.5,
        sahm_rule_indicator=0.0, vix_value=15.0,
    )
    ts = TransactionsStore(db_url="sqlite:///:memory:")
    market = _fake_market()
    symbols = [f"SYM{i}" for i in range(12)]

    def _run(sym):
        return evaluate(sym, None, market, None, macro_dto=macro, transactions_store=ts)

    with ThreadPoolExecutor(max_workers=8) as ex:
        recs = list(ex.map(_run, symbols))

    assert len(recs) == len(symbols)
    for rec in recs:
        assert rec.action in ("BUY", "SELL", "HOLD")
        assert isinstance(rec.key_indicators, dict)
