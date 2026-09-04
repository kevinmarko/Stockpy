import re

with open("scripts/refresh_validations.py", "r") as f:
    content = f.read()

replacement = """    # UNGATEABLE_DATA_GAP Exclusions: 
    # These strategies are explicitly documented as NOT GATEABLE due to missing 
    # data sources or structural gaps in this sandbox. Their adapters raise 
    # RuntimeError so that the validation harness correctly reports their status 
    # rather than dropping them silently.
    "news_catalyst": (
        _build_ungateable_adapter("Point-in-time news sentiment; not price-only backtestable."),
        0.01,
        ["SPY"],
    ),
    "regime_multiplier": (
        _build_ungateable_adapter("A sizing multiplier only, not an independent alpha strategy capable of backing a Pilot."),
        0.0,
        ["SPY"],
    ),
    "forecast_alignment": (
        _build_ungateable_adapter("External forecast target, not price-only. Covered by forecast_direction_arima_hw pilot proxy."),
        0.0,
        ["SPY"],
    ),
    "earnings_crush": ("""

content = re.sub(
    r'    # UNGATEABLE_DATA_GAP Exclusions: \n    # These strategies are explicitly documented as NOT GATEABLE due to missing \n    # data sources or structural gaps in this sandbox\. Their adapters raise \n    # RuntimeError so that the validation harness correctly reports their status \n    # rather than dropping them silently\.\n    "earnings_crush": \(',
    replacement,
    content
)

with open("scripts/refresh_validations.py", "w") as f:
    f.write(content)
