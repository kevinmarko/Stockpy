# Signal: `aroon_trend`

**File:** `signals/aroon_trend.py`  
**Default weight:** 15.0  
**Score range:** `[-1.0, +1.0]`  
**Regime gate:** Always active

---

## Rationale

The Aroon Indicator (Tushar Chande, 1995) measures how recently a high/low was set
within a lookback window (typically 25 periods):

```
Aroon Up   = ((n - periods since n-period high) / n) × 100
Aroon Down = ((n - periods since n-period low)  / n) × 100
Aroon Oscillator = Aroon Up − Aroon Down ∈ [−100, +100]
```

Unlike RSI (which measures price velocity) or MACD (which measures moving average
divergence), the Aroon Oscillator measures **time since extremes** — a fundamentally
different information source. An Aroon Oscillator of +80 means recent highs were set
very recently while recent lows are well in the past, indicating a strong uptrend.

The critical function of this module is the **chop filter**: when |Aroon Oscillator| < 50,
the market is range-bound, and directional signals from MACD, RSI, and momentum modules
have lower reliability. The −15 pts penalty for chop suppresses the overall score in
sideways markets without requiring any individual module to know about market structure.

---

## Signal Logic

**With Aroon Oscillator available (primary path):**

| Condition | Points |
|-----------|--------|
| `aroon_osc >= +50` | +15 pts — strong uptrend confirmed |
| `−50 < aroon_osc < +50` | −15 pts — choppy market, directional signals unreliable |
| `aroon_osc <= −50` | −15 pts — strong downtrend |

**Without Aroon Oscillator (legacy fallback using `trend_strength`):**

| Condition | Points |
|-----------|--------|
| `trend_strength >= 50` | +10 pts |
| `30 ≤ trend_strength < 50` | −5 pts |
| `trend_strength < 30` | −15 pts |

**Normalization:** raw points / 15.0.

---

## Role as Chop Filter

The chop filter is used by **two** modules simultaneously:
1. `aroon_trend` itself applies −15 pts.
2. `macd_momentum` suppresses its signal (0 pts) when `aroon_osc` is in the chop zone.

This means a choppy market results in a combined −15 pts (aroon) + 0 pts (vs. potential
±15 pts from MACD) = 15 pts of signal suppression purely from market-structure detection.

---

## Failure Modes

| Failure | Behaviour |
|---------|-----------|
| `aroon_osc` not computed (< 25 bars) | Falls back to `trend_strength`. Legacy path uses a simpler threshold but avoids the information loss of returning 0. |
| Both `aroon_osc` and `trend_strength` unavailable | Score = 0.0. No fabrication. |
| Strong uptrend with RECESSION macro regime | Aroon fires +15 pts, but `macro_regime` fires −15 pts or more. The net aggregate correctly suppresses the BUY signal. The signals do not fight each other — they add in a weighted sum; the macro penalty wins. |
| Very volatile trending stocks | VIX spikes can coexist with strong Aroon trends. The `macro_regime` module handles VIX directly; Aroon correctly still fires if the price trend is intact. |

---

## Empirical Notes

- The Aroon Oscillator as a chop filter outperforms simple ATR-based filters in the
  backtests because it directly measures *information content* (time since price extremes)
  rather than just *price spread* (which can be high in trending or range-bound markets).
- The 25-period Aroon lookback implies the indicator captures roughly 5 weeks of daily
  bar data — long enough to filter daily noise, short enough to react to regime changes
  within 1–2 months.
- Weight parity with `macd_momentum` (both 15.0) is intentional: together they form a
  coherent trend-detection pair. The combined max contribution is ±30 pts from the
  trend layer, balanced against ±20 pts from RSI and ±15 pts from Graham value.
