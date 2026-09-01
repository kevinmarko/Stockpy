# Phase 37 — Work Package C: Universe Disconnect — Task Tracker

- [x] `api/data_api.py::get_sync_report()` — `forecast_symbols` reverted from
      `resolve_universe()` back to `ForecastTracker().get_covered_symbols(horizon_days=30)`.
- [x] `investyo_mcp_server.py::get_portfolio_coverage()` — now threads the
      same `forecast_symbols` source into `build_sync_report()` (previously
      always `forecast_available=False`).
- [x] `tests/test_data_api.py::test_sync_report_forecast_available_reflects_real_forecast_tracker`
      re-verified passing against the reverted code.
- [x] `forecasting_engine.py::generate_forecast()` — insufficient-history
      `ValueError` now propagates instead of being swallowed internally.
- [x] `forecasting/forecast_tracker.py::record_forecasts()` — "empty"
      sentinel row now actually persists (NOT NULL fix) and is permanently
      excluded from skill-weight aggregates via `update_actuals()`.
- [x] `main_orchestrator.py::_main_body_impl()` now returns `ctx.macro_dto`;
      stray return statement removed from `main()`.
- [x] `tests/test_main.py` — 3 renamed tests' `test_` prefix restored.
- [x] `execution/options_lifecycle.py` — logger restored to `"InvestYo.main"`.
- [x] `docs/known_issues/options_lifecycle_daemon_gate_gap_2026_08_22.md` —
      Bug 2 status corrected to fixed.
- [ ] Underlying narrow-forecast-coverage-vs-wide-active-universe gap —
      explicitly out of scope for this pass; remains open, not silently closed.
