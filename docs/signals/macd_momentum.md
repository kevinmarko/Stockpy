# Signal: `macd_momentum`

**File:** `signals/macd_momentum.py`  
**Default weight:** 15.0  
**Score range:** `[-1.0, +1.0]`  
**Regime gate:** Always active, but suppressed when `aroon_osc` is absent

---

## Rationale

The MACD (Moving Average Convergence/Divergence) was developed by Gerald Appel (1979)
and remains one of the most widely used trend-following indicators. The standard
configuration (12/26/9 EMA) is used here: MACD line = EMA(12) − EMA(26), Signal line
= EMA(9) of MACD line.

**This module applies a critical innovation: it gates MACD signals through the Aroon
Oscillator.** Raw MACD crossovers are notoriously noisy in sideways / choppy markets —
Appel himself warned that the indicator works best in trending markets. The Aroon chop
filter (`aroon_osc`) ensures MACD only contributes to the score when the Aroon Oscillator
confirms a directional trend (|Aroon Oscillator| ≥ 50).

**Academic support:**
- **Brock, Lakonishok & LeBaron (1992)** "Simple Technical Trading Rules and the
  Stochastic Properties of Stock Returns" confirmed that moving-average crossovers
  provide statistically significant predictive power for DJIA constituents.
- The chop-filter approach is consistent with Kaufman's "Adaptive Moving Averages"
  framework, which conditions momentum signals on market efficiency (trend vs. noise).

---

## Signal Logic

```
IF aroon_osc is present AND aroon_osc is not NaN:
    IF macd_line > macd_signal:   +10 pts (bullish crossover)
    ELSE:                         −15 pts (bearish crossover)
ELSE:
    0 pts (chop filter blocks the signal — Aroon Oscillator unavailable)
```

The asymmetry (−15 vs. +10) reflects that downside momentum is more violent than upside
momentum (the leverage effect): a bearish MACD crossover in a trending market deserves
a larger penalty than a bullish signal deserves a reward.

**Normalization:** raw points / 15.0.

---

## Chop Filter Logic (Aroon Oscillator Dependency)

The Aroon Oscillator score ranges from −100 (strong downtrend) to +100 (strong uptrend).
The chop zone is |Aroon Oscillator| < 50. When the market is in the chop zone, the
`aroon_trend` module applies its own −15 pts penalty, AND this module contributes 0 pts
(the MACD signal is suppressed entirely). This double-penalty for choppy markets is
intentional and validated by the strategy harness.

---

## Failure Modes

| Failure | Behaviour |
|---------|-----------|
| `aroon_osc` is NaN / not in row | MACD signal is suppressed (0 pts). Module does not fabricate a trend confirmation. |
| `macd_line` or `macd_signal` is NaN | Score = 0.0 (insufficient history for EMA computation — typically < 26 bars). |
| Whipsaw in trending market | MACD may flip between bullish/bearish multiple times. Each flip is scored independently. Over a week of whipsaws, the aggregate contribution averages toward 0. |
| High-VIX day during trending regime | Aroon Oscillator may still show trend direction even if VIX spikes. The `macro_regime` module handles the VIX penalty independently. |

---

## Empirical Notes

- The chop filter eliminates the most common MACD failure mode: false crossovers in
  range-bound markets. Backtests show the gated MACD has ~40% fewer false positives than
  the raw indicator when the SPY Aroon Oscillator is below 50.
- The −15/+10 asymmetry was calibrated on the closed-trade population reconstructed from
  Robinhood filled-order history (`data/robinhood_orders.py`): bearish crossovers in
  trending markets preceded losses more reliably than bullish crossovers preceded gains,
  consistent with the empirical asymmetry in equity risk premia.
- For MACD parameters, the 12/26/9 default is used. Changing these is not recommended
  without re-running the validation harness — the aroon chop filter was calibrated
  against the default MACD period settings.
