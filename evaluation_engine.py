"""Strategy performance evaluation. Provides calibration_curve (conviction vs. realized win rate from closed trades) and calculate_equity_curve_metrics (Sharpe, Calmar, max drawdown, max-DD duration, CAGR) from an account equity curve, plus portfolio heat / edge / attribution helpers. Undefined statistics degrade to NaN, never a fabricated 0.0."""

# =============================================================================
# MODULE: EVALUATION ENGINE
# File: evaluation_engine.py
# Description: Implements post-trade evaluation (MFE/MAE/Edge Ratio), 
#              Kelly Criterion position sizing, and Brinson-Fachler sector attribution.
# =============================================================================

import json
import logging
import math
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional
from diagnostics_and_visuals import telemetry

# Configure module logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("EvaluationEngine")

# Try importing QuantFAA library for Brinson-Fachler attribution
try:
    import quantfaa  # type: ignore
    QUANTFAA_AVAILABLE = True
except ImportError:
    QUANTFAA_AVAILABLE = False


class EvaluationEngine:
    """
    Handles post-trade analytics, asset allocation optimization,
    and performance attribution modeling.
    """
    def __init__(self, max_portfolio_heat: float = 0.06):
        # 6% total institutional open risk threshold
        self.max_portfolio_heat = max_portfolio_heat

    def calculate_edge_ratio(
        self, 
        history_df: pd.DataFrame, 
        trade_entry_price: float, 
        entry_date: Any, 
        exit_date: Any
    ) -> Dict[str, Any]:
        """
        Calculates Maximum Favorable Excursion (MFE) and Maximum Adverse Excursion (MAE),
        normalizing MFE against MAE to output the Edge Ratio.
        Logs standard deviation of returns alongside the Edge Ratio as structured JSON.
        MAE is always reported as a POSITIVE number representing the magnitude of adverse move.
        """
        if history_df is None or history_df.empty:
            telemetry.warning("Empty history DataFrame provided for Edge Ratio calculation.")
            return {"MFE": np.nan, "MAE": np.nan, "Edge Ratio": np.nan, "Return Std Dev": np.nan}

        try:
            # Ensure index is datetime-like
            if not isinstance(history_df.index, pd.DatetimeIndex):
                history_df = history_df.copy()
                history_df.index = pd.to_datetime(history_df.index)

            # Strip timezones for naive date comparison
            if history_df.index.tz is not None:
                history_df = history_df.copy()
                history_df.index = history_df.index.tz_convert(None)  # F-06: tz_localize raises TypeError on tz-aware index

            entry_ts = pd.to_datetime(entry_date).tz_localize(None)
            exit_ts = pd.to_datetime(exit_date).tz_localize(None)

            # Slice history during the hold period (inclusive)
            hold_period = history_df.loc[entry_ts:exit_ts]

            if hold_period.empty:
                telemetry.warning(f"No pricing data found between {entry_ts} and {exit_ts}.")
                return {"MFE": np.nan, "MAE": np.nan, "Edge Ratio": np.nan, "Return Std Dev": np.nan}

            # Localized high and low extreme prices
            max_high = float(hold_period["High"].max())
            min_low = float(hold_period["Low"].min())

            # MFE and MAE relative to the trade entry price
            if trade_entry_price > 0:
                mfe = max(0.0, (max_high - trade_entry_price) / trade_entry_price)
                mae = max(0.0, (trade_entry_price - min_low) / trade_entry_price)
            else:
                mfe = np.nan
                mae = np.nan

            # Normalize MFE by MAE to calculate Edge Ratio
            if mae > 0:
                edge_ratio = mfe / mae
            else:
                # Avoid division by zero: if MFE is positive but MAE is 0, return high default proxy
                edge_ratio = mfe / 1e-6 if mfe > 0 else 0.0

            # Calculate returns standard deviation during the hold period
            pct_returns = hold_period["Close"].pct_change().dropna()
            std_dev = float(pct_returns.std()) if len(pct_returns) > 1 else 0.0

            # Log metrics as structured JSON telemetry
            log_payload = {
                "metric": "post_trade_evaluation",
                "trade_entry_price": float(trade_entry_price) if not pd.isna(trade_entry_price) else None,
                "entry_date": str(entry_ts.date()),
                "exit_date": str(exit_ts.date()),
                "mfe": float(mfe) if not pd.isna(mfe) else None,
                "mae": float(mae) if not pd.isna(mae) else None,
                "edge_ratio": float(edge_ratio) if not pd.isna(edge_ratio) else None,
                "std_dev_returns": float(std_dev)
            }
            telemetry.info(json.dumps(log_payload))

            return {
                "MFE": float(mfe) if not pd.isna(mfe) else np.nan,
                "MAE": float(mae) if not pd.isna(mae) else np.nan,
                "Edge Ratio": float(edge_ratio) if not pd.isna(edge_ratio) else np.nan,
                "Return Std Dev": float(std_dev)
            }

        except Exception as e:
            telemetry.error(json.dumps({
                "event": "edge_ratio_failed",
                "error": str(e)
            }))
            return {"MFE": np.nan, "MAE": np.nan, "Edge Ratio": np.nan, "Return Std Dev": np.nan}

    def calculate_kelly_target(
        self, 
        expected_return: float, 
        variance: float, 
        win_probability: Optional[float] = None, 
        win_loss_ratio: Optional[float] = None,
        half_kelly: bool = True
    ) -> Dict[str, Any]:
        """
        Calculates optimal fractional allocation using the Kelly Criterion.
        Supports win-rate/ratio calculations and continuous return/variance formulations.
        Constrained by a Half-Kelly allocation factor and bounded to [0.0, 1.0].
        """
        try:
            # 1. Win-Loss Probability Method
            if win_probability is not None and win_loss_ratio is not None:
                if win_loss_ratio > 0:
                    kelly_fraction = win_probability - (1.0 - win_probability) / win_loss_ratio
                else:
                    kelly_fraction = 0.0
            # 2. Continuous Return/Variance Method
            elif variance > 0:
                kelly_fraction = expected_return / variance
            else:
                kelly_fraction = 0.0

            # Apply Half-Kelly constraints
            if half_kelly:
                kelly_fraction = kelly_fraction / 2.0

            # Clamp allocation range to [0.0, 1.0] to protect against bankruptcy / shorting
            kelly_fraction = float(max(0.0, min(1.0, kelly_fraction)))

            return {"Kelly Target": kelly_fraction}

        except Exception as e:
            telemetry.error(json.dumps({
                "event": "kelly_target_failed",
                "error": str(e)
            }))
            return {"Kelly Target": 0.0}

    def calculate_excursion_metrics(
        self,
        entry_price: float,
        high_price: float,
        low_price: float,
        position_type: str = 'long'
    ) -> tuple:
        """
        Calculates Maximum Favorable Excursion (MFE) and Maximum Adverse Excursion (MAE).
        Both values are returned as POSITIVE magnitudes (fraction of entry price).
        F-02 FIX: MAE must be a positive loss magnitude, not a negative signed value.
        Returns: (mae, mfe) — consistent with evaluate_portfolio() unpack order.
        """
        try:
            if pd.isna(entry_price) or entry_price <= 0:
                return 0.0, 0.0

            if position_type == 'long':
                # MAE: how far price fell below entry — positive loss magnitude
                mae = (entry_price - low_price)  / entry_price   # always >= 0
                # MFE: how far price rose above entry — positive gain magnitude
                mfe = (high_price  - entry_price) / entry_price  # always >= 0
            else:  # short position
                # Adverse move is price rising above entry
                mae = (high_price  - entry_price) / entry_price
                # Favorable move is price falling below entry
                mfe = (entry_price - low_price)  / entry_price

            # Clamp to zero — excursion magnitudes cannot be negative
            mae = max(0.0, mae)
            mfe = max(0.0, mfe)

            return round(mae, 4), round(mfe, 4)
        except Exception as e:
            logger.error(f"Error calculating excursion metrics: {e}")
            return 0.0, 0.0

    def calculate_realized_slippage(self, entry_price: float, expected_price: float) -> float:
        """
        Calculates Implementation Shortfall (Realized Slippage).
        Measures the percentage difference between the actual executed Entry Price
        and the Expected (Arrival) Price generated by the quantitative signal.
        """
        try:
            if pd.isna(entry_price) or pd.isna(expected_price) or expected_price <= 0:
                return 0.0
            
            # Positive slippage means we paid more than expected (drag on returns)
            slippage = (entry_price - expected_price) / expected_price
            return round(slippage, 4)
        except Exception as e:
            logger.error(f"Error calculating realized slippage: {e}")
            return 0.0

    def calculate_tail_dependency(self, var_95: float, beta: float) -> float:
        """
        Calculates CoVaR Proxy (Conditional Value at Risk / Tail Dependency Risk).
        Measures systemic tail dependency by scaling the asset's idiosyncratic
        Value at Risk (VaR) by its market Beta to gauge vulnerability during market shocks.
        """
        try:
            if pd.isna(var_95) or pd.isna(beta):
                return 0.0
            
            # CoVaR Proxy: absolute VaR scaled by Beta. 
            # Negative Beta assets act as hedges, so we floor beta at 0.0 to represent 0 systemic tail drag.
            covar = abs(var_95) * max(beta, 0.0)
            return round(covar, 4)
        except Exception as e:
            logger.error(f"Error calculating tail dependency risk: {e}")
            return 0.0

    def calculate_brinson_fachler(self, portfolio_weights, benchmark_weights,
                                  portfolio_returns=None, benchmark_returns=None):
        """
        Implements Brinson-Fachler performance attribution modeling.
        If portfolio_weights is a DataFrame, it behaves like the old DataFrame method for compatibility.
        Allocation Effect = (w_p - w_b) * (R_b - R_total_b)
        Selection Effect = w_b * (R_p - R_b)
        """
        # Compatibility check: if a DataFrame is passed, route to compat handler
        if isinstance(portfolio_weights, pd.DataFrame):
            return self._calculate_brinson_fachler_compat(portfolio_weights, benchmark_weights)

        try:
            df = pd.DataFrame({
                'w_p': portfolio_weights,
                'w_b': benchmark_weights,
                'R_p': portfolio_returns,
                'R_b': benchmark_returns
            }).fillna(0)

            # Benchmark total return
            R_total_b = np.average(df['R_b'], weights=df['w_b']) if df['w_b'].sum() > 0 else 0

            df['BF_Allocation'] = (df['w_p'] - df['w_b']) * (df['R_b'] - R_total_b)
            df['BF_Selection'] = df['w_b'] * (df['R_p'] - df['R_b'])
            
            return df[['BF_Allocation', 'BF_Selection']]
        except Exception as e:
            logger.error(f"Error calculating Brinson-Fachler attribution: {e}")
            return pd.DataFrame()

    def _calculate_brinson_fachler_compat(
        self, 
        portfolio_df: pd.DataFrame, 
        benchmark_df: pd.DataFrame
    ) -> Dict[str, Any]:
        """
        Executes a Brinson-Fachler performance attribution model to decompose
        active portfolio returns into Allocation, Selection, and Interaction effects.
        Reshapes input DataFrames and integrates QuantFAA with local robust fallback.
        """
        try:
            # Attempt to use QuantFAA library if available
            if QUANTFAA_AVAILABLE:
                try:
                    p_df = portfolio_df.rename(columns=lambda x: x.strip())
                    b_df = benchmark_df.rename(columns=lambda x: x.strip())
                    pass
                except Exception as lib_err:
                    telemetry.warning(f"QuantFAA attribution call failed: {lib_err}. Reverting to fallback.")

            # Fallback/Local Implementation
            p_clean = portfolio_df.copy()
            b_clean = benchmark_df.copy()

            # Normalize column names
            p_clean.columns = [col.strip().lower() for col in p_clean.columns]
            b_clean.columns = [col.strip().lower() for col in b_clean.columns]

            # Rename mapping helper
            name_map = {
                "sector": "sector",
                "portfolio_weight": "weight_p",
                "portfolio_return": "return_p",
                "benchmark_weight": "weight_b",
                "benchmark_return": "return_b",
                "weight": "weight_p",
                "return": "return_p"
            }

            p_clean = p_clean.rename(columns={c: name_map[c] for c in p_clean.columns if c in name_map})
            b_clean = b_clean.rename(columns={c: name_map[c] for c in b_clean.columns if c in name_map})

            # Check required columns
            for col in ["sector", "weight_p", "return_p"]:
                if col not in p_clean.columns:
                    if col == "weight_p" and "weight" in p_clean.columns:
                        p_clean["weight_p"] = p_clean["weight"]
                    elif col == "return_p" and "return" in p_clean.columns:
                        p_clean["return_p"] = p_clean["return"]
                    else:
                        raise ValueError(f"Portfolio DataFrame missing required column: {col}")

            for col in ["sector", "weight_b", "return_b"]:
                if col not in b_clean.columns:
                    if col == "weight_b" and "weight" in b_clean.columns:
                        b_clean["weight_b"] = b_clean["weight"]
                    elif col == "return_b" and "return" in b_clean.columns:
                        b_clean["return_b"] = b_clean["return"]
                    else:
                        raise ValueError(f"Benchmark DataFrame missing required column: {col}")

            # Merge portfolio and benchmark on Sector
            merged = pd.merge(
                p_clean[["sector", "weight_p", "return_p"]],
                b_clean[["sector", "weight_b", "return_b"]],
                on="sector",
                how="outer"
            ).fillna(0.0)

            # Total returns calculations
            r_p = float((merged["weight_p"] * merged["return_p"]).sum())
            r_b = float((merged["weight_b"] * merged["return_b"]).sum())
            active_return = r_p - r_b

            # Decompose effects per sector
            merged["allocation_effect"] = (merged["weight_p"] - merged["weight_b"]) * (merged["return_b"] - r_b)
            merged["selection_effect"] = merged["weight_b"] * (merged["return_p"] - merged["return_b"])
            merged["interaction_effect"] = (merged["weight_p"] - merged["weight_b"]) * (merged["return_p"] - merged["return_b"])
            merged["total_attribution"] = (
                merged["allocation_effect"] + 
                merged["selection_effect"] + 
                merged["interaction_effect"]
            )

            # Total aggregate effects
            total_alloc = float(merged["allocation_effect"].sum())
            total_select = float(merged["selection_effect"].sum())
            total_inter = float(merged["interaction_effect"].sum())
            sum_attribution = total_alloc + total_select + total_inter

            diff = abs(active_return - sum_attribution)
            if diff > 1e-5:
                telemetry.warning(f"Brinson-Fachler attribution drift detected: {diff:.6f}")

            # F-03 FIX: Replace iterrows() with vectorized dict comprehension
            sector_attribution = {
                row["sector"]: {
                    "weight_p":           round(float(row["weight_p"]),           6),
                    "weight_b":           round(float(row["weight_b"]),           6),
                    "return_p":           round(float(row["return_p"]),           6),
                    "return_b":           round(float(row["return_b"]),           6),
                    "allocation_effect":  round(float(row["allocation_effect"]),  6),
                    "selection_effect":   round(float(row["selection_effect"]),   6),
                    "interaction_effect": round(float(row["interaction_effect"]), 6),
                    "total_attribution":  round(float(row["total_attribution"]),  6),
                }
                for row in merged.to_dict('records')
            }

            return {
                "Portfolio Return": r_p,
                "Benchmark Return": r_b,
                "Active Return": active_return,
                "Allocation Effect": total_alloc,
                "Selection Effect": total_select,
                "Interaction Effect": total_inter,
                "Attribution Sum": sum_attribution,
                "Sector Details": sector_attribution
            }

        except Exception as e:
            telemetry.error(json.dumps({
                "event": "brinson_attribution_failed",
                "error": str(e)
            }))
            return {
                "Portfolio Return": 0.0,
                "Benchmark Return": 0.0,
                "Active Return": 0.0,
                "Allocation Effect": 0.0,
                "Selection Effect": 0.0,
                "Interaction Effect": 0.0,
                "Attribution Sum": 0.0,
                "Sector Details": {}
            }

    def calculate_portfolio_heat(self, positions_df: pd.DataFrame) -> float:
        """
        Calculates total open risk ("Portfolio Heat") across all positions.
        Used to trigger the dynamic execution halt.
        """
        try:
            if 'position_size' not in positions_df.columns or 'stop_loss_pct' not in positions_df.columns:
                return 0.0

            total_capital = positions_df['position_size'].sum()
            if total_capital == 0:
                return 0.0

            # Open Risk per position = Position Size * Stop Loss Penalty
            positions_df['open_risk'] = positions_df['position_size'] * positions_df['stop_loss_pct']
            total_portfolio_risk = positions_df['open_risk'].sum()

            portfolio_heat = total_portfolio_risk / total_capital
            return round(portfolio_heat, 4)
        except Exception as e:
            logger.error(f"Error calculating portfolio heat: {e}")
            return 0.0

    def evaluate_portfolio(
        self,
        df: pd.DataFrame,
        # BUG-FIX: mutable default argument `pd.DataFrame()` is created once at
        # class-definition time and shared across every call that omits the arg.
        # Any mutation of that object would persist between invocations. Use None
        # and create an empty DataFrame inside the function body instead.
        benchmark_df: Optional[pd.DataFrame] = None,
        data_provider=None,
    ) -> pd.DataFrame:
        """
        Main execution method mapping MAE, MFE, Portfolio Heat, and Brinson-Fachler 
        metrics identically to internal DTO keys requested by config.py.
        Uses transactions_store to pull actual entry prices/timestamps and fetches
        actual historical OHLC of the hold period from data_provider.
        """
        logger.info("Running post-trade execution analytics...")
        from transactions_store import TransactionsStore

        if benchmark_df is None:
            benchmark_df = pd.DataFrame()

        df = df.copy()
        
        # Ensure target columns exist in the DataFrame
        for col in ['Entry_Price', 'MAE', 'MFE', 'Edge Ratio', 'Realized Slippage']:
            if col not in df.columns:
                df[col] = np.nan
        
        store = TransactionsStore()

        # Batch pre-fetch technical history for ALL symbols ONCE, up front, instead
        # of a per-row fetch_technical_raw([symbol]) call inside the loop below.
        # fetch_technical_raw is a batch API (list in, internally parallel); calling
        # it one symbol at a time defeats that parallelism (a latent N+1 fetch). The
        # dict-provider fast path is already fully pre-fetched, so mirror it here by
        # materializing one result dict for the live-provider path too.
        prefetched_tech: dict = {}
        if data_provider is not None and hasattr(data_provider, 'fetch_technical_raw'):
            try:
                symbols = df['Symbol'].dropna().unique().tolist()
                if symbols:
                    prefetched_tech = data_provider.fetch_technical_raw(symbols) or {}
            except Exception as e:
                logger.warning(f"Batch technical pre-fetch failed: {e}")
                prefetched_tech = {}

        # 1. Evaluate MAE / MFE / Edge Ratio / Slippage against real trade history
        eval_results = {}
        for idx, row in df.iterrows():
            symbol = row['Symbol']
            # Find trade history for this symbol
            trade_df = store.get_trade_history(symbol)
            
            entry_price = np.nan
            mae = np.nan
            mfe = np.nan
            edge_ratio = np.nan
            slippage = np.nan
            
            if not trade_df.empty:
                # Get the most recent trade (open or closed)
                # Sort by entry_ts descending
                trade_df['entry_ts'] = pd.to_datetime(trade_df['entry_ts'])
                trade_df = trade_df.sort_values(by='entry_ts', ascending=False)
                latest_trade = trade_df.iloc[0]
                
                entry_price = float(latest_trade['entry_price'])
                entry_ts = latest_trade['entry_ts']
                exit_ts = latest_trade['exit_ts']
                if pd.isna(exit_ts) or exit_ts is None:
                    from datetime import datetime, timezone
                    exit_ts = datetime.now(timezone.utc).replace(tzinfo=None)
                else:
                    exit_ts = pd.to_datetime(exit_ts)
                
                # Fetch actual OHLC history from data provider to get actual High and Low of the hold period
                if data_provider is not None:
                    try:
                        if hasattr(data_provider, 'fetch_technical_raw'):
                            # Index into the single batch pre-fetch above instead of
                            # re-fetching this symbol on its own (latent N+1).
                            history_df = prefetched_tech.get(symbol)
                        elif isinstance(data_provider, dict):
                            history_df = data_provider.get(symbol)
                        else:
                            history_df = None

                        if history_df is not None and not history_df.empty:
                            history_df = history_df.copy()
                            if not isinstance(history_df.index, pd.DatetimeIndex):
                                history_df.index = pd.to_datetime(history_df.index)
                            
                            # Naive datetimes comparison
                            history_df.index = history_df.index.tz_localize(None)
                            naive_entry = entry_ts.tz_localize(None) if entry_ts.tzinfo else entry_ts
                            naive_exit = exit_ts.tz_localize(None) if exit_ts.tzinfo else exit_ts
                            
                            hold_period = history_df.loc[naive_entry:naive_exit]
                            if not hold_period.empty:
                                max_high = float(hold_period['High'].max())
                                min_low = float(hold_period['Low'].min())
                                position_type = str(latest_trade['side']).lower().strip()
                                
                                mae, mfe = self.calculate_excursion_metrics(
                                    entry_price, max_high, min_low, position_type
                                )
                                
                                # Edge Ratio calculation
                                if mae > 0:
                                    edge_ratio = mfe / mae
                                else:
                                    edge_ratio = mfe / 1e-6 if mfe > 0 else 0.0
                                
                                # Realized Slippage
                                arrival_price = float(row.get('Price', entry_price))
                                slippage = self.calculate_realized_slippage(entry_price, arrival_price)
                    except Exception as e:
                        logger.warning(f"Failed to fetch actual hold period history for {symbol}: {e}")
            
            # If no transaction history was found but Entry_Price exists in the input df, we can fall back
            if np.isnan(entry_price) and 'Entry_Price' in row and not pd.isna(row['Entry_Price']):
                entry_price = float(row['Entry_Price'])
                if 'High' in row and not pd.isna(row['High']) and 'Low' in row and not pd.isna(row['Low']):
                    max_high = float(row['High'])
                    min_low = float(row['Low'])
                    mae, mfe = self.calculate_excursion_metrics(
                        entry_price, max_high, min_low, 'long'
                    )
                    if mae > 0:
                        edge_ratio = mfe / mae
                    else:
                        edge_ratio = mfe / 1e-6 if mfe > 0 else 0.0
            
            eval_results[idx] = {
                'Entry_Price': entry_price,
                'MAE': mae,
                'MFE': mfe,
                'Edge Ratio': edge_ratio,
                'Realized Slippage': slippage
            }

        # Vectorized mapping to avoid iterrows mutation (Constraint #3)
        for col in ['Entry_Price', 'MAE', 'MFE', 'Edge Ratio', 'Realized Slippage']:
            df[col] = df.index.map(lambda idx: eval_results.get(idx, {}).get(col, np.nan))

        # 2. Evaluate Portfolio Heat against Max Thresholds
        if 'position_size' not in df.columns:
            df['position_size'] = 10000.0 
        if 'stop_loss_pct' not in df.columns:
            if 'VaR 95' in df.columns:
                df['stop_loss_pct'] = df['VaR 95'].abs()
            elif 'VaR_95' in df.columns:
                df['stop_loss_pct'] = df['VaR_95'].abs()
            else:
                df['stop_loss_pct'] = 0.05

        portfolio_heat = self.calculate_portfolio_heat(df)
        df['Portfolio_Heat'] = portfolio_heat

        # SYSTEMIC HALT LOGIC
        if portfolio_heat > self.max_portfolio_heat:
            logger.critical(f"🛑 PORTFOLIO HEAT BREACH: {portfolio_heat*100:.2f}% exceeds {self.max_portfolio_heat*100:.2f}% limit. Halting new trade allocations.")
            if 'Action Signal' in df.columns:
                df['Action Signal'] = df['Action Signal'].apply(
                    lambda s: "AVOID (HEAT LIMIT)" if s in ["BUY", "STRONG BUY"] else s
                )

        # 3. Evaluate Brinson-Fachler Sector Attribution
        if 'sector' in df.columns and not benchmark_df.empty:
            total_position_size = df['position_size'].sum()
            if total_position_size <= 0.0:
                # Watchlist-only run (no held shares) — all position_sizes are 0.
                # Dividing by zero here would crash the entire pipeline (ZeroDivisionError).
                # Skip attribution and default to 0 rather than fabricating weights.
                logger.warning(
                    "All position_sizes are zero (watchlist-only or no holdings) — "
                    "skipping Brinson-Fachler attribution, defaulting BF columns to 0."
                )
                df['BF_Allocation'] = 0.0
                df['BF_Selection'] = 0.0
            else:
                port_sector_weights = (
                    df.groupby('sector')['position_size'].sum() / total_position_size
                )
                port_sector_returns = df.groupby('sector')['Relative_Strength'].mean()

                bench_weights = benchmark_df.set_index('sector')['weight']
                bench_returns = benchmark_df.set_index('sector')['return']

                bf_df = self.calculate_brinson_fachler(
                    port_sector_weights, bench_weights, port_sector_returns, bench_returns
                )

                df['BF_Allocation'] = df['sector'].map(bf_df['BF_Allocation']).fillna(0.0).round(4)
                df['BF_Selection'] = df['sector'].map(bf_df['BF_Selection']).fillna(0.0).round(4)
        else:
            logger.warning("Missing sector or benchmark data. Defaulting Brinson-Fachler to 0.")
            df['BF_Allocation'] = 0.0
            df['BF_Selection'] = 0.0

        # 5. Evaluate Tail Dependency Risk (CoVaR Proxy)
        var_key = 'VaR 95' if 'VaR 95' in df.columns else 'VaR_95' if 'VaR_95' in df.columns else None
        if var_key and 'Beta' in df.columns:
            df['CoVaR Proxy'] = df.apply(
                lambda row: self.calculate_tail_dependency(row[var_key], row['Beta']), axis=1
            )
        else:
            df['CoVaR Proxy'] = 0.0

        return df


# =============================================================================
# MODULE-LEVEL: Conviction Calibration (1.2 — "when 0.80, does it win 80%?")
# =============================================================================

_CALIBRATION_COLUMNS = [
    "bin_low", "bin_high", "bin_center",
    "conviction_mean", "win_rate", "count", "perfect_calibration",
]

def _empty_calibration_df() -> pd.DataFrame:
    dtypes = {c: (int if c == "count" else float) for c in _CALIBRATION_COLUMNS}
    return pd.DataFrame({c: pd.Series(dtype=dt) for c, dt in dtypes.items()})


def calibration_curve(
    transactions_store,
    n_bins: int = 10,
    min_trades_per_bin: int = 5,
) -> pd.DataFrame:
    """Reliability diagram: bins closed trades by conviction, computes actual win rate.

    Args:
        transactions_store: A ``TransactionsStore`` instance.
        n_bins: Number of equal-width conviction bins spanning [0, 1].
        min_trades_per_bin: Bins with fewer trades receive ``win_rate=NaN``
            (insufficient sample; never fabricated — CONSTRAINT #4).

    Returns:
        DataFrame with columns ``bin_low``, ``bin_high``, ``bin_center``,
        ``conviction_mean``, ``win_rate``, ``count``, ``perfect_calibration``.
        Empty (correct schema, zero rows) when no conviction-annotated closed
        trades exist or any read fails (dead-letter tolerant — CONSTRAINT #6).

    Win definition (side-aware, never fabricated):
        * long  — ``exit_price > entry_price``
        * short — ``exit_price < entry_price``
    """
    try:
        df = transactions_store.closed_trades_df()
    except Exception as exc:
        logger.warning("calibration_curve: failed to read closed trades: %s", exc)
        return _empty_calibration_df()

    if df.empty or "conviction" not in df.columns:
        return _empty_calibration_df()

    df = df.dropna(subset=["conviction", "entry_price", "exit_price"]).copy()
    if df.empty:
        return _empty_calibration_df()

    df["side"] = df["side"].fillna("long").str.lower().str.strip()
    df["win"] = (
        ((df["side"] == "long") & (df["exit_price"] > df["entry_price"]))
        | ((df["side"] == "short") & (df["exit_price"] < df["entry_price"]))
    )

    bins = np.linspace(0.0, 1.0, n_bins + 1)
    df["_bin"] = pd.cut(df["conviction"], bins=bins, include_lowest=True)

    records = []
    for interval in sorted(df["_bin"].cat.categories):
        bucket = df[df["_bin"] == interval]
        count = len(bucket)
        bin_low = float(interval.left)
        bin_high = float(interval.right)
        bin_center = (bin_low + bin_high) / 2.0
        conviction_mean = float(bucket["conviction"].mean()) if count > 0 else float("nan")
        win_rate = float(bucket["win"].mean()) if count >= min_trades_per_bin else float("nan")
        records.append({
            "bin_low": bin_low,
            "bin_high": bin_high,
            "bin_center": bin_center,
            "conviction_mean": conviction_mean,
            "win_rate": win_rate,
            "count": count,
            "perfect_calibration": bin_center,
        })

    result = pd.DataFrame(records)
    result["count"] = result["count"].astype(int)
    return result


# =============================================================================
# MODULE-LEVEL: Account Equity Curve Risk/Performance Stats
# =============================================================================

# Minimum distinct daily snapshots before Sharpe/Calmar/CAGR are meaningful
# (mirrors sizing/kelly.py's MIN_TRADES_REQUIRED module-top-constant convention
# instead of an inline magic number).
MIN_SNAPSHOTS_FOR_STATS = 20

_EQUITY_METRIC_KEYS = (
    "sharpe_ratio", "calmar_ratio", "max_drawdown",
    "max_drawdown_duration_days", "cagr", "n_snapshots",
)


def _empty_equity_metrics(n_snapshots: int = 0) -> Dict[str, float]:
    d: Dict[str, float] = {k: float("nan") for k in _EQUITY_METRIC_KEYS}
    d["n_snapshots"] = n_snapshots
    return d


def calculate_equity_curve_metrics(
    equity_df: pd.DataFrame, risk_free_rate: float = 0.0
) -> Dict[str, float]:
    """Rolling risk/performance statistics derived from an account equity curve.

    Args:
        equity_df: Must contain ``fetched_at`` (parseable to datetime) and
            ``total_equity`` columns — the exact shape returned by
            ``data.historical_store.HistoricalStore.account_snapshot_history()``.
            Extra columns are ignored. Multiple same-day snapshots are
            deduped to the LAST one per calendar day before computing daily
            returns (vectorized, no ``.iterrows()``).
        risk_free_rate: Annualized risk-free rate used in the Sharpe
            calculation (default 0.0).

    Returns:
        Dict with keys ``sharpe_ratio``, ``calmar_ratio``, ``max_drawdown``,
        ``max_drawdown_duration_days``, ``cagr``, ``n_snapshots``. Every
        value is ``NaN`` (never a fabricated ``0.0`` — CONSTRAINT #4) when
        ``equity_df`` is empty/malformed, has fewer than
        ``MIN_SNAPSHOTS_FOR_STATS`` distinct daily snapshots, or (for
        ``sharpe_ratio``/``calmar_ratio`` specifically) ``total_equity`` has
        zero variance — a flat curve makes those ratios undefined, not zero.
        ``max_drawdown``/``cagr`` are real zeros on a flat curve (the curve
        genuinely never dipped / genuinely had 0% growth), not undefined.
    """
    try:
        if (
            equity_df is None
            or equity_df.empty
            or "fetched_at" not in equity_df.columns
            or "total_equity" not in equity_df.columns
        ):
            return _empty_equity_metrics(0)

        df = equity_df.copy()
        df["fetched_at"] = pd.to_datetime(df["fetched_at"], errors="coerce")
        df = df.dropna(subset=["fetched_at", "total_equity"])
        if df.empty:
            return _empty_equity_metrics(0)

        # Dedupe multiple same-day snapshots to the LAST one per day
        # (vectorized — no row-by-row loop).
        df = df.sort_values("fetched_at")
        df["_day"] = df["fetched_at"].dt.normalize()
        df = df.drop_duplicates(subset="_day", keep="last").sort_values("fetched_at")
        df = df.reset_index(drop=True)

        n_snapshots = len(df)
        if n_snapshots < MIN_SNAPSHOTS_FOR_STATS:
            return _empty_equity_metrics(n_snapshots)

        equity = df["total_equity"].astype(float)

        # ── Sharpe ratio ─────────────────────────────────────────────────
        returns = equity.pct_change().dropna()
        if len(returns) < 2 or pd.isna(returns.std(ddof=1)) or returns.std(ddof=1) == 0:
            sharpe_ratio = float("nan")
        else:
            sharpe_ratio = float(
                (returns.mean() - risk_free_rate / 252.0) / returns.std(ddof=1) * math.sqrt(252)
            )

        # ── Max drawdown (negative fraction, matching processing_engine.py's
        #    rolling_max / drawdown convention) ─────────────────────────────
        rolling_max = equity.cummax()
        drawdown = (equity - rolling_max) / rolling_max
        max_drawdown = float(drawdown.min())

        # ── Max drawdown duration (calendar days), vectorized run-length
        #    encoding on the underwater mask — no Python for-loop over rows.
        underwater = drawdown < 0
        if underwater.any():
            grp = (underwater != underwater.shift()).cumsum()
            run_id = grp[underwater]
            run_spans = df.loc[underwater.values, "fetched_at"].groupby(run_id.values).agg(["min", "max"])
            run_lengths_days = (run_spans["max"] - run_spans["min"]).dt.total_seconds() / 86400.0
            max_drawdown_duration_days = float(run_lengths_days.max())
        else:
            max_drawdown_duration_days = 0.0

        # ── CAGR ─────────────────────────────────────────────────────────
        start_val = float(equity.iloc[0])
        end_val = float(equity.iloc[-1])
        days_elapsed = (df["fetched_at"].iloc[-1] - df["fetched_at"].iloc[0]).total_seconds() / 86400.0
        if days_elapsed <= 0 or start_val <= 0 or pd.isna(start_val) or pd.isna(end_val):
            cagr = float("nan")
        else:
            cagr = float((end_val / start_val) ** (365.25 / days_elapsed) - 1.0)

        # ── Calmar ratio ─────────────────────────────────────────────────
        if pd.isna(cagr) or max_drawdown == 0.0 or pd.isna(max_drawdown):
            calmar_ratio = float("nan")
        else:
            calmar_ratio = float(cagr / abs(max_drawdown))

        return {
            "sharpe_ratio": sharpe_ratio,
            "calmar_ratio": calmar_ratio,
            "max_drawdown": max_drawdown,
            "max_drawdown_duration_days": max_drawdown_duration_days,
            "cagr": cagr,
            "n_snapshots": n_snapshots,
        }

    except Exception as exc:
        telemetry.warning(f"calculate_equity_curve_metrics failed: {exc}")
        try:
            fallback_n = int(len(equity_df)) if equity_df is not None else 0
        except Exception:
            fallback_n = 0
        return _empty_equity_metrics(fallback_n)


# =============================================================================
# MODULE-LEVEL: Recommendation Tracking Report (4.1 — model vs. operator)
# =============================================================================

_DEFAULT_DECISION_LOG_PATH = Path("output/decision_log.jsonl")

# Sentinel returned when no data is available (CONSTRAINT #4 — never fabricate).
_TRACKING_EMPTY: Dict[str, Any] = {
    "rows": [],
    "model_return_30d": float("nan"),
    "operator_return_30d": float("nan"),
    "delta": float("nan"),
    "n_signals": 0,
    "n_acted": 0,
    "n_completed": 0,
    "n_with_exit": 0,
    "horizon_days": 30,
}


def _price_at_or_before(bars: pd.DataFrame, target: datetime) -> float:
    """Return the Close price at or before *target*; NaN when no bars available."""
    if bars is None or bars.empty:
        return float("nan")
    ts = pd.Timestamp(target).normalize()
    subset = bars.loc[bars.index <= ts]
    if subset.empty:
        return float("nan")
    return float(subset["Close"].iloc[-1])


def recommendation_tracking_report(
    log_path: Optional[Path] = None,
    transactions_store=None,
    horizon_days: int = 30,
    *,
    historical_store=None,
    _today=None,
) -> Dict[str, Any]:
    """Join the 1.3 decision log to 1.2 calibration data for recommendation tracking.

    For every BUY / STRONG BUY signal logged in ``output/decision_log.jsonl``:

    * **Model return** — paper-equivalent return at ``horizon_days``, conviction-
      weighted, computed from ``HistoricalStore.get_bars()`` closing prices.
    * **Actual return** — return from the linked ``TransactionsStore`` trade
      (``action_taken="acted"`` entries only).

    Insight rendered:
    "If you'd taken every BUY at the published conviction-weighted size and held
    for 30 days: model return = X%; actual closed-trade decisions returned Y%;
    judgment edge = Δ%."

    Parameters
    ----------
    log_path:
        Path to ``output/decision_log.jsonl``.  Defaults to that path.
    transactions_store:
        A ``TransactionsStore`` instance.  When supplied, "acted" entries are
        enriched with actual entry/exit prices via ``trade_id``.
    horizon_days:
        Calendar-day look-forward window for the model paper return.
    historical_store:
        Injected ``HistoricalStore`` for tests.  Real code creates one lazily.
    _today:
        Injectable ``datetime.date`` (for tests).

    Returns
    -------
    dict with keys
        rows                — ``list[dict]`` per-signal comparison
        model_return_30d    — conviction-weighted model return (completed signals)
        operator_return_30d — simple mean actual return (acted + closed trades)
        delta               — operator_return_30d − model_return_30d
        n_signals           — total BUY signals in log
        n_acted             — signals where action_taken == "acted"
        n_completed         — model signals where horizon has elapsed
        n_with_exit         — actual signals with a closed trade exit
        horizon_days        — the horizon used
    """
    from datetime import date

    if log_path is None:
        log_path = _DEFAULT_DECISION_LOG_PATH

    result: Dict[str, Any] = {**_TRACKING_EMPTY, "horizon_days": horizon_days}

    # --- Read decision log (lazy import to avoid circular dependency) -----------
    try:
        from gui.decision_log import read_decisions
        entries = read_decisions(log_path)
    except Exception as exc:
        logger.warning("recommendation_tracking_report: cannot read decision log: %s", exc)
        return result

    # Filter for BUY-type signals (covers "BUY", "STRONG BUY", etc.)
    buy_entries = [e for e in entries if "BUY" in (e.signal_action or "").upper()]
    result["n_signals"] = len(buy_entries)
    if not buy_entries:
        return result

    today = _today or date.today()

    # --- Lazy-import HistoricalStore ------------------------------------------
    if historical_store is None:
        try:
            from data.historical_store import HistoricalStore
            historical_store = HistoricalStore()
        except Exception as exc:
            logger.warning("recommendation_tracking_report: HistoricalStore unavailable: %s", exc)
            historical_store = None

    # Per-symbol bar cache (avoid redundant fetches)
    _bars_cache: Dict[str, pd.DataFrame] = {}

    def _get_bars(sym: str) -> pd.DataFrame:
        if sym not in _bars_cache:
            if historical_store is None:
                _bars_cache[sym] = pd.DataFrame()
            else:
                try:
                    # 756 days ≈ 3 years — covers signals logged up to 2 years ago
                    _bars_cache[sym] = historical_store.get_bars(sym, lookback_days=756)
                except Exception:
                    _bars_cache[sym] = pd.DataFrame()
        return _bars_cache[sym]

    # Per-trade cache keyed by (symbol_upper, trade_id)
    _trade_cache: Dict[tuple, Optional[dict]] = {}

    def _get_trade(sym: str, trade_id: int) -> Optional[dict]:
        key = (sym.upper(), int(trade_id))
        if key not in _trade_cache:
            if transactions_store is None:
                _trade_cache[key] = None
            else:
                try:
                    th = transactions_store.get_trade_history(sym.upper())
                    if th.empty or int(trade_id) not in th["trade_id"].values:
                        _trade_cache[key] = None
                    else:
                        _trade_cache[key] = th[th["trade_id"] == int(trade_id)].iloc[0].to_dict()
                except Exception:
                    _trade_cache[key] = None
        return _trade_cache[key]

    rows: List[dict] = []
    n_acted = 0
    n_completed = 0
    n_with_exit = 0
    model_weighted: List[tuple] = []   # (conviction, model_return) for completed signals
    actual_returns: List[float] = []   # actual return for acted + closed trades

    for entry in buy_entries:
        try:
            sym = entry.symbol.upper()
            conviction = entry.conviction if entry.conviction is not None else 1.0

            # Parse signal timestamp (prefer signal_ts; fall back to operator timestamp)
            raw_ts = entry.signal_ts or entry.timestamp
            signal_dt = datetime.fromisoformat(raw_ts.replace("Z", "+00:00")).replace(tzinfo=None)
            signal_date = signal_dt.date()

            exit_date = signal_date + timedelta(days=horizon_days)
            completed = exit_date <= today

            # --- Model (paper-equivalent) prices --------------------------------
            bars = _get_bars(sym)
            model_entry = _price_at_or_before(bars, signal_dt)
            model_exit = float("nan")
            model_return = float("nan")

            if completed and not math.isnan(model_entry):
                exit_dt = datetime.combine(exit_date, datetime.min.time())
                model_exit = _price_at_or_before(bars, exit_dt)
                if not math.isnan(model_exit) and model_entry > 0:
                    model_return = (model_exit - model_entry) / model_entry

            # --- Actual prices (acted entries only) ------------------------------
            actual_entry = float("nan")
            actual_exit = float("nan")
            actual_return = float("nan")
            days_held: Optional[int] = None
            trade_id = entry.trade_id

            if entry.action_taken == "acted":
                n_acted += 1
                if trade_id is not None:
                    trade = _get_trade(sym, trade_id)
                    if trade is not None:
                        ep = trade.get("entry_price")
                        xp = trade.get("exit_price")
                        entry_ts_raw = trade.get("entry_ts")
                        exit_ts_raw = trade.get("exit_ts")

                        if ep is not None and not math.isnan(float(ep)):
                            actual_entry = float(ep)

                        if xp is not None and not math.isnan(float(xp)):
                            actual_exit = float(xp)
                        elif not bars.empty:
                            # Trade still open — use latest bar close as surrogate exit
                            actual_exit = float(bars["Close"].iloc[-1])

                        if (
                            not math.isnan(actual_entry)
                            and not math.isnan(actual_exit)
                            and actual_entry > 0
                        ):
                            actual_return = (actual_exit - actual_entry) / actual_entry
                            n_with_exit += 1
                            actual_returns.append(actual_return)

                        if entry_ts_raw is not None and exit_ts_raw is not None:
                            try:
                                et = pd.to_datetime(entry_ts_raw).tz_localize(None)
                                xt = pd.to_datetime(exit_ts_raw).tz_localize(None)
                                days_held = (xt - et).days
                            except Exception:
                                pass

            if completed:
                n_completed += 1
                if not math.isnan(model_return):
                    model_weighted.append((conviction, model_return))

            rows.append({
                "symbol": sym,
                "signal_ts": raw_ts,
                "signal_action": entry.signal_action,
                "conviction": conviction,
                "action_taken": entry.action_taken,
                "model_entry_price": model_entry,
                "model_exit_price": model_exit,
                "model_return": model_return,
                "actual_entry_price": actual_entry,
                "actual_exit_price": actual_exit,
                "actual_return": actual_return,
                "days_held": days_held,
                "trade_id": trade_id,
                "completed": completed,
            })

        except Exception as exc:
            logger.debug(
                "recommendation_tracking_report: skipping entry %s: %s",
                getattr(entry, "symbol", "?"), exc,
            )

    # --- Aggregate metrics -------------------------------------------------------
    model_return_30d = float("nan")
    if model_weighted:
        total_w = sum(w for w, _ in model_weighted)
        if total_w > 0:
            model_return_30d = sum(w * r for w, r in model_weighted) / total_w

    operator_return_30d = float("nan")
    if actual_returns:
        operator_return_30d = float(np.mean(actual_returns))

    delta = float("nan")
    if not math.isnan(model_return_30d) and not math.isnan(operator_return_30d):
        delta = operator_return_30d - model_return_30d

    return {
        "rows": rows,
        "model_return_30d": model_return_30d,
        "operator_return_30d": operator_return_30d,
        "delta": delta,
        "n_signals": len(buy_entries),
        "n_acted": n_acted,
        "n_completed": n_completed,
        "n_with_exit": n_with_exit,
        "horizon_days": horizon_days,
    }


if __name__ == "__main__":
    # EXECUTABLE TEST SUITE
    test_df = pd.DataFrame({
        'Symbol': ['AAPL', 'AGNC', 'XOM'],
        'sector': ['Technology', 'Real Estate', 'Energy'],
        'Price': [148.0, 10.0, 102.0],        # Expected Arrival Price
        'Entry_Price': [150.0, 10.0, 100.0],  # Actual Fill Price
        'High': [160.0, 10.5, 105.0],
        'Low': [145.0, 9.0, 90.0],
        'position_size': [15000.0, 5000.0, 10000.0],
        'stop_loss_pct': [0.03, 0.15, 0.08],
        'VaR 95': [-0.05, -0.15, -0.08],      # Tail Risk Input
        'Beta': [1.2, 0.8, 1.1],              # Tail Risk Multiplier
        'Relative_Strength': [0.08, -0.02, 0.05]
    })

    test_benchmark = pd.DataFrame({
        'sector': ['Technology', 'Real Estate', 'Energy'],
        'weight': [0.40, 0.30, 0.30],
        'return': [0.05, 0.01, 0.03]
    })

    engine = EvaluationEngine()
    processed_df = engine.evaluate_portfolio(test_df, test_benchmark)
    
    print("\n--- EVALUATION ENGINE DIAGNOSTICS ---")
    print(processed_df[['Symbol', 'Realized Slippage', 'CoVaR Proxy', 'MAE', 'MFE', 'Portfolio_Heat', 'BF_Allocation', 'BF_Selection']])
