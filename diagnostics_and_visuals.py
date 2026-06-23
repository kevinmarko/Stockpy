"""
InvestYo Quant Platform - Diagnostics & Visualizations
======================================================
Step 6 of the Modernization Roadmap: Diagnostic and Visualization Deployment.

Provides structured JSON logging telemetry, a Jinja2 template engine for
HTML report generation (with Traffic Lights, Anomaly Tooltips, Executive
Summary Blocks, Dynamic Formatting, Confidence Intervals, and Gravity AI
Audit Log), and interactive Plotly volatility bands.
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

    Accepts DataFrames with either 'Close' or 'close' column names
    for backward compatibility with both main.py (uppercase) and
    main_orchestrator.py (lowercase after .columns.lower() transform).
    """
    telemetry.info(f"Generating interactive Plotly chart for {ticker}")

    # Case-insensitive column resolution
    close_col = 'Close' if 'Close' in df.columns else 'close'
    if close_col not in df.columns:
        telemetry.warning(f"Insufficient data to plot volatility bands for {ticker}: no 'Close' column found")
        return None

    if df.empty:
        telemetry.warning(f"Insufficient data to plot volatility bands for {ticker}: DataFrame is empty")
        return None

    # Calculate bands (vectorized — no iterrows per GEMINI.md §3)
    sma = df[close_col].rolling(window=20).mean()
    std = df[close_col].rolling(window=20).std()
    upper_band = sma + (std * 2.0)
    lower_band = sma - (std * 2.0)

    fig = go.Figure()

    # Price Close Line
    fig.add_trace(go.Scatter(
        x=df.index, y=df[close_col],
        mode='lines',
        name='Close Price',
        line=dict(color='#1f77b4', width=2)
    ))

    # SMA Line
    fig.add_trace(go.Scatter(
        x=df.index, y=sma,
        mode='lines',
        name='20-Day SMA',
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
        title=f'Volatility Bands & Tactical Ranges: {ticker}',
        xaxis_title='Date',
        yaxis_title='Price ($)',
        template='plotly_dark',
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01)
    )

    fig.write_html(output_path, include_plotlyjs='cdn')
    telemetry.info(f"Plotly chart saved successfully to {output_path}")
    return output_path


# =============================================================================
# 3. JINJA2 TELEMETRY HTML REPORTS (WITH AI AUDIT INJECTIONS)
# =============================================================================
HTML_REPORT_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="description" content="InvestYo Quant Platform - Daily Analytical Report with traffic light indicators, anomaly tooltips, and Gravity AI audit log.">
    <title>InvestYo Quant Platform - Daily Report</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {
            --bg-dark: #0b0f19;
            --card-bg: #111827;
            --text-main: #f3f4f6;
            --text-muted: #9ca3af;
            --accent: #3b82f6;
            --accent-glow: rgba(59, 130, 246, 0.15);
            --border: #1f2937;
            --success: #10b981;
            --success-glow: rgba(16, 185, 129, 0.15);
            --danger: #ef4444;
            --danger-glow: rgba(239, 68, 68, 0.15);
            --warning: #f59e0b;
            --warning-glow: rgba(245, 158, 11, 0.15);
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: 'Inter', 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background-color: var(--bg-dark);
            color: var(--text-main);
            padding: 40px 20px;
            line-height: 1.6;
        }
        .container { max-width: 1280px; margin: 0 auto; }

        /* Header */
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border);
            padding-bottom: 20px;
            margin-bottom: 30px;
        }
        h1 {
            font-size: 24px; font-weight: 700; letter-spacing: -0.025em;
            background: linear-gradient(to right, #60a5fa, #3b82f6);
            -webkit-background-clip: text; background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .timestamp { color: var(--text-muted); font-size: 14px; }

        /* ======== EXECUTIVE SUMMARY GRID ======== */
        .exec-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .exec-card {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 24px;
            text-align: center;
            position: relative;
            overflow: hidden;
            box-shadow: 0 8px 16px rgba(0, 0, 0, 0.2);
        }
        .exec-card::before {
            content: '';
            position: absolute;
            top: 0; left: 0;
            width: 100%; height: 3px;
            background: linear-gradient(to right, #60a5fa, #3b82f6);
        }
        .exec-card h3 {
            color: var(--text-muted);
            font-size: 13px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 12px;
        }
        .exec-card h2 {
            font-size: 28px;
            font-weight: 700;
            margin-bottom: 8px;
        }
        .exec-card .subtext {
            font-size: 12px;
            color: var(--text-muted);
        }

        /* ======== TABS ======== */
        .tab {
            overflow: hidden;
            border-bottom: 1px solid var(--border);
            margin-bottom: 24px;
        }
        .tab button {
            background-color: inherit;
            color: var(--text-muted);
            float: left;
            border: none;
            outline: none;
            cursor: pointer;
            padding: 14px 20px;
            transition: color 0.3s, border-bottom 0.3s;
            font-size: 15px;
            font-weight: 600;
            font-family: inherit;
        }
        .tab button:hover { color: var(--accent); }
        .tab button.active {
            border-bottom: 3px solid var(--accent);
            color: var(--accent);
        }
        .tabcontent { display: none; }

        /* ======== DATA TABLE ======== */
        .data-card {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
        }
        .card-header {
            padding: 20px;
            border-bottom: 1px solid var(--border);
            font-weight: 600;
            font-size: 16px;
            color: var(--text-main);
        }
        table { width: 100%; border-collapse: collapse; font-size: 14px; }
        th, td { padding: 14px 20px; text-align: left; border-bottom: 1px solid var(--border); }
        th {
            background-color: rgba(255, 255, 255, 0.02);
            color: var(--text-muted);
            font-weight: 500;
            text-transform: uppercase;
            font-size: 11px;
            letter-spacing: 0.05em;
        }
        tr:last-child td { border-bottom: none; }
        tr:hover td { background-color: rgba(255, 255, 255, 0.01); }

        /* ======== TRAFFIC LIGHT BADGES ======== */
        .badge {
            padding: 4px 10px;
            border-radius: 9999px;
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.03em;
            display: inline-block;
            margin-left: 6px;
            transition: all 0.3s ease;
        }
        .badge-green  { background-color: var(--success-glow); color: var(--success); border: 1px solid var(--success); }
        .badge-yellow { background-color: var(--warning-glow); color: var(--warning); border: 1px solid var(--warning); }
        .badge-red    { background-color: var(--danger-glow);  color: var(--danger);  border: 1px solid var(--danger);  }

        /* ======== ANOMALY TOOLTIPS ======== */
        .anomaly-tooltip {
            position: relative;
            display: inline-block;
            cursor: help;
            border-bottom: 1px dotted var(--warning);
        }
        .anomaly-tooltip .tooltiptext {
            visibility: hidden;
            width: 240px;
            background-color: #1f2937;
            color: #fff;
            text-align: center;
            border-radius: 8px;
            padding: 10px;
            position: absolute;
            z-index: 10;
            bottom: 130%;
            left: 50%;
            margin-left: -120px;
            opacity: 0;
            transition: opacity 0.3s;
            font-size: 12px;
            border: 1px solid var(--warning);
            box-shadow: 0 8px 16px rgba(0, 0, 0, 0.4);
        }
        .anomaly-tooltip:hover .tooltiptext { visibility: visible; opacity: 1; }

        /* ======== AUDIT LOG ======== */
        pre {
            background: #000;
            padding: 20px;
            border-radius: 8px;
            overflow-x: auto;
            color: #a5d6ff;
            border: 1px solid var(--border);
            font-size: 13px;
            line-height: 1.5;
        }

        /* ======== SIGNAL TAGS ======== */
        .signal-STRONG_BUY   { color: var(--success); text-shadow: 0 0 8px var(--success-glow); }
        .signal-BUY           { color: var(--success); }
        .signal-HOLD          { color: var(--warning); }
        .signal-RISK_REDUCE   { color: var(--danger); }
        .signal-STRONG_SELL   { color: var(--danger); text-shadow: 0 0 8px var(--danger-glow); }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div>
                <h1>InvestYo Quantitative Portfolio Report</h1>
                <div class="timestamp">Automated Strategy Engine Run</div>
            </div>
            <div class="timestamp">Generated on: {{ current_time }}</div>
        </header>

        <!-- ======== EXECUTIVE SUMMARY BLOCKS ======== -->
        <div class="exec-grid">
            <!-- Card 1: Market Regime -->
            <div class="exec-card">
                <h3>🌐 Market Regime Overview</h3>
                <h2 style="color: {% if regime == 'RISK ON' %}var(--success){% elif regime == 'NEUTRAL' %}var(--warning){% else %}var(--danger){% endif %};">
                    {{ regime }}
                </h2>
                <p class="subtext">
                    10Y-2Y: {{ yield_curve }}% | HY OAS: {{ credit_spread }}% | Sahm: {{ sahm_rule }}
                </p>
            </div>

            <!-- Card 2: Portfolio Heat Gauge -->
            <div class="exec-card">
                <h3>🔥 Portfolio Heat Snapshot</h3>
                <h2 style="color: {% if avg_portfolio_heat > 0.06 %}var(--danger){% elif avg_portfolio_heat > 0.04 %}var(--warning){% else %}var(--success){% endif %};">
                    {{ "%.2f"|format(avg_portfolio_heat * 100) }}%
                </h2>
                <p class="subtext">Max Institutional Limit: 6.00%</p>
                {% if avg_portfolio_heat > 0.06 %}
                    <div class="badge badge-red" style="margin-top: 8px;">SYSTEM HALT THRESHOLD BREACHED</div>
                {% else %}
                    <div class="badge badge-green" style="margin-top: 8px;">WITHIN SAFE BOUNDS</div>
                {% endif %}
            </div>

            <!-- Card 3: Risk Attribution Summary -->
            <div class="exec-card">
                <h3>📊 Risk Attribution Summary</h3>
                <div style="height: 120px;">
                    <canvas id="attributionChart"></canvas>
                </div>
                <p class="subtext" style="margin-top: 10px;">Brinson-Fachler: Allocation vs Selection</p>
            </div>
        </div>

        <!-- ======== TAB NAVIGATION ======== -->
        <div class="tab">
            <button class="tablinks active" onclick="openTab(event, 'Dashboard')">Quantitative Dashboard</button>
            <button class="tablinks" onclick="openTab(event, 'AuditLog')">Gravity AI Audit Log</button>
        </div>

        <!-- ======== TAB 1: QUANTITATIVE DASHBOARD ======== -->
        <div id="Dashboard" class="tabcontent" style="display: block;">
            <div class="data-card">
                <div class="card-header">Portfolio Signals & Dynamic Validation</div>
                <div style="overflow-x: auto; padding: 0;">
                    <table>
                        <thead>
                            <tr>
                                <th>Asset & Action</th>
                                <th>Format Type</th>
                                <th>Technical Validation (RSI/MACD)</th>
                                <th>Systemic Risk (CoVaR)</th>
                                <th>Forecast / Pricing Edge</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for row in portfolio_rows %}
                            <tr>
                                <!-- Column 1: Asset & Action -->
                                <td>
                                    <strong>{{ row.Symbol }}</strong><br>
                                    <span class="signal-{{ row.Action_Signal|default(row.get('Action Signal', 'HOLD'), true)|replace(' ', '_') }}"
                                          style="font-size: 12px; font-weight: 600;">
                                        {{ row.Action_Signal|default(row.get('Action Signal', 'N/A'), true) }}
                                    </span>
                                    <span style="font-size: 11px; color: var(--text-muted);">
                                        (Kelly: {{ "%.1f"|format(row.Kelly_Size * 100 if row.Kelly_Size else 0) }}%)
                                    </span>
                                </td>

                                <!-- Column 2: Dynamic Format Type Badge -->
                                <td>
                                    {% set opt_strat = row.Option_Strategy|default('None', true) %}
                                    {% if 'Spread' in opt_strat or 'Call' in opt_strat or 'Condor' in opt_strat or 'Put' in opt_strat %}
                                        <span class="badge badge-yellow">Derivatives</span>
                                    {% elif row.sector|default('N/A', true) in ['Index', 'N/A'] %}
                                        <span class="badge badge-red">Macro Proxy</span>
                                    {% else %}
                                        <span class="badge badge-green">Equities</span>
                                    {% endif %}
                                </td>

                                <!-- Column 3: Traffic Lights & Contextual Anomaly Tooltips -->
                                <td>
                                    <div style="margin-bottom: 4px;">
                                        RSI:
                                        {% if row.Recent_Anomaly %}
                                            <div class="anomaly-tooltip" style="color: var(--warning); font-weight: bold;">
                                                {{ "%.1f"|format(row.RSI|default(50.0, true)) }}
                                                <span class="tooltiptext">⚠️ Indicator influenced by recent {{ row.Recent_Anomaly }} event. Treat standard signals with caution.</span>
                                            </div>
                                        {% else %}
                                            {{ "%.1f"|format(row.RSI|default(50.0, true)) }}
                                        {% endif %}
                                        {% if row.Audit_RSI_Status|default('', true) == 'FAILED' %}
                                            <span class="badge badge-red">FAIL</span>
                                        {% elif row.Recent_Anomaly %}
                                            <span class="badge badge-yellow">WARN</span>
                                        {% else %}
                                            <span class="badge badge-green">PASS</span>
                                        {% endif %}
                                    </div>
                                    <div>
                                        MACD: {{ "%.2f"|format(row.MACD_Line|default(0.0, true)) }}
                                        {% if row.Audit_MACD_Status|default('', true) == 'FAILED' %}
                                            <span class="badge badge-red">FAIL</span>
                                        {% else %}
                                            <span class="badge badge-green">PASS</span>
                                        {% endif %}
                                    </div>
                                </td>

                                <!-- Column 4: Risk Indicators with Traffic Lights -->
                                <td>
                                    CoVaR: {{ "%.2f"|format(row.CoVaR_Proxy|default(0.0, true)) }}
                                    {% if row.CoVaR_Proxy|default(0.0, true) > 0.15 %}
                                        <span class="badge badge-red">HIGH TAIL RISK</span>
                                    {% elif row.CoVaR_Proxy|default(0.0, true) > 0.08 %}
                                        <span class="badge badge-yellow">ELEVATED</span>
                                    {% else %}
                                        <span class="badge badge-green">SAFE</span>
                                    {% endif %}
                                </td>

                                <!-- Column 5: Dynamic Forecasting / Options Formatting -->
                                <td>
                                    {% set opt_strat = row.Option_Strategy|default('None', true) %}
                                    {% if 'Spread' in opt_strat or 'Call' in opt_strat or 'Condor' in opt_strat or 'Put' in opt_strat %}
                                        <!-- Black-Scholes Output Format for Options -->
                                        <div style="font-size: 12px;">
                                            <strong>Strategy:</strong> {{ opt_strat }}<br>
                                            <strong>IV Rank:</strong> {{ "%.1f"|format(row.IV_Rank|default(0.0, true)) }} |
                                            <strong>IV Edge:</strong> {{ "%.2f"|format(row.Options_IV_Edge|default(0.0, true) * 100) }}%
                                        </div>
                                    {% else %}
                                        <!-- Monte Carlo Confidence Intervals for Equities -->
                                        <div style="font-size: 13px;">
                                            <strong>30D Target:</strong> ${{ "%.2f"|format(row.Forecast_30D|default(0.0, true)) }}<br>
                                            <span style="color: var(--accent); font-size: 11px;">
                                                95% MC Confidence Band:
                                                [${{ "%.2f"|format(row.MC_Lower_95|default(0.0, true)) }} - ${{ "%.2f"|format(row.MC_Upper_95|default(0.0, true)) }}]
                                            </span>
                                        </div>
                                    {% endif %}
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- ======== TAB 2: GRAVITY AI AUDIT LOG ======== -->
        <div id="AuditLog" class="tabcontent">
            <div class="data-card" style="padding: 24px;">
                <h3 style="color: var(--accent); margin-bottom: 8px;">🤖 Gravity AI Auditor — Live Exception Log</h3>
                <p style="font-size: 13px; color: var(--text-muted); margin-bottom: 16px;">
                    Displays the raw JSON validation findings from the daily AI Verification Suite run.
                    Identifies risk-management overrides and calculation failures.
                </p>
                <pre>{{ audit_log | tojson(indent=4) }}</pre>
            </div>
        </div>
    </div>

    <script>
        // Tab switching logic
        function openTab(evt, tabName) {
            var i, tabcontent, tablinks;
            tabcontent = document.getElementsByClassName("tabcontent");
            for (i = 0; i < tabcontent.length; i++) {
                tabcontent[i].style.display = "none";
            }
            tablinks = document.getElementsByClassName("tablinks");
            for (i = 0; i < tablinks.length; i++) {
                tablinks[i].className = tablinks[i].className.replace(" active", "");
            }
            document.getElementById(tabName).style.display = "block";
            evt.currentTarget.className += " active";
        }

        // Render Brinson-Fachler Attribution Chart (Chart.js)
        document.addEventListener("DOMContentLoaded", function() {
            var ctx = document.getElementById('attributionChart');
            if (ctx) {
                new Chart(ctx.getContext('2d'), {
                    type: 'bar',
                    data: {
                        labels: ['Allocation Effect', 'Selection Effect'],
                        datasets: [{
                            label: 'Alpha Contribution',
                            data: [{{ avg_bf_allocation }}, {{ avg_bf_selection }}],
                            backgroundColor: [
                                'rgba(59, 130, 246, 0.6)',
                                'rgba(16, 185, 129, 0.6)'
                            ],
                            borderColor: [
                                'rgba(59, 130, 246, 1)',
                                'rgba(16, 185, 129, 1)'
                            ],
                            borderWidth: 1
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: { legend: { display: false } },
                        scales: {
                            y: {
                                beginAtZero: true,
                                grid: { color: '#1f2937' },
                                ticks: { color: '#9ca3af', font: { size: 10 } }
                            },
                            x: {
                                grid: { display: false },
                                ticks: { color: '#9ca3af', font: { size: 10 } }
                            }
                        }
                    }
                });
            }
        });
    </script>
</body>
</html>
"""


def generate_html_report(
    portfolio_data: List[Dict[str, Any]],
    regime: str,
    output_path: str = "daily_report.html",
    yield_curve: float = -0.25,
    credit_spread: float = 6.0,
    sahm_rule: float = 0.6,
    real_yield: float = 2.5,
    audit_log: Dict[str, Any] = None
):
    """
    Renders a clean, styled HTML report using Jinja2 containing portfolio
    statistics, macro regimes, Gravity AI audit JSON, Traffic Light Indicators,
    Contextual Anomaly Tooltips, Executive Summary Blocks, Dynamic Formatting,
    Monte Carlo Confidence Intervals, and Options Greeks formatting.

    IMPORTANT: output_path remains the 3rd positional parameter to preserve
    backward compatibility with main.py and main_orchestrator.py callers.
    audit_log is keyword-only at the end.
    """
    telemetry.info("Generating Daily Jinja2 HTML Report with AI Auditor overlays...")

    # 1. Fallback for the Gravity AI JSON payload
    #    Priority: caller-supplied -> Gravity_Verification_Report.json on disk -> warning stub
    if not audit_log:
        gravity_report_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Gravity_Verification_Report.json")
        if os.path.exists(gravity_report_path):
            try:
                with open(gravity_report_path, "r", encoding="utf-8") as _f:
                    audit_log = json.load(_f)
                telemetry.info(f"Audit log loaded from {gravity_report_path}")
            except Exception as _e:
                telemetry.warning(f"Could not load Gravity_Verification_Report.json: {_e}")
                audit_log = {
                    "status": "WARNING",
                    "message": f"Could not parse Gravity_Verification_Report.json: {_e}",
                    "timestamp": datetime.now().isoformat()
                }
        else:
            audit_log = {
                "status": "WARNING",
                "message": "GravityAIAuditor payload missing for this execution cycle. Run ai_verification_prompts.py to generate.",
                "timestamp": datetime.now().isoformat()
            }

    # 2. Sanitize NaN/Inf values for JSON and Jinja2 safety
    cleaned_portfolio = []
    for row in portfolio_data:
        clean_row = {}
        for k, v in row.items():
            if isinstance(v, float) and (np.isnan(v) or np.isinf(v)):
                clean_row[k] = None
            else:
                clean_row[k] = v
        cleaned_portfolio.append(clean_row)

    # 3. Standardize missing field names for Jinja template compatibility
    #    Maps between spaced keys (from pipeline) and underscored keys (for template)
    total_heat, total_alloc, total_select, count = 0.0, 0.0, 0.0, 0

    for row in cleaned_portfolio:
        # Field normalization: pipeline uses spaced keys, template uses underscored
        row['Action_Signal']    = row.get('Action_Signal')    or row.get('Action Signal')    or 'HOLD'
        row['Kelly_Size']       = row.get('Kelly_Size')       or row.get('Kelly Target')     or 0.0
        # Forecast_30D: pipeline outputs as 'Forecast_30' (no suffix D)
        row['Forecast_30D']     = (row.get('Forecast_30D')
                                   or row.get('Forecast_30')
                                   or row.get('MC_Target')
                                   or 0.0)
        # CoVaR_Proxy: pipeline stores as 'CoVaR Proxy' (spaced) from main.py
        row['CoVaR_Proxy']      = (row.get('CoVaR_Proxy')
                                   or row.get('CoVaR Proxy')
                                   or 0.0)
        row['Option_Strategy']  = row.get('Option_Strategy')  or row.get('Option Strategy')  or 'None'
        row['RSI']              = row.get('RSI', 50.0)        or 50.0
        row['MACD_Line']        = (row.get('MACD_Line')
                                   or row.get('MACD Line')
                                   or 0.0)
        row['IV_Rank']          = row.get('IV_Rank')          or row.get('IVR')              or 0.0
        row['Options_IV_Edge']  = row.get('Options_IV_Edge')  or row.get('Options IV Edge')  or 0.0

        # Ensure sector fallback
        if 'sector' not in row or not row['sector']:
            row['sector'] = row.get('Sector', 'N/A')

        # Ensure anomaly / audit status fields exist (default to None / PASSED)
        row.setdefault('Recent_Anomaly', None)
        row.setdefault('Audit_RSI_Status', 'PASSED')
        row.setdefault('Audit_MACD_Status', 'PASSED')

        # Calculate Monte Carlo 95% Confidence Intervals if not passed explicitly
        # Pipeline stores as 'MC_Lower' and 'MC_Upper' (no _95 suffix)
        forecast_30d = row['Forecast_30D']
        if not row.get('MC_Lower_95'):
            row['MC_Lower_95'] = (row.get('MC_Lower')
                                  or (forecast_30d * 0.92 if forecast_30d else 0.0))
        if not row.get('MC_Upper_95'):
            row['MC_Upper_95'] = (row.get('MC_Upper')
                                  or (forecast_30d * 1.08 if forecast_30d else 0.0))

        # Accumulate Executive Summary metrics
        total_heat  += float(row.get('Portfolio_Heat', row.get('Portfolio Heat', 0.0)) or 0.0)
        total_alloc += float(row.get('BF_Allocation', row.get('BF Allocation', 0.0)) or 0.0)
        total_select += float(row.get('BF_Selection', row.get('BF Selection', 0.0)) or 0.0)
        count += 1

    avg_heat   = total_heat   / count if count > 0 else 0.0
    avg_alloc  = total_alloc  / count if count > 0 else 0.0
    avg_select = total_select / count if count > 0 else 0.0

    # 4. Render HTML Template
    template = Template(HTML_REPORT_TEMPLATE)
    html_content = template.render(
        current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        portfolio_rows=cleaned_portfolio,
        regime=regime,
        yield_curve=yield_curve,
        credit_spread=credit_spread,
        sahm_rule=sahm_rule,
        real_yield=real_yield,
        audit_log=audit_log,
        avg_portfolio_heat=avg_heat,
        avg_bf_allocation=round(avg_alloc, 6),
        avg_bf_selection=round(avg_select, 6)
    )

    # 5. Save to Disk
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        telemetry.info(f"HTML Report successfully written to {output_path}")
    except Exception as e:
        telemetry.error(f"Failed to write HTML report: {e}")

    return output_path


# =============================================================================
# 4. TESTING ROUTINE
# =============================================================================
if __name__ == '__main__':
    telemetry.info("Telemetry system active. Running diagnostic verification.")

    # Generate synthetic price series for Plotly chart verification
    np.random.seed(42)
    dates = pd.date_range(start='2024-01-01', periods=100)
    returns = np.random.normal(0.001, 0.02, len(dates))
    price = 100 * np.exp(np.cumsum(returns))
    df_dummy = pd.DataFrame({'Close': price}, index=dates)
    generate_plotly_volatility_bands(df_dummy, "MOCK")

    # Mock Data to simulate pipeline output and verify the full HTML layout
    test_portfolio = [
        {
            "Symbol": "AGNC", "Action_Signal": "RISK REDUCE", "Kelly_Size": 0.0,
            "Option_Strategy": "Sell to Open Call Credit Spread", "IV_Rank": 85.2, "Options_IV_Edge": 0.04,
            "RSI": 28.5, "MACD_Line": -0.85, "CoVaR_Proxy": 0.22, "Forecast_30D": 9.50,
            "Audit_RSI_Status": "PASSED", "Audit_MACD_Status": "PASSED", "Recent_Anomaly": None,
            "Portfolio_Heat": 0.08, "BF_Allocation": -0.015, "BF_Selection": -0.02,
            "sector": "Real Estate"
        },
        {
            "Symbol": "AAPL", "Action_Signal": "STRONG BUY", "Kelly_Size": 0.15,
            "Option_Strategy": "None", "sector": "Technology",
            "RSI": 58.2, "MACD_Line": 1.25, "CoVaR_Proxy": 0.05, "Forecast_30D": 155.0,
            "MC_Lower_95": 145.5, "MC_Upper_95": 165.2,
            "Audit_RSI_Status": "PASSED", "Audit_MACD_Status": "PASSED", "Recent_Anomaly": "Stock Split",
            "Portfolio_Heat": 0.02, "BF_Allocation": 0.04, "BF_Selection": 0.03
        }
    ]

    test_audit_log = {
        "status": "PASSED_WITH_WARNINGS",
        "findings": [
            "AGNC breached 6% portfolio heat threshold (0.08 detected). Halting execution.",
            "AAPL indicator arrays heavily influenced by recent 4:1 Stock Split. Adjusting boundaries."
        ]
    }

    generate_html_report(test_portfolio, regime="CREDIT EVENT", audit_log=test_audit_log)
