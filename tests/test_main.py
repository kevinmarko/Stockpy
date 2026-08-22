import logging

import pytest
from unittest import mock

from main import _read_macro_snapshot_hint, _run_automated_delta_hedge_cycle

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
