from jinja2 import Template
import pandas as pd
with open('daily_report_template.html', 'r', encoding='utf-8') as f:
    t = Template(f.read())
print("Jinja parsed successfully")
