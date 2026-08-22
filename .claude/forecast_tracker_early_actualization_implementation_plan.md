# Implementation plan: fix `ForecastTracker.update_actuals` early-actualization bug

(Copied from the approved plan-mode artifact for this task.)

## Context

`forecasting/forecast_tracker.py::update_actuals()` computed its eligibility
cutoff as `as_of - timedelta(days=max(0, horizon_days - tolerance_days))`,
`tolerance_days` defaulting to 5. This makes a forecast eligible for
actualization up to `tolerance_days` **early** — a 30-day forecast scored
against a day-25 price. There is no corresponding lateness bound, and none is
needed: a late-arriving actual still satisfies `forecast_ts <= cutoff`
whenever the next `update_actuals()` call finally runs, so genuine lateness
(weekends, holidays, skipped runs) is already handled by the existing `<=`
comparison with zero extra arithmetic.

Empirically confirmed (simulated run against a real `ForecastTracker`/SQLite
instance): under normal cadence — `forecasting_engine.py:1376` is the only
production call site and never passes `tolerance_days` explicitly — a 30-day
forecast made on day 0 actualized on day 25; a 10-day forecast actualized on
day 5 (50% relative timing error).

Checked history for a considered reason and found none: the introducing
commit (`ce6aa23f`, PR #45) gives no rationale beyond the docstring already in
the code; `test_tolerance_window_boundary` asserts the exact early-firing
behavior mechanically, not as a stated design goal; `settings.py`'s
`FORECAST_SKILL_WINDOW_DAYS` docstring independently derives the same
`horizon - tolerance` arithmetic, confirming the bug was load-bearing
elsewhere but never justified as intentional. Treated as a genuine defect.

## Fix

- `forecasting/forecast_tracker.py::update_actuals()`: removed the
  `tolerance_days` parameter entirely (used nowhere in production). New
  cutoff: `cutoff_dt = as_of - timedelta(days=max(0, horizon_days))`.
  Docstring rewritten to explain the full-horizon requirement and why
  lateness needs no special handling.
- `tests/test_forecast_tracker.py`: dropped `tolerance_days=5` kwargs from
  `_fill_window()` and the two unaffected `TestUpdateActuals` tests; replaced
  `test_tolerance_window_boundary` with
  `test_full_horizon_required_for_actualization` (full-horizon boundary is
  the actualization point) and added
  `test_not_actualized_before_full_horizon_elapses` (regression test proving
  a 30-day forecast made 25 days ago — the exact old-bug scenario — stays
  pending).
- `tests/test_forecast_skill_uplift.py`: dropped the `tolerance_days=5` kwarg.
- `settings.py`: corrected `FORECAST_SKILL_WINDOW_DAYS`'s docstring
  (`now-85d` → `now-90d`) to match the new arithmetic.

## Documentation

- `docs/architecture/signal-engines.md`: appended an "Early-actualization fix
  (2026-08 quant-integrity fix)" bullet to the existing forecast-tracker
  paragraph, and corrected the same stale `now-85d` reference inside the
  pre-existing "Skill-weighted blend" bullet.
- `docs/known_issues/forecast_tracker_early_actualization.md` (new): full
  write-up — root cause, empirical evidence, the "why this wasn't a
  considered tradeoff" history check, the fix, and an explicit
  going-forward-only historical-data decision (already-recorded
  `forecast_errors` rows are not reconciled/purged; the bias is bounded and
  self-heals over the 180-day rolling skill window since
  `FORECAST_SKILL_WEIGHTING_ENABLED` defaults `False`).
- `docs/known_issues/README.md`: added an index row for the new doc.

Not touched: `docs/VALIDATION_STRATEGY_FIX_LOG.md` (scoped to `STRATEGY_REGISTRY`
adapter deployability-gate fixes; this change doesn't touch a registered
strategy adapter) and `CLAUDE.md` (no existing CLAUDE.md section covers this
specific bullet's content — it lives only in `docs/architecture/signal-engines.md`).

## Verification

- `pytest tests/test_forecast_tracker.py tests/test_forecast_skill_uplift.py -q -m "not slow"` → 72 passed, 2 deselected.
- `pytest tests/test_forecasting_engine.py -q -m "not slow"` → 48 passed (confirms the production call site in `forecasting_engine.py` is unaffected).
- Grepped the whole repo for remaining `tolerance_days` usages — none left in
  live code, only docstring/comment references documenting the removed bug.
