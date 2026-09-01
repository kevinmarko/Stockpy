# Phase 37 Work Package C — Walkthrough (code-review fix pass)

## Summary

The originally-shipped WP C change swapped `api/data_api.py::get_sync_report()`'s
`forecast_symbols` source from `ForecastTracker().get_covered_symbols(horizon_days=30)`
to `data.portfolio_sync.resolve_universe()`, intending to widen `forecast_available`
coverage from a narrow ~26-symbol set to the full ~430-symbol active trading
universe. A 10-finding code review against the merged PR (`feat-phase-37-remediation`)
found this changed the *meaning* of `forecast_available`, not just its
coverage — `resolve_universe()`'s output is nearly the same union
`build_sync_report()` already iterates over, so the flag became
near-tautologically `True` regardless of whether a real forecast was ever
computed (a CONSTRAINT #4 concern), and it broke the pre-existing regression
test `test_sync_report_forecast_available_reflects_real_forecast_tracker`.

## Changes made in this fix pass

1. **`api/data_api.py`** — reverted `forecast_symbols` back to
   `ForecastTracker().get_covered_symbols(horizon_days=30)`. Verified the
   pre-existing regression test now passes again.
2. **`investyo_mcp_server.py::get_portfolio_coverage()`** — this parallel MCP
   read path called `build_sync_report(snapshot, probe_market=True)` with no
   `forecast_symbols` at all, so it always reported `forecast_available=False`
   regardless of real coverage — a scope gap the original WP C fix never
   touched. Now threads the same `ForecastTracker().get_covered_symbols(...)`
   call, keeping both read paths consistent.
3. **`forecasting_engine.py::generate_forecast()`** — the WP C addition of
   `raise ValueError("Insufficient historical data...")` (replacing a
   fabricated `mu=0.0002/sigma=0.015` Monte Carlo cone) was itself caught by
   this same function's own outer `except Exception`, so it never reached
   `pipeline/production_steps.py::ForecastingStep`'s new NaN-fallback branch
   — verified live: the function returned a dict of literal `0.0` for every
   forecast field instead, worse than the fabrication it was meant to
   replace. Added an explicit `except ValueError: raise` before the generic
   handler so the error now genuinely propagates to the caller's NaN
   fallback. Verified both existing external callers
   (`api/metrics_api.py`, `engine/advisory.py`) already wrap the call in
   their own `try/except Exception` and degrade correctly (404 / `None`
   forecast_price + `partial_flags`), so nothing new crashes.
4. **`forecasting/forecast_tracker.py::record_forecasts()`** — the WP C
   "empty" sentinel row (`forecast_price=None`) always violated the table's
   `forecast_price REAL NOT NULL` constraint, so it never actually
   persisted (verified live: `sqlite3.IntegrityError`, 0 rows inserted,
   silently caught). Fixed to use `0.0` (not `None`, and specifically not
   `float("nan")` — verified SQLite silently coerces a bound NaN back to
   NULL, which still trips the same constraint). Added a `MODEL_EMPTY`
   constant and excluded it from `update_actuals()`'s actualization query
   (`AND model_name != ?`), so the sentinel row can never receive an
   `actual_price`/`squared_error` and therefore can never appear in
   `get_skill_weights()` or `pilots/observability.py`'s two aggregate
   siblings (all three filter on `actual_price IS NOT NULL`) — verified
   live: the row now persists, and `get_skill_weights()` still returns `{}`
   for a symbol with only an "empty" row.
5. **`main_orchestrator.py`** — `_main_body_impl()`'s signature claimed
   `-> Optional[Any]` but the function body had no `return` statement at
   all; added `return ctx.macro_dto` at the end. Separately, a
   `return getattr(ctx, "macro_dto", None)` line had been mistakenly
   appended to `async def main()` (the CLI entrypoint), which never binds a
   local `ctx` — this raised `NameError` on every successful
   `python3 main_orchestrator.py` run, uncaught by the `except
   PipelineFatalError` wrapper. Removed the stray line (its intended
   purpose — the daemon path — is already served by `_main_body_impl()`'s
   fix, which flows through `_main_body`).
6. **`tests/test_main.py`** — 3 test functions had lost the underscore after
   `test` (`testrun_automated_delta_hedge_cycle_...`), silently failing
   `pytest.ini`'s `python_functions = test_*` collection pattern. Renamed
   back to `test_run_automated_delta_hedge_cycle_...`.
7. **`execution/options_lifecycle.py`** — the module's logger had been
   changed to `logging.getLogger(__name__)` during the move out of
   `main.py`, but `tests/test_main.py`'s `caplog.at_level(..., logger="InvestYo.main")`
   assertions still scope to the old name. Restored `logging.getLogger("InvestYo.main")`.
8. **`docs/known_issues/options_lifecycle_daemon_gate_gap_2026_08_22.md`** —
   Bug 2's status updated from "documented, not fixed" to fixed, now that
   item 5 above genuinely closes the gap it describes.

## Verification

- `pytest tests/test_data_api.py -k test_sync_report_forecast_available_reflects_real_forecast_tracker`
- `pytest tests/test_main.py`
- `pytest tests/test_forecast_tracker.py`
- Live repro scripts confirming: `generate_forecast()` with no history now
  raises (was returning `$0.00`s); `record_forecasts()` with an empty
  `model_prices` dict now persists a row and `get_skill_weights()` still
  returns `{}`; `_main_body_impl()` now returns a real `MacroEconomicDTO`.

## Non-goals / disclosed remaining gap

The underlying reason `ForecastTracker` only ever covers a narrow subset of
the ~430-symbol active trading universe (rather than every symbol
`ForecastingStep` runs each cycle) is **not** fixed by this pass — see
`.claude/feat-phase37-wp-c-manual_plan.md`'s "Decision" §3. Showing the true,
narrower coverage honestly (this pass) is preferred over a fabricated
near-universal signal (the reverted approach), but the real gap remains open.
