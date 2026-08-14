"""Tests for execution/options_paper_executor.py."""

from unittest.mock import MagicMock, patch
import pytest

from data.paper_account_store import PaperAccountStore
from execution.options_paper_executor import OptionsPaperExecutor, _calculate_default_expiration


def test_calculate_default_expiration():
    exp = _calculate_default_expiration(30)
    assert len(exp) == 10
    assert exp.count("-") == 2


def test_get_actionable_directives_filters_cash_and_wait():
    store = PaperAccountStore(db_url="sqlite:///:memory:")
    executor = OptionsPaperExecutor(store=store)

    mock_directive_cash = {
        "Strategy": "Cash",
        "Action": "Wait",
        "Integrity_OK": True,
        "IVR_Proxy": 30.0,
    }
    mock_directive_pcs = {
        "Strategy": "Put Credit Spread",
        "Action": "Open",
        "Integrity_OK": True,
        "IVR_Proxy": 65.0,
        "True_IVR": 65.0,
        "Net_Premium": 1.50,
        "Trend_Bias": "Bullish",
        "Legs": [
            {"Strike": 150.0, "Side": "Short", "Delta": -0.30},
            {"Strike": 145.0, "Side": "Long", "Delta": -0.15},
        ],
    }

    with patch("execution.options_paper_executor._directive_for_symbol") as mock_fetch:
        mock_fetch.side_effect = lambda sym, **kwargs: mock_directive_pcs if sym == "AAPL" else mock_directive_cash
        directives = executor.get_actionable_directives(symbols=["AAPL", "SPY"])

    assert len(directives) == 1
    assert directives[0]["symbol"] == "AAPL"
    assert directives[0]["strategy"] == "Put Credit Spread"
    assert directives[0]["net_premium"] == 1.50


def test_execute_strategy_directives_dry_run():
    store = PaperAccountStore(db_url="sqlite:///:memory:")
    executor = OptionsPaperExecutor(store=store)

    directives = [
        {
            "symbol": "AAPL",
            "strategy": "Put Credit Spread",
            "action": "Open",
            "net_premium": 1.50,
            "target_dte": 30,
            "legs": [
                {"strike": 150.0, "side": "sell", "type": "put", "ratio_qty": 1.0, "price": 2.20},
                {"strike": 145.0, "side": "buy", "type": "put", "ratio_qty": 1.0, "price": 0.70},
            ]
        }
    ]

    result = executor.execute_strategy_directives(directives=directives, dry_run=True)
    assert result["executed_count"] == 1
    assert result["executed"][0]["dry_run"] is True

    # In dry run, store is untouched
    assert len(store.get_open_positions()) == 0


def test_execute_strategy_directives_live_fill():
    store = PaperAccountStore(db_url="sqlite:///:memory:")
    executor = OptionsPaperExecutor(store=store)

    directives = [
        {
            "symbol": "AAPL",
            "strategy": "Put Credit Spread",
            "action": "Open",
            "net_premium": 1.50,
            "target_dte": 30,
            "legs": [
                {"strike": 150.0, "side": "sell", "type": "put", "ratio_qty": 1.0, "price": 2.20},
                {"strike": 145.0, "side": "buy", "type": "put", "ratio_qty": 1.0, "price": 0.70},
            ]
        }
    ]

    result = executor.execute_strategy_directives(directives=directives, dry_run=False, max_notional_per_order=2500.0)
    assert result["executed_count"] == 1
    assert result["skipped_count"] == 0
    assert result["failed_count"] == 0

    positions = store.get_open_positions()
    assert len(positions) == 2
    short_leg = next(p for p in positions if "$150.00" in p.symbol)
    long_leg = next(p for p in positions if "$145.00" in p.symbol)
    assert short_leg.qty < 0
    assert long_leg.qty > 0


def test_execute_strategy_directives_deduplication():
    store = PaperAccountStore(db_url="sqlite:///:memory:")
    executor = OptionsPaperExecutor(store=store)

    directives = [
        {
            "symbol": "AAPL",
            "strategy": "Put Credit Spread",
            "action": "Open",
            "net_premium": 1.50,
            "target_dte": 30,
            "legs": [
                {"strike": 150.0, "side": "sell", "type": "put", "ratio_qty": 1.0, "price": 2.20},
                {"strike": 145.0, "side": "buy", "type": "put", "ratio_qty": 1.0, "price": 0.70},
            ]
        }
    ]

    # First execution succeeds
    res1 = executor.execute_strategy_directives(directives=directives, dry_run=False)
    assert res1["executed_count"] == 1

    # Second execution skips duplicate symbol
    res2 = executor.execute_strategy_directives(directives=directives, dry_run=False)
    assert res2["executed_count"] == 0
    assert res2["skipped_count"] == 1
    assert "already exists" in res2["skipped"][0]["reason"]
