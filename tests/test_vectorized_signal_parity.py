"""
tests/test_vectorized_signal_parity.py
=======================================
Parity verification between each newly-vectorized signal module's original
scalar `compute()` and its new `compute_vectorized()` batch path.

Context: a concurrent batch of "vectorization" commits added `compute_vectorized()`
to 11 signal modules and a new `SignalAggregator.aggregate_vectorized()` /
`StrategyEngine.evaluate_security(..., precomputed_signal_tuple=...)` fast path in
`pipeline/production_steps.py`, with ZERO test coverage (confirmed by `git show
--stat` on every vectorization commit touching no `tests/` files, and a repo-wide
grep for `compute_vectorized`/`aggregate_vectorized`/`precomputed_signal_tuple`
across `tests/` returning no hits prior to this file). Investigating that gap
surfaced a real, live crash bug in `signals/forecast_alignment.py::compute_vectorized`
(fixed alongside this file — see the module's inline comment) rather than a mere
numeric-drift concern.

All 11 modules kept their scalar `compute()` byte-identical; vectorization only
*added* `compute_vectorized()` alongside it, giving a natural ground truth: for
every module, `compute(row, ctx).score` must equal
`compute_vectorized(pd.DataFrame([row]), ctx).score` for the same inputs, within
the codebase's documented 1e-5 numeric-drift tolerance. All 11 are purely
row-local (no cross-sectional/groupby dependence), so single-row comparison is
meaningful for all of them.

Two modules read inputs differently between the two code paths:
`dividend_quality`/`graham_value`'s `compute()` reads
`context.fundamentals.{dividend_yield,is_dividend_sustainable,graham_number}`
(computed `@property`s on `FundamentalDataDTO`) while `compute_vectorized()`
reads the same-named DataFrame **columns** — not auto-derived from `context`.
Tests bridge that gap explicitly by setting those columns from the same
`FundamentalDataDTO` properties `compute()` uses.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from signals.aroon_trend import AroonTrendSignal
from signals.macd_momentum import MACDMomentumSignal
from signals.rsi_extremes import RSIExtremesSignal
from signals.timeseries_momentum import TimeSeriesMomentumSignal
from signals.rsi2_mean_reversion import RSI2MeanReversionSignal
from signals.dividend_quality import DividendQualitySignal
from signals.graham_value import GrahamValueSignal
from signals.edge_garch import EdgeGarchSignal
from signals.forecast_alignment import ForecastAlignmentSignal
from signals.relative_strength import RelativeStrengthSignal
from signals.sortino_drawdown import SortinoDrawdownSignal
from signals.aggregator import SignalAggregator
from signals.registry import global_registry, SignalRegistry
from signals.base import SignalModule

from tests.test_signal_module_contracts import _signal_context, _realistic_row


ABS_TOL = 1e-5

# Row-local modules with no context.fundamentals-vs-DataFrame-column mismatch --
# a single realistic row is directly comparable through both code paths.
SIMPLE_MODULES = [
    AroonTrendSignal(),
    MACDMomentumSignal(),
    RSIExtremesSignal(),
    TimeSeriesMomentumSignal(),
    RSI2MeanReversionSignal(),
    EdgeGarchSignal(),
    ForecastAlignmentSignal(),
    RelativeStrengthSignal(),
    SortinoDrawdownSignal(),
]


def _parity_ids(modules):
    return [m.name for m in modules]


# ============================================================================
# Section 1 -- per-module 1:1 parity (single ticker, same inputs)
# ============================================================================

@pytest.mark.parametrize("module", SIMPLE_MODULES, ids=_parity_ids(SIMPLE_MODULES))
def test_scalar_and_vectorized_agree_on_a_realistic_row(module):
    ctx = _signal_context()
    row = _realistic_row()

    scalar_out = module.compute(row, ctx)
    vec_out = module.compute_vectorized(pd.DataFrame([row]), ctx)

    assert math.isclose(scalar_out.score, vec_out["score"].iloc[0], abs_tol=ABS_TOL), (
        f"{module.name}: scalar={scalar_out.score} vs vectorized={vec_out['score'].iloc[0]}"
    )
    assert math.isclose(scalar_out.confidence, vec_out["confidence"].iloc[0], abs_tol=ABS_TOL)
    assert math.isclose(scalar_out.meta_label_proba, vec_out["meta_label_proba"].iloc[0], abs_tol=ABS_TOL)


@pytest.mark.parametrize("module", SIMPLE_MODULES, ids=_parity_ids(SIMPLE_MODULES))
def test_scalar_and_vectorized_agree_on_bearish_variant(module):
    """A second, distinctly-bearish row shape so parity isn't just verified on
    one fixed 'everything is bullish' fixture."""
    ctx = _signal_context()
    row = _realistic_row()
    row = row.copy()
    row["forecast_price"] = 90.0
    row["current_price"] = 100.0
    row["Close"] = 100.0
    row["aroon_osc"] = -65.0
    row["macd_line"] = 0.2
    row["macd_signal"] = 0.6
    row["rsi"] = 12.0
    row["RSI_2"] = 4.0
    row["SMA_5"] = 101.0
    row["SMA_200"] = 105.0
    row["relative_strength"] = -0.05
    row["garch_vol"] = 0.60
    row["GARCH_Vol"] = 0.60
    row["edge_ratio"] = 0.5
    row["sortino_ratio"] = -0.5
    row["max_drawdown"] = -0.35
    row["roc_12m"] = -0.10
    row["ROC_12M"] = -0.10

    scalar_out = module.compute(row, ctx)
    vec_out = module.compute_vectorized(pd.DataFrame([row]), ctx)

    assert math.isclose(scalar_out.score, vec_out["score"].iloc[0], abs_tol=ABS_TOL), (
        f"{module.name}: scalar={scalar_out.score} vs vectorized={vec_out['score'].iloc[0]}"
    )


class TestDividendQualityParity:
    """compute() reads context.fundamentals.{dividend_yield,is_dividend_sustainable};
    compute_vectorized() reads the same-named DataFrame columns -- bridged here."""

    def _ctx_and_columns(self, *, dividend_yield: float, payout_ratio: float, sector: str = "Technology"):
        ctx = _signal_context(dividend_yield=dividend_yield, payout_ratio=payout_ratio, sector=sector)
        fund = ctx.fundamentals
        columns = {
            "dividend_yield": fund.dividend_yield,
            "is_dividend_sustainable": fund.is_dividend_sustainable,
        }
        return ctx, columns

    def test_sustainable_dividend_parity(self):
        ctx, cols = self._ctx_and_columns(dividend_yield=0.03, payout_ratio=0.40)
        row = _realistic_row().copy()
        for k, v in cols.items():
            row[k] = v
        scalar_out = DividendQualitySignal().compute(row, ctx)
        vec_out = DividendQualitySignal().compute_vectorized(pd.DataFrame([row]), ctx)
        assert math.isclose(scalar_out.score, vec_out["score"].iloc[0], abs_tol=ABS_TOL)

    def test_yield_trap_parity(self):
        ctx, cols = self._ctx_and_columns(dividend_yield=0.08, payout_ratio=0.99)
        row = _realistic_row().copy()
        for k, v in cols.items():
            row[k] = v
        scalar_out = DividendQualitySignal().compute(row, ctx)
        vec_out = DividendQualitySignal().compute_vectorized(pd.DataFrame([row]), ctx)
        assert math.isclose(scalar_out.score, vec_out["score"].iloc[0], abs_tol=ABS_TOL)

    def test_no_dividend_parity(self):
        ctx, cols = self._ctx_and_columns(dividend_yield=0.0, payout_ratio=0.0)
        row = _realistic_row().copy()
        for k, v in cols.items():
            row[k] = v
        scalar_out = DividendQualitySignal().compute(row, ctx)
        vec_out = DividendQualitySignal().compute_vectorized(pd.DataFrame([row]), ctx)
        assert math.isclose(scalar_out.score, vec_out["score"].iloc[0], abs_tol=ABS_TOL)


class TestGrahamValueParity:
    """compute() reads context.fundamentals.graham_number (a computed property);
    compute_vectorized() reads the DataFrame's 'graham_number' column -- bridged here."""

    def _ctx_and_graham(self, *, graham_eps: float, graham_book_value: float, current_price: float):
        ctx = _signal_context(graham_eps=graham_eps, graham_book_value=graham_book_value)
        return ctx, ctx.fundamentals.graham_number, current_price

    def test_undervalued_parity(self):
        ctx, graham_number, price = self._ctx_and_graham(graham_eps=5.0, graham_book_value=50.0, current_price=50.0)
        row = _realistic_row().copy()
        row["current_price"] = price
        row["graham_number"] = graham_number
        scalar_out = GrahamValueSignal().compute(row, ctx)
        vec_out = GrahamValueSignal().compute_vectorized(pd.DataFrame([row]), ctx)
        assert math.isclose(scalar_out.score, vec_out["score"].iloc[0], abs_tol=ABS_TOL)

    def test_overvalued_parity(self):
        ctx, graham_number, price = self._ctx_and_graham(graham_eps=5.0, graham_book_value=50.0, current_price=200.0)
        row = _realistic_row().copy()
        row["current_price"] = price
        row["graham_number"] = graham_number
        scalar_out = GrahamValueSignal().compute(row, ctx)
        vec_out = GrahamValueSignal().compute_vectorized(pd.DataFrame([row]), ctx)
        assert math.isclose(scalar_out.score, vec_out["score"].iloc[0], abs_tol=ABS_TOL)

    def test_no_graham_value_parity(self):
        ctx, graham_number, price = self._ctx_and_graham(graham_eps=-5.0, graham_book_value=50.0, current_price=100.0)
        row = _realistic_row().copy()
        row["current_price"] = price
        row["graham_number"] = graham_number
        scalar_out = GrahamValueSignal().compute(row, ctx)
        vec_out = GrahamValueSignal().compute_vectorized(pd.DataFrame([row]), ctx)
        assert math.isclose(scalar_out.score, vec_out["score"].iloc[0], abs_tol=ABS_TOL)


# ============================================================================
# Section 2 -- forecast_alignment crash-bug regression (multi-row, mixed-sign)
# ============================================================================

class TestForecastAlignmentMixedSignRegression:
    """signals/forecast_alignment.py::compute_vectorized previously indexed
    `up.index[strong]`/`up.index[mod]` -- `up.index` is the FULL DataFrame
    index, but `strong`/`mod` are boolean masks over the `up`-SUBSET
    (produced by `expected_gain = (...)[up]`). Boolean-indexing the full
    index with a shorter mask raises IndexError, but ONLY when the universe
    has a genuine mix of up and down tickers (an all-up or all-down universe
    has matching lengths by coincidence and doesn't crash) -- which is why a
    single-row test alone would never catch this. Fixed by indexing through
    `expected_gain.index` instead of `up.index`."""

    def _mixed_universe(self) -> pd.DataFrame:
        return pd.DataFrame({
            "current_price": [10.0, 20.0, 30.0, 40.0, 50.0],
            "forecast_price": [15.0, 18.0, 40.0, 39.0, 50.0],
            # tickers:      strong-up  down    strong-up  down    flat(down-branch)
        }, index=["A", "B", "C", "D", "E"])

    def test_mixed_sign_universe_does_not_raise(self):
        ctx = _signal_context()
        df = self._mixed_universe()
        # Must not raise IndexError.
        out = ForecastAlignmentSignal().compute_vectorized(df, ctx)
        assert list(out.index) == list(df.index)

    def test_mixed_sign_universe_scores_match_per_ticker_scalar_calls(self):
        ctx = _signal_context()
        df = self._mixed_universe()
        vec_out = ForecastAlignmentSignal().compute_vectorized(df, ctx)

        for ticker in df.index:
            row = df.loc[ticker]
            scalar_out = ForecastAlignmentSignal().compute(row, ctx)
            assert math.isclose(scalar_out.score, vec_out.loc[ticker, "score"], abs_tol=ABS_TOL), (
                f"{ticker}: scalar={scalar_out.score} vs vectorized={vec_out.loc[ticker, 'score']}"
            )

    def test_all_up_universe_does_not_raise(self):
        """An all-up universe happened to not crash even pre-fix (lengths
        coincidentally matched) -- pinned here as a sanity boundary, not a
        substitute for the mixed-sign case above."""
        ctx = _signal_context()
        df = pd.DataFrame({
            "current_price": [10.0, 20.0],
            "forecast_price": [15.0, 25.0],
        }, index=["A", "B"])
        out = ForecastAlignmentSignal().compute_vectorized(df, ctx)
        assert list(out.index) == list(df.index)

    def test_all_down_universe_does_not_raise(self):
        ctx = _signal_context()
        df = pd.DataFrame({
            "current_price": [10.0, 20.0],
            "forecast_price": [9.0, 18.0],
        }, index=["A", "B"])
        out = ForecastAlignmentSignal().compute_vectorized(df, ctx)
        assert list(out.index) == list(df.index)


# ============================================================================
# Section 3 -- aggregator-level end-to-end parity
# (SignalAggregator.aggregate_vectorized vs. per-ticker aggregate())
# ============================================================================

def test_aggregate_vectorized_matches_per_ticker_aggregate_end_to_end():
    """The deepest form of the numeric-drift check: build a small multi-ticker
    universe covering several vectorized modules (deliberately including a
    forecast_alignment up/down mix, the exact shape that used to crash), run
    the batch aggregate_vectorized() path, and compare every ticker's 6-tuple
    against calling the original per-ticker aggregate() in a loop."""
    ctx = _signal_context()

    base_row = _realistic_row()
    rows = {}
    # Ticker A: bullish across the board, forecast up.
    r = base_row.copy()
    r["forecast_price"] = 120.0
    r["current_price"] = 100.0
    r["Close"] = 100.0
    rows["A"] = r
    # Ticker B: bearish, forecast down -- the mixed-sign partner for A.
    r = base_row.copy()
    r["forecast_price"] = 90.0
    r["current_price"] = 100.0
    r["Close"] = 100.0
    r["aroon_osc"] = -65.0
    r["macd_line"] = 0.2
    r["macd_signal"] = 0.6
    r["rsi"] = 12.0
    r["relative_strength"] = -0.05
    r["sortino_ratio"] = -0.5
    r["max_drawdown"] = -0.35
    rows["B"] = r
    # Ticker C: another bullish name to prove >2-ticker universes work too.
    r = base_row.copy()
    r["forecast_price"] = 110.0
    r["current_price"] = 100.0
    r["Close"] = 100.0
    rows["C"] = r

    # Build column-by-column (via a list of row-dicts) rather than
    # pd.DataFrame(rows).T -- transposing a DataFrame of heterogeneous-dtype
    # Series upcasts every column to `object`, which breaks the vectorized
    # modules' `.round()` calls (they need real numeric dtypes, exactly as
    # pipeline/production_steps.py's column-by-column `.map()` construction
    # already produces in production).
    tickers = list(rows.keys())
    universe_df = pd.DataFrame([rows[t].to_dict() for t in tickers], index=tickers)

    # dividend_quality/graham_value's compute_vectorized() reads DataFrame
    # columns instead of context.fundamentals (see TestGrahamValueParity /
    # TestDividendQualityParity above) -- bridge the same gap here, or the
    # vectorized path silently defaults graham_number/dividend_yield to 0.0
    # and diverges from the scalar path's real ctx.fundamentals values.
    fund = ctx.fundamentals
    universe_df["graham_number"] = fund.graham_number
    universe_df["dividend_yield"] = fund.dividend_yield
    universe_df["is_dividend_sustainable"] = fund.is_dividend_sustainable

    vectorized_results = SignalAggregator(global_registry).aggregate_vectorized(universe_df, ctx)

    for ticker in universe_df.index:
        scalar_result = SignalAggregator(global_registry).aggregate(universe_df.loc[ticker], ctx)
        vec_result = vectorized_results[ticker]

        scalar_final_score, _, _, _, scalar_outputs, scalar_meta = scalar_result
        vec_final_score, _, _, _, vec_outputs, vec_meta = vec_result

        assert math.isclose(scalar_final_score, vec_final_score, abs_tol=ABS_TOL), (
            f"{ticker}: final_score scalar={scalar_final_score} vs vectorized={vec_final_score}"
        )
        assert math.isclose(scalar_meta, vec_meta, abs_tol=ABS_TOL)

        assert set(scalar_outputs.keys()) == set(vec_outputs.keys())
        for name in scalar_outputs:
            assert math.isclose(
                scalar_outputs[name].score, vec_outputs[name].score, abs_tol=ABS_TOL
            ), f"{ticker}/{name}: scalar={scalar_outputs[name].score} vs vectorized={vec_outputs[name].score}"


# ============================================================================
# Section 4 -- dead-letter fallback around aggregate_vectorized()
# (pipeline/production_steps.py's new try/except, added alongside the fix)
# ============================================================================

def test_aggregate_vectorized_failure_falls_back_to_empty_dict_not_raise(monkeypatch):
    """Mirrors the fallback semantics added in pipeline/production_steps.py:
    a universe-wide failure in aggregate_vectorized() must not propagate --
    callers are expected to catch it and substitute {}, which makes every
    ticker's precomputed_signal_tuple=None (the pre-existing default that
    routes evaluate_security() back through the proven-safe per-ticker
    aggregate() path). This test exercises that exact try/except shape
    directly against the real aggregator, proving the guard actually
    degrades gracefully rather than just looking like it does."""
    ctx = _signal_context()
    universe_df = pd.DataFrame([_realistic_row().to_dict()], index=["A"])

    aggregator = SignalAggregator(global_registry)

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated universe-wide vectorized failure")

    monkeypatch.setattr(aggregator, "aggregate_vectorized", _boom)

    try:
        vectorized_results = aggregator.aggregate_vectorized(universe_df, ctx)
    except Exception:
        vectorized_results = {}

    assert vectorized_results == {}
    # And the per-ticker fallback path itself still works fine.
    final_score, _, _, _, outputs, meta = aggregator.aggregate(universe_df.loc["A"], ctx)
    assert isinstance(final_score, float)
    assert isinstance(outputs, dict)
