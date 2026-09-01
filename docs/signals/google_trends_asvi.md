# Feature: Google Trends Stitching & Abnormal Search Volume Index (ASVI)

**File:** `data/trends_stitcher.py` (`GoogleTrendsStitcher`, `ASVICalculator`, `FMPDataLoader`)
**Related Files:** `data/attention_sources.py`, `data/sector_selection_heat.py`, `data/fmp_client.py`, `data/google_trends_client.py` (live `pytrends` fetcher), `data/trends_store.py` (SQLite persistence), `desktop/daemon_runtime.py::maybe_refresh_google_trends` (daemon scheduling), `pipeline/production_steps.py` (`Google_Trends_ASVI` diagnostic dashboard column)
**Research Grounding:** Da, Engelberg & Gao (2011), "In Search of Attention," *Journal of Finance* 66(5): 1461-1499.

---

## 1. Overview & Architecture

This module implements the end-to-end data ingestion, overlapping stitching, and abnormal attention signal transformation pipeline for search volume indicators and financial market series.

**Configuration Settings (settings.py):**
- `GOOGLE_TRENDS_ENABLED`: Master switch for fetching (default `False` — opt-in).
- `GOOGLE_TRENDS_WINDOW_DAYS`: Size of fetch window in days (default: 90).
- `GOOGLE_TRENDS_OVERLAP_DAYS`: Overlap between windows for splicing (default: 30).
- `GOOGLE_TRENDS_REFRESH_INTERVAL_HOURS`: Daemon fetch cadence for `maybe_refresh_google_trends` (default: 24).
- `GOOGLE_TRENDS_MIN_REQUEST_INTERVAL_SECONDS`: In-process limiter delay between calls.
- `GOOGLE_TRENDS_MAX_RETRIES`: Number of retries on HTTP 429/5xx.
- `GOOGLE_TRENDS_COOLDOWN_THRESHOLD`: Failures required to enter cooldown.
- `GOOGLE_TRENDS_COOLDOWN_SECONDS`: Duration of cooldown lock-out.
- `GOOGLE_TRENDS_MAX_SECONDS_PER_CYCLE`: Wall-clock budget for the per-symbol pipeline dashboard-column loop (default: 120).


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
   If $S_A \le 10^{-9}$ **or** $S_B \le 10^{-9}$ (either side's overlap sum is near-zero), $f = 1.0$ (passthrough).
   Otherwise, $f = \frac{S_A}{S_B}$.

   The guard is deliberately symmetric across both operands rather than only flooring the
   denominator: flooring only $S_B$ against a real, unfloored $S_A$ (an earlier formulation of
   this guard) let a genuine, non-zero $S_B$ get rescaled by a multi-thousand-times factor
   whenever $S_A$ happened to be near-zero — the exact case this symmetric guard now excludes.
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
- **Trends Stitching Demo:** A dedicated visualization screen in the Pilots PWA (`/research/trends-stitcher`) demonstrates the overlapping window stitching algorithm via `GoogleTrendsStitcher.stitch_intervals`. Live mode (`GET /data/trends/stitch-demo`) serves real SPY trading volume as an explicitly disclosed proxy for genuine Search Volume Index data — never presented as real SVI — failing closed with `HTTP 503` on any fetch failure; mock mode exercises a client-side TypeScript port of the same algorithm. **This demo screen is independent of, and not yet wired to, the real Google Trends ingestion pipeline described below.** That pipeline (`data/google_trends_client.py` + `data/trends_store.py`) fetches genuine SVI data via `pytrends` only when `GOOGLE_TRENDS_ENABLED=True` (default `False`), on a daemon-scheduled cadence (`GOOGLE_TRENDS_REFRESH_INTERVAL_HOURS`, default 24h) restricted to `settings.DEFAULT_TICKERS`, persisting to `TrendsStore` for consumption by the diagnostic `Google_Trends_ASVI` dashboard column — it has no dedicated UI visualization of its own yet.
