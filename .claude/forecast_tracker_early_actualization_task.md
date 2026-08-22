# Task tracker: fix `ForecastTracker.update_actuals` early-actualization bug

- [x] Read `tests/test_forecast_tracker.py::test_tolerance_window_boundary` and
      the introducing commit's git history/PR description to confirm there was
      no deliberate reason for the early-firing tolerance subtraction.
- [x] Confirmed genuine defect: no rationale in commit message, test docstring
      only mechanically asserts the buggy behavior, `settings.py`'s
      `FORECAST_SKILL_WINDOW_DAYS` docstring independently baked in the same
      arithmetic without justification.
- [x] Fixed `forecasting/forecast_tracker.py::update_actuals()` — removed
      `tolerance_days`, cutoff now requires the full `horizon_days` to elapse.
- [x] Updated `tests/test_forecast_tracker.py`: replaced
      `test_tolerance_window_boundary` with
      `test_full_horizon_required_for_actualization`; added regression test
      `test_not_actualized_before_full_horizon_elapses`; dropped
      `tolerance_days=5` from `_fill_window()` and two other tests.
- [x] Updated `tests/test_forecast_skill_uplift.py`: dropped `tolerance_days=5`.
- [x] Corrected `settings.py`'s `FORECAST_SKILL_WINDOW_DAYS` docstring
      (`now-85d` → `now-90d`).
- [x] Decided and documented: going-forward-only fix; historical
      `forecast_errors` rows are not reconciled — see
      `docs/known_issues/forecast_tracker_early_actualization.md`'s
      "Historical data decision" section.
- [x] Updated `docs/architecture/signal-engines.md` (the relevant
      `docs/architecture/*.md` entry describing this mechanism).
- [x] Added `docs/known_issues/forecast_tracker_early_actualization.md` +
      index row in `docs/known_issues/README.md`.
- [x] Ran targeted tests:
      `pytest tests/test_forecast_tracker.py tests/test_forecast_skill_uplift.py -q -m "not slow"`
      (72 passed, 2 deselected) and
      `pytest tests/test_forecasting_engine.py -q -m "not slow"` (48 passed).
- [x] Branch `fix-forecast-tracker-early-actualization` created off the synced
      worktree branch; PR artifacts copied to `.claude/` with task-scoped
      names.
- [x] Open PR: https://github.com/kevinmarko/Stockpy/pull/859
