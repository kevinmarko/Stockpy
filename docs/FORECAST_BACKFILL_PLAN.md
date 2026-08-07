# Agentic Trading: Multi-Horizon Forecast Backfill & Meta-Labeling Guide

## Overview

The Multi-Horizon Forecast Backfill and Meta-Labeling engine (`ml/backfill/GlobalBackfillEngine.py`) implements Marcos Lopez de Prado's Meta-Labeling methodology across **10, 30, 60, and 90-day horizons** for primary momentum models:

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

---

## API Endpoints & Web App UI

### REST API Endpoints (`api/pilots_api.py`)
- `GET /api/backfill/status/{job_id}`: Returns backfill polling status, trained model metrics (Accuracy, ROC-AUC, sample counts), and metadata.
- `POST /api/backfill/run`: Triggers an on-demand async global backfill cycle.

### Web App (Pilots PWA)
- **Screen**: `<ForecastBackfillScreen />` (`webapp/src/screens/ForecastBackfillScreen.tsx`)
- **Route**: `/forecast/backfill`
- Displays pipeline status, trained model performance table, data sourcing badges (FMP), and on-demand trigger control.

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
pytest tests/test_forecast_backfill.py -v
```
