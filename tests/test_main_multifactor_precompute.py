"""
tests/test_main_multifactor_precompute.py
==========================================
Offline unit tests for main.py's multifactor raw-input pre-compute wiring:

  - _fetch_fundamentals_for_universe()  — universe-wide fundamentals fetch,
    dead-lettered per symbol.
  - _build_realized_vol_60d_map()       — per-ticker 60-day realized vol via
    ProcessingEngine.calculate_momentum_metrics().
  - _build_context_extras()             — end-to-end: with real bars +
    fundamentals, signals/multifactor.py's pre_compute() must actually
    populate context.multifactor_scores instead of silently scoring 0
    (the gap main.py's advisory path had before this wiring was added).

All network I/O is monkeypatched; settings.HISTORICAL_STORE_ENABLED is
disabled per-test (see tests/conftest.py::disable_historical_store) so
_fetch_fundamentals_for_universe falls straight through to the fake market
provider instead of touching the real on-disk HistoricalStore.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Set

import numpy as np
import pandas as pd
import pytest

import main as m
from dto_models import FundamentalDataDTO, MacroEconomicDTO
from processing_engine import ProcessingEngine


# ---------------------------------------------------------------------------
# Fixtures / factories
# ---------------------------------------------------------------------------

def _make_bars_df(n: int = 320, start_price: float = 100.0, seed: int = 0) -> pd.DataFrame:
    """Deterministic synthetic OHLCV history, long enough (>= 275 rows) to
    clear both the xsec-momentum REQUIRED floor and the 253-row floor
    calculate_momentum_metrics() needs before it stops returning all-NaN."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    returns = rng.normal(0.0004, 0.01, size=n)
    close = start_price * np.cumprod(1.0 + returns)
    return pd.DataFrame(
        {
            "Open": close,
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": np.full(n, 1_000_000),
        },
        index=dates,
    )


def _make_fund_info(market_cap: float = 5_000_000_000.0) -> Dict[str, Any]:
    """A yfinance-.info-shaped dict with enough fields to clear the
    multifactor microcap threshold and populate all five raw inputs."""
    return {
        "shortName": "Test Co",
        "sector": "Technology",
        "trailingPE": 15.0,
        "priceToBook": 2.5,
        "bookValue": 40.0,
        "trailingEps": 6.0,
        "dividendYield": 0.01,
        "payoutRatio": 0.2,
        "marketCap": market_cap,
        "currentPrice": 100.0,
        "beta": 1.1,
        "returnOnEquity": 0.18,
        "operatingMargins": 0.22,
        "grossMargins": 0.55,
    }


class _FakeMarket:
    """Minimal MarketDataProvider stand-in exposing only get_fundamentals()
    (the only method _fetch_fundamentals_for_universe calls when
    HistoricalStore is disabled)."""

    def __init__(self, fundamentals: Dict[str, Dict[str, Any]], fail_symbols: Optional[Set[str]] = None):
        self._fundamentals = fundamentals
        self._fail_symbols = fail_symbols or set()

    def get_fundamentals(self, symbol: str) -> Dict[str, Any]:
        if symbol in self._fail_symbols:
            raise RuntimeError(f"simulated fundamentals failure for {symbol}")
        return self._fundamentals.get(symbol, {})


def _neutral_macro_dto() -> MacroEconomicDTO:
    return MacroEconomicDTO(
        yield_curve_10y_2y=0.5,
        high_yield_oas=3.5,
        inflation_rate=3.0,
        nominal_10y=4.5,
        vix_value=18.0,
        sahm_rule_indicator=0.0,
    )


# ---------------------------------------------------------------------------
# _fetch_fundamentals_for_universe
# ---------------------------------------------------------------------------

class TestFetchFundamentalsForUniverse:
    def test_happy_path_builds_dto_per_symbol(self, disable_historical_store) -> None:
        market = _FakeMarket({"AAPL": _make_fund_info(), "MSFT": _make_fund_info(market_cap=2e9)})

        result = m._fetch_fundamentals_for_universe(["AAPL", "MSFT"], market)

        assert set(result.keys()) == {"AAPL", "MSFT"}
        assert isinstance(result["AAPL"], FundamentalDataDTO)
        assert result["AAPL"].pb_ratio == 2.5
        assert result["AAPL"].raw_info["marketCap"] == 5_000_000_000.0

    def test_bad_symbol_is_dead_lettered_not_raised(self, disable_historical_store) -> None:
        market = _FakeMarket(
            {"AAPL": _make_fund_info(), "BAD": _make_fund_info()},
            fail_symbols={"BAD"},
        )

        result = m._fetch_fundamentals_for_universe(["AAPL", "BAD"], market)

        assert "AAPL" in result
        assert "BAD" not in result

    def test_empty_fundamentals_dict_is_skipped(self, disable_historical_store) -> None:
        market = _FakeMarket({"AAPL": _make_fund_info(), "NODATA": {}})

        result = m._fetch_fundamentals_for_universe(["AAPL", "NODATA"], market)

        assert "AAPL" in result
        assert "NODATA" not in result


# ---------------------------------------------------------------------------
# _build_realized_vol_60d_map
# ---------------------------------------------------------------------------

class TestBuildRealizedVol60dMap:
    def test_sufficient_history_produces_finite_vol(self) -> None:
        bars = {"AAPL": _make_bars_df(n=320, seed=1)}
        pe = ProcessingEngine()

        result = m._build_realized_vol_60d_map(bars, pe)

        assert "AAPL" in result
        assert result["AAPL"] > 0.0
        assert np.isfinite(result["AAPL"])

    def test_insufficient_history_is_excluded_not_fabricated(self) -> None:
        bars = {"NEWCO": _make_bars_df(n=100, seed=2)}  # < 253-row floor
        pe = ProcessingEngine()

        result = m._build_realized_vol_60d_map(bars, pe)

        assert "NEWCO" not in result

    def test_does_not_mutate_caller_bars_dict(self) -> None:
        """calculate_momentum_metrics()'s early-return branch (< 253 rows)
        mutates its argument in place; _build_realized_vol_60d_map must pass
        a copy so the shared bars_dict used elsewhere in the pre-compute pass
        is never silently altered."""
        df = _make_bars_df(n=100, seed=3)
        original_columns = list(df.columns)
        bars = {"NEWCO": df}
        pe = ProcessingEngine()

        m._build_realized_vol_60d_map(bars, pe)

        assert list(df.columns) == original_columns


# ---------------------------------------------------------------------------
# _build_context_extras — end-to-end multifactor wiring
# ---------------------------------------------------------------------------

class TestBuildContextExtrasMultifactor:
    def test_multifactor_scores_populated_when_fundamentals_available(
        self, disable_historical_store
    ) -> None:
        """The gap this test guards against: before this wiring, main.py's
        universe_df never carried book_to_market/earnings_yield/
        quality_factor_score/low_vol_score/log_market_cap, so
        MultifactorSignal.pre_compute() logged 'missing raw input columns'
        and context.multifactor_scores stayed empty for every run. With real
        bars + fundamentals supplied, it must now actually populate scores."""
        symbols = ["AAPL", "MSFT", "GOOG"]
        bars_dict = {s: _make_bars_df(n=320, seed=i) for i, s in enumerate(symbols)}
        market = _FakeMarket({s: _make_fund_info(market_cap=5e9 + i * 1e9) for i, s in enumerate(symbols)})

        extras = m._build_context_extras(symbols, bars_dict, _neutral_macro_dto(), market)

        assert "multifactor_scores" in extras
        assert len(extras["multifactor_scores"]) == len(symbols)
        for sym in symbols:
            entry = extras["multifactor_scores"][sym]
            assert "Multifactor_Composite" in entry
            assert np.isfinite(entry["Multifactor_Composite"])

    def test_degrades_to_nan_composites_on_total_fundamentals_failure(
        self, disable_historical_store
    ) -> None:
        """CONSTRAINT #6/#4: a fundamentals-fetch outage must not abort the
        whole pre-compute pass, and must never fabricate factor exposure.
        signals/multifactor.py's own contract (see its compute() docstring)
        is: every universe_df row gets an entry in multifactor_scores, but a
        row with no real inputs gets a NaN/microcap-excluded placeholder --
        compute() is what turns that into a neutral 0.0 score downstream, not
        an absent dict key. xsec ranks (Step 1, unrelated to fundamentals)
        must still be produced even when Step 1b fails entirely."""
        symbols = ["AAPL", "MSFT"]
        bars_dict = {s: _make_bars_df(n=320, seed=i) for i, s in enumerate(symbols)}
        market = _FakeMarket({}, fail_symbols=set(symbols))  # every symbol raises

        extras = m._build_context_extras(symbols, bars_dict, _neutral_macro_dto(), market)

        scores = extras.get("multifactor_scores", {})
        assert set(scores.keys()) == set(symbols)
        for sym in symbols:
            composite = scores[sym]["Multifactor_Composite"]
            assert composite is None or (isinstance(composite, float) and np.isnan(composite))
        # xsec ranks are independent of the fundamentals path and must still work.
        assert len(extras.get("xsec_percentile_ranks", {})) == len(symbols)

    def test_missing_symbol_gets_neutral_not_fabricated_score(
        self, disable_historical_store
    ) -> None:
        """A symbol with no fundamentals available at all must not receive a
        fabricated factor exposure. Its Market Cap defaults to NaN ->
        fillna(0.0) < MULTIFACTOR_MICROCAP_THRESHOLD, so
        signals/multifactor.py's own microcap-exclusion path (not a missing
        dict entry) is what neutralizes it -- compute() maps
        excluded_microcap=True to a 0.0 score, never a fabricated one.

        Two real-data symbols (AAPL, MSFT) are included alongside NODATA so
        the cross-sectional z-score population clears _zscore_winsorize()'s
        own >= 2 valid-observation floor -- with only one eligible ticker the
        composite is legitimately NaN too ("insufficient population"), which
        would conflate that separate degrade path with this test's actual
        target (a single missing-data ticker among real peers)."""
        symbols = ["AAPL", "MSFT", "NODATA"]
        bars_dict = {s: _make_bars_df(n=320, seed=i) for i, s in enumerate(symbols)}
        market = _FakeMarket({"AAPL": _make_fund_info(), "MSFT": _make_fund_info(market_cap=3e9)})

        extras = m._build_context_extras(symbols, bars_dict, _neutral_macro_dto(), market)

        scores = extras.get("multifactor_scores", {})
        assert np.isfinite(scores["AAPL"]["Multifactor_Composite"])
        assert scores["NODATA"]["excluded_microcap"] is True
        assert np.isnan(scores["NODATA"]["Multifactor_Composite"])
