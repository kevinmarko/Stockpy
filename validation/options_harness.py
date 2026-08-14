"""
InvestYo Quant Platform - Options Strategy Validation Harness
=============================================================
Simulates and validates multi-leg options strategies (e.g. Put Credit Spreads,
Call Credit Spreads, Iron Condors, Bull Call Spreads, Bear Put Spreads, Straddles).
Computes risk-adjusted metrics, trade statistics, Black-Scholes daily mark-to-market
payoffs, downsampled equity curves, and tail-scenario stress tests.
"""

from __future__ import annotations

import argparse
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

from execution.cost_model import TieredCostModel
from validation.metrics import sharpe_ratio, deflated_sharpe_ratio, probability_of_backtest_overfitting
from validation.stress_scenarios import (
    StressResult,
    run_stress_tests,
    passes_stress_gate,
    format_stress_summary,
    compute_max_drawdown,
)
from validation.thresholds import (
    PBO_MAX,
    DSR_MIN,
    NET_SHARPE_MIN,
    MAX_DRAWDOWN_MAX,
    STRESS_MAX_DRAWDOWN,
)

logger = logging.getLogger("OptionsValidationHarness")


@dataclass
class OptionLegSpec:
    """Specification for a single option leg in a multi-leg strategy."""
    side: str  # "buy" or "sell"
    option_type: str  # "call" or "put"
    strike_offset_pct: float  # e.g., -0.05 for 5% OTM Put, +0.05 for 5% OTM Call
    dte: int = 30  # Days to expiration at entry
    ratio: int = 1  # Number of contracts per spread unit


@dataclass
class OptionsStrategySpec:
    """Configuration for an options strategy."""
    name: str
    legs: List[OptionLegSpec]
    target_profit_pct: float = 0.50  # Close at 50% max profit (for credit strategies)
    stop_loss_multiple: float = 2.0  # Close at 2x credit loss
    min_iv: float = 0.15  # Minimum IV to enter trade
    rebalance_interval_days: int = 7  # Check for entry opportunities every N days


@dataclass
class OptionsTradeRecord:
    """Record of a simulated options strategy trade."""
    entry_date: str
    exit_date: str
    strategy: str
    underlying_entry_price: float
    underlying_exit_price: float
    entry_net_premium: float  # Positive for credit, negative for debit
    exit_net_cost: float
    pnl_dollar: float
    pnl_pct: float
    exit_reason: str  # "profit_target", "stop_loss", "expiration", "rebalance"
    holding_days: int
    contracts: int = 1


@dataclass
class OptionsBacktestResult:
    """Comprehensive result of an options strategy validation backtest."""
    strategy_name: str
    ticker: str
    start_date: str
    end_date: str
    initial_capital: float
    final_capital: float
    total_return_pct: float
    annualized_return_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    pbo: float
    dsr: float
    daily_returns: pd.Series = field(repr=False, default_factory=lambda: pd.Series(dtype=float))
    equity_curve: List[Dict[str, Any]] = field(default_factory=list)
    trades: List[OptionsTradeRecord] = field(default_factory=list)
    stress_results: Dict[str, StressResult] = field(default_factory=dict)
    passes_stress: bool = False
    deployable: bool = False


# ---------------------------------------------------------------------------
# Pre-defined Options Strategies
# ---------------------------------------------------------------------------
STANDARD_OPTIONS_STRATEGIES: Dict[str, OptionsStrategySpec] = {
    "Put Credit Spread": OptionsStrategySpec(
        name="Put Credit Spread",
        legs=[
            OptionLegSpec(side="sell", option_type="put", strike_offset_pct=-0.03, dte=35),
            OptionLegSpec(side="buy", option_type="put", strike_offset_pct=-0.07, dte=35),
        ],
        target_profit_pct=0.50,
        stop_loss_multiple=2.0,
    ),
    "Call Credit Spread": OptionsStrategySpec(
        name="Call Credit Spread",
        legs=[
            OptionLegSpec(side="sell", option_type="call", strike_offset_pct=0.03, dte=35),
            OptionLegSpec(side="buy", option_type="call", strike_offset_pct=0.07, dte=35),
        ],
        target_profit_pct=0.50,
        stop_loss_multiple=2.0,
    ),
    "Iron Condor": OptionsStrategySpec(
        name="Iron Condor",
        legs=[
            OptionLegSpec(side="sell", option_type="put", strike_offset_pct=-0.04, dte=35),
            OptionLegSpec(side="buy", option_type="put", strike_offset_pct=-0.08, dte=35),
            OptionLegSpec(side="sell", option_type="call", strike_offset_pct=0.04, dte=35),
            OptionLegSpec(side="buy", option_type="call", strike_offset_pct=0.08, dte=35),
        ],
        target_profit_pct=0.50,
        stop_loss_multiple=2.0,
    ),
    "Bull Call Spread": OptionsStrategySpec(
        name="Bull Call Spread",
        legs=[
            OptionLegSpec(side="buy", option_type="call", strike_offset_pct=0.0, dte=35),
            OptionLegSpec(side="sell", option_type="call", strike_offset_pct=0.05, dte=35),
        ],
        target_profit_pct=0.75,
        stop_loss_multiple=1.0,
    ),
    "Bear Put Spread": OptionsStrategySpec(
        name="Bear Put Spread",
        legs=[
            OptionLegSpec(side="buy", option_type="put", strike_offset_pct=0.0, dte=35),
            OptionLegSpec(side="sell", option_type="put", strike_offset_pct=-0.05, dte=35),
        ],
        target_profit_pct=0.75,
        stop_loss_multiple=1.0,
    ),
    "Long Straddle": OptionsStrategySpec(
        name="Long Straddle",
        legs=[
            OptionLegSpec(side="buy", option_type="call", strike_offset_pct=0.0, dte=35),
            OptionLegSpec(side="buy", option_type="put", strike_offset_pct=0.0, dte=35),
        ],
        target_profit_pct=0.50,
        stop_loss_multiple=0.50,
    ),
}


def _black_scholes_price(
    spot: float, strike: float, t_years: float, sigma: float, r: float = 0.045, option_type: str = "call"
) -> float:
    """Standard Black-Scholes analytical option pricing formula."""
    if spot <= 0 or strike <= 0:
        return 0.0
    if t_years <= 1e-5:
        # At expiration intrinsic value
        if option_type.lower() == "call":
            return max(0.0, spot - strike)
        else:
            return max(0.0, strike - spot)

    sigma = max(1e-4, sigma)
    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma ** 2) * t_years) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t

    def norm_cdf(x: float) -> float:
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    if option_type.lower() == "call":
        price = spot * norm_cdf(d1) - strike * math.exp(-r * t_years) * norm_cdf(d2)
    else:
        price = strike * math.exp(-r * t_years) * norm_cdf(-d2) - spot * norm_cdf(-d1)

    return max(0.0, price)


class OptionsValidationHarness:
    """
    Validation harness specifically designed for options trading strategies.
    Simulates trades over historical price bars, evaluates P&L, downsamples
    equity curves, computes risk metrics, and validates deployability.
    """

    def __init__(self, cost_model: Optional[TieredCostModel] = None):
        self.cost_model = cost_model or TieredCostModel()

    def run_backtest(
        self,
        strategy: str | OptionsStrategySpec,
        ticker: str = "SPY",
        start_date: str = "2020-01-01",
        end_date: str = "2024-01-01",
        initial_capital: float = 100000.0,
        price_df: Optional[pd.DataFrame] = None,
        allocation_pct: float = 0.05,
    ) -> OptionsBacktestResult:
        """
        Runs an options strategy backtest against historical price series.
        """
        if isinstance(strategy, str):
            if strategy not in STANDARD_OPTIONS_STRATEGIES:
                raise ValueError(
                    f"Unknown options strategy '{strategy}'. Registered: {list(STANDARD_OPTIONS_STRATEGIES.keys())}"
                )
            spec = STANDARD_OPTIONS_STRATEGIES[strategy]
        else:
            spec = strategy

        # Fetch data if not provided
        if price_df is None or price_df.empty:
            logger.info("Downloading historical price data for %s (%s to %s)...", ticker, start_date, end_date)
            try:
                raw_df = yf.download(ticker, start=start_date, end=end_date, progress=False)
            except Exception as exc:
                logger.warning("yf.download failed: %s", exc)
                raw_df = pd.DataFrame()

            if raw_df.empty:
                raise RuntimeError(f"Failed to fetch market data for {ticker}")
            
            # Standardize column index
            if isinstance(raw_df.columns, pd.MultiIndex):
                raw_df.columns = raw_df.columns.get_level_values(0)
            df = raw_df.copy()
        else:
            df = price_df.copy()

        df.index = pd.to_datetime(df.index)
        df = df.sort_index()

        # Compute 20-day rolling annualized volatility
        close_series = df["Close"].astype(float)
        log_ret = np.log(close_series / close_series.shift(1))
        rolling_hv = log_ret.rolling(window=20, min_periods=5).std() * math.sqrt(252)
        rolling_hv = rolling_hv.fillna(0.20)
        # Volatility Risk Premium multiplier (IV typically trades ~1.15x HV)
        iv_series = rolling_hv * 1.15

        capital = initial_capital
        equity_series: Dict[pd.Timestamp, float] = {}
        trades: List[OptionsTradeRecord] = []

        # Active trade state
        active_trade: Optional[Dict[str, Any]] = None
        last_entry_date = None

        dates = df.index
        for i, current_dt in enumerate(dates):
            spot = float(close_series.iloc[i])
            iv = float(iv_series.iloc[i])
            date_str = current_dt.strftime("%Y-%m-%d")

            # Update active trade if exists
            if active_trade is not None:
                days_held = (current_dt - active_trade["entry_dt"]).days
                dte_left = max(0, active_trade["initial_dte"] - days_held)
                t_years_left = dte_left / 365.0

                # Mark to market current value of all legs
                current_legs_value = 0.0
                for leg in active_trade["legs"]:
                    p = _black_scholes_price(
                        spot=spot,
                        strike=leg["strike"],
                        t_years=t_years_left,
                        sigma=iv,
                        option_type=leg["option_type"],
                    )
                    # For long leg: value is +price; for short leg: value is -price
                    multiplier = 1.0 if leg["side"] == "buy" else -1.0
                    current_legs_value += multiplier * p * 100.0 * leg["ratio"]

                # P&L calculation: (current_legs_value - entry_legs_value) * contracts
                contracts = active_trade["contracts"]
                gross_pnl = (current_legs_value - active_trade["entry_legs_value"]) * contracts
                max_profit_cap = active_trade["initial_max_profit"] * contracts

                # Check exit conditions
                is_exit = False
                exit_reason = "rebalance"

                if dte_left <= 0:
                    is_exit = True
                    exit_reason = "expiration"
                elif max_profit_cap > 0 and gross_pnl >= spec.target_profit_pct * max_profit_cap:
                    is_exit = True
                    exit_reason = "profit_target"
                elif max_profit_cap > 0 and gross_pnl <= -spec.stop_loss_multiple * max_profit_cap:
                    is_exit = True
                    exit_reason = "stop_loss"
                elif days_held >= 45:  # Hard safety exit after 45 days
                    is_exit = True
                    exit_reason = "max_hold"

                if is_exit:
                    # Deduct commissions on close
                    exit_commission = 0.65 * len(spec.legs) * contracts
                    net_pnl = gross_pnl - exit_commission
                    capital += net_pnl

                    trades.append(
                        OptionsTradeRecord(
                            entry_date=active_trade["entry_date_str"],
                            exit_date=date_str,
                            strategy=spec.name,
                            underlying_entry_price=active_trade["entry_spot"],
                            underlying_exit_price=spot,
                            entry_net_premium=active_trade["entry_legs_value"] * contracts,
                            exit_net_cost=current_legs_value * contracts,
                            pnl_dollar=net_pnl,
                            pnl_pct=net_pnl / max(1.0, active_trade["margin_or_debit"] * contracts),
                            exit_reason=exit_reason,
                            holding_days=days_held,
                            contracts=contracts,
                        )
                    )
                    active_trade = None

            # Open new trade if no active trade and rebalance cadence allows
            if active_trade is None and (last_entry_date is None or (current_dt - last_entry_date).days >= spec.rebalance_interval_days):
                if iv >= spec.min_iv:
                    # Construct legs
                    legs_info = []
                    net_entry_value = 0.0
                    dte = spec.legs[0].dte
                    t_years = dte / 365.0

                    for leg_spec in spec.legs:
                        strike = round(spot * (1.0 + leg_spec.strike_offset_pct), 2)
                        p = _black_scholes_price(
                            spot=spot,
                            strike=strike,
                            t_years=t_years,
                            sigma=iv,
                            option_type=leg_spec.option_type,
                        )
                        multiplier = 1.0 if leg_spec.side == "buy" else -1.0
                        net_entry_value += multiplier * p * 100.0 * leg_spec.ratio
                        legs_info.append({
                            "side": leg_spec.side,
                            "option_type": leg_spec.option_type,
                            "strike": strike,
                            "ratio": leg_spec.ratio,
                            "entry_price": p,
                        })

                    # Sizing: determine contracts based on capital allocation
                    target_budget = capital * allocation_pct
                    # Margin estimate for spread (width of spread * 100) or debit cost
                    strikes = [l["strike"] for l in legs_info]
                    spread_width = max(strikes) - min(strikes) if len(strikes) > 1 else spot * 0.05
                    margin_per_unit = max(spread_width * 100.0, abs(net_entry_value), 100.0)
                    contracts = max(1, int(target_budget / margin_per_unit))

                    # Deduct entry commission
                    entry_commission = 0.65 * len(spec.legs) * contracts
                    capital -= entry_commission

                    active_trade = {
                        "entry_dt": current_dt,
                        "entry_date_str": date_str,
                        "entry_spot": spot,
                        "initial_dte": dte,
                        "legs": legs_info,
                        "entry_legs_value": net_entry_value,
                        "initial_max_profit": max(0.0, -net_entry_value) if net_entry_value < 0 else 500.0,
                        "margin_or_debit": margin_per_unit,
                        "contracts": contracts,
                    }
                    last_entry_date = current_dt

            equity_series[current_dt] = max(0.0, capital)

        # Build equity curve & returns
        equity_df = pd.Series(equity_series).sort_index()
        daily_ret = equity_df.pct_change().fillna(0.0)

        # Compute summary metrics
        total_ret = (capital - initial_capital) / initial_capital
        n_days = len(equity_df)
        ann_ret = (1.0 + total_ret) ** (252.0 / max(1, n_days)) - 1.0 if total_ret > -1.0 else -1.0

        sr = sharpe_ratio(daily_ret)
        # Sortino
        downside_ret = daily_ret[daily_ret < 0]
        downside_std = downside_ret.std() * math.sqrt(252) if len(downside_ret) > 1 else 1e-4
        sortino = (daily_ret.mean() * 252 - 0.045) / max(1e-4, downside_std)

        # Max drawdown
        cum_max = equity_df.cummax()
        drawdowns = (equity_df - cum_max) / cum_max
        max_dd = abs(float(drawdowns.min())) if len(drawdowns) > 0 else 0.0

        # Trade metrics
        total_trades = len(trades)
        winning_trades = len([t for t in trades if t.pnl_dollar > 0])
        losing_trades = len([t for t in trades if t.pnl_dollar <= 0])
        win_rate = (winning_trades / total_trades * 100.0) if total_trades > 0 else 0.0

        wins = [t.pnl_dollar for t in trades if t.pnl_dollar > 0]
        losses = [abs(t.pnl_dollar) for t in trades if t.pnl_dollar < 0]
        avg_win = float(np.mean(wins)) if wins else 0.0
        avg_loss = float(np.mean(losses)) if losses else 0.0
        profit_factor = (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else (99.0 if wins else 1.0)

        # Statistical overfitting & DSR
        skew = float(daily_ret.skew()) if len(daily_ret) > 2 and not np.isnan(daily_ret.skew()) else 0.0
        kurt = float(daily_ret.kurtosis()) if len(daily_ret) > 3 and not np.isnan(daily_ret.kurtosis()) else 3.0
        dsr_val = deflated_sharpe_ratio(
            sr_observed=sr if not np.isnan(sr) else 0.0,
            n_trials=max(1, total_trades),
            sr_variance=0.01,
            skew=skew,
            kurtosis=kurt,
            n_observations=len(daily_ret),
        )

        n_splits = 4
        split_size = len(daily_ret) // n_splits
        if split_size > 10:
            is_s = []
            oos_s = []
            for s_idx in range(n_splits):
                oos_slice = daily_ret.iloc[s_idx * split_size : (s_idx + 1) * split_size]
                is_slice = daily_ret.drop(oos_slice.index)
                is_sr = sharpe_ratio(is_slice)
                oos_sr = sharpe_ratio(oos_slice)
                is_s.append([is_sr if not np.isnan(is_sr) else 0.0])
                oos_s.append([oos_sr if not np.isnan(oos_sr) else 0.0])
            pbo_val = probability_of_backtest_overfitting(np.array(is_s), np.array(oos_s))
        else:
            pbo_val = 0.0


        # Stress testing across shock windows
        def returns_fn(s_date: str, e_date: str) -> pd.Series:
            sub = daily_ret.loc[pd.to_datetime(s_date):pd.to_datetime(e_date)]
            return sub

        stress_res = run_stress_tests(returns_fn)
        pass_stress = passes_stress_gate(stress_res)

        # Deployability decision
        deployable = (
            sr >= NET_SHARPE_MIN
            and max_dd <= MAX_DRAWDOWN_MAX
            and pbo_val < PBO_MAX
            and dsr_val >= DSR_MIN
            and pass_stress
            and capital > 0.0
        )

        # Downsample equity curve to ~120 points
        equity_curve_points = []
        if len(equity_df) > 0:
            step = max(1, len(equity_df) // 120)
            sampled = equity_df.iloc[::step]
            for ts, val in sampled.items():
                equity_curve_points.append({
                    "date": ts.strftime("%Y-%m-%d"),
                    "value": round(float(val / initial_capital * 100.0), 2),
                })

        return OptionsBacktestResult(
            strategy_name=spec.name,
            ticker=ticker,
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            final_capital=capital,
            total_return_pct=round(total_ret * 100.0, 2),
            annualized_return_pct=round(ann_ret * 100.0, 2),
            sharpe_ratio=round(sr, 2),
            sortino_ratio=round(sortino, 2),
            max_drawdown_pct=round(max_dd * 100.0, 2),
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate_pct=round(win_rate, 1),
            profit_factor=round(profit_factor, 2),
            avg_win=round(avg_win, 2),
            avg_loss=round(avg_loss, 2),
            pbo=round(pbo_val, 3),
            dsr=round(dsr_val, 3),
            daily_returns=daily_ret,
            equity_curve=equity_curve_points,
            trades=trades,
            stress_results=stress_res,
            passes_stress=pass_stress,
            deployable=deployable,
        )


def main():
    """CLI entrypoint for running options strategy backtests."""
    parser = argparse.ArgumentParser(description="InvestYo Options Strategy Validation Harness")
    parser.add_argument("--strategy", type=str, default="Put Credit Spread", help="Options strategy to backtest")
    parser.add_argument("--ticker", type=str, default="SPY", help="Underlying ticker symbol")
    parser.add_argument("--start", type=str, default="2020-01-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default="2024-01-01", help="End date (YYYY-MM-DD)")
    parser.add_argument("--capital", type=float, default=100000.0, help="Initial capital ($)")
    args = parser.parse_args()

    harness = OptionsValidationHarness()
    result = harness.run_backtest(
        strategy=args.strategy,
        ticker=args.ticker,
        start_date=args.start,
        end_date=args.end,
        initial_capital=args.capital,
    )

    print("\n========================================================")
    print(f"  OPTIONS STRATEGY VALIDATION REPORT: {result.strategy_name} ({result.ticker})")
    print("========================================================")
    print(f"Period:             {result.start_date} -> {result.end_date}")
    print(f"Initial Capital:    ${result.initial_capital:,.2f}")
    print(f"Final Capital:      ${result.final_capital:,.2f}")
    print(f"Total Return:       {result.total_return_pct:+.2f}% (Annualized: {result.annualized_return_pct:+.2f}%)")
    print(f"Sharpe Ratio:       {result.sharpe_ratio:.2f} (Gate >= {NET_SHARPE_MIN})")
    print(f"Sortino Ratio:      {result.sortino_ratio:.2f}")
    print(f"Max Drawdown:       {result.max_drawdown_pct:.2f}% (Gate <= {MAX_DRAWDOWN_MAX*100:.0f}%)")
    print(f"PBO:                {result.pbo:.3f} (Gate < {PBO_MAX})")
    print(f"DSR:                {result.dsr:.3f} (Gate >= {DSR_MIN})")
    print(f"Total Trades:       {result.total_trades} (Win Rate: {result.win_rate_pct:.1f}%, Profit Factor: {result.profit_factor:.2f})")
    print(f"Avg Win / Loss:     ${result.avg_win:.2f} / ${result.avg_loss:.2f}")
    print(f"Stress Test Gate:   {'PASSED' if result.passes_stress else 'FAILED'}")
    print(f"Deployable:         {'YES ✅' if result.deployable else 'NO ❌'}")
    print("========================================================\n")


if __name__ == "__main__":
    main()
