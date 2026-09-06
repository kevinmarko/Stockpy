# Task: fix forecast_available coverage telemetry incorrectly gated on FORECAST_SKILL_WEIGHTING_ENABLED

- [x] Read `forecasting/forecast_tracker.py::ForecastTracker` in full and both call sites
      constructing a `ForecastTracker` gated on `FORECAST_SKILL_WEIGHTING_ENABLED`.
- [x] Determine root cause from code + git history (settings.py field description +
      `ce2d0daa`/`ce6aa23f` "Tier 2.1 + 2.2" introducing commit).
- [x] Decouple recording (`record_forecasts`/`update_actuals`, unconditional on tracker
      presence) from blending (`get_skill_weights`, gated on the flag) in
      `forecasting_engine.py::generate_forecast()`.
- [x] Always attach a `ForecastTracker` at the three production call sites
      (`main_orchestrator.py`, `engine/advisory.py`, `pipeline/production_steps.py`).
- [x] Add `conftest.py::_isolate_forecast_tracker_db_in_tests` + move
      `forecast_tracker.py`'s `resolve_database_url` import to module top level.
- [x] Update `tests/test_forecast_tracker.py::TestDefaultDbPathResolvesThroughDbConfig`
      for the new import location.
- [x] Update `tests/test_forecasting_engine.py::test_tracker_lifecycle_is_called_for_each_horizon`
      to require the flag explicitly.
- [x] Add regression tests: flag-off recording + byte-identical blend
      (`test_tracker_recording_runs_even_when_skill_weighting_disabled`,
      `test_blend_is_byte_identical_whether_or_not_tracker_is_attached_when_flag_off`),
      always-attached tracker at the two singleton-getter call sites
      (`tests/test_engine_context.py`, `tests/test_advisory.py`).
- [x] Update `settings.py`'s `FORECAST_SKILL_WEIGHTING_ENABLED` field description.
- [x] Write `docs/known_issues/forecast_available_coupled_to_skill_weighting_flag.md` +
      index row in `docs/known_issues/README.md`.
- [x] Add CLAUDE.md/AGENTS.md bullet (both, hook did not fire in this context).
- [x] Verify byte-identical behavior when the flag is ON (existing + updated tests).
- [x] Run full targeted test suite + ruff genuine-bug lint.
- [x] Commit on `fix-forecast-available-skill-weighting-coupling` branch, push, open PR.
