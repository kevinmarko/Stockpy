"""
InvestYo Quant Platform - Multifactor Signal Validation Harness Test
=======================================================================
Runs a price-derived multifactor proxy strategy over real historical price
data (2005-2023) for a representative cross-section of liquid equities and
verifies the StrategyValidationHarness produces a well-formed report.

SCOPE LIMITATION (read before extending this test)
----------------------------------------------------
A literal "S&P 500 2005-2023" backtest using POINT-IN-TIME fundamentals
(book-to-market, earnings yield, ROE, operating margin -- i.e. the Value and
Quality factors in signals/multifactor.py) is not achievable with this
project's free-data-only constraint (no paid deps; see CLAUDE.md constraint
#1). yfinance's `.info` dict is a CURRENT snapshot, not a point-in-time
historical fundamentals feed -- there is no free vendor for 18 years of
historical P/B, P/E, ROE, or shares-outstanding history. Faking that history
would violate the "no fabricated metrics" constraint (#4) more directly than
just not running the test.

This test therefore validates only the two factors that ARE honestly
derivable from real, free, point-in-time-correct data:
  - Low-Vol : 60-day trailing realized volatility (computed causally with
              .shift(1), identical to processing_engine.calculate_momentum_metrics)
  - Size    : log(price * CURRENT shares outstanding). Using a CURRENT share
              count against historical prices is itself an approximation
              (share counts drift over 18 years via buybacks/issuance) --
              flagged here explicitly rather than silently treated as exact.

Value and Quality are covered by the synthetic-but-engineered cross-section
in tests/test_multifactor.py instead, which can construct exact, known factor
exposures without needing a historical fundamentals feed at all.

PIT-fundamentals path (Tier 2.3 Phase 3 — future extension)
-------------------------------------------------------------
The ``fundamentals_history`` table in ``quant_platform.db`` (written by
``data.historical_store.HistoricalStore.get_fundamentals()``) accumulates
real point-in-time fundamentals snapshots starting from the day Phase 3
ships. Each row stores ``raw_json`` containing the full provider dict at
the time of capture (including book-to-market, earnings yield, ROE, and
operating margin).

Once ≥ 90 days of history have accumulated, this harness test COULD be
extended to replay the Value and Quality factors using
``HistoricalStore.get_fundamentals_history(symbol)`` — reading ``raw_json``
parsed as a daily fundamentals snapshot and computing factor z-scores
cross-sectionally for each date.  That would close the gap noted in the
SCOPE LIMITATION section above.

**Do NOT implement that extension here** — it is explicitly out-of-scope
for Phase 3 and should be a separate PR (filed as a follow-up ticket once
≥ 90 days of fundamentals have been accumulated in production).
"""

import math

import numpy as np
import pandas as pd
import pytest
import yfinance as yf

from execution.cost_model import TieredCostModel
from validation.harness import StrategyValidationHarness

# Downloads real multi-ticker price history live from Yahoo Finance in its
# module-scoped fixtures — network-dependent, deselected in CI via
# ``pytest -m "not network"``.
pytestmark = pytest.mark.network

# A representative cross-section of liquid, long-listed equities spanning a
# real large-to-small market-cap spread (avoids downloading the full S&P 500
# universe, which is too slow/flaky for a unit test).
TICKERS = ["AAPL", "JNJ", "XOM", "KO", "JPM", "PG", "INTC", "T", "GE", "F"]


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


@pytest.fixture(scope="module")
def current_shares_outstanding() -> dict:
    """CURRENT shares outstanding per ticker -- an approximation when applied
    against 2005-2023 historical prices (see module docstring SCOPE LIMITATION)."""
    shares = {}
    for ticker in TICKERS:
        try:
            info = yf.Ticker(ticker).fast_info
            so = getattr(info, "shares", None) or info.get("shares") if hasattr(info, "get") else None
            if so:
                shares[ticker] = float(so)
        except Exception:
            continue
    return shares


def _realized_vol_60d(close: pd.Series) -> pd.Series:
    """Causal 60-day annualized realized vol -- identical methodology to
    processing_engine.calculate_momentum_metrics (.shift(1) before the rolling window)."""
    daily_returns = close.pct_change().shift(1)
    return daily_returns.rolling(window=60).std() * np.sqrt(252)


def test_low_vol_and_size_proxy_validation_harness_runs(price_history, current_shares_outstanding, tmp_path):
    """Smoke-tests the StrategyValidationHarness end-to-end on a Low-Vol +
    Size multifactor proxy built from real historical prices. As with
    tests/test_validation_rsi2.py, we assert a well-formed report (not NaN,
    deployable is a bool) rather than deployability itself -- an 18-year,
    10-name proxy is not expected to clear the Sharpe/DSR bar on its own, and
    that is not what this test is verifying.
    """
    closes = {t: df["Close"].squeeze() for t, df in price_history.items()}
    common_index = None
    for s in closes.values():
        common_index = s.index if common_index is None else common_index.intersection(s.index)
    assert common_index is not None and len(common_index) > 300

    low_vol_z = {}
    size_z = {}
    daily_rets = {}
    for ticker, close in closes.items():
        close = close.reindex(common_index)
        vol_60d = _realized_vol_60d(close)
        low_vol_z[ticker] = -vol_60d  # negate: low vol -> high score
        daily_rets[ticker] = close.pct_change()

        shares = current_shares_outstanding.get(ticker)
        if shares:
            log_mcap = np.log(close * shares)
            size_z[ticker] = -log_mcap  # smaller -> positive
        else:
            size_z[ticker] = pd.Series(np.nan, index=common_index)

    low_vol_df = pd.DataFrame(low_vol_z)
    size_df = pd.DataFrame(size_z)
    rets_df = pd.DataFrame(daily_rets)

    # Cross-sectional z-score each factor per day (winsorized at +/-3, same as
    # signals/multifactor.py's _zscore_winsorize).
    def _xsec_zscore(df: pd.DataFrame) -> pd.DataFrame:
        mean = df.mean(axis=1)
        std = df.std(axis=1)
        z = df.sub(mean, axis=0).div(std.replace(0.0, np.nan), axis=0)
        return z.clip(lower=-3.0, upper=3.0)

    low_vol_xz = _xsec_zscore(low_vol_df)
    size_xz = _xsec_zscore(size_df)
    composite = (low_vol_xz + size_xz) / 2.0

    # Equal-weighted long-only portfolio tilted toward the composite's top half
    # each day (long-only, rebalanced daily, simplistic but sufficient for a
    # harness smoke test).
    weights = composite.rank(axis=1, pct=True).ge(0.5).astype(float)
    weights = weights.div(weights.sum(axis=1).replace(0.0, np.nan), axis=0).fillna(0.0)
    portfolio_returns = (weights.shift(1) * rets_df).sum(axis=1).fillna(0.0)

    X = pd.DataFrame(index=common_index)
    X["LowVol_Composite"] = low_vol_xz.mean(axis=1).fillna(0.0)
    X["Size_Composite"] = size_xz.mean(axis=1).fillna(0.0)
    y = rets_df.mean(axis=1).fillna(0.0)

    precomputed = {"Multifactor_LowVol_Size": portfolio_returns}

    def multifactor_strategy_fn(X_train, y_train, X_test, y_test):
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
        strategy_fn=multifactor_strategy_fn,
        universe_fn=mock_universe_fn,
        cost_model=cost_model,
        n_cpcv_splits=10,
        n_test_splits=2,
        reports_dir=str(tmp_path),
    )

    report = harness.run(
        start_date=str(common_index[0].date()),
        end_date=str(common_index[-1].date()),
        X=X,
        y=y,
        strategy_name="Multifactor_LowVol_Size_Harness_Test",
    )

    print("\n--- MULTIFACTOR (LOW-VOL + SIZE) VALIDATION HARNESS REPORT ---")
    print(f"Sharpe Ratio (net): {report.sharpe:.3f}")
    print(f"Max Drawdown: {report.max_dd * 100:.2f}%")
    print(f"DSR: {report.dsr:.4f}")
    print(f"PBO: {report.pbo:.4f}")
    print(f"Deployable: {report.deployable}")

    assert not np.isnan(report.sharpe)
    assert not np.isnan(report.max_dd)
    assert isinstance(report.deployable, bool)


def test_low_vol_proxy_is_lookahead_free(price_history):
    """Perturbing a future day's price must not change today's Low-Vol score
    (the rolling window is built on .shift(1) returns)."""
    ticker = TICKERS[0]
    close = price_history[ticker]["Close"].squeeze().copy()

    baseline = _realized_vol_60d(close)
    perturbed = close.copy()
    mid = len(perturbed) // 2
    perturbed.iloc[mid + 1:] = 99999.9

    perturbed_vol = _realized_vol_60d(perturbed)

    # Up to and including index `mid`, both series must be identical --
    # nothing after `mid` may influence them.
    pd.testing.assert_series_equal(
        baseline.iloc[:mid + 1].fillna(-1.0),
        perturbed_vol.iloc[:mid + 1].fillna(-1.0),
        check_names=False,
    )
