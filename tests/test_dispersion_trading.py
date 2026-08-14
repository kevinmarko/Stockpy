"""Tests for pilots/dispersion_trading.py."""

import ast
from pathlib import Path
import pytest
import numpy as np
import pandas as pd

from data.paper_account_store import PaperAccountStore
from execution.options_paper_executor import OptionsPaperExecutor
from pilots.dispersion_trading import (
    DEFAULT_DISPERSION_CONSTITUENTS,
    DEFAULT_DISPERSION_INDEX,
    DEFAULT_WEIGHTS,
    DispersionBasket,
    build_dispersion_basket,
    calculate_default_expiration,
    calculate_option_price,
    calculate_straddle_vega,
    compute_implied_correlation,
    compute_realized_correlation_matrix,
    evaluate_dispersion_opportunity,
    execute_dispersion_trade,
)


# ---------------------------------------------------------------------------
# 1. Math & Pricing Helpers
# ---------------------------------------------------------------------------

def test_calculate_default_expiration():
    exp = calculate_default_expiration(30)
    assert len(exp) == 10
    assert exp.count("-") == 2
    # Verify year-month-day structure
    parts = exp.split("-")
    assert len(parts[0]) == 4
    assert len(parts[1]) == 2
    assert len(parts[2]) == 2


def test_calculate_straddle_vega():
    # SPY 500 spot, 500 strike, 30 DTE, 18% IV
    vega = calculate_straddle_vega(spot=500.0, strike=500.0, iv=0.18, dte=30)
    assert vega > 0.0
    # Higher spot should yield higher dollar vega
    vega_high = calculate_straddle_vega(spot=1000.0, strike=1000.0, iv=0.18, dte=30)
    assert vega_high > vega

    # Degenerate / zero guards
    assert calculate_straddle_vega(spot=0.0, strike=500.0, iv=0.18, dte=30) == 0.0
    assert calculate_straddle_vega(spot=500.0, strike=0.0, iv=0.18, dte=30) == 0.0
    assert calculate_straddle_vega(spot=500.0, strike=500.0, iv=0.0, dte=30) == 0.0
    assert calculate_straddle_vega(spot=500.0, strike=500.0, iv=0.18, dte=0) == 0.0


def test_calculate_option_price():
    # ATM Call vs Put prices with positive interest rate
    call_price = calculate_option_price(spot=100.0, strike=100.0, dte=30, iv=0.20, opt_type="call")
    put_price = calculate_option_price(spot=100.0, strike=100.0, dte=30, iv=0.20, opt_type="put")
    assert call_price > 0.0
    assert put_price > 0.0

    # 0DTE intrinsic test
    call_0dte_itm = calculate_option_price(spot=105.0, strike=100.0, dte=0, iv=0.20, opt_type="call")
    assert pytest.approx(call_0dte_itm, 0.01) == 500.0  # (105 - 100) * 100

    put_0dte_itm = calculate_option_price(spot=95.0, strike=100.0, dte=0, iv=0.20, opt_type="put")
    assert pytest.approx(put_0dte_itm, 0.01) == 500.0  # (100 - 95) * 100


# ---------------------------------------------------------------------------
# 2. Implied & Realized Correlation Math
# ---------------------------------------------------------------------------

def test_compute_implied_correlation():
    # If index IV equals constituent IVs exactly and equal weights, implied correlation is 1.0
    weights = {"AAPL": 0.5, "MSFT": 0.5}
    const_ivs = {"AAPL": 0.20, "MSFT": 0.20}
    rho = compute_implied_correlation(index_iv=0.20, constituent_ivs=const_ivs, weights=weights)
    assert pytest.approx(rho, 0.01) == 1.0

    # If index IV is significantly lower than individual IVs, implied correlation is lower
    rho_low = compute_implied_correlation(index_iv=0.14, constituent_ivs=const_ivs, weights=weights)
    assert 0.0 <= rho_low < 1.0

    # Degenerate guards -- CONSTRAINT #4: a non-computable correlation must come back None
    # (never a fabricated "typical" 0.50 guess) so a caller can tell "no real data" apart from
    # "computed a genuine 0.50 correlation".
    assert compute_implied_correlation(index_iv=0.0, constituent_ivs=const_ivs, weights=weights) is None
    assert compute_implied_correlation(index_iv=0.20, constituent_ivs={}, weights=weights) is None


def test_compute_realized_correlation_matrix():
    np.random.seed(42)
    dates = pd.date_range("2026-01-01", periods=100)
    # Generate correlated returns
    r1 = np.random.normal(0, 0.01, 100)
    r2 = r1 * 0.8 + np.random.normal(0, 0.005, 100)
    df = pd.DataFrame({"AAPL": r1, "MSFT": r2}, index=dates)

    matrix, avg_corr = compute_realized_correlation_matrix(df)
    assert matrix.shape == (2, 2)
    assert avg_corr > 0.50

    # Weighted realized correlation
    weights = {"AAPL": 0.6, "MSFT": 0.4}
    _, weighted_avg = compute_realized_correlation_matrix(df, weights=weights)
    assert -1.0 <= weighted_avg <= 1.0


def test_evaluate_dispersion_opportunity():
    # When implied correlation >> realized correlation => Long Dispersion
    res_long = evaluate_dispersion_opportunity(
        index_symbol="SPY",
        index_iv=0.25,
        constituent_ivs={"AAPL": 0.26, "MSFT": 0.26},
        weights={"AAPL": 0.5, "MSFT": 0.5},
        realized_correlation=0.30,
        threshold=0.15,
    )
    assert res_long["regime"] == "Long Dispersion"
    assert res_long["is_actionable"] is True
    assert res_long["direction"] == "long_dispersion"

    # When implied correlation << realized correlation => Short Dispersion
    res_short = evaluate_dispersion_opportunity(
        index_symbol="SPY",
        index_iv=0.12,
        constituent_ivs={"AAPL": 0.30, "MSFT": 0.30},
        weights={"AAPL": 0.5, "MSFT": 0.5},
        realized_correlation=0.80,
        threshold=0.15,
    )
    assert res_short["regime"] == "Short Dispersion"
    assert res_short["is_actionable"] is True
    assert res_short["direction"] == "short_dispersion"

    # Fair value spread => Neutral
    res_neutral = evaluate_dispersion_opportunity(
        index_symbol="SPY",
        index_iv=0.22,
        constituent_ivs={"AAPL": 0.25, "MSFT": 0.25},
        weights={"AAPL": 0.5, "MSFT": 0.5},
        realized_correlation=0.50,
        threshold=0.15,
    )
    assert res_neutral["regime"] == "Neutral"
    assert res_neutral["is_actionable"] is False


# ---------------------------------------------------------------------------
# 3. Dispersion Basket Construction & Vega Neutrality
# ---------------------------------------------------------------------------

def test_build_dispersion_basket_vega_neutrality():
    index_symbol = "SPY"
    constituents = ["AAPL", "MSFT", "NVDA"]
    spot_map = {"SPY": 500.0, "AAPL": 220.0, "MSFT": 420.0, "NVDA": 120.0}
    iv_map = {"SPY": 0.18, "AAPL": 0.25, "MSFT": 0.22, "NVDA": 0.40}
    weights = {"AAPL": 0.40, "MSFT": 0.35, "NVDA": 0.25}

    basket = build_dispersion_basket(
        index_symbol=index_symbol,
        constituent_symbols=constituents,
        spot_map=spot_map,
        iv_map=iv_map,
        weights=weights,
        index_contracts=2,
        target_dte=30,
        is_long_dispersion=True,
    )

    assert isinstance(basket, DispersionBasket)
    assert basket.index_symbol == "SPY"
    assert basket.constituent_symbols == constituents
    assert basket.index_contracts == 2
    assert basket.index_vega > 0
    assert basket.basket_vega > 0

    # Vega neutrality balance: ratio should be close to 1.0 (within integer rounding band)
    assert 0.70 <= basket.vega_neutrality_ratio <= 1.30
    assert abs(basket.vega_imbalance_pct) < 35.0

    # Verify Index Legs (Long Dispersion => Short Index Straddle: Sell Call & Sell Put)
    assert len(basket.index_leg_requests) == 2
    assert basket.index_leg_requests[0]["side"] == "sell"
    assert basket.index_leg_requests[0]["type"] == "call"
    assert basket.index_leg_requests[1]["side"] == "sell"
    assert basket.index_leg_requests[1]["type"] == "put"
    assert basket.index_leg_requests[0]["qty"] == 2.0

    # Verify Constituent Legs (Long Dispersion => Long Constituent Straddles: Buy Call & Buy Put)
    assert len(basket.constituent_leg_requests) == 3
    for sym in constituents:
        legs = basket.constituent_leg_requests[sym]
        assert len(legs) == 2
        assert legs[0]["side"] == "buy"
        assert legs[0]["type"] == "call"
        assert legs[1]["side"] == "buy"
        assert legs[1]["type"] == "put"
        assert legs[0]["qty"] >= 1.0

    # Check to_dict serialization
    d = basket.to_dict()
    assert d["index_symbol"] == "SPY"
    assert "summary" in d
    assert d["summary"]["strategy"] == "Dispersion Arbitrage"


def test_build_dispersion_basket_short_dispersion():
    basket = build_dispersion_basket(
        index_symbol="QQQ",
        constituent_symbols=["AAPL", "MSFT"],
        spot_map={"QQQ": 450.0, "AAPL": 220.0, "MSFT": 420.0},
        iv_map={"QQQ": 0.20, "AAPL": 0.26, "MSFT": 0.24},
        weights={"AAPL": 0.5, "MSFT": 0.5},
        index_contracts=1,
        is_long_dispersion=False,
    )

    # Short Dispersion => Long Index Straddle (Buy), Short Constituent Straddles (Sell)
    assert basket.is_long_dispersion is False
    assert basket.index_leg_requests[0]["side"] == "buy"
    assert basket.index_leg_requests[1]["side"] == "buy"
    assert basket.constituent_leg_requests["AAPL"][0]["side"] == "sell"
    assert basket.constituent_leg_requests["AAPL"][1]["side"] == "sell"


# ---------------------------------------------------------------------------
# 4. Paper Account Execution
# ---------------------------------------------------------------------------

def test_execute_dispersion_trade_dry_run():
    store = PaperAccountStore(db_url="sqlite:///:memory:")
    basket = build_dispersion_basket(
        index_symbol="SPY",
        constituent_symbols=["AAPL", "MSFT"],
        spot_map={"SPY": 500.0, "AAPL": 220.0, "MSFT": 420.0},
        iv_map={"SPY": 0.18, "AAPL": 0.25, "MSFT": 0.22},
        weights={"AAPL": 0.5, "MSFT": 0.5},
        index_contracts=1,
    )

    res = execute_dispersion_trade(basket, store=store, dry_run=True)
    assert res["ok"] is True
    assert res["dry_run"] is True
    assert "Dry run" in res["message"]

    # Store remains untouched in dry run
    assert len(store.get_open_positions()) == 0


def test_execute_dispersion_trade_atomic_execution():
    store = PaperAccountStore(db_url="sqlite:///:memory:")
    initial_cash = store.get_account().cash
    assert initial_cash > 0

    basket = build_dispersion_basket(
        index_symbol="SPY",
        constituent_symbols=["AAPL", "MSFT"],
        spot_map={"SPY": 500.0, "AAPL": 220.0, "MSFT": 420.0},
        iv_map={"SPY": 0.18, "AAPL": 0.25, "MSFT": 0.22},
        weights={"AAPL": 0.5, "MSFT": 0.5},
        index_contracts=1,
        is_long_dispersion=True,
    )

    res = execute_dispersion_trade(basket, store=store, dry_run=False)
    assert res["ok"] is True
    assert res["strategy"] == "Dispersion Arbitrage"
    assert res["index_symbol"] == "SPY"
    assert "SPY" in res["index_order_id"]
    assert len(res["constituent_order_ids"]) == 2
    assert res["total_legs_filled"] == 6  # 2 index legs + 2*2 constituent legs

    positions = store.get_open_positions()
    assert len(positions) == 6

    # Verify short index straddle positions (qty < 0)
    spy_positions = [p for p in positions if "SPY" in p.symbol]
    assert len(spy_positions) == 2
    for p in spy_positions:
        assert p.qty == -1.0

    # Verify long constituent straddle positions (qty > 0)
    aapl_positions = [p for p in positions if "AAPL" in p.symbol]
    assert len(aapl_positions) == 2
    for p in aapl_positions:
        assert p.qty > 0.0

    msft_positions = [p for p in positions if "MSFT" in p.symbol]
    assert len(msft_positions) == 2
    for p in msft_positions:
        assert p.qty > 0.0


def test_execute_dispersion_trade_executor_delegation():
    store = PaperAccountStore(db_url="sqlite:///:memory:")
    executor = OptionsPaperExecutor(store=store)

    basket = build_dispersion_basket(
        index_symbol="SPY",
        constituent_symbols=["AAPL", "NVDA"],
        spot_map={"SPY": 500.0, "AAPL": 220.0, "NVDA": 120.0},
        iv_map={"SPY": 0.18, "AAPL": 0.25, "NVDA": 0.35},
        weights={"AAPL": 0.6, "NVDA": 0.4},
        index_contracts=1,
    )

    res = executor.execute_dispersion_trade(basket, dry_run=False)
    assert res["ok"] is True
    assert len(store.get_open_positions()) == 6


# ---------------------------------------------------------------------------
# 5. AST Import Safety Test
# ---------------------------------------------------------------------------

def test_dispersion_trading_ast_import_safety():
    """Verifies that pilots/dispersion_trading.py never imports heavy forbidden engines."""
    file_path = Path(__file__).resolve().parent.parent / "pilots" / "dispersion_trading.py"
    with open(file_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename="dispersion_trading.py")

    forbidden_modules = {
        "processing_engine",
        "technical_options_engine",
        "forecasting_engine",
        "strategy_engine",
        "macro_engine",
        "main",
        "main_orchestrator",
        "desktop",
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for forbidden in forbidden_modules:
                    assert forbidden not in alias.name, f"Forbidden import found: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            mod_name = node.module or ""
            for forbidden in forbidden_modules:
                assert forbidden not in mod_name, f"Forbidden from-import found: {mod_name}"
