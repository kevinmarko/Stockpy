# `forecast_available` silently False on any deployment with skill weighting off

**Status: Fixed (2026-09).**

## Symptom

`forecast_available` — surfaced by `GET /data/sync-report`
(`data.portfolio_sync.build_sync_report`) and rendered by
`webapp/src/components/UniverseCoverage.tsx` — reported `False` for every
symbol in the tracked universe, on every cycle, on any deployment that left
`settings.FORECAST_SKILL_WEIGHTING_ENABLED` at its coded default (`False`).
This happened even though `ForecastingEngine.generate_forecast()` genuinely
ran, and genuinely produced `Forecast_10`/`Forecast_30`/`Forecast_60`/
`Forecast_90` values, for every symbol every cycle. Coverage telemetry was
wrong; forecasting itself was not broken.

## Root cause

`forecast_available` is computed by `ForecastTracker.get_covered_symbols()`,
which queries the `forecast_errors` SQLite table (see
`forecasting/forecast_tracker.py`'s own module docstring for the schema).
That table is populated ONLY when something calls
`ForecastTracker.record_forecasts(...)`.

`ForecastingEngine.generate_forecast()` only calls `record_forecasts` (and
`update_actuals`, `get_skill_weights`) when `self._tracker is not None`. A
`ForecastTracker` was attached to a `ForecastingEngine` at exactly three
production call sites, and all three used the identical pattern:

```python
_tracker = ForecastTracker() if settings.FORECAST_SKILL_WEIGHTING_ENABLED else None
```

- `main_orchestrator.py::EngineContext.build()`
- `engine/advisory.py::_build_forecasting_engine()`
- `pipeline/production_steps.py::ForecastingStep.run()` (the fallback branch
  used when no `EngineContext` was supplied)

Since `FORECAST_SKILL_WEIGHTING_ENABLED` defaults to `False`, `_tracker` was
`None` at all three sites by default — so `record_forecasts` was **never
called at all** on a default-configuration deployment, `forecast_errors`
stayed permanently empty, and `get_covered_symbols()` correctly (and
honestly, per CONSTRAINT #4/#6) reported "nothing recorded" for every
symbol, every cycle, forever.

## Why it was gated this way — and why that was wrong

Reading `settings.py`'s own field description for
`FORECAST_SKILL_WEIGHTING_ENABLED` (before this fix) confirms this was a
genuine design coupling, not a deliberate performance decision:

> "Opt-in activation of inverse-RMSE skill-weighted multi-model forecast
> blending... When True, a persistent ForecastTracker is threaded into
> every ForecastingEngine construction, self-provisioning its
> forecast_errors table."

The flag was designed to gate the **skill-weighted ensemble blending
feature** — reading historical per-model RMSE back via
`ForecastTracker.get_skill_weights()` to weight ARIMA/Monte Carlo/
Holt-Winters/CNN-LSTM by recent accuracy instead of fixed fractions. The
author's chosen implementation mechanism for "disable this feature" was
"never attach a tracker at all" — which had the side effect of ALSO
disabling `record_forecasts`/`update_actuals`, i.e. the recording/telemetry
half, since `generate_forecast()` gates all three tracker methods on the
exact same `self._tracker is not None` check.

**No cost-based justification was found** for withholding recording
specifically. `ForecastTracker.record_forecasts()` is a single
lock-protected `executemany()` INSERT of at most ~7 rows (one per model)
per symbol per horizon, against a WAL-mode SQLite connection reused across
the whole cycle (`ForecastTracker` opens one connection lazily and reuses
it, not a fresh connection per call). `update_actuals()` is a single
indexed `UPDATE` statement. Neither is a plausible per-cycle performance
concern; nothing in the codebase, git history, or original PR (`ce2d0daa`/
`ce6aa23f`, "Tier 2.1 + 2.2") suggested otherwise.

## Fix

Decoupled the two concerns explicitly:

1. **Recording is now unconditional.** All three production call sites
   above now always construct `ForecastTracker()` — a tracker is always
   attached to the `ForecastingEngine`s that back real cycles, regardless
   of `FORECAST_SKILL_WEIGHTING_ENABLED`.
2. **`ForecastingEngine.generate_forecast()`'s `update_actuals`/
   `record_forecasts` calls remain gated ONLY on `self._tracker is not
   None`** — i.e. they now run every cycle in production, since a tracker
   is always attached. This is what backs `get_covered_symbols()` /
   `forecast_available`, and it has no effect on the blended `Forecast_*`
   values (their return values are only ever logged, never read back into
   the blend).
3. **`get_skill_weights()` — the one method that actually feeds the
   blend — is now gated on BOTH `self._tracker is not None` AND
   `settings.FORECAST_SKILL_WEIGHTING_ENABLED`,** checked dynamically
   inside `generate_forecast()` (matching the existing dynamic read of
   `FORECAST_SKILL_WINDOW_DAYS`/`FORECAST_SKILL_MIN_OBS` at the same call
   site). When the flag is off, this step is skipped entirely and blending
   proceeds with an empty `skill_weights` dict — byte-identical to the
   pre-fix "no tracker attached" cold-start blend.

Net effect: with the flag off (the default), a production cycle now writes
real `forecast_errors` rows every cycle (so `forecast_available` is
accurate) while the blended forecast values themselves are completely
unaffected — proven by
`tests/test_forecasting_engine.py::TestGenerateForecast::
test_blend_is_byte_identical_whether_or_not_tracker_is_attached_when_flag_off`.
With the flag on, behavior is unchanged from before this fix (verified by
`test_tracker_lifecycle_is_called_for_each_horizon`, now explicit that it
requires the flag on).

`update_actuals` was deliberately left in the "always run" bucket alongside
`record_forecasts` rather than gated with `get_skill_weights` — completing
a past forecast with its realized price is a recording/ground-truth
concern, not a blending concern, and other read-only consumers
(`pilots/observability.py`'s per-symbol forecast-skill table,
`pilots/forecast_skill.py`'s reliability curve) depend on `actual_price`
being filled in regardless of whether skill-weighted blending is enabled.

## Test isolation fallout (also fixed)

Because all three production call sites now ALWAYS construct a real,
write-mode `ForecastTracker()` with no explicit `db_path` — reachable from
dozens of pre-existing test files that exercise `EngineContext.build()`,
`engine.advisory.evaluate()`, or `run_pipeline()` with no `ForecastTracker`
of their own — this reactivated the same "implicit write to the real,
shared `~/.stockpy_local/quant_platform.db`" risk class already documented
for `ValidationHistoryStore`/`ExecutionAuditStore`/`BrokerFillsStore`/
`TrendsStore` (see CLAUDE.md's respective bullets). Before this fix, the
flag defaulting `False` meant these three sites never actually reached the
real `ForecastTracker` class during a test run at all.

Fixed via:

- `forecasting/forecast_tracker.py` now imports `resolve_database_url` at
  MODULE TOP LEVEL (`from db_config import resolve_database_url`) instead
  of deferred inside `__init__`, exposing a patchable
  `forecasting.forecast_tracker.resolve_database_url` name — scoped so a
  test-isolation fixture can redirect ONLY this class's default DB
  resolution without also touching every other deferred consumer of
  `db_config.resolve_database_url` (`data/historical_store.py`,
  `investyo_mcp_server.py`, `scripts/preflight_check.py`).
- `conftest.py::_isolate_forecast_tracker_db_in_tests` (new, session-wide
  autouse, mirroring the four siblings named above) patches that name to
  `sqlite:///:memory:` for the duration of every test, unless the test
  passes its own explicit `db_path` to `ForecastTracker`.
- `tests/test_forecast_tracker.py::TestDefaultDbPathResolvesThroughDbConfig`
  updated to patch `forecasting.forecast_tracker.resolve_database_url`
  (not `db_config.resolve_database_url`) to match the new import site.

## Tests

- `tests/test_forecasting_engine.py::TestGenerateForecast`:
  `test_tracker_lifecycle_is_called_for_each_horizon` (now explicit that it
  requires `FORECAST_SKILL_WEIGHTING_ENABLED=True`),
  `test_tracker_recording_runs_even_when_skill_weighting_disabled` (new —
  proves `update_actuals`/`record_forecasts` still run per horizon with the
  flag off, and `get_skill_weights` is never called),
  `test_blend_is_byte_identical_whether_or_not_tracker_is_attached_when_flag_off`
  (new — real `ForecastTracker`, proves both the blend is unaffected AND
  `get_covered_symbols()` reflects the newly-recorded coverage).
- `tests/test_engine_context.py::TestEngineContextForecastTrackerAlwaysAttached`
  (new — `EngineContext.build()` always attaches a tracker, flag on or off).
- `tests/test_advisory.py::TestBuildForecastingEngineTrackerAlwaysAttached`
  (new — same contract for `engine/advisory.py::_build_forecasting_engine()`).
- `tests/test_forecast_tracker.py::TestDefaultDbPathResolvesThroughDbConfig`
  (updated for the module-top-level import change).

Pre-existing coverage unaffected by this fix (still passes as-is):
`tests/test_data_api.py::test_sync_report_forecast_available_reflects_real_forecast_tracker`
(a different, already-fixed bug in `get_covered_symbols()` itself —
querying a nonexistent table — seeds the tracker directly and never
exercised the flag-gating bug this document describes).
