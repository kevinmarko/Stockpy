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
from typing import Dict, Optional

from dto_models import FundamentalDataDTO, MacroEconomicDTO
from research_engine import AdvancedResearchEngine

# --- CONFIGURATION IMPORT ---
from config import COLUMN_SCHEMA, get_internal_keys
from settings import settings

class ProcessingEngine:
    
    def __init__(self, data_provider=None):
        """
        Initializes the Processing Engine with default risk models.
        """
        self.data_provider = data_provider
        self.risk_free_rate = settings.RISK_FREE_RATE
        self.market_risk_premium = settings.MARKET_RISK_PREMIUM
        self.required_return_rate = settings.REQUIRED_RETURN_RATE
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
        Formula: D1 / (r - g)  where D1 = D0 * (1 + g) and g is the capped growth rate.
        """
        try:
            if dividend_yield is None or pd.isna(dividend_yield) or dividend_yield <= 0:
                return 0.0
            if div_growth_rate is None or pd.isna(div_growth_rate):
                div_growth_rate = 0.0

            # BUG-FIX: Cap the growth rate BEFORE using it anywhere.
            # Previously g was capped for the denominator but div_growth_rate
            # (uncapped) was used for D1 in the numerator — an asymmetry that
            # inflated the Gordon value when g > r-0.01. Both D1 and the
            # denominator must use the same capped g.
            g = min(div_growth_rate, self.required_return_rate - 0.01)

            if self.required_return_rate <= g:
                return 0.0

            annual_dividend = current_price * dividend_yield
            expected_dividend_next_year = annual_dividend * (1 + g)

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
                "Inflation": macro_dto.inflation,
                # HMM second opinion (regime/hmm_regime.py); None when unavailable,
                # never fabricated -- compile_dashboard() below maps None to NaN.
                "HMM_Risk_On_Probability": getattr(macro_dto, "hmm_risk_on_probability", None),
            }
        except Exception as e:
            logging.error(f"Macro Processing Error: {e}")
            return {"Regime": "Neutral", "Real_Yield": 0.0, "HMM_Risk_On_Probability": None}


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
            # No SPY benchmark history available -> relative strength is undefined.
            # NaN (never a fabricated 0.0) so rs_vs_spy below becomes NaN rather than
            # misreporting the stock's raw return as relative outperformance.
            spy_return = float('nan')

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
                # Connors RSI(2): short-lookback RSI used for mean-reversion entries
                # (signals/rsi2_mean_reversion.py). Causal — ta.rsi(length=2) at row t
                # only consumes Close[<=t].
                df['RSI_2'] = ta.rsi(df['Close'], length=2)
                macd = ta.macd(df['Close'], fast=12, slow=26, signal=9)
                if macd is not None:
                    df['MACD_Line'] = macd['MACD_12_26_9']
                    df['MACD_Signal'] = macd['MACDs_12_26_9']

                df['ATR'] = ta.atr(df['High'], df['Low'], df['Close'], length=14)
                df['SMA_5'] = ta.sma(df['Close'], length=5)
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
                
                # NaN (never a fabricated 0.0) when downside deviation is zero or
                # undefined -- an honest "insufficient/zero downside" reading.
                # signals/sortino_drawdown.py treats NaN as "abstain".
                sortino = float('nan')
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

                # Compute time-series momentum metrics
                df = self.calculate_momentum_metrics(df)

                # Reuse the already-computed df['Pct_Change'] (== df['Close'].pct_change()
                # from the RISK METRICS block above) instead of recomputing it.
                # NaN (never a fabricated 0% vol) when no valid returns exist.
                hv = df['Pct_Change'].std() * np.sqrt(252) if not df['Pct_Change'].isna().all() else float('nan')
                
                # Extract Latest
                last_row = df.iloc[-1]
                atr = last_row.get('ATR', 0.0)
                price = last_row['Close']
                iv_edge = self.research_engine.calculate_options_volatility_edge(hv, atr, price)
                
                results[ticker] = {
                    'Price_Tech': last_row['Close'],
                    'Volume': last_row.get('Volume', 0),
                    'RSI': last_row.get('RSI', 50),
                    'RSI_2': float(last_row.get('RSI_2', 50.0)) if pd.notna(last_row.get('RSI_2')) else 50.0,
                    'MACD_Line': last_row.get('MACD_Line', 0),
                    'MACD_Signal': last_row.get('MACD_Signal', 0),
                    'ATR': last_row.get('ATR', 0),
                    'SMA_5': float(last_row.get('SMA_5', 0.0)) if pd.notna(last_row.get('SMA_5')) else last_row['Close'],
                    'SMA_50': last_row.get('SMA_50', 0),
                    'SMA_200': last_row.get('SMA_200', 0),
                    'ROC_12M': float(last_row.get('ROC_12M', 0.0)) if pd.notna(last_row.get('ROC_12M')) else 0.0,
                    'ROC_6M': float(last_row.get('ROC_6M', 0.0)) if pd.notna(last_row.get('ROC_6M')) else 0.0,
                    'Momentum_Vol_Scaled': float(last_row.get('Momentum_Vol_Scaled', 0.0)) if pd.notna(last_row.get('Momentum_Vol_Scaled')) else 0.0,
                    # NaN (not 0.0) when <60 valid daily returns are available -- this
                    # feeds signals/multifactor.py's low-volatility factor input and
                    # must never be fabricated as a fake "low vol" reading.
                    'Realized_Vol_60D': float(last_row['Realized_Vol_60D']) if pd.notna(last_row.get('Realized_Vol_60D')) else float('nan'),
                    
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
    def calculate_fundamental_metrics(
        self,
        fund_dtos,
        realized_vol_60d_map: Optional[Dict[str, float]] = None,
    ):
        """
        Parameters
        ----------
        fund_dtos : dict[str, FundamentalDataDTO]
        realized_vol_60d_map : dict[str, float], optional
            Per-ticker 60-day annualized realized volatility, sourced from
            calculate_technical_metrics()'s 'Realized_Vol_60D' (itself from
            calculate_momentum_metrics() -- lookahead-free, shift(1)-based).
            Required to compute the low-volatility factor input
            (low_vol_score); absent/missing tickers get NaN, never a
            fabricated default.

        Notes (Tier 2.3 Phase 3)
        -------------------------
        When ``settings.HISTORICAL_STORE_ENABLED`` is True, this method routes
        each ticker's raw fundamentals dict through
        ``HistoricalStore.get_fundamentals(ticker)``, which caches the result
        in ``fundamentals_history`` (daily refresh) and provides it from the DB
        on subsequent calls without hitting the live provider.

        The HistoricalStore lookup uses the SAME raw dict keys that
        ``FundamentalDataDTO.from_raw_dict`` already consumes, so no downstream
        key-mapping changes are required.  The ``provider`` argument is seeded
        from the DTO's original provider so the fallback path is identical to
        pre-Phase-3 behavior.
        """
        realized_vol_60d_map = realized_vol_60d_map or {}

        # ── Phase 3: HistoricalStore fundamentals routing ────────────────────
        # When enabled, persist each ticker's raw fundamentals dict and serve
        # subsequent requests from the DB cache (daily TTL).  The typed dict
        # returned by get_fundamentals uses the same yfinance-style keys as the
        # existing FundamentalDataDTO pipeline, so nothing below changes.
        _hist_store = None
        if settings.HISTORICAL_STORE_ENABLED:
            try:
                from data.historical_store import HistoricalStore
                _hist_store = HistoricalStore()
            except Exception as _exc:
                logging.warning(
                    "ProcessingEngine: could not initialise HistoricalStore — "
                    "falling back to direct DTO path. Error: %s", _exc,
                )

        results = {}
        for ticker, dto in fund_dtos.items():
            if not dto:
                continue
            try:
                # EXPLANATION: Safe extraction of raw yfinance info fields stored on the DTO.
                info = getattr(dto, 'raw_info', {}) or {}

                # ── Phase 3: merge typed fundamentals from DB cache ───────────
                # If HistoricalStore is active, write today's raw info dict to the
                # DB (or read from cache if fresh) and overlay any non-None typed
                # values onto the existing DTO attributes.  The DTO remains the
                # authoritative object; we only supplement missing or stale fields.
                if _hist_store is not None and info:
                    try:
                        # Pass the provider extracted from data.market_data so the
                        # inner fallback in get_fundamentals can reach the network.
                        from data.market_data import get_provider as _get_provider
                        _provider = _get_provider()
                        _typed = _hist_store.get_fundamentals(
                            ticker,
                            max_age_days=settings.FUNDAMENTALS_REFRESH_DAYS,
                            provider=_provider,
                        )
                        # Overlay typed values onto the raw info dict so that
                        # downstream processing_engine code (which reads from `info`
                        # and `dto.*`) still works unchanged.
                        if _typed:
                            _key_remap = {
                                "pe_ratio":         "trailingPE",
                                "pb_ratio":         "priceToBook",
                                "roe":              "returnOnEquity",
                                "dividend_yield":   "dividendYield",
                                "market_cap":       "marketCap",
                                "eps":              "trailingEps",
                                "operating_margin": "operatingMargins",
                                # debt_to_equity: stored as decimal; info uses percent
                            }
                            for typed_col, raw_key in _key_remap.items():
                                typed_val = _typed.get(typed_col)
                                if typed_val is not None and not (
                                    isinstance(typed_val, float) and math.isnan(typed_val)
                                ):
                                    info.setdefault(raw_key, typed_val)
                    except Exception as _exc:
                        logging.debug(
                            "ProcessingEngine[%s]: HistoricalStore fundamentals "
                            "overlay failed: %s (continuing with DTO).", ticker, _exc,
                        )
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

                # Quality Score (heuristic dividend/valuation score -- distinct
                # from the 'quality_factor_score' multifactor input below).
                score = 50
                if yield_dragged_val > price:  score += 10
                if dto.dividend_yield > 0.03:  score += 10
                if dto.beta < 1.0:             score += 5

                # ==========================================================
                # MULTIFACTOR FACTOR INPUTS (signals/multifactor.py)
                # Reference: Hou-Xue-Zhang (2020) -- only factors with strong
                # economic priors (value, quality, low-vol, size; momentum is
                # signals/cross_sectional_momentum.py). The quality factor is the
                # mean of the available profitability metrics among
                # {returnOnEquity, operatingMargins, grossMargins}. NaN (never
                # fabricated) when the underlying yfinance field is unavailable.
                # ==========================================================
                book_to_market = (
                    1.0 / dto.pb_ratio if dto.pb_ratio and dto.pb_ratio > 0 else float('nan')
                )
                earnings_yield = (
                    1.0 / dto.pe_ratio if dto.pe_ratio and dto.pe_ratio > 0 else float('nan')
                )

                # Quality = MEAN of the available profitability metrics among
                # {returnOnEquity, operatingMargins, grossMargins} (all FRACTIONS,
                # same units). A mean (not a sum) keeps 1/2/3-metric tickers on one
                # scale for the downstream cross-sectional z-score in
                # signals/multifactor.py, so a ticker isn't advantaged merely by
                # having more metrics present. grossMargins (emitted by
                # data/yahoo_fundamentals.py) is now consumed here. Missing metrics
                # are skipped, never treated as 0.0 (CONSTRAINT #4).
                _quality_inputs = []
                for _q in (info.get('returnOnEquity'),
                           info.get('operatingMargins'),
                           info.get('grossMargins')):
                    if _q is not None and not math.isnan(float(_q)):
                        _quality_inputs.append(float(_q))
                if _quality_inputs:
                    quality_factor_score = sum(_quality_inputs) / len(_quality_inputs)
                else:
                    # Fallback proxy: lower leverage = higher quality. debt_to_equity
                    # is already parsed above (None when yfinance omits the field).
                    quality_factor_score = (
                        -float(debt_to_equity) if debt_to_equity is not None else float('nan')
                    )

                vol_60d = realized_vol_60d_map.get(ticker)
                low_vol_score = (
                    -float(vol_60d) if vol_60d is not None and not math.isnan(vol_60d) else float('nan')
                )

                log_market_cap = (
                    math.log(dto.market_cap) if dto.market_cap and dto.market_cap > 0 else float('nan')
                )

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
                    'book_to_market':           book_to_market,
                    'earnings_yield':           earnings_yield,
                    'quality_factor_score':     quality_factor_score,
                    'low_vol_score':            low_vol_score,
                    'log_market_cap':           log_market_cap,
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
            hmm_p = regime_data.get('HMM_Risk_On_Probability')
            flat_data['HMM_Risk_On_Probability'] = float(hmm_p) if hmm_p is not None else float('nan')
            final_rows.append(flat_data)
            
        return pd.DataFrame(final_rows)

    def calculate_momentum_metrics(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Computes ROC_12M and ROC_6M (the trailing-return momentum metrics with
        real downstream consumers). All use .shift(1) to guarantee no lookahead.
        Also computes realized 60-day volatility and Momentum_Vol_Scaled.
        """
        if df.empty or len(df) < 253:
            # BUG-FIX: was 0.0 — but 0% momentum is a FABRICATED value for
            # securities with insufficient history. Downstream signal modules
            # (CrossSectionalMomentum, TimeSeriesMomentum) must see NaN and
            # treat these as "no opinion", not "flat". Constraint #4.
            _nan = float('nan')
            df['ROC_12M'] = _nan
            df['ROC_6M'] = _nan
            df['Realized_Vol_60D'] = _nan
            df['Momentum_Vol_Scaled'] = _nan
            return df

        df = df.sort_index()

        # 1. Trailing returns (without skip) shifted by 1 to guarantee no lookahead
        # Close[t-1] / Close[t-253] - 1.0 (252 trading days)
        df['ROC_12M'] = df['Close'].shift(1) / df['Close'].shift(253) - 1.0
        df['ROC_6M'] = df['Close'].shift(1) / df['Close'].shift(127) - 1.0

        # 2. Volatility scaling: 60-day realized vol of daily returns (annualized)
        # Use daily returns shifted by 1 to prevent lookahead
        daily_returns = df['Close'].pct_change().shift(1)
        realized_vol_60d = daily_returns.rolling(window=60).std() * np.sqrt(252)
        # Exposed as a standalone column (in addition to feeding Momentum_Vol_Scaled
        # below) so calculate_technical_metrics can surface it for the low-volatility
        # factor input consumed by signals/multifactor.py.
        df['Realized_Vol_60D'] = realized_vol_60d

        # Momentum Vol Scaled = ROC_12M * (0.10 / realized_vol_60d)
        # Handle zero or NaN volatility safely
        df['Momentum_Vol_Scaled'] = np.where(
            (realized_vol_60d > 0) & (df['ROC_12M'].notna()) & (realized_vol_60d.notna()),
            df['ROC_12M'] * (0.10 / realized_vol_60d),
            0.0
        )

        return df


def calculate_rolling_beta(
    price_df: pd.DataFrame,
    spy_df: pd.DataFrame,
    window: int = 60,
) -> pd.Series:
    """Rolling window beta of a ticker vs SPY: Cov(returns, spy_returns) / Var(spy_returns).

    Distinct from the existing STATIC single-window beta (data/yahoo_fundamentals.py,
    surfaced as config.COLUMN_SCHEMA's ``Beta`` column) -- this exposes how beta
    DRIFTS over time rather than a single point-in-time snapshot.

    Args:
        price_df: Must have a 'Close' column and a DatetimeIndex (same shape
            contract as DataEngine.fetch_technical_raw() / HistoricalStore.get_bars()).
        spy_df: Same shape, SPY's own OHLCV history, aligned to price_df by
            date (inner join -- dates missing from either side are dropped,
            never fabricated/forward-filled -- CONSTRAINT #4).
        window: Rolling window size in trading days (default 60).

    Returns:
        pd.Series indexed like the aligned data. Each value uses ONLY data up
        to and including that date (``.rolling(window)`` at row i sees only
        rows [i-window+1, i], so this is lookahead-free by construction --
        verified by tests/test_indicators_lookahead.py's perturbation test).
        The first `window` rows are NaN (insufficient history), never a
        fabricated 0.0 or forward-filled value. Returns an empty Series if
        either input is empty/missing 'Close', or if the aligned overlap has
        fewer than `window` rows (CONSTRAINT #6 -- never raises).
    """
    try:
        if (
            price_df is None or price_df.empty or "Close" not in price_df.columns
            or spy_df is None or spy_df.empty or "Close" not in spy_df.columns
        ):
            return pd.Series(dtype=float)

        aligned = pd.concat(
            [price_df["Close"].rename("ticker"), spy_df["Close"].rename("spy")],
            axis=1,
            join="inner",
        ).sort_index()

        if len(aligned) < window:
            return pd.Series(dtype=float, index=aligned.index)

        returns = aligned["ticker"].pct_change()
        spy_returns = aligned["spy"].pct_change()

        rolling_cov = returns.rolling(window).cov(spy_returns)
        rolling_var = spy_returns.rolling(window).var()
        beta = rolling_cov / rolling_var
        beta.name = "Rolling_Beta"
        return beta

    except Exception as exc:
        logging.warning("calculate_rolling_beta failed: %s", exc)
        return pd.Series(dtype=float)
