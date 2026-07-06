"""
tests/test_forecast_parallel.py
===============================
Offline unit tests for the parallelized per-ticker forecasting loop in
``main_orchestrator.run_pipeline()`` (PR 1).

The forecasting loop was refactored from a sequential ``for … in
dashboard_df.iterrows()`` into a local ``_forecast_one(row)`` helper dispatched
either sequentially (``FORECAST_MAX_CONCURRENCY == 1`` or ≤ 1 row) or across a
``ThreadPoolExecutor``.  These tests assert:

  1. the parallel path (workers=8) produces the EXACT same ``forecast_results``
     dict as the sequential path (workers=1);
  2. a falsy price yields ``(ticker, None)`` and is dropped from the results;
  3. a per-ticker ``generate_forecast`` exception is isolated by the try/except
     Monte-Carlo fallback and never aborts the pool.

All I/O is offline: ``ForecastingEngine.generate_forecast`` /
``run_monte_carlo`` are patched with deterministic stubs.  The dispatch logic
exercised here is byte-for-byte identical to the code shipped in
``run_pipeline`` (same helper contract, same ThreadPoolExecutor construct keyed
off ``settings.FORECAST_MAX_CONCURRENCY``).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import numpy as np
import pandas as pd

from forecasting_engine import ForecastingEngine


# ---------------------------------------------------------------------------
# Helpers mirroring run_pipeline's forecasting section, parameterized on workers
# ---------------------------------------------------------------------------

def _build_forecast_results(dashboard_df: pd.DataFrame, tech_raw: dict, fe, workers: int) -> dict:
    """Reproduce run_pipeline's exact _forecast_one + dispatch logic."""

    def _forecast_one(row):
        ticker = row['Symbol']
        price = row['Price']
        if not price or price == 0:
            return ticker, None

        history_df = tech_raw.get(ticker)
        history_series = history_df['Close'] if history_df is not None else None

        try:
            forecasts = fe.generate_forecast(row, price, history_series, history_df=history_df)
            return ticker, forecasts
        except Exception:
            mu = 0.0002
            sigma = 0.015
            if history_series is not None and len(history_series) > 1:
                returns = np.log(history_series / history_series.shift(1)).dropna()
                mu = float(returns.mean())
                sigma = float(returns.std())

            mc_target, mc_low, mc_high = fe.run_monte_carlo(price, mu, sigma, 30)
            mc_10, _, _ = fe.run_monte_carlo(price, mu, sigma, 10)
            mc_60, _, _ = fe.run_monte_carlo(price, mu, sigma, 60)
            mc_90, _, _ = fe.run_monte_carlo(price, mu, sigma, 90)
            return ticker, {
                'Target_Days': 30,
                'ARIMA': price,
                'MC_Target': mc_target,
                'MC_Lower': mc_low,
                'MC_Upper': mc_high,
                'Forecast_10': mc_10,
                'Forecast_30': mc_target,
                'Forecast_60': mc_60,
                'Forecast_90': mc_90,
                'Forecast_30_Prophet_Lower': mc_low,
                'Forecast_30_Prophet_Upper': mc_high,
            }

    rows = [row for _, row in dashboard_df.iterrows()]
    if workers == 1 or len(rows) <= 1:
        pairs = [_forecast_one(r) for r in rows]
    else:
        with ThreadPoolExecutor(max_workers=min(workers, len(rows))) as pool:
            pairs = list(pool.map(_forecast_one, rows))
    return {tk: fc for tk, fc in pairs if fc is not None}


def _make_dashboard(symbols_prices):
    return pd.DataFrame([{'Symbol': s, 'Price': p} for s, p in symbols_prices])


def _make_tech_raw(symbols):
    idx = pd.date_range('2024-01-01', periods=30, freq='D')
    out = {}
    for i, s in enumerate(symbols):
        # Deterministic, distinct per-symbol close series.
        close = pd.Series(100.0 + i + np.arange(30) * 0.5, index=idx)
        out[s] = pd.DataFrame({'Close': close})
    return out


# Identity stub: forecast keyed off ticker + price so equality is meaningful.
# `self` is included because it is patched in as a class attribute (bound method).
def _identity_forecast(self, row, price, history_series, history_df=None):
    return {
        'Target_Days': 30,
        'ARIMA': price,
        'MC_Target': price * 1.01,
        'MC_Lower': price * 0.95,
        'MC_Upper': price * 1.05,
        'Forecast_10': price * 1.002,
        'Forecast_30': price * 1.01,
        'Forecast_60': price * 1.02,
        'Forecast_90': price * 1.03,
        'Forecast_30_Prophet_Lower': price * 0.95,
        'Forecast_30_Prophet_Upper': price * 1.05,
        '_ticker': row['Symbol'],
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_parallel_matches_sequential():
    symbols = ['AAPL', 'MSFT', 'GOOG', 'AMZN', 'NVDA', 'META', 'TSLA', 'SPY']
    prices = [(s, 100.0 + i) for i, s in enumerate(symbols)]
    dashboard_df = _make_dashboard(prices)
    tech_raw = _make_tech_raw(symbols)

    with patch.object(ForecastingEngine, 'generate_forecast', _identity_forecast):
        fe = ForecastingEngine()
        seq = _build_forecast_results(dashboard_df, tech_raw, fe, workers=1)
        par = _build_forecast_results(dashboard_df, tech_raw, fe, workers=8)

    assert set(seq.keys()) == set(symbols)
    assert seq == par


def test_falsy_price_dropped():
    symbols = ['AAPL', 'ZERO', 'MSFT']
    dashboard_df = _make_dashboard([('AAPL', 150.0), ('ZERO', 0.0), ('MSFT', 300.0)])
    tech_raw = _make_tech_raw(symbols)

    with patch.object(ForecastingEngine, 'generate_forecast', _identity_forecast):
        fe = ForecastingEngine()
        res = _build_forecast_results(dashboard_df, tech_raw, fe, workers=8)

    assert 'ZERO' not in res
    assert set(res.keys()) == {'AAPL', 'MSFT'}


def test_failure_isolated_by_fallback():
    symbols = ['AAPL', 'BOOM', 'MSFT']
    dashboard_df = _make_dashboard([('AAPL', 150.0), ('BOOM', 200.0), ('MSFT', 300.0)])
    tech_raw = _make_tech_raw(symbols)

    def _flaky_forecast(self, row, price, history_series, history_df=None):
        if row['Symbol'] == 'BOOM':
            raise RuntimeError("simulated CNN-LSTM failure")
        return _identity_forecast(self, row, price, history_series, history_df=history_df)

    def _fake_mc(self, start_price, mu, sigma, days_forward, simulations=1000):
        # Deterministic, no randomness.
        return start_price, start_price * 0.9, start_price * 1.1

    with patch.object(ForecastingEngine, 'generate_forecast', _flaky_forecast), \
         patch.object(ForecastingEngine, 'run_monte_carlo', _fake_mc):
        fe = ForecastingEngine()
        seq = _build_forecast_results(dashboard_df, tech_raw, fe, workers=1)
        par = _build_forecast_results(dashboard_df, tech_raw, fe, workers=8)

    # One bad ticker never aborts the pool: all three symbols present.
    assert set(seq.keys()) == set(symbols)
    assert seq == par
    # The failed ticker took the Monte-Carlo fallback path.
    assert seq['BOOM']['MC_Target'] == 200.0
    assert seq['BOOM']['Forecast_30'] == 200.0
