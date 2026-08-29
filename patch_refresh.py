import re

with open("scripts/refresh_validations.py", "r") as f:
    content = f.read()

ungateable_block = """
    # UNGATEABLE_DATA_GAP Exclusions: 
    # These strategies are explicitly documented as NOT GATEABLE due to missing 
    # data sources or structural gaps in this sandbox. Their adapters raise 
    # RuntimeError so that the validation harness correctly reports their status 
    # rather than dropping them silently.
    "news_catalyst": (
        _build_ungateable_adapter("No historical point-in-time news history exists to perform walk-forward validation (violates CONSTRAINT #4)."),
        0.01,
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
}
"""

content = re.sub(r'\}\n*$', ungateable_block, content, flags=re.MULTILINE)

with open("scripts/refresh_validations.py", "w") as f:
    f.write(content)
