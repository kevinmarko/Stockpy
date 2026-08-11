"""
tests/test_etf_transmission.py
==============================
Pure-math unit tests for ``risk/etf_transmission.py`` -- the ETF
volatility-transmission measurement layer (Ben-David, Franzoni & Moussawi
2018, "Do ETFs Increase Volatility?", *Journal of Finance* 73(6)).

This sandbox has NO live-market network access, so everything here is
fixture-driven synthetic data. Nothing in this file makes (or asserts
anything about) a real holdings/price fetch.

The single most important test in the file is
``TestMarketResidualization::test_residualized_r2_is_materially_below_naive_r2``:
a naive R² of a stock on a sector-ETF composite is high for *every* large-cap
because both legs load on the same market factor. Residualizing both legs
against the market first is the whole point of the design, and that test is
what proves the implementation actually does it.

``ETFHolding`` is Agent B's frozen contract in ``data/etf_holdings.py``.
``risk/etf_transmission.py`` consumes it duck-typed (``holding_symbol`` /
``weight`` / ``shares_held`` / ``as_of_date``) and never imports it, so these
tests use a local stub of the same shape -- which also keeps them runnable
before/independently of that module landing.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
import pytest

from risk.etf_transmission import (
    build_etf_return_composite,
    compute_etf_ownership,
    compute_market_residual_r2,
    filter_holdings_as_of,
    primary_wrapper,
)

AS_OF = date(2026, 7, 27)


@dataclass(frozen=True)
class StubHolding:
    """Local stand-in for data.etf_holdings.ETFHolding (same field names)."""
    etf_symbol: str
    holding_symbol: str
    weight: float = float("nan")
    shares_held: float = float("nan")
    as_of_date: date = AS_OF
    source: str = "stub"


def _bars(returns: np.ndarray, start: str = "2024-01-01") -> pd.DataFrame:
    """OHLCV frame whose Close reproduces `returns` exactly via pct_change."""
    close = 100.0 * np.cumprod(1.0 + np.concatenate([[0.0], returns]))
    idx = pd.bdate_range(start=start, periods=len(close))
    return pd.DataFrame(
        {"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1e6},
        index=idx,
    )


# ── compute_etf_ownership ────────────────────────────────────────────────────


class TestComputeETFOwnership:
    def test_sums_shares_across_baskets_over_shares_outstanding(self):
        holdings = {
            "SPY": [StubHolding("SPY", "AAPL", weight=0.07, shares_held=1_000_000.0)],
            "XLK": [StubHolding("XLK", "AAPL", weight=0.22, shares_held=500_000.0)],
        }
        out = compute_etf_ownership(holdings, {"AAPL": 10_000_000.0})
        assert out["AAPL"] == pytest.approx(0.15)

    def test_missing_shares_outstanding_is_nan_not_zero(self):
        holdings = {"XLK": [StubHolding("XLK", "AAPL", shares_held=1_000.0)]}
        assert math.isnan(compute_etf_ownership(holdings, {})["AAPL"])

    def test_zero_or_negative_shares_outstanding_is_nan_not_inf(self):
        """The Market Cap / Price guard's failure mode: a fabricated 0.0 market
        cap must yield NaN, never inf and never 0.0."""
        holdings = {"XLK": [StubHolding("XLK", "AAPL", shares_held=1_000.0)]}
        assert math.isnan(compute_etf_ownership(holdings, {"AAPL": 0.0})["AAPL"])
        assert math.isnan(compute_etf_ownership(holdings, {"AAPL": -5.0})["AAPL"])

    def test_unreported_shares_held_makes_the_sum_nan_not_understated(self):
        """A basket reporting no shares_held makes the TOTAL unknowable -- the
        honest answer is NaN, not a silently smaller (understated) number."""
        holdings = {
            "SPY": [StubHolding("SPY", "AAPL", shares_held=1_000_000.0)],
            "XLK": [StubHolding("XLK", "AAPL", shares_held=float("nan"))],
        }
        assert math.isnan(compute_etf_ownership(holdings, {"AAPL": 10_000_000.0})["AAPL"])

    def test_zero_shares_held_is_a_real_zero_contribution_not_missing(self):
        """0.0 shares is a MEASURED zero, distinct from an unreported NaN."""
        holdings = {
            "SPY": [StubHolding("SPY", "AAPL", shares_held=1_000_000.0)],
            "XLK": [StubHolding("XLK", "AAPL", shares_held=0.0)],
        }
        assert compute_etf_ownership(holdings, {"AAPL": 10_000_000.0})["AAPL"] == pytest.approx(0.1)

    def test_excluded_symbols_are_omitted_entirely(self):
        """A ticker that IS an ETF scores 1.0 against its own basket -- max
        derate for a trivially wrong reason. It must not appear at all."""
        holdings = {
            "SPY": [
                StubHolding("SPY", "AAPL", shares_held=1_000.0),
                StubHolding("SPY", "XLK", shares_held=9_999.0),
            ],
        }
        out = compute_etf_ownership(
            holdings, {"AAPL": 10_000.0, "XLK": 10_000.0},
            exclude_symbols=frozenset({"XLK"}),
        )
        assert "XLK" not in out
        assert out["AAPL"] == pytest.approx(0.1)

    def test_symbol_in_no_basket_is_simply_absent(self):
        out = compute_etf_ownership({"SPY": []}, {"AAPL": 1_000.0})
        assert out == {}

    def test_market_proxy_ownership_is_counted(self):
        """Unlike the return composite, ownership INCLUDES the market proxy --
        being wrapped by the biggest basket in the market is the exposure."""
        holdings = {"SPY": [StubHolding("SPY", "AAPL", shares_held=2_000.0)]}
        assert compute_etf_ownership(holdings, {"AAPL": 10_000.0})["AAPL"] == pytest.approx(0.2)

    def test_duplicate_rows_are_deduped_not_double_counted(self):
        """Two snapshots of the same (etf, symbol) must not sum."""
        holdings = {
            "XLK": [
                StubHolding("XLK", "AAPL", shares_held=1_000.0, as_of_date=date(2026, 6, 30)),
                StubHolding("XLK", "AAPL", shares_held=1_200.0, as_of_date=date(2026, 7, 27)),
            ],
        }
        out = compute_etf_ownership(holdings, {"AAPL": 10_000.0})
        assert out["AAPL"] == pytest.approx(0.12)  # newest row only, not 0.22

    def test_never_raises_on_garbage_input(self):
        assert compute_etf_ownership(None, None) == {}
        assert compute_etf_ownership({"XLK": [object()]}, {"AAPL": 1.0}) == {}


# ── build_etf_return_composite ───────────────────────────────────────────────


class TestBuildETFReturnComposite:
    def test_single_wrapper_composite_equals_that_etfs_returns(self):
        rng = np.random.RandomState(7)
        xlk_r = rng.normal(0, 0.01, 120)
        holdings = {"XLK": [StubHolding("XLK", "AAPL", weight=0.2, shares_held=100.0)]}
        out = build_etf_return_composite(holdings, {"XLK": _bars(xlk_r)})
        assert np.allclose(out["AAPL"].to_numpy(), xlk_r)

    def test_two_wrappers_are_ownership_weighted_by_shares_held(self):
        rng = np.random.RandomState(11)
        a = rng.normal(0, 0.01, 120)
        b = rng.normal(0, 0.01, 120)
        holdings = {
            "XLK": [StubHolding("XLK", "AAPL", weight=0.2, shares_held=750.0)],
            "QQQ": [StubHolding("QQQ", "AAPL", weight=0.1, shares_held=250.0)],
        }
        out = build_etf_return_composite(holdings, {"XLK": _bars(a), "QQQ": _bars(b)})
        assert np.allclose(out["AAPL"].to_numpy(), 0.75 * a + 0.25 * b)

    def test_falls_back_to_nav_weight_when_shares_held_unreported(self):
        rng = np.random.RandomState(13)
        a = rng.normal(0, 0.01, 120)
        b = rng.normal(0, 0.01, 120)
        holdings = {
            "XLK": [StubHolding("XLK", "AAPL", weight=0.3)],
            "QQQ": [StubHolding("QQQ", "AAPL", weight=0.1)],
        }
        out = build_etf_return_composite(holdings, {"XLK": _bars(a), "QQQ": _bars(b)})
        assert np.allclose(out["AAPL"].to_numpy(), 0.75 * a + 0.25 * b)

    def test_neither_weighting_basis_available_yields_no_composite(self):
        holdings = {
            "XLK": [StubHolding("XLK", "AAPL")],
            "QQQ": [StubHolding("QQQ", "AAPL")],
        }
        out = build_etf_return_composite(
            holdings, {"XLK": _bars(np.zeros(50)), "QQQ": _bars(np.zeros(50))},
        )
        assert "AAPL" not in out

    def test_market_proxy_is_excluded_from_the_composite(self):
        """THE identification-limit case: a name whose only covered wrapper is
        the market proxy gets NO composite, so downstream R² is NaN."""
        holdings = {"SPY": [StubHolding("SPY", "AAPL", weight=0.07, shares_held=1e6)]}
        out = build_etf_return_composite(
            holdings, {"SPY": _bars(np.random.RandomState(3).normal(0, 0.01, 120))},
            market_proxy="SPY",
        )
        assert out == {}

    def test_market_proxy_excluded_but_sector_wrapper_retained(self):
        rng = np.random.RandomState(17)
        spy_r = rng.normal(0, 0.01, 120)
        xlk_r = rng.normal(0, 0.01, 120)
        holdings = {
            "SPY": [StubHolding("SPY", "AAPL", shares_held=9e9)],
            "XLK": [StubHolding("XLK", "AAPL", shares_held=1.0)],
        }
        out = build_etf_return_composite(
            holdings, {"SPY": _bars(spy_r), "XLK": _bars(xlk_r)}, market_proxy="SPY",
        )
        # SPY's enormous shares_held must NOT drag the composite -- it's out.
        assert np.allclose(out["AAPL"].to_numpy(), xlk_r)

    def test_etf_without_bars_does_not_contribute(self):
        rng = np.random.RandomState(19)
        xlk_r = rng.normal(0, 0.01, 120)
        holdings = {
            "XLK": [StubHolding("XLK", "AAPL", shares_held=100.0)],
            "XLF": [StubHolding("XLF", "AAPL", shares_held=100.0)],
        }
        out = build_etf_return_composite(holdings, {"XLK": _bars(xlk_r)})
        assert np.allclose(out["AAPL"].to_numpy(), xlk_r)

    def test_never_raises_on_garbage_input(self):
        assert build_etf_return_composite(None, None) == {}
        assert build_etf_return_composite({"XLK": [object()]}, {"XLK": pd.DataFrame()}) == {}


# ── compute_market_residual_r2 -- the crux ───────────────────────────────────


def _naive_r2(stock_bars: pd.DataFrame, composite: pd.Series, window: int) -> float:
    """The WRONG measurement, kept here only as the comparison baseline."""
    aligned = pd.concat(
        [stock_bars["Close"].rename("s"), composite.rename("c")], axis=1, join="inner",
    ).sort_index()
    frame = pd.DataFrame({"s": aligned["s"].pct_change(), "c": aligned["c"]}).dropna()
    return float(frame["s"].iloc[-window:].corr(frame["c"].iloc[-window:]) ** 2)


class TestMarketResidualization:
    def test_residualized_r2_is_materially_below_naive_r2(self):
        """The whole point of the design.

        Construct a stock and a sector ETF that share a market beta but have
        INDEPENDENT idiosyncratic components -- i.e. zero genuine ETF
        transmission. A naive R² still reads high (both load on the market);
        the market-residualized partial R² must collapse toward zero.
        """
        rng = np.random.RandomState(2026)
        n = 400
        mkt = rng.normal(0.0, 0.012, n)
        stock = 1.1 * mkt + rng.normal(0.0, 0.004, n)   # idio independent of...
        etf = 1.0 * mkt + rng.normal(0.0, 0.004, n)     # ...this one

        stock_bars = _bars(stock)
        market_bars = _bars(mkt)
        composite = _bars(etf)["Close"].pct_change().dropna()

        naive = _naive_r2(stock_bars, composite, window=250)
        residual = compute_market_residual_r2(
            stock_bars, composite, market_bars, window=250, min_obs=250,
        )

        # Shared market factor makes the naive number look like strong
        # "transmission" when there is none.
        assert naive > 0.7
        # Residualizing strips it out.
        assert residual < 0.1
        assert naive - residual > 0.5

    def test_genuine_residual_comovement_survives_residualization(self):
        """Mirror case: when the stock and its wrapper DO share a
        non-market common shock, the residualized R² stays high -- the
        measurement isn't just always-near-zero."""
        rng = np.random.RandomState(4242)
        n = 400
        mkt = rng.normal(0.0, 0.012, n)
        shared = rng.normal(0.0, 0.008, n)   # non-market, ETF-transmitted shock
        stock = 1.0 * mkt + shared + rng.normal(0.0, 0.001, n)
        etf = 1.0 * mkt + shared + rng.normal(0.0, 0.001, n)

        residual = compute_market_residual_r2(
            _bars(stock), _bars(etf)["Close"].pct_change().dropna(), _bars(mkt),
            window=250, min_obs=250,
        )
        assert residual > 0.9

    def test_market_proxy_only_composite_is_nan_not_a_number(self):
        """If the composite IS the market series, e_t == 0 identically. The
        identification limit must surface as NaN, never a fabricated value."""
        rng = np.random.RandomState(5)
        n = 300
        mkt = rng.normal(0.0, 0.012, n)
        market_bars = _bars(mkt)
        composite = market_bars["Close"].pct_change().dropna()
        stock_bars = _bars(1.1 * mkt + rng.normal(0.0, 0.004, n))

        assert math.isnan(
            compute_market_residual_r2(
                stock_bars, composite, market_bars, window=120, min_obs=120,
            )
        )

    def test_r2_is_bounded_in_unit_interval(self):
        rng = np.random.RandomState(31)
        n = 200
        mkt = rng.normal(0.0, 0.01, n)
        value = compute_market_residual_r2(
            _bars(0.9 * mkt + rng.normal(0, 0.01, n)),
            _bars(1.1 * mkt + rng.normal(0, 0.01, n))["Close"].pct_change().dropna(),
            _bars(mkt), window=60, min_obs=60,
        )
        assert 0.0 <= value <= 1.0

    def test_dropping_one_midwindow_composite_date_does_not_shift_r2(self):
        """Finding 20 regression: previously the stock/market PRICE legs were
        inner-joined against the already-differenced composite return column
        BEFORE differencing -- so one date missing from the composite dropped
        the corresponding stock/market PRICE row too, and the pct_change()
        computed AFTER the join then spanned TWO days for the row immediately
        after the gap, corrupting that one return and, through it, the whole
        trailing window's cov/var/corr statistics. With returns computed
        BEFORE the join, dropping one composite date removes only that one
        row from the final 3-way alignment -- the OTHER returns are
        unaffected, so R2 over a `window` that still has plenty of remaining
        observations should barely move."""
        rng = np.random.RandomState(4242)
        n = 400
        window = 60
        mkt = rng.normal(0.0, 0.012, n)
        shared = rng.normal(0.0, 0.008, n)  # non-market, ETF-transmitted shock
        stock = 1.0 * mkt + shared + rng.normal(0.0, 0.001, n)
        etf = 1.0 * mkt + shared + rng.normal(0.0, 0.001, n)

        stock_bars = _bars(stock)
        market_bars = _bars(mkt)
        composite = _bars(etf)["Close"].pct_change().dropna()

        r2_full = compute_market_residual_r2(
            stock_bars, composite, market_bars, window=window, min_obs=window,
        )
        assert not math.isnan(r2_full)

        # Drop a single date well inside the trailing `window` (not at the
        # very tail, and not the whole-history edge), leaving stock_bars and
        # market_bars themselves fully contiguous -- the realistic scenario
        # of a composite with one missing constituent-basket date. gap_pos=-10
        # is empirically where the pre-fix join-order bug's corruption is
        # most pronounced for this seed (diff ~0.16 pre-fix vs ~0.0003
        # post-fix) -- picked by direct measurement against the reverted
        # buggy code, not tuned to make the assertion trivially pass.
        gap_date = composite.index[-10]
        composite_gapped = composite.drop(gap_date)

        r2_gapped = compute_market_residual_r2(
            stock_bars, composite_gapped, market_bars, window=window, min_obs=window,
        )
        assert not math.isnan(r2_gapped)

        assert abs(r2_full - r2_gapped) < 0.01, (
            f"a single dropped mid-window composite date shifted R2 by "
            f"{abs(r2_full - r2_gapped):.4f} (full={r2_full:.4f}, "
            f"gapped={r2_gapped:.4f}) -- the join-order bug corrupts the "
            f"return computed for the row after the gap"
        )


class TestMarketResidualR2Degradation:
    """Every honesty-contract path: NaN, never 0.0."""

    def _triple(self, n: int):
        rng = np.random.RandomState(101)
        mkt = rng.normal(0.0, 0.01, n)
        stock = _bars(0.9 * mkt + rng.normal(0, 0.005, n))
        composite = _bars(1.1 * mkt + rng.normal(0, 0.005, n))["Close"].pct_change().dropna()
        return stock, composite, _bars(mkt)

    def test_fewer_than_min_obs_is_nan(self):
        stock, composite, market = self._triple(40)
        assert math.isnan(
            compute_market_residual_r2(stock, composite, market, window=60, min_obs=60)
        )

    def test_partial_window_coverage_is_nan_never_understated(self):
        """Composition drift: a name added to a wrapper last week has no
        tethered history. NaN-until-full-window-coverage is the deliberate
        choice -- a partial-window R² would understate with a confident face."""
        stock, composite, market = self._triple(300)
        short_composite = composite.iloc[-30:]
        assert math.isnan(
            compute_market_residual_r2(
                stock, short_composite, market, window=60, min_obs=60,
            )
        )
        # ...and the same pair over a full window DOES produce a number, so the
        # NaN above is genuinely about coverage, not a broken code path.
        assert not math.isnan(
            compute_market_residual_r2(stock, composite, market, window=60, min_obs=60)
        )

    def test_empty_or_missing_inputs_are_nan(self):
        stock, composite, market = self._triple(300)
        assert math.isnan(compute_market_residual_r2(pd.DataFrame(), composite, market))
        assert math.isnan(compute_market_residual_r2(stock, pd.Series(dtype=float), market))
        assert math.isnan(compute_market_residual_r2(stock, composite, pd.DataFrame()))
        assert math.isnan(compute_market_residual_r2(None, composite, market))
        assert math.isnan(compute_market_residual_r2(stock, None, market))
        assert math.isnan(compute_market_residual_r2(stock, composite, None))

    def test_missing_close_column_is_nan(self):
        stock, composite, market = self._triple(300)
        assert math.isnan(
            compute_market_residual_r2(stock.drop(columns=["Close"]), composite, market)
        )

    def test_flat_market_series_is_nan(self):
        """Zero market variance -> no market leg to residualize against."""
        n = 200
        rng = np.random.RandomState(59)
        flat_market = _bars(np.zeros(n))
        stock = _bars(rng.normal(0, 0.01, n))
        composite = _bars(rng.normal(0, 0.01, n))["Close"].pct_change().dropna()
        assert math.isnan(
            compute_market_residual_r2(stock, composite, flat_market, window=60, min_obs=60)
        )

    def test_near_flat_market_series_is_nan_not_corrupted_r2(self):
        """Finding 27 regression: a near-flat (not bit-identical) market-proxy
        window produces a var_m that is near-zero but not exactly <= 0.0 due
        to floating-point noise -- the old exact `<= 0.0` guard let that
        near-zero value through, corrupting beta_i/beta_e (division by a
        near-zero var_m) and, downstream, R2. The fixed `_DEGENERATE_STD`
        (1e-12) guard must catch it and return NaN."""
        n = 200
        rng = np.random.RandomState(60)
        # Near-flat market with floating-point-scale noise (~1e-10), NOT
        # bit-identical -- an exact `<= 0.0` check would not catch this.
        near_flat_market = _bars(rng.normal(0, 1e-10, n))
        stock = _bars(rng.normal(0, 0.01, n))
        composite = _bars(rng.normal(0, 0.01, n))["Close"].pct_change().dropna()
        result = compute_market_residual_r2(
            stock, composite, near_flat_market, window=60, min_obs=60,
        )
        assert math.isnan(result)

    def test_disjoint_date_ranges_are_nan(self):
        rng = np.random.RandomState(71)
        stock = _bars(rng.normal(0, 0.01, 200), start="2020-01-01")
        market = _bars(rng.normal(0, 0.01, 200), start="2024-01-01")
        composite = _bars(rng.normal(0, 0.01, 200), start="2024-01-01")["Close"].pct_change().dropna()
        assert math.isnan(
            compute_market_residual_r2(stock, composite, market, window=60, min_obs=60)
        )

    def test_alignment_is_inner_join_never_forward_filled(self):
        """A gap in the market series must DROP those dates, not carry the last
        price forward (which would fabricate zero-return days)."""
        rng = np.random.RandomState(83)
        n = 300
        mkt = rng.normal(0.0, 0.01, n)
        market = _bars(mkt)
        stock = _bars(0.9 * mkt + rng.normal(0, 0.005, n))
        composite = _bars(1.1 * mkt + rng.normal(0, 0.005, n))["Close"].pct_change().dropna()

        gapped_market = market.drop(market.index[100:140])
        full = compute_market_residual_r2(stock, composite, market, window=60, min_obs=60)
        gapped = compute_market_residual_r2(stock, composite, gapped_market, window=60, min_obs=60)
        # Both finite (the gap is far from the trailing window), and the gapped
        # run is computed on strictly fewer rows -- not on ffilled ones.
        assert not math.isnan(full) and not math.isnan(gapped)


# ── primary_wrapper (operator explainability) ────────────────────────────────


class TestPrimaryWrapper:
    def test_largest_nav_weight_wins(self):
        holdings = {
            "SPY": [StubHolding("SPY", "AAPL", weight=0.07)],
            "XLK": [StubHolding("XLK", "AAPL", weight=0.22)],
        }
        assert primary_wrapper(holdings)["AAPL"] == "XLK"

    def test_falls_back_to_shares_held_when_no_weights(self):
        holdings = {
            "SPY": [StubHolding("SPY", "AAPL", shares_held=10.0)],
            "XLK": [StubHolding("XLK", "AAPL", shares_held=99.0)],
        }
        assert primary_wrapper(holdings)["AAPL"] == "XLK"

    def test_no_ranking_key_anywhere_means_absent_not_arbitrary(self):
        holdings = {"SPY": [StubHolding("SPY", "AAPL")], "XLK": [StubHolding("XLK", "AAPL")]}
        assert primary_wrapper(holdings) == {}

    def test_market_proxy_can_be_the_primary_wrapper(self):
        holdings = {"SPY": [StubHolding("SPY", "AAPL", weight=0.07)]}
        assert primary_wrapper(holdings)["AAPL"] == "SPY"

    def test_never_raises_on_garbage_input(self):
        assert primary_wrapper(None) == {}


# ── filter_holdings_as_of ────────────────────────────────────────────────────


class TestFilterHoldingsAsOf:
    def test_keeps_only_latest_row_per_constituent(self):
        holdings = {
            "XLK": [
                StubHolding("XLK", "AAPL", weight=0.1, as_of_date=date(2026, 3, 31)),
                StubHolding("XLK", "AAPL", weight=0.2, as_of_date=date(2026, 6, 30)),
                StubHolding("XLK", "MSFT", weight=0.3, as_of_date=date(2026, 6, 30)),
            ],
        }
        out = filter_holdings_as_of(holdings, as_of=AS_OF)
        assert len(out["XLK"]) == 2
        aapl = [r for r in out["XLK"] if r.holding_symbol == "AAPL"][0]
        assert aapl.weight == pytest.approx(0.2)

    def test_blank_holding_symbols_are_dropped(self):
        holdings = {"XLK": [StubHolding("XLK", "", weight=0.1)]}
        assert filter_holdings_as_of(holdings, as_of=AS_OF)["XLK"] == []

    def test_none_as_of_disables_date_filtering_only(self):
        future = StubHolding("XLK", "AAPL", weight=0.9, as_of_date=date(2099, 1, 1))
        out = filter_holdings_as_of({"XLK": [future]}, as_of=None)
        assert len(out["XLK"]) == 1
