import pandas as pd
from reporting_engine import ReportingEngine

# mock df
df = pd.DataFrame([{
    "Symbol": "AAPL", "Price": 150.0, "Action Signal": "BUY", "Kelly Target": 0.05, 
    "Option Strategy": "None", "buyRange": "145-155",
    "shortName": "Apple", "sector": "Tech", "graham_number": 120.0, "Gordon Fair Value": 130.0,
    "Div Yield": 1.5, "P/E": 25.0, "Book Value": 50.0, "RSI": 60, "Beta": 1.2, 
    "Max Drawdown": -0.15, "VaR 95": -0.05, "Sortino Ratio": 1.5, "Institutional Velocity": 0.1,
    "CoVaR Proxy": 0.5, "Forecast_10": 155, "Forecast_30": 160, "Forecast_90": 170,
    "Strategy Explainer Notes": "Buy it", "position_size": 1000.0, "MFE": 0.05, "MAE": -0.02,
    "BF_Allocation": 0.01, "BF_Selection": 0.02, "Portfolio_Heat": 0.03
}])

engine = ReportingEngine()
engine.generate_daily_report(df, "RISK_ON")
print("Done!")
