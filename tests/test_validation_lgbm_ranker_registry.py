"""
InvestYo Quant Platform - LGBM Ranker (STRATEGY_REGISTRY) Validation Test
==============================================================================
Runs ``scripts.refresh_validations._build_lgbm_ranker_adapter`` — the
production adapter registered as ``STRATEGY_REGISTRY["lgbm_ranker"]`` and
joined to the ``ml-cross-sectional-rank`` Pilot's ``validation_strategy_id``
— over real historical price data for a subset of the platform's 30-name
cross-sectional universe, and verifies the StrategyValidationHarness produces
a well-formed report end-to-end.

Modeled on ``tests/test_validation_xsec_momentum_registry.py`` (closest
precedent), with one structural difference: this adapter returns a
ready-made ``strategy_fn`` callable (genuine per-fold retraining) rather than
a dict of precomputed static return series, so the harness is wired directly
against the adapter's own third return value instead of a
``_make_strategy_fn``-wrapped dict.

A SMALLER universe / bounded window / reduced ``n_cpcv_splits`` than the
production registry entry's own defaults are used here purely for test
runtime (each CPCV fold genuinely retrains a LightGBM model — this is
inherently much more expensive than every other adapter's precomputed-return
lookup) — the production entry itself (``STRATEGY_REGISTRY["lgbm_ranker"]``)
uses the full ``_XSEC_UNIVERSE_30`` and the harness's own default
``n_cpcv_splits``, exercised for real numbers by
``scripts/refresh_validations.py``'s CLI (see
``docs/signals/lgbm_ranker.md``'s Backtest Validation section for that run's
actual measured numbers), not by this test.
"""

import numpy as np
import pandas as pd
import pytest
import yfinance as yf

from execution.cost_model import TieredCostModel
from scripts.refresh_validations import _build_lgbm_ranker_adapter
from validation.harness import StrategyValidationHarness

# Downloads real multi-ticker price history live from Yahoo Finance in its
# module-scoped fixture — network-dependent, deselected in CI via
# ``pytest -m "not network"``.
pytestmark = pytest.mark.network

# A small subset of _XSEC_UNIVERSE_30 — enough for a meaningful cross-section
# without paying the full 30-ticker panel-build cost in a test.
TICKERS = ["AAPL", "MSFT", "JNJ", "XOM", "KO", "JPM", "PG", "INTC", "T", "WMT"]


@pytest.fixture(scope="module")
def price_history() -> dict:
    data = {}
    for ticker in TICKERS:
        df = yf.download(ticker, start="2018-01-01", end="2024-12-31", progress=False)
        if df is not None and not df.empty:
            df.index = pd.to_datetime(df.index)
            data[ticker] = df
    assert len(data) >= 5, "Failed to download enough tickers for a meaningful cross-section"
    return data


def _closes_frame(price_history: dict) -> pd.DataFrame:
    closes = {t: df["Close"].squeeze() for t, df in price_history.items()}
    common_index = None
    for s in closes.values():
        common_index = s.index if common_index is None else common_index.intersection(s.index)
    assert common_index is not None and len(common_index) > 300
    return pd.DataFrame({t: s.reindex(common_index) for t, s in closes.items()})


def test_lgbm_ranker_adapter_returns_callable_strategy_fn(price_history, monkeypatch):
    import scripts.refresh_validations
    monkeypatch.setattr(scripts.refresh_validations, "_XSEC_UNIVERSE_CAPPED", TICKERS)
    closes = _closes_frame(price_history)
    X, y, strategy_fn = _build_lgbm_ranker_adapter(closes)

    assert not X.empty
    assert not y.empty
    assert callable(strategy_fn)
    # Outer contract: ONE row per unique date (harness.py's benchmark_curve
    # reindexes y against a unique-date index and raises InvalidIndexError
    # otherwise).
    assert X.index.is_unique
    assert y.index.is_unique


def test_lgbm_ranker_strategy_fn_produces_real_trades(price_history, monkeypatch):
    """A single fold call must genuinely train a ranker and produce
    non-empty train/test long-short return series."""
    import scripts.refresh_validations
    monkeypatch.setattr(scripts.refresh_validations, "_XSEC_UNIVERSE_CAPPED", TICKERS)
    closes = _closes_frame(price_history)
    X, y, strategy_fn = _build_lgbm_ranker_adapter(closes)
    assert not X.empty

    split = int(len(X) * 0.7)
    X_tr, y_tr = X.iloc[:split], y.iloc[:split]
    X_te, y_te = X.iloc[split:], y.iloc[split:]

    trials = strategy_fn(X_tr, y_tr, X_te, y_te)
    assert trials, "strategy_fn produced no trials for a real, non-trivial fold"
    trial = trials[0]
    assert trial["params"] == "LGBM_Ranker"
    assert not trial["train_returns"].empty
    assert not trial["test_returns"].empty
    assert np.isfinite(trial["train_returns"]).all()
    assert np.isfinite(trial["test_returns"]).all()


def test_lgbm_ranker_validation_harness_runs(price_history, monkeypatch, tmp_path):
    """Validates the full harness runs without crashing and produces the
    expected metrics dict shape. Does not assert profitability (test data
    is arbitrary/small)."""
    import scripts.refresh_validations
    monkeypatch.setattr(scripts.refresh_validations, "_XSEC_UNIVERSE_CAPPED", TICKERS)
    
    closes = _closes_frame(price_history)
    X, y, strategy_fn = _build_lgbm_ranker_adapter(closes)
    assert not X.empty and not y.empty and callable(strategy_fn)

    cost_model = TieredCostModel()

    def mock_universe_fn(as_of_date):
        return TICKERS

    harness = StrategyValidationHarness(
        strategy_fn=strategy_fn,
        universe_fn=mock_universe_fn,
        cost_model=cost_model,
        n_cpcv_splits=6,   # reduced from the production default (10) purely
        n_test_splits=2,   # for test runtime -- see module docstring.
        reports_dir=str(tmp_path),
    )

    report = harness.run(
        start_date=str(X.index[0].date()),
        end_date=str(X.index[-1].date()),
        X=X,
        y=y,
        strategy_name="LGBM_Ranker_Registry_Harness_Test",
    )

    print("\n--- LGBM RANKER (STRATEGY_REGISTRY) VALIDATION REPORT ---")
    print(f"Sharpe Ratio (net): {report.sharpe:.3f}")
    print(f"Max Drawdown: {report.max_dd * 100:.2f}%")
    print(f"DSR: {report.dsr:.4f}")
    print(f"PBO: {report.pbo:.4f}")
    print(f"Deployable: {report.deployable}")

    assert not np.isnan(report.sharpe)
    assert not np.isnan(report.max_dd)
    assert isinstance(report.deployable, bool)
