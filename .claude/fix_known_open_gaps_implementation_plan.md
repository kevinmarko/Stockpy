# Goal Description
Fix remaining Master Session open gaps (Section 7): the active trading universe disconnect
(Gap 3) and the missing deployability-gate enforcement for options pilot execution (Gap 2).

**Status note (2026-08-29): this plan was rewritten after merge to describe what was actually
built.** The original version of this document (and the task/walkthrough artifacts alongside it)
described a diagnosis and a fix list that were each wrong in one place — both confirmed false by
independent post-commit audit, both fixed before this branch was considered done. See
`.claude/fix_known_open_gaps_walkthrough.md` for the full post-mortem of what the original
self-report got wrong and how it was caught.

## Actual root causes (corrected diagnosis)

### Gap 3 — active trading universe disconnect
The original diagnosis assumed `data/portfolio_sync.py::build_sync_report()` did not accept a
`forecast_symbols` parameter and needed one added. **That diagnosis was wrong.**
`build_sync_report()` already had a working `forecast_symbols` parameter from an earlier
(June 2026) commit — `data/portfolio_sync.py` needed no changes at all and none were made to it.

The real bug was in the *new* code this PR added: `ForecastTracker.get_covered_symbols()`
(`forecasting/forecast_tracker.py`), which:
- queried a table named `forecasts`, which does not exist — the real table this class writes
  forecasts into is `forecast_errors` (see the `CREATE TABLE IF NOT EXISTS forecast_errors` DDL
  in the same file);
- referenced `self.readonly` in its `finally` block, which does not exist — the real attribute is
  `self._readonly`;
- closed the tracker's shared/cached connection (`self._conn`) without resetting the reference,
  breaking every subsequent call on the same `ForecastTracker` instance
  (`sqlite3.ProgrammingError: Cannot operate on a closed database`);
- had no `try/except` at all, unlike every other read method on this class.

Because of the second and first points, every real call to `get_covered_symbols()` raised —
`AttributeError` first (accessing `self.readonly`), or would have raised `sqlite3.OperationalError:
no such table: forecasts` if that line had been reached first. That exception was then silently
swallowed by `api/data_api.py::get_sync_report()`'s own bare `except Exception: forecast_symbols =
[]` (added in the same original commit specifically to make this call safe), so `forecast_symbols`
was unconditionally `[]` and `forecast_available` was unconditionally `False` for every symbol,
regardless of real forecast coverage — the exact bug this PR set out to fix, just reached by a
different, hidden mechanism than the one originally diagnosed.

**Fix**: rewrote `get_covered_symbols()` to query `forecast_errors`, use the class's real
`self._lock`/`self._get_conn()` pattern (matching every sibling read method in the file) instead
of closing the connection, wrap the query in a `try/except` that logs a warning and degrades to
`[]` (CONSTRAINT #6, matching every other method on this class), and added a `window_days=7`
recency filter (default) so a symbol forecast once, long ago, and never again does not read as
"covered" forever — `window_days=None` restores an all-time distinct scan for callers that want it.

### Gap 2 — options-desk deployability gate enforcement
The original self-report claimed enforcing checks were added to `EarningsCrushExecuteRequest`,
`DispersionExecuteRequest`, and `ZeroDteExecuteRequest` alike. **This was only two-thirds true.**
The original commit did add an `override_deployability_gate: bool = False` field to all three
Pydantic request models, and it did add the actual enforcing block —
`if gate["gate_status"] == "UNGATEABLE_DATA_GAP" and not body.override_deployability_gate: return
{"ok": False, "blocked": True, ...}` — to `post_options_earnings_crush_execute` and
`post_options_dispersion_execute`. It did **not** add the equivalent block to
`post_options_zero_dte_execute`, which kept calling `execute_0dte_trade(...)` unconditionally
regardless of the new field's value. The field existed on the request model but nothing in the
handler ever read it. A caller could still execute a real 0DTE paper trade with zero deployability
gating, despite the PR's own commit message claiming otherwise.

**Fix**: added the identical enforcing block to `post_options_zero_dte_execute`, keyed off
`OPTIONS_DESK_DEPLOYABILITY_GATES["zero_dte_engine"]`, matching the earnings_crush/dispersion
pattern exactly.

**Fixing this also surfaced two pre-existing, already-broken tests**, unrelated to the missing
zero_dte block itself: `test_earnings_crush_execute_surfaces_gate_status` and
`test_dispersion_execute_surfaces_gate_status` in
`tests/test_options_desk_deployability_runtime_gap.py` posted request bodies without
`override_deployability_gate: true` and asserted a 200 with `gate_status` in the body — exactly
the shape the *new* blocking behavior for those two endpoints no longer returns. These two tests
broke the moment the original commit's own earnings_crush/dispersion enforcement landed, and were
fixed alongside the zero_dte gap (updated to pass the override explicitly).

## Deliberate behavior reversal (disclosed)

Fixing the zero_dte gap did not just close a bug — it reverses a decision this repo's own docs had
previously stated explicitly. `CLAUDE.md`/`AGENTS.md`'s "Options desk ML/safety gates and
findings" bullet used to say the three `UNGATEABLE_DATA_GAP` strategies' gate is "informational
and never blocks," in deliberate contrast to `vol_mispricing`'s enforced `MEASURED_FAIL` gate. That
is no longer accurate for any of the three, so it no longer says that. As of this PR, **all four**
options-desk execute endpoints (`earnings_crush`, `dispersion_trading`, `zero_dte_engine`,
`vol_mispricing`) enforce their gate identically: blocked by default, proceeding only when the
request explicitly sets `override_deployability_gate: true`. The `UNGATEABLE_DATA_GAP` vs
`MEASURED_FAIL` distinction is now purely about *why* a strategy is blocked (no historical data to
measure a verdict from, vs. a real measured failing verdict) — not about whether it blocks.
`CLAUDE.md`, `AGENTS.md`, and `docs/signals/vol_mispricing.md` were all updated to state this
plainly rather than silently drop the old claim.

## Changes made (final, verified file list)

- `forecasting/forecast_tracker.py` — rewrote `get_covered_symbols()` per the Gap 3 root cause above.
- `api/data_api.py` — `get_sync_report()` threads `ForecastTracker().get_covered_symbols(horizon_days=30)`
  into `build_sync_report(snapshot, forecast_symbols=...)`; the previously-silent
  `except Exception: forecast_symbols = []` now also logs a warning.
- `api/pilots_api.py` — added the missing enforcing block to `post_options_zero_dte_execute`;
  updated `post_options_mispricing_execute`'s docstring to describe the now-shared enforcement
  behavior instead of the old "unlike the other three" framing.
- `data/portfolio_sync.py` — **not modified.** `build_sync_report()`'s `forecast_symbols` parameter
  already existed and needed no code change; it was, however, missing test coverage of its own
  (see below).
- `tests/test_data_api.py` — kwargs-tolerant mock lambdas (original commit), plus a new
  end-to-end regression test using a real (unmocked) `ForecastTracker` against a temp SQLite DB to
  prove the full chain — not just that `build_sync_report` was called, but that a real forecast row
  actually produces `forecast_available: True` in the real HTTP response.
- `tests/test_forecast_tracker.py` — new `TestGetCoveredSymbols` class: recent-forecast match,
  no-forecast exclusion, horizon filtering, the default 7-day recency window excluding a stale
  forecast, `window_days=None` restoring the all-time scan, empty-on-DB-error, and a regression
  test proving the tracker's shared connection is still usable after a call (guards the
  connection-leak bug specifically).
- `tests/test_portfolio_sync.py` — new tests for `build_sync_report`'s `forecast_symbols` kwarg,
  which had zero test coverage before this PR despite already existing in the function signature:
  a covered symbol reports `forecast_available=True`, an uncovered held symbol reports `False`,
  the comparison is case-insensitive, and both "kwarg omitted" and "kwarg is `[]`" degrade to
  `False` for every symbol without raising.
- `tests/test_options_desk_deployability_runtime_gap.py` — fixed the two broken tests above (now
  pass `override_deployability_gate: true` where they mean to exercise the post-override response
  shape), and added a `test_*_blocked_without_override_never_executes_a_trade` test for each of
  earnings_crush/dispersion/zero_dte that mocks the underlying `execute_*_trade` function and
  asserts `mock_exec.assert_not_called()` — proving the block actually prevents the trade, not
  just that the response shape looks blocked.
- `tests/test_pilots_api.py` — updated the comment above `TestVolMispricingExecuteDeployabilityGate`
  to describe the shared four-endpoint enforcement instead of the old "vol_mispricing is the one
  that blocks" framing.
- `CLAUDE.md` / `AGENTS.md` — corrected the "Options desk ML/safety gates and findings" bullet
  (item 3) to disclose the reversal above instead of restating the now-false "informational, never
  blocks" claim.
- `docs/signals/vol_mispricing.md` — added a dated correction note under "Live Paper-Execution
  Status" for the same stale claim it repeated.

## Fourth-pass addendum: `tests/test_pilots_paper_broker.py` regression + response-shape parity

No prior pass had run `tests/test_pilots_paper_broker.py`, which exercises these same three
endpoints from a different angle than `test_pilots_api.py`/`test_options_desk_deployability_runtime_gap.py`.
It found 8 pre-existing tests (2 earnings_crush, 3 dispersion, 3 zero_dte) that legitimately test
real execution/rejection behavior and now require `override_deployability_gate: true` under the
default-blocked gate — fixed by adding that field to each payload, not by loosening the gate.
Separately, the blocked-response dict for all three endpoints was missing `gate_status` (present in
`vol_mispricing`'s own blocked response) and the success path was missing `override_applied` —
fixed for full parity, which in turn required correcting 3 of the second pass's own new
`test_*_blocked_without_override_never_executes_a_trade` tests, which had asserted `gate_status`
absent.

`docs/settings_field_census.json`/`.md` were also stale after `api/pilots_api.py`'s edits (breaking
`tests/test_measure_settings_census.py`) — regenerated via `scripts/measure_settings_census.py --write`.

## Verification Plan

Run, and confirm zero failures on:
```
pytest tests/test_forecast_tracker.py tests/test_options_desk_deployability_runtime_gap.py tests/test_portfolio_sync.py tests/test_data_api.py -q
pytest tests/test_pilots_api.py -k "VolMispricing or Deployability or vol_mispricing or ZeroDte or zero_dte or Dispersion or EarningsCrush or earnings_crush" -q
pytest tests/test_pilots_paper_broker.py tests/test_measure_settings_census.py tests/test_earnings_crush.py tests/test_dispersion_trading.py tests/test_zero_dte_engine.py tests/test_vol_mispricing.py -q
```
Real results from this session (see the walkthrough for the full write-up): first two commands —
122 passed (`test_forecast_tracker.py` + `test_options_desk_deployability_runtime_gap.py` +
`test_portfolio_sync.py`), 58 passed (`test_data_api.py`), 7 passed (the targeted
`test_pilots_api.py` slice). Combined final run across every file this change (across all four
passes) touches: **915 passed, 0 failed**. A full offline-suite run (`pytest -m "not network and
not slow"`) was also performed as a final check before merge — see the walkthrough for its result.
