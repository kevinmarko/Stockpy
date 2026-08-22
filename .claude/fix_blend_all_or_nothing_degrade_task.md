# Task Tracker: Fix all-or-nothing readiness gates in N-way blends

Branch: `fix-blend-all-or-nothing-degrade`

## Fix 1 — forecast-skill weighting
- [x] Add `compute_skill_weights_from_stats` to `forecasting/forecast_tracker.py`
- [x] Rewire `ForecastTracker.get_skill_weights` to call it
- [x] Rewire `pilots/observability.py::_portfolio_forecast_stats` to call it
- [x] Rewire `pilots/observability.py::_forecast_stats_by_symbol` to call it
- [x] Remove dead `_MIN_RMSE_FALLBACK` constant
- [x] Add `n_by_model` field to both bulk-stats functions and their callers
      (`portfolio_forecast_skill`, `forecast_skill_by_symbol_summary`)
- [x] Fix exact dict-equality test assertions broken by `n_by_model` addition
- [x] Rewrite the two cold-start tests that encoded the OLD (buggy)
      all-or-nothing behavior as correct
- [x] Add `TestComputeSkillWeightsFromStats` direct unit tests for the new
      pure function

## Fix 2 — `risk/etf_transmission.py::build_etf_return_composite`
- [x] Replace all-or-nothing basis selection with per-entry filtering +
      majority-coverage-wins + shares_held tie-break
- [x] Update docstring's "Weighting basis" section
- [x] Verify all pre-existing `TestBuildETFReturnComposite` tests still pass
      unmodified
- [x] Add test: 4-wrapper case, shares-held basis wins outright (not a tie)
- [x] Add test: 3-wrapper tie case, tie-break picks shares_held

## Fix 3 — `signals/registry.py`
- [x] `compute_all`: raise → warning + continue
- [x] `compute_all_vectorized`: raise → warning + continue
- [x] Replace `test_signal_registry_missing_features` (no longer expects a
      raise; asserts module absent from outputs)
- [x] Add test: two modules, only one satisfied — skip is per-module

## Documentation
- [x] New CLAUDE.md "Graduated-degrade convention for N-way blends" bullet
      (auto-mirrored to AGENTS.md via `sync_agent_docs.sh` hook — verified)
- [x] Update `docs/architecture/signal-engines.md` (`forecasting_engine.py`
      and `risk/etf_transmission.py` bullets)
- [x] Update `docs/architecture/validation-and-signals.md`
      (`signals/registry.py` bullet)
- [x] Add new `pilots/observability.py` bullet to
      `docs/architecture/observability-and-apis.md`
- [x] Update `docs/signals/etf_transmission.md`'s "Composite weighting
      basis" section
- [x] New `docs/known_issues/graduated_degrade_all_or_nothing_blends.md`
- [x] Index row in `docs/known_issues/README.md`

## Verification
- [x] `pytest tests/test_forecast_tracker.py tests/test_pilots_observability.py tests/test_etf_transmission.py tests/test_signal_registry.py -v`
      → 193 passed
- [ ] `pytest -m "not network and not slow" -q` (broader offline gate) —
      running in background, results to be recorded in walkthrough

## PR mechanics
- [x] Copy plan/task/walkthrough into `.claude/`
- [ ] Commit changes
- [ ] Push branch
- [ ] Open PR against `main` (do not merge)
