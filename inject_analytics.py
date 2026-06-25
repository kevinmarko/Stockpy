import re

with open('daily_report_template.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Inject DataTables CSS
css_tag = '<link rel="stylesheet" type="text/css" href="https://cdn.datatables.net/1.13.6/css/jquery.dataTables.css">\n'
content = content.replace('</head>', css_tag + '</head>')

# Add custom css for DataTables dark theme compatibility
dark_dt_css = """
        /* DataTables Dark Mode Overrides */
        .dataTables_wrapper .dataTables_length, .dataTables_wrapper .dataTables_filter, .dataTables_wrapper .dataTables_info, .dataTables_wrapper .dataTables_processing, .dataTables_wrapper .dataTables_paginate {
            color: var(--text-muted);
            font-size: 13px;
        }
        .dataTables_wrapper .dataTables_paginate .paginate_button {
            color: var(--text-main) !important;
        }
        table.dataTable tbody tr {
            background-color: transparent;
        }
        table.dataTable.no-footer {
            border-bottom: 1px solid var(--border-color);
        }
        table.dataTable thead th, table.dataTable thead td {
            border-bottom: 1px solid var(--border-color);
        }
"""
content = content.replace('</style>', dark_dt_css + '</style>')

# Construct Analytics Table
analytics_table_html = """
        <!-- Post-Trade Execution Analytics Section -->
        <h2 style="font-size: 18px; margin-bottom: 20px; font-weight: 600; border-left: 4px solid var(--accent-color); padding-left: 10px;">Post-Trade Execution & Portfolio Analytics</h2>
        <div class="summary-card" style="padding: 20px; overflow-x: auto;">
            <table id="analytics-table" class="display" style="width:100%">
                <thead>
                    <tr>
                        <th>Ticker</th>
                        <th>Price</th>
                        <th>Position Size</th>
                        <th>Max Favorable Excursion (MFE)</th>
                        <th>Max Adverse Excursion (MAE)</th>
                        <th>BF Allocation Effect</th>
                        <th>BF Selection Effect</th>
                        <th>Portfolio Heat</th>
                    </tr>
                </thead>
                <tbody>
                    {% for row in portfolio_rows %}
                    <tr>
                        <td><strong>{{ row.Symbol }}</strong></td>
                        <td>${{ "%.2f"|format(row.Price) }}</td>
                        <td>${{ "{:,.2f}".format(row.position_size) if row.position_size is not none else "0.00" }}</td>
                        <td style="color: var(--success-color);">{{ "%.2f"|format(row.MFE * 100) if (row.MFE is not none and row.MFE == row.MFE) else "N/A — no trade history" }}{% if row.MFE is not none and row.MFE == row.MFE %}%{% endif %}</td>
                        <td style="color: var(--danger-color);">{{ "%.2f"|format(row.MAE * 100) if (row.MAE is not none and row.MAE == row.MAE) else "N/A — no trade history" }}{% if row.MAE is not none and row.MAE == row.MAE %}%{% endif %}</td>
                        <td>{{ "%.4f"|format(row.BF_Allocation) if row.BF_Allocation is not none else "0.0000" }}</td>
                        <td>{{ "%.4f"|format(row.BF_Selection) if row.BF_Selection is not none else "0.0000" }}</td>
                        <td>{{ "%.4f"|format(row.Portfolio_Heat) if row.Portfolio_Heat is not none else "0.0000" }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
"""

# Insert before "Detailed Insights Section"
insights_start = content.find('<!-- Detailed Insights Section -->')
content = content[:insights_start] + analytics_table_html + content[insights_start:]

# Add jQuery and DataTables JS at the end of the body
js_tags = """
    <script src="https://code.jquery.com/jquery-3.7.1.min.js"></script>
    <script type="text/javascript" charset="utf8" src="https://cdn.datatables.net/1.13.6/js/jquery.dataTables.js"></script>
    <script>
        $(document.ready(function() {
            $('#analytics-table').DataTable({
                "pageLength": 10,
                "order": [[ 7, "desc" ]], // Sort by Portfolio Heat descending
                "language": {
                    "search": "Filter Tickers:"
                }
            });
        });
    </script>
"""
# Replace <script> containing portfolioData
script_tag = '<script>'
content = content.replace(script_tag, js_tags.replace('$(document.ready', '$(document).ready') + script_tag, 1)

with open('daily_report_template.html', 'w', encoding='utf-8') as f:
    f.write(content)
