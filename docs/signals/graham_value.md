# Signal: `graham_value`

**File:** `signals/graham_value.py`  
**Default weight:** 15.0  
**Score range:** `[-1.0, +1.0]`  
**Regime gate:** Always active  
**Pilot:** Deep Value (`deep-value`, `pilots/catalog.py`) — no backtest curve
(`validation_strategy_id=None`); Graham Number intrinsic value needs point-in-time EPS/book
value history that no free vendor supplies (same honesty constraint as `dividend_quality`).

---

## Rationale

Benjamin Graham's intrinsic value formula (from "The Intelligent Investor", 1949 revised
edition) provides a simple, durable floor for equity valuation:

```
Graham Number = √(22.5 × EPS × BVPS)
```

Where:
- `EPS` = trailing twelve-month earnings per share
- `BVPS` = book value per share
- `22.5` = 15× P/E × 1.5× P/B (Graham's upper limits for "fair value")

A price below the Graham Number implies a stock is priced as if the market assigns no
premium for growth, intangibles, or franchise value — a classic margin-of-safety condition.

**Academic support:**
- Graham & Dodd (1934) "Security Analysis" introduced the margin-of-safety concept that
  underpins modern value investing.
- **Oppenheimer (1984)** tested Graham's criteria empirically, finding that stocks meeting
  his combined P/E + P/B criteria significantly outperformed the market over 1971–1980.
- **Greenblatt (2005)** "The Little Book That Beats the Market" provides a more modern
  academic validation of value/quality screens related to Graham's framework.

---

## Signal Logic

| Condition | Points |
|-----------|--------|
| `graham_number > 0` AND `graham_number > current_price` | +15 pts — undervalued |
| `graham_number > 0` AND `graham_number ≤ current_price` | −10 pts — overvalued vs Graham |
| `graham_number ≤ 0` (imaginary root — negative EPS or BVPS) | −5 pts — no intrinsic value computable |

**Normalization:** raw points / 15.0.

The −5 pts for an imaginary Graham Number is modest because a negative EPS or negative
book value can be a sign of a high-growth company (e.g. Amazon pre-2002) rather than
distress — but it earns no valuation credit until it generates positive earnings.

---

## Computation in `processing_engine.py`

The Graham Number is computed by `calculate_fundamental_metrics()` as:

```python
import math
if eps > 0 and bvps > 0:
    graham_number = math.sqrt(22.5 * eps * bvps)
else:
    graham_number = 0.0  # imaginary — no real root
```

Inputs (`eps` = `trailingEps`, `bvps` = `bookValue`) come from `FundamentalDataDTO.from_raw_dict()`,
fed by the **Yahoo statement-derived fundamentals engine** (`data/yahoo_fundamentals.py` →
`YahooFundamentalsProvider`, the primary source; raw yfinance `.info` is the emergency fallback).
Finnhub is no longer a fundamentals source. The DTO normalises any residual string fields
(currency symbols, `%`, `"N/A"`) before passing them to calculations.

---

## Failure Modes

| Failure | Behaviour |
|---------|-----------|
| EPS or BVPS not available from yfinance | `graham_number = 0.0` → −5 pts (no-value penalty). |
| Fundamentals stale (> 1 day, per `FUNDAMENTALS_REFRESH_DAYS`) | `HistoricalStore` returns cached values. Signal may lag 1 quarter on earnings releases. |
| Highly negative EPS (severe loss) | −5 pts regardless of book value — consistent with Graham's requirement for positive earnings. |
| Graham Number applies poorly to banks / financials | Book value is meaningful for banks but EPS can be distorted by loan-loss provisions. The `macro_regime` sector veto partially mitigates this during adverse credit conditions. |

---

## Limitations and Known Gaps

- **Growth stocks**: Graham explicitly excluded companies with P/E > 15, which excludes
  most growth stocks by design. For AAPL, MSFT, NVDA, the Graham Number will almost
  always be below market price, resulting in a permanent −10 pts penalty. This is a
  feature, not a bug — those names rely on other signals (momentum, multifactor, forecast)
  for their thesis.
- **Financial companies (banks, insurance)**: Book value is the relevant metric but EPS
  is volatile. The Graham formula can produce wildly different numbers quarter-to-quarter
  for these companies.
- **Intangibles-heavy companies**: Graham's formula uses book value, which excludes
  intangible assets (brand, IP, software). Modern value investors often use an adjusted
  book that adds back amortised intangibles — this adjustment is not currently implemented.

---

## Empirical Notes

- In the reconstructed closed-trade population (from Robinhood filled-order history via
  `data/robinhood_orders.py`), the Graham signal has a slightly positive win-rate
  correlation (stocks below Graham Number that were bought tended to outperform), but
  the sample is not large enough to draw firm conclusions for individual tickers.
- Weight of 15.0 reflects that pure Graham value is a useful sanity check but not a
  primary driver in a modern multi-factor context where quality and momentum explain more
  return variation than raw cheapness alone.
