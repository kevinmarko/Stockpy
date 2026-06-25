# =============================================================================
# MODULE: MASTER ORCHESTRATOR
# File: main_orchestrator.py
# Description: Acts as the central routing hub of the InvestYo Quant Platform.
#              Orchestrates asynchronous data acquisition, routes data to math
#              engines, performs schema validation, and compiles HTML reports.
# =============================================================================

import sys
import os
import subprocess

# Auto-re-route to virtual environment interpreter if not already running in it
venv_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv", "bin")
venv_python = os.path.join(venv_dir, "python3")
if not os.path.exists(venv_python):
    venv_python = os.path.join(venv_dir, "python")

if os.path.exists(venv_python) and os.path.realpath(sys.executable) != os.path.realpath(venv_python):
    sys.exit(subprocess.call([venv_python] + sys.argv))


import contextlib
import os
import sys
import json
import logging
import asyncio
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from typing import Optional, Any

# Core imports
import config
from settings import settings
from data_engine import DataEngine, MockDataEngine
from processing_engine import ProcessingEngine
from macro_engine import MacroEngine
from technical_options_engine import TechnicalOptionsEngine
from forecasting_engine import ForecastingEngine
from strategy_engine import StrategyEngine
from evaluation_engine import EvaluationEngine
from dto_models import MarketBarDTO, FundamentalDataDTO, MacroEconomicDTO
from allocators.dual_momentum import DualMomentumAllocator
from signals import global_registry
from signals.base import SignalContext
from volatility.iv_engine import IVHistoryStore, get_30d_atm_iv, calculate_true_ivr, get_vrp
from diagnostics_and_visuals import (
    telemetry, 
    generate_plotly_volatility_bands, 
    generate_html_report
)

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("MasterOrchestrator")


# =============================================================================
# CROSS-SECTIONAL MOMENTUM HELPER (Jegadeesh-Titman 1993)
# =============================================================================

def compute_xsec_momentum_ranks(
    tech_raw: dict,
    skip_days: int = 22,
    lookback_days: int = 252,
) -> pd.Series:
    """Compute cross-sectional 12-1m momentum returns and percentile ranks.

    Fully vectorized — no iterrows().  For each ticker in tech_raw:
        r = close[t - skip_days] / close[t - lookback_days] - 1
    where t is the last available row.

    Parameters
    ----------
    tech_raw : dict[str, pd.DataFrame]
        OHLCV DataFrames keyed by ticker (output of DataEngine.fetch_technical_raw).
    skip_days : int
        Number of trading days to skip at the end (default 22 ≈ 1 month).
    lookback_days : int
        Total lookback window in trading days (default 252 ≈ 12 months).

    Returns
    -------
    pd.Series
        Index: ticker str, values: percentile rank in [0, 1].
        NaN rank for tickers with insufficient history.
    """
    returns: dict = {}
    required = lookback_days + skip_days + 1

    for ticker, df in tech_raw.items():
        if df is None or df.empty or "Close" not in df.columns:
            continue
        close = df["Close"].dropna()
        if len(close) < required:
            continue
        # All indexing is on the sorted series; both references are strictly < t
        p_recent = close.iloc[-(skip_days + 1)].item()   # price at t - skip_days
        p_old = close.iloc[-(lookback_days + 1)].item()  # price at t - lookback_days
        if p_old <= 0:
            continue
        returns[ticker] = p_recent / p_old - 1.0

    if not returns:
        return pd.Series(dtype=float)

    ret_series = pd.Series(returns)
    # Cross-sectional rank: ascending so high returns → high rank
    return ret_series.rank(pct=True, ascending=True)


async def fetch_all_data_async(de: DataEngine, tickers: list) -> tuple:
    """
    Fetches macroeconomic, fundamental, and technical pricing data concurrently
    using asyncio.gather to avoid blocking the event loop.
    """
    telemetry.info(f"Initiating concurrent data fetching for {len(tickers)} tickers...")
    
    macro_task = asyncio.to_thread(de.fetch_macro_raw)
    fund_task = asyncio.to_thread(de.fetch_fundamentals_raw, tickers)
    tech_task = asyncio.to_thread(de.fetch_technical_raw, list(set(tickers + ["SPY"])))
    
    macro_raw, fund_raw, tech_raw = await asyncio.gather(macro_task, fund_task, tech_task)
    telemetry.info("Data fetching completed successfully.")
    return macro_raw, fund_raw, tech_raw


def run_pipeline(tickers: list, macro_raw: dict, fund_raw: dict, tech_raw: dict,
                  data_engine: Optional[Any] = None) -> pd.DataFrame:
    """
    Synchronous execution of the quantitative engines:
    Macro -> Technical Options -> Processing -> Forecasting -> Strategy & Evaluation.

    Parameters
    ----------
    data_engine : IDataProvider, optional
        Used by MacroEngine.compute_hmm_risk_on_probability() to fetch
        historical VIX/yield-curve series (regime/hmm_regime.py's second
        opinion). None (the default) disables the HMM second opinion --
        market_regime/killSwitch then behave exactly as before this feature
        existed (no fabricated probability).
    """
    # 1. Macro Economic Regime Analysis
    telemetry.info("Routing data through Macro Engine...")
    me = MacroEngine(data_engine=data_engine)
    sahm_val = me._fallback_sentiment("")  # Default sahm value proxy
    macro_data = me.run_macro_killswitch(macro_raw, sahm_val)
    market_regime = macro_data["market_regime"].iloc[0]

    # HMM second opinion (regime/hmm_regime.py): uses real SPY price history
    # already fetched into tech_raw -- never fabricated, None if unavailable.
    hmm_risk_on_probability = me.compute_hmm_risk_on_probability(tech_raw.get('SPY'))

    macro_dto = MacroEconomicDTO(
        yield_curve_10y_2y=float(macro_raw.get('T10Y2Y', 0.5)),
        high_yield_oas=float(macro_raw.get('BAMLH0A0HYM2', 3.5)),
        inflation_rate=float(macro_raw.get('CPIAUCSL_YoY', 2.0)),
        nominal_10y=float(macro_raw.get('DGS10', 4.0)),
        vix_value=float(macro_raw.get('VIXCLS', 15.0)),
        hmm_risk_on_probability=hmm_risk_on_probability,
    )

    # 2. Technical Options Analysis
    telemetry.info("Routing data through Technical Options Engine...")
    toe = TechnicalOptionsEngine()
    tech_opt_indicators = {}
    iv_store = IVHistoryStore()
    for ticker in tickers:
        df_hist = tech_raw.get(ticker)
        if df_hist is not None and not df_hist.empty:
            indicators = toe.calculate_indicators(df_hist)
            vol = toe.estimate_gjr_garch_volatility(df_hist)
            realized_vol_rank = toe.calculate_realized_vol_rank(df_hist, vol)
            
            # Fetch options chain / compute true 30d ATM IV
            as_of_date = df_hist.index[-1].strftime("%Y-%m-%d")
            price_val = float(df_hist['Close'].iloc[-1])
            
            current_iv = float('nan')
            if data_engine is not None:
                current_iv = get_30d_atm_iv(data_engine, ticker, as_of_date, spot_price=price_val)
                if not np.isnan(current_iv):
                    iv_store.record_iv(ticker, as_of_date, current_iv)
            
            true_ivr = calculate_true_ivr(ticker, current_iv, as_of_date, iv_store)
            vrp = get_vrp(ticker, current_iv, vol)
            
            # Call strategy matrix with true_ivr, vrp, and macro_dto
            opt_strategy = toe.generate_option_strategy_matrix(
                true_ivr=true_ivr if not np.isnan(true_ivr) else 50.0,
                aroon_osc=indicators["Aroon_Oscillator"],
                coppock_val=indicators["Coppock_Curve"],
                stock_price=price_val,
                current_iv=current_iv if not np.isnan(current_iv) else vol,
                vrp=vrp,
                macro_dto=macro_dto
            )
            tech_opt_indicators[ticker] = {
                "Aroon_Oscillator": indicators["Aroon_Oscillator"],
                "Coppock_Curve": indicators["Coppock_Curve"],
                "Chandelier_Long": indicators["Chandelier_Long"],
                "Chandelier_Short": indicators["Chandelier_Short"],
                "GARCH_Vol": vol,
                "Realized_Vol_Rank": realized_vol_rank,
                "True_IVR": true_ivr,
                "VRP": vrp,
                "Option_Strategy_Matrix": opt_strategy
            }

    # 3. Core Processing
    telemetry.info("Routing data through Computational Core (Processing)...")
    pe = ProcessingEngine()
    regime_metrics = pe.process_macro_regime(macro_dto)
    tech_metrics = pe.calculate_technical_metrics(tech_raw, transactions_df=None)
    
    # Map raw fundamentals to DTOs
    fund_dtos = {}
    for ticker, data in fund_raw.items():
        if data and 'info' in data:
            fund_dtos[ticker] = FundamentalDataDTO.from_raw_dict(ticker, data['info'], dividends=data.get('dividends'))

    # Realized_Vol_60D feeds the multifactor low-volatility factor input
    # (signals/multifactor.py); sourced from tech_metrics, not fabricated.
    realized_vol_60d_map = {
        ticker: metrics.get('Realized_Vol_60D', float('nan'))
        for ticker, metrics in tech_metrics.items()
    }
    fund_metrics = pe.calculate_fundamental_metrics(fund_dtos, realized_vol_60d_map=realized_vol_60d_map)
    dashboard_df = pe.compile_dashboard(tech_metrics, fund_metrics, regime_metrics)

    # Explicitly map GARCH_Vol, Realized_Vol_Rank, True_IVR, VRP, and advanced indicators from tech_opt_indicators to dashboard_df
    dashboard_df['GARCH_Vol'] = 0.0
    dashboard_df['Realized_Vol_Rank'] = 0.0
    dashboard_df['True_IVR'] = 0.0
    dashboard_df['VRP'] = 0.0
    dashboard_df['Aroon Oscillator'] = 0.0
    dashboard_df['Coppock Curve'] = 0.0
    dashboard_df['Chandelier Exit'] = 0.0
    for idx, row in dashboard_df.iterrows():
        ticker = row['Symbol']
        if ticker in tech_opt_indicators:
            dashboard_df.at[idx, 'GARCH_Vol'] = tech_opt_indicators[ticker].get('GARCH_Vol', 0.0)
            dashboard_df.at[idx, 'Realized_Vol_Rank'] = tech_opt_indicators[ticker].get('Realized_Vol_Rank', 0.0)
            dashboard_df.at[idx, 'True_IVR'] = tech_opt_indicators[ticker].get('True_IVR', 0.0)
            dashboard_df.at[idx, 'VRP'] = tech_opt_indicators[ticker].get('VRP', 0.0)
            dashboard_df.at[idx, 'Aroon Oscillator'] = tech_opt_indicators[ticker].get('Aroon_Oscillator', 0.0)
            dashboard_df.at[idx, 'Coppock Curve'] = tech_opt_indicators[ticker].get('Coppock_Curve', 0.0)
            dashboard_df.at[idx, 'Chandelier Exit'] = tech_opt_indicators[ticker].get('Chandelier_Long', 0.0)

    # 4. Multi-Horizon Forecasting (with robust ML exception safety)
    telemetry.info("Routing data through Forecasting Engine...")
    fe = ForecastingEngine()
    forecast_cols = ['Target_Days', 'ARIMA', 'MC_Target', 'MC_Lower', 'MC_Upper',
                     'Forecast_10', 'Forecast_30', 'Forecast_60', 'Forecast_90',
                     'Forecast_30_Prophet_Lower', 'Forecast_30_Prophet_Upper']
    for col in forecast_cols:
        dashboard_df[col] = 0.0

    for idx, row in dashboard_df.iterrows():
        ticker = row['Symbol']
        price = row['Price']
        if not price or price == 0:
            continue
        
        history_df = tech_raw.get(ticker)
        history_series = history_df['Close'] if history_df is not None else None

        # ML Fallback wrapping: ensures CNN-LSTM failure does not crash execution
        try:
            forecasts = fe.generate_forecast(row, price, history_series, history_df=history_df)
            for col in forecast_cols:
                dashboard_df.at[idx, col] = forecasts.get(col, 0.0)
        except Exception as ml_err:
            telemetry.warning(f"ML / Deep learning forecast failed for {ticker}: {ml_err}. Falling back to statistical values.")
            # Calculate standard Monte Carlo and ARIMA manually as fallback
            mu = 0.0002
            sigma = 0.015
            if history_series is not None and len(history_series) > 1:
                returns = np.log(history_series / history_series.shift(1)).dropna()
                mu = float(returns.mean())
                sigma = float(returns.std())
            
            mc_target, mc_low, mc_high = fe.run_monte_carlo(price, mu, sigma, 30)
            dashboard_df.at[idx, 'Target_Days'] = 30
            dashboard_df.at[idx, 'MC_Target'] = mc_target
            dashboard_df.at[idx, 'MC_Lower'] = mc_low
            dashboard_df.at[idx, 'MC_Upper'] = mc_high
            dashboard_df.at[idx, 'Forecast_30'] = mc_target
            dashboard_df.at[idx, 'Forecast_10'] = price * (1.0 + mu * 10)
            dashboard_df.at[idx, 'Forecast_60'] = price * (1.0 + mu * 60)
            dashboard_df.at[idx, 'Forecast_90'] = price * (1.0 + mu * 90)

    # 5. Cross-Sectional Momentum Pre-Compute (Jegadeesh-Titman 1993)
    # Must run BEFORE the per-ticker strategy loop so pre_compute populates
    # context.xsec_percentile_ranks for all tickers at once.
    telemetry.info("Computing cross-sectional momentum ranks (Jegadeesh-Titman)...")
    dashboard_df['XSec_12_1M'] = float('nan')
    dashboard_df['XSec_Momentum_Rank'] = float('nan')

    # Compute vectorized 12-1m returns for the full universe
    xsec_rank_series = compute_xsec_momentum_ranks(tech_raw)

    # Write 12-1m returns and ranks back to dashboard_df before pre_compute
    xsec_return_dict: dict = {}
    for ticker_i, df_i in tech_raw.items():
        if df_i is None or df_i.empty or 'Close' not in df_i.columns:
            continue
        close_i = df_i['Close'].dropna()
        required_i = 252 + 22 + 1
        if len(close_i) < required_i:
            continue
        p_recent_i = float(close_i.iloc[-23])   # t - 22
        p_old_i = float(close_i.iloc[-253])      # t - 252
        if p_old_i > 0:
            xsec_return_dict[ticker_i] = p_recent_i / p_old_i - 1.0

    for idx_x, row_x in dashboard_df.iterrows():
        tk = row_x['Symbol']
        if tk in xsec_return_dict:
            dashboard_df.at[idx_x, 'XSec_12_1M'] = xsec_return_dict[tk]
        if tk in xsec_rank_series.index:
            dashboard_df.at[idx_x, 'XSec_Momentum_Rank'] = float(xsec_rank_series[tk])

    # Build a shared SignalContext stub (bar/fundamentals populated per-ticker below)
    # The xsec_percentile_ranks dict lives in the shared context and is read-only per ticker
    _shared_macro_dto = macro_dto  # already built above
    _stub_bar = MarketBarDTO(datetime.now(), "__UNIVERSE__", 100.0, 100.0, 100.0, 100.0, 0)
    _stub_fund = FundamentalDataDTO(
        ticker="__UNIVERSE__", pe_ratio=None, pb_ratio=None, dividend_yield=0.0,
        book_value=0.0, eps_trailing=0.0, dividend_growth_rate=0.0,
        payout_ratio=0.0, sector="Unknown", company_name="Unknown"
    )
    shared_context = SignalContext(
        bar=_stub_bar,
        fundamentals=_stub_fund,
        macro=_shared_macro_dto,
    )
    # Trigger pre_compute on all signal modules (most are no-ops; XSec fills rank
    # dict; MultifactorSignal fills multifactor_scores -- see signals/multifactor.py)
    global_registry.run_pre_compute(dashboard_df, shared_context)
    telemetry.info(
        "Cross-sectional pre_compute complete. %d tickers ranked.",
        len(shared_context.xsec_percentile_ranks),
    )

    # Write multifactor Z-scores back into dashboard_df (computed by
    # MultifactorSignal.pre_compute above; not available before this point).
    for col in ('Value_Z', 'Quality_Z', 'LowVol_Z', 'Size_Z', 'Multifactor_Composite'):
        dashboard_df[col] = float('nan')
    for idx_m, row_m in dashboard_df.iterrows():
        tk_m = row_m['Symbol']
        entry_m = shared_context.multifactor_scores.get(tk_m)
        if entry_m is None:
            continue
        for col in ('Value_Z', 'Quality_Z', 'LowVol_Z', 'Size_Z', 'Multifactor_Composite'):
            dashboard_df.at[idx_m, col] = entry_m.get(col, float('nan'))

    # 6. Strategy & Sizing Evaluations
    telemetry.info("Routing data through Strategy and Evaluation Engines...")
    se = StrategyEngine()
    ee = EvaluationEngine()
    
    # 'sellRange' is the dedicated sell-side execution band (strategy_engine.
    # apply_sell_side_range) — always populated alongside buyRange, never empty
    # for a valid Action Signal. See config.COLUMN_SCHEMA for the dashboard header.
    strategy_cols = ['Action Signal', 'Advice', 'Actionable Advice Signal', 'Kelly Target', 'Option Strategy', 'buyRange', 'sellRange', 'Strategy Explainer Notes']
    for col in strategy_cols:
        dashboard_df[col] = ""
    dashboard_df['Kelly Target'] = 0.0
    dashboard_df['Edge Ratio'] = 0.0

    for idx, row in dashboard_df.iterrows():
        ticker = row['Symbol']
        price = row['Price']
        if not price or price == 0:
            continue

        # MarketBar DTO
        history_df = tech_raw.get(ticker)
        if history_df is not None and not history_df.empty:
            latest_row = history_df.iloc[-1]
            bar_dto = MarketBarDTO(
                date=datetime.now(),
                ticker=ticker,
                open_price=latest_row.get('Open', price),
                high_price=latest_row.get('High', price),
                low_price=latest_row.get('Low', price),
                close_price=latest_row.get('Close', price),
                volume=int(latest_row.get('Volume', 0))
            )
        else:
            bar_dto = MarketBarDTO(datetime.now(), ticker, price, price, price, price, 0)

        # Fundamentals DTO
        fund_dto = fund_dtos.get(ticker)
        if fund_dto is None:
            fund_dto = FundamentalDataDTO(
                ticker=ticker, pe_ratio=None, pb_ratio=None, dividend_yield=0.0,
                book_value=0.0, eps_trailing=0.0, dividend_growth_rate=0.0,
                payout_ratio=0.0, sector="Unknown", company_name="Unknown"
            )

        # Generate action signal
        atr_val = float(row.get('ATR', 0.0))
        aroon_val = float(row.get('Aroon Up', 50.0))
        macd_line_val = float(row.get('MACD_Line', 0.0))
        macd_signal_val = float(row.get('MACD_Signal', 0.0))
        aroon_osc_val = float(row.get('Aroon Oscillator', 0.0))
        rsi_val = float(row.get('RSI', 50.0))
        sortino_val = float(row.get('Sortino Ratio', row.get('Sortino_Ratio', 0.0)))
        drawdown_val = float(row.get('Max Drawdown', row.get('Max_Drawdown', 0.0)))
        rs_val = float(row.get('Relative_Strength', row.get('RS vs SPY', row.get('Relative Strength', 0.0))))
        garch_val = float(row.get('GARCH_Vol', 0.0))
        edge_val = float(row.get('Edge Ratio', row.get('Edge_Ratio', 0.0)))
        rsi_2_val = float(row.get('RSI_2', 50.0)) if pd.notna(row.get('RSI_2', 50.0)) else 50.0
        sma_5_val = float(row.get('SMA_5')) if pd.notna(row.get('SMA_5')) else None

        chan_long = 0.0
        chan_short = 0.0
        if ticker in tech_opt_indicators:
            chan_long = tech_opt_indicators[ticker].get('Chandelier_Long', 0.0)
            chan_short = tech_opt_indicators[ticker].get('Chandelier_Short', 0.0)

        strategy_output = se.evaluate_security(
            bar=bar_dto,
            fundamentals=fund_dto,
            macro=macro_dto,
            forecast_price=row.get('Forecast_30', 0.0),
            trend_strength=aroon_val,
            atr=atr_val,
            macd_line=macd_line_val,
            macd_signal=macd_signal_val,
            aroon_osc=aroon_osc_val,
            rsi=rsi_val,
            sortino_ratio=sortino_val,
            max_drawdown=drawdown_val,
            relative_strength=rs_val,
            garch_vol=garch_val,
            edge_ratio=edge_val,
            chandelier_long=chan_long,
            chandelier_short=chan_short,
            roc_12m=float(row.get('ROC_12M') if pd.notna(row.get('ROC_12M')) else 0.0),
            sma_200=float(row.get('SMA_200') if pd.notna(row.get('SMA_200')) else 0.0),
            rsi_2=rsi_2_val,
            sma_5=sma_5_val
        )

        # Calculate Edge Ratio (Post-trade evaluation)
        edge_ratio_val = 0.0
        if history_df is not None and len(history_df) >= 20:
            # Evaluate a mock hold period for the last 15 trading days
            entry_d = history_df.index[-15]
            exit_d = history_df.index[-1]
            trade_entry_p = float(history_df["Close"].iloc[-15])
            
            edge_data = ee.calculate_edge_ratio(history_df, trade_entry_p, entry_d, exit_d)
            edge_ratio_val = float(edge_data['Edge Ratio'])
            # Add Edge Ratio to explainer notes for completeness
            strategy_output["Strategy Explainer Notes"] += f"\nPOST-TRADE EDGE RATIO: {edge_data['Edge Ratio']:.2f} (MFE: {edge_data['MFE']*100:.1f}%, MAE: {edge_data['MAE']*100:.1f}%)"
        
        dashboard_df.at[idx, 'Edge Ratio'] = edge_ratio_val

        # Apply strategy options and Kelly Targets
        dashboard_df.at[idx, 'Action Signal'] = strategy_output['Action Signal']
        dashboard_df.at[idx, 'Advice'] = strategy_output['Advice']
        dashboard_df.at[idx, 'Actionable Advice Signal'] = strategy_output['Actionable Advice Signal']
        dashboard_df.at[idx, 'is_dividend_sustainable'] = int(fund_dto.is_dividend_sustainable)
        dashboard_df.at[idx, 'eps_trailing'] = fund_dto.eps_trailing
        dashboard_df.at[idx, 'book_value'] = fund_dto.book_value
        dashboard_df.at[idx, 'graham_number'] = fund_dto.graham_number
        
        # Kelly Target: single source of truth is StrategyEngine._calculate_kelly_sizing
        # (sizing.kelly.fractional_kelly / sizing.vol_target.volatility_target_weight),
        # already computed inside se.evaluate_security() above. No second, divergent
        # win-probability formula or score-bracket override here anymore.
        dashboard_df.at[idx, 'Kelly Target'] = float(strategy_output['Kelly Target'])
        if ticker in tech_opt_indicators:
            dashboard_df.at[idx, 'Option Strategy'] = tech_opt_indicators[ticker].get('Option_Strategy_Matrix', '')
        else:
            dashboard_df.at[idx, 'Option Strategy'] = strategy_output['Option Strategy']
        dashboard_df.at[idx, 'buyRange'] = strategy_output['buyRange']
        # Propagate the dedicated sell-side range into dashboard_df so the
        # HTML report, Google Sheets sink, JSON payload, and observability
        # state snapshot all see the same source-of-truth value.
        dashboard_df.at[idx, 'sellRange'] = strategy_output['sellRange']
        dashboard_df.at[idx, 'Strategy Explainer Notes'] = strategy_output['Strategy Explainer Notes']

    # Map 'Avg Cost' to 'Entry_Price' if present
    if 'Avg Cost' in dashboard_df.columns:
        dashboard_df['Entry_Price'] = dashboard_df['Avg Cost']

    # Map 'Shares' to 'position_size' for Portfolio Heat
    if 'Shares' in dashboard_df.columns and 'Price' in dashboard_df.columns:
        dashboard_df['position_size'] = dashboard_df['Shares'] * dashboard_df['Price']
    elif 'position_size' not in dashboard_df.columns:
        dashboard_df['position_size'] = 10000.0 # Default $10k assumption

    # Map VaR 95 to stop loss percentage
    if 'VaR 95' in dashboard_df.columns:
        dashboard_df['stop_loss_pct'] = dashboard_df['VaR 95'].abs()
    elif 'VaR_95' in dashboard_df.columns:
        dashboard_df['stop_loss_pct'] = dashboard_df['VaR_95'].abs()
    elif 'stop_loss_pct' not in dashboard_df.columns:
        dashboard_df['stop_loss_pct'] = 0.05

    # Align Sector string casing
    if 'Sector' in dashboard_df.columns and 'sector' not in dashboard_df.columns:
        dashboard_df['sector'] = dashboard_df['Sector']
    
    if 'RS vs SPY' in dashboard_df.columns and 'Relative_Strength' not in dashboard_df.columns:
        dashboard_df['Relative_Strength'] = dashboard_df['RS vs SPY']
    elif 'Relative Strength' in dashboard_df.columns and 'Relative_Strength' not in dashboard_df.columns:
        dashboard_df['Relative_Strength'] = dashboard_df['Relative Strength']
    elif 'Relative_Strength' not in dashboard_df.columns:
        dashboard_df['Relative_Strength'] = 0.0

    # CRITICAL FIX 3: Generate Benchmark and Execute Evaluation
    if 'sector' in dashboard_df.columns:
        unique_sectors = dashboard_df['sector'].dropna().unique()
        if len(unique_sectors) > 0:
            benchmark_df = pd.DataFrame({
                'sector': unique_sectors,
                'weight': 1.0 / len(unique_sectors),
                'return': 0.02
            })
        else:
            benchmark_df = pd.DataFrame()
    else:
        benchmark_df = pd.DataFrame()

    dashboard_df = ee.evaluate_portfolio(dashboard_df, benchmark_df, data_provider=tech_raw)

    # CRITICAL FIX 4: Handle NaNs/values before Google Sheets Export
    export_keys = ['MAE', 'MFE', 'Edge Ratio', 'Portfolio_Heat', 'BF_Allocation', 'BF_Selection']
    for key in export_keys:
        if key in dashboard_df.columns:
            if key not in ['MAE', 'MFE', 'Edge Ratio']:
                dashboard_df[key] = dashboard_df[key].fillna(0.0)
        else:
            if key not in ['MAE', 'MFE', 'Edge Ratio']:
                dashboard_df[key] = 0.0
            else:
                dashboard_df[key] = np.nan

    # ---- Dual Momentum Overlay (optional, gated by settings flag) ----
    if settings.USE_DUAL_MOMENTUM_OVERLAY:
        telemetry.info("Running Dual Momentum Overlay...")
        try:
            dm = DualMomentumAllocator(
                risky_assets=list(settings.DUAL_MOMENTUM_RISKY_ASSETS),
                safe_asset=settings.DUAL_MOMENTUM_SAFE_ASSET,
            )
            dm_alloc = dm.decide(
                as_of_date=datetime.now(timezone.utc).date(),
                price_data=tech_raw,
            )
            dm_winner = next(iter(dm_alloc))  # Single-asset allocation
            telemetry.info(f"Dual Momentum decision: {dm_winner} ({dm_alloc})"
                           )
            # If safe asset selected, zero out Kelly for all risky universe tickers
            if dm_winner == settings.DUAL_MOMENTUM_SAFE_ASSET:
                risky_set = set(settings.DUAL_MOMENTUM_RISKY_ASSETS)
                mask = dashboard_df["Symbol"].isin(risky_set)
                dashboard_df.loc[mask, "Kelly Target"] = 0.0
                telemetry.info(
                    f"Dual Momentum: safe-asset regime. Kelly Target zeroed for "
                    f"{list(risky_set & set(dashboard_df['Symbol'].tolist()))}"
                )
            # Record the DM decision in a new column for reporting
            dashboard_df["DualMomentum_Signal"] = dm_winner
        except Exception as dm_err:
            telemetry.warning(f"Dual Momentum Overlay failed (non-critical): {dm_err}")
            dashboard_df["DualMomentum_Signal"] = "N/A"
    else:
        dashboard_df["DualMomentum_Signal"] = "disabled"

    return dashboard_df


async def _heartbeat(output_dir, interval: int = 60) -> None:
    """Background task: log 'ALIVE' and update heartbeat.txt every ``interval`` seconds.

    A watchdog script can read heartbeat.txt and activate the global kill switch
    if the UTC timestamp goes stale (> 2× interval), signalling an orchestrator crash.
    """
    heartbeat_file = output_dir / "heartbeat.txt"
    while True:
        await asyncio.sleep(interval)
        ts = datetime.now(timezone.utc).isoformat()
        logger.info("ORCHESTRATOR ALIVE — heartbeat at %s", ts)
        try:
            heartbeat_file.write_text(ts, encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to write heartbeat file: %s", exc)


async def _execute_broker_orders(
    final_df: "pd.DataFrame",
    dry_run: bool,
    macro_dto: Optional[Any] = None,
) -> None:
    """
    Translate signal → desired position → delta → orders and submit via
    OrderManager (with kill-switch gate + pre-trade risk gate).

    Design constraints
    ------------------
    * Never called when Alpaca credentials are absent (checked by caller).
    * Errors are logged as ERROR and never propagate — broker execution is
      best-effort; the analysis pipeline's value must never be held hostage
      to broker connectivity.
    * Only BUY signals with Kelly Target > 0 generate new orders; SELL/TRIM
      signals close existing positions.
    * Kill-switch active → ``KillSwitchActiveError`` is raised inside
      ``submit_order_with_idempotency``; caught here and logged as CRITICAL.
    * ``dry_run=True`` logs intent but never reaches the broker network.
    """
    try:
        from execution.alpaca_broker import AlpacaBroker
        from execution.broker_base import OrderIntent, OrderSide, OrderType
        from execution.kill_switch import KillSwitchActiveError
        from execution.order_manager import OrderManager
        from execution.risk_gate import PreTradeRiskGate, RiskContext
        from transactions_store import TransactionsStore

        broker = AlpacaBroker()
        ts_store = TransactionsStore()
        risk_gate = PreTradeRiskGate()
        om = OrderManager(broker, dry_run=dry_run, risk_gate=risk_gate)

        # --- Reconcile before submitting new orders ---
        recon_report = await om.reconcile_state(ts_store)
        if recon_report.has_drift:
            telemetry.critical(
                "Broker state drift detected before order submission — "
                "review reconciliation report before trusting signals."
            )

        # --- Fetch live positions + account for risk-gate context ---
        open_pos = await broker.get_open_positions()
        open_symbols = {p.symbol: p.qty for p in open_pos}
        try:
            account = await broker.get_account()
        except Exception:
            account = None

        prices: dict[str, float] = {
            str(row.get("Symbol", "")).upper(): float(row.get("Price", 0.0) or 0.0)
            for _, row in final_df.iterrows()
        }

        risk_ctx = RiskContext(
            macro=macro_dto,
            open_positions=open_pos,
            account=account,
            current_prices=prices,
            is_premium_sell_strategy=False,
        )

        now = datetime.now(timezone.utc)
        for _, row in final_df.iterrows():
            symbol = str(row.get("Symbol", "")).upper()
            signal = str(row.get("Action Signal", "")).upper()
            kelly = float(row.get("Kelly Target", 0.0) or 0.0)

            if not symbol:
                continue

            try:
                if "BUY" in signal and kelly > 0 and symbol not in open_symbols:
                    intent = OrderIntent(
                        strategy_id="main_pipeline",
                        symbol=symbol,
                        side=OrderSide.BUY,
                        qty=1.0,
                        order_type=OrderType.MARKET,
                    )
                    result = await om.submit_order_with_idempotency(
                        intent, timestamp=now, risk_context=risk_ctx
                    )
                    telemetry.info(
                        "Order submitted: BUY %s -> status=%s broker_id=%s",
                        symbol, result.status.value, result.broker_order_id,
                    )

                elif signal in ("SELL", "TRIM") and symbol in open_symbols:
                    sell_qty = abs(open_symbols[symbol])
                    intent = OrderIntent(
                        strategy_id="main_pipeline",
                        symbol=symbol,
                        side=OrderSide.SELL,
                        qty=sell_qty,
                        order_type=OrderType.MARKET,
                    )
                    result = await om.submit_order_with_idempotency(
                        intent, timestamp=now, risk_context=risk_ctx
                    )
                    telemetry.info(
                        "Order submitted: SELL %s x %.4f -> status=%s broker_id=%s",
                        symbol, sell_qty, result.status.value, result.broker_order_id,
                    )

            except KillSwitchActiveError as ks_err:
                telemetry.critical(
                    "Kill switch is ACTIVE — aborting all remaining order submission. %s", ks_err
                )
                return  # bail out of the entire loop; no further submissions this cycle

            except Exception as order_err:
                telemetry.error(
                    "Order submission failed for %s: %s", symbol, order_err, exc_info=True
                )

    except Exception as exc:
        telemetry.error(
            "_execute_broker_orders crashed (non-fatal): %s", exc, exc_info=True
        )


def _write_state_snapshot(macro_raw: dict, final_df: "pd.DataFrame", tickers: list) -> None:
    """Persist a JSON state snapshot to OUTPUT_DIR/state_snapshot.json.

    The Streamlit observability dashboard reads this file to display the
    last-known macro state without requiring a live FRED/broker connection.
    Errors are swallowed so a snapshot failure never crashes the pipeline.
    """
    import json
    try:
        signals = []
        if not final_df.empty:
            for _, row in final_df.iterrows():
                signals.append({
                    "symbol": str(row.get("Symbol", "")),
                    "action": str(row.get("Action Signal", "")),
                    "kelly_target": float(row.get("Kelly Target", 0.0) or 0.0),
                    "score": float(row.get("Score", 0.0) or 0.0),
                    "price": float(row.get("Price", 0.0) or 0.0),
                    "macro_status": str(row.get("Macro Status", "")),
                    "hmm_risk_on": float(row.get("HMM_Risk_On_Probability", 0.0) or 0.0),
                    # Buy- and sell-side execution corridors surfaced so the
                    # Streamlit observability dashboard can render the full
                    # tactical plan without re-reading the SQLite DB.
                    "buy_range": str(row.get("buyRange", "")),
                    "sell_range": str(row.get("sellRange", "")),
                })
        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tickers": tickers,
            "market_regime": str(macro_raw.get("market_regime", "UNKNOWN")),
            "vix": float(macro_raw.get("VIXCLS", 0.0) or 0.0),
            "yield_curve": float(macro_raw.get("T10Y2Y", 0.0) or 0.0),
            "kill_switch_active": (settings.OUTPUT_DIR / "KILL_SWITCH").exists(),
            "signals": signals,
        }
        snap_path = settings.OUTPUT_DIR / "state_snapshot.json"
        snap_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    except Exception as exc:
        telemetry.warning("Failed to write state snapshot: %s", exc)


async def _main_body(effective_dry_run: bool) -> None:
    """Core pipeline logic — separated from main() so the heartbeat try/finally is clean."""
    # Surface a CRITICAL alert if the previously leaked FRED key is still in use.
    settings.warn_if_fred_key_leaked(telemetry)

    # Initialize real or mock data engine based on credentials
    creds_exist = os.path.exists("credentials.json")
    if creds_exist:
        settings.ensure_fred_configured()
        de = DataEngine(settings.FRED_API_KEY)
        tickers = settings.DEFAULT_TICKERS
    else:
        telemetry.warning("credentials.json not found. Operating with deterministic MockDataEngine.")
        de = MockDataEngine()
        tickers = ["AAPL"]  # Use mock ticker

    # 1. Asynchronous concurrent data fetching
    try:
        macro_raw, fund_raw, tech_raw = await fetch_all_data_async(de, tickers)
    except Exception as fetch_err:
        telemetry.critical(f"Asynchronous data gathering crashed: {fetch_err}")
        sys.exit(1)

    # Fail-safe check: If offline or data is empty, fall back to MockDataEngine for verification
    if not tech_raw or all(df.empty for df in tech_raw.values()):
        telemetry.warning("Fetched pricing data is empty (likely due to network offline). Falling back to MockDataEngine for verification.")
        de = MockDataEngine()
        macro_raw = de.fetch_macro_raw()
        fund_raw = de.fetch_fundamentals_raw(tickers)
        tech_raw = de.fetch_technical_raw(tickers)

    # 2. Run Pipeline
    try:
        final_df = run_pipeline(tickers, macro_raw, fund_raw, tech_raw, data_engine=de)
    except Exception as pipe_err:
        telemetry.critical(f"Platform execution pipeline crashed: {pipe_err}")
        sys.exit(1)

    # 3. Schema Validation
    if not final_df.empty:
        try:
            config.DashboardSchema.validate(final_df)
            telemetry.info("✅ Final compiled DataFrame successfully validated against DashboardSchema.")
        except Exception as schema_err:
            telemetry.error(f"❌ Final compiled DataFrame failed DashboardSchema validation: {schema_err}")

    # 4. Reporting & Visualization Output
        # Output directory is centrally configured (created on settings load).
        out_dir = str(settings.OUTPUT_DIR)

        primary_ticker = tickers[0]
        primary_hist = tech_raw.get(primary_ticker)
        if primary_hist is not None and not primary_hist.empty:
            try:
                # Plotly expects lowercase column names in diagnostics_and_visuals
                plotly_df = primary_hist.copy()
                plotly_df.columns = [col.lower() for col in plotly_df.columns]
                generate_plotly_volatility_bands(plotly_df, primary_ticker, os.path.join(out_dir, "volatility_bands_dashboard.html"))
            except Exception as plot_err:
                telemetry.warning(f"Failed to generate interactive Plotly chart: {plot_err}")

        # Jinja2 HTML Report Generation
        try:
            portfolio_dicts = final_df.to_dict(orient="records")
            for row in portfolio_dicts:
                if "Max Drawdown" in row:
                    row["Max_Drawdown"] = row["Max Drawdown"]
            yield_curve_val = float(macro_raw.get('T10Y2Y', 0.5))
            credit_spread_val = float(macro_raw.get('BAMLH0A0HYM2', 3.5))
            sahm_rule_val = float(macro_raw.get('SAHMREALTIME', 0.3))
            real_yield_val = float(macro_raw.get('DGS10', 4.0)) - float(macro_raw.get('CPIAUCSL_YoY', 2.0))
            regime_val = final_df["Macro Status"].iloc[0] if "Macro Status" in final_df.columns else "NEUTRAL"
            generate_html_report(
                portfolio_dicts,
                regime_val,
                os.path.join(out_dir, "daily_report_dashboard.html"),
                yield_curve=yield_curve_val,
                credit_spread=credit_spread_val,
                sahm_rule=sahm_rule_val,
                real_yield=real_yield_val
            )
        except Exception as html_err:
            telemetry.warning(f"Failed to generate daily HTML report: {html_err}")

    # 5. Export Final JSON Payload Representation
    if not final_df.empty:
        # Include 'sellRange' so downstream JSON consumers (alerts, custom
        # dashboards, paper-trading harness) receive the same sell-side
        # take-profit/stop instructions the HTML report renders.
        output_payload = final_df[["Symbol", "Price", "Action Signal", "buyRange", "sellRange", "Kelly Target", "Option Strategy", "GARCH_Vol", "True_IVR"]].to_dict(orient="records")
        print("\n=== FINAL ACTIONABLE PAYLOAD REPRESENTATION ===")
        print(json.dumps(output_payload, indent=4))
        print("================================================\n")

    # 6. Broker Execution — submit delta orders and reconcile state
    # Only runs when Alpaca credentials are configured; silently skipped otherwise.
    if not final_df.empty and settings.ALPACA_API_KEY and settings.ALPACA_SECRET_KEY:
        # Reconstruct macro_dto from macro_raw for the risk gate context.
        # run_pipeline() builds a macro_dto internally but doesn't return it, so
        # we reconstruct the same DTO here with the same field mapping.
        _broker_macro_dto = MacroEconomicDTO(
            yield_curve_10y_2y=float(macro_raw.get('T10Y2Y', 0.5)),
            high_yield_oas=float(macro_raw.get('BAMLH0A0HYM2', 3.5)),
            inflation_rate=float(macro_raw.get('CPIAUCSL_YoY', 2.0)),
            nominal_10y=float(macro_raw.get('DGS10', 4.0)),
            vix_value=float(macro_raw.get('VIXCLS', 15.0)),
        )
        await _execute_broker_orders(final_df, effective_dry_run, macro_dto=_broker_macro_dto)
    elif not final_df.empty:
        telemetry.info(
            "ALPACA_API_KEY/SECRET_KEY not configured; skipping broker execution. "
            "Set them in .env to enable live/paper order submission."
        )

    # Write a machine-readable state snapshot for the observability dashboard.
    _write_state_snapshot(macro_raw, final_df, tickers)
    telemetry.info("✅ Master Orchestration finished successfully.")


async def main(dry_run: bool = False):  # --dry-run flag propagated from CLI
    """Master async entry point.  Starts a heartbeat background task and
    always cancels it (even on crash) via try/finally."""
    telemetry.info("🚀 Launching Master Orchestration Routing Hub...")

    # --dry-run: merge CLI arg with settings; either source can enable it.
    effective_dry_run = dry_run or settings.DRY_RUN
    if effective_dry_run:
        telemetry.info("DRY-RUN mode active: orders will be logged but NOT submitted.")

    _hb_task = asyncio.create_task(_heartbeat(settings.OUTPUT_DIR, interval=60))
    try:
        await _main_body(effective_dry_run)
    finally:
        _hb_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _hb_task


if __name__ == "__main__":
    import argparse

    _parser = argparse.ArgumentParser(description="InvestYo Master Orchestrator")
    _parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Log intended orders but do not submit to broker.",
    )
    _args = _parser.parse_args()
    asyncio.run(main(dry_run=_args.dry_run))
