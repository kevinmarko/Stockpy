# Forecasting Rebuild F4 Part 2a — Task Tracker

- [x] Write implementation plan `.claude/forecasting_f4_part2a_band_persistence_implementation_plan.md` and incorporate operator review corrections
- [x] Add `Forecast_{10,30,60,90}_{Lower,Upper}` to `forecast_cols` and `_forecast_one` fallback in `pipeline/production_steps.py`
- [x] Emit `forecast_{10,30,60,90}_{lower,upper}` in `main_orchestrator.py::_write_state_snapshot`
- [x] Extend `tests/test_production_steps_columns_contract.py`, `tests/test_forecasting_step_fallback_disclosure.py`, and `tests/test_state_snapshot_parity.py`
- [x] Run targeted forecast tests and `make ci`
- [x] Update `docs/architecture/signal-engines.md`, `.claude/forecasting_rebuild_implementation_plan.md`, and `docs/FEATURE_TIER_HISTORY.md`
- [x] Commit `.claude/forecasting_f4_part2a_band_persistence_*` artifacts, push, and open PR (do not merge)
