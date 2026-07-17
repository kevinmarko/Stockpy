# Signal: `news_catalyst`

**File:** `signals/news_catalyst.py`  
**Default weight:** 10.0  
**Score range:** `[-1.0, +1.0]`  
**Regime gate:** Always active (but scoring degrades gracefully when FINNHUB_API_KEY is absent)  
**Hook pattern:** Two-phase `pre_compute` / `compute`  
**Pilot:** News Catalyst (`news-catalyst`, `pilots/catalog.py`) — no backtest curve
(`validation_strategy_id=None`); backtesting headline sentiment needs point-in-time news
history no free vendor supplies historically — fabricating a headline archive would
violate CONSTRAINT #4. As of 2026-07, `pre_compute()` forward-archives each cycle's
live score to `HistoricalStore.news_history` (`settings.NEWS_HISTORY_CAPTURE_ENABLED`,
default on) so real point-in-time history accumulates going forward — a genuine
backtest becomes possible after roughly 6-12+ months, but not before.

---

## Rationale

News sentiment captures fundamental information flow not reflected in price history:
earnings surprises, management changes, regulatory events, macro commentary. A stock
with neutral technicals but strongly positive news sentiment may have a near-term catalyst
that price has not yet discounted.

**Academic support:**
- **Tetlock (2007)** "Giving Content to Investor Sentiment" found that high media
  pessimism predicts downward pressure on market prices, with reversal within days for
  large-caps.
- **Boudoukh et al. (2019)** "Information, Trading and Volatility: Evidence with Public
  Announcements" documented that news releases significantly predict short-term returns
  in a direction consistent with the sentiment of the announcement.

**FinBERT** (Araci, 2019) is a BERT-based language model fine-tuned on financial news
corpora. It outperforms general-purpose sentiment classifiers (VADER, TextBlob) on
financial text by ~10–15 F1 points on the FPB dataset.

---

## Two-Phase Hook

```
pre_compute(universe_df, context):
    For each symbol:
        1. Fetch company_news (last NEWS_LOOKBACK_DAYS = 7 days) from Finnhub.
        2. Fetch next earnings date from earnings_calendar.
        3. Score each headline via FinBERT (preferred) or lexicon fallback.
        4. Average headline scores → raw_sentiment ∈ [-1, +1].
        5. Apply earnings proximity multiplier (see below).
        6. Store in self._news_scores[symbol] AND context.news_sentiment_scores[symbol].
        7. Store next earnings date in self._earnings_dt[symbol].

compute(row, context):
    score = context.news_sentiment_scores.get(symbol, 0.0)
    return SignalOutput(score=score, ...)
```

Rate courtesy sleep: 0.12 s per symbol ≈ 8 calls/s, safely under Finnhub's 60/min
free-tier ceiling.

---

## Earnings Proximity Multiplier

News near an earnings announcement is unreliable — sentiment reflects speculation and
positioning rather than confirmed fundamentals. The multiplier suppresses the signal:

| Window | Multiplier | Rationale |
|--------|------------|-----------|
| Within 48 h of earnings | **0.0** (fully suppressed) | Pre-earnings positioning noise |
| 3–7 days before earnings | **0.5** (dampened) | Approaching the event |
| 0–24 h after earnings | **0.5** (dampened) | Post-announcement whipsaw |
| > 7 days from earnings | **1.0** (full) | Clean fundamental signal |

Configurable via `NEWS_EARNINGS_SUPPRESS_HOURS` (default 48) and
`NEWS_EARNINGS_DAMPEN_DAYS` (default 7).

---

## FinBERT vs Lexicon Fallback

```
IF FINBERT_ENABLED=True AND transformers/PyTorch available:
    Load once at process start via _get_finbert_pipeline()
    Score per headline: "positive" → +confidence, "negative" → −confidence, "neutral" → 0
ELSE (transformers ImportError OR FINBERT_ENABLED=False):
    Lexicon fallback:
        score = (positive_word_count − negative_word_count)
                / max(1, positive_word_count + negative_word_count)
```

The lexicon uses ~80 domain-specific words: "bullish", "beat", "exceeded", "acquisition"
(positive) vs. "miss", "downgrade", "investigation", "lawsuit" (negative).

Both paths produce a score ∈ [−1, +1]. The FinBERT path is significantly more accurate
but requires a ~400 MB model download on first use and a GPU or fast CPU for inference.

---

## Failure Modes

| Failure | Behaviour |
|---------|-----------|
| `FINNHUB_API_KEY` absent | `pre_compute` skips all Finnhub calls; every symbol gets `sentiment = 0.0`. Module is informationless, not broken. |
| Finnhub 429 rate limit | `FinnhubProvider` applies exponential backoff (2 s) + retry once; on persistent 429, returns empty news list. Score = 0.0 for that symbol. |
| `transformers` ImportError (no PyTorch) | Automatic fallback to lexicon. Logged at INFO, not WARNING — this is a supported configuration. |
| FinBERT inference OOM on CPU | An exception in `_score_headlines_finbert()` is caught; fallback to lexicon for that symbol. |
| No headlines in lookback window | score = 0.0 (no news ≠ neutral news, but we treat it as neutral to avoid punishing quiet periods). |
| Symbol with no Finnhub coverage | empty news list → score = 0.0. |

---

## Config / New Columns

Added to `config.COLUMN_SCHEMA`:
- `News_Sentiment` — average headline score ∈ [−1, +1]
- `Earnings_Date` — next earnings date as ISO string or empty

`Correlation_Cluster` (also in COLUMN_SCHEMA) is populated on-demand in the GUI Reports
tab via `research_engine.compute_correlation_clusters()`, not by this module.

---

## Empirical Notes

- At 10.0 weight, a perfectly positive sentiment score (+1.0) contributes +10 pts to the
  aggregate — meaningful but not dominant. A strong fundamental signal (macro + value +
  momentum) of 60+ pts will not be overruled by a single strong news day.
- The 7-day lookback matches the typical "holding the news" period for institutional
  investors before position-building starts. Longer windows (30 days) dilute the signal
  with stale headlines; shorter windows (1–2 days) capture momentum rather than
  fundamental reassessment.
- For earnings-calendar-sparse symbols (e.g. monthly-dividend payers), the earnings
  proximity multiplier defaults to 1.0 (full signal) — the suppression only fires when
  Finnhub returns a valid next-earnings date.
