# Autonomous Backtest Synthetic Data Undisclosed

**Date:** 2026-08-27
**Status:** Resolved

## Issue Summary
The `validation/autonomous_backtest_runner.py` autonomous backtest runner was falling back to generated synthetic OHLCV data when real historical data was unavailable or insufficient (length < 50). This synthetic data run was incorrectly allowed to report `is_deployable: true` if it passed the quantitative gates (PBO, DSR, Sharpe, MaxDD). This exposed a severe risk where paper-broker code could be deployed based entirely on a backtest run on hallucinated data.

## Root Cause
The fallback mechanism to `generate_synthetic_ohlcv` in `api/pilots_api.py` generated data and passed it to the runner without marking it as synthetic. The runner evaluated the performance over this fake data and, since it has no way to know it wasn't real market data, set `is_deployable = True` when the strategy accidentally performed well. The frontend also did not surface this state to the user, who would see a "🚀 DEPLOYABLE" badge and a "Deploy to Paper Broker" button.

## Resolution
1. **Runner Updates**: Added `data_source` and `is_synthetic_data` to `AutonomousBacktestResult`. In `run()`, we now check if `data_source != "real_historical_bars"`. If so, and the strategy would otherwise pass the gates, we force `is_deployable = False` with an explicit failure reason: `"NOT DEPLOYABLE: backtest ran on data_source='synthetic_demo_data' -- a synthetic-data run can never certify real-market deployability."` This is an **allowlist**, not a denylist: `is_deployable` can only ever be `True` when `data_source == "real_historical_bars"`, so the default `data_source="unknown"` (a caller that forgets to pass it) also fails closed rather than slipping through.
2. **API Updates**: Threaded `data_source="real_historical_bars"` vs `"synthetic_demo_data"` from `pilots_api.py` into the runner.
3. **Frontend Updates**: Threaded the new fields through `webapp/src/api/types.ts` and `webapp/src/api/mock.ts`. Updated `ResearchCopilotView.tsx` to display a visible warning banner (`⚠️ SYNTHETIC DATA FALLBACK`) and to explicitly hide the "Deploy to Paper Broker" button when `is_synthetic_data` is true, ensuring operators cannot click deploy even if a bug in the backend incorrectly reported `is_deployable: true`.

## Verification

A follow-up audit pass (by an independent agent, then reproduced by hand) found the
first version of this fix shipped without the one test that actually *proves* the
override fires — every existing assertion happened to already be `False` for an
unrelated reason (e.g. a real fixture's DSR never clearing `DSR_MIN=0.95` in the
first place), so nothing in the original test suite would have caught a regression
in the override logic itself. Closed by adding:

- `tests/test_autonomous_backtest_runner.py::TestDeployabilityGates::test_synthetic_data_source_forces_not_deployable_even_when_all_gates_pass`
  — constructs a strategy/threshold combination that genuinely clears **all four**
  gates (`pbo_gate`, `dsr_gate`, `sharpe_gate`, `max_dd_gate` all `True`), confirms
  `is_deployable is True` under `data_source="real_historical_bars"`, then re-runs
  the identical strategy/data/thresholds under `data_source="synthetic_demo_data"`
  and asserts the override forces `is_deployable is False` with the explicit
  synthetic-data failure reason present — plus a third run with the default
  (`"unknown"`) data_source confirming the allowlist, not a denylist, is what's
  enforced. Real result: `33 passed` in `tests/test_autonomous_backtest_runner.py`.
- `tests/test_pilots_paper_broker.py::TestPilotsAIResearchBacktest::test_backtest_falls_back_to_synthetic_data_when_bars_insufficient` /
  `test_backtest_falls_back_to_synthetic_data_when_bars_fetch_raises` — the endpoint
  test previously relied on 2 assertions appended to `test_backtest_success`, which
  never mocked `HistoricalStore` and only exercised the real-data branch because
  this environment's local DB cache happened to already hold real SPY bars — the
  `<50`-row/exception fallback branch itself had zero controlled test coverage.
  Both new tests explicitly mock `HistoricalStore.get_bars` (one returning a
  10-row DataFrame, one raising) so the outcome is deterministic regardless of
  what's cached wherever the suite runs; `test_backtest_success` was also fixed
  to mock a controlled ≥50-row fixture rather than depending on ambient data.
  Real result: `8 passed` in `TestPilotsAIResearchBacktest`.
- `npm run --prefix webapp typecheck` — clean.
- `npx vitest run src/components/ai/ResearchCopilotView.test.tsx` — `8 passed`.

## What this does not fix

- The endpoint's ad-hoc `is_deployable` field is a completely separate concept
  from `validation/harness.py::ValidationReport.deployable` (the harness-vetted
  `STRATEGY_REGISTRY` gate covered by `docs/VALIDATION_STRATEGY_FIX_LOG.md`) —
  they share similar names but nothing else. This fix does not merge or
  reconcile the two; an operator should not read this tool's verdict as
  equivalent to a strategy having cleared the real deployability gate.
- This fix only closes the specific "synthetic OHLCV fallback" data-integrity
  gap. It does not audit whether the CPCV/PBO/DSR math itself is correctly
  computed for arbitrary freeform strategy code submitted through this
  endpoint — that is `validation/autonomous_backtest_runner.py`'s own
  pre-existing, separately-tested responsibility.
