"""InvestYo Quant Platform — Numba-Accelerated Event-Driven Backtest Loop.

Provides JIT-compiled bar-by-bar execution loops simulating slippage, transaction fees,
and strict causal trade execution without lookahead bias.
"""

import numpy as np
import pandas as pd
import time
from typing import Tuple, Dict, Any, Optional
from numba import njit

# ==============================================================================
# HIGH-PERFORMANCE EVENT-DRIVEN SIMULATION CORE (NUMBA JIT)
# ==============================================================================

@njit
def run_numba_backtest(prices, signals, initial_cash=10000.0, fee_rate=0.001, slippage_rate=0.0005):
    """
    Numba-compiled (JIT) sequential execution engine.
    This simulates an event-driven backtest bar-by-bar, completely eliminating 
    look-ahead bias, while executing in microseconds.
    
    Parameters:
    -----------
    prices : np.ndarray - 1D array of close prices (float64)
    signals : np.ndarray - 1D array of trading signals (int64: 1 = Buy, -1 = Sell, 0 = Hold)
    initial_cash : float - starting capital
    fee_rate : float - commission fee per transaction (e.g. 0.001 = 10 bps)
    slippage_rate : float - slippage penalty (e.g. 0.0005 = 5 bps)
    
    Returns:
    --------
    equity_curve : np.ndarray - step-by-step account valuation
    trades_log : np.ndarray - detailed trade ledger [bar_index, execution_price, size, fee, pnl]
    """
    n_bars = len(prices)
    
    # Pre-allocate arrays for performance (Numba handles fixed arrays best)
    equity_curve = np.zeros(n_bars, dtype=np.float64)
    # Trade log columns: [bar_index, execution_price, size, fees, realized_pnl]
    trades_log = np.zeros((n_bars, 5), dtype=np.float64)
    trade_count = 0
    
    # State variables
    cash = initial_cash
    position = 0.0
    entry_price = 0.0
    entry_fee = 0.0
    
    for i in range(n_bars):
        current_price = prices[i]
        signal = signals[i]
        
        # Current valuation of the entire portfolio (Equity = Cash + Position * Current Price)
        current_equity = cash + (position * current_price)
        equity_curve[i] = current_equity
        
        # 1. RISK MANAGEMENT: Trailing Stop Loss / Exit Logic (Path-Dependent)
        if position > 0.0:
            # Simple Hard Stop-Loss: Exit if price drops 5% below entry_price
            stop_price = entry_price * 0.95
            if current_price <= stop_price:
                # Execute Force Sell (Stop Triggered), bounding price to avoid gap-down execution optimism
                base_exit_price = min(stop_price, current_price)
                exec_price = base_exit_price * (1.0 - slippage_rate) # apply downward slippage
                fees = (position * exec_price) * fee_rate
                realized_pnl = (position * (exec_price - entry_price)) - (entry_fee + fees)
                
                cash += (position * exec_price) - fees
                
                # Log the trade
                trades_log[trade_count, 0] = float(i)
                trades_log[trade_count, 1] = exec_price
                trades_log[trade_count, 2] = -position
                trades_log[trade_count, 3] = fees
                trades_log[trade_count, 4] = realized_pnl
                trade_count += 1
                
                # Reset state
                position = 0.0
                entry_price = 0.0
                entry_fee = 0.0
                continue # move to next bar immediately

        # 2. TRIGGER LOGIC: Evaluation of Signals
        # Entry (Long) - Trigger buy order if signal is 1 and flat
        if signal == 1 and position == 0.0:
            # Allocate 95% of available cash to the position (leaving 5% buffer for fees/slippage)
            target_capital = cash * 0.95
            exec_price = current_price * (1.0 + slippage_rate) # buy with upward slippage
            position_size = target_capital / exec_price
            
            fees = (position_size * exec_price) * fee_rate
            cash -= (position_size * exec_price) + fees
            
            position = position_size
            entry_price = exec_price
            entry_fee = fees
            
            # Log the trade
            trades_log[trade_count, 0] = float(i)
            trades_log[trade_count, 1] = exec_price
            trades_log[trade_count, 2] = position_size
            trades_log[trade_count, 3] = fees
            trades_log[trade_count, 4] = 0.0 # Entry trade, no PnL realized yet
            trade_count += 1
            
        # Exit (Sell) - Close position if signal is -1 and holding asset
        elif signal == -1 and position > 0.0:
            exec_price = current_price * (1.0 - slippage_rate) # sell with downward slippage
            fees = (position * exec_price) * fee_rate
            realized_pnl = (position * (exec_price - entry_price)) - (entry_fee + fees)
            
            cash += (position * exec_price) - fees
            
            # Log the trade
            trades_log[trade_count, 0] = float(i)
            trades_log[trade_count, 1] = exec_price
            trades_log[trade_count, 2] = -position
            trades_log[trade_count, 3] = fees
            trades_log[trade_count, 4] = realized_pnl
            trade_count += 1
            
            # Reset state
            position = 0.0
            entry_price = 0.0
            entry_fee = 0.0

    return equity_curve, trades_log[:trade_count]


@njit
def run_numba_backtest_with_margin(
    prices: np.ndarray,
    signals: np.ndarray,
    volatility: np.ndarray,
    initial_cash: float = 10000.0,
    base_margin_rate: float = 0.20,
    base_fee_rate: float = 0.001,
    base_slippage_rate: float = 0.0005,
    stop_loss_pct: float = 0.05,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Event-driven sequential backtester with dynamic margin scaling and volatility-dependent frictions.

    Margin Scaling:
        Margin_Req_t = Base_Margin * (1.0 + 2.0 * Volatility_t)
    Slippage Scaling:
        Slippage_t = Base_Slippage * (1.0 + 3.0 * Volatility_t)
    """
    n_bars = len(prices)
    equity_curve = np.zeros(n_bars, dtype=np.float64)
    trades_log = np.zeros((n_bars * 2, 5), dtype=np.float64)
    trade_count = 0

    cash = initial_cash
    position = 0.0
    entry_price = 0.0
    entry_fee = 0.0

    for i in range(n_bars):
        current_price = prices[i]
        signal = signals[i]
        vol = volatility[i] if i < len(volatility) else 0.02

        # Volatility-dependent slippage
        slippage_rate = base_slippage_rate * (1.0 + 3.0 * vol)
        margin_rate = base_margin_rate * (1.0 + 2.0 * vol)

        current_equity = cash + (position * current_price)
        equity_curve[i] = current_equity

        # 1. RISK MANAGEMENT: Trailing Stop Loss & Dynamic Margin Check
        if position > 0.0:
            stop_price = entry_price * (1.0 - stop_loss_pct)
            # Forced liquidation if stop loss hit or position exceeds equity / margin_rate
            margin_required = (position * current_price) * margin_rate
            is_margin_call = current_equity < margin_required

            if current_price <= stop_price or is_margin_call:
                base_exit_price = min(stop_price, current_price)
                exec_price = base_exit_price * (1.0 - slippage_rate)
                fees = (position * exec_price) * base_fee_rate
                realized_pnl = (position * (exec_price - entry_price)) - (entry_fee + fees)

                cash += (position * exec_price) - fees

                trades_log[trade_count, 0] = float(i)
                trades_log[trade_count, 1] = exec_price
                trades_log[trade_count, 2] = -position
                trades_log[trade_count, 3] = fees
                trades_log[trade_count, 4] = realized_pnl
                trade_count += 1

                position = 0.0
                entry_price = 0.0
                entry_fee = 0.0
                continue

        # 2. TRIGGER LOGIC: Signals
        if signal == 1 and position == 0.0:
            target_capital = cash * 0.95
            exec_price = current_price * (1.0 + slippage_rate)
            position_size = target_capital / exec_price

            fees = (position_size * exec_price) * base_fee_rate
            cash -= (position_size * exec_price) + fees

            position = position_size
            entry_price = exec_price
            entry_fee = fees

            trades_log[trade_count, 0] = float(i)
            trades_log[trade_count, 1] = exec_price
            trades_log[trade_count, 2] = position_size
            trades_log[trade_count, 3] = fees
            trades_log[trade_count, 4] = 0.0
            trade_count += 1

        elif signal == -1 and position > 0.0:
            exec_price = current_price * (1.0 - slippage_rate)
            fees = (position * exec_price) * base_fee_rate
            realized_pnl = (position * (exec_price - entry_price)) - (entry_fee + fees)

            cash += (position * exec_price) - fees

            trades_log[trade_count, 0] = float(i)
            trades_log[trade_count, 1] = exec_price
            trades_log[trade_count, 2] = -position
            trades_log[trade_count, 3] = fees
            trades_log[trade_count, 4] = realized_pnl
            trade_count += 1

            position = 0.0
            entry_price = 0.0
            entry_fee = 0.0

    return equity_curve, trades_log[:trade_count]


def compute_numba_backtest_metrics(
    equity_curve: np.ndarray,
    trades_log: np.ndarray,
    freq: int = 252,
) -> Dict[str, Any]:
    """
    Computes full institutional risk and return metrics from Numba equity curves and trade ledgers.
    """
    import pandas as pd
    from validation.metrics import (
        sharpe_ratio,
        profit_factor,
        ulcer_index,
        ulcer_performance_index,
    )
    from validation.stress_scenarios import compute_max_drawdown

    if len(equity_curve) < 2:
        return {}

    returns = pd.Series(np.diff(equity_curve) / equity_curve[:-1])
    sr = sharpe_ratio(returns, freq=freq)
    max_dd = compute_max_drawdown(returns)
    ui = ulcer_index(returns)
    upi = ulcer_performance_index(returns, freq=freq)
    pf = profit_factor(returns)

    # Downside Sortino
    downside = returns[returns < 0]
    downside_std = downside.std()
    sortino = (
        float(returns.mean() / downside_std * np.sqrt(freq))
        if downside_std >= 1e-12 else (np.inf if returns.mean() > 0 else 0.0)
    )

    # Trade statistics
    closed_trades = trades_log[trades_log[:, 2] < 0]
    n_trades = len(closed_trades)
    winning_trades = closed_trades[closed_trades[:, 4] > 0]
    hit_rate = float(len(winning_trades) / n_trades) if n_trades > 0 else 0.0

    ann_return = float(returns.mean() * freq)
    calmar = float(ann_return / max_dd) if max_dd > 1e-6 else np.nan

    return {
        "sharpe": float(sr) if not np.isnan(sr) else 0.0,
        "sortino": sortino,
        "calmar": calmar,
        "max_drawdown": float(max_dd),
        "ulcer_index": float(ui) if not np.isnan(ui) else 0.0,
        "ulcer_performance_index": float(upi) if not np.isnan(upi) else 0.0,
        "profit_factor": float(pf) if not np.isnan(pf) else 0.0,
        "hit_rate": hit_rate,
        "total_trades": n_trades,
        "annualized_return": ann_return,
    }



# ==============================================================================
# WRAPPER FOR SYSTEM INTEGRATION AND PERFORMANCE DEMO
# ==============================================================================

if __name__ == "__main__":
    # Generate 100,000 bars of mock market data to test execution speed
    np.random.seed(42)
    n_samples = 100000
    
    # Generate mock random walk prices
    returns = np.random.normal(0.0001, 0.01, n_samples)
    prices = 100.0 * np.cumprod(1.0 + returns)
    
    # Generate random buy/sell/hold signals
    # In a real environment, these would be generated using vectorized indicators (e.g. SMA/RSI)
    signals = np.zeros(n_samples, dtype=np.int64)
    # Put random 1s (buy) and -1s (sell)
    rand_vals = np.random.random(n_samples)
    signals[rand_vals > 0.99] = 1
    signals[rand_vals < 0.01] = -1
    
    # Warm-up compile run (compiles python bytecode to machine code)
    print("Compiling Numba function...")
    _ = run_numba_backtest(prices[:10], signals[:10])
    
    # Time execution on a huge dataset
    print(f"Executing backtest over {n_samples:,} bars...")
    start_time = time.perf_counter()
    equity, trades = run_numba_backtest(prices, signals)
    end_time = time.perf_counter()
    
    duration = (end_time - start_time) * 1000.0 # in ms
    print(f"Backtest completed successfully in {duration:.4f} ms!")
    print(f"Total trades executed: {len(trades)}")
    print(f"Final Account Equity: ${equity[-1]:.2f}")
    
    if len(trades) > 0:
        print("\nLast 3 Executed Trades:")
        for t in trades[-3:]:
            print(f"  Bar: {int(t[0]):5d} | Price: ${t[1]:6.2f} | Size: {t[2]:8.3f} | Fee: ${t[3]:4.2f} | Realized PnL: ${t[4]:+.2f}")
