"""
InvestYo Quant Platform - Diagnostics & Visualizations
======================================================
Step 6 of the Modernization Roadmap: Diagnostic and Visualization Deployment.

Provides structured JSON logging telemetry, a Jinja2 template engine for
HTML report generation, and interactive Plotly volatility bands.

Report redesign (2026-06)
-------------------------
The daily HTML report was rebuilt to lead with **Holdings & P&L** and
**Action & Rationale** — the two information classes the operator most wants
to see at a glance.  Previously the embedded template was hard-wired to the
*orchestrator's* wide dashboard schema (CoVaR / IV Rank / Monte-Carlo bands)
and silently discarded the advisory fields that ``main.py`` actually computes
(holdings, cost basis, unrealized P&L, conviction, plain-English rationale,
suggested position size).  The new template:

  • Renders an optional **portfolio summary band** (equity, buying power,
    unrealized P&L, dividends, position count, BUY/HOLD/SELL tally) driven by
    the new ``account_summary`` keyword argument.
  • Surfaces per-symbol **holdings** (shares, average cost, current price,
    market value, unrealized P&L $ / %) sourced from the Robinhood
    ``AccountSnapshot`` — the source of truth for account state (CONSTRAINT #4).
  • Surfaces the **action, conviction, suggested size and full rationale** for
    every symbol, with a click-to-expand detail row.
  • Provides a dependency-free **client-side search + column sort** so the
    operator can find / order rows without a page reload.
  • Remains **backward compatible** with ``main_orchestrator.py``: when the
    advisory / holdings / ``account_summary`` fields are absent the same rows
    degrade gracefully to "—" placeholders and the summary band is hidden.

All field normalization happens in Python (``generate_html_report``) so the
Jinja template stays declarative; both the spaced pipeline keys
(``"Action Signal"``) and the underscored advisory keys (``"Action_Signal"``)
are accepted.
"""

import json
import logging
import os
import numpy as np
import pandas as pd
from datetime import datetime
from typing import List, Dict, Any, Optional
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
# 3. JINJA2 HTML REPORT TEMPLATE (HOLDINGS & P&L + ACTION & RATIONALE LEAD)
# =============================================================================
HTML_REPORT_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="description" content="InvestYo Quant Platform - Daily Advisory Report. Holdings, unrealized P&L, action signals, conviction and plain-English rationale.">
    <title>InvestYo Quant Platform - Daily Report</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {
            --bg-dark: #0b0f19;
            --card-bg: #111827;
            --card-bg-soft: #161e2e;
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
            padding: 32px 20px;
            line-height: 1.55;
        }
        .container { max-width: 1440px; margin: 0 auto; }

        /* Header */
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border);
            padding-bottom: 18px;
            margin-bottom: 26px;
            flex-wrap: wrap;
            gap: 12px;
        }
        h1 {
            font-size: 24px; font-weight: 700; letter-spacing: -0.025em;
            background: linear-gradient(to right, #60a5fa, #3b82f6);
            -webkit-background-clip: text; background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .timestamp { color: var(--text-muted); font-size: 13px; }
        .freshness-pill {
            display: inline-block; padding: 3px 10px; border-radius: 9999px;
            font-size: 11px; font-weight: 600; margin-left: 8px;
        }
        .fresh-ok    { background: var(--success-glow); color: var(--success); border: 1px solid var(--success); }
        .fresh-stale { background: var(--warning-glow); color: var(--warning); border: 1px solid var(--warning); }

        /* ======== PORTFOLIO SUMMARY BAND ======== */
        .summary-band {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
            gap: 14px;
            margin-bottom: 22px;
        }
        .summary-tile {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 16px 18px;
            position: relative;
            overflow: hidden;
        }
        .summary-tile::before {
            content: ''; position: absolute; top: 0; left: 0;
            width: 100%; height: 2px;
            background: linear-gradient(to right, #60a5fa, #3b82f6);
        }
        .summary-tile .label {
            color: var(--text-muted); font-size: 11px; font-weight: 600;
            text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 6px;
        }
        .summary-tile .value { font-size: 22px; font-weight: 700; }
        .summary-tile .sub { font-size: 11px; color: var(--text-muted); margin-top: 2px; }
        .pos { color: var(--success); }
        .neg { color: var(--danger); }

        /* ======== Δ SINCE LAST RUN BAND ======== */
        .delta-band {
            background: var(--card-bg);
            border: 1px solid var(--accent);
            border-left: 4px solid var(--accent);
            border-radius: 10px;
            padding: 16px 20px;
            margin-bottom: 22px;
            box-shadow: 0 0 0 1px var(--accent-glow);
        }
        .delta-band .delta-header {
            display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;
            margin-bottom: 10px;
        }
        .delta-band h3 {
            font-size: 14px; color: var(--accent); margin: 0;
            text-transform: uppercase; letter-spacing: 0.06em; font-weight: 700;
        }
        .delta-band .delta-ts { color: var(--text-muted); font-size: 12px; }
        .delta-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 12px 22px;
        }
        .delta-cell .delta-label {
            color: var(--text-muted); font-size: 11px;
            text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px;
        }
        .delta-cell ul { list-style: none; padding-left: 0; margin: 0; font-size: 13px; }
        .delta-cell li { padding: 2px 0; }
        .delta-cell .sym { font-weight: 700; color: var(--text-main); }
        .delta-band .regime-banner {
            background: var(--warning-glow);
            border: 1px solid var(--warning);
            color: var(--warning); border-radius: 6px;
            padding: 8px 12px; margin-bottom: 12px;
            font-size: 13px; font-weight: 600;
        }
        .delta-band .empty-note {
            color: var(--text-muted); font-size: 13px;
            padding: 4px 0; text-align: left;
        }

        /* ======== MACRO / REGIME CARDS ======== */
        .exec-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 18px;
            margin-bottom: 26px;
        }
        .exec-card {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 22px;
            text-align: center;
            position: relative;
            overflow: hidden;
            box-shadow: 0 8px 16px rgba(0, 0, 0, 0.2);
        }
        .exec-card::before {
            content: ''; position: absolute; top: 0; left: 0;
            width: 100%; height: 3px;
            background: linear-gradient(to right, #60a5fa, #3b82f6);
        }
        .exec-card h3 {
            color: var(--text-muted); font-size: 13px; font-weight: 600;
            text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 12px;
        }
        .exec-card h2 { font-size: 26px; font-weight: 700; margin-bottom: 8px; }
        .exec-card .subtext { font-size: 12px; color: var(--text-muted); }

        /* ======== TABS ======== */
        .tab {
            overflow: hidden; border-bottom: 1px solid var(--border); margin-bottom: 22px;
        }
        .tab button {
            background-color: inherit; color: var(--text-muted); float: left;
            border: none; outline: none; cursor: pointer; padding: 14px 20px;
            transition: color 0.3s, border-bottom 0.3s;
            font-size: 15px; font-weight: 600; font-family: inherit;
        }
        .tab button:hover { color: var(--accent); }
        .tab button.active { border-bottom: 3px solid var(--accent); color: var(--accent); }
        .tabcontent { display: none; }

        /* ======== TOOLBAR (SEARCH) ======== */
        .toolbar {
            display: flex; align-items: center; gap: 12px;
            margin-bottom: 14px; flex-wrap: wrap;
        }
        .toolbar input[type="search"] {
            flex: 1; min-width: 220px;
            background: var(--card-bg-soft); border: 1px solid var(--border);
            color: var(--text-main); border-radius: 8px; padding: 10px 14px;
            font-size: 14px; font-family: inherit; outline: none;
        }
        .toolbar input[type="search"]:focus { border-color: var(--accent); }
        .toolbar .hint { font-size: 12px; color: var(--text-muted); }

        /* ======== DATA TABLE ======== */
        .data-card {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
        }
        .card-header {
            padding: 18px 20px; border-bottom: 1px solid var(--border);
            font-weight: 600; font-size: 16px; color: var(--text-main);
        }
        table { width: 100%; border-collapse: collapse; font-size: 14px; }
        th, td { padding: 12px 16px; text-align: left; border-bottom: 1px solid var(--border); white-space: nowrap; }
        th {
            background-color: rgba(255, 255, 255, 0.02);
            color: var(--text-muted); font-weight: 500;
            text-transform: uppercase; font-size: 11px; letter-spacing: 0.05em;
            cursor: pointer; user-select: none; position: relative;
        }
        th.sortable:hover { color: var(--accent); }
        th .arrow { font-size: 9px; opacity: 0.5; margin-left: 4px; }
        td.num { text-align: right; font-variant-numeric: tabular-nums; }
        tr.data-row:hover td { background-color: rgba(255, 255, 255, 0.025); cursor: pointer; }
        tr.detail-row td {
            background: var(--card-bg-soft); white-space: normal;
            font-size: 13px; color: var(--text-main);
        }
        .detail-grid {
            display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 10px 24px; margin-bottom: 12px;
        }
        .detail-grid .di-label { color: var(--text-muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; }
        .detail-grid .di-val { font-weight: 600; }
        .rationale {
            background: rgba(0,0,0,0.25); border-left: 3px solid var(--accent);
            border-radius: 6px; padding: 12px 14px; margin-top: 6px;
            white-space: pre-line; line-height: 1.6;
        }

        /* ======== BADGES ======== */
        .badge {
            padding: 4px 10px; border-radius: 9999px;
            font-size: 11px; font-weight: 700; text-transform: uppercase;
            letter-spacing: 0.03em; display: inline-block;
        }
        .badge-green  { background-color: var(--success-glow); color: var(--success); border: 1px solid var(--success); }
        .badge-yellow { background-color: var(--warning-glow); color: var(--warning); border: 1px solid var(--warning); }
        .badge-red    { background-color: var(--danger-glow);  color: var(--danger);  border: 1px solid var(--danger);  }
        .badge-gray   { background-color: rgba(255,255,255,0.05); color: var(--text-muted); border: 1px solid var(--border); }

        /* Action signal colouring */
        .sig-STRONG_BUY, .sig-BUY  { color: var(--success); font-weight: 700; }
        .sig-HOLD                  { color: var(--warning); font-weight: 700; }
        .sig-SELL, .sig-STRONG_SELL, .sig-RISK_REDUCE { color: var(--danger); font-weight: 700; }

        /* Conviction meter */
        .conv-meter {
            display: inline-block; width: 54px; height: 6px; border-radius: 3px;
            background: var(--border); vertical-align: middle; margin-left: 6px; overflow: hidden;
        }
        .conv-fill { display: block; height: 100%; background: var(--accent); }

        /* ======== AUDIT LOG ======== */
        pre {
            background: #000; padding: 20px; border-radius: 8px; overflow-x: auto;
            color: #a5d6ff; border: 1px solid var(--border); font-size: 13px; line-height: 1.5;
        }
        .empty-note { padding: 28px 20px; color: var(--text-muted); font-size: 14px; text-align: center; }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div>
                <h1>InvestYo Quantitative Portfolio Report</h1>
                <div class="timestamp">Holdings-aware advisory engine
                    {% if account_summary %}
                        {% if account_summary.is_stale %}
                            <span class="freshness-pill fresh-stale">ACCOUNT DATA STALE</span>
                        {% else %}
                            <span class="freshness-pill fresh-ok">ACCOUNT DATA FRESH</span>
                        {% endif %}
                    {% endif %}
                </div>
            </div>
            <div class="timestamp">Generated: {{ current_time }}
                {% if account_summary and account_summary.fetched_at %}
                    <br>Account snapshot: {{ account_summary.fetched_at }}
                    ({{ "%.1f"|format(account_summary.age_hours) }}h old)
                {% endif %}
            </div>
        </header>

        {% if account_summary %}
        <!-- ======== PORTFOLIO SUMMARY BAND ======== -->
        <div class="summary-band">
            <div class="summary-tile">
                <div class="label">Total Equity</div>
                <div class="value">${{ "{:,.0f}".format(account_summary.total_equity) }}</div>
                <div class="sub">{{ account_summary.num_positions }} position(s) held</div>
            </div>
            <div class="summary-tile">
                <div class="label">Buying Power</div>
                <div class="value">${{ "{:,.0f}".format(account_summary.buying_power) }}</div>
                <div class="sub">Unallocated cash</div>
            </div>
            <div class="summary-tile">
                <div class="label">Unrealized P&amp;L</div>
                <div class="value {{ 'pos' if account_summary.total_unrealized_pl >= 0 else 'neg' }}">
                    {{ '+' if account_summary.total_unrealized_pl >= 0 else '-' }}${{ "{:,.0f}".format(account_summary.total_unrealized_pl|abs) }}
                </div>
                <div class="sub">Across held positions</div>
            </div>
            <div class="summary-tile">
                <div class="label">Dividends Received</div>
                <div class="value">${{ "{:,.0f}".format(account_summary.total_dividends) }}</div>
                <div class="sub">Paid + reinvested</div>
            </div>
            <div class="summary-tile">
                <div class="label">Signals</div>
                <div class="value" style="font-size:18px;">
                    <span class="pos">{{ account_summary.n_buy }} BUY</span> /
                    <span style="color:var(--warning)">{{ account_summary.n_hold }} HOLD</span> /
                    <span class="neg">{{ account_summary.n_sell }} SELL</span>
                </div>
                <div class="sub">{{ account_summary.n_total }} symbols analysed</div>
            </div>
        </div>
        {% endif %}

        {% if snapshot_diff %}
        <!-- ======== Δ SINCE LAST RUN ======== -->
        <div class="delta-band">
            <div class="delta-header">
                <h3>Δ Since Last Run</h3>
                {% if snapshot_diff.prev_ts and snapshot_diff.curr_ts %}
                    <span class="delta-ts">{{ snapshot_diff.prev_ts }} → {{ snapshot_diff.curr_ts }}</span>
                {% elif snapshot_diff.curr_ts %}
                    <span class="delta-ts">First snapshot — {{ snapshot_diff.curr_ts }}</span>
                {% endif %}
            </div>
            {% if snapshot_diff.regime_change %}
            <div class="regime-banner">
                ⚠ Regime change:
                <strong>{{ snapshot_diff.regime_change[0] }}</strong>
                →
                <strong>{{ snapshot_diff.regime_change[1] }}</strong>
            </div>
            {% endif %}
            {% if snapshot_diff.is_empty %}
                <div class="empty-note">No material changes since last run.</div>
            {% else %}
            <div class="delta-grid">
                {% if snapshot_diff.new_buys %}
                <div class="delta-cell">
                    <div class="delta-label">🟢 New BUYs</div>
                    <ul>
                        {% for sym in snapshot_diff.new_buys %}
                            <li><span class="sym pos">{{ sym }}</span></li>
                        {% endfor %}
                    </ul>
                </div>
                {% endif %}
                {% if snapshot_diff.action_flips %}
                <div class="delta-cell">
                    <div class="delta-label">🔁 Action flips</div>
                    <ul>
                        {% for flip in snapshot_diff.action_flips %}
                            <li><span class="sym">{{ flip.symbol }}</span>:
                                <span style="color:var(--text-muted)">{{ flip.before }}</span>
                                →
                                <span class="sig-{{ flip.after|replace(' ', '_') }}">{{ flip.after }}</span>
                            </li>
                        {% endfor %}
                    </ul>
                </div>
                {% endif %}
                {% if snapshot_diff.conviction_deltas %}
                <div class="delta-cell">
                    <div class="delta-label">📈 Conviction moved (|Δ| ≥ {{ "%.2f"|format(snapshot_diff.conviction_deltas[0].delta|abs if snapshot_diff.conviction_deltas else 0.2) }})</div>
                    <ul>
                        {% for d in snapshot_diff.conviction_deltas %}
                            <li><span class="sym">{{ d.symbol }}</span>:
                                {{ "%.2f"|format(d.before) }} → {{ "%.2f"|format(d.after) }}
                                <span class="{{ 'pos' if d.delta >= 0 else 'neg' }}">
                                    ({{ '+' if d.delta >= 0 else '' }}{{ "%.2f"|format(d.delta) }})
                                </span>
                            </li>
                        {% endfor %}
                    </ul>
                </div>
                {% endif %}
                {% if snapshot_diff.added_holdings %}
                <div class="delta-cell">
                    <div class="delta-label">➕ Holdings added</div>
                    <ul>
                        {% for sym in snapshot_diff.added_holdings %}
                            <li><span class="sym pos">{{ sym }}</span></li>
                        {% endfor %}
                    </ul>
                </div>
                {% endif %}
                {% if snapshot_diff.dropped_holdings %}
                <div class="delta-cell">
                    <div class="delta-label">➖ Holdings dropped</div>
                    <ul>
                        {% for sym in snapshot_diff.dropped_holdings %}
                            <li><span class="sym neg">{{ sym }}</span></li>
                        {% endfor %}
                    </ul>
                </div>
                {% endif %}
            </div>
            {% endif %}
        </div>
        {% endif %}

        <!-- ======== MACRO / REGIME CARDS ======== -->
        <div class="exec-grid">
            <div class="exec-card">
                <h3>🌐 Market Regime</h3>
                <h2 style="color: {% if regime == 'RISK ON' %}var(--success){% elif regime == 'NEUTRAL' %}var(--warning){% else %}var(--danger){% endif %};">
                    {{ regime }}
                </h2>
                <p class="subtext">
                    10Y-2Y: {{ "%.2f"|format(yield_curve) }}% | HY OAS: {{ "%.2f"|format(credit_spread) }}% | Sahm: {{ "%.2f"|format(sahm_rule) }}
                </p>
            </div>
            <div class="exec-card">
                <h3>🔥 Portfolio Heat</h3>
                <h2 style="color: {% if avg_portfolio_heat > 0.06 %}var(--danger){% elif avg_portfolio_heat > 0.04 %}var(--warning){% else %}var(--success){% endif %};">
                    {{ "%.2f"|format(avg_portfolio_heat * 100) }}%
                </h2>
                <p class="subtext">Max Institutional Limit: 6.00%</p>
                {% if avg_portfolio_heat > 0.06 %}
                    <div class="badge badge-red" style="margin-top: 8px;">HALT THRESHOLD BREACHED</div>
                {% else %}
                    <div class="badge badge-green" style="margin-top: 8px;">WITHIN SAFE BOUNDS</div>
                {% endif %}
            </div>
            <div class="exec-card">
                <h3>📊 Signal Distribution</h3>
                <div style="height: 120px;">
                    <canvas id="signalChart"></canvas>
                </div>
                <p class="subtext" style="margin-top: 10px;">BUY / HOLD / SELL across analysed universe</p>
            </div>
        </div>

        <!-- ======== TAB NAVIGATION ======== -->
        <div class="tab">
            <button class="tablinks active" onclick="openTab(event, 'Holdings')">Holdings &amp; Signals</button>
            <button class="tablinks" onclick="openTab(event, 'AuditLog')">Gravity AI Audit Log</button>
        </div>

        <!-- ======== TAB 1: HOLDINGS & SIGNALS ======== -->
        <div id="Holdings" class="tabcontent" style="display: block;">
            <div class="toolbar">
                <input type="search" id="tableSearch" placeholder="🔎 Filter by symbol, action or rationale…" oninput="filterTable()">
                <span class="hint">Click a row to expand rationale · click a column header to sort</span>
            </div>
            <div class="data-card">
                <div class="card-header">Holdings, Action Signals &amp; Advisory Rationale</div>
                <div style="overflow-x: auto; padding: 0;">
                    <table id="mainTable">
                        <thead>
                            <tr>
                                <th class="sortable" data-type="str"   onclick="sortTable(0,'str')">Symbol <span class="arrow">▲▼</span></th>
                                <th class="sortable" data-type="str"   onclick="sortTable(1,'str')">Action <span class="arrow">▲▼</span></th>
                                <th class="sortable" data-type="num"   onclick="sortTable(2,'num')">Shares <span class="arrow">▲▼</span></th>
                                <th class="sortable" data-type="num"   onclick="sortTable(3,'num')">Avg Cost <span class="arrow">▲▼</span></th>
                                <th class="sortable" data-type="num"   onclick="sortTable(4,'num')">Price <span class="arrow">▲▼</span></th>
                                <th class="sortable" data-type="num"   onclick="sortTable(5,'num')">Mkt Value <span class="arrow">▲▼</span></th>
                                <th class="sortable" data-type="num"   onclick="sortTable(6,'num')">Unreal. P&amp;L <span class="arrow">▲▼</span></th>
                                <th class="sortable" data-type="num"   onclick="sortTable(7,'num')">P&amp;L % <span class="arrow">▲▼</span></th>
                                <th class="sortable" data-type="num"   onclick="sortTable(8,'num')">Suggest % <span class="arrow">▲▼</span></th>
                                <th class="sortable" data-type="num"   onclick="sortTable(9,'num')">30D Fcst <span class="arrow">▲▼</span></th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for row in portfolio_rows %}
                            <tr class="data-row" onclick="toggleDetail('detail-{{ loop.index }}')"
                                data-search="{{ (row.Symbol ~ ' ' ~ row.Action_Signal ~ ' ' ~ row.Rationale)|lower }}">
                                <td><strong>{{ row.Symbol }}</strong>
                                    {% if row.DataQuality and row.DataQuality != 'OK' %}
                                        <span class="badge badge-yellow" style="margin-left:4px;">{{ row.DataQuality }}</span>
                                    {% endif %}
                                    {% if row.CompanyName %}<br><span style="font-size:11px;color:var(--text-muted);">{{ row.CompanyName }}</span>{% endif %}
                                </td>
                                <td>
                                    <span class="sig-{{ row.Action_Signal|replace(' ', '_') }}">{{ row.Action_Signal }}</span>
                                    {% if row.Conviction is not none and row.Conviction > 0 %}
                                        <span class="conv-meter" title="Conviction {{ "%.0f"|format(row.Conviction * 100) }}%">
                                            <span class="conv-fill" style="width: {{ "%.0f"|format(row.Conviction * 100) }}%;"></span>
                                        </span>
                                    {% endif %}
                                </td>
                                <td class="num" data-sort="{{ row.Shares }}">{{ "%.2f"|format(row.Shares) if row.Shares else '—' }}</td>
                                <td class="num" data-sort="{{ row.AvgCost }}">{{ "$%.2f"|format(row.AvgCost) if row.AvgCost else '—' }}</td>
                                <td class="num" data-sort="{{ row.Price }}">{{ "$%.2f"|format(row.Price) if row.Price else '—' }}</td>
                                <td class="num" data-sort="{{ row.MarketValue }}">{{ "${:,.0f}".format(row.MarketValue) if row.MarketValue else '—' }}</td>
                                <td class="num" data-sort="{{ row.UnrealizedPL }}">
                                    {% if row.Shares and row.Shares > 0 %}
                                        <span class="{{ 'pos' if row.UnrealizedPL >= 0 else 'neg' }}">
                                            {{ '+' if row.UnrealizedPL >= 0 else '-' }}${{ "{:,.0f}".format(row.UnrealizedPL|abs) }}
                                        </span>
                                    {% else %}—{% endif %}
                                </td>
                                <td class="num" data-sort="{{ row.UnrealizedPLPct }}">
                                    {% if row.Shares and row.Shares > 0 %}
                                        <span class="{{ 'pos' if row.UnrealizedPLPct >= 0 else 'neg' }}">
                                            {{ '+' if row.UnrealizedPLPct >= 0 else '' }}{{ "%.1f"|format(row.UnrealizedPLPct * 100) }}%
                                        </span>
                                    {% else %}—{% endif %}
                                </td>
                                <td class="num" data-sort="{{ row.SuggestedPct }}">
                                    {{ "%.1f"|format(row.SuggestedPct * 100) }}%
                                </td>
                                <td class="num" data-sort="{{ row.Forecast_30D }}">
                                    {{ "$%.2f"|format(row.Forecast_30D) if row.Forecast_30D else '—' }}
                                </td>
                            </tr>
                            <tr class="detail-row" id="detail-{{ loop.index }}" style="display:none;">
                                <td colspan="10">
                                    <div class="detail-grid">
                                        <div><div class="di-label">Strategy</div><div class="di-val">{{ row.Strategy|default('—', true) }}</div></div>
                                        <div><div class="di-label">Conviction</div><div class="di-val">{{ "%.0f"|format((row.Conviction or 0) * 100) }}%</div></div>
                                        <div><div class="di-label">Suggested Size</div><div class="di-val">{{ "%.2f"|format((row.SuggestedPct or 0) * 100) }}%</div></div>
                                        <div><div class="di-label">Dividends</div><div class="di-val">${{ "{:,.2f}".format(row.Dividends or 0) }}</div></div>
                                        <div><div class="di-label">RSI (14)</div><div class="di-val">{{ "%.1f"|format(row.RSI) if row.RSI else '—' }}</div></div>
                                        <div><div class="di-label">GARCH Vol</div><div class="di-val">{{ "%.1f"|format((row.GARCH_Vol or 0) * 100) }}%</div></div>
                                        <div><div class="di-label">Max Drawdown</div><div class="di-val">{{ "%.1f"|format((row.Max_Drawdown or 0) * 100) }}%</div></div>
                                        <div><div class="di-label">Data Quality</div><div class="di-val">{{ row.DataQuality|default('OK', true) }}</div></div>
                                    </div>
                                    <div class="rationale">{{ row.Rationale|default('No rationale available.', true) }}</div>
                                </td>
                            </tr>
                            {% endfor %}
                            {% if not portfolio_rows %}
                            <tr><td colspan="10" class="empty-note">No symbols were analysed this cycle.</td></tr>
                            {% endif %}
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
                    Raw JSON validation findings from the daily AI Verification Suite run.
                </p>
                <pre>{{ audit_log | tojson(indent=4) }}</pre>
            </div>
        </div>
    </div>

    <script>
        // ---- Tab switching ----
        function openTab(evt, tabName) {
            var i, tabcontent, tablinks;
            tabcontent = document.getElementsByClassName("tabcontent");
            for (i = 0; i < tabcontent.length; i++) { tabcontent[i].style.display = "none"; }
            tablinks = document.getElementsByClassName("tablinks");
            for (i = 0; i < tablinks.length; i++) { tablinks[i].className = tablinks[i].className.replace(" active", ""); }
            document.getElementById(tabName).style.display = "block";
            evt.currentTarget.className += " active";
        }

        // ---- Expand/collapse rationale detail rows ----
        function toggleDetail(id) {
            var el = document.getElementById(id);
            if (!el) return;
            el.style.display = (el.style.display === "none" || el.style.display === "") ? "table-row" : "none";
        }

        // ---- Client-side search filter ----
        function filterTable() {
            var q = document.getElementById("tableSearch").value.toLowerCase();
            var rows = document.querySelectorAll("#mainTable tbody tr.data-row");
            rows.forEach(function (r) {
                var hay = r.getAttribute("data-search") || "";
                var match = hay.indexOf(q) !== -1;
                r.style.display = match ? "" : "none";
                // Hide any expanded detail row belonging to a filtered-out data row.
                var detail = r.nextElementSibling;
                if (detail && detail.classList.contains("detail-row")) {
                    if (!match) detail.style.display = "none";
                }
            });
        }

        // ---- Column sort (data rows keep their detail row adjacency) ----
        var sortState = {};
        function sortTable(col, type) {
            var table = document.getElementById("mainTable");
            var tbody = table.tBodies[0];
            // Pair each data row with its following detail row.
            var pairs = [];
            var rows = Array.prototype.slice.call(tbody.querySelectorAll("tr.data-row"));
            rows.forEach(function (dr) {
                var detail = dr.nextElementSibling;
                if (detail && !detail.classList.contains("detail-row")) detail = null;
                pairs.push([dr, detail]);
            });
            var asc = !sortState[col];
            sortState = {}; sortState[col] = asc;
            pairs.sort(function (a, b) {
                var ca = a[0].cells[col], cb = b[0].cells[col];
                var va, vb;
                if (type === "num") {
                    va = parseFloat(ca.getAttribute("data-sort"));
                    vb = parseFloat(cb.getAttribute("data-sort"));
                    if (isNaN(va)) va = -Infinity; if (isNaN(vb)) vb = -Infinity;
                    return asc ? va - vb : vb - va;
                } else {
                    va = (ca.textContent || "").trim().toLowerCase();
                    vb = (cb.textContent || "").trim().toLowerCase();
                    return asc ? va.localeCompare(vb) : vb.localeCompare(va);
                }
            });
            pairs.forEach(function (p) {
                tbody.appendChild(p[0]);
                if (p[1]) tbody.appendChild(p[1]);
            });
        }

        // ---- Signal distribution doughnut ----
        document.addEventListener("DOMContentLoaded", function () {
            var ctx = document.getElementById('signalChart');
            if (ctx) {
                new Chart(ctx.getContext('2d'), {
                    type: 'doughnut',
                    data: {
                        labels: ['BUY', 'HOLD', 'SELL'],
                        datasets: [{
                            data: [{{ n_buy }}, {{ n_hold }}, {{ n_sell }}],
                            backgroundColor: ['rgba(16,185,129,0.7)', 'rgba(245,158,11,0.7)', 'rgba(239,68,68,0.7)'],
                            borderColor: ['#10b981', '#f59e0b', '#ef4444'],
                            borderWidth: 1
                        }]
                    },
                    options: {
                        responsive: true, maintainAspectRatio: false,
                        plugins: { legend: { position: 'bottom', labels: { color: '#9ca3af', font: { size: 10 } } } }
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
    audit_log: Dict[str, Any] = None,
    account_summary: Optional[Dict[str, Any]] = None,
    snapshot_diff: Optional[Dict[str, Any]] = None,
):
    """
    Render the daily advisory HTML report.

    Leads with Holdings & P&L (shares, average cost, current price, market
    value, unrealized P&L $ / %) and Action & Rationale (action signal,
    conviction, suggested position size, plain-English rationale).

    Parameters
    ----------
    portfolio_data:
        One dict per analysed symbol.  Accepts both the advisory schema
        (``main.py``) and the wide pipeline schema (``main_orchestrator.py``).
        All field access is defensive — missing keys degrade to "—".
    regime:
        Macro regime string (e.g. ``"RISK ON"``, ``"RECESSION"``).
    output_path:
        Destination HTML file.  Remains the 3rd positional parameter to
        preserve backward compatibility with existing callers.
    yield_curve, credit_spread, sahm_rule, real_yield:
        Macro context for the regime card.
    audit_log:
        Gravity AI verification JSON.  Falls back to the on-disk
        ``Gravity_Verification_Report.json`` then to a warning stub.
    account_summary:
        Optional portfolio-level totals dict.  When provided (``main.py``
        advisory path) the report renders a summary band; when ``None``
        (``main_orchestrator.py``) the band is hidden.  Expected keys:
        ``total_equity``, ``buying_power``, ``total_unrealized_pl``,
        ``total_dividends``, ``num_positions``, ``n_buy``, ``n_hold``,
        ``n_sell``, ``n_total``, ``fetched_at`` (str), ``age_hours`` (float),
        ``is_stale`` (bool).  Never contains secrets.
    snapshot_diff:
        Optional ``SnapshotDiff.to_dict()`` payload from
        :mod:`scripts.snapshot_diff`.  When present the report renders a
        "Δ Since Last Run" band at the top (new BUYs, action flips,
        conviction moves, holdings added/dropped, regime change).  When
        ``None`` (no prior snapshot or rotation failure) the band is
        hidden entirely.
    """
    telemetry.info("Generating Daily Jinja2 HTML Report (holdings + rationale layout)...")

    # 1. Gravity AI audit-log fallback chain
    if not audit_log:
        gravity_report_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "Gravity_Verification_Report.json"
        )
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
                    "timestamp": datetime.now().isoformat(),
                }
        else:
            audit_log = {
                "status": "WARNING",
                "message": "GravityAIAuditor payload missing for this execution cycle. Run ai_verification_prompts.py to generate.",
                "timestamp": datetime.now().isoformat(),
            }

    # 2. Sanitize NaN/Inf for JSON + Jinja safety
    cleaned_portfolio: List[Dict[str, Any]] = []
    for row in portfolio_data:
        clean_row = {}
        for k, v in row.items():
            if isinstance(v, float) and (np.isnan(v) or np.isinf(v)):
                clean_row[k] = None
            else:
                clean_row[k] = v
        cleaned_portfolio.append(clean_row)

    # 3. Field normalization — accept advisory (underscored) AND pipeline
    #    (spaced) keys; source holdings/P&L from the Robinhood snapshot fields
    #    when present, else fall back to a derived computation.
    def _num(row: Dict[str, Any], *keys, default: float = 0.0) -> float:
        """Return the first present, finite numeric value among ``keys``."""
        for key in keys:
            if key in row and row[key] is not None:
                try:
                    val = float(row[key])
                    if not (np.isnan(val) or np.isinf(val)):
                        return val
                except (TypeError, ValueError):
                    continue
        return default

    n_buy = n_hold = n_sell = 0
    for row in cleaned_portfolio:
        action = (row.get("Action_Signal") or row.get("Action Signal") or "HOLD")
        row["Action_Signal"] = action
        au = action.upper()
        if "SELL" in au or "RISK REDUCE" in au:
            n_sell += 1
        elif "BUY" in au:
            n_buy += 1
        else:
            n_hold += 1

        # Holdings (source of truth: Robinhood snapshot fields injected by main.py)
        row["Shares"]   = _num(row, "Shares", "Robinhood Shares")
        row["AvgCost"]  = _num(row, "AvgCost", "Robinhood Avg Cost")
        row["Price"]    = _num(row, "Price", "Robinhood Current Price")
        row["Dividends"] = _num(row, "Dividends", "Robinhood Dividends")
        row["CompanyName"] = (row.get("CompanyName") or row.get("Company Name")
                              or row.get("shortName") or "")

        # Market value: prefer snapshot value, else shares × price
        mv = _num(row, "MarketValue", "Robinhood Market Value", default=float("nan"))
        if np.isnan(mv):
            mv = row["Shares"] * row["Price"]
        row["MarketValue"] = mv

        # Unrealized P&L: prefer snapshot value, else (price - avg cost) × shares
        upl = _num(row, "UnrealizedPL", "Robinhood Unrealized PL", default=float("nan"))
        if np.isnan(upl):
            upl = (row["Price"] - row["AvgCost"]) * row["Shares"]
        row["UnrealizedPL"] = upl

        uplp = _num(row, "UnrealizedPLPct", "Robinhood Unrealized PL Pct", default=float("nan"))
        if np.isnan(uplp):
            cost_basis = row["AvgCost"] * row["Shares"]
            uplp = (upl / cost_basis) if cost_basis > 0 else 0.0
        row["UnrealizedPLPct"] = uplp

        # Action & rationale
        row["Conviction"] = _num(row, "Conviction", "Advisory_Conviction")
        row["SuggestedPct"] = _num(row, "SuggestedPct", "Advisory_Position_Pct", "Kelly Target", "Kelly_Size")
        row["Rationale"] = (row.get("Rationale") or row.get("Advisory_Rationale")
                            or row.get("Advice") or "")
        row["Strategy"] = row.get("Strategy") or row.get("strategy") or ""
        row["DataQuality"] = row.get("DataQuality") or row.get("data_quality") or "OK"

        # Forecast / risk detail
        row["Forecast_30D"] = _num(row, "Forecast_30D", "Forecast_30", "MC_Target")
        row["RSI"] = _num(row, "RSI", default=float("nan"))
        if np.isnan(row["RSI"]):
            row["RSI"] = None
        row["GARCH_Vol"] = _num(row, "GARCH_Vol", "GARCH Vol")
        row["Max_Drawdown"] = _num(row, "Max_Drawdown", "Max Drawdown")

    # 4. Portfolio heat (for the regime card) — averaged when supplied
    total_heat, count = 0.0, 0
    for row in cleaned_portfolio:
        total_heat += float(row.get("Portfolio_Heat", row.get("Portfolio Heat", 0.0)) or 0.0)
        count += 1
    avg_heat = total_heat / count if count > 0 else 0.0

    # 5. Enrich account_summary with derived signal tallies when present
    if account_summary is not None:
        account_summary.setdefault("n_buy", n_buy)
        account_summary.setdefault("n_hold", n_hold)
        account_summary.setdefault("n_sell", n_sell)
        account_summary.setdefault("n_total", len(cleaned_portfolio))

    # 6. Render
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
        account_summary=account_summary,
        snapshot_diff=snapshot_diff,
        n_buy=n_buy,
        n_hold=n_hold,
        n_sell=n_sell,
    )

    # 7. Persist
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

    # Mock advisory-schema portfolio (mirrors main.py's _write_html_report output)
    test_portfolio = [
        {
            "Symbol": "AAPL", "Action Signal": "BUY", "Advisory_Conviction": 0.72,
            "Advisory_Rationale": "Held above effective cost basis with a constructive 30-day forecast; "
                                  "RSI neutral and GARCH volatility contained. Suggested partial add.",
            "Advisory_Position_Pct": 0.043, "Forecast_30": 232.50,
            "Robinhood Shares": 12.0, "Robinhood Avg Cost": 180.25,
            "Robinhood Current Price": 214.10, "Robinhood Market Value": 2569.20,
            "Robinhood Unrealized PL": 406.20, "Robinhood Unrealized PL Pct": 0.1878,
            "Robinhood Dividends": 8.40, "Company Name": "Apple Inc.",
            "RSI": 54.2, "GARCH_Vol": 0.21, "Max Drawdown": -0.14,
            "data_quality": "OK", "strategy": "momentum_trend",
        },
        {
            "Symbol": "AGNC", "Action Signal": "SELL", "Advisory_Conviction": 0.81,
            "Advisory_Rationale": "Below effective cost basis with a bearish forecast and a credit-sensitive "
                                  "sector exposure; dividend cushion insufficient. Escalated to SELL.",
            "Advisory_Position_Pct": 0.0, "Forecast_30": 8.95,
            "Robinhood Shares": 300.0, "Robinhood Avg Cost": 11.40,
            "Robinhood Current Price": 9.62, "Robinhood Market Value": 2886.0,
            "Robinhood Unrealized PL": -534.0, "Robinhood Unrealized PL Pct": -0.1561,
            "Robinhood Dividends": 142.0, "Company Name": "AGNC Investment Corp.",
            "RSI": 31.0, "GARCH_Vol": 0.34, "Max Drawdown": -0.41,
            "data_quality": "OK", "strategy": "mean_reversion",
        },
        {
            "Symbol": "NVDA", "Action Signal": "HOLD", "Advisory_Conviction": 0.40,
            "Advisory_Rationale": "Not currently held; signal score neutral. No entry edge this cycle.",
            "Advisory_Position_Pct": 0.0, "Forecast_30": 131.0,
            "RSI": 61.5, "GARCH_Vol": 0.29, "Max Drawdown": -0.22,
            "data_quality": "STALE", "strategy": "neutral",
        },
    ]

    test_account_summary = {
        "total_equity": 41250.0, "buying_power": 5120.0,
        "total_unrealized_pl": -127.80, "total_dividends": 150.40,
        "num_positions": 2, "fetched_at": "2026-06-25T13:02:11+00:00",
        "age_hours": 1.4, "is_stale": False,
    }

    test_audit_log = {
        "status": "PASSED_WITH_WARNINGS",
        "findings": [
            "AGNC breached cost-basis SELL escalation rule. Advisory action confirmed.",
            "NVDA quote flagged STALE — sourced from delayed yfinance feed.",
        ],
    }

    generate_html_report(
        test_portfolio,
        regime="NEUTRAL",
        audit_log=test_audit_log,
        account_summary=test_account_summary,
    )
    telemetry.info("Diagnostic HTML report written to daily_report.html")
