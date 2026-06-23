import asyncio
import os
import json
import pandas as pd
import numpy as np
from datetime import datetime
import config
from data_engine import MockDataEngine
from main_orchestrator import run_pipeline
from diagnostics_and_visuals import HTML_REPORT_TEMPLATE
from jinja2 import Template

async def main():
    de = MockDataEngine()
    tickers = ["AAPL", "MSFT", "JNJ", "AGNC"]
    
    macro_raw = de.fetch_macro_raw()
    fund_raw = de.fetch_fundamentals_raw(tickers)
    tech_raw = de.fetch_technical_raw(tickers)
    
    final_df = run_pipeline(tickers, macro_raw, fund_raw, tech_raw)
    
    portfolio_dicts = final_df.to_dict(orient="records")
    for row in portfolio_dicts:
        if "Max Drawdown" in row:
            row["Max_Drawdown"] = row["Max Drawdown"]
    regime_val = final_df["Macro Status"].iloc[0] if "Macro Status" in final_df.columns else "NEUTRAL"
    
    template = Template(HTML_REPORT_TEMPLATE)
    rendered_html = template.render(
        current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        regime=regime_val,
        portfolio_data=portfolio_dicts
    )
    
    print("---HTML_START---")
    print(rendered_html)
    print("---HTML_END---")

if __name__ == "__main__":
    asyncio.run(main())
