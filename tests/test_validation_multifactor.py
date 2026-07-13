"""
InvestYo Quant Platform - Multifactor Signal Validation Harness Test
=======================================================================
Runs a price-derived multifactor proxy strategy over real historical price
data (2005-2023) for a representative cross-section of liquid equities and
verifies the StrategyValidationHarness produces a well-formed report.

SCOPE LIMITATION (read before extending this test)
----------------------------------------------------
A literal "S&P 500 2005-2023" backtest using POINT-IN-TIME fundamentals
is computationally heavy to pull live. We use synthetic PIT fundamentals
populated into a mock HistoricalStore to validate the Value and Quality
paths through the validation harness.

This validates that the `validation/harness.py` mechanics (Sharpe, DSR, etc)
work transparently over PIT fundamental DataFrames.
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


@pytest.fixture(scope="module")
def mock_fundamentals_store(price_history, tmp_path_factory):
    """Generates synthetic PIT fundamental history for the test universe."""
    from data.historical_store import HistoricalStore
    db_path = tmp_path_factory.mktemp("db") / "cov.db"
    store = HistoricalStore(db_path=str(db_path))
    
    for ticker, df in price_history.items():
        if df.empty:
            continue
        start_year = df.index[0].year
        end_year = df.index[-1].year
        
        for year in range(start_year, end_year + 1):
            for month in [3, 6, 9, 12]:
                report_date = f"{year}-{month:02d}-15"
                store.upsert_fundamentals_pit(
                    ticker,
                    {
                        "pb_ratio": float(np.random.uniform(1.0, 5.0)),
                        "pe_ratio": float(np.random.uniform(10.0, 30.0)),
                        "roe": float(np.random.uniform(0.05, 0.25)),
                        "operating_margin": float(np.random.uniform(0.1, 0.3)),
                    },
                    {},
                    report_date=report_date,
                    source="mock"
                )
    return store


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

    # nothing after `mid` may influence them.
    pd.testing.assert_series_equal(
        baseline.iloc[:mid + 1].fillna(-1.0),
        perturbed_vol.iloc[:mid + 1].fillna(-1.0),
        check_names=False,
    )


def test_value_quality_proxy_validation_harness_runs(price_history, mock_fundamentals_store, tmp_path):
    """Smoke-tests the StrategyValidationHarness end-to-end on a Value +
    Quality multifactor proxy built from the new PIT fundamentals layer.
    """
    closes = {t: df["Close"].squeeze() for t, df in price_history.items()}
    common_index = None
    for s in closes.values():
        common_index = s.index if common_index is None else common_index.intersection(s.index)
    assert common_index is not None and len(common_index) > 300

    value_z = {}
    quality_z = {}
    daily_rets = {}
    
    for ticker, close in closes.items():
        close = close.reindex(common_index)
        daily_rets[ticker] = close.pct_change()

        hist = mock_fundamentals_store.get_fundamentals_history(ticker)
        if not hist.empty:
            hist["as_of"] = pd.to_datetime(hist["as_of"])
            hist = hist.sort_values("as_of")
            
            # Forward fill the PIT fundamentals onto the daily common_index
            daily_fund = pd.merge_asof(
                pd.DataFrame(index=common_index),
                hist,
                left_index=True,
                right_on="as_of",
                direction="backward"
            )
            daily_fund.index = common_index
            
            pb = pd.to_numeric(daily_fund["pb_ratio"], errors="coerce")
            val_factor = 1.0 / pb.replace(0.0, np.nan)
            value_z[ticker] = val_factor
            
            roe = pd.to_numeric(daily_fund["roe"], errors="coerce")
            opm = pd.to_numeric(daily_fund["operating_margin"], errors="coerce")
            quality_z[ticker] = roe + opm
        else:
            value_z[ticker] = pd.Series(np.nan, index=common_index)
            quality_z[ticker] = pd.Series(np.nan, index=common_index)

    value_df = pd.DataFrame(value_z)
    quality_df = pd.DataFrame(quality_z)
    rets_df = pd.DataFrame(daily_rets)

    def _xsec_zscore(df: pd.DataFrame) -> pd.DataFrame:
        mean = df.mean(axis=1)
        std = df.std(axis=1)
        z = df.sub(mean, axis=0).div(std.replace(0.0, np.nan), axis=0)
        return z.clip(lower=-3.0, upper=3.0)

    val_xz = _xsec_zscore(value_df)
    qual_xz = _xsec_zscore(quality_df)
    composite = (val_xz + qual_xz) / 2.0

    weights = composite.rank(axis=1, pct=True).ge(0.5).astype(float)
    weights = weights.div(weights.sum(axis=1).replace(0.0, np.nan), axis=0).fillna(0.0)
    portfolio_returns = (weights.shift(1) * rets_df).sum(axis=1).fillna(0.0)

    X = pd.DataFrame(index=common_index)
    X["Value_Composite"] = val_xz.mean(axis=1).fillna(0.0)
    X["Quality_Composite"] = qual_xz.mean(axis=1).fillna(0.0)
    y = rets_df.mean(axis=1).fillna(0.0)

    precomputed = {"Multifactor_Value_Quality": portfolio_returns}

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
    def mock_universe_fn(as_of_date): return TICKERS

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
        strategy_name="Multifactor_Value_Quality_Test",
    )

    assert not np.isnan(report.sharpe)
    assert not np.isnan(report.max_dd)
    assert isinstance(report.deployable, bool)
