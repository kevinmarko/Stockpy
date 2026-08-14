# Task Tracker — Options Backtest Dedup + Redundant-Recompute Fix

- [x] Read CLAUDE.md Branch Workflow + Agent Workflow sections
- [x] `git fetch origin && git rebase origin/main` (confirmed already up to date)
- [x] Read `validation/options_selling_backtest.py` in full
- [x] Read `scripts/refresh_validations.py`'s options-adapter registry section + `run_validations`
      dispatch loop (confirmed all 6 adapters slice the same `closes_df["SPY"]` column, so a
      content-based cache key will hit across all 6 within one `run_validations()` sweep)
- [x] Read existing test coverage (`tests/test_options_selling_backtest_stress.py`,
      `tests/test_refresh_validations.py::TestBuildOptionsStrategiesAdapters`) — confirmed no
      existing byte-identical-behavior or call-count-instrumentation test covers either finding
- [x] Derive the algebraic unification proof for Finding A's shared MTM formula (credit spreads,
      debit spreads, covered call all reduce to one expression)
- [x] Write `implementation_plan.md` / `task.md` / `walkthrough.md`
- [x] Capture pre-refactor golden output (6 strategies x synthetic SPY, seed=22/scale=0.016/n=450)
      for the byte-identical regression test, BEFORE touching production code —
      `tests/fixtures/options_selling_backtest_golden.json`
- [x] `git checkout -b refactor-options-backtest-shared-mtm-cache`
- [x] Implement Finding A: `_OptionLeg`, `_simulate_leg_mtm_pnl`, refactor all 6 branches in
      `simulate_options_strategy_returns` to delegate to it
- [x] Implement Finding B: `_CycleEntry`, `_CyclePlan`, `_compute_cycle_plan`,
      `_closes_fingerprint`, `_CYCLE_PLAN_CACHE`, `_get_cycle_plan`, `_reset_cycle_plan_cache`;
      refactor `simulate_options_strategy_returns` to use the cached plan
- [x] Add `TestSharedMtmHelperByteIdentical` regression test (golden fixture-backed)
- [x] Add `TestSharedMtmHelperDirectFormulaEquivalence` (Iron Condor + Call Debit Spread, the two
      strategies the golden fixture's window happens not to activate)
- [x] Add `TestCyclePlanCacheAvoidsRedundantRecompute` instrumentation tests (3 tests: call-count,
      cache-key content-distinguishing, closes=None-vs-explicit correctness)
- [x] Add `docs/architecture/validation-and-signals.md` bullet
- [x] Add `tests/fixtures/README.md` exception note for the new golden fixture
- [x] Run offline+network suite: `tests/test_options_selling_backtest_stress.py -v` → 55 passed
- [x] Run `tests/test_refresh_validations.py -v` → 95 passed, 1 pre-existing unrelated warning
- [x] Run `tests/test_stress_gate.py tests/test_technical_options_engine.py
      tests/test_validation_vrp_premium_selling_registry.py tests/test_vrp_premium_selling.py -v`
      → 102 passed
- [x] Run `ruff check . --select=F821,F822,F823,E9` → All checks passed
- [x] Copy `implementation_plan.md`/`task.md`/`walkthrough.md` into `.claude/` on the branch
- [ ] `git add` (incl. `-f` for the gitignored fixture JSON), commit, `gh pr create` against `main`
      (do NOT merge)
- [ ] Report branch name, PR URL, and full verification output back to caller
