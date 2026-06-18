# ==============================================================================
# MODULE: ORCHESTRATOR
# File: main.py
# Description: Coordinates Data -> Processing -> Forecasting using DTOs.
# ==============================================================================

import gspread
import pandas as pd
import numpy as np
import logging 
import time
import os
from gspread_dataframe import set_with_dataframe
from data_engine import DataEngine
from processing_engine import ProcessingEngine
from forecasting_engine import ForecastingEngine
from dto_models import FundamentalDataDTO, MacroEconomicDTO
import config

SHEET_NAME = "Stock Dashboard Py"
TAB_NAME_INPUT = "Sheet2"
TAB_NAME_OUTPUT = "FidelityData_Automated"
FRED_KEY = "38e72b904fd5fbd3a3a40805c4e6086d"
CREDENTIALS_FILE = "credentials.json"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def main():
    print("--- 🚀 STARTING QUANT PIPELINE (WITH DTOs) ---")
    
    # 1. INIT
    try:
        if not os.path.exists(CREDENTIALS_FILE):
             logging.critical(f"❌ Missing {CREDENTIALS_FILE}")
             return

        de = DataEngine(FRED_KEY)
        pe = ProcessingEngine(data_provider=de)
        fe = ForecastingEngine()
        
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
    tech_raw = de.fetch_technical_raw(tickers) # This contains HISTORY!

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

    # 3. PROCESSING
    print("--- 3. EXECUTING PROCESSING ENGINE ---")
    regime_data = pe.process_macro_regime(macro_raw)
    
    # Process technical metrics and fundamental metrics
    tech_metrics = pe.calculate_technicals_vectorized(tech_raw) 
    fund_metrics = pe.calculate_fundamentals(fund_raw)
    
    dashboard_df = pe.compile_dashboard(tech_metrics, fund_metrics, regime_data)
    print(f"✅ Compiled baseline data for {len(dashboard_df)} tickers.")

    # Validate compile dashboard dataframe structure
    if not dashboard_df.empty:
        try:
            dashboard_df = config.DashboardSchema.validate(dashboard_df)
            print("✅ Consolidated dashboard schema validation passed.")
        except Exception as e:
            logging.error(f"❌ Consolidated dashboard schema validation failed: {e}")

    # 4. FORECASTING
    print("--- 4. EXECUTING FORECASTING ENGINE ---")
    forecast_cols = ['Target_Days', 'ARIMA', 'MC_Target', 'MC_Lower', 'MC_Upper',
                     'Forecast_10', 'Forecast_30', 'Forecast_60', 'Forecast_90']
    
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

            # Pass history to engine so ARIMA/HW can run
            forecasts = fe.generate_forecast(row, price, history_series)
            
            # Map results
            for key in forecast_cols:
                dashboard_df.at[index, key] = forecasts.get(key, 0)
            
        except Exception as e:
            logging.error(f"Forecasting failed for {ticker}: {e}")

    # 5. EXPORT
    print("--- 5. PREPARING EXPORT SCHEMA ---")
    rename_map = config.get_rename_mapping()
    export_df = dashboard_df.rename(columns=rename_map)
    final_headers = config.get_headers()
    
    for h in final_headers:
        if h not in export_df.columns: export_df[h] = 0
            
    export_df = export_df[final_headers]
    export_df = export_df.replace([np.inf, -np.inf], 0).fillna(0)
    
    # 6. WRITE
    print(f"--- 6. WRITING TO SHEET ---")
    try:
        try: output_ws = sh.worksheet(TAB_NAME_OUTPUT)
        except: output_ws = sh.add_worksheet(title=TAB_NAME_OUTPUT, rows=100, cols=len(final_headers))
        
        output_ws.clear()
        set_with_dataframe(output_ws, export_df, row=1, col=1, include_column_header=True, resize=True)
        print("✅ Dashboard Updated Successfully.")
        
    except Exception as e:
        logging.critical(f"❌ Write Failed: {e}")

if __name__ == "__main__":
    main()