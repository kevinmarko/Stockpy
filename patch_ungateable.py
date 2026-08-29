import re

with open("scripts/refresh_validations.py", "r") as f:
    content = f.read()

ungateable_block = """
    "pairs_trading": (_build_pairs_trading_adapter, 0.04, ["SPY", "XOM", "CVX"]),
    # turnover=0.04: the entry/exit/stop z-score gate (entry |Z|>=~2.0, exit at
    # 0-cross, stop at |Z|>=4.0) produces roughly one full round-trip every
    # ~25 trading days on a KO/PEP-scale mean-reversion cycle -- 1/25 ~= 0.04,
    # matching pairs_trading's own turnover order of magnitude above (same
    # entry/exit/stop shape, same asset-class liquidity), not an independently
    # re-measured value for this specific pair.
    "copula_stat_arb": (_build_copula_stat_arb_adapter, 0.04, ["KO", "PEP"]),
    "aroon_trend": (_build_aroon_trend_adapter, 0.02, ["SPY"]),

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

content = re.sub(
    r'    "pairs_trading": \(_build_pairs_trading_adapter, 0\.04, \["SPY", "XOM", "CVX"\]\),\n    # turnover=0\.04:.*?    "copula_stat_arb": \(_build_copula_stat_arb_adapter, 0\.04, \["KO", "PEP"\]\),\n    "aroon_trend": \(_build_aroon_trend_adapter, 0\.02, \["SPY"\]\),\n\}',
    ungateable_block.strip(),
    content,
    flags=re.DOTALL
)

with open("scripts/refresh_validations.py", "w") as f:
    f.write(content)
