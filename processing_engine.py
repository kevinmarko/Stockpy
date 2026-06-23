# =============================================================================
# MODULE: COMPUTATIONAL CORE
# File: processing_engine.py
# Description: Handles Technical Analysis, Fundamental Valuations, and Risk.
#              Merges disparate data sources into a unified Schema-compliant DataFrame.
# =============================================================================

import pandas as pd
import numpy as np
try:
    import pandas_ta as ta  # type: ignore
except ImportError:
    import pandas_ta_classic as ta

import logging
import math
from datetime import datetime

from dto_models import FundamentalDataDTO, MacroEconomicDTO
from research_engine import AdvancedResearchEngine

# --- CONFIGURATION IMPORT ---
from config import COLUMN_SCHEMA, get_internal_keys

class ProcessingEngine:
    
    def __init__(self, data_provider=None):
        """
        Initializes the Processing Engine with default risk models.
        """
        self.data_provider = data_provider
        self.risk_free_rate = 0.0425 
        self.market_risk_premium = 0.055
        self.required_return_rate = 0.08
        self.research_engine = AdvancedResearchEngine(risk_free_rate=self.risk_free_rate)

    def calculate_graham_number(self, eps: float, book_value: float) -> float:
        """
        Calculates the Ben Graham Number.
        Formula: sqrt(15 * 1.5 * EPS * BVPS) = sqrt(22.5 * EPS * BVPS)
        """
        try:
            if eps is None or book_value is None or pd.isna(eps) or pd.isna(book_value):
                return 0.0
            if eps <= 0 or book_value <= 0:
                return 0.0
            return round(math.sqrt(22.5 * eps * book_value), 2)
        except Exception as e:
            logging.error(f"Graham Number error: {e}")
            return 0.0

    def calculate_gordon_fair_value(self, current_price: float, dividend_yield: float, div_growth_rate: float) -> float:
        """
        Calculates the Gordon Growth Model (Dividend Discount Model) Fair Value.
        Formula: D1 / (r - g)
        """
        try:
            if dividend_yield is None or pd.isna(dividend_yield) or dividend_yield <= 0:
                return 0.0
            if div_growth_rate is None or pd.isna(div_growth_rate):
                div_growth_rate = 0.0

            annual_dividend = current_price * dividend_yield
            expected_dividend_next_year = annual_dividend * (1 + div_growth_rate)

            # Cap the growth rate to prevent infinite or negative valuations (g must be < r)
            g = min(div_growth_rate, self.required_return_rate - 0.01)

            if self.required_return_rate <= g:
                return 0.0 

            gordon_value = expected_dividend_next_year / (self.required_return_rate - g)
            return round(gordon_value, 2)
        except Exception as e:
            logging.error(f"Gordon Fair Value error: {e}")
            return 0.0


    # ==========================================================================
    # 1. MACRO LOGIC
    # ==========================================================================
    def process_macro_regime(self, macro_dto):
        try:
            if isinstance(macro_dto, dict):
                from dto_models import MacroEconomicDTO
                macro_dto = MacroEconomicDTO(
                    yield_curve_10y_2y=macro_dto.get('T10Y2Y', 0.5),
                    high_yield_oas=macro_dto.get('BAMLH0A0HYM2', 3.5),
                    inflation_rate=macro_dto.get('CPIAUCSL_YoY', 2.0)
                )
            self.research_engine.real_yield = macro_dto.real_yield
            return {
                "Regime": macro_dto.market_regime,
                "Real_Yield": macro_dto.real_yield,
                "Inflation": macro_dto.inflation
            }
        except Exception as e:
            logging.error(f"Macro Processing Error: {e}")
            return {"Regime": "Neutral", "Real_Yield": 0.0}


    # ==========================================================================
    # 2. TECHNICAL ANALYSIS & RISK METRICS (UPDATED)
    # ==========================================================================
    def calculate_technical_metrics(self, raw_tech_data, transactions_df=None):
        """
        Calculates RSI, MACD, ATR, SMA AND Risk Metrics (VaR, Sortino, DD).
        """
        results = {}
        
        # Pre-calculate SPY return for relative strength comparison
        spy_df = raw_tech_data.get('SPY')
        if spy_df is not None and not spy_df.empty:
            spy_df = spy_df.sort_index()
            spy_return = (spy_df['Close'].iloc[-1] - spy_df['Close'].iloc[0]) / spy_df['Close'].iloc[0]
        else:
            spy_return = 0.0

        # Calculate realized slippage (Topic 28)
        # EXPLANATION: The research engine returns a float directly for the slippage metric now.
        avg_slippage = self.research_engine.calculate_realized_slippage(
            transactions_df if transactions_df is not None else pd.DataFrame()
        )

        # Calculate portfolio tail-dependency covariance risk (Topic 30)
        returns_dict = {}
        for t, d in raw_tech_data.items():
            if not d.empty and len(d) >= 2:
                returns_dict[t] = d['Close'].pct_change()
        returns_df = pd.DataFrame(returns_dict)
        # EXPLANATION: The research engine returns a float directly for tail dependency now.
        max_corr = self.research_engine.calculate_portfolio_covar_dependency(returns_df)
            
        for ticker, df in raw_tech_data.items():
            try:
                if df.empty or len(df) < 30: continue
                    
                df = df.sort_index()
                
                # --- A. STANDARD INDICATORS ---
                df['RSI'] = ta.rsi(df['Close'], length=14)
                macd = ta.macd(df['Close'], fast=12, slow=26, signal=9)
                if macd is not None:
                    df['MACD_Line'] = macd['MACD_12_26_9']
                    df['MACD_Signal'] = macd['MACDs_12_26_9']
                
                df['ATR'] = ta.atr(df['High'], df['Low'], df['Close'], length=14)
                df['SMA_50'] = ta.sma(df['Close'], length=50)
                df['SMA_200'] = ta.sma(df['Close'], length=200)
                
                # --- B. RISK METRICS (NEW) ---
                # Calculate Returns
                df['Pct_Change'] = df['Close'].pct_change()
                
                # 1. VaR 95 (Historical Method, 5th percentile of daily returns)
                # We assume 1-day VaR. For annual, multiply by sqrt(252)
                var_95 = df['Pct_Change'].quantile(0.05)
                
                # 2. Max Drawdown (Rolling Peak)
                rolling_max = df['Close'].cummax()
                drawdown = (df['Close'] - rolling_max) / rolling_max
                max_drawdown = drawdown.min()
                
                # 3. Sortino Ratio (Annualized)
                # Mean Return / Downside Deviation
                avg_return = df['Pct_Change'].mean()
                downside_returns = df.loc[df['Pct_Change'] < 0, 'Pct_Change']
                downside_std = downside_returns.std()
                
                sortino = 0.0
                if downside_std > 0:
                    sortino = (avg_return * 252) / (downside_std * np.sqrt(252))

                # --- C. AROON INDICATOR & OSCILLATOR ---
                aroon = ta.aroon(df['High'], df['Low'], length=25)
                if aroon is not None and not aroon.empty:
                    df['Aroon_Up'] = aroon.get('AROONU_25', 50.0)
                    df['Aroon_Down'] = aroon.get('AROOND_25', 50.0)
                    df['Aroon_Oscillator'] = aroon.get('AROONOSC_25', 0.0)
                else:
                    df['Aroon_Up'] = 50.0
                    df['Aroon_Down'] = 50.0
                    df['Aroon_Oscillator'] = 0.0

                # --- NEW: COPPOCK CURVE ---
                coppock = ta.coppock(df['Close'])
                if coppock is not None and not coppock.dropna().empty:
                    df['Coppock_Curve'] = coppock
                else:
                    df['Coppock_Curve'] = 0.0

                # --- NEW: CHANDELIER EXIT ---
                if 'ATR' in df.columns and not df['ATR'].isna().all():
                    rolling_max_high = df['High'].rolling(window=22).max()
                    df['Chandelier_Exit'] = rolling_max_high - 3.0 * df['ATR']
                else:
                    df['Chandelier_Exit'] = df['Close']

                # --- D. RELATIVE STRENGTH vs SPY (NEW) ---
                if len(df) >= 2:
                    stock_return = (df['Close'].iloc[-1] - df['Close'].iloc[0]) / df['Close'].iloc[0]
                    rs_vs_spy = stock_return - spy_return
                else:
                    rs_vs_spy = 0.0

                # --- E. RS MOMENTUM SLOPE & OPTIONS IV EDGE (NEW) ---
                spy_df = raw_tech_data.get('SPY')
                if spy_df is not None and not spy_df.empty:
                    rs_slope = self.research_engine.calculate_relative_strength_momentum_slope(df['Close'], spy_df['Close'])
                else:
                    rs_slope = 0.0

                pct_change = df['Close'].pct_change()
                hv = pct_change.std() * np.sqrt(252) if not pct_change.isna().all() else 0.0
                
                # Extract Latest
                last_row = df.iloc[-1]
                atr = last_row.get('ATR', 0.0)
                price = last_row['Close']
                iv_edge = self.research_engine.calculate_options_volatility_edge(hv, atr, price)
                
                results[ticker] = {
                    'Price_Tech': last_row['Close'],
                    'Volume': last_row.get('Volume', 0),
                    'RSI': last_row.get('RSI', 50),
                    'MACD_Line': last_row.get('MACD_Line', 0),
                    'MACD_Signal': last_row.get('MACD_Signal', 0),
                    'ATR': last_row.get('ATR', 0),
                    'SMA_50': last_row.get('SMA_50', 0),
                    'SMA_200': last_row.get('SMA_200', 0),
                    
                    # Risk Keys mapped to Schema
                    'VaR 95': var_95,
                    'Max Drawdown': max_drawdown,
                    'Sortino Ratio': sortino,

                    # Aroon & Relative Strength Keys mapped to Schema
                    'Aroon Up': float(last_row.get('Aroon_Up', 50.0)) if pd.notna(last_row.get('Aroon_Up')) else 50.0,
                    'Aroon Down': float(last_row.get('Aroon_Down', 50.0)) if pd.notna(last_row.get('Aroon_Down')) else 50.0,
                    'Aroon Oscillator': float(last_row.get('Aroon_Oscillator', 0.0)) if pd.notna(last_row.get('Aroon_Oscillator')) else 0.0,
                    'Coppock Curve': float(last_row.get('Coppock_Curve', 0.0)) if pd.notna(last_row.get('Coppock_Curve')) else 0.0,
                    'Chandelier Exit': float(last_row.get('Chandelier_Exit', last_row['Close'])) if pd.notna(last_row.get('Chandelier_Exit')) else last_row['Close'],
                    'RS vs SPY': rs_vs_spy,

                    # Advanced Research Keys mapped to Schema
                    'RS-MACD': rs_slope,
                    'Realized Slippage': avg_slippage,
                    'Options IV Edge': iv_edge,
                    'CoVaR Proxy': max_corr,
                }
                
            except Exception as e:
                logging.warning(f"Technical Calc Failed for {ticker}: {e}")
                continue
                
        return results

    def calculate_technicals_vectorized(self, raw_tech_data, transactions_df=None):
        # EXPLANATION: Implements Step 3 of instructions.
        # Calculates MACD aligned variables and computes the Options IV Edge from historical vol and ATR.
        res = self.calculate_technical_metrics(raw_tech_data, transactions_df)
        for ticker, metrics in res.items():
            metrics['MACD'] = metrics.get('MACD_Line', 0.0)
            df = raw_tech_data.get(ticker)
            if df is not None and not df.empty and len(df) >= 14:
                try:
                    returns = df['Close'].pct_change().dropna()
                    atr_series = df['ATR'] if 'ATR' in df.columns else ta.atr(df['High'], df['Low'], df['Close'], length=14)
                    close_arr = df['Close'].to_numpy()
                    
                    options_edge = self.research_engine.calculate_options_volatility_edge(
                        historical_vol=float(returns.std() * math.sqrt(252)), 
                        atr=float(atr_series.iloc[-1]), 
                        price=float(close_arr[-1])
                    )
                    metrics['Options IV Edge'] = options_edge
                except Exception as e:
                    logging.warning(f"Vectorized Options IV Edge calculation failed for {ticker}: {e}")
        return res

    # ==========================================================================
    # ==========================================================================
    # 3. FUNDAMENTAL ANALYSIS
    # ==========================================================================
    def calculate_fundamental_metrics(self, fund_dtos):
        results = {}
        for ticker, dto in fund_dtos.items():
            if not dto:
                continue
            try:
                # EXPLANATION: Safe extraction of raw yfinance info fields stored on the DTO.
                info = getattr(dto, 'raw_info', {}) or {}
                price = float(
                    info.get('regularMarketPrice')
                    or info.get('previousClose')
                    or dto.price
                    or 0.0
                )

                # F-01 FIX: Dead inline Gordon block removed.
                # The inline block incorrectly assigned dto.dividend_growth_rate (e.g. 0.04)
                # as a dollar dividend amount, producing nonsensical sub-$1 valuations.
                # calculate_gordon_fair_value() is the single authoritative source.
                gordon_val = self.calculate_gordon_fair_value(
                    price, dto.dividend_yield, dto.dividend_growth_rate
                )

                debt_to_equity_raw = info.get('debtToEquity')
                debt_to_equity = (float(debt_to_equity_raw) / 100.0
                                  if debt_to_equity_raw else None)

                inst_own = info.get('heldPercentInstitutions')
                inst_change = (
                    info.get('netPercentInsiderShares')
                    or info.get('netPercentInstitutionsSharesOut')
                )
                if not inst_change or inst_change == 0.0:
                    # EXPLANATION: Fallback to monthly change in short interest as a proxy
                    # for institutional velocity, since Yahoo Finance has removed direct
                    # institutional transaction keys from the info dictionary.
                    short_prior = float(info.get('sharesShortPriorMonth', 0.0) or 0.0)
                    short_curr  = float(info.get('sharesShort', 0.0) or 0.0)
                    shares_out  = float(info.get('sharesOutstanding', 1.0) or 1.0)
                    inst_change = (short_prior - short_curr) / shares_out if shares_out > 0 else 0.0

                # 1. Calculate Sector-Adjusted Graham
                adj_graham = self.research_engine.calculate_sector_adjusted_valuation(
                    sector=dto.sector, pe=dto.pe_ratio or 0, pb=dto.pb_ratio or 0,
                    book_value=dto.book_value, eps=dto.eps_trailing, price=price
                )

                # 2. Apply Real Yield Drag to the sector-adjusted Graham value
                yield_dragged_val = self.research_engine.calculate_real_yield_drag(adj_graham)

                # 3-6. Calculate the remaining fundamental metrics
                dps         = self.research_engine.calculate_dividend_premium_spread(dto.dividend_yield)
                inst_vel    = self.research_engine.calculate_institutional_velocity(inst_own, inst_change)
                dph         = self.research_engine.calculate_dividend_payback_horizon(
                                  price, dto.dividend_yield, dto.dividend_growth_rate
                              )
                lev_distress = self.research_engine.calculate_leverage_distress_factor(
                                  dto.sector, debt_to_equity
                               )

                # Quality Score
                score = 50
                if yield_dragged_val > price:  score += 10
                if dto.dividend_yield > 0.03:  score += 10
                if dto.beta < 1.0:             score += 5

                results[ticker] = {
                    'Symbol':                   ticker,
                    'shortName':                dto.company_name,
                    'Market Cap':               dto.market_cap,
                    'sector':                   dto.sector,
                    'Price_Fund':               price,
                    'Graham Num':               yield_dragged_val,
                    'Gordon Fair Value':         gordon_val,   # single authoritative source
                    'Quality Score':            score,
                    'Div Yield':                dto.dividend_yield,
                    'P/E':                      dto.pe_ratio if dto.pe_ratio is not None else 0.0,
                    'Book Value':               dto.book_value,
                    'Beta':                     dto.beta,
                    'DPS':                      dps,
                    'Institutional Velocity':   inst_vel,
                    'DPH':                      dph,
                    'Leverage Distress Factor': lev_distress,
                }
            except Exception as e:
                logging.error(f"Error calculating fundamentals for {ticker}: {e}")
                continue
        return results


    # ==========================================================================
    # 4. DASHBOARD COMPILATION
    # ==========================================================================
    def compile_dashboard(self, tech_data, fund_data, regime_data):
        final_rows = []
        all_tickers = set(tech_data.keys()) | set(fund_data.keys())
        
        for ticker in all_tickers:
            flat_data = {'Symbol': ticker}
            flat_data.update(tech_data.get(ticker, {}))
            flat_data.update(fund_data.get(ticker, {}))
            
            pf = flat_data.get('Price_Fund', 0)
            pt = flat_data.get('Price_Tech', 0)
            flat_data['Price'] = pf if pf and pf > 0 else pt
            
            flat_data['Macro Status'] = regime_data.get('Regime', 'Neutral')
            final_rows.append(flat_data)
            
        return pd.DataFrame(final_rows)
