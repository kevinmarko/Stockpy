import pandas as pd
from jinja2 import Template
from datetime import datetime
import logging
import os

class ReportingEngine:
    """
    Automates the generation of the static daily HTML report using Jinja2
    and the final export dataframe.
    """
    def __init__(self, template_path="daily_report_template.html", output_path="daily_report.html"):
        self.template_path = template_path
        self.output_path = output_path
        self.logger = logging.getLogger("ReportingEngine")
        
    def generate_daily_report(self, export_df: pd.DataFrame, market_state: str = "NEUTRAL"):
        """
        Reads the Jinja2 template and writes the dynamic HTML report.
        """
        try:
            if not os.path.exists(self.template_path):
                self.logger.error(f"Template {self.template_path} not found.")
                return

            with open(self.template_path, 'r', encoding='utf-8') as file:
                template_str = file.read()
                
            template = Template(template_str)
            
            # Map macro states to CSS classes
            state_class_map = {
                "RECESSION": "recession",
                "RISK_ON": "risk-on",
                "NEUTRAL": "neutral",
                "EXPANSION": "risk-on"
            }
            market_state_class = state_class_map.get(market_state.upper(), "neutral")
            
            # Convert DF to list of dicts for jinja
            portfolio_rows = export_df.to_dict(orient='records')
            
            rendered_html = template.render(
                generated_timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                market_state=market_state.upper(),
                market_state_class=market_state_class,
                portfolio_rows=portfolio_rows
            )
            
            with open(self.output_path, 'w', encoding='utf-8') as file:
                file.write(rendered_html)
                
            self.logger.info(f"✅ Successfully generated dynamic daily report at {self.output_path}")
            
        except Exception as e:
            self.logger.error(f"❌ Failed to generate daily report: {e}")
