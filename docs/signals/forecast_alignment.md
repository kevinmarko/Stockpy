# Signal: `forecast_alignment`

**File:** `signals/forecast_alignment.py`  
**Default weight:** 10.0  
**Score range:** `[-1.0, +1.0]`  
**Regime gate:** Always active  
**Pilot:** Forecast Aligned (`forecast-aligned`, `pilots/catalog.py`) — no backtest curve
(`validation_strategy_id=None`); the module scores against an external multi-model
forecast target (ARIMA/MC/HW/CNN-LSTM ensemble), not a price-only signal, so it can't be
honestly reconstructed inside a price-series-only backtest adapter.

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
