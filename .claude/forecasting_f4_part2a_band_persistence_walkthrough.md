# Walkthrough — Forecasting Rebuild F4 Part 2a: Per-Horizon Forecast Band Persistence

## Overview

Persists the per-horizon Monte-Carlo p5/p95 forecast bands (`Forecast_{10,30,60,90}_{Lower,Upper}`) that `ForecastingEngine.generate_forecast()` (`forecasting_engine.py` lines 1780–1794) already computed (when `> 0` and not suppressed by an F2 guard) through `pipeline/production_steps.py::ForecastingStep` onto `ctx.dashboard_df` and into `OUTPUT_DIR/state_snapshot.json`'s `signals[]` entries.

This change is **persistence-only** (freeze-allowed under `.claude/forecasting_rebuild_implementation_plan.md` §F4; independent of the F3 flip) and makes zero changes to forecast math, model blends, the naive gate, `config.COLUMN_SCHEMA`, or any decision logic.

---

## Changes Made

1. **`pipeline/production_steps.py` (`ForecastingStep`):**
   - Appended `'Forecast_10_Lower'`, `'Forecast_10_Upper'`, `'Forecast_30_Lower'`, `'Forecast_30_Upper'`, `'Forecast_60_Lower'`, `'Forecast_60_Upper'`, `'Forecast_90_Lower'`, `'Forecast_90_Upper'` to `forecast_cols`. Kept deliberately out of `config.COLUMN_SCHEMA` so missing/suppressed bounds remain `NaN` on `ctx.dashboard_df` via `_apply_forecast_columns`'s NaN-fill-first contract (never `0.0` — CONSTRAINT #4).
   - In `_forecast_one`'s `except Exception as ml_err:` fallback branch, captured `(mc_10, mc_10_low, mc_10_high)`, `(mc_60, mc_60_low, mc_60_high)`, and `(mc_90, mc_90_low, mc_90_high)` alongside `(mc_target, mc_low, mc_high)` from `fe.run_monte_carlo(price, mu, sigma, h)` and wrote `Forecast_{h}_Lower` / `Forecast_{h}_Upper` into the fallback dict only when the bound is finite (`math.isfinite(b)`) and `> 0` (leaving all existing fallback keys/values untouched).

2. **`main_orchestrator.py` (`_write_state_snapshot`) & `tests/test_state_snapshot_parity.py`:**
   - Investigated where the daemon's `ctx.dashboard_df` is persisted: `DailySignals` in SQLite has zero production writers (`database_setup.py`), while `StateSnapshotStep` persists `ctx.dashboard_df` via `main_orchestrator._write_state_snapshot()` into `OUTPUT_DIR/state_snapshot.json` using an explicit per-signal key dict.
   - Added `"forecast_{10,30,60,90}_{lower,upper}": _safe_float_or_none(row.get("Forecast_{h}_{Lower|Upper}"))` to `_write_state_snapshot` (`null` when `NaN`/absent, never `0.0`) and registered the 8 keys in `ORCHESTRATOR_ONLY_FIELDS` in `tests/test_state_snapshot_parity.py`.

3. **Tests & Documentation:**
   - Extended `tests/test_production_steps_columns_contract.py` (present bands copied; absent bands stay `NaN`, not `0.0`).
   - Extended `tests/test_forecasting_step_fallback_disclosure.py` (happy-path band round-trip + omitted `NaN` check; fallback branch writes positive finite bounds and omits non-positive/non-finite bounds).
   - Extended `tests/test_state_snapshot_parity.py` (`ORCHESTRATOR_ONLY_FIELDS` + `TestOrchestratorForecastBands`).
   - Updated `docs/architecture/signal-engines.md`, `.claude/forecasting_rebuild_implementation_plan.md` (§F4 status), and `docs/FEATURE_TIER_HISTORY.md`.

---

## Verification

- **Targeted suites:**
  ```bash
  NO_VENV_REEXEC=1 /Users/kevinlee/Stockpy-live/.venv/bin/pytest \
    tests/test_production_steps_columns_contract.py \
    tests/test_forecasting_step_fallback_disclosure.py \
    tests/test_state_snapshot_parity.py \
    tests/test_forecast_rebuild_f2.py \
    tests/test_forecast_rebuild_f3.py \
    tests/test_forecast_parallel.py
  ```
  Result: `142 passed`.
- **Full offline CI gate:**
  ```bash
  NO_VENV_REEXEC=1 make ci PYTHON=/Users/kevinlee/Stockpy-live/.venv/bin/python3
  ```
