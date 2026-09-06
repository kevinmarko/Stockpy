# Walkthrough: `forecast_available` telemetry decoupled from `FORECAST_SKILL_WEIGHTING_ENABLED`

## Root cause

`forecast_available` is computed by `ForecastTracker.get_covered_symbols()` reading the
`forecast_errors` table, which is populated only by `ForecastTracker.record_forecasts(...)`.
`ForecastingEngine.generate_forecast()` only ever calls `record_forecasts`/`update_actuals`/
`get_skill_weights` when `self._tracker is not None`. All three production call sites that
construct a `ForecastTracker` used the identical pattern:

```python
_tracker = ForecastTracker() if settings.FORECAST_SKILL_WEIGHTING_ENABLED else None
```

`FORECAST_SKILL_WEIGHTING_ENABLED` defaults `False`, so `_tracker` was `None` at all three
sites by default, meaning `record_forecasts` was never called at all on a default
configuration — `forecast_errors` stayed permanently empty and `forecast_available` correctly
(per CONSTRAINT #4/#6) reported "nothing recorded" forever, even though forecasting genuinely
ran every cycle.

`settings.py`'s own (pre-fix) field description confirmed the flag was designed to gate the
**skill-weighted ensemble blending feature** specifically ("a persistent ForecastTracker is
threaded into every ForecastingEngine construction, self-provisioning its forecast_errors
table"). The implementation mechanism chosen ("don't attach a tracker at all") swept the
recording/telemetry side into the same gate as an unintended side effect — there is no
performance justification anywhere in the code, comments, or the introducing PR
(`ce2d0daa`/`ce6aa23f`, "Tier 2.1 + 2.2") for withholding recording specifically.
`ForecastTracker.record_forecasts()` is a single lock-protected `executemany()` INSERT of at
most ~7 rows against a reused WAL-mode connection; `update_actuals()` is a single indexed
`UPDATE`. Neither is a plausible per-cycle cost concern.

## What changed

1. **`forecasting_engine.py::generate_forecast()`** — the `get_skill_weights()` call (the
   ONLY tracker touchpoint that actually feeds the blend) is now additionally gated on
   `settings.FORECAST_SKILL_WEIGHTING_ENABLED`, checked dynamically right next to the
   existing dynamic read of `FORECAST_SKILL_WINDOW_DAYS`/`FORECAST_SKILL_MIN_OBS`.
   `update_actuals`/`record_forecasts` remain gated only on `self._tracker is not None`
   (unchanged) — they are recording/telemetry, not blending, and their return values are
   never read back into the blend.
2. **`main_orchestrator.py::EngineContext.build()`, `engine/advisory.py::_build_forecasting_engine()`,
   `pipeline/production_steps.py::ForecastingStep.run()`'s fallback branch** — all three now
   always construct `ForecastTracker()`, dropping the flag-gated ternary. A tracker is now
   always attached in production, regardless of `FORECAST_SKILL_WEIGHTING_ENABLED`.
3. **`forecasting/forecast_tracker.py`** — moved its `from db_config import resolve_database_url`
   from deferred-inside-`__init__` to module top level, exposing a patchable
   `forecasting.forecast_tracker.resolve_database_url` name.
4. **`conftest.py`** — new autouse fixture `_isolate_forecast_tracker_db_in_tests`, mirroring
   `_isolate_validation_runs_db_in_tests`/`_isolate_execution_audit_db_in_tests`/
   `_isolate_broker_fills_db_in_tests`/`_isolate_trends_store_db_in_tests`, redirecting
   `ForecastTracker`'s default DB resolution to a per-test temp FILE (not `:memory:` — see
   the fixture's own docstring for why a bare in-memory sqlite db is actually unsafe for this
   specific class, which uses two independent raw `sqlite3.connect()` calls rather than one
   pooled SQLAlchemy engine). Required because the three call sites above now always
   construct a real, write-mode `ForecastTracker()` reachable from dozens of pre-existing
   tests that previously never reached the real class at all (flag defaulted off in every
   test).
5. **`settings.py`** — `FORECAST_SKILL_WEIGHTING_ENABLED`'s field description rewritten to
   state the corrected, narrower scope.
6. **Tests updated**: `tests/test_forecast_tracker.py::TestDefaultDbPathResolvesThroughDbConfig`
   (patches the new import location); `tests/test_forecasting_engine.py::
   test_tracker_lifecycle_is_called_for_each_horizon` (now explicitly requires the flag ON,
   since it manually attaches a tracker and asserts the full lifecycle).
7. **New tests**:
   - `tests/test_forecasting_engine.py::TestGenerateForecast::
     test_tracker_recording_runs_even_when_skill_weighting_disabled` — with the flag off,
     `update_actuals`/`record_forecasts` still fire per horizon; `get_skill_weights` never
     does.
   - `tests/test_forecasting_engine.py::TestGenerateForecast::
     test_blend_is_byte_identical_whether_or_not_tracker_is_attached_when_flag_off` — a REAL
     `ForecastTracker` attached with the flag off produces byte-identical `Forecast_*` values
     to no tracker at all, AND `get_covered_symbols()` reflects the newly-recorded coverage.
   - `tests/test_engine_context.py::TestEngineContextForecastTrackerAlwaysAttached` — 
     `EngineContext.build()` always attaches a tracker, flag on or off.
   - `tests/test_advisory.py::TestBuildForecastingEngineTrackerAlwaysAttached` — same
     contract for `_build_forecasting_engine()`.
8. **Docs**: `docs/known_issues/forecast_available_coupled_to_skill_weighting_flag.md` (new,
   full write-up) + index row in `docs/known_issues/README.md`; a new CLAUDE.md/AGENTS.md
   bullet (both files manually kept in sync — the `sync_agent_docs.sh` PostToolUse hook did
   not fire in this delegated worktree/background-agent context).

## Byte-identical-when-ON verification

When `FORECAST_SKILL_WEIGHTING_ENABLED=True`, the code path taken inside
`generate_forecast()` is unchanged: `self._tracker is not None and settings.
FORECAST_SKILL_WEIGHTING_ENABLED` reduces to the same truth value as the old
`self._tracker is not None` check, since a tracker was only ever non-`None` when the flag
was already `True` at the (old) construction sites. `tests/test_forecasting_engine.py::
test_tracker_lifecycle_is_called_for_each_horizon` (updated to explicitly set the flag True,
since a manually-attached mock tracker no longer implies the flag) still asserts
`update_actuals`/`get_skill_weights`/`record_forecasts` are each called once per horizon (4
horizons) — identical assertions to before this fix.

## Real test output

```
$ NUMBA_CACHE_DIR=<isolated> pytest tests/test_forecast_tracker.py tests/test_forecasting_engine.py \
    tests/test_engine_context.py tests/test_advisory.py tests/test_main_orchestrator.py \
    tests/test_data_api.py -q
307 passed, 133 warnings in 64.07s

$ NUMBA_CACHE_DIR=<isolated> pytest tests/test_state_snapshot_parity.py \
    tests/test_no_hardcoded_db_path_defaults.py tests/test_pilots_forecast_skill.py \
    tests/test_gui_forecast_skill_panel.py tests/test_forecast_skill_uplift.py -q
67 passed, 30 warnings in 88.95s

$ ruff check <changed files> --select=F821,F822,F823,E9
All checks passed!
```

`NUMBA_CACHE_DIR` was pointed at a private scratch directory only to route around a
confirmed, pre-existing, unrelated flakiness on this shared machine (a numba disk-cache
locator race when many concurrent worktrees/agents import `pandas_ta`/`pandas_ta_classic`
simultaneously) — reproduced identically on a pristine, unmodified `main` checkout with no
files from this change present, confirming it is environmental, not a regression introduced
here.
