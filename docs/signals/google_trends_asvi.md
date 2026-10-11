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
- `GOOGLE_TRENDS_MAX_TERMS_PER_PASS`: Most terms the daemon downloads in one refresh pass, stalest first (default: 10).
- `GOOGLE_TRENDS_RAW_RETENTION_BATCHES`: Complete download batches kept per term in `raw_trends_downloads` (default: 14).

**Daemon refresh (`maybe_refresh_google_trends`), since 2026-10:**
- **Universe.** The tracked universe via `data/portfolio_sync.py::compute_tracked_universe()` (held ∪ watchlist ∪ discovered, else `DEFAULT_TICKERS`), using only the cached Robinhood snapshot. It used to be `DEFAULT_TICKERS` alone.
- **Schedule.** A term is due when its last download *attempt* (complete, partial or failed) is older than `GOOGLE_TRENDS_REFRESH_INTERVAL_HOURS`, so a term Google has no data for is not retried on every wake. The schedule is read from the `trends_download_batches` table, so it survives a daemon restart. Each pass takes at most `GOOGLE_TRENDS_MAX_TERMS_PER_PASS` due terms, never-attempted first, then oldest attempt first (round-robin), so a large universe is covered across several timer wakes.
- **Batches.** The windows of one download share a `batch_id` and are recorded in `trends_download_batches` with `status` `complete` (every planned window returned), `partial` or `failed`, plus a `reason`. Raw rows carry `window_id = "<batch_id>/<window_start>/<window_end>"`.
- **Only complete batches are stitched.** The new stitch *replaces* the term's stored series in one transaction (`save_stitched_series(..., replace=True)`), so no days from an older stitch, on an older scale, are left behind. A partial or failed batch leaves the stored series unchanged.
- **Partial current day dropped.** `fetch_overlapping_windows` drops rows pytrends flags `isPartial`. The current day's value climbs while the day is in progress, so the dashboard ASVI (the last stored day) is now computed from the last *complete* day.
- **Rate-limit cooldown stops the pass.** The terms not reached stay due for a later wake.
- **Retention.** After every batch, raw rows beyond the newest `GOOGLE_TRENDS_RAW_RETENTION_BATCHES` batches of each kind (complete; partial/failed) are pruned. Pre-2026-10 rows with bare `uuid4` window ids go once the term has a complete batch.
- Write-up: [`docs/known_issues/google_trends_stale_scale_and_partial_day_2026_10.md`](../known_issues/google_trends_stale_scale_and_partial_day_2026_10.md).


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

**Implementation note:** step 1's scaling-factor computation (the ratio $f = \sum_{t \in O} SVI_{A,t} / \sum_{t \in O} SVI_{B,t}$ over the overlap window, including the $f = 1.0$ degenerate-denominator guard when $\sum_{t \in O} SVI_{B,t} \le 10^{-9}$) is implemented as its own public static method, `GoogleTrendsStitcher.get_scaling_metadata(period_a_svi, period_b_svi) -> dict`, extracted out of `stitch_intervals`. `stitch_intervals` now delegates to it internally rather than re-deriving the ratio itself, so it remains the single source of truth for the overlap-window/scaling-factor computation — any caller needing just the scaling factor and overlap-window details (e.g. a diagnostics or visualization endpoint) can call `get_scaling_metadata` directly without running the full stitch.

### B. Abnormal Search Volume Index (`ASVICalculator`)
Following Da, Engelberg & Gao (2011), investor attention shocks are isolated by comparing today's search volume against the historical baseline median:
$$ASVI_t = \ln(SVI_t) - \ln\left(\text{Median}(SVI_{t-k \dots t-1})\right)$$

**Lookahead-Free Causality**:
The rolling median strictly operates on $SVI$ shifted by 1 day ($t-1$), ensuring information from date $t$ never enters the baseline calculation.

**Warm-up rows are NaN.** The first 7 rows have no prior data to form a baseline, so their ASVI is NaN. Until 2026-10 they were filled with the median of the first 84 days, which includes dates after $t$ (a lookahead). `tests/test_trends_stitcher.py::test_asvi_no_lookahead_in_warmup_perturbation` pins this with cutoffs inside the first lookback window. The dashboard takes the last valid value, so it is unaffected unless a term has 7 or fewer stored days, in which case it now shows NaN.

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
- **Search Attention viewer (live, 2026-10):** the Pilots PWA screen `/research/trends` (`webapp/src/screens/TrendsAttention.tsx`) shows, per term, the raw downloaded windows, a seams table (overlap sums, applied factor, pair factor, degenerate-guard flag), the rescaled windows with the stitched series, the ASVI line, and an honesty strip (latest attempt, age, staleness, recompute check). It is served by two read-only endpoints on the data API (port 8603): `GET /data/trends/terms` (every term with data, plus tracked-universe coverage) and `GET /data/trends/svi?term=` (the full per-term view; a valid term with no data returns 200 with `status: "no_data"`, never 404). Both build their payload in `data/trends_view.py`, which reads `TrendsStore` only. **Neither endpoint makes a network call:** no pytrends, no Google, no FMP. The viewer displays what the daemon already saved and recomputes the stitch from the saved raw windows to compare it with the stored series. See [HOW_TO_GUIDE.md](../HOW_TO_GUIDE.md) ("Reading the Search Attention screen") for the operator's reading guide.
- **Sampling noise:** Google answers each request from a random sample of its logs, so repeated downloads of the same day differ by a few points. The live DB showed 78 / 79 / 80 / 81 for one complete day (AAL, 2026-10-03) across four downloads. This is inherent to the data source, not a stitching bug; do not read single-point differences between downloads as signal.
- *(The earlier demo, `/research/trends-stitcher` with `GET /data/trends/stitch-demo` and `webapp/src/utils/trendsStitch.ts`, was removed 2026-10 and is in git history. `data/trends_stitcher.py` is used by both the ASVI pipeline and the live viewer.)*
