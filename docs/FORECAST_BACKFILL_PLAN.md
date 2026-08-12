# Agentic Trading: Multi-Horizon Forecast Backfill & Meta-Labeling Guide

## Overview

The Multi-Horizon Forecast Backfill and Meta-Labeling engine (`ml/forecast_backfill.py`) implements Marcos Lopez de Prado's Meta-Labeling methodology across **10, 30, 60, and 90-day horizons** for primary momentum models:

1. **Time-Series Momentum (TSMOM)**
2. **Cross-Sectional Momentum (CSMOM)**

The pipeline trains confidence classifiers that evaluate market environment conditions (volatility, RSI, MACD, volume ratio) to output out-of-sample $P(\text{success})$ probabilities for primary signals, per horizon.

**Current scope: standalone research/backfill diagnostic, not wired to live trading.** The trained
per-horizon models (`ml/models/meta_{TSMOM,CSMOM}_{10,30,60,90}d.pkl`) are plain pickled
classifiers, not `ml.meta_labeling.MetaLabeler` instances, and are **not** registered into
`ml.meta_labeling.global_meta_registry` — `SignalAggregator`/`StrategyEngine`'s live position
sizing and confidence gating are unaffected by this engine today. `ml/meta_bootstrap.py` documents
why (file-naming convention, pickled type, and signal-id keying are all incompatible with the
existing single-model-per-`SignalModule` gate). Wiring multi-horizon confidence into live sizing
is a real, separate design decision (which horizon should gate the existing `timeseries_momentum`/
`cross_sectional_momentum` signals, or a new aggregation across horizons) that hasn't been made —
this PR ships the backfill/training/reporting engine and its API + UI, not that wiring.

---

## Technical Features & Signal Formulations

### Primary Signals
- **TSMOM**: 252-day absolute return sign ($+1$ if $R_{252} > 0$, else $-1$).
- **CSMOM**: 252-day cross-sectional percentile rank across the stock universe ($+1$ if percentile rank $> 0.5$, else $-1$).

### Contextual Features
- **Vol_20 & Vol_50**: 20-day and 50-day rolling standard deviations of daily returns, annualized by $\sqrt{252}$.
- **RSI_14**: 14-day Relative Strength Index.
- **MACD**: Moving Average Convergence Divergence ($EMA_{12} - EMA_{26}$).
- **Vol_Ratio**: Ratio of daily volume to 20-day moving average volume.

### Meta-Target Formulation
For horizon $h \in \{10, 30, 60, 90\}$ days:
- Actual forward return: $R_{t, t+h} = \frac{P_{t+h}}{P_t} - 1$.
- Meta-Label target ($y$): $1$ if $\text{sign}(\text{Primary Signal}) == \text{sign}(R_{t, t+h})$, else $0$.

---

## Performance, Timeout, and Partial-Result Checkpointing

A full-universe run (~515 tickers × 4 horizons) is dominated by two CPU-bound stages —
step 3's per-date/per-ticker cross-sectional signal replay and step 5's per-(model,
horizon) meta-labeler training — and can genuinely take well over 30 minutes. Three
complementary fixes address this:

**Step 3 vectorization.** `_run_cross_sectional_module` (`ml/forecast_backfill.py`) first
tries a module's optional `SignalModule.compute_batch_xsec(ranks_wide)` hook
(`signals/base.py`, default `None`) before falling back to the original per-date
`pre_compute()` + per-ticker `compute()` replay. `CrossSectionalMomentumSignal`
(`signals/cross_sectional_momentum.py`) implements it as `2.0 * (ranks_wide - 0.5)` — the
same formula `pre_compute`/`compute` already use, verified numerically identical
(`test_cross_sectional_fast_path_matches_slow_path_parity`) — computed once, vectorized,
over the entire historical panel instead of once per (date, ticker). `SignalModule`
subclasses that don't implement the hook keep using the original, correctness-preserving
loop. Separately, `step_3_generate_primary_signals` now skips any registered module with
no `meta_label_features` before running it at all, since such a module can never be
trained or exported by this engine regardless of what step 3 computes for it.

**Step 5/6 checkpointing.** Training (step 5) and inference (step 6) are interleaved
per (model_type, horizon) combo — `_infer_one()` runs immediately after each combo's
classifier is trained and its `.pkl` is saved, instead of waiting for every combo across
every strategy to finish first. After each combo, `_write_partial_export()` writes a
running snapshot to `output/agentic_forecast_backfill.partial.csv` /
`output/agentic_forecast_summary.partial.json` — **separate files** from the canonical
`agentic_forecast_backfill.csv` / `agentic_forecast_summary.json`, which remain
exclusively the product of a genuinely *completed* run via `export_results()`. The
worker process (`ml/forecast_backfill_worker.py`) also emits a `{"event": "progress", ...}`
NDJSON event after each combo; the parent job (`ml/forecast_backfill_job.py`) records this
onto `BackfillJobState.partial_summary` (`{"trained": [...], "metrics_so_far": {...}}`),
included in `serialize_job()`'s payload and therefore in every
`GET /pilots/forecast_backfill/status/{job_id}` poll response. `_enforce_deadline` never
touches this field, so whichever combo last completed before a timeout SIGKILLs the worker
is preserved. The Pilots PWA reads it: `backfillFailureMessage()`
(`webapp/src/forecastBackfillCopy.ts`) reports *"The backfill timed out after training N
models — partial results were saved"* instead of the old unconditional "Nothing was
saved" whenever `partial_summary.trained` is non-empty, and `ForecastBackfillScreen.tsx`
renders a "Partial Results Saved Before Timeout" table listing the checkpointed models.

**Deadline.** `settings.FORECAST_BACKFILL_DEADLINE_SECONDS` (see table below) is a hard
wall-clock backstop independent of the above — it still exists even after the
vectorization/checkpointing fixes land, and was raised from its prior 1800s (30 min)
default because 30 minutes was measured to be genuinely too short for a full-universe run
even before accounting for the perf work above.

---

## Zero Hardcoded Configuration Reference

All hyperparameters are centralized in `settings.py` and configurable via `.env`:

| Setting | Default | Description |
|---------|---------|-------------|
| `FORECAST_BACKFILL_HORIZONS` | `[10, 30, 60, 90]` | List of forecast horizons in days. |
| `FORECAST_BACKFILL_LOOKBACK_YEARS` | `4` | Default backfill window in years, used when no explicit `start_date` is given (e.g. the webapp's "Run Forecast Backfill" button always omits it). Computed relative to `end_date` at run time — rolls forward on every re-run rather than growing unbounded. |
| `FORECAST_BACKFILL_MOMENTUM_WINDOW` | `252` | Lookback window in trading days for primary TSMOM/CSMOM signals. |
| `FORECAST_BACKFILL_VOL_SHORT_WINDOW` | `20` | Short rolling volatility lookback window in days. |
| `FORECAST_BACKFILL_VOL_LONG_WINDOW` | `50` | Long rolling volatility lookback window in days. |
| `FORECAST_BACKFILL_RSI_WINDOW` | `14` | RSI calculation window in days. |
| `FORECAST_BACKFILL_MACD_FAST` | `12` | MACD fast EMA span. |
| `FORECAST_BACKFILL_MACD_SLOW` | `26` | MACD slow EMA span. |
| `FORECAST_BACKFILL_VOL_RATIO_WINDOW` | `20` | Volume ratio moving average window in days. |
| `FORECAST_BACKFILL_TRAIN_SPLIT` | `0.80` | Chronological train/test split fraction (no lookahead bias). |
| `FORECAST_BACKFILL_N_ESTIMATORS` | `100` | Tree count for RandomForest / LightGBM classifier. |
| `FORECAST_BACKFILL_MAX_DEPTH` | `5` | Maximum tree depth for classifier. |
| `FORECAST_BACKFILL_RANDOM_STATE` | `42` | Random seed for reproducibility. |
| `FORECAST_BACKFILL_CLASSIFIER_TYPE` | `"random_forest"` | Algorithm (`"random_forest"` or `"lightgbm"`). |
| `FORECAST_BACKFILL_DEADLINE_SECONDS` | `5400` (90 min) | Hard wall-clock deadline for one run, from worker start to a terminal result — the process group is SIGKILLed if it hasn't produced a result by then. Raised from a prior `1800` (30 min) default, which was too short for a full ~500-ticker operator universe. GUI-writable. |

---

## API Endpoints & Web App UI

### REST API Endpoints (`api/pilots_api.py`)
- `GET /pilots/forecast_backfill`: Returns backfill status, trained 8-model metrics (Accuracy, ROC-AUC, sample counts), and metadata — always the last *completed* run (never in-progress/partial data).
- `POST /pilots/forecast_backfill/run`: Triggers an on-demand, asynchronous forecast backfill cycle (202 + job id; runs in an isolated subprocess).
- `GET /pilots/forecast_backfill/status/{job_id}`: Polled every 2s by the webapp. Includes `phase`/`step`/`state` and, once at least one (model, horizon) combo has finished training, `partial_summary: {"trained": [...], "metrics_so_far": {...}} | null` — survives into a `state: "timeout"` response so a killed run still reports what it managed to checkpoint.
- `POST /pilots/forecast_backfill/cancel/{job_id}`: Cancels an in-flight run.

### Web App (Pilots PWA)
- **Screen**: `<ForecastBackfillScreen />` (`webapp/src/screens/ForecastBackfillScreen.tsx`)
- **Route**: `/forecast/backfill`
- Displays pipeline status, trained model performance table, data sourcing badges (FMP), and on-demand trigger control.
- On a timed-out run with a non-empty `partial_summary`, renders a "Partial Results Saved Before Timeout" table listing the checkpointed (model, horizon) combos and their metrics, and the failure banner reads *"The backfill timed out after training N models — partial results were saved"* instead of an unconditional "Nothing was saved" (`webapp/src/forecastBackfillCopy.ts::backfillFailureMessage`).

---

## CLI Usage

Run a backfill cycle directly from the command line:

```bash
python scripts/run_forecast_backfill.py --use-fmp
python scripts/run_forecast_backfill.py --tickers AAPL,MSFT,NVDA,JPM --horizons 10,30,60,90
```

---

## Automated Tests

Run the unit test suite:

```bash
pytest tests/test_forecast_backfill.py tests/test_forecast_backfill_job.py -v
```

Coverage added for the performance/checkpointing work above:
- `test_step_3_skips_cross_sectional_modules_with_no_meta_label_features` — confirms only
  `cross_sectional_momentum` reaches the expensive per-date replay path.
- `test_cross_sectional_fast_path_matches_slow_path_parity` — proves `compute_batch_xsec`'s
  vectorized output is numerically identical (within this codebase's 1e-5 drift convention)
  to the original per-row loop's output.
- `test_cross_sectional_fast_path_is_lookahead_free` — the standard perturbation test,
  re-verified against the new fast path specifically.
- `test_kill_mid_step_5_leaves_partial_export_with_completed_combos` /
  `test_kill_before_any_combo_finishes_produces_no_partial_files` — SIGKILL a real child
  subprocess mid-run and inspect the filesystem, proving the partial CSV/JSON checkpoint
  survives a genuine hard kill (not just a clean early return), and that a kill before any
  combo finishes still honestly produces no partial files.
- `tests/test_forecast_backfill_job.py::TestDeadlineEnforcement` — extended to assert
  `partial_summary` survives into the serialized `"timeout"` state.
- Webapp: `webapp/src/forecastBackfillCopy.test.ts` (new — full branch coverage for
  `backfillFailureMessage`, including the singular/plural and honest-empty cases),
  `webapp/src/screens/ForecastBackfillScreen.test.tsx` (partial-results table
  shown/not-shown), `webapp/src/api/mock.test.ts` (end-to-end
  `stockpy.mock.forecast_backfill_timeout` marker + a shape-parity assertion against
  `ForecastBackfillModelMetrics`).
