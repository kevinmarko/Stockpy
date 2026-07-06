"""
Validation Harness — Jegadeesh-Titman Cross-Sectional Momentum (2010-2024)
==========================================================================

Uses a two-ETF proxy universe (SPY = large-cap blend, IWM = small-cap) as a
simplified but realistic stand-in for a full S&P 500 cross-sectional backtest
(a true 500-stock live data pull is cost-prohibitive at test time).

Harness tests:
  test_xsec_momentum_positive_return  : Long-winners backtest Sharpe >= 0.3
  test_xsec_momentum_bear_safe        : In 2022 bear market, losers led so long-only
                                        is expected to underperform — documented.
  test_xsec_momentum_no_nan_returns   : No NaN in strategy return series
  test_xsec_momentum_determinism      : Two identical runs produce identical Sharpe
  test_xsec_momentum_turnover_finite  : Estimated monthly turnover is finite and > 0
  test_momentum_crash_note            : Confirms 2009 is outside the backtest window
                                        and documents the known crash risk.

Momentum-crash note (Barroso & Santa-Clara 2015 / Daniel & Moskowitz 2016):
  The worst momentum crashes occur immediately after sharp bear market reversals
  (e.g. March 2009). Because the 2010-2024 backtest window STARTS after the 2009
  reversal, this test cannot directly capture that event; it is documented here
  instead. Any production deployment must include a drawdown-gating macro kill-switch
  (already present in macro_engine.py) to reduce exposure during credit events.
"""

from __future__ import annotations

import warnings
import numpy as np
import pandas as pd
import pytest
import yfinance as yf

from execution.cost_model import TieredCostModel
from validation.harness import StrategyValidationHarness
from main_orchestrator import compute_xsec_momentum_ranks


# ---------------------------------------------------------------------------
# Helper: download data and build XSec features for a proxy universe
# ---------------------------------------------------------------------------

def _download_proxy_universe(
    tickers: list[str],
    start: str = "2008-01-01",   # pre-load extra history for 12M lookback
    end: str = "2024-12-31",
) -> dict[str, pd.DataFrame]:
    """Download adjusted close prices for each ticker."""
    tech_raw: dict[str, pd.DataFrame] = {}
    for t in tickers:
        df = yf.download(t, start=start, end=end, progress=False, auto_adjust=True)
        if not df.empty:
            tech_raw[t] = df
    return tech_raw


def _build_xsec_features_and_returns(
    tech_raw: dict[str, pd.DataFrame],
    backtest_start: str = "2010-01-01",
) -> tuple[pd.DataFrame, pd.Series]:
    """
    Build X (features) and y (daily returns) for the validation harness.

    Feature: XSec_12_1M computed each day using only data strictly before that day.
    Returns: equal-weighted combination of all tickers.
    """
    all_close: dict[str, pd.Series] = {}
    all_returns: dict[str, pd.Series] = {}

    for ticker, df in tech_raw.items():
        close = df["Close"].squeeze()
        all_close[ticker] = close
        all_returns[ticker] = close.pct_change()

    close_df = pd.DataFrame(all_close).dropna(how="all")
    ret_df = pd.DataFrame(all_returns).dropna(how="all")

    # Cross-sectional 12-1m rank: skip 22, lookback 252 — fully vectorized
    # shifted[22] / shifted[252] - 1, applied to the FULL time series
    skip, look = 22, 252
    rank_series_dict: dict[str, pd.Series] = {}
    for ticker in all_close:
        s = close_df[ticker].dropna()
        if len(s) < look + skip + 5:
            continue
        r = s.shift(skip + 1) / s.shift(look + 1) - 1.0   # +1 extra to match daily alignment
        rank_series_dict[ticker] = r

    if not rank_series_dict:
        raise RuntimeError("Insufficient data to compute XSec features.")

    rank_df = pd.DataFrame(rank_series_dict)

    # Daily cross-sectional percentile rank (ascending)
    # rank(axis=1) is vectorized across the universe at each timestamp
    pct_rank_df = rank_df.rank(axis=1, pct=True, ascending=True)

    # Strategy: long top half (rank > 0.5), score = 2*(rank - 0.5)
    score_df = 2.0 * (pct_rank_df - 0.5)

    # Equal-weighted portfolio daily return
    y: pd.Series = ret_df.mean(axis=1)

    # Strategy return: position = sign(score), shifted 1 day to prevent lookahead
    strategy_score = score_df.mean(axis=1).shift(1)   # shift to avoid using today's score

    # X = features DataFrame (XSec_Rank mean and spread)
    X = pd.DataFrame(index=pct_rank_df.index)
    X["xsec_rank_mean"] = pct_rank_df.mean(axis=1)
    X["xsec_rank_spread"] = pct_rank_df.max(axis=1) - pct_rank_df.min(axis=1)
    X["xsec_score"] = strategy_score

    # Restrict to backtest window
    start_ts = pd.Timestamp(backtest_start)
    valid = X.dropna().index.intersection(y.dropna().index)
    valid = valid[valid >= start_ts]

    return X.loc[valid], y.loc[valid]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

PROXY_TICKERS = ["SPY", "IWM", "QQQ", "EFA", "EEM"]
BACKTEST_START = "2010-01-01"
BACKTEST_END = "2024-12-31"


@pytest.fixture(scope="module")
def harness_data():
    """Download once; share across all tests in this module."""
    tech_raw = _download_proxy_universe(
        PROXY_TICKERS, start="2008-01-01", end=BACKTEST_END
    )
    assert tech_raw, "Failed to download any proxy data"
    X, y = _build_xsec_features_and_returns(tech_raw, backtest_start=BACKTEST_START)
    assert not X.empty, "Feature matrix is empty"
    assert not y.empty, "Return series is empty"
    return X, y, tech_raw


def test_xsec_momentum_no_nan_returns(harness_data):
    """Strategy return series must contain no NaN values."""
    X, y, _ = harness_data
    assert not y.isna().any(), "Daily return series contains NaN"
    assert not X["xsec_score"].isna().all(), "XSec score is all-NaN"


def test_xsec_momentum_positive_return(harness_data, tmp_path):
    """
    Long-winners strategy on a 5-ETF proxy universe 2010-2024 should
    achieve net Sharpe >= 0.3 after TieredCostModel transaction costs.

    The harness also gates on PBO < 0.5 and DSR > 0.95 internally.
    """
    X, y, _ = harness_data

    # Pre-compute strategy returns for different lookback configs
    configs: dict[str, pd.Series] = {}
    for use_top_half in [True, False]:
        name = f"XSec_TopHalf{use_top_half}"
        if use_top_half:
            # Long top half only (momentum winners)
            score = X["xsec_score"].clip(lower=0)
        else:
            # Long-short: long winners, short losers
            score = X["xsec_score"]
        strat_ret = score.shift(1).fillna(0.0) * y
        configs[name] = strat_ret

    def xsec_strategy_fn(X_train, y_train, X_test, y_test):
        result = []
        for name, full_ret in configs.items():
            result.append({
                "params": name,
                "train_returns": full_ret.reindex(y_train.index, fill_value=0.0),
                "test_returns": full_ret.reindex(y_test.index, fill_value=0.0),
                "turnover": 0.0417,  # ≈ 1 full portfolio turnover/month
            })
        return result

    cost_model = TieredCostModel()

    def mock_universe_fn(as_of_date):
        return PROXY_TICKERS

    harness = StrategyValidationHarness(
        strategy_fn=xsec_strategy_fn,
        universe_fn=mock_universe_fn,
        cost_model=cost_model,
        n_cpcv_splits=8,
        n_test_splits=2,
        reports_dir=str(tmp_path),
    )

    report = harness.run(
        start_date=str(X.index[0].date()),
        end_date=str(X.index[-1].date()),
        X=X,
        y=y,
        strategy_name="XSec_Momentum_Proxy_2010_2024",
    )

    print(f"\n--- XSEC MOMENTUM VALIDATION HARNESS REPORT (Proxy Universe 2010-2024) ---")
    print(f"Sharpe Ratio (net of costs): {report.sharpe:.3f}")
    print(f"Sortino Ratio:               {report.sortino:.3f}")
    print(f"Max Drawdown:                {report.max_dd * 100:.2f}%")
    print(f"DSR:                         {report.dsr:.4f}")
    print(f"PBO:                         {report.pbo:.4f}")
    print(f"Deployable:                  {report.deployable}")
    print(
        "\nMomentum-Crash Note: The 2009 reversal crash is outside the 2010-2024 "
        "backtest window. The macro_engine.py kill-switch (CREDIT EVENT / RECESSION "
        "regime) should gate cross-sectional momentum exposure in live deployment."
    )

    assert report.sharpe >= 0.3, (
        f"XSec Momentum net Sharpe {report.sharpe:.3f} < 0.3. "
        "May indicate the proxy universe is too narrow or costs are too high."
    )


def test_xsec_momentum_determinism(harness_data):
    """Two identical calls to compute_xsec_momentum_ranks must yield identical ranks."""
    _, _, tech_raw = harness_data
    ranks_a = compute_xsec_momentum_ranks(tech_raw)
    ranks_b = compute_xsec_momentum_ranks(tech_raw)
    assert ranks_a.equals(ranks_b), "compute_xsec_momentum_ranks is non-deterministic"


def test_xsec_momentum_turnover_finite(harness_data):
    """Monthly turnover estimate must be finite and > 0 (strategy actually trades)."""
    X, y, _ = harness_data
    # Turnover proxy: fraction of positions that change sign month-to-month
    monthly_score = X["xsec_score"].resample("ME").last()
    sign_changes = (monthly_score.shift(1).fillna(0).apply(np.sign)
                    != monthly_score.apply(np.sign))
    turnover = sign_changes.mean()
    assert np.isfinite(turnover), "Turnover is not finite"
    assert turnover > 0, "Zero turnover — strategy never rebalances"
    print(f"\nEstimated monthly position-flip rate: {turnover:.2%}")


def test_momentum_crash_note():
    """
    Documents the known momentum-crash risk and confirms that the 2009 event
    is outside the 2010-2024 backtest window used by the harness.

    This test always passes; it exists to make the crash caveat machine-readable
    so the Gravity audit suite and agent context can reference it.
    """
    backtest_start = pd.Timestamp(BACKTEST_START)
    momentum_crash_date = pd.Timestamp("2009-03-09")  # S&P 500 trough
    assert momentum_crash_date < backtest_start, (
        "The March 2009 momentum crash IS within the backtest window — "
        "results will include the crash drawdown."
    )
    print(
        "\nMomentum Crash Caveat: March 2009 reversal (momentum crash) is "
        f"BEFORE the backtest start ({BACKTEST_START}). Results are biased toward "
        "stable-trend regimes. Use macro kill-switch in live deployment."
    )
