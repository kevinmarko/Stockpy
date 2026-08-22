# Known issue (2026-08-22): `ForecastTracker.update_actuals` scored every forecast up to 5 days early, not late

**Status: fixed.** Landed on branch `fix-forecast-tracker-early-actualization`.
**Decision: going-forward-only fix — already-recorded `forecast_errors` rows
are not reconciled or purged.** See "Historical data decision" below.

## What happened

`forecasting/forecast_tracker.py::ForecastTracker.update_actuals()` decided
whether a past forecast was due to be scored against a real observed price
using:

```python
cutoff_dt = as_of - timedelta(days=max(0, horizon_days - tolerance_days))
```

with `tolerance_days` defaulting to `5`. The method's own docstring described
this as tolerance for lateness: *"The tolerance window (+5 days) absorbs
weekends, holidays, and the fact that runs may be skipped."*

That description does not match what the arithmetic actually does. Subtracting
`tolerance_days` from the cutoff makes a forecast eligible **up to 5 days
before** its nominal horizon has elapsed — early, not late. A 30-day forecast
became eligible for actualization once only 25 days had passed. Genuine
lateness never needed this subtraction in the first place: the query is
`WHERE forecast_ts <= cutoff_dt`, so a forecast that becomes due while a cycle
is skipped (a weekend, a holiday, a missed run) simply stays pending and gets
picked up correctly whenever `update_actuals()` next runs — no tolerance
arithmetic required for that case.

## Empirical confirmation

Simulated a daily `update_actuals()` call against a real `ForecastTracker`/
SQLite instance:

- A 30-day-horizon forecast made on day 0 was actualized on **day 25** — 5
  days (17%) early — using the day-25 price as the "actual" for a nominally
  30-day-ahead forecast.
- This is proportionally worst for the shortest tracked horizon: a 10-day
  forecast (`Forecast_10`) was actualized on **day 5** — a 50% relative
  timing error — versus ~5.5% for the 90-day horizon.

`forecasting_engine.py:1376` — `self._tracker.update_actuals(symbol, h,
current_price, now_utc)` — is the only production call site, and it never
passed `tolerance_days` explicitly, so the buggy default fired on every real
pipeline cycle (as often as every interval during 4am-8pm ET weekdays, per
`settings.ORCHESTRATOR_EXTENDED_HOURS_ONLY`), not as an occasional edge case.

## Why this wasn't a considered tradeoff

Checked before concluding this was a defect rather than deliberate design:

- `git log -p` on the introducing commit (`ce6aa23f`, "Tier 2.1 + 2.2 —
  regime-conditional signal weights and forecast skill ensemble", PR #45) —
  the commit message gives no rationale beyond the mismatched docstring
  already present in the diff.
- `tests/test_forecast_tracker.py::test_tolerance_window_boundary` asserted
  the exact early-firing behavior as a mechanical boundary check (forecast at
  `horizon - tolerance` days actualizes) — evidence the behavior was tested,
  not evidence it was *intended* to fire early; the test's own docstring
  ("A forecast at horizon-tolerance_days should be actualized") just restates
  the arithmetic without explaining why early is correct.
- `settings.py`'s `FORECAST_SKILL_WINDOW_DAYS` docstring independently derived
  "a 'completed' row for horizon 90 needs forecast_ts ≤ now-85d" from this
  exact `horizon - tolerance` arithmetic — confirming the early-firing was
  load-bearing elsewhere in the codebase's own reasoning, but still never
  justified as intentional anywhere in that reasoning either.

No comment, commit message, or PR description anywhere in the module's history
explains why "early" (rather than "late, with a bound that's a no-op given the
existing `<=` comparison") was the intended behavior. Treated as a genuine
defect.

## Impact

This is exactly the mechanism feeding `ForecastTracker.get_skill_weights()`'s
RMSE computation — every completed row was scored against a real price
observed at a shorter horizon than the forecast claimed to be for. This
systematically biases the measured RMSE (and therefore the inverse-RMSE skill
weights `FORECAST_SKILL_WEIGHTING_ENABLED` computes) in a direction and
magnitude that depends on how volatility/predictability changes over the last
`tolerance_days` of each nominal horizon — making shorter-horizon models look
more or less accurate than they really are for their claimed horizon,
unpredictably rather than in one consistent direction.

`FORECAST_SKILL_WEIGHTING_ENABLED` defaults to `False` and this fix does not
change that default, so this bias was never automatically acted on by a
default-configuration deployment — it only affects operators who had
explicitly opted in.

## The fix

`update_actuals()`'s `tolerance_days` parameter is removed entirely (it was
used nowhere in production — only in tests, all updated alongside this fix).
The cutoff is now:

```python
cutoff_dt = as_of - timedelta(days=max(0, horizon_days))
```

i.e. eligibility requires the **full** nominal horizon to have elapsed. No
replacement lateness parameter was added, because none is needed: the
`WHERE forecast_ts <= cutoff_dt AND actual_price IS NULL` query already picks
up an arbitrarily late row the next time `update_actuals()` runs.

`settings.py`'s `FORECAST_SKILL_WINDOW_DAYS` docstring was corrected
(`now-85d` → `now-90d`) to match the new arithmetic; the field's default
(`180`) and the `WINDOW=60`-insufficient reasoning both still hold — if
anything the fixed arithmetic makes 180 an even more comfortable margin over
90 than before.

## Historical data decision

Every already-actualized row in the live `forecast_errors` table was scored
under the old, early-firing window — up to `tolerance_days` (5) days before
its nominal horizon actually elapsed. This fix is **going-forward only**:
existing rows are **not** flagged, reconciled, or purged. Reasoning:

- There is no way to recover what the "correct" actual price would have been
  at the true horizon for a row whose actualization window has already
  passed — the DB stores only the price that was actually observed at
  actualization time, not the full subsequent price path.
- The bias is bounded, not catastrophic: worst case ~17% of the shortest
  tracked horizon (10 days), tapering to ~5.5% at the 90-day horizon.
- `FORECAST_SKILL_WEIGHTING_ENABLED` defaults to `False`, so no default
  deployment is currently acting on these weights.
- An operator who has opted into skill weighting should expect the weights to
  read as noisy/mildly biased until enough new, correctly-scored rows
  accumulate under the `FORECAST_SKILL_WINDOW_DAYS` (180-day) rolling window
  to dilute the old, early-scored rows out of the window — i.e. correctness
  self-heals over the next ~180 days of normal operation without any manual
  intervention.

## Related

- `docs/architecture/signal-engines.md`'s `forecasting_engine.py` bullet
  ("Early-actualization fix (2026-08 quant-integrity fix)").
- `docs/known_issues/graduated_degrade_all_or_nothing_blends.md` — a separate,
  earlier defect in the same `get_skill_weights()`/skill-weighting pipeline;
  unrelated root cause (an all-or-nothing readiness gate, not a cutoff-timing
  bug), but worth reading together as two independent correctness issues
  found in the same measurement pipeline within a few weeks of each other.
- Tests: `tests/test_forecast_tracker.py::TestUpdateActuals::test_full_horizon_required_for_actualization`,
  `::test_not_actualized_before_full_horizon_elapses`.
