# Walkthrough: fix `ForecastTracker.update_actuals` early-actualization bug

## The bug

`forecasting/forecast_tracker.py::ForecastTracker.update_actuals()` decided
whether a past forecast could be scored against a real observed price using:

```python
cutoff_dt = as_of - timedelta(days=max(0, horizon_days - tolerance_days))
```

`tolerance_days` defaulted to `5`. Its docstring claimed this absorbed
lateness ("weekends, holidays, and skipped runs"), but subtracting
`tolerance_days` from the cutoff actually makes a forecast eligible **early**
— up to 5 days before its nominal horizon elapses. A 30-day forecast was
scored against the price observed on day 25, not day 30.

Lateness never needed this arithmetic: the query is `WHERE forecast_ts <=
cutoff_dt AND actual_price IS NULL`, so a forecast that becomes due while a
cycle is skipped simply stays pending and is correctly picked up the next
time `update_actuals()` runs — no tolerance term required.

## Why this was confirmed as a real defect, not a design choice

Per the task's own instructions, I checked before changing behavior:

1. `git log -p` on the introducing commit (`ce6aa23f`, "Tier 2.1 + 2.2", PR
   #45) — no rationale beyond the docstring already present in that same
   diff.
2. `tests/test_forecast_tracker.py::test_tolerance_window_boundary` — asserted
   the exact early-firing boundary, but only mechanically (its own docstring,
   "A forecast at horizon-tolerance_days should be actualized," just restates
   the arithmetic).
3. `settings.py`'s `FORECAST_SKILL_WINDOW_DAYS` field docstring independently
   derived "forecast_ts ≤ now-85d" for horizon 90 from the same `horizon -
   tolerance` arithmetic — proof the bug was load-bearing in the codebase's
   own reasoning about window sizing, but that reasoning never argued for
   early-firing as correct either; it just inherited the arithmetic.
4. `forecasting_engine.py:1376` — the sole production call site — never
   passes `tolerance_days`, so the buggy default fires on every real pipeline
   cycle, not an edge case.

No comment or commit anywhere explains why early (rather than the existing
`<=` comparison, which already handles arbitrary lateness for free) was
intended. Confirmed as a genuine defect.

## The fix

- Removed the `tolerance_days` parameter from `update_actuals()` entirely —
  it was unused in production and, once the subtraction is gone, has no
  remaining purpose.
- New cutoff: `cutoff_dt = as_of - timedelta(days=max(0, horizon_days))` —
  eligibility now requires the full nominal horizon to have elapsed.
- Rewrote the method's docstring to state the corrected contract and explain
  why lateness needs no separate handling.

## Tests

- Replaced `test_tolerance_window_boundary` (asserted the old, buggy boundary)
  with `test_full_horizon_required_for_actualization` (asserts the corrected
  boundary: exactly `horizon_days` days elapsed → actualized).
- Added `test_not_actualized_before_full_horizon_elapses`: a 30-day forecast
  made 25 days ago — the exact scenario the empirical repro found actualizing
  incorrectly under the old code — now correctly stays pending
  (`pending_count == 1`, `update_actuals` returns `0`).
- Removed now-invalid `tolerance_days=5` kwargs from
  `tests/test_forecast_tracker.py` (`_fill_window()` helper + two other
  `TestUpdateActuals` tests whose forecast ages were unambiguously past/not-
  past the full horizon either way) and `tests/test_forecast_skill_uplift.py`.

All targeted suites pass:
`pytest tests/test_forecast_tracker.py tests/test_forecast_skill_uplift.py -q -m "not slow"`
→ 72 passed, 2 deselected (2 slow-marked uplift tests deselected, matching
existing convention). `pytest tests/test_forecasting_engine.py -q -m "not slow"`
→ 48 passed, confirming the real production call site
(`ForecastingEngine.generate_forecast`) is unaffected.

## Docs

- `settings.py`: corrected the `FORECAST_SKILL_WINDOW_DAYS` docstring's stale
  `now-85d` reference to `now-90d` to match the fixed arithmetic.
- `docs/architecture/signal-engines.md`: appended a dated bullet to the
  existing forecast-tracker paragraph describing the bug, evidence, and fix
  (same style as the paragraph's existing "Graduated-degrade skill weighting"
  bullet); also fixed the same stale `now-85d` reference inside that
  paragraph's pre-existing "Skill-weighted blend" bullet.
- `docs/known_issues/forecast_tracker_early_actualization.md` (new): full
  incident write-up in this repo's established known-issues format, including
  an explicit, reasoned decision on the historical-data question (task item
  3): **going-forward-only fix, no reconciliation of already-recorded
  `forecast_errors` rows.** Reasoning: there's no way to recover the "correct"
  actual price for a row whose window already passed; the bias is bounded
  (worst case ~17% of the shortest tracked horizon); `FORECAST_SKILL_WEIGHTING_ENABLED`
  defaults `False` so no default deployment acted on the biased weights; and
  the bias self-heals over the 180-day rolling `FORECAST_SKILL_WINDOW_DAYS`
  window as new, correctly-scored rows dilute the old ones out.
- `docs/known_issues/README.md`: added an index row for the new doc.
- Did not touch `docs/VALIDATION_STRATEGY_FIX_LOG.md` (scoped specifically to
  `STRATEGY_REGISTRY` adapter deployability-gate fixes; not applicable here)
  or `CLAUDE.md` (this mechanism isn't separately documented there — only in
  `docs/architecture/signal-engines.md`).

## Branch / PR workflow

This touches `forecasting/` (an engine feeding the deployability-relevant
skill-weighting pipeline), so it went through CLAUDE.md's "Everything else"
tier: `EnterPlanMode` → user-approved plan → feature branch
`fix-forecast-tracker-early-actualization` → implementation → PR artifacts
copied into `.claude/` under task-scoped names
(`forecast_tracker_early_actualization_{implementation_plan,task,walkthrough}.md`).
