# Phase 37 — Work Package C: Universe Disconnect — Implementation Plan

## §0 Dependency check

- `data/portfolio_sync.py::resolve_universe()` — real, live function; returns
  the operator's tracked universe (held ∪ watchlists ∪ `settings.DEFAULT_TICKERS`).
- `data/portfolio_sync.py::build_sync_report(snapshot, *, forecast_symbols=..., probe_market=...)`
  — real, live function; accepts an explicit `forecast_symbols` iterable used
  to set each row's `forecast_available` flag.
- `forecasting/forecast_tracker.py::ForecastTracker.get_covered_symbols(horizon_days=...)`
  — real, live method; returns symbols with an actual recorded forecast row
  in `forecast_errors` within the lookback window.
- `investyo_mcp_server.py::get_portfolio_coverage()` — a second, independent
  caller of `build_sync_report()`, parallel to `api/data_api.py::get_sync_report()`.
- `pytest.ini`'s `python_functions = test_*` collection pattern.
- `forecast_errors` table DDL: `forecast_price REAL NOT NULL`.

## Problem

The originally-shipped WP C fix (`api/data_api.py::get_sync_report()`)
swapped `ForecastTracker().get_covered_symbols(horizon_days=30)` for
`resolve_universe()` to compute `forecast_symbols`, intending to fix
`forecast_available` being `False` for most of the ~430-symbol active
trading universe (vs. the narrower set of symbols that had ever received a
real recorded forecast).

A follow-up code-review pass (10 findings against the merged PR, see
`docs/VALIDATION_STRATEGY_FIX_LOG.md`-adjacent review record) found this
swap changed what `forecast_available` *means*, not just its coverage:
`resolve_universe()`'s output is nearly the same union `build_sync_report()`
already iterates to build the whole report, so `forecast_available` became
near-tautologically `True` for almost every symbol regardless of whether
`ForecastingEngine` ever actually produced a forecast for it — a CONSTRAINT
#4 concern (a fabricated-looking coverage signal), and it broke the
pre-existing regression test
`tests/test_data_api.py::test_sync_report_forecast_available_reflects_real_forecast_tracker`.

## Decision (this pass)

1. Revert `api/data_api.py::get_sync_report()`'s `forecast_symbols` source
   back to `ForecastTracker().get_covered_symbols(horizon_days=30)` — the
   semantically-correct source (a symbol having a real recorded forecast),
   restoring the pre-existing regression test's guarantee.
2. Thread the same `ForecastTracker().get_covered_symbols(horizon_days=30)`
   call into `investyo_mcp_server.py::get_portfolio_coverage()`'s
   `build_sync_report(...)` call, which previously passed no
   `forecast_symbols` at all (always `forecast_available=False`) — closing
   the parallel MCP read path the original WP C fix never touched.
3. The genuine "narrow forecast coverage vs. the wide active trading
   universe" problem this WP was meant to address is a real, separate,
   larger gap in the forecasting pipeline itself (why `ForecastTracker` only
   ever records forecasts for a subset of the tracked universe) — out of
   scope for this pass. Showing the true (narrow) coverage honestly is
   preferable to a fabricated near-universal signal; the underlying gap
   remains open and undocumented as its own known issue (a follow-up, not
   silently closed here).
4. Also fixed in the same commit (from the same code-review pass, grouped
   here since they touch immediately-adjacent forecasting/daemon code):
   `forecasting_engine.py`'s insufficient-history `ValueError` now actually
   propagates instead of being swallowed by its own outer `except Exception`
   (was silently returning fabricated `$0.00` forecasts);
   `forecasting/forecast_tracker.py::record_forecasts()`'s "empty" sentinel
   row now actually persists (was violating `forecast_price REAL NOT NULL`
   on every insert) while remaining permanently excluded from
   `get_skill_weights()`/the `pilots/observability.py` skill-weight
   aggregates; `main_orchestrator.py::_main_body_impl()` now actually
   returns `ctx.macro_dto` (was implicitly `None`, defeating WP D's
   daemon-path regime-gate fix) and the stray `return getattr(ctx,
   "macro_dto", None)` erroneously appended to `main()` (which has no local
   `ctx`, crashing every successful `python3 main_orchestrator.py` run with
   `NameError`) was removed; 3 renamed tests in `tests/test_main.py` had
   their `test_` prefix restored (were silently dropped from pytest
   collection); `execution/options_lifecycle.py`'s logger was restored to
   `"InvestYo.main"` (was `__name__`, breaking `tests/test_main.py`'s
   `caplog.at_level(..., logger="InvestYo.main")` scoping).

## Documentation-update step

- `docs/known_issues/options_lifecycle_daemon_gate_gap_2026_08_22.md` — Bug
  2's status corrected from "documented, not fixed" to fixed, now that
  `main_orchestrator.py::_main_body_impl()` genuinely returns `macro_dto` to
  the daemon path (item 4 above), matching WP D's own prior fix claim.
- This file, `.claude/feat-phase37-wp-c-manual_task.md`, and the new
  `.claude/feat-phase37-wp-c_walkthrough.md` are the WP C plan/task/
  walkthrough triad this PR was previously missing (the original stubs were
  content-free single lines — flagged and fixed in the same review pass).
- No `docs/architecture/*.md`/`docs/signals/*.md` change is needed — no
  signal module, sizing, or execution-surface behavior changed.
