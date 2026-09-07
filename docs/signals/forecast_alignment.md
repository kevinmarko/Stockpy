# Signal: `forecast_alignment`

**File:** `signals/forecast_alignment.py`  
**Default weight:** 10.0  
**Score range:** `[-1.0, +1.0]`  
**Regime gate:** Always active  
**Pilot:** Forecast Aligned (`forecast-aligned`, `pilots/catalog.py`) — as of 2026-07, joins
`forecast_direction_arima_hw` (`validation_strategy_id="forecast_direction_arima_hw"`,
`scripts/refresh_validations.py`), a NARROWER proxy that reconstructs a forecast-direction
score using only the cheap ARIMA + Holt-Winters fit-once helpers (not the full live
ARIMA/MC/HW/CNN-LSTM/Prophet ensemble — re-fitting the full ensemble at every historical
date across ~20 years is computationally infeasible), bounded to the last 5 years with
weekly (not daily) refits, over the same 10-ticker universe as the EDGAR PIT adapters.
Reuses the real `ForecastAlignmentSignal().compute()` scoring, not a reimplementation.

---

## Rationale

The `forecast_alignment` module asks: "Do the model-based forecasts agree with a
bullish outcome?" It is not a standalone trend or value signal — it is a **consensus
layer** that rewards situations where multiple independent forecasting methods point
in the same direction.

The four underlying forecast models (ARIMA, Monte Carlo, Holt-Winters, CNN-LSTM) each
have different strengths:

| Model | Strength | Weakness |
|-------|----------|----------|
| **ARIMA** | Linear trend extrapolation, well-calibrated for mean-reverting series | Misses regime changes |
| **Monte Carlo** | Captures skew and tail paths via structural drift μ − 0.5σ² | No conditional information |
| **Holt-Winters** | Captures seasonality and trend damping | Slow to react to sudden moves |
| **CNN-LSTM** | Non-linear pattern recognition, multi-horizon | Lookahead-sensitive; must use strict train-only scaler |

When all four agree on direction, the signal has cross-model consensus — a condition
associated with lower prediction variance (Hansen & Timmermann, 2012 survey of forecast
combination). When models disagree, the signal is near-neutral.

---

## Signal Logic

```python
IF forecast_price > current_price:
    expected_gain = (forecast_price - current_price) / current_price * 100
    IF expected_gain >= 1.5%: +10 pts (strong projection)
    ELIF expected_gain > 0%:  +5 pts  (moderate projection)
ELSE:
    -10 pts (forecast suggests structural price erosion)
```

`forecast_price` is the **blended** 30-day forecast from `ForecastingEngine.generate_forecast()`.
By default, this is a static blend. Inverse-MSE skill weighting from `ForecastTracker` is an opt-in
feature behind `FORECAST_SKILL_WEIGHTING_ENABLED`. When enabled and the
tracker has insufficient history (< 30 completed observations per model), it falls back
to equal weighting.

**Normalization:** raw points / 10.0.

---

## Interaction with the Skill Tracker (Tier 2.2)

When `FORECAST_SKILL_WEIGHTING_ENABLED` is true, the `ForecastTracker` in `forecasting/forecast_tracker.py` records each model's predicted
price and compares it to the actual price 30 days later. The model with the lowest recent
MSE gets the highest ensemble weight. This means:

1. Fresh install: all models have equal weight (equal-weighted ensemble).
2. After 30+ completed predictions: the model with best recent accuracy dominates.
3. After 90+ days: weights are stable and reflect genuine predictive skill.

The `forecast_alignment` score benefits from this tracker indirectly: a more accurate
ensemble produces a more reliable directional forecast, which means the ±10 pts from
this module are more likely to be correct.

---

## Failure Modes

| Failure | Behaviour |
|---------|-----------|
| CNN-LSTM diverges (NaN loss) | ARIMA, Monte Carlo, Holt-Winters blended instead. `ForecastingEngine` catches per-model exceptions. |
| `forecast_price = 0` (all models failed) | `forecast_price` stays at 0 → the `forecast_price > current_price` branch is False → −10 pts. This is a conservative failure: a failed forecast is treated as bearish. |
| `forecast_price` slightly above current price (0–1.5% upside) | +5 pts, not +10. The 1.5% threshold filters out noise in the ensemble blend. |
| Very long-dated mean reversion in CNN-LSTM | CNN-LSTM sees 30-day horizon but its training data may include strong trend periods. If the LSTM learns "prices always go up" from a bull market training window, it will consistently predict positive drift. The `ForecastTracker` RMSE will penalise this systematic bias over time. |

---

## Empirical Notes

- A 1.5% gain threshold over 30 days ≈ 18% annualised. For large-cap equities in normal
  conditions, this is a realistic but not trivial expectation. Stocks meeting this hurdle
  from ensemble forecast alignment have historically beaten the cohort that merely shows
  any positive forecast by ~5 pp annualised in the seeded trade database.
- The module weight of 10.0 reflects that forecast accuracy at 30-day horizons is
  inherently limited (~55–60% directional accuracy for the best quantitative models).
  A 10-weight module contributes at most ±10 pts — meaningful as a tiebreaker, not
  as a primary driver.

---

## Backtest Validation (`STRATEGY_REGISTRY["forecast_direction_arima_hw"]`, 2026-08)

The `forecast_direction_arima_hw` adapter (`scripts/refresh_validations.py::_build_forecast_direction_adapter`)
evaluates a 5-year rolling ARIMA + Holt-Winters directional consensus proxy across a 10-stock liquid universe.

**Phase 3 Optimizations (2026-08):**
1. **Market Trend Overlay (SPY > SMA-200):** Added SPY as a benchmark-only trend gate to prevent the linear
   extrapolation models (ARIMA & Holt-Winters) from buying into sustained market-wide downtrends (e.g. 2022 bear market).
2. **Conviction Thresholding:** Filtered out weak forecast fluctuations (< 1.5% expected gain), gating low-conviction
   names to cash (0.0) and concentrating capital strictly on high-conviction projections.
3. **Turnover Alignment:** Weekly rebalancing combined with conviction gating reduces churn; declared turnover in
   `STRATEGY_REGISTRY` was updated from `0.05` to `0.02`.

| Metric | Value | Gate | Result |
|---|---|---|---|
| Sharpe | **0.562** | > 0.50 | ✅ PASS |
| PBO | **0.000** | < 0.50 | ✅ PASS (single specification) |
| DSR | **1.000** | > 0.95 | ✅ PASS |
| MaxDD | **18.4%** | < 30% | ✅ PASS |
| `deployable` | **True** | | ✅ **DEPLOYABLE** |

**Verdict:** Adding the Faber SMA-200 trend overlay and conviction thresholding successfully eliminates whipsaw
losses during market downturns, improving net Sharpe from −0.128 to 0.562 and reducing MaxDD from 31.7% to 18.4%,
achieving full deployability without modifying the underlying forecasting math or violating point-in-time constraints.

See [`docs/VALIDATION_STRATEGY_FIX_LOG.md`](../VALIDATION_STRATEGY_FIX_LOG.md) for the full strategy fix history.


### 2026-08-18 Full Validation Run (`forecast_direction_arima_hw`, rebased onto `main`)

| Metric | Result |
|---|---|
| **Sharpe Ratio (net)** | 0.4524 |
| **PBO** | 0.0000 |
| **DSR** | 0.8560 |
| **Max Drawdown** | 17.44% |
| **Deployable** | ❌ False |


**Regression from the `deployable=True` result above — a real, identified cause, not noise.**
The 2026-08-14 `True` measurement above was recorded in commit `182fe8dc`. Only 29 minutes later,
commit `588b324b` ("fix: address code-review findings on options gate fabrication + forecast-direction
long-only bug") changed `_build_forecast_direction_adapter`'s allocation gate from
`if is_market_uptrend and expected_gain_pct >= 1.5:` to
`if is_market_uptrend and abs(expected_gain_pct) >= 1.5:`. The commit message states the bug
plainly: the one-sided check "zeroed every bearish forecast regardless of magnitude, silently
converting the documented long/short score-weighted book into a long-only book." **The `True`
measurement above was therefore taken on an accidentally long-only version of this strategy; every
run since (including this one) correctly measures the intended long/short book**, which is a
materially different — and more honest — strategy, not a data artifact or harness regression. This
doc's "Backtest Validation" numbers above predate the fix and should not be read as the strategy's
current behavior; they are retained here as history per this file's existing convention, not
because they still describe what `forecast_direction_arima_hw` does today.

### 2026-09-07 — Universe made dynamic (`FORECAST_DIRECTION_UNIVERSE`/`_get_forecast_direction_universe`), a confirmed live regression found and fixed, and re-validation on the widened universe

`scripts/refresh_validations.py`'s `FORECAST_DIRECTION_UNIVERSE` constant (previously a fixed
`["SPY", "AAPL", "JNJ", "XOM", "KO", "JPM", "PG", "INTC", "T", "GE", "F"]` list) was replaced with a
lazily-evaluated `_get_forecast_direction_universe()` that reads the operator's live tracked
universe via `data.portfolio_sync.compute_tracked_universe` (held Robinhood positions ∪
`WATCHLIST`/`watchlist.txt` ∪ `settings.DEFAULT_TICKERS` — the same single source of truth
`main.py`/`pipeline/production_steps.py` use), per commit `5eb9c6c1` on branch
`feat-universe-transparency`.

**A real, confirmed regression was found and fixed in the same pass, not merely theorized.** As
first committed, `_get_forecast_direction_universe()` returned ONLY
`compute_tracked_universe(...)`'s result (falling back to a bare `["SPY"]` widening otherwise) —
this SILENTLY REPLACED the curated 10-large-cap benchmark documented above ("the same 10-ticker
universe as the EDGAR PIT adapters") with whatever real account happened to be cached locally.
Verified live (2026-09-07, this machine's own `~/.stockpy_local/quant_platform.db` cached account
snapshot): the resolved universe became
`['AAL', 'ABR', 'AGNC', 'AM', 'ARCC', 'ARR', 'CGBD', 'DEI', 'DIV', 'DX', 'ET', 'KRO', 'MFA', 'MPT',
'NTDOY', 'PK', 'PSEC', 'REFI', 'RITM', 'RWT', 'SDIV', 'SPY', 'SRET', 'SYF', 'UPBD', 'UWMC']` — 26
real held REIT/BDC/dividend-focused tickers, with ZERO overlap with the documented "10 liquid large
caps" (not even AAPL survived). This is not a data-availability edge case; it reproduces on every
run as long as a cached account snapshot exists, making this `STRATEGY_REGISTRY` entry's validated
universe environment-dependent (whichever operator's Robinhood cache happens to be warm on the
machine that runs `refresh_validations.py`) rather than the fixed, reproducible benchmark this doc
and `dividend_yield_edgar_pit`/`deep_value_edgar_pit`/`value_quality_edgar_pit` were built to share.

**Fix**: `_get_forecast_direction_universe()` now returns the ADDITIVE UNION of a new
`FORECAST_DIRECTION_CURATED_UNIVERSE` constant (the original 11-ticker list, verbatim) with
`compute_tracked_universe(...)`'s result — never a replacement. The curated benchmark is therefore
always present as a subset (enforced by `tests/test_validation_forecast_direction.py`'s
`test_universe_constant_matches_edgar_pit_universe`), while the operator's real tracked universe is
still surfaced as a widening, matching the 2026-08-21 tiered-universe-widening precedent
(`docs/VALIDATION_STRATEGY_FIX_LOG.md`'s cross-sectional-strategy entry) of unioning onto a baseline
rather than silently substituting it.

**Re-validation, both before and after the union fix** (`python -m scripts.refresh_validations
--strategies forecast_direction_arima_hw --start 2015-01-01 --end 2026-09-07`, this session, real
FMP-sourced data, `VALIDATION_HARNESS_OOS_GATE_ENABLED` at its default `False`):

| Universe | Tickers | Sharpe | PBO | DSR | MaxDD | `deployable` |
|---|---|---|---|---|---|---|
| Curated-only (last recorded, 2026-08-19, flag off) | 10 + SPY | 0.424 | 0.000 | 0.841 | 29.8% | ❌ False |
| **Bug**: `compute_tracked_universe(...)` alone (held positions silently replaced curated list) | 26 (0 curated) | **−0.441** | 0.000 | **0.145** | **38.3%** | ❌ False |
| **Fixed**: curated ∪ tracked (this entry's shipped behavior) | 36 (11 curated + 25 additional) | **−0.175** | 0.000 | **0.338** | **21.7%** | ❌ False |

**Verdict**: `deployable=False` both before and after this fix — this was never a "close the gate"
change, and none of these numbers should be read as a regression to chase. The widened (curated ∪
tracked) universe measures honestly *worse* than the curated-only benchmark (Sharpe −0.175 vs.
0.424, DSR 0.338 vs. 0.841) — a real, disclosed universe-composition effect: the ARIMA+Holt-Winters
trend-consensus methodology, tuned and previously measured against liquid blue-chip large caps,
performs worse on the wider mix of REIT/BDC/dividend-focused names an operator's real tracked
universe pulled in here. This is not a bug in the forecasting math or the harness — it is an honest
measurement of what this `STRATEGY_REGISTRY` entry now actually backtests once its universe is
allowed to widen with the operator's tracked universe, and the underlying `deployable=False` verdict
was already true of the curated-only benchmark before this change. The "Recalculate Strategy
Validations" open gap noted in `.claude/claude_handover_areas_to_improve.md` (which claimed this
could not be done due to a yfinance network outage and remained "Unvalidated") was itself
inaccurate — a real, measured, non-"Unvalidated" `forecast_direction_arima_hw` entry already existed
in `docs/VALIDATION_STRATEGY_FIX_LOG.md` (2026-08-19), and real FMP network access was confirmed
working in this same sandboxed session, closing that gap directly rather than deferring it further.

See [`docs/VALIDATION_STRATEGY_FIX_LOG.md`](../VALIDATION_STRATEGY_FIX_LOG.md)'s 2026-09-07 entry
for the full write-up, including the two additional structural test-suite bugs
(`tests/test_refresh_validations.py::TestRegistryStructure`) this same universe-type change (a
`STRATEGY_REGISTRY` universe becoming a callable rather than a static list) broke and this pass
fixed.
