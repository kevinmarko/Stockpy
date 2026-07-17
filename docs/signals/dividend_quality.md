# Signal: `dividend_quality`

**File:** `signals/dividend_quality.py`  
**Default weight:** 25.0  
**Score range:** `[-1.0, +1.0]`  
**Regime gate:** Always active  
**Pilot:** Dividend Income (`dividend-income`, `pilots/catalog.py`) — backed by a real,
PBO/DSR-gated backtest (`dividend_yield_edgar_pit` in `scripts/refresh_validations.py`):
a cross-sectional dividend-yield tilt over real SEC EDGAR point-in-time fundamentals
(the raw `dividend_yield` PIT field, used directly). As of 2026-07 this backtest is real
but `deployable=False` — see **Backtest Validation** below.

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

---

## Backtest Validation (`dividend_yield_edgar_pit`, 2026-07)

The `dividend_yield_edgar_pit` adapter (same 10 fixed EDGAR-fixture-matched tickers as
`deep_value_edgar_pit`, long-only top-half equal-weight `dividend_yield` tilt, 1
variant) had its registry turnover corrected from 0.05 to 0.01, empirically measured at
0.119%/day averaged over the full 20-year backtest (8 discrete rebalance events total).

| Metric | Before | After | Gate |
|---|---|---|---|
| Sharpe | -0.082 | 0.222 | needs > 0.50 — **still FAILS** |
| PBO | 0.000 | 0.000 | < 0.50 ✅ |
| DSR | 1.000 | 1.000 | > 0.95 ✅ |
| MaxDD | 25.7% | **12.2%** | < 30% ✅ (was already passing) |
| `deployable` | False | **False (honest)** | |

**Verdict:** MaxDD is fully fixed by the turnover correction alone. Sharpe stays
honestly below the gate for the same class of reason as its sibling `graham_value` /
`deep_value_edgar_pit` fix, but manifesting as a *time* gap rather than a *ticker* gap:
real `dividend_yield` EDGAR point-in-time coverage only exists from 2024-02 onward —
95.5% of the 20-year backtest window is forced-flat — and JNJ/XOM/GE have zero coverage
of this field at any date. Restricted to only its genuinely covered ~11-month window,
the strategy's raw Sharpe is a strong 1.40; the full-series number is a dilution
artifact of backtest-window length against real, but recent-only, PIT coverage — not
evidence the underlying yield-quality thesis is weak. A trend overlay (gated on the
book's own trailing return, not an external SPY series) was tested across 4 lookback
windows and measurably *hurt* performance every time — an already-thin 228-active-day
sample means any additional filter removes real signal, not noise — so it was rejected
with evidence rather than assumed to help.

See [PR #314](https://github.com/kevinmarko/Stockpy/pull/314) and
[`docs/VALIDATION_STRATEGY_FIX_LOG.md`](../VALIDATION_STRATEGY_FIX_LOG.md) for the
full 12-strategy series this fix was part of.
