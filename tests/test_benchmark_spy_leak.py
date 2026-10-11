"""
tests/test_benchmark_spy_leak.py
================================
Regression coverage for the daemon's benchmark-SPY leak
(docs/known_issues/daemon_benchmark_spy_leak.md).

``main_orchestrator.fetch_all_data_async`` adds ``BENCHMARK_SYMBOLS`` (SPY) to
tech_raw as reference data for the HMM, RS-vs-SPY/beta and Dual Momentum.
``ProcessingEngine.compile_dashboard`` makes a row for every tech_raw key, so
SPY used to become a full universe row -- forecast, strategy-evaluated, put in
the advisory queue source, traded on the paper ledger and ranked in the
cross-sectional momentum pass -- even when nobody held or watched it.

Pins:
  * ``_drop_benchmark_only_rows``: drops a benchmark row only when it is not
    in the cycle universe; case-insensitive; never raises.
  * A full ``run_pipeline`` cycle with SPY in tech_raw but not in the universe
    has no SPY row, still fills RS vs SPY (the benchmark is still used), and
    ranks xsec momentum over the universe only.
  * A held/watched SPY (in the universe) keeps its row.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from main_orchestrator import BENCHMARK_SYMBOLS, run_pipeline
from data_engine import MockDataEngine
from pipeline.production_steps import _compute_xsec_momentum, _drop_benchmark_only_rows


def _dash(*symbols) -> pd.DataFrame:
    return pd.DataFrame({"Symbol": list(symbols), "Price": [1.0] * len(symbols)})


class TestDropBenchmarkOnlyRows:
    def test_drops_benchmark_not_in_universe(self):
        out = _drop_benchmark_only_rows(_dash("AAPL", "SPY", "MSFT"), ["AAPL", "MSFT"], ("SPY",))
        assert out["Symbol"].tolist() == ["AAPL", "MSFT"]
        assert list(out.index) == [0, 1]

    def test_keeps_benchmark_in_universe(self):
        # A held/watched SPY must keep its row, or the advisory path could never SELL it.
        out = _drop_benchmark_only_rows(_dash("AAPL", "SPY"), ["AAPL", "SPY"], ("SPY",))
        assert out["Symbol"].tolist() == ["AAPL", "SPY"]

    def test_case_insensitive_both_ways(self):
        out = _drop_benchmark_only_rows(_dash("aapl", "spy"), ["AAPL"], ("SPY",))
        assert out["Symbol"].tolist() == ["aapl"]
        kept = _drop_benchmark_only_rows(_dash("AAPL", "SPY"), ["aapl", "spy"], ("SPY",))
        assert kept["Symbol"].tolist() == ["AAPL", "SPY"]

    def test_empty_or_none_universe_still_drops_benchmark(self):
        for universe in (None, []):
            out = _drop_benchmark_only_rows(_dash("SPY", "AAPL"), universe, ("SPY",))
            assert out["Symbol"].tolist() == ["AAPL"]

    def test_non_benchmark_rows_outside_universe_are_untouched(self):
        # Only benchmarks are filtered here; other strays are a different problem
        # (AdvisoryOverlayStep's "evaluated anyway" warning still surfaces them).
        out = _drop_benchmark_only_rows(_dash("AAPL", "ZZZ"), ["AAPL"], ("SPY",))
        assert out["Symbol"].tolist() == ["AAPL", "ZZZ"]

    def test_no_benchmark_rows_returns_same_frame(self):
        df = _dash("AAPL")
        assert _drop_benchmark_only_rows(df, ["AAPL"], ("SPY",)) is df

    @pytest.mark.parametrize("bad", [None, pd.DataFrame(), pd.DataFrame({"Ticker": ["SPY"]})])
    def test_bad_input_returned_unchanged(self, bad):
        assert _drop_benchmark_only_rows(bad, ["AAPL"], ("SPY",)) is bad

    def test_logs_dropped_symbols(self, caplog):
        with caplog.at_level("INFO"):
            _drop_benchmark_only_rows(_dash("AAPL", "SPY"), ["AAPL"], ("SPY",))
        assert any("SPY" in r.getMessage() and "benchmark" in r.getMessage() for r in caplog.records)


def _pipeline_inputs(universe, extra_tech=("SPY",)):
    """MockDataEngine inputs shaped like fetch_all_data_async's output:
    fundamentals for the universe, tech for universe + benchmark."""
    de = MockDataEngine()
    tech_symbols = list(dict.fromkeys(list(universe) + list(extra_tech)))
    return (
        list(universe),
        de.fetch_macro_raw(),
        de.fetch_fundamentals_raw(list(universe)),
        de.fetch_technical_raw(tech_symbols),
        de,
    )


class TestPipelineExcludesBenchmarkRow:
    def test_benchmark_constant_is_spy(self):
        assert "SPY" in BENCHMARK_SYMBOLS

    def test_spy_not_in_universe_has_no_dashboard_row(self, disable_historical_store):
        tickers, macro_raw, fund_raw, tech_raw, de = _pipeline_inputs(["AAPL", "MSFT"])
        assert "SPY" in tech_raw  # precondition: the benchmark really is in tech_raw
        final_df, _macro, _ctx = run_pipeline(tickers, macro_raw, fund_raw, tech_raw, data_engine=de)
        assert "SPY" not in set(final_df["Symbol"].astype(str).str.upper())
        assert set(final_df["Symbol"]) == {"AAPL", "MSFT"}

    def test_benchmark_still_feeds_relative_strength(self, disable_historical_store):
        tickers, macro_raw, fund_raw, tech_raw, de = _pipeline_inputs(["AAPL", "MSFT"])
        final_df, _macro, _ctx = run_pipeline(tickers, macro_raw, fund_raw, tech_raw, data_engine=de)
        assert "RS vs SPY" in final_df.columns
        assert final_df["RS vs SPY"].notna().all()

    def test_held_spy_keeps_its_row(self, disable_historical_store):
        tickers, macro_raw, fund_raw, tech_raw, de = _pipeline_inputs(["AAPL", "SPY"])
        final_df, _macro, _ctx = run_pipeline(tickers, macro_raw, fund_raw, tech_raw, data_engine=de)
        assert "SPY" in set(final_df["Symbol"])

    def test_xsec_ranks_cover_universe_only(self, disable_historical_store):
        # MockDataEngine bars are too short (30) for the 12-1m window, so use
        # 300-bar trends where SPY sits mid-pack: including it would move every
        # universe rank (2 of 3 -> 2 of 4, etc.).
        tickers = ["AAPL", "MSFT", "GOOG"]
        drifts = {"AAPL": 0.0015, "MSFT": 0.0005, "GOOG": -0.0005, "SPY": 0.0010}
        tech_raw = {t: _trend_ohlcv(300, d) for t, d in drifts.items()}
        de = MockDataEngine()
        final_df, _macro, shared = run_pipeline(
            tickers, de.fetch_macro_raw(), de.fetch_fundamentals_raw(tickers), tech_raw, data_engine=de,
        )

        _, universe_ranks = _compute_xsec_momentum({t: tech_raw[t] for t in tickers})
        _, leaky_ranks = _compute_xsec_momentum(tech_raw)
        assert len(universe_ranks) == 3  # precondition: enough history to rank
        assert universe_ranks["MSFT"] != pytest.approx(leaky_ranks["MSFT"])  # SPY would move it

        assert "SPY" not in shared.xsec_percentile_ranks
        got = final_df.set_index("Symbol")["XSec_Momentum_Rank"]
        for t in tickers:
            assert got[t] == pytest.approx(universe_ranks[t])


def _trend_ohlcv(n: int, drift: float) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    close = 100.0 * np.exp(drift * np.arange(n))
    return pd.DataFrame(
        {"Open": close, "High": close * 1.01, "Low": close * 0.99, "Close": close, "Volume": 1_000_000.0},
        index=dates,
    )
