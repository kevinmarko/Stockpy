"""
InvestYo Quant Platform - Cross-Sectional Momentum (STRATEGY_REGISTRY) Validation Test
=========================================================================================
Runs ``scripts.refresh_validations._build_xsec_momentum_adapter`` — the
production adapter registered as ``STRATEGY_REGISTRY["cross_sectional_momentum"]``
and joined to the ``cross-sectional-momentum`` Pilot's ``validation_strategy_id``
— over real historical price data (2005-2023) for a representative
cross-section of liquid equities, and verifies the StrategyValidationHarness
produces a well-formed report.

Mirrors ``tests/test_validation_multifactor.py``'s Low-Vol/Size test pattern
(same equity universe, same well-formedness-not-forced-pass assertion
convention). Named distinctly from the pre-existing
``tests/test_validation_xsec_momentum.py`` (a differently-scoped ETF-proxy
harness test, unrelated to ``STRATEGY_REGISTRY``) to avoid any confusion or
collision with that file's own coverage.
"""

import numpy as np
import pandas as pd
import pytest
import yfinance as yf

from execution.cost_model import TieredCostModel
from validation.harness import StrategyValidationHarness
from scripts.refresh_validations import _build_xsec_momentum_adapter

# Downloads real multi-ticker price history live from Yahoo Finance in its
# module-scoped fixture — network-dependent, deselected in CI via
# ``pytest -m "not network"``.
pytestmark = pytest.mark.network

# Same representative universe as tests/test_validation_multifactor.py and
# STRATEGY_REGISTRY["cross_sectional_momentum"]'s declared universe.
TICKERS = ["AAPL", "JNJ", "XOM", "KO", "JPM", "PG", "INTC", "T"]


@pytest.fixture(scope="module")
def price_history() -> dict:
    data = {}
    for ticker in TICKERS:
        df = yf.download(ticker, start="2005-01-01", end="2023-12-31", progress=False)
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


def test_xsec_momentum_validation_harness_runs(price_history, tmp_path):
    """Smoke-tests the StrategyValidationHarness end-to-end on the production
    cross-sectional-momentum adapter. As with the sibling Low-Vol/Size test,
    we assert a well-formed report (not NaN, deployable is a bool) rather
    than deployability itself — an 18-year, 8-name proxy is not expected to
    clear the Sharpe/DSR bar on its own, and that is not what this test is
    verifying.
    """
    closes = _closes_frame(price_history)
    X, y, precomputed = _build_xsec_momentum_adapter(closes)

    assert not X.empty and not y.empty and precomputed

    def strategy_fn(X_train, y_train, X_test, y_test):
        return [
            {
                "params": name,
                "train_returns": returns.loc[returns.index.intersection(y_train.index)],
                "test_returns": returns.loc[returns.index.intersection(y_test.index)],
                "turnover": 0.05,
            }
            for name, returns in precomputed.items()
        ]

    cost_model = TieredCostModel()

    def mock_universe_fn(as_of_date):
        return TICKERS

    harness = StrategyValidationHarness(
        strategy_fn=strategy_fn,
        universe_fn=mock_universe_fn,
        cost_model=cost_model,
        n_cpcv_splits=10,
        n_test_splits=2,
        reports_dir=str(tmp_path),
    )

    report = harness.run(
        start_date=str(X.index[0].date()),
        end_date=str(X.index[-1].date()),
        X=X,
        y=y,
        strategy_name="XSec_Momentum_Registry_Harness_Test",
    )

    print("\n--- CROSS-SECTIONAL MOMENTUM (STRATEGY_REGISTRY) VALIDATION REPORT ---")
    print(f"Sharpe Ratio (net): {report.sharpe:.3f}")
    print(f"Max Drawdown: {report.max_dd * 100:.2f}%")
    print(f"DSR: {report.dsr:.4f}")
    print(f"PBO: {report.pbo:.4f}")
    print(f"Deployable: {report.deployable}")

    assert not np.isnan(report.sharpe)
    assert not np.isnan(report.max_dd)
    assert isinstance(report.deployable, bool)


def test_xsec_momentum_adapter_is_lookahead_free(price_history):
    """Perturbing prices strictly AFTER a cutoff must not change the
    momentum composite or portfolio-return decision AT OR BEFORE that
    cutoff (the SKIP_DAYS/LOOKBACK_DAYS shifts and the final
    ``weights.shift(1)`` are all strictly causal)."""
    closes = _closes_frame(price_history)
    baseline_X, _, baseline_precomputed = _build_xsec_momentum_adapter(closes)

    perturbed = closes.copy()
    mid = len(perturbed) // 2
    perturbed.iloc[mid + 1:, :] = 99999.9
    perturbed_X, _, perturbed_precomputed = _build_xsec_momentum_adapter(perturbed)

    # Re-align both runs' (differently-trimmed) indices to the shared
    # pre-cutoff window and compare.
    common = baseline_X.index.intersection(perturbed_X.index)
    common = common[common <= closes.index[mid]]
    assert len(common) > 100

    pd.testing.assert_series_equal(
        baseline_X.loc[common, "Momentum_12_1_Composite"],
        perturbed_X.loc[common, "Momentum_12_1_Composite"],
        check_names=False,
    )
    pd.testing.assert_series_equal(
        baseline_precomputed["XSecMom_TopHalf"].loc[common],
        perturbed_precomputed["XSecMom_TopHalf"].loc[common],
        check_names=False,
    )
