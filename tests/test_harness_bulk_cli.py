"""tests/test_harness_bulk_cli.py — ``validation.harness``'s bulk (``--strategies``)
CLI mode.

Mirrors ``tests/test_refresh_validations.py::TestMainCLI``'s conventions (monkeypatch
the strategy-runner, exercise ``main()`` end-to-end, assert on exit code / stdout).
Fully offline: ``StrategyValidationHarness.run_options_validation`` is always
monkeypatched, so no real backtest/network I/O runs.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from validation.harness import StrategyValidationHarness, ValidationReport, main


def _dummy_report(name: str, **overrides) -> ValidationReport:
    """Construct a ValidationReport with the minimum required positional args
    (mirrors tests/test_harness_equity_curve.py's ``_dummy_report`` helper)."""
    kwargs = dict(
        name=name,
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
        # False by default so `stress_gate_passed` (which fails closed without
        # real stress_test_results for an options-selling report) doesn't make
        # every dummy report un-deployable regardless of its sharpe/pbo/dsr/maxdd
        # -- these tests exercise bulk-mode plumbing, not the stress gate itself.
        is_options_selling=False,
    )
    kwargs.update(overrides)
    return ValidationReport(**kwargs)


def _run_main_argv(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["validation.harness"] + argv)
    with pytest.raises(SystemExit) as exc_info:
        main()
    return exc_info.value.code


class TestUnknownStrategyRejection:
    def test_unknown_name_exits_2_and_never_runs_anything(self, monkeypatch, capsys):
        called = []
        monkeypatch.setattr(
            "validation.harness.StrategyValidationHarness.run_options_validation",
            classmethod(lambda cls, **kw: called.append(kw) or _dummy_report(kw["strategy_name"])),
        )

        code = _run_main_argv(monkeypatch, ["--strategies", "Not A Real Strategy"])

        assert code == 2
        assert not called, "an unknown strategy name must never trigger a real run"
        out = capsys.readouterr().out
        assert "Not A Real Strategy" in out
        assert "scripts.refresh_validations" in out  # points the operator at the right tool

    def test_mixed_known_and_unknown_rejects_the_whole_batch(self, monkeypatch, capsys):
        called = []
        monkeypatch.setattr(
            "validation.harness.StrategyValidationHarness.run_options_validation",
            classmethod(lambda cls, **kw: called.append(kw) or _dummy_report(kw["strategy_name"])),
        )

        code = _run_main_argv(
            monkeypatch, ["--strategies", "Iron Condor,Not A Real Strategy"]
        )

        assert code == 2
        assert not called, "one bad name must reject the entire batch, not just skip it"

    def test_strategy_and_strategies_are_mutually_exclusive(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["validation.harness", "--strategy", "foo", "--strategies", "Iron Condor"])
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 2


class TestBulkRun:
    def test_all_names_are_run_and_all_pass_exits_0(self, monkeypatch, capsys):
        calls = []

        def fake_run(cls, **kw):
            calls.append(kw["strategy_name"])
            return _dummy_report(kw["strategy_name"], dsr=0.99, pbo=0.1, sharpe=1.5, max_dd=0.05)

        monkeypatch.setattr(
            "validation.harness.StrategyValidationHarness.run_options_validation",
            classmethod(fake_run),
        )

        code = _run_main_argv(
            monkeypatch, ["--strategies", "Iron Condor,Put Credit Spread"]
        )

        assert code == 0
        assert sorted(calls) == ["Iron Condor", "Put Credit Spread"]
        out = capsys.readouterr().out
        assert "Iron Condor" in out and "Put Credit Spread" in out
        assert "All strategies passed" in out

    def test_one_failing_strategy_exits_1(self, monkeypatch):
        def fake_run(cls, **kw):
            if kw["strategy_name"] == "Iron Condor":
                return _dummy_report(kw["strategy_name"], dsr=0.99, pbo=0.1, sharpe=1.5, max_dd=0.05)
            return _dummy_report(kw["strategy_name"], dsr=0.10, pbo=0.9, sharpe=-0.5, max_dd=0.5)

        monkeypatch.setattr(
            "validation.harness.StrategyValidationHarness.run_options_validation",
            classmethod(fake_run),
        )

        code = _run_main_argv(
            monkeypatch, ["--strategies", "Iron Condor,Put Credit Spread"]
        )
        assert code == 1

    def test_one_raising_strategy_does_not_abort_the_batch(self, monkeypatch, capsys):
        calls = []

        def fake_run(cls, **kw):
            calls.append(kw["strategy_name"])
            if kw["strategy_name"] == "Iron Condor":
                raise RuntimeError("boom")
            return _dummy_report(kw["strategy_name"], dsr=0.99, pbo=0.1, sharpe=1.5, max_dd=0.05)

        monkeypatch.setattr(
            "validation.harness.StrategyValidationHarness.run_options_validation",
            classmethod(fake_run),
        )

        code = _run_main_argv(
            monkeypatch, ["--strategies", "Iron Condor,Put Credit Spread", "--json"]
        )

        assert code == 1
        assert sorted(calls) == ["Iron Condor", "Put Credit Spread"]
        out = capsys.readouterr().out
        assert "ERROR" in out
        last_line = out.strip().splitlines()[-1]
        payload = json.loads(last_line)
        assert payload["Iron Condor"]["deployable"] is False
        assert "boom" in payload["Iron Condor"]["error"]
        assert payload["Put Credit Spread"]["deployable"] is True

    def test_workers_flag_is_accepted_and_still_runs_every_name(self, monkeypatch):
        calls = []

        def fake_run(cls, **kw):
            calls.append(kw["strategy_name"])
            return _dummy_report(kw["strategy_name"], dsr=0.99, pbo=0.1, sharpe=1.5, max_dd=0.05)

        monkeypatch.setattr(
            "validation.harness.StrategyValidationHarness.run_options_validation",
            classmethod(fake_run),
        )

        code = _run_main_argv(
            monkeypatch,
            ["--strategies", "Iron Condor,Put Credit Spread,Long Straddle", "--workers", "3"],
        )

        assert code == 0
        assert sorted(calls) == ["Iron Condor", "Long Straddle", "Put Credit Spread"]

    def test_json_output_shape_on_all_pass(self, monkeypatch, capsys):
        def fake_run(cls, **kw):
            return _dummy_report(kw["strategy_name"], dsr=0.99, pbo=0.1, sharpe=1.5, max_dd=0.05)

        monkeypatch.setattr(
            "validation.harness.StrategyValidationHarness.run_options_validation",
            classmethod(fake_run),
        )

        code = _run_main_argv(monkeypatch, ["--strategies", "Iron Condor", "--json"])
        assert code == 0

        out = capsys.readouterr().out
        last_line = out.strip().splitlines()[-1]
        payload = json.loads(last_line)
        assert set(payload.keys()) == {"Iron Condor"}
        entry = payload["Iron Condor"]
        assert entry["deployable"] is True
        for key in ("pbo", "dsr", "sharpe", "max_drawdown"):
            assert key in entry


def _synthetic_price_df(n_days: int = 400, seed: int = 7) -> pd.DataFrame:
    """Mirrors tests/test_options_harness.py's own helper of the same name."""
    start_dt = datetime(2022, 1, 1)
    dates = [start_dt + timedelta(days=i) for i in range(n_days)]
    rng = np.random.default_rng(seed)
    prices = [100.0]
    for _ in range(n_days - 1):
        ret = rng.normal(0.0003, 0.011)
        prices.append(prices[-1] * (1.0 + ret))
    return pd.DataFrame(
        {
            "Open": prices,
            "High": [p * 1.01 for p in prices],
            "Low": [p * 0.99 for p in prices],
            "Close": prices,
            "Volume": [1_000_000] * n_days,
        },
        index=dates,
    )


class TestRunOptionsValidationConstructorRegression:
    """Regression coverage for a pre-existing bug (confirmed present on `main`
    before this test file existed, via `git stash`): ``run_options_validation``
    constructed ``cls(strategy_fn=..., reports_dir=...)`` without the two other
    required-positional constructor args (``universe_fn``, ``cost_model``),
    raising ``TypeError`` on EVERY invocation -- so this, the only code path
    that ever gives a real per-strategy options result, had never actually
    completed via the CLI. Exercises the real (non-monkeypatched) function end
    to end with a synthetic price_df so no network call is needed."""

    def test_run_options_validation_completes_without_raising(self, tmp_path):
        df = _synthetic_price_df()
        report = StrategyValidationHarness.run_options_validation(
            strategy_name="Put Credit Spread",
            ticker="SPY",
            start_date="2022-01-01",
            end_date="2023-01-01",
            price_df=df,
            reports_dir=str(tmp_path),
        )
        assert isinstance(report, ValidationReport)
        assert report.name == "Put Credit Spread_SPY"
        # The three persistence calls made on the throwaway harness instance
        # actually wrote real output -- proves reports_dir was honored, not
        # just that no exception happened to be raised.
        assert (tmp_path / "Put_Credit_Spread_SPY_validation_summary.json").exists()

    def test_bulk_mode_end_to_end_against_a_real_run_options_validation(self, monkeypatch, tmp_path, capsys):
        # No monkeypatching of run_options_validation's own logic here -- this
        # exercises the actual bulk-CLI integration point end to end against
        # the real function, only substituting a synthetic price_df (so it's
        # still fully offline) and redirecting reports_dir to tmp_path.
        df = _synthetic_price_df()
        real_run = StrategyValidationHarness.run_options_validation.__func__

        def _inject_price_df(cls, *, strategy_name, ticker="SPY", start_date, end_date, **_ignored):
            return real_run(cls, strategy_name=strategy_name, ticker=ticker,
                             start_date=start_date, end_date=end_date,
                             price_df=df, reports_dir=str(tmp_path))

        monkeypatch.setattr(
            "validation.harness.StrategyValidationHarness.run_options_validation",
            classmethod(_inject_price_df),
        )

        monkeypatch.setattr(sys, "argv", [
            "validation.harness", "--strategies", "Put Credit Spread,Iron Condor",
            "--start", "2022-01-01", "--end", "2023-01-01",
        ])
        with pytest.raises(SystemExit) as exc_info:
            main()

        out = capsys.readouterr().out
        assert "Put Credit Spread" in out and "Iron Condor" in out
        assert "ERROR" not in out
        assert exc_info.value.code in (0, 1)  # real deployability outcome, not a crash


class TestSingleStrategyModeUnaffected:
    """Regression coverage: the pre-existing single-`--strategy` path must be
    byte-identical in behavior after adding the mutually-exclusive `--strategies`
    group."""

    def test_options_strategy_name_still_routes_to_run_options_validation(self, monkeypatch, capsys):
        called = {}

        def fake_run(cls, **kw):
            called.update(kw)
            return _dummy_report(kw["strategy_name"], dsr=0.99, pbo=0.1, sharpe=1.5, max_dd=0.05)

        monkeypatch.setattr(
            "validation.harness.StrategyValidationHarness.run_options_validation",
            classmethod(fake_run),
        )
        monkeypatch.setattr(
            sys, "argv",
            ["validation.harness", "--strategy", "Iron Condor", "--start", "2021-01-01", "--end", "2022-01-01"],
        )

        # Single-strategy mode doesn't raise SystemExit -- it falls through main()'s
        # normal return.
        main()

        assert called["strategy_name"] == "Iron Condor"
        assert called["start_date"] == "2021-01-01"
        assert called["end_date"] == "2022-01-01"
        out = capsys.readouterr().out
        assert "STRATEGY VALIDATION COMPLETE: Iron Condor" in out

    def test_non_options_strategy_name_still_runs_buyhold_placeholder(self, monkeypatch, capsys):
        harness_calls = []

        class _FakeHarness:
            def __init__(self, **kw):
                harness_calls.append(kw)

            def run(self, **kw):
                harness_calls.append(kw)
                return _dummy_report("SPY_Buy_and_Hold", dsr=0.99, pbo=0.1, sharpe=1.5, max_dd=0.05, is_options_selling=False)

        monkeypatch.setattr("validation.harness.StrategyValidationHarness", _FakeHarness)
        monkeypatch.setattr(
            "universe_engine.get_sp500_constituents", lambda: ["AAPL", "MSFT"]
        )
        monkeypatch.setattr(
            sys, "argv",
            ["validation.harness", "--strategy", "some_placeholder_strategy"],
        )

        main()

        assert any("strategy_name" in kw for kw in harness_calls)
