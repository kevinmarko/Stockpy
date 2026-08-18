# Backtest Validation & Documentation Refresh

Run comprehensive backtest validation across all 28 registered quantitative strategies in `STRATEGY_REGISTRY`, generate fresh validation summaries, HTML reports, and history ledgers, and update platform documentation and fix logs.

## Background & Scope
The platform uses walk-forward **Combinatorial Purged Cross-Validation (CPCV)** with transaction cost modeling ([`TieredCostModel`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/improve_backtest_performance_logic/execution/cost_model.py)) to evaluate strategies against the quantitative deployability gate:
- **PBO** $< 0.50$
- **DSR** $> 0.95$
- **Net Sharpe** $> 0.50$
- **Max Drawdown** $< 30\%$
- **Options-selling stress gate** (MaxDD $< 50\%$ across 2008, 2018, 2020, 2024 shock windows)

Currently, the `reports/` directory contains templates but lacks fresh validation summary JSONs. Running the complete validation run updates all report artifacts, historical records, and platform documentation.

## User Review Required

> [!NOTE]
> - Strategy validation will be executed across all 28 strategies concurrently using multi-threaded execution (`--workers 4`).
> - Point-in-time (PIT) fundamentals strategies (`dividend_yield_edgar_pit`, `deep_value_edgar_pit`, `value_quality_edgar_pit`) and macro regime strategies (`macro_regime_pit`) will read persisted historical fundamentals and macro series from `quant_platform.db`.
> - Any strategy that genuinely does not clear the deployability gate will report `deployable=False` honestly per the platform's quant integrity constraints.

## Proposed Changes

### 1. Execute Backtest Validation Fleet

#### [EXECUTE] Run `scripts/refresh_validations.py`
Run walk-forward CPCV for all 28 registered strategies:
- Tickers: Download price history via yfinance across required ticker unions (`SPY`, `_XSEC_UNIVERSE_30`, sector/options tickers).
- Window: `2005-01-01` to current date.
- Workers: 4 concurrent threads.

Output artifacts generated in `reports/`:
- `<strategy>_validation_summary.json` (consumed by [`check_validation_reports`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/improve_backtest_performance_logic/scripts/preflight_check.py) and [`PreTradeRiskGate`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/improve_backtest_performance_logic/execution/risk_gate.py))
- `validation_<strategy>_<timestamp>.html` (interactive HTML equity curve and metric reports)
- `cpcv_<strategy>_<timestamp>.html` (CPCV overfitting audit charts)
- `history/<strategy>_validation_history.jsonl` (accumulated run ledger for historical tracking)

---

### 2. Documentation Updates

#### [MODIFY] [`docs/VALIDATION_STRATEGY_FIX_LOG.md`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/improve_backtest_performance_logic/docs/VALIDATION_STRATEGY_FIX_LOG.md)
- Append a dated 2026-08 full rollup table covering all 28 registered strategies with their refreshed Sharpe, PBO, DSR, Max Drawdown, and deployable status.
- Document any specific performance observations, empirical turnover calibrations, or honest constraint outcomes.

#### [MODIFY] [`docs/signals/<name>.md`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/improve_backtest_performance_logic/docs/signals/README.md)
- Update or add `## Backtest Validation` sections for signal documentation files corresponding to strategies in `STRATEGY_REGISTRY` to ensure metrics match the refreshed run.

#### [NEW] [`.claude/backtest_validation_refresh_implementation_plan.md`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/improve_backtest_performance_logic/.claude/backtest_validation_refresh_implementation_plan.md)
#### [NEW] [`.claude/backtest_validation_refresh_task.md`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/improve_backtest_performance_logic/.claude/backtest_validation_refresh_task.md)
#### [NEW] [`.claude/backtest_validation_refresh_walkthrough.md`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/improve_backtest_performance_logic/.claude/backtest_validation_refresh_walkthrough.md)
- Copy project-scoped plan, task tracker, and walkthrough to `.claude/` per repository PR requirements.

---

## Verification Plan

### Automated Backtests & Preflight Validation
1. Execute full validation pass:
   ```bash
   /Users/kevinlee/Stockpy-live/.venv/bin/python -m scripts.refresh_validations --workers 4 --json
   ```
2. Verify preflight readiness gate:
   ```bash
   /Users/kevinlee/Stockpy-live/.venv/bin/python scripts/preflight_check.py --validation-staleness-only
   ```
3. Run validation test suite:
   ```bash
   /Users/kevinlee/Stockpy-live/.venv/bin/python -m pytest tests/test_validation_*.py tests/test_harness_*.py tests/test_metrics_*.py
   ```
