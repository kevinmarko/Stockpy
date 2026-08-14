# Signal: `options_flow_sentiment`

* **File:** `signals/options_flow_sentiment.py`
* **Default weight:** 10.0 (`settings.SIGNAL_WEIGHTS["options_flow_sentiment"]`)
* **Score range:** `[-1.0, +1.0]`
* **Regime gate:** Always active
* **Pilot:** Options Flow Sentiment (`options-flow-sentiment`)

---

## Rationale

Institutional options order flow provides valuable predictive information regarding informed positioning and directional alpha. Academic literature (e.g., Pan & Poteshman 2006, "The Information in Option Volume for Future Stock Prices"; Johnson & So 2012, "The Option to Stock Volume Ratio") demonstrates that non-market-maker options volume—specifically aggressive order flow (ask sweeps vs. bid sweeps)—contains leading information about future asset returns and price momentum.

Large block trades executed aggressively at or above the Ask price indicate urgency to acquire upside (for Calls) or downside protection/speculation (for Puts). Conversely, trades executed aggressively at or below the Bid represent aggressive premium selling or position liquidations.

---

## Signal Logic

The signal evaluates unusual options activity (UOA) records and calculates the normalized net directional flow sentiment:

$$\text{Net Flow Sentiment} = \frac{\text{Bullish Notional} - \text{Bearish Notional}}{\text{Total Notional}} \in [-1.0, +1.0]$$

Where:
* **Bullish Notional** = Call Ask Sweeps ($\text{Price} \ge \text{Ask}$) + Put Bid Sweeps ($\text{Price} \le \text{Bid}$) + explicit BULLISH records
* **Bearish Notional** = Put Ask Sweeps ($\text{Price} \ge \text{Ask}$) + Call Bid Sweeps ($\text{Price} \le \text{Bid}$) + explicit BEARISH records
* **Total Notional** = Bullish Notional + Bearish Notional + Neutral Notional

| Condition | Points / Score | Interpretation |
|-----------|----------------|----------------|
| $\text{Net Sentiment} > +0.15$ | $+0.15 \text{ to } +1.0$ | Bullish institutional call sweep / put bid flow |
| $\text{Net Sentiment} < -0.15$ | $-0.15 \text{ to } -1.0$ | Bearish institutional put sweep / call bid flow |
| $-0.15 \le \text{Net Sentiment} \le +0.15$ | $-0.15 \text{ to } +0.15$ | Balanced / neutral options order flow |
| No UOA Data for Symbol | $0.0$ (`confidence = 0.0`) | Neutral fallback — no unusual flow detected |

---

## Failure Modes

| Failure Mode | Impact | Handling (CONSTRAINT #4 / #6) |
|--------------|--------|-------------------------------|
| Missing / Empty Chain | No flow detected | Returns `score = 0.0, confidence = 0.0` with `"Options flow sentiment: neutral/no flow data this cycle"` explanation. |
| Single-sided 0 OI trade | Division by zero in V/OI | Volume / 0 OI safely treated as infinite ratio ($999.99\times$) without crashing. |
| Zero Total Notional | Division by zero in Net Sentiment | Returns neutral `score = 0.0`. |

---

## Interaction with Other Modules

* **`news_catalyst`**: Complements headline and social sentiment with real-time institutional money flow.
* **`timeseries_momentum` / `cross_sectional_momentum`**: Acts as an alpha overlay confirming whether institutional options positioning aligns with price trend.
* **`vrp_premium_selling`**: Differentiates between directional options flow vs. volatility-selling regimes.
