# Forecasting rebuild: implementation plan (for review, no code yet)

## Why
Forecasts are how the platform picks prices. Today:
- The number that drives decisions, the blended `Forecast_30`, is **never recorded or scored**.
- Every component model scores about the same as "price stays flat" (naive):
  - ARIMA, Holt-Winters and Monte Carlo beat naive on ~50% of 30-day forecasts.
  - Prophet does so on 36%, CNN-LSTM on 28%.
  - Yet Prophet and CNN-LSTM together carry ~40% of the 30-day blend weight.

Where the forecast reaches decisions (audit, 2026-09-27):
- `forecast_alignment` signal: ±10 points on a 50-based score. It can flip HOLD↔BUY. A *missing* forecast scores −10 (bearish).
- Advisory: ±3% on `Forecast_30` drives Case A (a loss plus a bearish forecast forces a full-exit SELL), Case C (BUY downgraded to HOLD) and a conviction boost.
- sellRange take-profit: `max(price + 3·ATR, Forecast_30)`.
- Not used by: Kelly sizing, execution limit prices, buyRange.

Problems found:
- BERT-LLA is enabled but has never run (torch is missing, the window needs 920 bars against ~400 available, and a coverage gate blocks it).
- ARIMA and CNN-LSTM blow up on sub-$1 symbols (SINX ratio 1.3e15). There is no clamp.
- Prophet's "30" means 30 *calendar* days; it is blended and scored as 30 trading days.
- On 2026-08-14 Monte Carlo was seeded at ~$100 for symbols trading at ~$20; that affects 29 symbols. Not yet root-caused.
- Hourly cycles write ~20 correlated rows per symbol per hour. That is 2.4M rows and a 796 MB DB, and skill weights "mature" after a day or two of one market move.
- The Forecast Viewer refits every model per request, so it shows a different number than the pipeline. The UI skill window is 60 days; the live blend uses 365.
- 60/90-day horizons: 0 matured rows. The first land in early October and mid-November.

## Goal
One forecasting service that:
1. publishes a small set of models that each earn their place against naive, measured continuously;
2. scores exactly what it publishes;
3. feeds decisions in units of its own measured error;
4. has one home in the app.

## Phases (one PR each; each is merged before the next)

### F1: Measure what we publish (no decision change)
- Record the published blend as its own model, `blend`, at every horizon. Record `naive` at every horizon (today it only has 10-day rows).
- **One row per symbol × model × horizon × trading day.** An upsert on that key means hourly cycles overwrite rather than append. That removes the fake maturity.
- Score on **log error** (|ln(forecast / actual)|), not squared dollars, so one bad row can't dominate a weight. Exclude sub-$1 symbols from all scoring.
- Skill report per model × horizon:
  - median |log error| vs naive
  - % beating naive
  - direction hit rate
  - MC/quantile coverage
  - n
  - a simple significance flag (sign test)

  It is exposed on the existing `/symbols/{t}/forecast` and observability skill sections, and uses the same window as the live blend.
- Ledger cleanup script. **Dry-run only; you run the delete**:
  - `TEST` symbol rows
  - the 2026-08-14 $100-seed rows
  - duplicate intra-day rows (collapse to the last of each day)

  A backup is taken first.
- Tests: tracker upsert, the log-error scorer, the naive-comparison math on a synthetic ground truth.

### F2: Safety guards (small, disclosed decision change)
- **Clamp** any model whose implied log-return exceeds k·σ_GARCH·√h (k = 4). This happens at the single point where model outputs enter the blend. A clamped model is dropped for that symbol and cycle (logged), never fabricated.
- **Input-price check.** If a model's starting price differs from the last close by more than 5%, drop that model for the cycle. This catches the 08-14 class of bug. The root cause is chased separately.
- Prophet: `periods` in trading days (use the `freq="B"` business calendar).
- `forecast_alignment`: a missing or fallback forecast scores **0 (neutral)**, not −10. This is the one intentional score change here. Before merging, I diff one cycle's actions to show which symbols move.

### F3: Naive gate in the blend (shadow first, then switch)
- **Gate rule:** a model enters the blend for a horizon only if it beats naive on rolling horizon-matched median |log error| by ≥ 0.5% relative, with n ≥ 60 daily scored observations (after F1's dedup). Weights among admitted models are inverse log-error.
- If the gate admits nothing, **publish naive (the last close)** and say so in the column/metadata, instead of a blend of losers.
- **Shadow mode:** for 2 weeks the gated blend is recorded as `gated_blend` next to the live `blend`, without driving decisions. Then I show you the side-by-side skill and flip `FORECAST_NAIVE_GATE_ENABLED` on. Default off → on only after your review of the shadow numbers.

### F4: Streamline (archive what the gate or the data rules out)
- Archive to `legacy/`:
  - BERT-LLA (+ its settings)
  - the LSTM-attention endpoint
  - the sector static-preference blend
  - `SECTOR_FORECAST_CONFIGS` and `FORECAST_DRIFT_SHRINKAGE`
  - the stray `forecasting/sector_configs 2.json`
- CNN-LSTM and Prophet: archived **if** they fail the F3 shadow gate, which the current data says they will. That also removes the TensorFlow subprocess pool and the retrain cost.
- **One code path:**
  - `/metrics/forecast` and the advisory path read the pipeline's persisted forecast instead of refitting.
  - `main.py`'s own forecasting goes away with `main.py` in step 5.
  - The daemon's `ForecastingStep` becomes the only producer.
- Persist per-horizon `Forecast_{h}_Lower/Upper` quantiles (computed today, dropped by the pipeline).

### F5: Decisions in units of forecast error (the real rebuild; validated before switching)
- Replace the fixed ±1.5% / ±3% thresholds with **z = expected log-return ÷ that model-horizon's measured error**. Bullish/bearish means |z| above a threshold.
- sellRange upper bound from the forecast's upper quantile instead of the point forecast.
- Validated with a backtest through the existing harness (PBO/DSR gates) plus a before/after diff of a live cycle. Shipped behind a flag and flipped only with your OK.
- Needs matured 30-day data from F1 (≈ mid-October at the earliest). So F5 is scheduled, not started, until the data exists.

### F6: One home in the app
- Rework the **Forecast Viewer** into the single Forecasts screen:
  - the published forecast + band per symbol
  - which models are admitted by the gate and why
  - skill vs naive per horizon
  - what the forecast changed this cycle (alignment points, advisory case, sellRange)
- Symbol Detail links to it. The ModelHealthPanel forecast section points there too.

## Order vs the rest of the cleanup
**Recommended:** start F1 now, because measurement needs calendar time to accrue, and run step 4 (archive) in parallel, since the two don't touch the same files. F2 follows F1. F3's two-week shadow runs while step 4/5 proceed. F5 waits for data.

## Docs (in each PR)
- `CLAUDE.md`/`AGENTS.md` forecasting bullets.
- `docs/architecture/signal-engines.md` (`forecasting_engine.py` entry).
- `docs/signals/forecast_alignment.md` (F2 neutral change, F5 z-score).
- `webapp/src/help/helpContent.ts` (F6).

## Verification (every PR)
- Targeted tests.
- The full offline suite.
- For any PR that changes decisions: a frozen-input before/after cycle diff listing every symbol whose action, score or sellRange moved.

## Decisions (operator, 2026-09-27)
1. F2: a missing or fallback forecast scores **0 (neutral)** in `forecast_alignment`. Diff one cycle before merging.
2. F3: **2-week shadow** of the gated blend, then flip `FORECAST_NAIVE_GATE_ENABLED` after reviewing the side-by-side.
3. Order: **F1 now, step 4 (archive) in parallel.**
