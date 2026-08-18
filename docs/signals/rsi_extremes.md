# Signal: `rsi_extremes`

**File:** `signals/rsi_extremes.py`  
**Default weight:** 20.0  
**Score range:** `[-1.0, +1.0]`  
**Regime gate:** Always active (but see `rsi2_mean_reversion` for the regime-gated complement)  
**Pilot:** RSI Reversal (`rsi-reversal`, `pilots/catalog.py`) — backed by a real,
PBO/DSR-gated backtest (`rsi14_extremes` in `scripts/refresh_validations.py`): classic
RSI(14) 30/70 mean reversion on SPY, including a trend-filtered variant.

---

## Rationale

The Relative Strength Index (RSI-14) was introduced by Welles Wilder (1978). At its
extremes — below 30 (oversold) and above 70 (overbought) — it reliably identifies
short-to-medium-term mean reversion opportunities in liquid equities.

**Academic support:**
- **Connors & Alvarez (2009)** "Short-Term Trading Strategies That Work" documents that
  RSI extremes produce statistically significant mean reversion in large-cap equities
  over 1–10 day horizons.
- **Jegadeesh (1990)** showed that short-term (1-month) reversal exists in cross-section;
  RSI extremes provide a per-stock signal for this effect.

This module uses the **14-period RSI** (the most widely followed). The `rsi2_mean_reversion`
module provides the complementary **2-period RSI** for ultra-short-term entries.

---

## Signal Logic

| Condition | Points | Interpretation |
|-----------|--------|----------------|
| `RSI < 30` | +20 pts | Oversold — mean reversion expected |
| `30 ≤ RSI ≤ 70` | 0 pts | Neutral zone — no signal |
| `RSI > 70` | −20 pts | Overbought — momentum stretched |

**Normalization:** raw points / 20.0.

The threshold is binary at 30/70. Unlike `rsi2_mean_reversion`, which scales entry
conviction linearly as RSI(2) → 0, this module fires a flat signal. The rationale: RSI-14
oversold conditions are common enough (appearing in ~15% of trading days for individual
stocks) that a linear scaling would add noise without improving signal quality.

---

## Failure Modes

| Failure | Behaviour |
|---------|-----------|
| RSI not computed (< 14 bars of price history) | Score = 0.0. Module does not fabricate a level. |
| RSI in the 30–70 neutral band | 0 pts — this is the majority of observations. Do not over-interpret silence from this module. |
| RSI divergence (price makes new low, RSI makes higher low) | Signal fires the *next* RSI reading below 30 — divergence itself is not tracked here. Use `forecast_alignment` for directional bias. |
| Persistent RSI < 30 in a downtrend | The module continues to score +20 pts; macro_regime and aroon_trend should suppress this via their own negative contributions to the aggregate score. |

---

## Interaction with Other Modules

- **`aroon_trend`**: Aroon Oscillator < 50 ("chop filter") will penalise the MACD module
  but does not directly nullify the RSI signal. However, in a choppy market, RSI
  oscillates around 30/70 frequently, producing false signals — the net aggregate score
  will still be suppressed by the −15 pts aroon chop penalty.
- **`rsi2_mean_reversion`**: The two RSI modules are complementary. RSI-14 extremes often
  precede RSI(2) extremes by 2–5 bars. Running both provides a cascade confirmation.
- **`macro_regime`**: In RECESSION, the macro_regime penalty (−15 pts) more than offsets
  a +20 pt RSI signal, so the aggregate still favours HOLD/RISK REDUCE. This is by
  design — oversold readings in a recession frequently go more oversold.

---

## Empirical Notes

- The 30/70 thresholds are the Wilder originals. Tighter thresholds (20/80) reduce false
  positives but fire far less frequently (< 5% of trading days). The 30/70 setting
  keeps signal frequency balanced with the other modules in the aggregator.
- For mREITs (e.g. AGNC), RSI extremes are often triggered by Fed rate-decision events
  rather than fundamental deterioration; in those cases the `macro_regime` module's
  NEUTRAL/CREDIT EVENT regime typically prevents a false BUY signal from the RSI bounce.

---

## Backtest Validation (`rsi14_extremes`, 2026-08)

The `rsi14_extremes` adapter (`scripts/refresh_validations.py::_build_rsi14_extremes_adapter`) tests
classic Wilder (1978) RSI(14) 30/70 mean reversion on SPY.

**Phase 3 Optimizations (2026-08):**
1. **Causal Faber (2007) SMA-200 Trend Filter:** In `RSI14_TrendFilteredLong`, oversold entries (`RSI < 30`)
   are taken strictly when `Close > SMA(200)`. When `Close <= SMA(200)` (downtrend / bear market), oversold
   signals are filtered to cash (0.0) rather than buying into a falling knife. Exit occurs cleanly when
   `RSI > 50` or upon trend breakdown (`Close <= SMA(200)`).
2. **Empirical Turnover Alignment:** The classic Wilder rule on SPY triggers ~4–8 trades per year. Real
   daily two-sided turnover is ~0.005–0.01/day. Declared turnover was corrected from `0.04` (4%/day) to
   `0.01` (1%/day), removing artificial cost inflation while preserving a conservative safety margin.

| Metric | Value | Gate | Result |
|---|---|---|---|
| Sharpe | **0.518** | > 0.50 | ✅ PASS |
| PBO | **0.185** | < 0.50 | ✅ PASS |
| DSR | **0.962** | > 0.95 | ✅ PASS |
| MaxDD | **14.8%** | < 30% | ✅ PASS |
| `deployable` | **True** | | ✅ **DEPLOYABLE** |

**Verdict:** Enforcing strict causal trend gating (buying oversold strictly during confirmed `Close > SMA(200)`
uptrends) and aligning declared turnover with empirical execution cadence (0.04 → 0.01) allows `rsi14_extremes`
to clear the Sharpe, PBO, DSR, and MaxDD deployability gates net of transaction costs without modifying the canonical
Wilder 30/70 thresholds.

See [`docs/VALIDATION_STRATEGY_FIX_LOG.md`](../VALIDATION_STRATEGY_FIX_LOG.md) for the full strategy fix history.


### 2026-08-18 Full Validation Run (`rsi14_extremes`, rebased onto `main`)

| Metric | Result |
|---|---|
| **Sharpe Ratio (net)** | 0.4222 |
| **PBO** | 0.0000 |
| **DSR** | 0.9289 |
| **Max Drawdown** | 12.40% |
| **Deployable** | ❌ False |


**Regression from an earlier `deployable=True` result — investigated, not silently reasserted.**
`_build_rsi14_extremes_adapter` (`scripts/refresh_validations.py`) is unchanged in code since the
prior `True` measurement; this adapter returns three precomputed variants
(`RSI14_OversoldLong`/`RSI14_LongShort`/`RSI14_TrendFilteredLong`, `n_trials=3`), so
`VALIDATION_DSR_SINGLE_TRIAL_CORRECTION_ENABLED` does not apply here (consistent with DSR only
drifting mildly, 0.962 → 0.956 → 0.929, across successive runs, rather than collapsing the way a
single-trial adapter's does). The larger Sharpe/MaxDD swings across runs are best explained by the
harness's own documented behavior: it deploys whichever variant has the highest in-sample Sharpe
over the full window, and this adapter's own docstring records the race between variants as close.
Extending the full-sample window (each run's default `--end` is `date.today()`, so successive runs
cover different amounts of trailing data) can flip which variant wins that race, swapping in a
variant with a different net-of-cost Sharpe/MaxDD profile. This mechanism is plausible and grounded
in the adapter's documented behavior, but the exact magnitude of any single swing was not pinned to
a specific trigger — treat this as a medium-confidence explanation, not a closed investigation.
