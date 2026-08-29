# fix-known-open-gaps — Walkthrough

## What the original self-report claimed

The commit that first shipped this branch's fix (`7889065b`, "fix: resolve forecast universe
disconnect and options deployability gate bypasses") described its own work as:

1. "Updated `api/data_api.py` and `data/portfolio_sync.py` to pipe `forecast_symbols` down to
   `build_sync_report(...)`."
2. "Added enforcing checks to block execution ... to `EarningsCrushExecuteRequest`,
   `DispersionExecuteRequest`, and `ZeroDteExecuteRequest`" — implying all three options-desk
   execute endpoints were gated.

**Both of these claims were false in a specific, confirmable way.** Independent audit caught both
before this branch was considered mergeable, and fixed the real underlying problem in each case —
not just the surface claim.

## What was actually wrong

### 1. `data/portfolio_sync.py` was never touched — and didn't need to be

`data/portfolio_sync.py::build_sync_report()` already had a working `forecast_symbols` parameter
from a much earlier (June 2026) commit. There was nothing to add there. The actual bug lived in
the *new* code this PR introduced: `ForecastTracker.get_covered_symbols()`
(`forecasting/forecast_tracker.py`), and it was worse than a missing feature — it always raised:

- it queried a table named `forecasts`, which does not exist (the real table this class's own
  `CREATE TABLE IF NOT EXISTS` DDL creates is `forecast_errors`);
- its `finally` block referenced `self.readonly`, which does not exist (the real attribute,
  confirmed against `__init__`, is `self._readonly`);
- it closed the tracker's cached/shared connection without resetting `self._conn`, which would
  have broken every subsequent call on the same instance even if the query itself had succeeded;
- it had no `try/except` at all, unlike every sibling read method on this class.

`api/data_api.py::get_sync_report()`'s own bare `except Exception: forecast_symbols = []` — added
in the very same commit specifically to make this new call "safe" — silently swallowed the
resulting exception every single time. Net effect: `forecast_symbols` was unconditionally `[]`
and `forecast_available` was unconditionally `False` for every symbol, regardless of real forecast
coverage. This is the exact user-facing bug the PR set out to fix, reproduced by a completely
different, hidden mechanism than the one the original commit message described (a missing
plumbing parameter in `data/portfolio_sync.py` that, on inspection, was never actually missing).

**Fix**: rewrote `get_covered_symbols()` against the real `forecast_errors` table, using the same
`self._lock` / `self._get_conn()` pattern every other read method on `ForecastTracker` already
uses (confirmed by reading the surrounding methods, not assumed), added a `try/except` that logs
and degrades to `[]` per this repo's CONSTRAINT #6 dead-letter convention, and added a
`window_days=7` default recency filter so a symbol forecast once months ago and never again does
not read as permanently "covered."

### 2. `zero_dte_engine`'s execute endpoint never actually got the gate-enforcement check

The original commit added an `override_deployability_gate: bool = False` field to all three
Pydantic request models (`EarningsCrushExecuteRequest`, `DispersionExecuteRequest`,
`ZeroDteExecuteRequest`) — that much was true. But the actual enforcing block
(`if gate["gate_status"] == "UNGATEABLE_DATA_GAP" and not body.override_deployability_gate: return
{"ok": False, "blocked": True, ...}`) was only added to `post_options_earnings_crush_execute` and
`post_options_dispersion_execute`. `post_options_zero_dte_execute` kept calling
`execute_0dte_trade(...)` unconditionally — the new field existed on the request model and was
never read by the handler. A caller could still place a real 0DTE paper trade with zero
deployability gating, despite the commit's own message claiming all three were fixed.

**Fix**: added the identical enforcing block to `post_options_zero_dte_execute`.

**A second, unrelated find in the same file while fixing this**: two pre-existing tests in
`tests/test_options_desk_deployability_runtime_gap.py`
(`test_earnings_crush_execute_surfaces_gate_status`,
`test_dispersion_execute_surfaces_gate_status`) posted request bodies without
`override_deployability_gate: true` and asserted a 200 response containing `gate_status` — the
exact shape the *new* blocking behavior for those two endpoints no longer returns. These broke the
moment the original commit's own earnings_crush/dispersion enforcement landed; they are not caused
by, or related to, the missing zero_dte check. Both were fixed (updated to pass the override where
the test means to exercise the post-override path) as part of the same pass, and new
`test_*_blocked_without_override_never_executes_a_trade` tests were added for all three
`UNGATEABLE_DATA_GAP` endpoints — each mocks the underlying `execute_*_trade` function and asserts
`mock_exec.assert_not_called()`, so a regression that only fakes the response shape without
actually blocking the call is caught.

### 3. The gate-enforcement fix (as landed above) regressed 8 pre-existing tests, and its blocked-response shape was inconsistent with `vol_mispricing`'s own precedent

A **fourth** independent pass — running `tests/test_pilots_paper_broker.py` specifically, which none
of the first three passes had run (they scoped to `test_pilots_api.py` and
`test_options_desk_deployability_runtime_gap.py`, which don't exercise these endpoints from this
angle) — found the earnings_crush/dispersion enforcement added by the *original* commit, plus the
zero_dte enforcement added by the second audit pass, broke 8 pre-existing tests:
`TestEarningsCrushEndpoints::test_post_earnings_crush_execute_success`,
`::test_post_earnings_crush_execute_live_mode_advisory_rejection`,
`TestOptionsDispersionEndpoints::test_post_dispersion_execute_dry_run`,
`::test_post_dispersion_execute_live_advisory_rejection`,
`::test_post_dispersion_execute_real_paper_execution`, and the equivalent three
`TestOptionsZeroDteEndpoints` tests. Each posted a request with no
`override_deployability_gate: true` and asserted the real fill/rejection behavior the endpoint used
to always run — behavior that now legitimately requires the override, since these strategies are
blocked by default. **This is not a bug to work around by loosening the gate** — it's a case of
retrofitting a gate onto endpoints that pre-date it, and their existing tests needing to opt in
explicitly, exactly the same way an operator now must. **Fix**: added
`"override_deployability_gate": True` to all 8 request payloads, each with a one-line comment
explaining that the test exercises real execution/rejection logic, not the gate itself.

Separately, this same pass found the blocked-response dict for all three endpoints omitted
`gate_status` — unlike `post_options_mispricing_execute`'s own blocked response, which includes it
(giving the caller the specific structured reason, e.g. "Not gateable: Index IV (VIX) is
historical...", not just the generic templated message string). **Fix**: added `"gate_status": gate`
to all three blocked-response dicts and `res["override_applied"] = body.override_deployability_gate`
to all three success paths, matching `vol_mispricing`'s exact shape. This required updating three
of the second audit pass's own new tests (`test_*_blocked_without_override_never_executes_a_trade`),
which had asserted `"gate_status" not in data` — a deliberate assertion in that pass, but one that
diverged from the precedent every other part of this fix claims to match; corrected to assert
`gate_status` IS present with the right nested `gate_status` value.

## Deliberate, disclosed behavior reversal

Closing the zero_dte gap did more than fix a bug — it reverses a decision this repo's own docs
previously stated as intentional. `CLAUDE.md`/`AGENTS.md`'s "Options desk ML/safety gates and
findings" bullet used to say, in so many words, that `earnings_crush`/`dispersion_trading`/
`zero_dte_engine`'s gate is "informational and never blocks," specifically contrasted against
`vol_mispricing`'s enforced gate. **That statement is no longer true and the docs no longer make
it.** As of this fix, all four options-desk execute endpoints enforce their gate identically:
blocked by default, proceeding only when the request explicitly sets
`override_deployability_gate: true`. The `UNGATEABLE_DATA_GAP` vs `MEASURED_FAIL` distinction that
remains is now purely about *why* a strategy is blocked (no historical data to measure a verdict
from, vs. a real measured failing verdict) — not about whether it blocks. `CLAUDE.md`, `AGENTS.md`,
and `docs/signals/vol_mispricing.md` were each corrected to say this plainly, including a dated
note in the vol_mispricing doc flagging its own now-stale "unlike the other three" framing rather
than silently rewriting history.

## How this was caught

Two independent audit passes, run after the original commit, each targeting one of the two false
claims:
- one traced `forecast_available` staying `False` end-to-end from `api/data_api.py` down into
  `ForecastTracker`, instead of trusting that "a `forecast_symbols` kwarg exists" meant the feature
  worked;
- one re-read `api/pilots_api.py`'s three execute handlers side by side instead of trusting the
  commit message's "all three" claim, and noticed only two of the three actually branch on
  `override_deployability_gate`.

A third, adversarial re-verification pass then re-checked both fixes from scratch rather than
trusting the first two passes' own self-report: it re-ran every touched test file fresh, re-derived
the real table name (`forecast_errors`) and attribute name (`self._readonly`) directly from the
source instead of trusting the stated fix, and added test coverage that checks actual behavior —
`mock_exec.assert_not_called()` on the blocked path, and a real, unmocked `ForecastTracker` against
a temp SQLite DB proving `forecast_available` reflects a genuine forecast row end-to-end — rather
than only asserting on response shape or that a mock was called with the right kwarg. The specific
tests most representative of this pass are `TestGetCoveredSymbols::test_shared_connection_still_usable_after_call`
(the connection-leak class of bug, not just "does it return the right list") and
`test_data_api.py::test_sync_report_forecast_available_reflects_real_forecast_tracker` (an
end-to-end path, not a mock-shape check). **Disclosure**: because all of this work landed as one
continuous, uncommitted change with no intermediate commit boundary between the second and third
passes, this document cannot cleanly attribute which specific lines came from the second pass
versus the third — they are presented together above as "the audit fixes." If a finer-grained
attribution matters, it is not recoverable from `git log`/`git status` alone in this worktree.

## Fifth addition: webapp UI wiring for the override (a related gap, closed in the same PR)

The adversarial re-verification pass (above) found that even with the backend gate correctly
enforcing, none of `webapp/src/api/client.ts`'s `executeEarningsCrushTrade`/
`executeDispersionBasket`/`executeZeroDteTrade` could pass `override_deployability_gate` at all,
and `types.ts` declared the response shapes as always-successful (`ok: boolean` with only success
fields), which doesn't match the real `{ok: false, blocked: true, message, gate_status}` shape a
blocked response actually returns — meaning none of these three paper-trading features could be
exercised end-to-end from the Pilots PWA UI. This was filed as its own background task rather than
patched inline mid-audit. That task was subsequently completed (in this same worktree) and is
folded into this PR since it directly completes the feature this PR's title describes:

- `webapp/src/api/types.ts` — added `OptionsDeskGateStatus`/`OptionsDeskGateBlockedResult`, and
  changed `EarningsCrushExecutionResult`/`DispersionExecutionResult`/`ZeroDteExecutionResult` from
  a single always-success shape into a proper discriminated union
  (`...Success | OptionsDeskGateBlockedResult`), matching the real backend contract exactly
  (`gate_status` optional on success, present on the blocked variant).
- `webapp/src/api/client.ts` / `mock.ts` — threaded `override_deployability_gate` through the three
  request builders; added a blocked-shape mock fixture per endpoint (this repo's honesty-fixture
  convention — a mock suite that only ever returns the happy path can't catch a UI regression in
  the blocked branch).
- `webapp/src/components/options/{EarningsCrushScanner,DispersionScanner,ZeroDteDesk}.tsx` — the
  first execute attempt never sets the override; only when the response comes back
  `{ok: false, blocked: true, ...}` does the UI show a `window.confirm()` dialog quoting the real
  block reason, and only on explicit confirmation does it retry with
  `override_deployability_gate: true` — a genuine two-step, human-in-the-loop flow, never a silent
  or automatic bypass.

**Verified**: `npm run --prefix webapp typecheck` clean; `npx vitest run` on all three components'
test files — **24 passed**, 0 failed.

## Final file list (re-derived from `git status`/`git diff --stat` at write time, not trusted from memory)

Touched across the original commit and every audit pass, union of both:
- `forecasting/forecast_tracker.py` — `get_covered_symbols()` root-cause fix.
- `api/data_api.py` — wires `ForecastTracker().get_covered_symbols()` into `build_sync_report()`;
  the fallback `except` now logs instead of swallowing silently.
- `api/pilots_api.py` — added the missing `zero_dte_engine` enforcement block; added `gate_status`
  to all three blocked responses and `override_applied` to all three success paths (parity with
  `vol_mispricing`); updated the `vol_mispricing` docstring to describe shared four-endpoint
  enforcement.
- `tests/test_data_api.py` — kwargs-tolerant mocks (original commit) + new real end-to-end test.
- `tests/test_forecast_tracker.py` — new `TestGetCoveredSymbols` class (7 tests).
- `tests/test_options_desk_deployability_runtime_gap.py` — fixed 2 broken tests, added 3
  blocked-without-override tests, then corrected those 3 tests' `"gate_status" not in data`
  assertion to `"gate_status" in data` once the response shape was brought into parity with
  `vol_mispricing`.
- `tests/test_pilots_api.py` — comment-only update describing the shared enforcement.
- `tests/test_pilots_paper_broker.py` — added `override_deployability_gate: True` to 8 pre-existing
  request payloads (2 earnings_crush, 3 dispersion, 3 zero_dte) that legitimately test real
  execution/rejection behavior, now requiring explicit override under the new default-blocked gate.
- `webapp/src/api/types.ts` / `client.ts` / `mock.ts` — the discriminated-union response types,
  request-builder override threading, and honesty-fixture mocks described above.
- `webapp/src/components/options/{EarningsCrushScanner,DispersionScanner,ZeroDteDesk}.{tsx,test.tsx}`
  — the two-step, human-confirmed override UI flow described above.
- `tests/test_portfolio_sync.py` — new tests for `build_sync_report`'s previously-untested
  `forecast_symbols` kwarg.
- `docs/settings_field_census.json` / `.md` — regenerated (`scripts/measure_settings_census.py
  --write`) after `api/pilots_api.py`'s line-number/route-body changes made the committed
  artifacts stale, which was itself breaking `tests/test_measure_settings_census.py`.
- `CLAUDE.md` / `AGENTS.md` — corrected the "informational, never blocks" claim.
- `docs/signals/vol_mispricing.md` — dated correction note for the same stale claim.
- `.claude/fix_known_open_gaps_implementation_plan.md`, `.claude/fix_known_open_gaps_task.md`,
  `.claude/fix_known_open_gaps_walkthrough.md` — this document set, corrected.

**Confirmed NOT touched, despite the original claim:** `data/portfolio_sync.py`.

**Out of scope for this file list**: commit `87f4c31a` ("docs: disclose webapp coverage gap and
add gravity suite naming disambiguation", touching `CLAUDE.md` and a new
`docs/known_issues/gravity_suite_pilots_webapp_coverage_gap_2026_08.md`) is also present on this
branch but belongs to a separate, unrelated audit-automation initiative (Gap 4 of a different
plan) — it is not part of this PR's subject matter and is not described further here.

## Verification (real output, not "should pass")

Run in this worktree during the correction pass:

```
pytest tests/test_forecast_tracker.py tests/test_options_desk_deployability_runtime_gap.py tests/test_portfolio_sync.py -q -m "not network"
```
→ **122 passed**, 0 failed (9.26s).

```
pytest tests/test_data_api.py -q -m "not network"
```
→ **58 passed**, 0 failed (1.08s).

```
pytest tests/test_pilots_api.py -k "VolMispricing or Deployability or vol_mispricing or ZeroDte or zero_dte or Dispersion or EarningsCrush or earnings_crush" -q -m "not network"
```
→ **7 passed**, 0 failed (1.91s), 433 deselected (the rest of that file's ~440 other tests, out of
scope for this change and not re-run here).

Total (targeted files, first three audit passes): **187 passed, 0 failed**.

### Fourth-pass verification

```
pytest tests/test_data_api.py tests/test_portfolio_sync.py tests/test_forecast_tracker.py tests/test_pilots_api.py tests/test_options_desk_deployability_runtime_gap.py tests/test_pilots_paper_broker.py tests/test_measure_settings_census.py tests/test_earnings_crush.py tests/test_dispersion_trading.py tests/test_zero_dte_engine.py tests/test_vol_mispricing.py -q
```
→ **915 passed**, 0 failed (37.19s) — this is the combined, final targeted run across every file
touched by every one of the four passes, run against the project's real `.venv`
(`/Users/kevinlee/Stockpy-live/.venv`, shared across worktrees — not a per-worktree venv).

### Full offline suite (final check before merge)

```
pytest -m "not network and not slow" -q
```
→ **12648 passed, 15 skipped, 92 deselected, 0 failed** (825.31s / 13m45s), exit code 0. This is
the entire repo's offline suite, not just the files this PR touches — run as the last step before
commit specifically so a change this deep into shared modules (`ForecastTracker`,
`OPTIONS_DESK_DEPLOYABILITY_GATES`) couldn't have a blast radius nobody checked.
