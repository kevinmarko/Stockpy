"""
Tests for pilots/gamma_scalper.py (Gamma Scalping & Dynamic Delta Hedging Engine).
"""

import ast
import math
import numpy as np
import pytest

from data.paper_account_store import PaperPosition
from pilots.gamma_scalper import (
    simulate_gamma_scalping,
    generate_gbm_price_path,
    GammaScalpResult,
)


def test_gamma_scalper_basic_call_oscillating_path():
    """
    Verifies gamma scalping on an oscillating price path:
    - S0 = 100, K = 100, Long 1 Call contract.
    - Price oscillates up and down (100 -> 105 -> 95 -> 105 -> 95 -> 100).
    - Long Gamma produces positive scalping realized P&L and positive gamma rent.
    """
    option = {
        "strike": 100.0,
        "option_type": "call",
        "qty": 1.0,
        "sigma": 0.20,
        "t_years": 30.0 / 365.0,
        "multiplier": 100.0,
    }
    price_path = [100.0, 105.0, 95.0, 105.0, 95.0, 100.0]

    res = simulate_gamma_scalping(
        option_position=option,
        price_path=price_path,
        delta_threshold=0.05,  # 5 delta drift trigger
        fee_per_share=0.005,
    )

    assert isinstance(res, GammaScalpResult)
    assert res.ok is True
    assert res.status == "success"

    # Scalping Realized P&L must be positive from buying low and selling high on long gamma
    assert res.total_scalping_realized_pnl > 0
    assert res["Total Scalping Realized P&L"] > 0

    # Theoretical Gamma Rent must be positive (sum of 0.5 * Gamma * dS^2)
    assert res.theoretical_gamma_rent > 0
    assert res["Theoretical Gamma Rent"] > 0

    # Rebalances should have occurred
    assert res.rebalance_count >= 4
    assert len(res.trades) >= 4

    # Transaction costs must be positive
    assert res.total_transaction_costs > 0
    assert res.scalping_net_pnl == pytest.approx(res.total_scalping_realized_pnl - res.total_transaction_costs, abs=1e-3)

    # Theta decay is computed
    assert res.theta_time_decay < 0  # Long call suffers negative theta decay
    assert res.theta_decay_cost > 0

    # Net Edge is calculated: Scalping Realized P&L - Theta Decay Cost
    assert res.net_edge == pytest.approx(res.total_scalping_realized_pnl - res.theta_decay_cost, abs=1e-3)


def test_gamma_scalper_multi_leg_straddle():
    """
    Verifies dynamic hedging for a long ATM Straddle (1 Call + 1 Put).
    Initial net delta is ~0.0, so initial hedge is near 0.
    As spot moves, gamma drives positive scalping gains.
    """
    call_leg = {"strike": 100.0, "option_type": "call", "qty": 1.0, "sigma": 0.25, "t_years": 0.1}
    put_leg = {"strike": 100.0, "option_type": "put", "qty": 1.0, "sigma": 0.25, "t_years": 0.1}
    straddle = [call_leg, put_leg]

    # Oscillating spot path
    price_path = [100.0, 104.0, 96.0, 104.0, 96.0, 100.0]

    res = simulate_gamma_scalping(
        option_position=straddle,
        price_path=price_path,
        delta_threshold=0.08,
        fee_per_share=0.005,
    )

    assert res.ok is True
    # Straddle has double gamma compared to single leg
    assert res.theoretical_gamma_rent > 0
    assert res.total_scalping_realized_pnl > 0
    assert res.rebalance_count > 0


def test_gamma_scalper_flat_price_path():
    """
    When spot price is completely flat (0 volatility), no rebalances trigger,
    gamma rent is 0, scalping realized P&L is 0, and option experiences pure theta decay.
    """
    option = {"strike": 100.0, "option_type": "call", "qty": 1.0, "sigma": 0.20, "t_years": 0.1}
    flat_path = [100.0, 100.0, 100.0, 100.0, 100.0]

    res = simulate_gamma_scalping(
        option_position=option,
        price_path=flat_path,
        delta_threshold=0.15,
        fee_per_share=0.005,
    )

    assert res.ok is True
    assert res.rebalance_count == 0
    assert res.theoretical_gamma_rent == 0.0
    assert res.total_scalping_realized_pnl == 0.0
    # Option MTM P&L is slightly negative due to time decay over the intervals
    assert res.theta_time_decay < 0.0


def test_gamma_scalper_rebalance_threshold_sensitivity():
    """
    A tighter delta threshold triggers more rebalances than a wide delta threshold.
    """
    option = {"strike": 100.0, "option_type": "call", "qty": 1.0, "sigma": 0.30, "t_years": 0.1}
    path = [100.0, 102.0, 104.0, 101.0, 97.0, 103.0, 98.0, 100.0]

    res_tight = simulate_gamma_scalping(option, path, delta_threshold=0.04)
    res_wide = simulate_gamma_scalping(option, path, delta_threshold=0.30)

    assert res_tight.rebalance_count > res_wide.rebalance_count
    assert res_tight.total_transaction_costs > res_wide.total_transaction_costs


def test_gamma_scalper_transaction_fee_scaling():
    """
    Transaction costs scale linearly with fee_per_share.
    """
    option = {"strike": 100.0, "option_type": "call", "qty": 1.0, "sigma": 0.25, "t_years": 0.1}
    path = [100.0, 105.0, 95.0, 105.0, 100.0]

    res_zero_fee = simulate_gamma_scalping(option, path, delta_threshold=0.05, fee_per_share=0.0)
    res_high_fee = simulate_gamma_scalping(option, path, delta_threshold=0.05, fee_per_share=0.05)

    assert res_zero_fee.total_transaction_costs == 0.0
    assert res_high_fee.total_transaction_costs > 0.0
    assert res_high_fee.scalping_net_pnl < res_zero_fee.scalping_net_pnl


def test_gamma_scalper_degenerate_inputs_never_raise():
    """
    Degenerate inputs (empty paths, 1-point paths, None, 0 vol, 0 DTE) degrade cleanly.
    """
    option = {"strike": 100.0, "option_type": "call", "qty": 1.0}

    # Empty path
    res_empty = simulate_gamma_scalping(option, [])
    assert res_empty.ok is True
    assert res_empty.rebalance_count == 0
    assert res_empty.total_scalping_realized_pnl == 0.0

    # Single-point path
    res_single = simulate_gamma_scalping(option, [100.0])
    assert res_single.ok is True
    assert res_single.rebalance_count == 0

    # 0DTE option (t_years = 0)
    opt_0dte = {"strike": 100.0, "option_type": "call", "t_years": 0.0}
    res_0dte = simulate_gamma_scalping(opt_0dte, [100.0, 105.0, 95.0])
    assert res_0dte.ok is True

    # 0 Volatility
    opt_0vol = {"strike": 100.0, "option_type": "call", "sigma": 0.0}
    res_0vol = simulate_gamma_scalping(opt_0vol, [100.0, 105.0, 95.0])
    assert res_0vol.ok is True

    # Negative fee and zero threshold clamped safely
    res_neg = simulate_gamma_scalping(option, [100.0, 102.0], delta_threshold=0.0, fee_per_share=-1.0)
    assert res_neg.ok is True
    assert res_neg.total_transaction_costs == 0.0


def test_gamma_scalper_paper_position_and_string_symbol():
    """
    Supports PaperPosition objects and standard string option symbols.
    """
    pos = PaperPosition(symbol="AAPL 2026-09-18 $150.00 CALL", qty=2.0, avg_entry_price=5.0)
    path = [150.0, 153.0, 147.0, 152.0, 150.0]

    res = simulate_gamma_scalping(pos, path, delta_threshold=0.10)
    assert res.ok is True
    assert res.initial_spot == 150.0
    assert res.rebalance_count >= 1
    assert "Option Position Mark-to-Market P&L" in res
    assert "Theoretical Gamma Rent" in res
    assert "Theta Time Decay" in res
    assert "Net Edge" in res


def test_generate_gbm_price_path():
    """
    Verifies Geometric Brownian Motion generator.
    """
    path = generate_gbm_price_path(s0=100.0, mu=0.05, sigma=0.20, n_steps=50, seed=42)
    assert len(path) == 50
    assert path[0] == 100.0
    assert all(p > 0 for p in path)

    # Degenerate n_steps
    assert generate_gbm_price_path(s0=100.0, n_steps=1) == [100.0]


def test_gamma_scalper_strangle_and_spreads():
    """
    Verifies multi-leg positions including Strangle and Put Credit Spread.
    """
    # Strangle: 105 Call + 95 Put
    strangle = [
        {"strike": 105.0, "option_type": "call", "qty": 1.0, "sigma": 0.25, "t_years": 0.1},
        {"strike": 95.0, "option_type": "put", "qty": 1.0, "sigma": 0.25, "t_years": 0.1},
    ]
    path = [100.0, 106.0, 94.0, 106.0, 100.0]
    res_strangle = simulate_gamma_scalping(strangle, path, delta_threshold=0.10)
    assert res_strangle.ok is True
    assert res_strangle.theoretical_gamma_rent > 0
    assert res_strangle.total_scalping_realized_pnl > 0

    # Put Credit Spread: Short 100 Put, Long 95 Put
    pcs = [
        {"strike": 100.0, "option_type": "put", "qty": -1.0, "sigma": 0.25, "t_years": 0.1},
        {"strike": 95.0, "option_type": "put", "qty": 1.0, "sigma": 0.25, "t_years": 0.1},
    ]
    res_pcs = simulate_gamma_scalping(pcs, [100.0, 98.0, 102.0, 100.0], delta_threshold=0.05)
    assert res_pcs.ok is True


def test_gamma_scalper_short_gamma_behavior():
    """
    Short Call (Short Gamma) on oscillating path suffers negative scalping realized P&L
    while collecting positive theta decay.
    """
    short_call = {
        "strike": 100.0,
        "option_type": "call",
        "qty": -1.0,  # Short 1 contract
        "sigma": 0.20,
        "t_years": 0.1,
    }
    path = [100.0, 105.0, 95.0, 105.0, 95.0, 100.0]
    res = simulate_gamma_scalping(short_call, path, delta_threshold=0.05)

    assert res.ok is True
    # Short gamma loses money on scalping (buys high and sells low to maintain delta neutral)
    assert res.total_scalping_realized_pnl < 0
    assert res.theoretical_gamma_rent < 0
    # Collects positive theta decay
    assert res.theta_time_decay > 0


def test_gamma_scalper_alias_compatibility():
    """
    Verifies backward compatibility with all alias dictionary keys and attribution block.
    """
    option = {"strike": 100.0, "option_type": "call", "qty": 1.0}
    path = [100.0, 103.0, 97.0, 100.0]
    res = simulate_gamma_scalping(option, path, delta_threshold=0.05)

    assert "stock_pnl" in res
    assert "option_pnl" in res
    assert "total_pnl" in res
    assert "attribution" in res
    assert "gamma_rent" in res["attribution"]
    assert "theta_decay" in res["attribution"]
    assert "transaction_costs" in res["attribution"]
    assert "net_edge" in res["attribution"]
    assert "path_history" in res
    assert "hedge_history" in res
    assert len(res["path_history"]) == 4


def test_generate_synthetic_price_path():
    """
    Verifies generate_synthetic_price_path helper.
    """
    from pilots.gamma_scalper import generate_synthetic_price_path
    path = generate_synthetic_price_path(initial_spot=100.0, annual_vol=0.20, n_steps=30, dt_days=0.1, seed=123)
    assert len(path) == 30
    assert path[0] == 100.0
    assert all(p > 0 for p in path)


def test_gamma_scalper_ast_import_safety():
    """
    Verifies that pilots/gamma_scalper.py never imports processing_engine or technical_options_engine.
    """
    with open("pilots/gamma_scalper.py", "r") as f:
        tree = ast.parse(f.read())

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "processing_engine" not in alias.name, f"Forbidden import: {alias.name}"
                assert "technical_options_engine" not in alias.name, f"Forbidden import: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                assert "processing_engine" not in node.module, f"Forbidden from-import: {node.module}"
                assert "technical_options_engine" not in node.module, f"Forbidden from-import: {node.module}"

