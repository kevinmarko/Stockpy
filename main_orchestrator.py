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


import os
import sys
import json
import logging
import asyncio
import pandas as pd
import numpy as np
from datetime import datetime, timezone

# Core imports
import config
from data_engine import DataEngine, MockDataEngine
from processing_engine import ProcessingEngine
from macro_engine import MacroEngine
from technical_options_engine import TechnicalOptionsEngine
from forecasting_engine import ForecastingEngine
from strategy_engine import StrategyEngine
from evaluation_engine import EvaluationEngine
from dto_models import MarketBarDTO, FundamentalDataDTO, MacroEconomicDTO
from diagnostics_and_visuals import (
    telemetry, 
    generate_plotly_volatility_bands, 
    generate_html_report
)

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("MasterOrchestrator")

FRED_KEY = "38e72b904fd5fbd3a3a40805c4e6086d"
DEFAULT_TICKERS = ["AAPL", "MSFT", "JNJ", "AGNC"]


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


def run_pipeline(tickers: list, macro_raw: dict, fund_raw: dict, tech_raw: dict) -> pd.DataFrame:
    """
    Synchronous execution of the quantitative engines:
    Macro -> Technical Options -> Processing -> Forecasting -> Strategy & Evaluation.
    """
    # 1. Macro Economic Regime Analysis
    telemetry.info("Routing data through Macro Engine...")
    me = MacroEngine(data_engine=None)  # Pass None to prevent redundant calls
    sahm_val = me._fallback_sentiment("")  # Default sahm value proxy
    macro_data = me.run_macro_killswitch(macro_raw, sahm_val)
    market_regime = macro_data["market_regime"].iloc[0]

    macro_dto = MacroEconomicDTO(
        yield_curve_10y_2y=float(macro_raw.get('T10Y2Y', 0.5)),
        high_yield_oas=float(macro_raw.get('BAMLH0A0HYM2', 3.5)),
        inflation_rate=float(macro_raw.get('CPIAUCSL_YoY', 2.0)),
        nominal_10y=float(macro_raw.get('DGS10', 4.0)),
        vix_value=float(macro_raw.get('VIXCLS', 15.0))
    )

    # 2. Technical Options Analysis
    telemetry.info("Routing data through Technical Options Engine...")
    toe = TechnicalOptionsEngine()
    tech_opt_indicators = {}
    for ticker in tickers:
        df_hist = tech_raw.get(ticker)
        if df_hist is not None and not df_hist.empty:
            indicators = toe.calculate_indicators(df_hist)
            vol = toe.estimate_gjr_garch_volatility(df_hist)
            ivr = toe.calculate_ivr(df_hist, vol)
            price_val = float(df_hist['Close'].iloc[-1]) if df_hist is not None and not df_hist.empty else 100.0
            opt_strategy = toe.generate_option_strategy_matrix(
                ivr, indicators["Aroon_Oscillator"], indicators["Coppock_Curve"],
                stock_price=price_val, current_iv=vol
            )
            tech_opt_indicators[ticker] = {
                "Aroon_Oscillator": indicators["Aroon_Oscillator"],
                "Coppock_Curve": indicators["Coppock_Curve"],
                "Chandelier_Long": indicators["Chandelier_Long"],
                "Chandelier_Short": indicators["Chandelier_Short"],
                "GARCH_Vol": vol,
                "IVR": ivr,
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

    fund_metrics = pe.calculate_fundamental_metrics(fund_dtos)
    dashboard_df = pe.compile_dashboard(tech_metrics, fund_metrics, regime_metrics)

    # Explicitly map GARCH_Vol, IVR, and advanced indicators from tech_opt_indicators to dashboard_df
    dashboard_df['GARCH_Vol'] = 0.0
    dashboard_df['IVR'] = 0.0
    dashboard_df['Aroon Oscillator'] = 0.0
    dashboard_df['Coppock Curve'] = 0.0
    dashboard_df['Chandelier Exit'] = 0.0
    for idx, row in dashboard_df.iterrows():
        ticker = row['Symbol']
        if ticker in tech_opt_indicators:
            dashboard_df.at[idx, 'GARCH_Vol'] = tech_opt_indicators[ticker].get('GARCH_Vol', 0.0)
            dashboard_df.at[idx, 'IVR'] = tech_opt_indicators[ticker].get('IVR', 0.0)
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

    # 5. Strategy & Sizing Evaluations
    telemetry.info("Routing data through Strategy and Evaluation Engines...")
    se = StrategyEngine()
    ee = EvaluationEngine()
    
    strategy_cols = ['Action Signal', 'Advice', 'Actionable Advice Signal', 'Kelly Target', 'Option Strategy', 'buyRange', 'Strategy Explainer Notes']
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
            chandelier_short=chan_short
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
        
        # Win-probability Kelly calculation
        kelly_dict = ee.calculate_kelly_target(
            expected_return=0.0, variance=0.0,
            win_probability=0.55 + (float(strategy_output['Score']) / 100.0) * 0.35,
            win_loss_ratio=2.0, half_kelly=True
        )
        kelly_val = kelly_dict["Kelly Target"]
        
        # Enforce maximum allocation limits based on score, trend, and edge ratio
        final_score_val = float(strategy_output['Score'])
        is_uptrend_val = (aroon_osc_val >= 50.0) if aroon_osc_val is not None else (aroon_val >= 50.0)
        
        if final_score_val >= 75.0 and sortino_val > 1.0 and edge_ratio_val >= 1.0:
            max_allocation_limit = 0.25
        elif final_score_val >= 55.0:
            max_allocation_limit = 0.15 if is_uptrend_val else 0.05
        elif final_score_val >= 35.0:
            max_allocation_limit = 0.05
        else:
            max_allocation_limit = 0.00
            
        dashboard_df.at[idx, 'Kelly Target'] = float(max(0.0, min(kelly_val, max_allocation_limit)))
        if ticker in tech_opt_indicators:
            dashboard_df.at[idx, 'Option Strategy'] = tech_opt_indicators[ticker].get('Option_Strategy_Matrix', '')
        else:
            dashboard_df.at[idx, 'Option Strategy'] = strategy_output['Option Strategy']
        dashboard_df.at[idx, 'buyRange'] = strategy_output['buyRange']
        dashboard_df.at[idx, 'Strategy Explainer Notes'] = strategy_output['Strategy Explainer Notes']

    # CRITICAL FIX 1: Map 'Avg Cost' to 'Entry_Price' for MFE/MAE
    if 'Avg Cost' in dashboard_df.columns and 'Entry_Price' not in dashboard_df.columns:
        dashboard_df['Entry_Price'] = dashboard_df['Avg Cost']
    elif 'Entry_Price' not in dashboard_df.columns:
        dashboard_df['Entry_Price'] = dashboard_df['Price'] # Fallback proxy to prevent NaNs

    # Ensure High/Low exist for excursion calculations
    if 'Price' in dashboard_df.columns:
        if 'High' not in dashboard_df.columns: dashboard_df['High'] = dashboard_df['Price'] * 1.05
        if 'Low' not in dashboard_df.columns: dashboard_df['Low'] = dashboard_df['Price'] * 0.95

    # CRITICAL FIX 2: Map 'Shares' to 'position_size' for Portfolio Heat
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

    dashboard_df = ee.evaluate_portfolio(dashboard_df, benchmark_df)

    # CRITICAL FIX 4: Eradicate NaNs before Google Sheets Export
    export_keys = ['MAE', 'MFE', 'Portfolio_Heat', 'BF_Allocation', 'BF_Selection']
    for key in export_keys:
        if key in dashboard_df.columns:
            dashboard_df[key] = dashboard_df[key].fillna(0.0)
        else:
            dashboard_df[key] = 0.0

    return dashboard_df


async def main():
    telemetry.info("🚀 Launching Master Orchestration Routing Hub...")
    
    # Initialize real or mock data engine based on credentials
    creds_exist = os.path.exists("credentials.json")
    if creds_exist:
        de = DataEngine(FRED_KEY)
        tickers = DEFAULT_TICKERS
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
        mock_de = MockDataEngine()
        macro_raw = mock_de.fetch_macro_raw()
        fund_raw = mock_de.fetch_fundamentals_raw(tickers)
        tech_raw = mock_de.fetch_technical_raw(tickers)

    # 2. Run Pipeline
    try:
        final_df = run_pipeline(tickers, macro_raw, fund_raw, tech_raw)
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
        # Determine sandbox-compliant output directory
        out_dir = "/Users/kevinlee/.gemini/antigravity-ide/brain/838d8722-86ff-4060-b1f4-a8547d2cde16"
        if not os.path.exists(out_dir):
            out_dir = "."

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
        output_payload = final_df[["Symbol", "Price", "Action Signal", "buyRange", "Kelly Target", "Option Strategy", "GARCH_Vol", "IVR"]].to_dict(orient="records")
        print("\n=== FINAL ACTIONABLE PAYLOAD REPRESENTATION ===")
        print(json.dumps(output_payload, indent=4))
        print("================================================\n")
    
    telemetry.info("✅ Master Orchestration finished successfully.")


if __name__ == "__main__":
    asyncio.run(main())
