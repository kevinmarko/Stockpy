import re

with open("scripts/refresh_validations.py", "r") as f:
    lines = f.readlines()

out = []
for i, line in enumerate(lines):
    out.append(line)
    if "UNGATEABLE_DATA_GAP Exclusions:" in line:
        out.append('    "news_catalyst": (\n        _build_ungateable_adapter("Point-in-time news sentiment; not price-only backtestable."), \n        0.01,\n        ["SPY"],\n    ),\n    "regime_multiplier": (\n        _build_ungateable_adapter("A sizing multiplier only, not an independent alpha strategy capable of backing a Pilot."),\n        0.0,\n        ["SPY"],\n    ),\n    "forecast_alignment": (\n        _build_ungateable_adapter("External forecast target, not price-only. Covered by forecast_direction_arima_hw pilot proxy."),\n        0.0,\n        ["SPY"],\n    ),\n')

with open("scripts/refresh_validations.py", "w") as f:
    f.writelines(out)
