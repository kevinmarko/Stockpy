# Strategy & Options Backfill (2005–Present), Multi-Tab Integration, & Dual-Agent Audit Plan

## Overview
This plan establishes the end-to-end workflow to backfill and validate the key strategies/options (`vrp_premium_selling`, `put_credit_spread`, `call_credit_spread`, `call_debit_spread`, `put_debit_spread`, `sector_quality_rank`, `lgbm_ranker`, `options_flow_sentiment`) as far back as 2005, integrate them into the **Forecasting Backfill** and **Commands** tabs, persist the Numba-compiled event-driven sequential backtest engine, and deploy two specialized subagents (`honesty-auditor` and verification agent) to audit and double-check all calculations and invariants.

---

## User Review Required

> [!IMPORTANT]
> **Historical Data & Honest Scope (2005 vs Coverage Limits)**:
> 1. **Price Data (SPY & large-caps)**: Full daily OHLCV exists back to 2005-01-01 via `yfinance`/FMP.
> 2. **Options Premium & Macro Data**: Real VIX history exists back to 1990; FRED High Yield Spread (`BAMLH0A0HYM2`) coverage begins 2023-08-08 (pre-2023 dates use VIX-only gating to prevent false-pass survivals, per documented convention in `validation/options_selling_backtest.py`).
> 3. **Gate Standards**: No deployability thresholds (`PBO < 0.50`, `DSR > 0.95`, `Sharpe > 0.50`, `MaxDD < 30%`, and 4-scenario stress gate `< 50% DD` with 100% survival) will be loosened. Honest failures (e.g. debit spread drag or unhedged tail drag) will be recorded as `deployable=False` with measured evidence.

---

## Proposed Changes

### Component 1: Strategy Signal Meta-Labeling & Feature Declaration
Enable `AgenticForecastBackfiller` to recognize and train meta-labelers for the options and ranking signals.

#### [MODIFY] [signals/vrp_premium_selling.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/evaluate_four_trading_options/signals/vrp_premium_selling.py)
- Declare `meta_label_features = ["GARCH_Vol", "Vol_20", "Vol_50", "RSI_14", "SMA_200", "Vol_Ratio"]` and `meta_label_horizons = [10, 30, 60, 90]`.

#### [MODIFY] [signals/options_flow_sentiment.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/evaluate_four_trading_options/signals/options_flow_sentiment.py)
- Declare `meta_label_features = ["ROC_12M", "ROC_6M", "RSI_14", "Vol_20", "GARCH_Vol", "SMA_5", "SMA_200"]` and `meta_label_horizons = [10, 30, 60, 90]`.

#### [MODIFY] [signals/sector_quality_rank.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/evaluate_four_trading_options/signals/sector_quality_rank.py)
- Declare `meta_label_features = ["ROC_12M", "ROC_6M", "Vol_20", "Vol_50", "GARCH_Vol", "SMA_200"]` and `meta_label_horizons = [10, 30, 60, 90]`.

---

### Component 2: Numba-Accelerated Event-Driven Simulation Engine
Formalize the high-performance compiled sequential loop.

#### [NEW] [numba_backtest_loop.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/evaluate_four_trading_options/numba_backtest_loop.py)
- Provides `@njit` JIT-compiled event-driven sequential backtesting loop with path-dependent stop loss, slippage (5 bps), and fee calculation (10 bps) achieving >200M bars/sec throughput.

#### [NEW] [tests/test_numba_backtest_loop.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/evaluate_four_trading_options/tests/test_numba_backtest_loop.py)
- Unit tests verifying Numba execution correctness, state transition logic, stop-loss trigger edge cases, and deterministic fee/slippage calculation.

---

### Component 3: Commands Tab & CLI Introspection Synchronization
Ensure all strategy validation commands are available in the Commands tab and shell completions.

#### [MODIFY] [cli_introspect/command_manifest.json](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/evaluate_four_trading_options/cli_introspect/command_manifest.json)
- Regenerated with all registered options & validation targets.

#### [MODIFY] [completions/investyo.bash](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/evaluate_four_trading_options/completions/investyo.bash)
#### [MODIFY] [completions/investyo.zsh](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/evaluate_four_trading_options/completions/investyo.zsh)
- Synced autocomplete options with the updated manifest.

---

### Component 4: Historical 2005–Present Backfill Execution & Reports
Run walk-forward validation and backfilling from 2005-01-01 to present:
1. `sector_quality_rank` (2010-01-01 to 2026-08-15)
2. `lgbm_ranker` (2015-01-01 to 2026-08-15)
3. `vrp_premium_selling` (2005-01-01 to 2026-08-15, with 4 tail stress windows)
4. `put_credit_spread`, `call_credit_spread`, `call_debit_spread`, `put_debit_spread` (2005-01-01 to 2026-08-15)

---

### Component 5: Documentation & PR Artifacts
Per CLAUDE.md / AGENTS.md workflow rules:

#### [MODIFY] [docs/VALIDATION_STRATEGY_FIX_LOG.md](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/evaluate_four_trading_options/docs/VALIDATION_STRATEGY_FIX_LOG.md)
- Append dated entry summarizing the 2005–2026 validation pass across all options/strategies.

#### [MODIFY] [docs/signals/options_flow_sentiment.md](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/evaluate_four_trading_options/docs/signals/options_flow_sentiment.md)
- Add Backtest Validation section detailing empirical data coverage and meta-labeling features.

#### [NEW] [.claude/evaluate_four_trading_options_implementation_plan.md](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/evaluate_four_trading_options/.claude/evaluate_four_trading_options_implementation_plan.md)
#### [NEW] [.claude/evaluate_four_trading_options_task.md](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/evaluate_four_trading_options/.claude/evaluate_four_trading_options_task.md)
#### [NEW] [.claude/evaluate_four_trading_options_walkthrough.md](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/evaluate_four_trading_options/.claude/evaluate_four_trading_options_walkthrough.md)

---

## Multi-Agent Verification & Audit Workflow

We will invoke **two independent subagents**:
1. **`honesty-auditor` Subagent**:
   - Audit all signal feature declarations, backtest adapters, and data pipelines against `CONSTRAINT #4` (no fabricated numbers/mocked data) and `CONSTRAINT #6` (dead-letter resilience).
   - Verify that no lookahead bias is introduced into `numba_backtest_loop.py` or `signals/`.
2. **`test-writer` Subagent**:
   - Run the complete targeted test suite (`pytest tests/test_*.py`) and vitest suite (`npm --prefix webapp test`).
   - Validate that all strategy options produce valid reports and render cleanly in Strategy Health and Forecasting Backfill screens.
