"""Unit tests for ``ValidationReport``'s universe-coverage fail-closed gate.

Motivating bug (see docs/known_issues/xsec_universe_coverage_concurrency_variance.md
and docs/VALIDATION_STRATEGY_FIX_LOG.md's matching dated entry): a cross-sectional
strategy's whole PBO/DSR/Sharpe/MaxDD verdict depends on its ENTIRE declared universe.
`scripts/refresh_validations.py`'s `_download_closes`/`_download_ohlcv` correctly drop
tickers that failed to fetch this run (never fabricate — CONSTRAINT #4), but that means
a run whose universe was only PARTIALLY fetched (e.g. concurrent FMP rate-limit
throttling under multiple simultaneous validation runs sharing one machine) can compute
a confident-looking, otherwise-passing verdict off a random subset of tickers that
flips run-to-run with no code changes — with nothing in the report distinguishing it
from a genuinely well-measured run.

These tests pin the fix: `ValidationReport.universe_coverage_ok` / `.deployable` fail
closed when tracked coverage falls below `validation.thresholds.MIN_UNIVERSE_COVERAGE_PCT`,
and `to_summary_dict()` surfaces both fields so a low-coverage run is never silently
absorbed into an undistinguished `deployable=True/False`.

Fully offline: constructs ``ValidationReport`` directly (no network, no real backtest),
mirroring ``tests/test_harness_equity_curve.py``'s ``_dummy_report(**overrides)`` pattern.
"""
from __future__ import annotations

import glob

import numpy as np
import pandas as pd
import pytest

from validation.harness import ValidationReport
from validation.thresholds import MIN_UNIVERSE_COVERAGE_PCT


def _dummy_report(**overrides) -> ValidationReport:
    """Construct a ValidationReport with the minimum required positional args,
    defaulting to metrics that pass every OTHER deployability gate (PBO=0.2 <
    0.5, DSR=0.96 > 0.95, Sharpe=1.0 > 0.5, MaxDD=0.1 < 0.3) — so any test
    below that ends up ``deployable=False`` is failing on the universe-
    coverage gate specifically, not incidentally failing some other gate.
    """
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


def _coverage(fetched: int, requested: int, missing=None) -> dict:
    return {
        "requested": requested,
        "fetched": fetched,
        "coverage_pct": fetched / requested,
        "missing": missing if missing is not None else [],
    }


class TestUniverseCoverageOkBackwardCompat:
    def test_none_coverage_is_ok(self) -> None:
        """A caller that never tracked coverage (universe_coverage=None) — e.g.
        the harness's own built-in single-ticker SPY fallback in run() — must
        not be retroactively penalized. Matches is_options_selling=False
        skipping the stress gate."""
        report = _dummy_report(universe_coverage=None)
        assert report.universe_coverage_ok is True

    def test_none_coverage_does_not_affect_deployable(self) -> None:
        report = _dummy_report(universe_coverage=None)
        assert report.deployable is True

    def test_default_constructor_arg_is_none(self) -> None:
        report = _dummy_report()
        assert report.universe_coverage is None
        assert report.universe_coverage_ok is True


class TestUniverseCoverageGating:
    def test_full_coverage_is_ok_and_deployable(self) -> None:
        report = _dummy_report(universe_coverage=_coverage(500, 500))
        assert report.universe_coverage_ok is True
        assert report.deployable is True

    def test_low_coverage_forces_universe_coverage_ok_false(self) -> None:
        report = _dummy_report(universe_coverage=_coverage(300, 500))  # 60%
        assert report.universe_coverage_ok is False

    def test_low_coverage_forces_deployable_false_despite_passing_metrics(self) -> None:
        """THE regression test: identical, otherwise-fully-passing PBO/DSR/
        Sharpe/MaxDD must NOT be silently absorbed into a confident
        deployable=True verdict when only 60% of the declared universe was
        actually fetched this run. This is the exact failure mode the audit
        found — a strategy's verdict silently depending on which random
        ticker subset happened to download."""
        full_coverage_report = _dummy_report(universe_coverage=_coverage(500, 500))
        low_coverage_report = _dummy_report(universe_coverage=_coverage(300, 500))

        # Same underlying PBO/DSR/Sharpe/MaxDD (both reports built from the
        # identical _dummy_report defaults) -- coverage is the ONLY variable.
        assert full_coverage_report.pbo == low_coverage_report.pbo
        assert full_coverage_report.dsr == low_coverage_report.dsr
        assert full_coverage_report.sharpe == low_coverage_report.sharpe
        assert full_coverage_report.max_dd == low_coverage_report.max_dd

        assert full_coverage_report.deployable is True
        assert low_coverage_report.deployable is False

    def test_low_coverage_alone_is_the_binding_constraint(self) -> None:
        """Every other individual gate genuinely passes for the low-coverage
        report -- it is ONLY universe_coverage_ok that is False. Guards
        against a future edit accidentally coupling the two checks."""
        report = _dummy_report(universe_coverage=_coverage(300, 500))
        assert report.pbo < 0.5
        assert report.dsr > 0.95
        assert report.sharpe > 0.5
        assert report.max_dd < 0.30
        assert report.stress_gate_passed is True
        assert report.universe_coverage_ok is False
        assert report.deployable is False


class TestUniverseCoverageBoundary:
    @pytest.mark.parametrize(
        "coverage_pct,expected_ok",
        [
            (MIN_UNIVERSE_COVERAGE_PCT, True),  # exactly at threshold: >=, passes
            (MIN_UNIVERSE_COVERAGE_PCT + 0.001, True),
            (MIN_UNIVERSE_COVERAGE_PCT - 0.001, False),
        ],
    )
    def test_threshold_is_inclusive_of_the_boundary(
        self, coverage_pct: float, expected_ok: bool
    ) -> None:
        report = _dummy_report(
            universe_coverage={
                "requested": 1000,
                "fetched": int(round(coverage_pct * 1000)),
                "coverage_pct": coverage_pct,
                "missing": [],
            }
        )
        assert report.universe_coverage_ok is expected_ok


class TestUniverseCoverageSummaryDict:
    def test_full_coverage_surfaced_in_summary(self) -> None:
        cov = _coverage(500, 500)
        report = _dummy_report(universe_coverage=cov)
        summary = report.to_summary_dict()
        assert summary["universe_coverage"] == cov
        assert summary["universe_coverage_ok"] is True

    def test_low_coverage_surfaced_in_summary_never_silently_absorbed(self) -> None:
        """The core visibility requirement: a low-coverage run's JSON summary
        must carry BOTH the coverage detail AND the derived ok/not-ok flag —
        not just a bare deployable=False indistinguishable from a genuine
        PBO/DSR/Sharpe/MaxDD failure."""
        cov = _coverage(300, 500, missing=["AAPL", "MSFT"])
        report = _dummy_report(universe_coverage=cov)
        summary = report.to_summary_dict()

        assert summary["deployable"] is False
        assert summary["universe_coverage"] == cov
        assert summary["universe_coverage"]["coverage_pct"] == pytest.approx(0.6)
        assert summary["universe_coverage"]["missing"] == ["AAPL", "MSFT"]
        assert summary["universe_coverage_ok"] is False

    def test_none_coverage_surfaced_as_none_in_summary(self) -> None:
        report = _dummy_report(universe_coverage=None)
        summary = report.to_summary_dict()
        assert summary["universe_coverage"] is None
        assert summary["universe_coverage_ok"] is True


class TestUniverseCoverageHtmlReport:
    """End-to-end (real Jinja render, no mocking of ``_render_html_report``)
    confirmation that ``reports/validation_report_template.html.j2`` actually
    renders the universe-coverage section — the user-facing surface this fix
    exists to add, not just the underlying data model. Mirrors
    ``tests/test_harness_equity_curve.py``'s offline real-``run()`` pattern
    (stubs only the network-touching survivorship-bias universe lookup)."""

    @staticmethod
    def _stub_universe(monkeypatch):
        monkeypatch.setattr(
            "validation.harness.get_universe_with_survivorship_warning",
            lambda _d: (
                ["SYN"],
                {"n_current": 1, "n_at_date": 1, "n_delisted_in_period": 0, "estimated_bias_pct": 0.5},
            ),
        )

    def _run(self, tmp_path, monkeypatch, *, universe_coverage):
        from execution.cost_model import TieredCostModel
        from validation.harness import StrategyValidationHarness

        self._stub_universe(monkeypatch)

        idx = pd.date_range("2015-01-01", periods=300, freq="B")
        rng = np.random.default_rng(11)
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
            reports_dir=str(tmp_path),
        )
        harness.run(
            start_date="2015-01-01",
            end_date="2016-03-01",
            X=X,
            y=y,
            strategy_name="coverage_html_test",
            universe_coverage=universe_coverage,
        )

        html_files = glob.glob(str(tmp_path / "validation_coverage_html_test_*.html"))
        assert html_files, f"no validation HTML report written to {tmp_path}"
        return open(html_files[0], encoding="utf-8").read()

    def test_low_coverage_renders_fail_badge_and_missing_tickers(
        self, tmp_path, monkeypatch
    ) -> None:
        html = self._run(
            tmp_path,
            monkeypatch,
            universe_coverage={
                "requested": 500,
                "fetched": 300,
                "coverage_pct": 0.6,
                "missing": ["AAPL", "MSFT", "GOOGL"],
            },
        )
        assert "Universe Coverage" in html
        assert "COVERAGE GATE: FAIL" in html
        assert "300 of 500" in html
        assert "AAPL" in html and "MSFT" in html and "GOOGL" in html
        # The forced-False verdict is visible in the top badge too.
        assert "REJECTED (FAIL)" in html

    def test_full_coverage_renders_pass_badge(self, tmp_path, monkeypatch) -> None:
        html = self._run(
            tmp_path,
            monkeypatch,
            universe_coverage={
                "requested": 500,
                "fetched": 500,
                "coverage_pct": 1.0,
                "missing": [],
            },
        )
        assert "Universe Coverage" in html
        assert "COVERAGE GATE: PASS" in html
        assert "500 of 500" in html

    def test_untracked_coverage_omits_section_entirely(
        self, tmp_path, monkeypatch
    ) -> None:
        """universe_coverage=None (the default, e.g. a caller that never
        tracked it) must not render a misleading coverage section at all."""
        html = self._run(tmp_path, monkeypatch, universe_coverage=None)
        assert "Universe Coverage" not in html
