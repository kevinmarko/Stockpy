# Options desk cut — step 3d walkthrough (orchestration hotspots)

Part of the shrink-in-place plan, step 3: cut the options desk out of core, one PR per
hotspot (PR 0 → 3a → 3b → 3c → **3d** → 3d′ → 3e → 3f). Step 4 then moves the orphaned
modules to `legacy/`.

## What 3d does

The pipeline's old `OptionsAnalysisStep` was not purely options: three things the equity
path relies on lived inside it or inside `technical_options_engine.py`. They were **moved**,
not deleted:

1. **GJR-GARCH** → `volatility/garch.py::GarchVolatilityEstimator` (verbatim copy of
   `sanitize_ohlcv`, `estimate_gjr_garch_volatility`,
   `estimate_gjr_garch_volatility_term_structure`). `GARCH_Vol` drives cold-start Kelly
   sizing on both the pipeline (`strategy_engine.py`) and advisory (`engine/advisory.py`)
   paths; with 0 closed trades a missing GARCH would zero every Kelly Target and every
   advisory BUY size.
2. **Macro DTO construction** → `pipeline/production_steps.py::MacroStep` (body unchanged).
3. **Aroon / Coppock / Chandelier** → `trend_indicators.py::calculate_trend_exit_indicators`
   (verbatim). The plan assumed these could be sourced from `processing_engine`'s own
   columns; they can't — `processing_engine` uses Aroon length 25 (vs 14), a different
   Coppock window, and only a long Chandelier. Swapping would have changed signals, so the
   exact code moved instead.

`TrendVolatilityStep` is what's left of the old step: per-ticker GARCH + trend indicators.
`TechnicalOptionsEngine`'s methods now delegate to the core modules so the options desk keeps
working until step 4.

Removed from core: `iv_engine` reads (30-day ATM IV, True IVR, VRP, IV history writes),
realized-vol rank, the options strategy matrix, `StrategyEngine._select_options_overlay`
(the text-only "OPTIONS HEDGE" note; `Option Strategy` is now `""`), the options premium
matrix snapshot, and the options lifecycle / 0DTE exits / options execution queue in
`main.py`, `main_orchestrator.py` and `desktop/daemon_runtime.py`.

`True_IVR` / `VRP` / `Realized_Vol_Rank` / `Option Strategy` stay in `COLUMN_SCHEMA` as
NaN/blank until the schema trim in step 4 (`StateSnapshotStep` selects them by name).

## Equivalence gate (how it was verified)

A sandboxed harness (`LOCAL_DATA_ROOT` pointed at a temp dir; no real DB touched) ran one
`main_orchestrator.run_pipeline()` cycle plus `engine.advisory.evaluate()` for every symbol
on frozen synthetic inputs (6 tickers × 600 bars with a mid-series vol shock, fixed macro
and fundamentals, a stub market provider), once on a `git archive` of the pre-3d commit and
once on the 3d tree, then diffed every dashboard column, the macro DTO and every advisory
recommendation.

- Two baseline-vs-baseline runs showed the CNN-LSTM and Prophet forecasts (and anything
  derived from them) vary run to run, so a first before/after diff flipped one ticker's
  Action Signal through `forecast_alignment`. To separate that noise from the change, both
  stochastic forecasters were stubbed with the same deterministic function in both runs.
- With that, before vs after differ **only** in:
  - `Option Strategy` (now `""`) and `Realized_Vol_Rank` (now NaN) — the allowed options
    columns (`True_IVR`/`VRP` were already NaN in the baseline, no IV source);
  - the "OPTIONS HEDGE: …" line of `Strategy Explainer Notes` (every other line identical);
  - ≤1.2e-13 float noise in the multifactor z-scores/`Score_Components`, which also appears
    between two identical baseline runs.
- Identical: `Action Signal`, `Score`, `Kelly Target`, `GARCH_Vol`, buy/sell ranges,
  `MC_*`, every `Forecast_*`, the trend indicators, the macro DTO, and all six advisory
  recommendations.
- The same cycle with `technical_options_engine`, `volatility.iv_engine`,
  `pilots.options_risk`, `execution.options_lifecycle`, `execution.options_queue_builder`,
  `pilots.zero_dte_engine`, `reporting.options_snapshot`,
  `execution.dynamic_circuit_breaker` and `execution.sec_rule_606_reporter` all blocked via
  `sys.modules[...] = None` runs to completion with identical output — step 4's moves are
  safe for the pipeline. An import smoke test (`main`, `main_orchestrator`,
  `execution.fmp_paper_broker`, `broker_live_execution_mcp`, the engines, the daemon) also
  passes with those modules blocked.

`tests/test_garch_extraction_equivalence.py` keeps the numeric half of this under test with
golden values captured from the pre-move code.

## Tests changed

- New: `tests/test_volatility_garch.py` (GARCH/indicator tests moved from
  `test_technical_options_engine.py` + a delegates-match-core check),
  `tests/test_garch_extraction_equivalence.py`.
- Renamed/retargeted: `test_options_analysis_step_macro_dto.py` → `test_macro_step.py`,
  `test_options_analysis_step_garch_none.py` → `test_trend_volatility_step.py`.
- Updated: engine-context, stage-order, columns-contract, advisory/forecasting patch
  targets (`engine.advisory.GarchVolatilityEstimator`,
  `volatility.garch.GarchVolatilityEstimator.*`), the overlay assertions in
  `test_strategy_engine.py` / `test_quantitative_models.py` / `test_signal_parity.py`.
- Replaced with guards: the daemon and `main.py` options-lifecycle tests (the lifecycle
  module keeps its own `tests/test_options_lifecycle.py` until step 4).

## Next

3d′ — retire the two options signal modules (`vrp_premium_selling`,
`options_flow_sentiment`), a disclosed scoring change for the advisory path.
