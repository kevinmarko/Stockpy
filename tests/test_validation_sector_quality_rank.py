"""
InvestYo Quant Platform - Sector Quality Rank (SNEQR) Validation Test
=========================================================================
Runs the REAL ``scripts.refresh_validations._build_sector_quality_rank_adapter``
(registered as ``STRATEGY_REGISTRY["sector_quality_rank"]``, joined to the
``sector-quality-rank`` Pilot) over REAL SEC EDGAR company facts and REAL
yfinance price history, through the REAL ``StrategyValidationHarness.run()``
with an explicit ``t1`` -- the one adapter in this codebase that exercises
``CombinatorialPurgedCV``'s native (Date, Ticker) ``pd.MultiIndex`` support
(PR #648) end-to-end against real data, rather than a flat single-DatetimeIndex
panel.

Network-dependent (real SEC EDGAR + yfinance calls) -- deselected in CI via
``pytest -m "not network"``, matching every other real-data validation test
in this repo (``tests/test_validation_edgar_pit_strategies.py`` etc.).

Honesty (CONSTRAINT #4): asserts the harness report is well-formed (finite
Sharpe/MaxDD/DSR, PBO in [0,1], deployable is a bool) but NEVER hardcodes an
expected PBO/DSR/Sharpe/MaxDD value or asserts ``deployable is True`` --
those are recorded, not enforced, in docs/VALIDATION_STRATEGY_FIX_LOG.md and
docs/signals/sector_quality_rank.md's Backtest Validation section.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import yfinance as yf

from execution.cost_model import TieredCostModel
from validation.harness import StrategyValidationHarness
from scripts.refresh_validations import (
    SNEQR_UNIVERSE,
    _build_sector_quality_rank_adapter,
)

pytestmark = pytest.mark.network


@pytest.fixture(scope="module")
def real_closes() -> pd.DataFrame:
    data = {}
    for ticker in SNEQR_UNIVERSE:
        df = yf.download(ticker, start="2015-01-01", end="2024-12-31", progress=False)
        if df is not None and not df.empty:
            df.index = pd.to_datetime(df.index)
            data[ticker] = df["Close"].squeeze()
    assert len(data) >= 10, "Failed to download enough tickers for a meaningful cross-section"
    common_index = None
    for s in data.values():
        common_index = s.index if common_index is None else common_index.intersection(s.index)
    assert common_index is not None and len(common_index) > 300
    return pd.DataFrame({t: s.reindex(common_index) for t, s in data.items()})


class TestAdapterAgainstRealEdgarData:
    def test_builds_a_real_multiindex_panel(self, real_closes: pd.DataFrame) -> None:
        X, y, (strategy_fn, t1) = _build_sector_quality_rank_adapter(real_closes, {})

        assert isinstance(X.index, pd.MultiIndex)
        assert list(X.index.names) == ["Date", "Ticker"]
        assert X.index.get_level_values(0).is_monotonic_increasing
        assert not X.empty
        assert y.index.equals(X.index)
        assert t1.index.equals(X.index)

        # Real EDGAR coverage sanity check (not a fabrication guard on the
        # NUMBERS -- just confirming the network call actually returned
        # something rather than silently degrading to all-NaN across the
        # board, which would make the rest of this test suite meaningless).
        assert X["accrual_ratio"].notna().any(), (
            "no real accrual_ratio values at all -- EDGAR fetch may have failed"
        )
        assert X["gross_profitability"].notna().any(), (
            "no real gross_profitability values at all -- EDGAR fetch may have failed"
        )
        # Loosened to a subset check (2026-08 universe widening): SNEQR_UNIVERSE
        # is now _XSEC_UNIVERSE_CAPPED (a 100-name slice of the real S&P 500
        # roster), which clears MIN_SECTOR_SIZE for several more sectors than
        # the old hand-picked 12-ticker list did -- an exact-set assertion
        # would be brittle against live CSV/roster data. Technology and
        # Consumer Defensive have always cleared the bar; that's still
        # asserted, just not that they're the only ones that do.
        assert {"Technology", "Consumer Defensive"}.issubset(set(X["sector"].unique()))

    def test_full_harness_run_is_well_formed(self, real_closes: pd.DataFrame, tmp_path) -> None:
        """The actual end-to-end validation this Pilot's honest backtest
        depends on. Never asserts deployable is True/False -- only that
        every reported number is finite/sane."""
        X, y, (strategy_fn, t1) = _build_sector_quality_rank_adapter(real_closes, {})

        harness = StrategyValidationHarness(
            strategy_fn=strategy_fn,
            universe_fn=lambda _d: SNEQR_UNIVERSE,
            cost_model=TieredCostModel(),
            n_cpcv_splits=10,
            n_test_splits=2,
            reports_dir=str(tmp_path),
        )
        date_level = X.index.get_level_values(0)
        report = harness.run(
            start_date=str(date_level.min().date()),
            end_date=str(date_level.max().date()),
            X=X, y=y, strategy_name="sector_quality_rank_test", t1=t1,
        )

        assert isinstance(report.deployable, bool)
        assert np.isfinite(report.sharpe)
        assert np.isfinite(report.max_dd)
        assert 0.0 <= report.pbo <= 1.0
        assert np.isfinite(report.dsr)
        summary = report.to_summary_dict()
        assert summary["strategy_id"] == "sector_quality_rank_test"
