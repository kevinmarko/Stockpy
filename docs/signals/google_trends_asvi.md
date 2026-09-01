# Feature: Google Trends Stitching & Abnormal Search Volume Index (ASVI)

**File:** `data/trends_stitcher.py` (`GoogleTrendsStitcher`, `ASVICalculator`, `FMPDataLoader`)
**Related Files:** `data/attention_sources.py`, `data/sector_selection_heat.py`, `data/fmp_client.py`
**Research Grounding:** Da, Engelberg & Gao (2011), "In Search of Attention," *Journal of Finance* 66(5): 1461-1499.

---

## 1. Overview & Architecture

This module implements the end-to-end data ingestion, overlapping stitching, and abnormal attention signal transformation pipeline for search volume indicators and financial market series.

**Configuration Settings (settings.py):**
- `GOOGLE_TRENDS_ENABLED`: Master switch for fetching.
- `GOOGLE_TRENDS_WINDOW_DAYS`: Size of fetch window in days (default: 90).
- `GOOGLE_TRENDS_OVERLAP_DAYS`: Overlap between windows for splicing (default: 30).
- `GOOGLE_TRENDS_MIN_REQUEST_INTERVAL_SECONDS`: Shared limiter delay between calls.
- `GOOGLE_TRENDS_MAX_RETRIES`: Number of retries on HTTP 429/5xx.
- `GOOGLE_TRENDS_COOLDOWN_THRESHOLD`: Failures required to enter cooldown.
- `GOOGLE_TRENDS_COOLDOWN_SECONDS`: Duration of cooldown lock-out.


```
┌───────────────────────────┐      ┌──────────────────────────┐
│ Google Trends API / Feeds │      │ Financial Modeling Prep  │
│ (Raw 90-day daily SVI)    │      │ (Daily OHLCV Bars)       │
└─────────────┬─────────────┘      └────────────┬─────────────┘
              │                                 │
              ▼                                 ▼
┌───────────────────────────┐      ┌──────────────────────────┐
│   GoogleTrendsStitcher    │      │      FMPDataLoader       │
│ (Scaling Factor Alignment)│      │  (EMA, MACD, RSI-14)     │
└─────────────┬─────────────┘      └────────────┬─────────────┘
              │                                 │
              ▼                                 │
┌───────────────────────────┐                   │
│      ASVICalculator       │                   │
│   (Causal Log-Median)     │                   │
└─────────────┬─────────────┘                   │
              │                                 │
              ▼                                 ▼
       [ ASVI Attention ]               [ Technical State ]
              │                                 │
              └───────────────┬─────────────────┘
                              ▼
                ┌───────────────────────────┐
                │ Sequence Input Tensor X_t │
                │ (Econometric / ML Models) │
                └───────────────────────────┘
```

---

## 2. Mathematical Formulation

### A. Overlapping Window Stitching Algorithm (`GoogleTrendsStitcher`)
Google Trends provides daily resolution data in 90-day intervals, with each interval internally normalized to $\max(SVI) = 100$. To reconstruct a continuous, multi-year daily time series without artificial step-discontinuities at window boundaries, an overlapping window stitching algorithm is applied.

Given two adjacent periods $A$ (earlier) and $B$ (subsequent) with non-empty intersection $O = A \cap B$:
1. Compute the scaling factor $f$:
   Let $S_A = \sum_{t \in O} SVI_{A, t}$ and $S_B = \sum_{t \in O} SVI_{B, t}$.
   If $S_A \le 10^{-9}$ and $S_B \le 10^{-9}$, $f = 1.0$.
   Otherwise, $f = \frac{\max(S_A, 0.1)}{\max(S_B, 0.1)}$.
2. Rescale period $B$:
   $$SVI_{B, \text{scaled}, t} = SVI_{B, t} \times f$$
3. Blend the overlapping boundary smoothly:
   $$SVI_{\text{stitched}, t} = \begin{cases} 
   SVI_{A, t} & t \in A \setminus O \\
   \frac{SVI_{A, t} + SVI_{B, \text{scaled}, t}}{2} & t \in O \\
   SVI_{B, \text{scaled}, t} & t \in B \setminus O
   \end{cases}$$

### B. Abnormal Search Volume Index (`ASVICalculator`)
Following Da, Engelberg & Gao (2011), investor attention shocks are isolated by comparing today's search volume against the historical baseline median:
$$ASVI_t = \ln(SVI_t) - \ln\left(\text{Median}(SVI_{t-k \dots t-1})\right)$$

**Lookahead-Free Causality**:
The rolling median strictly operates on $SVI$ shifted by 1 day ($t-1$), ensuring information from date $t$ never enters the baseline calculation.

### C. Technical Indicators (`FMPDataLoader`)
Computes normalized input features for sequence modeling:
- **Exponential Moving Averages**:
  $$\text{EMA}_{12, t} = \alpha_{12} P_t + (1 - \alpha_{12}) \text{EMA}_{12, t-1}, \quad \alpha = \frac{2}{N+1}$$
  $$\text{EMA}_{26, t} = \alpha_{26} P_t + (1 - \alpha_{26}) \text{EMA}_{26, t-1}$$
- **Moving Average Convergence Divergence**:
  $$\text{MACD}_t = \text{EMA}_{12, t} - \text{EMA}_{26, t}$$
  $$\text{Signal}_t = \text{EMA}_9(\text{MACD}_t)$$
  $$\text{Hist}_t = \text{MACD}_t - \text{Signal}_t$$
- **Relative Strength Index (14-day)**:
  $$\text{RS} = \frac{\text{SMA}_{14}(\text{Gain})}{\text{SMA}_{14}(\text{Loss}) + \epsilon}$$
  $$\text{RSI}_{14} = 100 - \frac{100}{1 + \text{RS}}$$

---

## 3. Testing & Verification

Unit tests are implemented in `tests/test_trends_stitcher.py`:
- `TestGoogleTrendsStitcher`: Verifies exact rescaling recovery across overlapping windows and multi-window chaining.
- `TestASVICalculator`: Verifies log-median reference output and runs perturbation testing to prove zero lookahead bias.
- `TestFMPDataLoader`: Verifies indicator computation accuracy and bounds.

### D. Visualizations
- **Trends Stitching Demo:** A dedicated visualization screen in the Pilots PWA (`/research/trends-stitcher`) demonstrates the overlapping window stitching algorithm. It plots the raw unscaled SVI curves alongside the final stitched continuous sequence. Live SVI fetching is enabled for this demo and is wired directly to the real Google Trends API via `pytrends`, pulling authentic overlapping periods for stitching and validation.

---

## 4. LSTM-Attention Forecaster (Phase 5)

An LSTM-Attention model utilizes the generated ASVI and technicals to forecast 1-day percentage returns.
The model uses a 15-feature sliding window (sequence length 15):
1. Target ASVI (from Google Trends)
2. Sector-Proxy ASVI (from Google Trends via SECTOR_TO_ETF mapping)
3-7. OHLCV
8-15. Technicals (RSI_14, EMA_12, EMA_26, MACD, MACD_Signal, MACD_Hist, Realized_Vol_60D, SMA_50)

The model leverages `cnn_lstm_worker.py` in a separate process to avoid TensorFlow threading deadlocks in the orchestrator.
Currently, due to rate limiting constraints preventing a long-horizon backtest on Google Trends, the model is set to `deployable=False` (weight = 0.0) in the registry, and its validation adapter returns `None`.
