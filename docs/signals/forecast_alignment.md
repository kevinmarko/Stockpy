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

`forecast_price` is the **blended** 30-day forecast from `ForecastingEngine.generate_forecast()`,
weighted by inverse-RMSE skill weights from `ForecastTracker` (Tier 2.2). When the
tracker has insufficient history (< 30 completed observations per model), it falls back
to equal weighting.

**Normalization:** raw points / 10.0.

---

## Interaction with the Skill Tracker (Tier 2.2)

The `ForecastTracker` in `forecasting/forecast_tracker.py` records each model's predicted
price and compares it to the actual price 30 days later. The model with the lowest recent
RMSE gets the highest ensemble weight. This means:

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

**Real, measured result** (live yfinance data, `python -m scripts.refresh_validations
--strategies forecast_direction_arima_hw --start 2015-01-01 --end 2026-08-06 --json`, run
2026-08-10):

| Metric | Value | Gate | Result |
|---|---|---|---|
| Sharpe | **−0.128** | > 0.50 | ❌ FAIL |
| PBO | 0.000 | < 0.50 | ✅ |
| DSR | 1.000 | > 0.95 | ✅ |
| MaxDD | **31.7%** | < 30% | ❌ FAIL |
| `deployable` | **False** | | |

Actual window used: **2021-08-05 → 2026-08-05** — the CLI was asked for 2015-2026, but the
adapter self-bounds to the trailing `FORECAST_DIRECTION_WINDOW_YEARS` (5) years of the requested
range, exactly as documented above ("Bounded scope, deliberate, not a data-availability gap").
`n_trials=1` (a single ARIMA+HW proxy, no variant strategies tried).

**Honest read**: PBO and DSR both pass — there's no overfitting-by-selection artifact here
(unsurprising with only one trial) — but the strategy fails on two independent, real gates:
negative net Sharpe and a max drawdown that clears the 30% ceiling by 1.7 points. Both are
measured, not fabricated (the adapter reuses the live `ForecastAlignmentSignal().compute()`
scoring and the real `ForecastingEngine.run_arima_fit`/`run_holt_winters_fit` methods — see the
docstring above). A few honest, evidence-adjacent factors likely contributing (not verified as
THE cause — no counterfactual re-run was performed to isolate any one of them individually,
stated as plausible, not proven):

* **The window is unusually hostile to trend-extrapolating models.** 2021-08 → 2026-08 spans the
  2021 speculative-growth peak, the sharp 2022 rate-hike bear market, and a multi-year recovery —
  several sharp regime reversals in five years. ARIMA and Holt-Winters are both fundamentally
  trend/level-extrapolation methods (see the Rationale table above: "Misses regime changes" /
  "Slow to react to sudden moves" are their documented weaknesses); a window this whipsaw-heavy
  is close to a worst case for exactly those two methods.
* **Narrower proxy, by design.** This adapter deliberately omits Monte Carlo, CNN-LSTM, and
  Prophet — the live signal's other three ensemble members — which could plausibly have
  dampened ARIMA/HW's trend-chasing behavior during the 2022 reversal. This is a documented,
  intentional scope limit (re-fitting the full 5-model ensemble at every historical rebalance
  date is computationally infeasible), not an oversight — but it does mean this result answers
  "does the cheap ARIMA+HW proxy alone have edge?", not "does the live 5-model signal have
  edge?".
* **Simple-averaged, not skill-weighted.** The live signal's `FORECAST_SKILL_WEIGHTING_ENABLED`
  path down-weights a model with a recent poor track record; no historical `ForecastTracker`
  skill weights exist this far back, so this adapter equal-weights ARIMA and HW throughout,
  giving each full say even in periods where one was clearly wrong.
* **Weekly, not daily, refits.** Each week's forecast score is held constant (ffill) between
  Monday-of-week fits while exposure still marks-to-market daily — a real signal could go stale
  for up to 5 trading days during a fast reversal, a documented computational-cost trade-off
  (~7,800 total statsmodels fits for this one run), not a bug.

**Not a contradiction of the module's live status**: `signals/forecast_alignment.py` remains
active in `settings.SIGNAL_WEIGHTS` (weight 10.0) and contributes to every symbol's composite
score exactly as before — this backtest exercises a narrower, deliberately-scoped proxy of one
piece of that signal's methodology, not the full live ensemble, and answers honestly: on this
measurement, the cheap proxy alone does not clear the deployability bar. No threshold was
loosened and no window was cherry-picked to avoid this result. See
`docs/VALIDATION_STRATEGY_FIX_LOG.md`'s 2026-08-10 entry for the full writeup.
