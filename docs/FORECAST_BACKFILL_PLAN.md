# Agentic Trading: Multi-Horizon Forecast Backfill & Meta-Labeling Guide

## Overview

The Multi-Horizon Forecast Backfill and Meta-Labeling engine (`ml/forecast_backfill.py`) implements Marcos Lopez de Prado's Meta-Labeling methodology across **10, 30, 60, and 90-day horizons** for primary momentum models:

1. **Time-Series Momentum (TSMOM)**
2. **Cross-Sectional Momentum (CSMOM)**

The pipeline enables the Agentic Trading platform to train confidence classifiers that evaluate market environment conditions (volatility, RSI, MACD, volume ratio) to output $P(\text{success})$ confidence probabilities for primary signals, providing quantitative guardrails and sizing filters.

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
- `GET /pilots/forecast_backfill`: Returns backfill status, trained 8-model metrics (Accuracy, ROC-AUC, sample counts), and metadata.
- `POST /pilots/forecast_backfill/run`: Triggers an on-demand forecast backfill cycle.

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
