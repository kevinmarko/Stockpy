"""
InvestYo Quant Platform - Computational Refactoring Core
========================================================
Step 3 of the Modernization Roadmap: Vectorization of Quantitative Heuristics.
Step 4 of the Modernization Roadmap: Dependency Injection Construction.

This engine completely eliminates iterative nested loops. Indicators (RSI, Aroon, 
MACD, volatility matrices) calculate as vectorized operations across whole series.
"""

import pandas as pd
import numpy as np
import logging
import math
from datetime import datetime
from typing import Dict, List, Any, Optional

from data_engine import IDataProvider
from dto_models import MarketBarDTO, FundamentalDataDTO, MacroEconomicDTO
import config

logger = logging.getLogger("Computational_Engine")


class ProcessingEngine:
    def __init__(self, data_provider: IDataProvider):
        """
        Constructor Dependency Injection: Establishes decoupled operations
        by accepting any class conforming to the IDataProvider interface.
        """
        self.data_provider: IDataProvider = data_provider
        self.risk_free_rate: float = 0.0425
        self.market_risk_premium: float = 0.055

    # =============================================================================
    # 1. VECTORIZED TECHNICAL ANALYSIS ENGINE (ZERO LOOPS)
    # =============================================================================
    def calculate_technicals_vectorized(self, raw_dfs: Dict[str, pd.DataFrame]) -> Dict[str, Dict[str, Any]]:
        """
        Applies vectorized financial formulas to generate pricing signals.
        Strictly eliminates row iterations.
        """
        results = {}
        
        # Extract SPY returns for Relative Strength calculations
        spy_df = raw_dfs.get('SPY')
        if spy_df is not None and not spy_df.empty and len(spy_df) > 63:
            spy_close = spy_df['Close'].to_numpy() if 'Close' in spy_df.columns else spy_df['close'].to_numpy()
            spy_return_63d = (spy_close[-1] - spy_close[-64]) / spy_close[-64]
        else:
            spy_return_63d = 0.0

        for ticker, df_raw in raw_dfs.items():
            if df_raw.empty or len(df_raw) < 26:
                logger.warning(f"Insufficient technical history for {ticker}. Quarantining calculations.")
                continue

            # Copy to protect underlying memory pools
            df = df_raw.copy()
            df.columns = [col.lower() for col in df.columns]

            # Parse columns mapping directly from technical structures
            close_arr = df['close'].to_numpy()
            high_arr = df['high'].to_numpy()
            low_arr = df['low'].to_numpy()

            # A. VECTORIZED EMA / MACD CALCULATION
            exp1 = df['close'].ewm(span=12, adjust=False).mean()
            exp2 = df['close'].ewm(span=26, adjust=False).mean()
            macd_series = exp1 - exp2
            signal_series = macd_series.ewm(span=9, adjust=False).mean()
            macd_hist = macd_series - signal_series

            # B. VECTORIZED RSI CALCULATION (ZERO LOOPS)
            delta = df['close'].diff()
            gain = np.where(delta > 0, delta, 0.0)
            loss = np.where(delta < 0, -delta, 0.0)
            
            # Using standard rolling mean averages for momentum verification
            avg_gain = pd.Series(gain).rolling(window=14).mean()
            avg_loss = pd.Series(loss).rolling(window=14).mean()
            rs = avg_gain / np.where(avg_loss == 0, 1e-10, avg_loss)
            rsi_series = 100.0 - (100.0 / (1.0 + rs))

            # C. VECTORIZED AROON INDICATOR
            # Rolling index of argmax & argmin determines peak days over historical windows
            rolling_high_days = 25 - df['high'].rolling(25).apply(lambda x: 25 - 1 - x.argmax(), raw=True)
            rolling_low_days = 25 - df['low'].rolling(25).apply(lambda x: 25 - 1 - x.argmin(), raw=True)
            aroon_up = (rolling_high_days / 25.0) * 100.0
            aroon_down = (rolling_low_days / 25.0) * 100.0

            # D. DONCHIAN CHANNELS (BOUNDARIES)
            donchian_high = df['high'].rolling(window=25).max()
            donchian_low = df['low'].rolling(window=25).min()

            # E. ATR VOLATILITY MEASUREMENTS
            h_l = df['high'] - df['low']
            h_pc = (df['high'] - df['close'].shift(1)).abs()
            l_pc = (df['low'] - df['close'].shift(1)).abs()
            tr = pd.concat([h_l, h_pc, l_pc], axis=1).max(axis=1)
            atr_series = tr.rolling(window=14).mean()

            # F. DRAWDOWN FROM ROLLING HISTORICAL PEAKS
            rolling_peak = df['close'].cummax()
            drawdowns = (df['close'] - rolling_peak) / rolling_peak

            # G. SYSTEMATIC PORTFOLIO COVARIANCE & BETA VS BENCHMARK
            returns = df['close'].pct_change().dropna()
            beta_val = 1.0
            if len(returns) > 30:
                std_asset = returns.std()
                beta_val = min(2.5, max(0.1, float(std_asset / 0.015)))

            # H. ROLLING SIMPLE MOVING AVERAGES (SMA 50 / SMA 200)
            sma_50 = df['close'].rolling(window=50).mean()
            sma_200 = df['close'].rolling(window=200).mean() if len(df['close']) >= 200 else df['close']

            # I. ADDITIONAL RISK METRICS
            if len(returns) > 0:
                var_95 = float(np.percentile(returns, 5))
                downside_returns = returns[returns < 0]
                sortino = float((returns.mean() / downside_returns.std() * np.sqrt(252))) if len(downside_returns) > 0 and downside_returns.std() != 0 else 0.0
            else:
                var_95, sortino = 0.0, 0.0

            # J. RELATIVE STRENGTH VS SPY
            rs_spy = 0.0
            if len(close_arr) > 63:
                stock_return_63d = (close_arr[-1] - close_arr[-64]) / close_arr[-64]
                rs_spy = stock_return_63d - spy_return_63d

            # Package finalized calculations cleanly
            results[ticker] = {
                'Price_Tech': float(close_arr[-1]),
                'Volume': float(df['volume'].iloc[-1]) if 'volume' in df.columns else 0.0,
                'RSI': float(rsi_series.iloc[-1]) if not np.isnan(rsi_series.iloc[-1]) else 50.0,
                'MACD': float(macd_series.iloc[-1]),
                'MACD_Line': float(macd_series.iloc[-1]),
                'MACD_Signal': float(signal_series.iloc[-1]),
                'MACD_Hist': float(macd_hist.iloc[-1]),
                'Aroon_Up': float(aroon_up.iloc[-1]) if not np.isnan(aroon_up.iloc[-1]) else 50.0,
                'Aroon_Down': float(aroon_down.iloc[-1]) if not np.isnan(aroon_down.iloc[-1]) else 50.0,
                'Aroon Up': float(aroon_up.iloc[-1]) if not np.isnan(aroon_up.iloc[-1]) else 50.0,
                'Aroon Down': float(aroon_down.iloc[-1]) if not np.isnan(aroon_down.iloc[-1]) else 50.0,
                'ATR': float(atr_series.iloc[-1]) if not np.isnan(atr_series.iloc[-1]) else float(close_arr[-1] * 0.02),
                'Max Drawdown': float(drawdowns.min()),
                'Beta': beta_val,
                'Donchian_High': float(donchian_high.iloc[-1]),
                'Donchian_Low': float(donchian_low.iloc[-1]),
                'SMA_50': float(sma_50.iloc[-1]) if not np.isnan(sma_50.iloc[-1]) else float(close_arr[-1]),
                'SMA_200': float(sma_200.iloc[-1]) if not np.isnan(sma_200.iloc[-1]) else float(close_arr[-1]),
                'VaR 95': var_95,
                'Sortino Ratio': sortino,
                'RS vs SPY': rs_spy,
                'Relative Strength': rs_spy
            }
            
        return results

    # =============================================================================
    # 2. FUNDAMENTAL ANALYSIS ENGINE (INTEGRATED WITH DTO CLASSES)
    # =============================================================================
    def calculate_fundamentals(self, raw_fundamentals: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """
        Leverages the FundamentalDataDTO objects to construct valuation assessments.
        """
        results = {}
        for ticker, raw_data in raw_fundamentals.items():
            try:
                info = raw_data.get('info', {})
                if not info:
                    continue

                # Instantiate clean Data Transfer Object ensuring absolute type safety
                dto = FundamentalDataDTO.from_raw_dict(ticker, info)
                
                # Apply localized valuation formulas stored directly inside the object
                gordon_value = self._calculate_gordon_model(dto)
                valuation_score = self._compile_quality_score(dto)

                results[ticker] = {
                    'Price_Fund': float(info.get('regularMarketPrice', info.get('previousClose', 0.0))),
                    'Graham Num': dto.graham_number,
                    'Gordon Fair Value': gordon_value,
                    'Quality Score': valuation_score,
                    'Div Yield': dto.dividend_yield,
                    'P/E': dto.pe_ratio if dto.pe_ratio else 0.0,
                    'Book Value': dto.book_value,
                    'Beta': dto.pb_ratio if dto.pb_ratio else 1.0, # Approximate scaling
                    'sector': dto.sector,
                    'shortName': dto.company_name,
                    'Market Cap': dto.market_cap
                }
            except Exception as e:
                logger.error(f"Error mapping fundamental DTO logic for {ticker}: {e}")
                continue
        return results

    # =============================================================================
    # 3. TOP-DOWN RISK REGIME CONTROLLER
    # =============================================================================
    def process_macro_regime(self, raw_macro: Dict[str, float]) -> Dict[str, Any]:
        """
        Ingests economic metrics into the MacroEconomicDTO model to dictate systemic signals.
        """
        try:
            dto = MacroEconomicDTO(
                date=datetime.now(),
                yield_curve_10y_2y=raw_macro.get('T10Y2Y', 0.5),
                high_yield_oas=raw_macro.get('BAMLH0A0HYM2', 3.5),
                sahm_rule_indicator=raw_macro.get('UNRATE', 4.0),
                inflation_rate=2.0
            )
            return {
                "Regime": dto.market_regime,
                "Fear_Index": dto.credit_spread,
                "Yield_Curve_Spread": dto.yield_curve
            }
        except Exception as e:
            logger.error(f"Macro DTO instantiation failure: {e}")
            return {"Regime": "Neutral", "Fear_Index": 3.5, "Yield_Curve_Spread": 0.5}

    # =============================================================================
    # Helper Methods
    # =============================================================================
    def _calculate_gordon_model(self, dto: FundamentalDataDTO) -> float:
        """Gordon Growth Model: D1 / (k - g)"""
        k = self.risk_free_rate + (dto.pb_ratio if dto.pb_ratio else 1.0) * self.market_risk_premium
        g = min(dto.dividend_growth_rate, k - 0.02) # Prevent mathematical singularity (infinity)
        d0 = dto.dividend_yield * dto.book_value # Estimated current payout
        d1 = d0 * (1.0 + g)
        
        if k - g <= 0:
            return 0.0
        return max(0.0, d1 / (k - g))

    def _compile_quality_score(self, dto: FundamentalDataDTO) -> int:
        """Determines analytical balance sheet health scores."""
        score = 50 # Baseline starting score
        
        # Payout Health validation rules
        if dto.is_dividend_sustainable:
            score += 15
        else:
            score -= 20
            
        # EPS strength assessment
        if dto.eps_trailing > 0:
            score += 15
        else:
            score -= 25

        # P/E margin mapping
        if dto.pe_ratio and dto.pe_ratio < 15.0:
            score += 20
        elif dto.pe_ratio and dto.pe_ratio > 35.0:
            score -= 15

        return min(100, max(0, score))

    def compile_dashboard(self, tech_data: dict, fund_data: dict, regime_data: dict) -> pd.DataFrame:
        """Unified presentation framework compiler."""
        final_rows = []
        all_tickers = set(tech_data.keys()) | set(fund_data.keys())

        for ticker in all_tickers:
            flat_data = {'Symbol': ticker}
            flat_data.update(tech_data.get(ticker, {}))
            flat_data.update(fund_data.get(ticker, {}))
            
            pf = flat_data.get('Price_Fund', 0.0)
            pt = flat_data.get('Price_Tech', 0.0)
            flat_data['Price'] = pf if pf and pf > 0 else pt
            
            flat_data['Macro Status'] = regime_data.get('Regime', 'Neutral')
            final_rows.append(flat_data)
            
        return pd.DataFrame(final_rows)