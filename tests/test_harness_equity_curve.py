"""Unit tests for the validation harness's persisted equity curve.

Fully offline: exercises the pure ``_build_equity_curve`` helper and the
``ValidationReport.to_summary_dict()`` contract directly (no yfinance, no real
backtest). The curve feeds the Pilots PWA performance chart via
``pilots/performance.py``; these tests lock in its shape and honesty rules.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from validation.harness import (
    MAX_EQUITY_CURVE_POINTS,
    ValidationReport,
    _build_equity_curve,
    _build_macro_benchmark_curve,
)


def _dummy_report(**overrides):
    """Construct a ValidationReport with the minimum required positional args."""
    kwargs = dict(
        name="unit",
        start_date="2020-01-01",
        end_date="2024-12-31",
        sharpe=1.0,
        sortino=1.0,
        calmar=1.0,
        max_dd=0.1,
        turnover=0.05,
        hit_rate=0.55,
        avg_trade_pct=0.001,
        dsr=0.96,
        pbo=0.2,
        bias_report={},
        walk_forward_60_40=1.0,
        walk_forward_70_30=1.0,
        walk_forward_80_20=1.0,
        distribution=np.array([1.0, 1.1]),
        paths=[],
        n_trials=10,
    )
    kwargs.update(overrides)
    return ValidationReport(**kwargs)


class TestBuildEquityCurve:
    def test_base_100_ascending_from_positive_returns(self):
        idx = pd.date_range("2020-01-01", periods=250, freq="B")
        r = pd.Series(0.001, index=idx)  # constant positive drift
        curve = _build_equity_curve(r)
        assert curve, "a real return series must yield a curve"
        assert curve[0]["value"] > 100.0  # (1.001)^1 * 100 after first compound
        assert curve[-1]["value"] > curve[0]["value"]
        assert all(set(p) == {"date", "value"} for p in curve)

    def test_downsampled_to_cap(self):
        idx = pd.date_range("2015-01-01", periods=2000, freq="B")
        rng = np.random.default_rng(1)
        r = pd.Series(rng.normal(0.0004, 0.01, size=2000), index=idx)
        curve = _build_equity_curve(r)
        assert 2 <= len(curve) <= MAX_EQUITY_CURVE_POINTS
        # dates are ISO and strictly increasing
        dates = [p["date"] for p in curve]
        assert dates == sorted(dates)

    def test_all_zero_returns_empty(self):
        idx = pd.date_range("2020-01-01", periods=100, freq="B")
        assert _build_equity_curve(pd.Series(0.0, index=idx)) == []

    def test_empty_returns_empty(self):
        assert _build_equity_curve(pd.Series([], dtype=float)) == []

    def test_none_returns_empty(self):
        assert _build_equity_curve(None) == []

    def test_nans_are_dropped_not_fabricated(self):
        idx = pd.date_range("2020-01-01", periods=100, freq="B")
        r = pd.Series(0.001, index=idx)
        r.iloc[:10] = np.nan
        curve = _build_equity_curve(r)
        assert curve  # remaining 90 points still build a curve
        assert all(np.isfinite(p["value"]) for p in curve)


class TestSummaryContract:
    def test_to_summary_dict_emits_equity_curve(self):
        pts = [
            {"date": "2020-01-31", "value": 100.0},
            {"date": "2020-02-28", "value": 101.5},
        ]
        summary = _dummy_report(equity_curve=pts).to_summary_dict()
        assert summary["equity_curve"] == pts

    def test_absent_curve_defaults_to_empty_list(self):
        # No equity_curve passed -> [] (never None/missing), so consumers can
        # rely on the key existing.
        summary = _dummy_report().to_summary_dict()
        assert summary["equity_curve"] == []

    def test_to_summary_dict_emits_benchmark_curve(self):
        pts = [
            {"date": "2020-01-31", "value": 100.0},
            {"date": "2020-02-28", "value": 100.8},
        ]
        summary = _dummy_report(benchmark_curve=pts).to_summary_dict()
        assert summary["benchmark_curve"] == pts

    def test_absent_benchmark_curve_defaults_to_empty_list(self):
        # No benchmark_curve passed -> [] (never None/missing), mirroring
        # equity_curve so the Pilots read path can rely on the key existing.
        summary = _dummy_report().to_summary_dict()
        assert summary["benchmark_curve"] == []

    def test_to_summary_dict_emits_macro_benchmark_curve(self):
        pts = [
            {"date": "2020-01-31", "value": 100.0},
            {"date": "2020-02-28", "value": 101.3},
        ]
        summary = _dummy_report(macro_benchmark_curve=pts).to_summary_dict()
        assert summary["macro_benchmark_curve"] == pts

    def test_absent_macro_benchmark_curve_defaults_to_empty_list(self):
        # No macro_benchmark_curve passed -> [] (never None/missing), mirroring
        # equity_curve/benchmark_curve so the Pilots read path can rely on the key.
        summary = _dummy_report().to_summary_dict()
        assert summary["macro_benchmark_curve"] == []

    def test_macro_benchmark_curve_independent_of_benchmark_curve(self):
        # The two fields are DISTINCT, independently-set series (not aliases).
        bench = [{"date": "2020-01-31", "value": 100.0},
                 {"date": "2020-02-28", "value": 100.5}]
        macro = [{"date": "2020-01-31", "value": 100.0},
                 {"date": "2020-02-28", "value": 101.9}]
        summary = _dummy_report(
            benchmark_curve=bench, macro_benchmark_curve=macro
        ).to_summary_dict()
        assert summary["benchmark_curve"] == bench
        assert summary["macro_benchmark_curve"] == macro


class TestBuildMacroBenchmarkCurve:
    """The pure ``_build_macro_benchmark_curve`` helper (SPY fetch stubbed)."""

    def _idx(self, n=120):
        return pd.date_range("2020-01-01", periods=n, freq="B")

    def test_spy_unavailable_yields_empty(self, monkeypatch):
        idx = self._idx()
        monkeypatch.setattr(
            "validation.harness._spy_return_series",
            lambda oos_index, s, e: None,
        )
        y = pd.Series(0.001, index=idx)
        assert _build_macro_benchmark_curve(idx, y, "2020-01-01", "2020-06-30") == []

    def test_real_spy_series_builds_base100_curve(self, monkeypatch):
        idx = self._idx()
        rng = np.random.default_rng(11)
        spy = pd.Series(rng.normal(0.0004, 0.009, size=len(idx)), index=idx)
        monkeypatch.setattr(
            "validation.harness._spy_return_series",
            lambda oos_index, s, e: spy.reindex(oos_index),
        )
        # Underlying is a DISTINCT series -> not redundant -> real macro curve.
        y = pd.Series(rng.normal(0.0006, 0.011, size=len(idx)), index=idx)
        curve = _build_macro_benchmark_curve(idx, y, "2020-01-01", "2020-06-30")
        assert isinstance(curve, list) and len(curve) >= 2
        assert all(set(p) == {"date", "value"} for p in curve)
        assert curve[0]["value"] == pytest.approx(100.0, rel=0.05)
        assert all(np.isfinite(p["value"]) and p["value"] > 0 for p in curve)

    def test_underlying_is_spy_is_redundant_empty(self, monkeypatch):
        """If the strategy's own underlying already IS SPY, the separate macro
        overlay is redundant -> [] (never a duplicate/fabricated line)."""
        idx = self._idx()
        rng = np.random.default_rng(5)
        spy = pd.Series(rng.normal(0.0004, 0.009, size=len(idx)), index=idx)
        monkeypatch.setattr(
            "validation.harness._spy_return_series",
            lambda oos_index, s, e: spy.reindex(oos_index),
        )
        # underlying == SPY (same series) -> redundancy guard fires.
        curve = _build_macro_benchmark_curve(idx, spy.copy(), "2020-01-01", "2020-06-30")
        assert curve == []

    def test_never_raises_on_bad_input(self, monkeypatch):
        monkeypatch.setattr(
            "validation.harness._spy_return_series",
            lambda oos_index, s, e: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        # A raising SPY fetch degrades to [] (CONSTRAINT #6), never propagates.
        assert _build_macro_benchmark_curve(
            self._idx(), None, "2020-01-01", "2020-06-30"
        ) == []


class TestRunBenchmarkAlignment:
    """The honest benchmark (buy-&-hold of the underlying `y`) is aligned to the
    SAME OOS index as the strategy equity curve. Exercised through a minimal
    offline StrategyValidationHarness.run() — no yfinance, no real backtest."""

    @pytest.fixture(autouse=True)
    def _offline_spy(self, monkeypatch):
        # run() now also builds a SPY macro overlay via _spy_return_series, which
        # would hit the network. Default it to None so every run() in this class
        # stays offline (macro persists []); positive macro tests override it.
        monkeypatch.setattr(
            "validation.harness._spy_return_series",
            lambda oos_index, start_date, end_date: None,
        )

    @staticmethod
    def _stub_universe(monkeypatch):
        # Keep the harness fully offline: stub the Wikipedia-scraping universe
        # loader so run() never touches the network (deterministic bias report).
        monkeypatch.setattr(
            "validation.harness.get_universe_with_survivorship_warning",
            lambda _d: (["SYN"], {"n_current": 1, "n_at_date": 1,
                                  "n_delisted_in_period": 0, "estimated_bias_pct": 0.5}),
        )

    def _run(self):
        from execution.cost_model import TieredCostModel
        from validation.harness import StrategyValidationHarness

        idx = pd.date_range("2015-01-01", periods=400, freq="B")
        rng = np.random.default_rng(7)
        # Underlying (benchmark) daily returns and a distinct strategy return path.
        y = pd.Series(rng.normal(0.0003, 0.008, size=len(idx)), index=idx)
        strat = pd.Series(rng.normal(0.0005, 0.010, size=len(idx)), index=idx)
        X = pd.DataFrame({"feat": np.arange(len(idx), dtype=float)}, index=idx)

        def strategy_fn(X_tr, y_tr, X_te, y_te):
            return [{
                "params": "s",
                "train_returns": strat.loc[strat.index.intersection(y_tr.index)],
                "test_returns": strat.loc[strat.index.intersection(y_te.index)],
                "turnover": 0.01,
            }]

        harness = StrategyValidationHarness(
            strategy_fn=strategy_fn,
            universe_fn=lambda _d: ["SYN"],
            cost_model=TieredCostModel(),
            n_cpcv_splits=4,
            n_test_splits=2,
            reports_dir=str(self._tmp),
        )
        return harness.run(
            start_date="2015-01-01",
            end_date="2016-07-01",
            X=X,
            y=y,
            strategy_name="synthetic_bench",
        )

    def test_benchmark_curve_persisted_and_aligned(self, tmp_path, monkeypatch):
        self._stub_universe(monkeypatch)
        self._tmp = tmp_path
        report = self._run()
        summary = report.to_summary_dict()
        eq = summary["equity_curve"]
        bench = summary["benchmark_curve"]
        # Both real, base-100, {date, value}-shaped.
        assert isinstance(eq, list) and len(eq) >= 2
        assert isinstance(bench, list) and len(bench) >= 2
        assert all(set(p) == {"date", "value"} for p in bench)
        # Base-100 indexed (first point is ~100 after one compound), all finite.
        assert bench[0]["value"] == pytest.approx(100.0, rel=0.05)
        assert all(np.isfinite(p["value"]) and p["value"] > 0 for p in bench)
        # Aligned to the SAME downsampled OOS dates as the strategy curve.
        assert [p["date"] for p in bench] == [p["date"] for p in eq]
        # A genuine benchmark, not a copy of the strategy line.
        assert bench != eq

    def test_no_y_yields_empty_benchmark(self, tmp_path, monkeypatch):
        """When the underlying return series is degenerate (all-zero), the
        benchmark honestly persists [] — never a fabricated line (CONSTRAINT #4)."""
        from execution.cost_model import TieredCostModel
        from validation.harness import StrategyValidationHarness

        self._stub_universe(monkeypatch)
        idx = pd.date_range("2015-01-01", periods=300, freq="B")
        rng = np.random.default_rng(3)
        y = pd.Series(0.0, index=idx)  # flat underlying -> no meaningful benchmark
        strat = pd.Series(rng.normal(0.0005, 0.01, size=len(idx)), index=idx)
        X = pd.DataFrame({"feat": np.arange(len(idx), dtype=float)}, index=idx)

        def strategy_fn(X_tr, y_tr, X_te, y_te):
            return [{
                "params": "s",
                "train_returns": strat.loc[strat.index.intersection(y_tr.index)],
                "test_returns": strat.loc[strat.index.intersection(y_te.index)],
                "turnover": 0.01,
            }]

        harness = StrategyValidationHarness(
            strategy_fn=strategy_fn,
            universe_fn=lambda _d: ["SYN"],
            cost_model=TieredCostModel(),
            n_cpcv_splits=4,
            n_test_splits=2,
            reports_dir=str(tmp_path),
        )
        report = harness.run(
            start_date="2015-01-01", end_date="2016-03-01",
            X=X, y=y, strategy_name="flat_bench",
        )
        assert report.to_summary_dict()["benchmark_curve"] == []

    def test_macro_benchmark_persisted_and_aligned(self, tmp_path, monkeypatch):
        """A real (stubbed) SPY series is persisted as macro_benchmark_curve,
        aligned to the SAME downsampled OOS dates as the strategy equity curve
        and DISTINCT from both the strategy curve and the underlying benchmark."""
        self._stub_universe(monkeypatch)
        self._tmp = tmp_path
        # Override the autouse offline stub with a real synthetic SPY series
        # (distinct from the harness's y/strat), reindexed to whatever OOS index
        # run() passes in.
        rng = np.random.default_rng(21)
        spy_full = pd.Series(
            rng.normal(0.0002, 0.007, size=400),
            index=pd.date_range("2015-01-01", periods=400, freq="B"),
        )
        monkeypatch.setattr(
            "validation.harness._spy_return_series",
            lambda oos_index, s, e: spy_full.reindex(oos_index),
        )
        report = self._run()
        summary = report.to_summary_dict()
        eq = summary["equity_curve"]
        bench = summary["benchmark_curve"]
        macro = summary["macro_benchmark_curve"]
        assert isinstance(macro, list) and len(macro) >= 2
        assert all(set(p) == {"date", "value"} for p in macro)
        assert macro[0]["value"] == pytest.approx(100.0, rel=0.05)
        assert all(np.isfinite(p["value"]) and p["value"] > 0 for p in macro)
        # Aligned to the same downsampled OOS dates as the strategy curve.
        assert [p["date"] for p in macro] == [p["date"] for p in eq]
        # A genuine separate overlay — not a copy of the strategy or the
        # underlying-benchmark line.
        assert macro != eq
        assert macro != bench

    def test_spy_unavailable_yields_empty_macro(self, tmp_path, monkeypatch):
        """With SPY unavailable (autouse stub -> None), macro honestly persists
        [] — never a fabricated line (CONSTRAINT #4)."""
        self._stub_universe(monkeypatch)
        self._tmp = tmp_path
        report = self._run()  # autouse _offline_spy keeps SPY unavailable
        assert report.to_summary_dict()["macro_benchmark_curve"] == []

    def test_underlying_is_spy_yields_empty_macro_redundant(self, tmp_path, monkeypatch):
        """When the strategy's underlying already IS SPY, the separate SPY macro
        overlay is redundant with benchmark_curve -> persists [] (not a duplicate)."""
        self._stub_universe(monkeypatch)
        self._tmp = tmp_path
        from execution.cost_model import TieredCostModel
        from validation.harness import StrategyValidationHarness

        idx = pd.date_range("2015-01-01", periods=400, freq="B")
        rng = np.random.default_rng(31)
        y = pd.Series(rng.normal(0.0003, 0.008, size=len(idx)), index=idx)
        strat = pd.Series(rng.normal(0.0005, 0.010, size=len(idx)), index=idx)
        X = pd.DataFrame({"feat": np.arange(len(idx), dtype=float)}, index=idx)
        # SPY fetch returns EXACTLY the underlying y -> redundancy guard fires.
        monkeypatch.setattr(
            "validation.harness._spy_return_series",
            lambda oos_index, s, e: y.reindex(oos_index),
        )

        def strategy_fn(X_tr, y_tr, X_te, y_te):
            return [{
                "params": "s",
                "train_returns": strat.loc[strat.index.intersection(y_tr.index)],
                "test_returns": strat.loc[strat.index.intersection(y_te.index)],
                "turnover": 0.01,
            }]

        harness = StrategyValidationHarness(
            strategy_fn=strategy_fn,
            universe_fn=lambda _d: ["SYN"],
            cost_model=TieredCostModel(),
            n_cpcv_splits=4,
            n_test_splits=2,
            reports_dir=str(tmp_path),
        )
        report = harness.run(
            start_date="2015-01-01", end_date="2016-07-01",
            X=X, y=y, strategy_name="spy_underlying",
        )
        summary = report.to_summary_dict()
        # The underlying benchmark is real; the macro (SPY) overlay is redundant.
        assert isinstance(summary["benchmark_curve"], list) and len(summary["benchmark_curve"]) >= 2
        assert summary["macro_benchmark_curve"] == []
