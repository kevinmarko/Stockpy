import re

with open('daily_report.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace the summary table rows
summary_tbody_start = content.find('<tbody>') + len('<tbody>')
summary_tbody_end = content.find('</tbody>')
summary_rows_text = content[summary_tbody_start:summary_tbody_end]

jinja_summary_row = """
                    {% for row in portfolio_rows %}
                    <tr id="row-{{ row.Symbol }}">
                        <td><strong>{{ row.Symbol }}</strong></td>
                        <td>${{ "%.2f"|format(row.Price) }}</td>
                        <td>
                            <span id="signal-{{ row.Symbol }}" class="signal-tag signal-{{ row['Action Signal']|replace(' ', '_') if row['Action Signal'] else '' }}">
                                {{ row['Action Signal'] }}
                            </span>
                        </td>
                        <td id="sizing-{{ row.Symbol }}">
                            <strong style="color: var(--accent-color);">
                                {% set kelly = row['Kelly Target'] %}
                                {% if kelly is not none %}
                                    {{ "%.2f"|format(kelly * 100) }}%
                                {% else %}
                                    0.00%
                                {% endif %}
                            </strong> (Half-Kelly)
                        </td>
                        <td>{{ row['Option Strategy'] if row['Option Strategy'] else 'N/A' }}</td>
                        <td><code id="range-{{ row.Symbol }}" style="background-color: rgba(255,255,255,0.05); padding: 2px 6px; border-radius: 4px; font-size: 12px;">{{ row.buyRange if row.buyRange else 'N/A' }}</code></td>
                    </tr>
                    {% endfor %}
"""
content = content[:summary_tbody_start] + jinja_summary_row + content[summary_tbody_end:]

# Replace ticker cards
ticker_grid_start = content.find('<div class="ticker-grid">') + len('<div class="ticker-grid">')
ticker_grid_end = content.find('<!-- Client-Side Recalculator Script -->')

jinja_ticker_card = """
            {% for row in portfolio_rows %}
            <div class="ticker-card">
                <div class="ticker-card-header">
                    <div class="ticker-identity">
                        <h2>{{ row.Symbol }}</h2>
                        <div class="company-name">{{ row.shortName }}</div>
                        <div class="ticker-sector">{{ row.sector }}</div>
                    </div>
                    <div class="ticker-price-info">
                        <div class="ticker-price">${{ "%.2f"|format(row.Price) }}</div>
                        <div class="timestamp" style="font-size: 11px;">Latest Close Price</div>
                    </div>
                </div>

                <div class="metrics-grid">
                    <div class="metrics-section">
                        <div class="metrics-section-title">Valuation & Fundamentals</div>
                        <div class="metric-row"><span class="metric-label">Graham Number</span><span class="metric-value">${{ "%.2f"|format(row.graham_number) }}</span></div>
                        <div class="metric-row"><span class="metric-label">Gordon Fair Value</span><span class="metric-value">${{ "%.2f"|format(row['Gordon Fair Value']) }}</span></div>
                        <div class="metric-row"><span class="metric-label">Dividend Yield</span><span class="metric-value">{{ "%.2f"|format(row['Div Yield']) }}%</span></div>
                        <div class="metric-row"><span class="metric-label">Trailing P/E</span><span class="metric-value">{{ "%.2f"|format(row['P/E']) }}</span></div>
                        <div class="metric-row"><span class="metric-label">Book Value</span><span class="metric-value">${{ "%.2f"|format(row['Book Value']) }}</span></div>
                    </div>

                    <div class="metrics-section">
                        <div class="metrics-section-title">Risk & Technical Indicators</div>
                        <div class="metric-row"><span class="metric-label">RSI (14)</span><span class="metric-value">{{ "%.1f"|format(row.RSI) }}</span></div>
                        <div class="metric-row"><span class="metric-label">Beta</span><span class="metric-value">{{ "%.2f"|format(row.Beta) }}</span></div>
                        <div class="metric-row"><span class="metric-label">Max Drawdown</span><span class="metric-value">{{ "%.2f"|format(row['Max Drawdown'] * 100) }}%</span></div>
                        <div class="metric-row"><span class="metric-label">VaR 95%</span><span class="metric-value">{{ "%.2f"|format(row['VaR 95'] * 100) }}%</span></div>
                        <div class="metric-row"><span class="metric-label">Sortino Ratio</span><span class="metric-value">{{ "%.2f"|format(row['Sortino Ratio']) }}</span></div>
                    </div>

                    <div class="metrics-section">
                        <div class="metrics-section-title">Quant Metrics & Forecasts</div>
                        <div class="metric-row"><span class="metric-label">Institutional Velocity</span><span class="metric-value">{{ "%.2f"|format(row['Institutional Velocity']) }}</span></div>
                        <div class="metric-row"><span class="metric-label">Tail Dependency (CoVaR)</span><span class="metric-value">{{ "%.4f"|format(row['CoVaR Proxy']) }}</span></div>
                        <div class="metric-row"><span class="metric-label">Forecast (10 Day)</span><span class="metric-value">${{ "%.2f"|format(row['Forecast_10']) }}</span></div>
                        <div class="metric-row"><span class="metric-label">Forecast (30 Day)</span><span class="metric-value">${{ "%.2f"|format(row['Forecast_30']) }}</span></div>
                        <div class="metric-row"><span class="metric-label">Forecast (90 Day)</span><span class="metric-value">${{ "%.2f"|format(row['Forecast_90']) }}</span></div>
                    </div>
                </div>

                <div class="advice-callout">
                    <div class="advice-header">
                        <div class="advice-item">
                            <span class="advice-label">Action Signal</span>
                            <span id="card-signal-{{ row.Symbol }}" class="advice-val signal-{{ row['Action Signal']|replace(' ', '_') if row['Action Signal'] else '' }}">{{ row['Action Signal'] }}</span>
                        </div>
                        <div class="advice-item">
                            <span class="advice-label">Sizing (Half-Kelly)</span>
                            <span class="advice-val" style="color: var(--accent-color);">
                                <span id="card-sizing-{{ row.Symbol }}">
                                    {% set kelly = row['Kelly Target'] %}
                                    {% if kelly is not none %}
                                        {{ "%.2f"|format(kelly * 100) }}%
                                    {% else %}
                                        0.00%
                                    {% endif %}
                                </span>
                            </span>
                        </div>
                        <div class="advice-item">
                            <span class="advice-label">Buy Corridor Range</span>
                            <span id="card-range-{{ row.Symbol }}" class="advice-val" style="color: var(--success-color);">{{ row.buyRange if row.buyRange else 'N/A' }}</span>
                        </div>
                        <div class="advice-item">
                            <span class="advice-label">Option Strategy</span>
                            <span class="advice-val">{{ row['Option Strategy'] if row['Option Strategy'] else 'N/A' }}</span>
                        </div>
                    </div>
                    <pre id="notes-{{ row.Symbol }}" class="strategy-notes">{{ row['Strategy Explainer Notes'] }}</pre>
                </div>
            </div>
            {% endfor %}
        </div>
"""

# The script section starts after ticker_grid
# To ensure we don't clobber the script, we only replace up to the end of the div
div_end_idx = content.rfind('</div>\n\n    <!-- Client-Side Recalculator Script -->')
if div_end_idx != -1:
    ticker_grid_end = div_end_idx + len('</div>')
else:
    ticker_grid_end = content.find('    <!-- Client-Side Recalculator Script -->')

content = content[:ticker_grid_start] + jinja_ticker_card + content[ticker_grid_end:]

# Replace static json array in script
json_start = content.find('const portfolioData = [{')
json_end = content.find('}];\n', json_start)
if json_start != -1 and json_end != -1:
    content = content[:json_start] + 'const portfolioData = {{ portfolio_rows | tojson }};\n' + content[json_end+len('}];\n'):]

with open('daily_report_template.html', 'w', encoding='utf-8') as f:
    f.write(content)
