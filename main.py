# ==============================================================================
# MODULE: ORCHESTRATOR
# File: main.py
# Description: Coordinates Data -> Processing -> Forecasting -> Strategy.
# ==============================================================================

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


import gspread
import pandas as pd
import numpy as np
import logging 
import time
import os
from datetime import datetime
from gspread_dataframe import set_with_dataframe
from data_engine import DataEngine
from processing_engine import ProcessingEngine
from forecasting_engine import ForecastingEngine
from strategy_engine import StrategyEngine
from reporting_engine import ReportingEngine
from evaluation_engine import EvaluationEngine
from technical_options_engine import TechnicalOptionsEngine
from dto_models import MarketBarDTO, FundamentalDataDTO, MacroEconomicDTO
import config
from settings import settings

SHEET_NAME = "Stock Dashboard Py"
TAB_NAME_INPUT = "Sheet2"
TAB_NAME_OUTPUT = "FidelityData_Automated"
CREDENTIALS_FILE = "credentials.json"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def main():
    print("--- 🚀 STARTING QUANT PIPELINE ---")
    
    # 1. INIT
    try:
        if not os.path.exists(CREDENTIALS_FILE):
             logging.critical(f"❌ Missing {CREDENTIALS_FILE}")
             return

        settings.warn_if_fred_key_leaked(logging.getLogger(__name__))
        settings.ensure_fred_configured()
        de = DataEngine(settings.FRED_API_KEY)
        pe = ProcessingEngine()
        fe = ForecastingEngine()
        se = StrategyEngine()
        ee = EvaluationEngine()
        
        gc = gspread.service_account(filename=CREDENTIALS_FILE)
        sh = gc.open(SHEET_NAME)
        input_ws = sh.worksheet(TAB_NAME_INPUT)
        tickers = [t for t in input_ws.col_values(1)[1:] if t]
        print(f"✅ Found {len(tickers)} tickers.")
        
    except Exception as e:
        logging.critical(f"❌ Init Failure: {e}")
        return

    # 2. DATA
    print("--- 2. EXECUTING DATA ENGINE ---")
    macro_raw = de.fetch_macro_raw()
    fund_raw = de.fetch_fundamentals_raw(tickers)
    tech_raw = de.fetch_technical_raw(list(set(tickers + ["SPY"]))) # This contains HISTORY!


    # Validate raw technical data via MarketDataSchema
    print("⏳ Validating fetched market data schemas...")
    validated_tech_raw = {}
    for ticker, df in tech_raw.items():
        try:
            validated_df = config.MarketDataSchema.validate(df)
            validated_tech_raw[ticker] = validated_df
        except Exception as e:
            logging.error(f"❌ Market data schema validation failed for {ticker}: {e}")
            # Keep the original df but log the issue
            validated_tech_raw[ticker] = df
    tech_raw = validated_tech_raw

    # Convert raw data into DTOs for Object-Oriented mathematical safety
    print("⏳ Transforming raw API outputs to strictly typed DTO models...")
    macro_dto = MacroEconomicDTO(
        yield_curve_10y_2y=macro_raw.get('T10Y2Y', 0.5),
        high_yield_oas=macro_raw.get('BAMLH0A0HYM2', 3.5),
        inflation_rate=macro_raw.get('CPIAUCSL_YoY', 2.0),
        nominal_10y=macro_raw.get('DGS10', 4.0),
        vix_value=macro_raw.get('VIXCLS', 15.0)
    )

    fund_dtos = {}
    if fund_raw:
        first_ticker = list(fund_raw.keys())[0]
        first_info = fund_raw[first_ticker].get('info', {})
        print(f"DEBUG {first_ticker} INFO:", {k: first_info[k] for k in first_info if 'institution' in k.lower() or 'held' in k.lower() or 'debt' in k.lower() or 'equity' in k.lower()})
        try:
            import json
            with open("info_keys.json", "w") as f:
                json.dump(first_info, f, indent=4)
        except Exception as e:
            logging.warning(f"Could not dump info_keys.json: {e}")
    for ticker, data in fund_raw.items():
        if data and 'info' in data:
            # EXPLANATION: Pass raw dividends history to DTO factory to calculate 5-year CAGR.
            fund_dtos[ticker] = FundamentalDataDTO.from_raw_dict(ticker, data['info'], dividends=data.get('dividends'))

    # EXPLANATION: Implements Step 4. Calculates portfolio-wide realized slippage and tail dependency risk (CoVaR Proxy).
    # Load Transactions for Slippage
    try:
        trans_ws = sh.worksheet("Transactions")
        trans_df = pd.DataFrame(trans_ws.get_all_records())
        portfolio_slippage_val = pe.research_engine.calculate_realized_slippage(trans_df)
        # EXPLANATION: Handle float return values directly, keeping a fallback check.
        portfolio_slippage = portfolio_slippage_val if isinstance(portfolio_slippage_val, (int, float)) else portfolio_slippage_val.get("average_slippage_bps", 0.0)
    except Exception as e:
        logging.warning(f"Could not load Transactions sheet for Slippage: {e}")
        portfolio_slippage = 0.0

    # Calculate Returns Matrix for Tail Dependency (CoVaR)
    returns_dict = {}
    for ticker, df in tech_raw.items():
        if not df.empty and 'Close' in df.columns:
            returns_dict[ticker] = df['Close'].pct_change().dropna()
    returns_matrix = pd.DataFrame(returns_dict)
    covar_risk_val = pe.research_engine.calculate_portfolio_covar_dependency(returns_matrix)
    # EXPLANATION: Handle float return values directly, keeping a fallback check.
    covar_risk = covar_risk_val if isinstance(covar_risk_val, (int, float)) else covar_risk_val.get("max_correlation", 1.0)

    # 3. PROCESSING
    print("--- 3. EXECUTING PROCESSING ENGINE ---")
    regime_data = pe.process_macro_regime(macro_dto)
    
    # Load Transactions data from sheet or CSV
    transactions_df = pd.DataFrame()
    try:
        trans_ws = sh.worksheet("Transactions")
        trans_data = trans_ws.get_all_records()
        transactions_df = pd.DataFrame(trans_data)
        print("✅ Loaded Transactions data from Google Sheet.")
    except Exception:
        if os.path.exists("Transactions.csv"):
            try:
                transactions_df = pd.read_csv("Transactions.csv")
                print("✅ Loaded Transactions data from Transactions.csv.")
            except Exception as e:
                logging.warning(f"Could not load Transactions.csv: {e}")

    # Process technical metrics and fundamental metrics
    tech_metrics = pe.calculate_technical_metrics(tech_raw, transactions_df=transactions_df) 
    fund_metrics = pe.calculate_fundamental_metrics(fund_dtos)
    
    dashboard_df = pe.compile_dashboard(tech_metrics, fund_metrics, regime_data)
    # EXPLANATION: Inject portfolio-wide slippage and tail dependency risk (CoVaR Proxy) for all rows.
    if not dashboard_df.empty:
        dashboard_df['Realized Slippage'] = portfolio_slippage
        dashboard_df['CoVaR Proxy'] = covar_risk

    # Instantiate TechnicalOptionsEngine and calculate advanced indicators
    toe = TechnicalOptionsEngine()
    dashboard_df['GARCH_Vol'] = 0.0
    dashboard_df['IVR'] = 0.0
    dashboard_df['Aroon Oscillator'] = 0.0
    dashboard_df['Coppock Curve'] = 0.0
    dashboard_df['Chandelier Exit'] = 0.0
    
    tech_opt_strategies = {}
    tech_opt_indicators = {}
    
    for index, row in dashboard_df.iterrows():
        ticker = row.get('Symbol')
        df_hist = tech_raw.get(ticker)
        if df_hist is not None and not df_hist.empty:
            try:
                indicators = toe.calculate_indicators(df_hist)
                vol = toe.estimate_gjr_garch_volatility(df_hist)
                ivr = toe.calculate_ivr(df_hist, vol)
                opt_strat = toe.generate_option_strategy_matrix(
                    ivr, indicators["Aroon_Oscillator"], indicators["Coppock_Curve"],
                    stock_price=row.get('Price', 100.0), current_iv=vol
                )
                
                dashboard_df.at[index, 'GARCH_Vol'] = vol
                dashboard_df.at[index, 'IVR'] = ivr
                dashboard_df.at[index, 'Aroon Oscillator'] = indicators["Aroon_Oscillator"]
                dashboard_df.at[index, 'Coppock Curve'] = indicators["Coppock_Curve"]
                dashboard_df.at[index, 'Chandelier Exit'] = indicators["Chandelier_Long"]
                tech_opt_strategies[ticker] = opt_strat
                tech_opt_indicators[ticker] = indicators
            except Exception as e:
                logging.error(f"Technical indicators/options calc failed for {ticker}: {e}")

    if "SPY" not in tickers and not dashboard_df.empty:
        dashboard_df = dashboard_df[dashboard_df['Symbol'] != "SPY"]
    print(f"✅ Compiled baseline data for {len(dashboard_df)} tickers.")

    # 4. FORECASTING
    print("--- 4. EXECUTING FORECASTING ENGINE ---")
    forecast_cols = ['Target_Days', 'ARIMA', 'MC_Target', 'MC_Lower', 'MC_Upper',
                     'Forecast_10', 'Forecast_30', 'Forecast_60', 'Forecast_90',
                     'Forecast_30_Prophet_Lower', 'Forecast_30_Prophet_Upper']
    
    for col in forecast_cols: dashboard_df[col] = 0.0

    print(f"⏳ Generating forecasts...")
    for index, row in dashboard_df.iterrows():
        try:
            ticker = row.get('Symbol')
            price = row.get('Price')
            
            if not price or price == 0: continue

            # Retrieve History from tech_raw dictionary
            history_df = tech_raw.get(ticker)
            history_series = history_df['Close'] if history_df is not None else None

            # Pass history to engine so ARIMA/HW/CNN-LSTM can run
            forecasts = fe.generate_forecast(row, price, history_series, history_df=history_df)
            
            # Map results
            for key in forecast_cols:
                dashboard_df.at[index, key] = forecasts.get(key, 0)
            
        except Exception as e:
            logging.error(f"Forecasting failed for {ticker}: {e}")

    # 5. STRATEGY EVALUATION
    print("--- 5. EXECUTING STRATEGY ENGINE ---")
    strategy_cols = ['Action Signal', 'Advice', 'Actionable Advice Signal', 'Kelly Target', 'Option Strategy', 'buyRange', 'Strategy Explainer Notes']
    for col in strategy_cols:
        dashboard_df[col] = ""
    dashboard_df['Kelly Target'] = 0.0

    # Pre-allocate trackers for newly mapped columns
    for col in ['Aroon Oscillator', 'Coppock Curve', 'Chandelier Exit', 'Edge Ratio']:
        if col not in dashboard_df.columns:
            dashboard_df[col] = 0.0

    for index, row in dashboard_df.iterrows():
        try:
            ticker = row.get('Symbol')
            price = row.get('Price')
            if not price or price == 0: continue

            # Construct MarketBarDTO
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
                    volume=latest_row.get('Volume', 0)
                )
            else:
                bar_dto = MarketBarDTO(datetime.now(), ticker, price, price, price, price, 0)

            # Get FundamentalDataDTO
            fund_dto = fund_dtos.get(ticker)
            if fund_dto is None:
                fund_dto = FundamentalDataDTO(
                    ticker=ticker, pe_ratio=None, pb_ratio=None, dividend_yield=0.0,
                    book_value=0.0, eps_trailing=0.0, dividend_growth_rate=0.0,
                    payout_ratio=0.0, sector="Unknown", company_name="Unknown"
                )

            # Evaluate strategy rules
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
                chan_long = tech_opt_indicators[ticker].get("Chandelier_Long", 0.0)
                chan_short = tech_opt_indicators[ticker].get("Chandelier_Short", 0.0)

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

            # Map strategy fields
            dashboard_df.at[index, 'Action Signal'] = strategy_output['Action Signal']
            dashboard_df.at[index, 'Advice'] = strategy_output['Advice']
            dashboard_df.at[index, 'Actionable Advice Signal'] = strategy_output['Actionable Advice Signal']
            dashboard_df.at[index, 'Kelly Target'] = float(strategy_output['Kelly Target'])
            dashboard_df.at[index, 'is_dividend_sustainable'] = int(fund_dto.is_dividend_sustainable)
            dashboard_df.at[index, 'eps_trailing'] = fund_dto.eps_trailing
            dashboard_df.at[index, 'book_value'] = fund_dto.book_value
            dashboard_df.at[index, 'graham_number'] = fund_dto.graham_number
            if ticker in tech_opt_strategies:
                dashboard_df.at[index, 'Option Strategy'] = tech_opt_strategies[ticker]
            else:
                dashboard_df.at[index, 'Option Strategy'] = strategy_output['Option Strategy']
            dashboard_df.at[index, 'buyRange'] = strategy_output['buyRange']
            dashboard_df.at[index, 'Strategy Explainer Notes'] = strategy_output['Strategy Explainer Notes']

            # Calculate Edge Ratio on historical holding segment of last 15 active trading days
            edge_ratio_val = 0.0
            if history_df is not None and len(history_df) >= 20:
                holding_period = history_df.iloc[-15:]
                entry_d = holding_period.index[0]
                exit_d = holding_period.index[-1]
                trade_entry_p = float(holding_period['Close'].iloc[0])
                
                edge_ratio_res = ee.calculate_edge_ratio(history_df, trade_entry_p, entry_d, exit_d)
                edge_ratio_val = float(edge_ratio_res.get("Edge Ratio", 0.0))
            
            dashboard_df.at[index, 'Edge Ratio'] = edge_ratio_val

        except Exception as e:
            logging.error(f"Strategy evaluation failed for {ticker}: {e}")

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

    if not dashboard_df.empty:
        dashboard_df = ee.evaluate_portfolio(dashboard_df, benchmark_df)

    # CRITICAL FIX 4: Eradicate NaNs before Google Sheets Export
    export_keys = ['MAE', 'MFE', 'Portfolio_Heat', 'BF_Allocation', 'BF_Selection']
    for key in export_keys:
        if key in dashboard_df.columns:
            dashboard_df[key] = dashboard_df[key].fillna(0.0)
        else:
            dashboard_df[key] = 0.0

    # Validate compile dashboard dataframe structure
    if not dashboard_df.empty:
        try:
            dashboard_df = config.DashboardSchema.validate(dashboard_df)
            print("✅ Consolidated dashboard schema validation passed.")
        except Exception as e:
            logging.error(f"❌ Consolidated dashboard schema validation failed: {e}")

    # 6. EXPORT
    print("--- 6. PREPARING EXPORT SCHEMA ---")
    rename_map = config.get_rename_mapping()
    export_df = dashboard_df.rename(columns=rename_map)
    final_headers = config.get_headers()
    
    for h in final_headers:
        if h not in export_df.columns: export_df[h] = 0
            
    export_df = export_df[final_headers]
    export_df = export_df.replace([np.inf, -np.inf], 0).fillna(0)
    
    # DEBUG DUMPS
    dashboard_df.to_csv("dashboard_df_debug.csv", index=False)
    export_df.to_csv("export_df_debug.csv", index=False)
    transactions_df.to_csv("transactions_df_debug.csv", index=False)
    
    # 7. WRITE
    print(f"--- 7. WRITING TO SHEET ---")
    try:
        try: output_ws = sh.worksheet(TAB_NAME_OUTPUT)
        except: output_ws = sh.add_worksheet(title=TAB_NAME_OUTPUT, rows=100, cols=len(final_headers))
        
        set_with_dataframe(output_ws, export_df, row=1, col=1, include_column_header=True, resize=True)
        
        # EXPLANATION: Apply conditional formatting rules to make columns visually intuitive.
        try:
            headers = list(export_df.columns)
            sheet_id = output_ws.id
            cf_requests = []

            # Step 1: Dividend Payback Horizon Color Scale (Green low < 8, turning to Red high > 15)
            if "Dividend Payback Horizon" in headers:
                dph_idx = headers.index("Dividend Payback Horizon")
                cf_requests.append({
                    "addConditionalFormatRule": {
                        "rule": {
                            "ranges": [{"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 1000, "startColumnIndex": dph_idx, "endColumnIndex": dph_idx + 1}],
                            "gradientRule": {
                                "minpoint": {"color": {"red": 0.85, "green": 0.95, "blue": 0.85}, "type": "NUMBER", "value": "8"},
                                "midpoint": {"color": {"red": 1.0, "green": 1.0, "blue": 0.85}, "type": "NUMBER", "value": "11.5"},
                                "maxpoint": {"color": {"red": 0.95, "green": 0.85, "blue": 0.85}, "type": "NUMBER", "value": "15"}
                            }
                        },
                        "index": 0
                    }
                })

            # Step 2: Leverage Warning Lights (Light Red Fill if < 0.3)
            if "Leverage Distress Factor" in headers:
                leverage_idx = headers.index("Leverage Distress Factor")
                cf_requests.append({
                    "addConditionalFormatRule": {
                        "rule": {
                            "ranges": [{"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 1000, "startColumnIndex": leverage_idx, "endColumnIndex": leverage_idx + 1}],
                            "booleanRule": {
                                "condition": {"type": "NUMBER_LESS", "values": [{"userEnteredValue": "0.3"}]},
                                "format": {"backgroundColor": {"red": 0.98, "green": 0.82, "blue": 0.82}}
                            }
                        },
                        "index": 0
                    }
                })

            # Step 3: Options Edge Identifier (Light Green Fill if > 0.0)
            if "Options IV Edge" in headers:
                options_idx = headers.index("Options IV Edge")
                cf_requests.append({
                    "addConditionalFormatRule": {
                        "rule": {
                            "ranges": [{"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 1000, "startColumnIndex": options_idx, "endColumnIndex": options_idx + 1}],
                            "booleanRule": {
                                "condition": {"type": "NUMBER_GREATER", "values": [{"userEnteredValue": "0.0"}]},
                                "format": {"backgroundColor": {"red": 0.85, "green": 0.95, "blue": 0.85}}
                            }
                        },
                        "index": 0
                    }
                })

            if cf_requests:
                sh.batch_update({"requests": cf_requests})
                print("✅ Conditional formatting rules applied to Google Sheet.")
        except Exception as fe:
            logging.warning(f"Could not apply conditional formatting rules: {fe}")

        print("✅ Dashboard Updated Successfully.")
        
        # 8. GENERATE DAILY HTML REPORT
        try:
            print("--- 8. GENERATING HTML REPORT ---")
            from diagnostics_and_visuals import generate_html_report
            portfolio_dicts = dashboard_df.to_dict(orient="records")
            # Map "Max Drawdown" to "Max_Drawdown" for template compatibility
            for row in portfolio_dicts:
                if "Max Drawdown" in row:
                    row["Max_Drawdown"] = row["Max Drawdown"]
            generate_html_report(
                portfolio_dicts, 
                regime_data.get('Regime', 'NEUTRAL'), 
                "daily_report.html",
                yield_curve=float(macro_dto.yield_curve),
                credit_spread=float(macro_dto.credit_spread),
                sahm_rule=float(macro_dto.sahm_rule_indicator),
                real_yield=float(macro_dto.real_yield)
            )
            print("✅ Successfully generated dynamic daily report at daily_report.html")
        except Exception as re_err:
            logging.error(f"❌ Failed to generate HTML report: {re_err}")
        
    except Exception as e:
        logging.critical(f"❌ Write Failed: {e}")

if __name__ == "__main__":
    main()