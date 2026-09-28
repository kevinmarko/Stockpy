"""tests/test_harness_equity_cli.py -- ``validation.harness``'s CLI after the
options-validation path was removed (2026-09, step 4a).

The single ``--strategy`` equity path is unchanged; the options-only
``--strategies`` bulk mode, ``--ticker`` and ``run_options_validation`` are
gone. Fully offline: the harness is replaced by a fake.
"""
from __future__ import annotations

import sys

import numpy as np
import pytest

import validation.harness as harness_mod
from validation.harness import ValidationReport, main


def _dummy_report(name: str, **overrides) -> ValidationReport:
    kwargs = dict(
        name=name,
        start_date="2020-01-01",
        end_date="2024-12-31",
        sharpe=1.5,
        sortino=1.0,
        calmar=1.0,
        max_dd=0.05,
        turnover=0.05,
        hit_rate=0.55,
        avg_trade_pct=0.001,
        dsr=0.99,
        pbo=0.1,
        bias_report={},
        walk_forward_60_40=1.0,
        walk_forward_70_30=1.0,
        walk_forward_80_20=1.0,
        distribution=np.array([1.0, 1.1]),
        paths=[],
        n_trials=10,
        is_options_selling=False,
    )
    kwargs.update(overrides)
    return ValidationReport(**kwargs)


def test_strategy_runs_buyhold_placeholder(monkeypatch, capsys):
    harness_calls = []

    class _FakeHarness:
        def __init__(self, **kw):
            harness_calls.append(kw)

        def run(self, **kw):
            harness_calls.append(kw)
            return _dummy_report("SPY_Buy_and_Hold")

    monkeypatch.setattr("validation.harness.StrategyValidationHarness", _FakeHarness)
    monkeypatch.setattr("universe_engine.get_sp500_constituents", lambda: ["AAPL", "MSFT"])
    monkeypatch.setattr(
        sys, "argv",
        ["validation.harness", "--strategy", "some_placeholder_strategy",
         "--start", "2021-01-01", "--end", "2022-01-01"],
    )

    main()

    run_kw = [kw for kw in harness_calls if "strategy_name" in kw]
    assert run_kw and run_kw[0]["strategy_name"] == "some_placeholder_strategy"
    assert run_kw[0]["start_date"] == "2021-01-01"
    assert run_kw[0]["end_date"] == "2022-01-01"
    assert "STRATEGY VALIDATION COMPLETE: some_placeholder_strategy" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [
        ["--strategies", "Iron Condor"],
        ["--strategy", "x", "--ticker", "SPY"],
        [],
    ],
)
def test_removed_options_flags_are_rejected(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["validation.harness"] + argv)
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 2


def test_options_validation_path_removed():
    assert not hasattr(harness_mod.StrategyValidationHarness, "run_options_validation")
    for name in ("_run_options_bulk", "_print_options_bulk_summary"):
        assert not hasattr(harness_mod, name)
