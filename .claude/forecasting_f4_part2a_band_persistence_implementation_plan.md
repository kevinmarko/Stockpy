# Forecasting Rebuild — F4 Part 2a: Persist Per-Horizon Forecast Bands (`Forecast_{10,30,60,90}_{Lower,Upper}`)

## Context & Goal

Per `.claude/forecasting_rebuild_implementation_plan.md` (§F4, last bullet: *"Persist per-horizon `Forecast_{h}_Lower/Upper` quantiles (computed today, dropped by the pipeline)"*), `ForecastingEngine.generate_forecast()` (`forecasting_engine.py` lines 1780–1794) already computes Monte-Carlo p5/p95 bounds for every horizon `h in (10, 30, 60, 90)` and writes `Forecast_{h}_Lower` / `Forecast_{h}_Upper` into its returned dict whenever `mc_lo > 0 and mc_band_ok` / `mc_hi > 0 and mc_band_ok` (where `mc_band_ok = "monte_carlo" in model_forecasts or "monte_carlo" not in raw_model_forecasts`).

However:
1. `pipeline/production_steps.py::ForecastingStep.run` (`forecast_cols`, lines 518–535) omits `Forecast_{10,30,60,90}_{Lower,Upper}`, so `_apply_forecast_columns` drops them before writing to `ctx.dashboard_df`.
2. `ForecastingStep._forecast_one`'s `except Exception as ml_err:` fallback branch (lines 578–609) already calls `fe.run_monte_carlo(price, mu, sigma, h)` for `h in (30, 10, 60, 90)`, but discards `lo`/`hi` for `10/60/90` and only populates `MC_Lower`/`MC_Upper` for `h=30`, leaving `Forecast_{10,30,60,90}_{Lower,Upper}` unset on fallback rows.
3. Even after `ctx.dashboard_df` carries `Forecast_{10,30,60,90}_{Lower,Upper}`, `StateSnapshotStep` persists `ctx.dashboard_df` via `main_orchestrator._write_state_snapshot()` (`main_orchestrator.py` lines 970–1133), which builds each `signals[]` entry from an explicit dict of named keys and therefore drops any `dashboard_df` column not explicitly mapped.

This plan is **persistence-only** (freeze-allowed under `.claude/forecasting_rebuild_implementation_plan.md` F4; independent of the F3 flip). It leaves `forecasting_engine.py` untouched and makes zero changes to forecast math, model blends, the naive gate, or any trading/sizing/advisory decision logic.

---

## §0 Dependency & Live-Code Check

Verified against current `origin/main` (`8d584dec`):

1. **`forecasting_engine.py` (lines 1780–1795):**
   - Inside `for h in horizons:` (`horizons = [10, 30, 60, 90]`), `generate_forecast()` computes:
     ```python
     mc_band_ok = "monte_carlo" in model_forecasts or "monte_carlo" not in raw_model_forecasts
     if mc_lo > 0 and mc_band_ok:
         results[f'Forecast_{h}_Lower'] = mc_lo
     if mc_hi > 0 and mc_band_ok:
         results[f'Forecast_{h}_Upper'] = mc_hi
     ```
   - When `mc_band_ok` is False (Monte Carlo dropped by an F2 guard) or `mc_lo <= 0` / `mc_hi <= 0`, the keys `Forecast_{h}_Lower` / `Forecast_{h}_Upper` are omitted from `results` (never written as `0.0`). No change to `forecasting_engine.py` is needed.
2. **`pipeline/production_steps.py::ForecastingStep` (lines 518–610, 672–700):**
   - `forecast_cols` (lines 518–535) currently lists:
     ```python
     forecast_cols = ['Target_Days', 'ARIMA', 'MC_Target', 'MC_Lower', 'MC_Upper',
                      'Forecast_10', 'Forecast_30', 'Forecast_60', 'Forecast_90',
                      'Forecast_30_Prophet_Lower', 'Forecast_30_Prophet_Upper',
                      'Forecast_10_Is_Fallback', 'Forecast_30_Is_Fallback',
                      'Forecast_60_Is_Fallback', 'Forecast_90_Is_Fallback']
     ```
     plus `Forecast_{10,30,60,90}_Gated_Naive` when `FORECAST_NAIVE_GATE_ENABLED` is True.
   - `_apply_forecast_columns(dashboard_df, forecast_results, forecast_cols)` (lines 672–700) pre-fills every column in `forecast_cols` with `nan = float("nan")` and then maps `forecast_results.get(x, {}).get(col, nan)`. Thus any omitted key in `forecast_results[sym]` remains `NaN` on `dashboard_df` (never `0.0`, CONSTRAINT #4).
   - `_forecast_one`'s `except Exception as ml_err:` block (lines 578–609) computes:
     ```python
     mc_target, mc_low, mc_high = fe.run_monte_carlo(price, mu, sigma, 30)
     mc_10, _, _ = fe.run_monte_carlo(price, mu, sigma, 10)
     mc_60, _, _ = fe.run_monte_carlo(price, mu, sigma, 60)
     mc_90, _, _ = fe.run_monte_carlo(price, mu, sigma, 90)
     ```
     and returns a dict with `'Target_Days'`, `'ARIMA'`, `'MC_Target'`, `'MC_Lower'`, `'MC_Upper'`, `'Forecast_10'`, `'Forecast_30'`, `'Forecast_60'`, `'Forecast_90'`, and `'Forecast_{10,30,60,90}_Is_Fallback': True`, but no `Forecast_{h}_{Lower,Upper}` keys.
3. **Where `ctx.dashboard_df` is persisted in the daemon (Requirement 3 investigation):**
   - **SQLite `DailySignals` (`config.COLUMN_SCHEMA`):** Not persisted. Per `database_setup.py` lines 88–140, `DailySignals` has **zero** `INSERT` / `.to_sql()` writers anywhere in the codebase (`_validate_dashboard` in `main_orchestrator.py` lines 1221–1238 only checks `set(COLUMN_SCHEMA) - set(df.columns)` so extra columns on `dashboard_df` pass validation without touching `config.COLUMN_SCHEMA`).
   - **HTML report (`OUTPUT_DIR/daily_report_dashboard.html`) & stdout summary:** `StateSnapshotStep.run` (`pipeline/production_steps.py` lines 3409–3458) renders `reports/daily_report.html.j2` and prints a fixed 11-column subset to stdout.
   - **`OUTPUT_DIR/state_snapshot.json` (+ rotated `OUTPUT_DIR/history/state_snapshot_<ts>.json`):** `StateSnapshotStep.run` (line 3406) calls `main_orchestrator._write_state_snapshot(ctx.macro_raw, ctx.dashboard_df, ctx.symbols, **snapshot_kwargs)`. This JSON file is the daemon's primary persisted store of `dashboard_df` signals.
   - **Finding:** Because `main_orchestrator._write_state_snapshot` (lines 970–1132) constructs each `signals[]` dict from an explicit key list, **the 8 `Forecast_{h}_{Lower,Upper}` columns on `ctx.dashboard_df` do NOT reach `OUTPUT_DIR/state_snapshot.json` as-is**.
   - **Smallest change so they reach the persisted store:**
     - Add the 8 snake_case fields `forecast_{10,30,60,90}_{lower,upper}` to each entry in `snapshot["signals"]` in `main_orchestrator._write_state_snapshot` using `_safe_float_or_none(row.get("Forecast_{h}_{Lower|Upper}"))` (which turns `NaN`/`None`/`±inf` into JSON `null`, never `0.0`), right alongside `"forecast_is_fallback"`.
     - Register those 8 keys in `ORCHESTRATOR_ONLY_FIELDS` in `tests/test_state_snapshot_parity.py`, matching how daemon-only columns are documented while `main.py` (`reporting/state_snapshot.py`) is being retired under shrink step 5.
     - Keep `Forecast_{10,30,60,90}_{Lower,Upper}` **out** of `config.COLUMN_SCHEMA` (same rationale as `Forecast_*_Is_Fallback`).

---

## Proposed Changes

### 1. `pipeline/production_steps.py` — `ForecastingStep` (`forecast_cols` and `_forecast_one` fallback)

- **Append the 8 band columns to `forecast_cols` (`pipeline/production_steps.py` ~lines 518–542):**
  - Keep all existing entries in `forecast_cols` unchanged and append:
    `'Forecast_10_Lower'`, `'Forecast_10_Upper'`, `'Forecast_30_Lower'`, `'Forecast_30_Upper'`, `'Forecast_60_Lower'`, `'Forecast_60_Upper'`, `'Forecast_90_Lower'`, `'Forecast_90_Upper'`.
- **Capture and write finite positive bounds in `_forecast_one`'s `except Exception as ml_err:` fallback branch (`pipeline/production_steps.py` ~lines 578–610):**
  - Keep every existing key and value in the fallback dict (`'Target_Days'`, `'ARIMA'`, `'MC_Target'`, `'MC_Lower'`, `'MC_Upper'`, `'Forecast_10'`, `'Forecast_30'`, `'Forecast_60'`, `'Forecast_90'`, and the four `'Forecast_*_Is_Fallback': True`) **exactly as is** (including `MC_Lower`/`MC_Upper` unconditional).
  - Capture `(mc_10, mc_10_low, mc_10_high)`, `(mc_60, mc_60_low, mc_60_high)`, `(mc_90, mc_90_low, mc_90_high)` alongside `(mc_target, mc_low, mc_high)` and add `Forecast_{h}_Lower` / `Forecast_{h}_Upper` for `h in (10, 30, 60, 90)` **only** when the bound is finite (`math.isfinite(b)`) and `> 0`; otherwise leave the key unset so `_apply_forecast_columns` keeps `NaN`.

### 2. `main_orchestrator.py` & `tests/test_state_snapshot_parity.py` — Persist Bands into `OUTPUT_DIR/state_snapshot.json`

- **`main_orchestrator._write_state_snapshot` (`main_orchestrator.py` ~line 995):**
  - Right below `"forecast_is_fallback": _safe_float_or_none(row.get("Forecast_30_Is_Fallback")),`, emit:
    ```python
    "forecast_10_lower": _safe_float_or_none(row.get("Forecast_10_Lower")),
    "forecast_10_upper": _safe_float_or_none(row.get("Forecast_10_Upper")),
    "forecast_30_lower": _safe_float_or_none(row.get("Forecast_30_Lower")),
    "forecast_30_upper": _safe_float_or_none(row.get("Forecast_30_Upper")),
    "forecast_60_lower": _safe_float_or_none(row.get("Forecast_60_Lower")),
    "forecast_60_upper": _safe_float_or_none(row.get("Forecast_60_Upper")),
    "forecast_90_lower": _safe_float_or_none(row.get("Forecast_90_Lower")),
    "forecast_90_upper": _safe_float_or_none(row.get("Forecast_90_Upper")),
    ```
- **`tests/test_state_snapshot_parity.py` (`ORCHESTRATOR_ONLY_FIELDS`, ~lines 131–143):**
  - Add `"forecast_10_lower"`, `"forecast_10_upper"`, `"forecast_30_lower"`, `"forecast_30_upper"`, `"forecast_60_lower"`, `"forecast_60_upper"`, `"forecast_90_lower"`, `"forecast_90_upper"` to `ORCHESTRATOR_ONLY_FIELDS`.

---

## Verification & Test Plan

1. **Extend `tests/test_production_steps_columns_contract.py`:**
   - Include `Forecast_{10,30,60,90}_{Lower,Upper}` in `FORECAST_COLS` / `CASES` (and add an explicit test exercising `ForecastingStep.run`) proving that present bands are copied onto `dashboard_df` and absent bands stay `NaN` (never `0.0`, CONSTRAINT #4) after `_apply_forecast_columns`.
2. **Extend `tests/test_forecasting_step_fallback_disclosure.py`:**
   - Assert that on the normal path (`_FakeEngineHealthy`), present `Forecast_{10,30,60,90}_{Lower,Upper}` round-trip and omitted ones stay `NaN` (not `0.0`).
   - Assert that on the `_forecast_one` `except`-fallback path (`_FakeEngineBlowsUp`), positive finite `lo`/`hi` from `run_monte_carlo` for `10/30/60/90` are written to `Forecast_{10,30,60,90}_{Lower,Upper}`, while non-positive or non-finite (`<= 0`, `NaN`, `inf`) bounds leave the corresponding `Forecast_{h}_{Lower,Upper}` keys unset (`NaN` on `dashboard_df`, never `0.0`).
3. **Extend `tests/test_state_snapshot_parity.py`:**
   - Verify `ORCHESTRATOR_ONLY_FIELDS` parity contract passes and that `_write_state_snapshot` persists `forecast_{10,30,60,90}_{lower,upper}` as floats when present and `None` (`null`) when `NaN` or absent.
4. **Run targeted and full offline test suites:**
   - `NO_VENV_REEXEC=1 /Users/kevinlee/Stockpy-live/.venv/bin/pytest tests/test_production_steps_columns_contract.py tests/test_forecasting_step_fallback_disclosure.py tests/test_state_snapshot_parity.py tests/test_forecast_rebuild_f2.py tests/test_forecast_rebuild_f3.py tests/test_forecast_parallel.py`
   - `NO_VENV_REEXEC=1 PATH="/Users/kevinlee/Stockpy-live/.venv/bin:$PATH" make ci`

---

## Documentation Updates (Same PR)

1. **`docs/architecture/signal-engines.md`:**
   - Under `forecasting_engine.py`, append a **Forecasting rebuild F4 part 2a — persist per-horizon bands (2026-10, persistence only, no decision change)** note documenting `Forecast_{10,30,60,90}_{Lower,Upper}` in `ForecastingStep` (`forecast_cols` + fallback branch) and `forecast_{10,30,60,90}_{lower,upper}` in `state_snapshot.json`.
2. **`.claude/forecasting_rebuild_implementation_plan.md`:**
   - Update the F4 status line (line 68) to record only that part 2a (persisting per-horizon `Forecast_{h}_Lower/Upper` quantiles) is done; keep the remaining F4 items (sector blend, CNN-LSTM/Prophet pending F3, one code path) unchanged.
3. **`docs/FEATURE_TIER_HISTORY.md`:**
   - Add a dated `### 2026-10-15 Forecasting Rebuild F4 Part 2a` entry at the bottom of the file.
