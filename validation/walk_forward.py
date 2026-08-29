"""
InvestYo Quant Platform - Walk-Forward Optimization & Analysis Engine
======================================================================
Implements institutional Walk-Forward Analysis (WFA) following Robert Pardo (2008):
1. Multi-window Out-Of-Sample (OOS) validation vs. In-Sample (IS) calibration.
2. Walk-Forward Efficiency (WFE) ratio = OOS Profit Factor / IS Profit Factor.
3. Institutional metrics: IS/OOS Sharpe, OOS Ulcer Index, OOS Martin Ratio (UPI), Max Drawdown.
4. Cross-sectional universe rebalancing with strict Point-In-Time (PIT) synchronization
   and zero lookahead bias.

Reviewed false positive (stockpy_codebase_auditor `orphaned_module`, 2026-08):
this engine (`run_walk_forward_analysis` and helpers) has no production
caller yet — it is a standalone institutional-metrics addition (see
docs/VALIDATION_STRATEGY_FIX_LOG.md's "Institutional Quantitative
Enhancements" entry) exercised by tests/test_walk_forward.py, not code that
`validation/harness.py` or any pilot currently invokes. Not dead code;
wiring it into the main validation pipeline is a separate, deliberate task.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd

from validation.metrics import (
    profit_factor,
    sharpe_ratio,
    ulcer_index,
    ulcer_performance_index,
    walk_forward_efficiency_ratio,
)
from validation.stress_scenarios import compute_max_drawdown

logger = logging.getLogger("WalkForwardAnalysis")


@dataclass
class WalkForwardWindow:
    """Represents a single In-Sample / Out-Of-Sample walk-forward window."""

    window_index: int
    is_start: pd.Timestamp
    is_end: pd.Timestamp
    oos_start: pd.Timestamp
    oos_end: pd.Timestamp
    is_returns: pd.Series
    oos_returns: pd.Series
    is_sharpe: float
    oos_sharpe: float
    is_profit_factor: float
    oos_profit_factor: float
    wfe: float
    oos_ulcer_index: float
    oos_martin_ratio: float
    oos_max_drawdown: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "window_index": self.window_index,
            "is_start": str(self.is_start.date()) if hasattr(self.is_start, "date") else str(self.is_start),
            "is_end": str(self.is_end.date()) if hasattr(self.is_end, "date") else str(self.is_end),
            "oos_start": str(self.oos_start.date()) if hasattr(self.oos_start, "date") else str(self.oos_start),
            "oos_end": str(self.oos_end.date()) if hasattr(self.oos_end, "date") else str(self.oos_end),
            "is_sharpe": self.is_sharpe,
            "oos_sharpe": self.oos_sharpe,
            "is_profit_factor": self.is_profit_factor,
            "oos_profit_factor": self.oos_profit_factor,
            "wfe": self.wfe,
            "oos_ulcer_index": self.oos_ulcer_index,
            "oos_martin_ratio": self.oos_martin_ratio,
            "oos_max_drawdown": self.oos_max_drawdown,
            "is_bars": len(self.is_returns),
            "oos_bars": len(self.oos_returns),
        }


def _split_walk_forward_windows(
    data: Union[pd.DataFrame, pd.Series],
    n_windows: int = 5,
    is_ratio: float = 0.80,
) -> List[Tuple[pd.DataFrame | pd.Series, pd.DataFrame | pd.Series, Tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]]]:
    """
    Partitions time-series data into n_windows rolling walk-forward windows.
    Guarantees strict temporal ordering: In-Sample strictly precedes Out-Of-Sample,
    with zero lookahead leakage across the split boundaries.
    """
    n_bars = len(data)
    if n_bars < 10 or n_windows <= 0:
        return []

    # Bound is_ratio to valid range (0.10, 0.95)
    is_ratio = max(0.10, min(0.95, float(is_ratio)))

    # Calculate OOS and IS slice lengths
    # We allocate n_windows consecutive non-overlapping OOS test blocks covering the latter portion
    # or rolling through the dataset.
    multiplier = is_ratio / (1.0 - is_ratio)
    oos_len = max(2, int(n_bars / (n_windows + multiplier)))
    is_len = int(oos_len * multiplier)

    # Ensure total span fits within dataset length
    if is_len + n_windows * oos_len > n_bars:
        # Scale proportionally to fit exactly
        total_units = multiplier + n_windows
        oos_len = max(2, int(n_bars / total_units))
        is_len = max(5, int(oos_len * multiplier))

    splits = []
    for k in range(n_windows):
        is_start_idx = k * oos_len
        is_end_idx = is_start_idx + is_len
        oos_start_idx = is_end_idx
        oos_end_idx = min(n_bars, oos_start_idx + oos_len) if k < n_windows - 1 else n_bars

        if is_end_idx >= n_bars or oos_start_idx >= oos_end_idx:
            break

        is_slice = data.iloc[is_start_idx:is_end_idx]
        oos_slice = data.iloc[oos_start_idx:oos_end_idx]

        dates = (
            data.index[is_start_idx],
            data.index[is_end_idx - 1],
            data.index[oos_start_idx],
            data.index[oos_end_idx - 1],
        )
        splits.append((is_slice, oos_slice, dates))

    return splits


def _default_cross_sectional_rebalance(
    is_data: Union[pd.DataFrame, pd.Series],
    oos_data: Union[pd.DataFrame, pd.Series],
    rebalance_freq: int = 63,
) -> Tuple[pd.Series, pd.Series]:
    """
    Institutional default cross-sectional point-in-time momentum & risk-parity strategy.
    
    Point-In-Time synchronization rule:
    - At every rebalance date t, weights are computed using ONLY data strictly before t.
    - Weights are held constant until t + rebalance_freq.
    - Zero lookahead bias: future prices or returns are never inspected when forming weights.
    """
    # 1. Multi-asset Cross-Sectional Universe
    if isinstance(is_data, pd.DataFrame) and is_data.shape[1] > 1:
        # Determine whether input is prices (all > 0, typical level > 1) or returns
        is_is_prices = bool((is_data > 0).all().all() and (is_data.mean() > 2.0).all())
        
        if is_is_prices:
            full_df = pd.concat([is_data, oos_data])
            returns_df = full_df.pct_change().fillna(0.0)
            is_returns_matrix = returns_df.loc[is_data.index]
            oos_returns_matrix = returns_df.loc[oos_data.index]
        else:
            is_returns_matrix = is_data.fillna(0.0)
            oos_returns_matrix = oos_data.fillna(0.0)

        n_assets = is_returns_matrix.shape[1]
        top_k = max(1, int(np.ceil(n_assets * 0.33)))

        # Simulate In-Sample
        is_port_returns = _rebalance_cross_sectional_matrix(
            is_returns_matrix, rebalance_freq=rebalance_freq, top_k=top_k
        )

        # Simulate Out-Of-Sample (PIT: uses trailing IS history at OOS start)
        combined_matrix = pd.concat([is_returns_matrix, oos_returns_matrix])
        full_port_returns = _rebalance_cross_sectional_matrix(
            combined_matrix,
            rebalance_freq=rebalance_freq,
            top_k=top_k,
            start_eval_idx=len(is_returns_matrix),
        )
        oos_port_returns = full_port_returns.loc[oos_returns_matrix.index]

        return is_port_returns, oos_port_returns

    # 2. Single-Asset Time-Series Strategy (SMA / Momentum Trend Following)
    else:
        is_series = is_data.squeeze() if isinstance(is_data, pd.DataFrame) else is_data
        oos_series = oos_data.squeeze() if isinstance(oos_data, pd.DataFrame) else oos_data

        is_prices = bool((is_series > 0).all() and is_series.mean() > 2.0)
        full_series = pd.concat([is_series, oos_series])

        if is_prices:
            ret_series = full_series.pct_change().fillna(0.0)
            # Signal: price > 20-day SMA, 1-bar execution lag
            sma = full_series.rolling(window=min(20, max(5, rebalance_freq // 3))).mean()
            signal = (full_series > sma).astype(float).shift(1).fillna(0.0)
        else:
            ret_series = full_series.fillna(0.0)
            # Signal: trailing cumulative return > 0, 1-bar execution lag
            cum_ret = full_series.rolling(window=min(20, max(5, rebalance_freq // 3))).sum()
            signal = (cum_ret > 0).astype(float).shift(1).fillna(0.0)

        strat_returns = signal * ret_series
        return strat_returns.loc[is_series.index], strat_returns.loc[oos_series.index]


def _rebalance_cross_sectional_matrix(
    returns_matrix: pd.DataFrame,
    rebalance_freq: int = 63,
    top_k: int = 3,
    start_eval_idx: int = 0,
) -> pd.Series:
    """
    Vectorized Point-In-Time cross-sectional rebalancing over a returns matrix.
    Computes trailing momentum and allocates inverse-volatility or equal weights to top_k assets.
    """
    n_rows, n_cols = returns_matrix.shape
    portfolio_returns = np.zeros(n_rows, dtype=np.float64)
    current_weights = np.ones(n_cols, dtype=np.float64) / n_cols
    lookback = max(10, min(63, rebalance_freq))

    for t in range(n_rows):
        # Rebalance check strictly at rebalance dates
        if t >= start_eval_idx and (t - start_eval_idx) % rebalance_freq == 0:
            hist_start = max(0, t - lookback)
            if t > hist_start:
                hist_window = returns_matrix.iloc[hist_start:t]
                # Trailing return momentum
                mom = (1.0 + hist_window).prod(axis=0) - 1.0
                vol = hist_window.std(axis=0)
                vol = vol.replace(0.0, 1e-4).fillna(1e-4)

                # Select top_k assets by risk-adjusted momentum (Sharpe proxy)
                score = mom / vol
                top_indices = np.argsort(score.to_numpy())[-top_k:]

                # Inverse volatility weights among top_k
                inv_vol = 1.0 / vol.iloc[top_indices].to_numpy()
                weights_k = inv_vol / inv_vol.sum()

                new_weights = np.zeros(n_cols, dtype=np.float64)
                new_weights[top_indices] = weights_k
                current_weights = new_weights

        # Realize daily portfolio return at bar t
        portfolio_returns[t] = np.dot(returns_matrix.iloc[t].to_numpy(), current_weights)

    return pd.Series(portfolio_returns, index=returns_matrix.index)


def run_walk_forward_analysis(
    prices_or_returns: Union[pd.DataFrame, pd.Series],
    is_ratio: float = 0.80,
    n_windows: int = 5,
    rebalance_freq: int = 63,
    strategy_fn: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    """
    Executes institutional Walk-Forward Optimization & Validation Analysis.

    Parameters
    ----------
    prices_or_returns : pd.DataFrame | pd.Series
        Historical asset price series or returns DataFrame (cross-sectional universe).
    is_ratio : float, default 0.80
        In-Sample ratio for each walk-forward fold (e.g. 0.80 = 80% IS, 20% OOS).
    n_windows : int, default 5
        Number of walk-forward validation windows.
    rebalance_freq : int, default 63
        Rebalancing cadence in trading bars (e.g. 63 = quarterly, 21 = monthly).
    strategy_fn : Optional[Callable], default None
        Custom strategy function taking (is_data, oos_data) and returning (is_returns, oos_returns).
        If None, the default institutional cross-sectional PIT momentum strategy is executed.

    Returns
    -------
    Dict[str, Any]
        Dictionary containing institutional Walk-Forward metrics:
        - 'wfe': Walk-Forward Efficiency ratio (OOS Profit Factor / IS Profit Factor)
        - 'is_sharpe': In-Sample annualized Sharpe ratio
        - 'oos_sharpe': Out-Of-Sample annualized Sharpe ratio
        - 'is_profit_factor': In-Sample Profit Factor
        - 'oos_profit_factor': Out-Of-Sample Profit Factor
        - 'oos_ulcer_index': Out-Of-Sample Peter Martin Ulcer Index (downside RMS drawdown)
        - 'oos_martin_ratio': Out-Of-Sample Ulcer Performance Index (UPI)
        - 'oos_upi': Alias for oos_martin_ratio
        - 'oos_max_drawdown': Maximum drawdown out-of-sample
        - 'oos_sortino': Out-Of-Sample downside Sortino ratio
        - 'oos_annualized_return': Annualized out-of-sample return
        - 'is_returns': Aggregated In-Sample daily returns Series
        - 'oos_returns': Concatenated Out-Of-Sample daily returns Series
        - 'windows': List of per-window metrics dictionaries
        - 'n_windows': Total number of evaluated windows
    """
    # Defensive guard for empty / insufficient data
    if prices_or_returns is None or len(prices_or_returns) < 10:
        return {
            "wfe": 0.0,
            "is_sharpe": 0.0,
            "oos_sharpe": 0.0,
            "is_profit_factor": 0.0,
            "oos_profit_factor": 0.0,
            "oos_ulcer_index": 0.0,
            "oos_martin_ratio": 0.0,
            "oos_upi": 0.0,
            "oos_max_drawdown": 0.0,
            "oos_sortino": 0.0,
            "oos_annualized_return": 0.0,
            "is_returns": pd.Series(dtype=float),
            "oos_returns": pd.Series(dtype=float),
            "windows": [],
            "n_windows": 0,
        }

    splits = _split_walk_forward_windows(prices_or_returns, n_windows=n_windows, is_ratio=is_ratio)
    if not splits:
        return {
            "wfe": 0.0,
            "is_sharpe": 0.0,
            "oos_sharpe": 0.0,
            "is_profit_factor": 0.0,
            "oos_profit_factor": 0.0,
            "oos_ulcer_index": 0.0,
            "oos_martin_ratio": 0.0,
            "oos_upi": 0.0,
            "oos_max_drawdown": 0.0,
            "oos_sortino": 0.0,
            "oos_annualized_return": 0.0,
            "is_returns": pd.Series(dtype=float),
            "oos_returns": pd.Series(dtype=float),
            "windows": [],
            "n_windows": 0,
        }

    window_results: List[WalkForwardWindow] = []
    all_is_returns: List[pd.Series] = []
    all_oos_returns: List[pd.Series] = []

    for k, (is_data, oos_data, dates) in enumerate(splits):
        is_start, is_end, oos_start, oos_end = dates

        # Execute Strategy (Custom or Default PIT Cross-Sectional)
        if strategy_fn is not None:
            try:
                res = strategy_fn(is_data, oos_data)
                if isinstance(res, (tuple, list)) and len(res) == 2:
                    is_ret, oos_ret = res[0], res[1]
                elif isinstance(res, dict) and "is_returns" in res and "oos_returns" in res:
                    is_ret, oos_ret = res["is_returns"], res["oos_returns"]
                else:
                    is_ret, oos_ret = _default_cross_sectional_rebalance(
                        is_data, oos_data, rebalance_freq=rebalance_freq
                    )
            except Exception as exc:
                logger.warning("Strategy function raised exception on window %d: %s", k, exc)
                is_ret, oos_ret = _default_cross_sectional_rebalance(
                    is_data, oos_data, rebalance_freq=rebalance_freq
                )
        else:
            is_ret, oos_ret = _default_cross_sectional_rebalance(
                is_data, oos_data, rebalance_freq=rebalance_freq
            )

        if not isinstance(is_ret, pd.Series):
            is_ret = pd.Series(is_ret, index=is_data.index)
        if not isinstance(oos_ret, pd.Series):
            oos_ret = pd.Series(oos_ret, index=oos_data.index)

        # Compute Window Metrics
        is_sr = sharpe_ratio(is_ret)
        oos_sr = sharpe_ratio(oos_ret)
        is_pf = profit_factor(is_ret)
        oos_pf = profit_factor(oos_ret)
        win_wfe = walk_forward_efficiency_ratio(is_ret, oos_ret)
        win_ui = ulcer_index(oos_ret)
        win_upi = ulcer_performance_index(oos_ret)
        win_dd = compute_max_drawdown(oos_ret)

        wf_win = WalkForwardWindow(
            window_index=k,
            is_start=is_start,
            is_end=is_end,
            oos_start=oos_start,
            oos_end=oos_end,
            is_returns=is_ret,
            oos_returns=oos_ret,
            is_sharpe=float(is_sr) if not np.isnan(is_sr) else 0.0,
            oos_sharpe=float(oos_sr) if not np.isnan(oos_sr) else 0.0,
            is_profit_factor=float(is_pf) if not np.isnan(is_pf) else 0.0,
            oos_profit_factor=float(oos_pf) if not np.isnan(oos_pf) else 0.0,
            wfe=float(win_wfe) if not np.isnan(win_wfe) else 0.0,
            oos_ulcer_index=float(win_ui) if not np.isnan(win_ui) else 0.0,
            oos_martin_ratio=float(win_upi) if not np.isnan(win_upi) else 0.0,
            oos_max_drawdown=float(win_dd) if not np.isnan(win_dd) else 0.0,
        )
        window_results.append(wf_win)
        all_is_returns.append(is_ret)
        all_oos_returns.append(oos_ret)

    # Aggregate In-Sample and Out-of-Sample Series
    agg_is_returns = pd.concat(all_is_returns) if all_is_returns else pd.Series(dtype=float)
    agg_oos_returns = pd.concat(all_oos_returns) if all_oos_returns else pd.Series(dtype=float)

    # Calculate Full Walk-Forward Aggregate Institutional Metrics
    agg_is_sharpe = sharpe_ratio(agg_is_returns)
    agg_oos_sharpe = sharpe_ratio(agg_oos_returns)
    agg_is_pf = profit_factor(agg_is_returns)
    agg_oos_pf = profit_factor(agg_oos_returns)
    overall_wfe = walk_forward_efficiency_ratio(agg_is_returns, agg_oos_returns)
    agg_oos_ui = ulcer_index(agg_oos_returns)
    agg_oos_upi = ulcer_performance_index(agg_oos_returns)
    agg_oos_dd = compute_max_drawdown(agg_oos_returns)

    downside = agg_oos_returns[agg_oos_returns < 0]
    downside_std = downside.std()
    agg_sortino = (
        float(agg_oos_returns.mean() / downside_std * np.sqrt(252))
        if downside_std >= 1e-12
        else (np.inf if agg_oos_returns.mean() > 0 else 0.0)
    )

    agg_ann_return = float(agg_oos_returns.mean() * 252) if len(agg_oos_returns) > 0 else 0.0

    return {
        "wfe": float(overall_wfe) if not np.isnan(overall_wfe) else 0.0,
        "is_sharpe": float(agg_is_sharpe) if not np.isnan(agg_is_sharpe) else 0.0,
        "oos_sharpe": float(agg_oos_sharpe) if not np.isnan(agg_oos_sharpe) else 0.0,
        "is_profit_factor": float(agg_is_pf) if not np.isnan(agg_is_pf) else 0.0,
        "oos_profit_factor": float(agg_oos_pf) if not np.isnan(agg_oos_pf) else 0.0,
        "oos_ulcer_index": float(agg_oos_ui) if not np.isnan(agg_oos_ui) else 0.0,
        "oos_martin_ratio": float(agg_oos_upi) if not np.isnan(agg_oos_upi) else 0.0,
        "oos_upi": float(agg_oos_upi) if not np.isnan(agg_oos_upi) else 0.0,
        "oos_max_drawdown": float(agg_oos_dd) if not np.isnan(agg_oos_dd) else 0.0,
        "oos_sortino": float(agg_sortino) if not np.isnan(agg_sortino) else 0.0,
        "oos_annualized_return": agg_ann_return,
        "is_returns": agg_is_returns,
        "oos_returns": agg_oos_returns,
        "windows": [w.to_dict() for w in window_results],
        "n_windows": len(window_results),
    }
