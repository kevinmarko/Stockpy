# Shrink step 7: feature freeze (walkthrough)

## What
- The new `CLAUDE.md` / `AGENTS.md` section "Feature freeze (step 7, since 2026-09-29)" covers:
  - what is allowed;
  - what needs the operator's OK;
  - the exit condition.
- `scripts/feature_freeze_status.py` reports freeze progress. It is read-only, and its exit code is 0 when the minimum is reached and 2 while still frozen.
- `tests/test_feature_freeze_status.py` covers the script.

## Exit condition
30 closed pipeline paper trades, with a target of 50. Only `paper_closed_trades` rows with `strategy_id == "main_pipeline"` count; manual, delta-hedge and untagged rows don't.

## Prerequisites found while scoping (separate PRs)
The operator-defined exit condition could not be reached. The pipeline had never placed an automated paper order, for three reasons:
1. `BrokerExecutionStep` required Alpaca keys even on `BROKER_BACKEND=fmp_paper` (#1089).
2. Every Kelly Target was 0, because the Kelly scale-in is n/30 and n = 0. The fix is a paper-only 1% probe, `PAPER_PIPELINE_PROBE_WEIGHT` (#1090).
3. The executor closed positions only on SELL/TRIM, which `strategy_engine` never emits; its exit signal is RISK REDUCE (#1090).

## State at start
0 closed pipeline paper trades; the freeze is on.
