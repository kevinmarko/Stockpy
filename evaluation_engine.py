# =============================================================================
# MODULE: EVALUATION ENGINE
# File: evaluation_engine.py
# Description: Implements post-trade evaluation (MFE/MAE/Edge Ratio), 
#              Kelly Criterion position sizing, and Brinson-Fachler sector attribution.
# =============================================================================

import json
import logging
import numpy as np
import pandas as pd
from typing import Dict, Any, Optional
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
        benchmark_df: pd.DataFrame = pd.DataFrame(),
        data_provider = None
    ) -> pd.DataFrame:
        """
        Main execution method mapping MAE, MFE, Portfolio Heat, and Brinson-Fachler 
        metrics identically to internal DTO keys requested by config.py.
        Uses transactions_store to pull actual entry prices/timestamps and fetches
        actual historical OHLC of the hold period from data_provider.
        """
        logger.info("Running post-trade execution analytics...")
        from transactions_store import TransactionsStore
        
        df = df.copy()
        
        # Ensure target columns exist in the DataFrame
        for col in ['Entry_Price', 'MAE', 'MFE', 'Edge Ratio', 'Realized Slippage']:
            if col not in df.columns:
                df[col] = np.nan
        
        store = TransactionsStore()
        
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
                            raw_tech = data_provider.fetch_technical_raw([symbol])
                            history_df = raw_tech.get(symbol) if raw_tech else None
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
            port_sector_weights = df.groupby('sector')['position_size'].sum() / df['position_size'].sum()
            port_sector_returns = df.groupby('sector')['Relative_Strength'].mean() 

            bench_weights = benchmark_df.set_index('sector')['weight']
            bench_returns = benchmark_df.set_index('sector')['return']

            bf_df = self.calculate_brinson_fachler(port_sector_weights, bench_weights, port_sector_returns, bench_returns)

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
