# Implementation Plan: decouple `forecast_available` telemetry from `FORECAST_SKILL_WEIGHTING_ENABLED`

## Problem

`forecast_available` (`GET /data/sync-report`, `data.portfolio_sync.build_sync_report`,
rendered by `webapp/src/components/UniverseCoverage.tsx`) is computed by
`ForecastTracker.get_covered_symbols()`, which reads the `forecast_errors` table. That
table is populated ONLY when `ForecastTracker.record_forecasts(...)` runs. Both
`main_orchestrator.py::EngineContext.build()` and `engine/advisory.py::_build_forecasting_engine()`
(plus `pipeline/production_steps.py::ForecastingStep.run()`'s fallback branch) only
construct/attach a `ForecastTracker` — and therefore only ever call `record_forecasts` —
when `settings.FORECAST_SKILL_WEIGHTING_ENABLED=True`. That flag defaults `False`. So on
any deployment with the flag off, `forecast_available` reports `False` for the entire
universe even though forecasting genuinely runs for every symbol every cycle.

## Root cause (confirmed against code + git history)

`settings.py`'s own `FORECAST_SKILL_WEIGHTING_ENABLED` field description states the flag
is meant to gate the **skill-weighted ensemble blending feature** (reading historical
per-model RMSE back to weight ARIMA/Monte Carlo/Holt-Winters/CNN-LSTM). The chosen
implementation mechanism ("never attach a tracker at all when off") incidentally also
disabled `record_forecasts`/`update_actuals`, since `ForecastingEngine.generate_forecast()`
gates all three tracker methods on the same `self._tracker is not None` check. No
cost-based justification exists for withholding recording specifically — `record_forecasts`
is a single lock-protected `executemany()` INSERT of at most ~7 rows, `update_actuals` a
single indexed `UPDATE`, both against a WAL-mode connection reused for the whole cycle.

## Fix

1. **`forecasting_engine.py::generate_forecast()`**: gate ONLY the `get_skill_weights()`
   call (and therefore the skill-weighted blend read-back) on
   `settings.FORECAST_SKILL_WEIGHTING_ENABLED`, checked dynamically at that call site
   (matching the existing dynamic read of `FORECAST_SKILL_WINDOW_DAYS`/`MIN_OBS` right
   next to it). `update_actuals`/`record_forecasts` remain gated ONLY on
   `self._tracker is not None`.
2. **Three production call sites** (`main_orchestrator.py::EngineContext.build`,
   `engine/advisory.py::_build_forecasting_engine`,
   `pipeline/production_steps.py::ForecastingStep.run`'s fallback branch): always
   construct `ForecastTracker()`, dropping the `if settings.FORECAST_SKILL_WEIGHTING_ENABLED
   else None` ternary.
3. **Test isolation**: since these three sites now always construct a real, write-mode
   `ForecastTracker()` with no explicit `db_path`, add a new `conftest.py` autouse fixture
   (`_isolate_forecast_tracker_db_in_tests`) redirecting the default DB resolution to a
   per-test temp file — mirrors `_isolate_validation_runs_db_in_tests` and its three
   siblings. Requires moving `forecasting/forecast_tracker.py`'s `db_config.resolve_database_url`
   import to module top level so it's independently patchable (scoped blast radius vs.
   patching `db_config.resolve_database_url` globally).
4. **Update existing tests** whose assertions encoded the old (undifferentiated) tracker
   lifecycle: `tests/test_forecasting_engine.py::TestGenerateForecast::
   test_tracker_lifecycle_is_called_for_each_horizon` now explicitly sets the flag True;
   `tests/test_forecast_tracker.py::TestDefaultDbPathResolvesThroughDbConfig` patches the
   new module-top-level import location.
5. **New regression tests** proving: (a) recording runs with the flag off and blending is
   byte-identical to the no-tracker case; (b) the flag still gates `get_skill_weights`
   exclusively; (c) the three production call sites always attach a tracker regardless of
   the flag.

## Documentation updates (part of this plan, not a follow-up)

- New `docs/known_issues/forecast_available_coupled_to_skill_weighting_flag.md` — full
  root-cause writeup, fix description, and test-isolation fallout.
- `docs/known_issues/README.md` — new index row.
- `CLAUDE.md` / `AGENTS.md` — new bullet describing the decoupling (both files edited
  directly since the `sync_agent_docs.sh` hook did not fire in this delegated/background
  worktree context; kept byte-identical between the two).
- `settings.py`'s `FORECAST_SKILL_WEIGHTING_ENABLED` field description updated to state
  the new, narrower scope of what the flag actually gates.

## Verification

- `tests/test_forecast_tracker.py`, `tests/test_forecasting_engine.py`,
  `tests/test_engine_context.py`, `tests/test_advisory.py`, `tests/test_main_orchestrator.py`,
  `tests/test_data_api.py`, `tests/test_state_snapshot_parity.py`,
  `tests/test_no_hardcoded_db_path_defaults.py`, `tests/test_pilots_forecast_skill.py`,
  `tests/test_gui_forecast_skill_panel.py`, `tests/test_forecast_skill_uplift.py` — all
  green via `/Users/kevinlee/Stockpy-live/.venv/bin/pytest` (using an isolated
  `NUMBA_CACHE_DIR` to route around unrelated, pre-existing shared-machine numba-cache
  flakiness confirmed to also occur on a pristine `main` checkout).
- `ruff check --select=F821,F822,F823,E9` clean on every changed file.
