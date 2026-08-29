import re

with open("scripts/refresh_validations.py", "r") as f:
    text = f.read()

# Make sure _build_ungateable_adapter is defined
if "_build_ungateable_adapter" not in text:
    adapter_code = """
def _build_ungateable_adapter(reason: str):
    def adapter(*args, **kwargs):
        raise RuntimeError(f"UNGATEABLE_DATA_GAP: {reason}")
    return adapter

STRATEGY_REGISTRY"""
    text = text.replace("STRATEGY_REGISTRY", adapter_code, 1)

missing = """
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
    "earnings_crush": (
        _build_ungateable_adapter("No historical single-name IV exists in data layer to perform walk-forward validation."),
        0.01,
        ["SPY"],
    ),
    "dispersion_trading": (
        _build_ungateable_adapter("Index IV (VIX) is historical; constituent single-name IVs are substituted (+1.18 vol-pt substitution bias)."),
        0.01,
        ["SPY"],
    ),
    "zero_dte_engine": (
        _build_ungateable_adapter("No 1-minute intraday history exists for mandatory historical stress windows outside 30-day retention."),
        0.01,
        ["SPY"],
    ),
    "gamma_scalper": (
        _build_ungateable_adapter("Excluded — not a strategy (no scan/evaluate/execute path, no PaperAccountStore import, its only threshold is a hedge band)."),
        0.01,
        ["SPY"],
    ),
}"""

text = re.sub(r'\}\n\n\n# The subset of STRATEGY_REGISTRY entries', missing + '\n\n\n# The subset of STRATEGY_REGISTRY entries', text)

with open("scripts/refresh_validations.py", "w") as f:
    f.write(text)
