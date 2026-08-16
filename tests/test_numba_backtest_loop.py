"""
Unit tests for numba_backtest_loop.py (Numba JIT Sequential Execution Core).
"""
import numpy as np
import pytest
from numba_backtest_loop import run_numba_backtest


def test_numba_backtest_basic_execution():
    """Verify run_numba_backtest runs and returns expected shape and type."""
    prices = np.array([100.0, 102.0, 104.0, 101.0, 99.0], dtype=np.float64)
    signals = np.array([1, 0, 0, -1, 0], dtype=np.int64)

    equity, trades = run_numba_backtest(prices, signals, initial_cash=10000.0)

    assert isinstance(equity, np.ndarray)
    assert len(equity) == len(prices)
    assert equity[0] <= 10000.0  # Fees deducted on entry
    assert len(trades) == 2  # 1 entry, 1 exit


def test_numba_backtest_stop_loss_trigger():
    """Verify path-dependent hard stop loss triggers when price falls 5% below entry."""
    # Entry at 100, drops to 94 (< 95 stop price)
    prices = np.array([100.0, 98.0, 94.0, 90.0, 95.0], dtype=np.float64)
    signals = np.array([1, 0, 0, 0, 0], dtype=np.int64)

    equity, trades = run_numba_backtest(
        prices, signals, initial_cash=10000.0, fee_rate=0.001, slippage_rate=0.0
    )

    # Trade 1: Buy, Trade 2: Stop-loss Sell
    assert len(trades) == 2
    # Stop-loss sell executed at min(stop_price, current_price) = 94.0
    exit_trade = trades[1]
    assert exit_trade[1] == pytest.approx(94.0, abs=1e-4)
    assert exit_trade[4] < 0  # Realized negative PnL


def test_numba_backtest_slippage_and_fees():
    """Verify upward slippage on entry and downward slippage on exit."""
    prices = np.array([100.0, 110.0], dtype=np.float64)
    signals = np.array([1, -1], dtype=np.int64)
    slippage = 0.01  # 1% slippage
    fee = 0.002  # 20 bps fee

    equity, trades = run_numba_backtest(
        prices, signals, initial_cash=1000.0, fee_rate=fee, slippage_rate=slippage
    )

    assert len(trades) == 2
    buy_trade = trades[0]
    sell_trade = trades[1]

    # Buy exec price = 100 * (1 + 0.01) = 101.0
    assert buy_trade[1] == pytest.approx(101.0, abs=1e-4)
    # Sell exec price = 110 * (1 - 0.01) = 108.9
    assert sell_trade[1] == pytest.approx(108.9, abs=1e-4)


def test_numba_backtest_no_signals():
    """Verify flat equity curve and 0 trades when no signals fire."""
    prices = np.array([100.0, 105.0, 95.0, 110.0], dtype=np.float64)
    signals = np.zeros(len(prices), dtype=np.int64)

    equity, trades = run_numba_backtest(prices, signals, initial_cash=10000.0)

    assert len(trades) == 0
    assert np.all(equity == 10000.0)


def test_numba_backtest_gap_down_stop_loss():
    """Verify stop loss executes at current price if bar gaps down below stop price."""
    # Entry at 100 (stop price = 95), bar 2 gaps down directly to 88.0
    prices = np.array([100.0, 88.0, 92.0], dtype=np.float64)
    signals = np.array([1, 0, 0], dtype=np.int64)

    equity, trades = run_numba_backtest(
        prices, signals, initial_cash=10000.0, fee_rate=0.0, slippage_rate=0.0
    )

    assert len(trades) == 2
    exit_trade = trades[1]
    # Execution price reflects gap price 88.0 rather than optimistic 95.0
    assert exit_trade[1] == pytest.approx(88.0, abs=1e-4)
    assert exit_trade[4] < 0


def test_numba_backtest_with_margin_and_vol_frictions():
    """Verify dynamic margin model and volatility-dependent slippage."""
    from numba_backtest_loop import run_numba_backtest_with_margin, compute_numba_backtest_metrics

    prices = np.array([100.0, 102.0, 104.0, 93.0, 95.0], dtype=np.float64)
    signals = np.array([1, 0, 0, 0, 0], dtype=np.int64)
    # High volatility spike on bar 3 (vol = 0.08)
    volatility = np.array([0.02, 0.02, 0.03, 0.08, 0.04], dtype=np.float64)

    equity, trades = run_numba_backtest_with_margin(
        prices, signals, volatility, initial_cash=10000.0, stop_loss_pct=0.05
    )

    assert len(equity) == len(prices)
    assert len(trades) == 2  # Entry + Stop/Margin Exit

    metrics = compute_numba_backtest_metrics(equity, trades)
    assert "sharpe" in metrics
    assert "sortino" in metrics
    assert "max_drawdown" in metrics
    assert "ulcer_index" in metrics
    assert "ulcer_performance_index" in metrics
    assert "profit_factor" in metrics
    assert metrics["total_trades"] == 1


