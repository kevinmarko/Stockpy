"""
InvestYo Quant Platform - Diagnostics & Visualizations
======================================================
Step 6 of the Modernization Roadmap: Diagnostic and Visualization Deployment.

Provides structured JSON logging telemetry, a Jinja2 template engine for 
HTML report generation, and interactive Plotly volatility bands.
"""

import json
import logging
import os
import numpy as np
import pandas as pd
from datetime import datetime
from typing import List, Dict, Any
import plotly.graph_objects as go
from jinja2 import Template

# =============================================================================
# 1. STRUCTURED JSON TELEMETRY LOGGING
# =============================================================================
class JSONFormatter(logging.Formatter):
    """Formats standard python logs as structured JSON objects for ingestion."""
    def format(self, record):
        log_entry = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "module": record.module,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry)

def setup_telemetry_logger():
    """Initializes and returns the telemetry logger config."""
    logger = logging.getLogger("QuantTelemetry")
    logger.setLevel(logging.INFO)
    
    # Avoid duplicate handlers
    if not logger.handlers:
        ch = logging.StreamHandler()
        formatter = JSONFormatter()
        ch.setFormatter(formatter)
        logger.addHandler(ch)
    return logger

# Initialize the global logger instance
telemetry = setup_telemetry_logger()


# =============================================================================
# 2. PLOTLY VOLATILITY BANDS GENERATION
# =============================================================================
def generate_plotly_volatility_bands(df: pd.DataFrame, ticker: str, output_path: str = "volatility_bands.html"):
    """
    Renders an interactive Plotly chart featuring price close, 20 SMA,
    and 2 Standard Deviation Bollinger Bands.
    """
    telemetry.info(f"Generating interactive Plotly chart for {ticker}")
    
    # Calculate bands
    sma = df['close'].rolling(window=20).mean()
    std = df['close'].rolling(window=20).std()
    upper_band = sma + (std * 2.0)
    lower_band = sma - (std * 2.0)

    fig = go.Figure()

    # Price Close Line
    fig.add_trace(go.Scatter(
        x=df.index, y=df['close'],
        mode='lines',
        name='Close Price',
        line=dict(color='#1f77b4', width=2)
    ))

    # SMA Line
    fig.add_trace(go.Scatter(
        x=df.index, y=sma,
        mode='lines',
        name='20-day SMA',
        line=dict(color='#ff7f0e', width=1.5, dash='dash')
    ))

    # Upper Bollinger Band
    fig.add_trace(go.Scatter(
        x=df.index, y=upper_band,
        mode='lines',
        name='Upper BB (2.0 Std)',
        line=dict(color='rgba(46, 204, 113, 0.4)', width=1)
    ))

    # Lower Bollinger Band
    fig.add_trace(go.Scatter(
        x=df.index, y=lower_band,
        mode='lines',
        name='Lower BB (2.0 Std)',
        line=dict(color='rgba(231, 76, 60, 0.4)', width=1),
        fill='tonexty',
        fillcolor='rgba(200, 200, 200, 0.1)'
    ))

    fig.update_layout(
        title=f'{ticker} Price Volatility Bands (Bollinger)',
        xaxis_title='Date',
        yaxis_title='Price ($)',
        template='plotly_dark',
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01)
    )

    fig.write_html(output_path, include_plotlyjs='cdn')
    telemetry.info(f"Plotly chart saved successfully to {output_path}")
    return output_path


# =============================================================================
# 3. JINJA2 TELEMETRY HTML REPORTS
# =============================================================================
HTML_REPORT_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>InvestYo Quant Platform - Daily Analytical Summary</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #0d1117; color: #c9d1d9; margin: 40px; }
        h1 { color: #58a6ff; border-bottom: 1px solid #30363d; padding-bottom: 10px; }
        .timestamp { color: #8b949e; font-size: 0.9em; margin-bottom: 20px; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; background-color: #161b22; }
        th, td { border: 1px solid #30363d; padding: 12px; text-align: left; }
        th { background-color: #21262d; color: #58a6ff; }
        tr:nth-child(even) { background-color: #1f242c; }
        .badge { display: inline-block; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 0.85em; }
        .recession { background-color: #8c1d1d; color: #ff9e9e; }
        .risk-on { background-color: #1f4d2b; color: #9effaf; }
        .neutral { background-color: #4c4d1f; color: #fffa9e; }
    </style>
</head>
<body>
    <h1>InvestYo Quantitative Report</h1>
    <div class="timestamp">Generated on: {{ current_time }}</div>
    
    <h2>Systemic Regime Signals</h2>
    <p>Current Market State: 
        <span class="badge {% if regime == 'RECESSION' %}recession{% elif regime == 'RISK ON' %}risk-on{% else %}neutral{% endif %}">
            {{ regime }}
        </span>
    </p>

    <h2>Model Output Portfolio Summary</h2>
    <table>
        <thead>
            <tr>
                <th>Ticker</th>
                <th>Closing Price</th>
                <th>RSI</th>
                <th>Beta</th>
                <th>Max Drawdown</th>
            </tr>
        </thead>
        <tbody>
            {% for row in portfolio_data %}
            <tr>
                <td><strong>{{ row.Symbol }}</strong></td>
                <td>${{ "%.2f"|format(row.Price) }}</td>
                <td>{{ "%.1f"|format(row.RSI) }}</td>
                <td>{{ "%.2f"|format(row.Beta) }}</td>
                <td>{{ "%.2f"|format(row.Max_Drawdown * 100) }}%</td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
</body>
</html>
"""

def generate_html_report(portfolio_data: List[Dict[str, Any]], regime: str, output_path: str = "daily_report.html"):
    """
    Renders a clean, styled HTML report using Jinja2 containing
    portfolio statistics and macro regimes.
    """
    telemetry.info("Generating Daily Jinja2 HTML Report")
    
    template = Template(HTML_REPORT_TEMPLATE)
    rendered_html = template.render(
        current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        regime=regime,
        portfolio_data=portfolio_data
    )

    with open(output_path, "w") as f:
        f.write(rendered_html)
        
    telemetry.info(f"Jinja2 report saved successfully to {output_path}")
    return output_path


# =============================================================================
# 4. TESTING ROUTINE
# =============================================================================
if __name__ == '__main__':
    # Telemetry logging test
    telemetry.info("Telemetry system active. Running diagnostic verification.")

    # Generate synthetic price series
    np.random.seed(42)
    dates = pd.date_range(start='2024-01-01', periods=100)
    returns = np.random.normal(0.001, 0.02, len(dates))
    price = 100 * np.exp(np.cumsum(returns))
    df_dummy = pd.DataFrame({'close': price}, index=dates)

    # Generate Interactive Plotly chart
    generate_plotly_volatility_bands(df_dummy, "MOCK")

    # Generate Daily Report
    mock_portfolio = [
        {"Symbol": "AAPL", "Price": 180.50, "RSI": 62.4, "Beta": 1.15, "Max_Drawdown": -0.085},
        {"Symbol": "AGNC", "Price": 9.80, "RSI": 45.1, "Beta": 0.85, "Max_Drawdown": -0.124},
        {"Symbol": "SPY", "Price": 510.20, "RSI": 58.0, "Beta": 1.00, "Max_Drawdown": -0.052}
    ]
    generate_html_report(mock_portfolio, "NEUTRAL")
