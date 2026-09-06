# Forecast Itô Double Correction and Horizon Units

## 1. Issue Summary
This document covers the resolution of three related math and horizon unit issues in the forecasting pipeline (F1, F4, F5).
- **F1 (Monte Carlo Itô Double Correction):** `run_monte_carlo` previously applied an Itô drift term `(μ − 0.5σ²)` to the simulated paths. Because `μ` (expected log-return) already incorporates the `−0.5σ²` drag relative to simple returns, subtracting it again resulted in a systematic downward bias (double correction) that artificially depressed long-horizon Monte Carlo bands.
- **F4 & F5 (Horizon Units & DB Repair):** The actualization of forecasts in `ForecastTracker` was evaluated against calendar days instead of trading days, and `make_future_dataframe` used calendar days rather than business days. This led to misalignment between forecast horizons (e.g., 30 days) and the actual data used for scoring and blending. 

## 2. Remediation
- **F1:** Removed the `(μ − 0.5σ²)` term from `run_monte_carlo` in `forecasting_engine.py`. The simulation now correctly scales with `μ * dt`.
- **F4 & F5:** `update_actuals` was fixed to score against the correct trading bars close. A historic repair script (`scripts/repair_forecast_errors_horizon.py`) was introduced to amend the `forecast_errors` database table without discarding past records.

## 3. Numeric Impact
### DB Repair Record
Live database state (`~/.stockpy_local/quant_platform.db`, read-only inspection):
`forecast_errors` holds 2,362,035 rows, 379,606 actualized, spanning 2026-07-10 → 2026-09-06.
h=60 and h=90 have zero actualized rows across every model, so those horizons have never had skill data at all.

### Bug Specifics
| # | Finding | Evidence |
|---|---|---|
| **F1** | **Itô correction applied to a drift that already contains it.** `mu = log_returns.mean()` (`forecasting_engine.py:1397-1398`) is already a log drift; `daily_drift = (mu - 0.5*sigma**2)*dt` (`:187`) subtracts ½σ² a second time. | Verified with 400k paths: every output (`MC_Target`, `MC_Lower`, `MC_Upper`) shifted down by exactly `exp(−½σ_d²T) − 1`. |
| **F4** | **Purged train/val split leaks exactly `max_h` rows.** `embargo = max(0, lookback - 1)` (`:571`) purges input overlap only; training labels reach `end−1+max_h`. | With lookback=60, max_h=90, n=1000: last training label is row **889**, first validation *input* is row **800** — 90 rows of overlap. Correct embargo is `lookback − 1 + max_h = 149`. |
| **F5** | **Tracker horizon is calendar days; forecasts are trading bars.** `cutoff_dt = as_of - timedelta(days=horizon_days)` (`forecast_tracker.py:369`) vs `run_monte_carlo`'s `dt = 1 trading day`, `ARIMA.forecast(steps=h)`, CNN-LSTM target `close[last+h]`. A 30-bar forecast is scored after 30 calendar days ≈ 21 bars — **30% short**. | `docs/known_issues/forecast_tracker_early_actualization.md` fixed a different bug (a `tolerance_days` subtraction) and never mentions calendar-vs-trading days. |
