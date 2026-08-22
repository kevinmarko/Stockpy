import logging

import pytest
from unittest import mock

import main as m
from main import (
    _read_macro_snapshot_hint,
    _run_automated_delta_hedge_cycle,
    _run_automated_options_lifecycle,
)

def test_read_macro_snapshot_hint_missing_file(monkeypatch):
    """Test that default values are returned when the file does not exist."""
    mock_settings = mock.MagicMock()
    mock_path = mock.MagicMock()
    mock_path.exists.return_value = False
    mock_settings.OUTPUT_DIR.__truediv__.return_value = mock_path

    monkeypatch.setattr("main.settings", mock_settings)

    result = _read_macro_snapshot_hint()

    assert result == {"vix": None, "market_regime": None}
    mock_path.exists.assert_called_once()

def test_read_macro_snapshot_hint_happy_path(monkeypatch):
    """Test that valid JSON data with vix and market_regime is successfully parsed."""
    mock_settings = mock.MagicMock()
    mock_path = mock.MagicMock()
    mock_path.exists.return_value = True

    # Valid JSON payload
    valid_payload = '{"vix": 15.5, "market_regime": "bull"}'
    mock_path.read_text.return_value = valid_payload
    mock_settings.OUTPUT_DIR.__truediv__.return_value = mock_path

    monkeypatch.setattr("main.settings", mock_settings)

    result = _read_macro_snapshot_hint()

    assert result == {"vix": 15.5, "market_regime": "bull"}
    mock_path.exists.assert_called_once()
    mock_path.read_text.assert_called_once_with(encoding="utf-8")

def test_read_macro_snapshot_hint_invalid_data(monkeypatch):
    """Test edge cases with invalid or missing data such as 0, None or missing fields."""
    mock_settings = mock.MagicMock()
    mock_path = mock.MagicMock()
    mock_path.exists.return_value = True
    mock_settings.OUTPUT_DIR.__truediv__.return_value = mock_path

    monkeypatch.setattr("main.settings", mock_settings)

    # Test case 1: VIX is 0, market_regime is None
    mock_path.read_text.return_value = '{"vix": 0, "market_regime": null}'
    assert _read_macro_snapshot_hint() == {"vix": None, "market_regime": None}

    # Test case 2: Missing fields
    mock_path.read_text.return_value = '{"other_key": "value"}'
    assert _read_macro_snapshot_hint() == {"vix": None, "market_regime": None}

    # Test case 3: VIX is 0.0, market_regime is empty string
    mock_path.read_text.return_value = '{"vix": 0.0, "market_regime": ""}'
    assert _read_macro_snapshot_hint() == {"vix": None, "market_regime": None}

def test_read_macro_snapshot_hint_file_io_exception(monkeypatch):
    """Test that the function handles file I/O exceptions by returning default values."""
    mock_settings = mock.MagicMock()
    mock_path = mock.MagicMock()
    mock_path.exists.return_value = True
    # Simulate a PermissionError when reading the file
    mock_path.read_text.side_effect = PermissionError("Permission denied")
    mock_settings.OUTPUT_DIR.__truediv__.return_value = mock_path

    monkeypatch.setattr("main.settings", mock_settings)

    result = _read_macro_snapshot_hint()

    assert result == {"vix": None, "market_regime": None}
    mock_path.exists.assert_called_once()
    mock_path.read_text.assert_called_once_with(encoding="utf-8")


# ---------------------------------------------------------------------------
# _run_automated_delta_hedge_cycle -- regression coverage for the fabricated-
# $500-SPY-spot / sizing-vs-fill-price-divergence bug (see
# docs/known_issues/options_risk_fabricated_spy_spot.md).
# ---------------------------------------------------------------------------

def test_run_automated_delta_hedge_cycle_threads_one_resolved_spy_spot_into_both_calls():
    """Sizing (calculate_portfolio_greeks) and fill (execute_delta_hedge) must
    be called with the IDENTICAL resolved spy_spot -- never a fabricated
    price for one and a real price for the other."""
    executor = mock.MagicMock()
    executor.store = mock.sentinel.store

    with mock.patch("pilots.price_provider.get_current_price", return_value=642.17) as mock_get_price, \
         mock.patch("pilots.options_risk.calculate_portfolio_greeks") as mock_calc_greeks, \
         mock.patch("pilots.options_hedging.execute_delta_hedge") as mock_execute_hedge:
        mock_calc_greeks.return_value = {"beta_weighted_delta_spy": 40.0}
        mock_execute_hedge.return_value = {"ok": True, "hedged": False, "action": "HOLD"}

        result = _run_automated_delta_hedge_cycle(executor)

    mock_get_price.assert_called_once_with("SPY")
    mock_calc_greeks.assert_called_once_with(store=mock.sentinel.store, spy_spot=642.17)
    mock_execute_hedge.assert_called_once_with(
        store=mock.sentinel.store,
        portfolio_greeks=mock_calc_greeks.return_value,
        spy_spot=642.17,
    )
    assert result == mock_execute_hedge.return_value


def test_run_automated_delta_hedge_cycle_skips_when_spy_quote_unavailable(caplog):
    """No live SPY quote -> the cycle is skipped entirely (fail closed) --
    neither sizing nor execution is ever attempted, and nothing is sized off
    a fabricated placeholder price."""
    executor = mock.MagicMock()
    executor.store = mock.sentinel.store

    with mock.patch("pilots.price_provider.get_current_price", return_value=0.0), \
         mock.patch("pilots.options_risk.calculate_portfolio_greeks") as mock_calc_greeks, \
         mock.patch("pilots.options_hedging.execute_delta_hedge") as mock_execute_hedge, \
         caplog.at_level(logging.WARNING, logger="InvestYo.main"):
        result = _run_automated_delta_hedge_cycle(executor)

    assert result is None
    mock_calc_greeks.assert_not_called()
    mock_execute_hedge.assert_not_called()
    assert any("no live SPY quote" in rec.message for rec in caplog.records)


def test_run_automated_delta_hedge_cycle_logs_on_real_hedged_result(caplog):
    """A real hedged=True result must actually produce the confirmation INFO
    log -- regression for the dead `_hedge_res.get('executed')` /
    `_hedge_res.get('spot_price')` key-mismatch bug (execute_delta_hedge's
    real contract uses 'hedged' and nests the fill price under
    fill['fill_price'])."""
    executor = mock.MagicMock()
    executor.store = mock.sentinel.store

    hedge_result = {
        "ok": True,
        "hedged": True,
        "action": "SELL",
        "shares": 40.0,
        "order": {"side": "sell", "qty": 40},
        "fill": {"fill_price": 642.17},
    }

    with mock.patch("pilots.price_provider.get_current_price", return_value=642.17), \
         mock.patch("pilots.options_risk.calculate_portfolio_greeks", return_value={"beta_weighted_delta_spy": 40.0}), \
         mock.patch("pilots.options_hedging.execute_delta_hedge", return_value=hedge_result), \
         caplog.at_level(logging.INFO, logger="InvestYo.main"):
        result = _run_automated_delta_hedge_cycle(executor)

    assert result == hedge_result
    messages = [rec.getMessage() for rec in caplog.records]
    assert any("Automated SPY delta hedging executed" in m for m in messages)
    assert any("sell" in m and "40" in m and "642.17" in m for m in messages)


# ---------------------------------------------------------------------------
# _run_automated_options_lifecycle -- regression coverage for the missing
# OPTIONS_0DTE_ENABLED outer-gate bug (see
# docs/known_issues/options_lifecycle_daemon_gate_gap_2026_08_22.md). Before
# the fix, the outer gate OR'd together only
# PAPER_OPTIONS_AUTO_EXECUTE_ENABLED / OPTIONS_AUTO_EXIT_ENABLED /
# OPTIONS_DELTA_HEDGE_ENABLED -- an operator enabling ONLY OPTIONS_0DTE_ENABLED
# (a documented, self-contained feature per its own settings.py docstring)
# got the whole function body silently skipped, so manage_0dte_exits() (the
# +75% profit target / -30% stop loss / 15:45 ET hard exit) never ran.
# ---------------------------------------------------------------------------

def _set_options_lifecycle_flags(
    monkeypatch,
    *,
    paper_auto_execute: bool = False,
    auto_exit: bool = False,
    delta_hedge: bool = False,
    zero_dte: bool = False,
) -> None:
    monkeypatch.setattr(m.settings, "PAPER_OPTIONS_AUTO_EXECUTE_ENABLED", paper_auto_execute)
    monkeypatch.setattr(m.settings, "OPTIONS_AUTO_EXIT_ENABLED", auto_exit)
    monkeypatch.setattr(m.settings, "OPTIONS_DELTA_HEDGE_ENABLED", delta_hedge)
    monkeypatch.setattr(m.settings, "OPTIONS_0DTE_ENABLED", zero_dte)


def test_options_lifecycle_runs_0dte_exits_when_only_0dte_flag_enabled(monkeypatch):
    """THE regression test: with ONLY OPTIONS_0DTE_ENABLED=True (the other
    three False), manage_0dte_exits() must actually be invoked. This is the
    exact configuration that silently no-op'd before the fix."""
    _set_options_lifecycle_flags(monkeypatch, zero_dte=True)

    mock_executor = mock.MagicMock()
    mock_executor.store = mock.sentinel.store

    with mock.patch(
        "execution.options_paper_executor.OptionsPaperExecutor",
        return_value=mock_executor,
    ) as mock_executor_cls, mock.patch(
        "pilots.zero_dte_engine.manage_0dte_exits",
        return_value={"evaluated_count": 1, "executed_count": 1, "failed_count": 0},
    ) as mock_manage_0dte:
        _run_automated_options_lifecycle(macro_dto=mock.sentinel.macro_dto)

    mock_executor_cls.assert_called_once()
    mock_manage_0dte.assert_called_once_with(store=mock.sentinel.store)
    # The other three steps must NOT have fired for this flag combination.
    mock_executor.execute_auto_exits.assert_not_called()
    mock_executor.execute_strategy_directives.assert_not_called()


def test_options_lifecycle_skips_everything_when_all_flags_disabled(monkeypatch):
    """All four flags False -> the executor is never even constructed and
    nothing runs. Pins the still-correct "fully disabled" side of the gate."""
    _set_options_lifecycle_flags(monkeypatch)

    with mock.patch(
        "execution.options_paper_executor.OptionsPaperExecutor",
    ) as mock_executor_cls, mock.patch(
        "pilots.zero_dte_engine.manage_0dte_exits",
    ) as mock_manage_0dte:
        _run_automated_options_lifecycle(macro_dto=mock.sentinel.macro_dto)

    mock_executor_cls.assert_not_called()
    mock_manage_0dte.assert_not_called()


def test_options_lifecycle_runs_exit_management_when_only_auto_exit_flag_enabled(monkeypatch):
    """OPTIONS_AUTO_EXIT_ENABLED alone must still independently trigger
    execute_auto_exits() -- pins this branch of the outer OR gate. It also
    drives the inner 0DTE OR-condition ("... or OPTIONS_AUTO_EXIT_ENABLED"),
    unchanged existing behavior."""
    _set_options_lifecycle_flags(monkeypatch, auto_exit=True)

    mock_executor = mock.MagicMock()
    mock_executor.store = mock.sentinel.store
    mock_executor.execute_auto_exits.return_value = {
        "evaluated_count": 2, "closed_count": 1, "failed_count": 0,
    }

    with mock.patch(
        "execution.options_paper_executor.OptionsPaperExecutor",
        return_value=mock_executor,
    ), mock.patch(
        "pilots.zero_dte_engine.manage_0dte_exits",
        return_value={"evaluated_count": 0, "executed_count": 0, "failed_count": 0},
    ) as mock_manage_0dte:
        _run_automated_options_lifecycle(macro_dto=mock.sentinel.macro_dto)

    mock_executor.execute_auto_exits.assert_called_once()
    mock_manage_0dte.assert_called_once_with(store=mock.sentinel.store)
    mock_executor.execute_strategy_directives.assert_not_called()


def test_options_lifecycle_runs_strategy_auto_execute_when_only_that_flag_enabled(monkeypatch):
    """PAPER_OPTIONS_AUTO_EXECUTE_ENABLED alone must still independently
    trigger execute_strategy_directives(macro_dto=...) -- pins this branch of
    the OR gate and confirms the real macro_dto is threaded through."""
    _set_options_lifecycle_flags(monkeypatch, paper_auto_execute=True)

    mock_executor = mock.MagicMock()
    mock_executor.store = mock.sentinel.store
    mock_executor.execute_strategy_directives.return_value = {
        "executed_count": 1, "skipped_count": 0, "failed_count": 0,
    }

    with mock.patch(
        "execution.options_paper_executor.OptionsPaperExecutor",
        return_value=mock_executor,
    ), mock.patch("pilots.zero_dte_engine.manage_0dte_exits") as mock_manage_0dte:
        _run_automated_options_lifecycle(macro_dto=mock.sentinel.macro_dto)

    mock_executor.execute_strategy_directives.assert_called_once_with(
        macro_dto=mock.sentinel.macro_dto,
    )
    mock_executor.execute_auto_exits.assert_not_called()
    # Neither OPTIONS_0DTE_ENABLED nor OPTIONS_AUTO_EXIT_ENABLED is set here,
    # so the inner 0DTE OR-condition is also False.
    mock_manage_0dte.assert_not_called()


def test_options_lifecycle_runs_delta_hedge_when_only_that_flag_enabled(monkeypatch):
    """OPTIONS_DELTA_HEDGE_ENABLED alone must still independently trigger
    _run_automated_delta_hedge_cycle() -- pins this branch of the OR gate."""
    _set_options_lifecycle_flags(monkeypatch, delta_hedge=True)

    mock_executor = mock.MagicMock()
    mock_executor.store = mock.sentinel.store

    with mock.patch(
        "execution.options_paper_executor.OptionsPaperExecutor",
        return_value=mock_executor,
    ), mock.patch(
        "pilots.zero_dte_engine.manage_0dte_exits",
    ) as mock_manage_0dte, mock.patch(
        "main._run_automated_delta_hedge_cycle",
    ) as mock_hedge_cycle:
        _run_automated_options_lifecycle(macro_dto=mock.sentinel.macro_dto)

    mock_hedge_cycle.assert_called_once_with(mock_executor)
    mock_executor.execute_auto_exits.assert_not_called()
    mock_executor.execute_strategy_directives.assert_not_called()
    mock_manage_0dte.assert_not_called()


def test_options_lifecycle_swallows_exceptions_and_logs_warning(monkeypatch, caplog):
    """A failure anywhere inside the lifecycle must never propagate --
    non-fatal to the pipeline, matching the original inline block's
    try/except contract."""
    _set_options_lifecycle_flags(monkeypatch, zero_dte=True)

    with mock.patch(
        "execution.options_paper_executor.OptionsPaperExecutor",
        side_effect=RuntimeError("boom"),
    ), caplog.at_level(logging.WARNING, logger="InvestYo.main"):
        _run_automated_options_lifecycle(macro_dto=None)  # must not raise

    assert any("non-critical" in rec.message for rec in caplog.records)
