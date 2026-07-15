# Signal: `dividend_quality`

**File:** `signals/dividend_quality.py`  
**Default weight:** 25.0  
**Score range:** `[-1.0, +1.0]`  
**Regime gate:** Always active  
**Pilot:** Dividend Income (`dividend-income`, `pilots/catalog.py`) — no backtest curve
(`validation_strategy_id=None`); the module needs point-in-time dividend-yield/payout-ratio
history that no free vendor supplies (same honesty constraint as `graham_value`).

---

## Rationale

Dividend-paying stocks are a core component of long-term equity portfolios. However,
an unsustainable dividend yield is often a **value trap**: the payout ratio exceeds
earnings (or cash flow), and the company will eventually cut the dividend, causing both
the income stream and the share price to fall simultaneously.

**Academic support:**
- **Lintner (1956)** showed that managers smooth dividends, so a cut is a strong signal
  of deteriorating fundamentals.
- **Arnott & Asness (2003)** "Surprise! Higher Dividends = Higher Earnings Growth"
  found that high *sustainable* dividend yields predict higher future earnings growth,
  while high payout ratios predict lower growth — the inverse of naive yield-chasing.

The 25.0 weight reflects the holding-aware overlay in `engine/advisory.py`: when
`dividend_yield ≥ 4%` OR `dividends_received ≥ $50`, the advisory engine applies a
**dividend hold bias** — even a neutral or slightly negative score results in HOLD
rather than SELL to avoid disrupting a reliable income stream.

---

## Signal Logic

| Condition | Points |
|-----------|--------|
| `dividend_yield > 0` AND `is_dividend_sustainable == True` | +10 pts |
| `dividend_yield > 0` AND `is_dividend_sustainable == False` (payout > 100%) | −25 pts + WARNING |
| `dividend_yield == 0` | 0 pts |

**Normalization:** raw points / 25.0.

`is_dividend_sustainable` is computed in `FundamentalDataDTO` from **Yahoo statement-derived
fundamentals** (`data/yahoo_fundamentals.py` → `YahooFundamentalsProvider`, primary; raw
yfinance `.info` is the fallback — Finnhub is no longer a fundamentals source):
`payout_ratio < 1.0` (less than 100% of earnings paid out) combined with positive trailing
EPS. When payout ratio data is unavailable, defaults to `False` (conservative: treats
unknown sustainability as a potential trap).

**Scale notes** (from the Yahoo engine's frozen contract): `dividendYield` is emitted as a
**fraction** (e.g. `0.0257`, not `2.57`) and is consumed as-is (not re-normalised);
`payoutRatio` uses `abs()` of the "Cash Dividends Paid" cash-flow line (a negative outflow)
over TTM net income, so the sign is always positive.

---

## Interaction with the Dividend Hold Bias (Advisory Overlay)

The holding-aware overlay in `engine/advisory.py` independently applies a **Case B**
hold bias:

> If a symbol has `dividend_yield ≥ 4%` OR `dividends_received ≥ $50` AND the
> composite score is below the BUY threshold, the advisory action is forced to HOLD
> rather than SELL. The rationale explicitly cites the dividend income.

This means a stock with a 5% sustainable yield will show `+10pts` from this module
AND receive a HOLD floor from the overlay — two independent reinforcing mechanisms.

---

## Failure Modes

| Failure | Behaviour |
|---------|-----------|
| Payout ratio not available (yfinance returns NaN) | `is_dividend_sustainable = False` (conservative). Score is −25 pts if dividend_yield > 0. |
| Dividend announced but not yet reflected in yfinance | Signal may fire a false "no yield" (0 pts) one period before the ex-dividend date. |
| Payout > 100% but dividend not yet cut | Signal correctly warns, but the equity may still appreciate if the market believes the cut is coming. |

---

## Empirical Notes

- The −25 pts penalty for unsustainable dividends is intentionally asymmetric vs. the
  +10 pts reward. This reflects that yield-trap collapses (e.g. GE 2018, AGNC during
  rate spikes) tend to be sudden and severe — the option value of avoiding a 30–50%
  capital loss outweighs the cost of missing a few months of yield.
- For AGNC (a mortgage REIT): the platform tracks `dividends_received` from the
  Robinhood account snapshot, so a long-held AGNC position accumulating real dividend
  income will have its HOLD bias reinforced every cycle, even if the paper price
  fluctuates below cost basis.
