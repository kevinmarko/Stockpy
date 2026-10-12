# Walkthrough: stop the benchmark SPY leaking into the daemon's per-symbol pipeline

Plan (approved 2026-10-11): `.claude/fix_advisory_spy_leak_implementation_plan.md`.
Root cause and fix detail: `docs/known_issues/daemon_benchmark_spy_leak.md`.

## Why now

The step-5 primary cutover is on hold after the 2026-10-05 shadow review, and step 5.5 (archive `main.py`) can't land before it. One named blocker was that `AdvisoryOverlayStep` evaluated SPY, which was only ever fetched as a benchmark.

## What changed

| File | Change |
|---|---|
| `main_orchestrator.py` | `BENCHMARK_SYMBOLS = ("SPY",)`, used by `fetch_all_data_async`. No behaviour change |
| `pipeline/production_steps.py` | Adds `_drop_benchmark_only_rows()`, called right after `compile_dashboard` in `ProcessingStep`, so funnel diagnostics count honestly. Xsec momentum now ranks `ctx.symbols` only |
| `tests/test_benchmark_spy_leak.py` | 15 tests: 9 unit tests on the helper, 4 full `run_pipeline` tests, 2 constant/RS checks |
| docs | New known-issue file + README row; one bullet in `docs/architecture/signal-engines.md`; step-5 plan Traps entry |

## Behaviour after deploy

- SPY is no longer a dashboard, forecast, strategy, advisory, paper-trade, rating or snapshot row unless it is in the cycle universe (held, watchlist, discovered or retained).
- `RS vs SPY`, beta, the HMM and Dual Momentum are unchanged, since SPY stays in `tech_raw`.
- `XSec_Momentum_Rank` shifts for every universe symbol, because SPY no longer sits in the cross-section. This matches `main.py`'s ranking and should shrink shadow-compare drift.
- Forecasting does one fewer symbol per cycle.

## Verification run

- `tests/test_benchmark_spy_leak.py`: 15 passed.
- Revert checks:
  - with the filter monkeypatched to identity, `test_spy_not_in_universe_has_no_dashboard_row` FAILS;
  - with the old `_compute_xsec_momentum(ctx.tech_raw)` call restored, `test_xsec_ranks_cover_universe_only` FAILS.
- The first xsec test was vacuous: mock bars are only 30 days, so the ranks were empty. It was rebuilt on 300-bar trends with a precondition assert.
- Related files: `test_production_steps_universe`, `test_main_orchestrator`, `test_xsec_momentum{,_advisory_parity}`, `test_orchestrator_e2e`, `test_production_steps_fund_dtos_dead_letter`, `test_state_snapshot_parity`: 163 passed.
- Full offline suite (`-m "not network and not slow" -n auto --dist loadgroup`): see the PR body.
- All runs used `/Users/kevinlee/Stockpy-live/.venv` (Python 3.12).
- Known noise: this worktree has no `.venv`, so the Stop hook ran system Python 3.14. There, `test_main_orchestrator.py::TestExecuteBrokerOrders::test_broker_failure_is_caught_and_never_propagates` fails on clean `origin/main` too. That is unrelated to this change and is tracked as a separate task (stop hook → repo venv).

## Not done (operator decisions)

- The leaked `main_pipeline` SPY round trip (−$4.87) in the paper ledger counts toward the freeze tally and was not edited.
- Cutover still also depends on the `ADVISORY_REUSE_PIPELINE_COMPUTE` forecast-reuse diffs and on whether to extend the review.
