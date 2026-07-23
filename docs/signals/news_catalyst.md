# Signal: `news_catalyst`

**File:** `signals/news_catalyst.py`  
**Default weight:** 10.0  
**Score range:** `[-1.0, +1.0]`  
**Regime gate:** Suppressed (not just down-weighted) during `RECESSION`/`CREDIT EVENT` regimes or `VIX > 30` — see [Regime Gate](#regime-gate) below. Scoring also degrades gracefully when `FINNHUB_API_KEY` is absent.  
**Hook pattern:** Two-phase `pre_compute` / `compute`  
**Pilot:** News Catalyst (`news-catalyst`, `pilots/catalog.py`) — no backtest curve
(`validation_strategy_id=None`); backtesting headline sentiment needs point-in-time news
history no free vendor supplies historically — fabricating a headline archive would
violate CONSTRAINT #4. As of 2026-07, `pre_compute()` forward-archives each cycle's
live score to `HistoricalStore.news_history` (`settings.NEWS_HISTORY_CAPTURE_ENABLED`,
default on) so real point-in-time history accumulates going forward — a genuine
backtest becomes possible after roughly 6-12+ months, but not before.

**Multi-source credibility blend (Sentiment Pipeline Phase 3-4, 2026-07):** `compute()`'s
score is now a renormalized weighted blend of the Finnhub-headline component above and a
multi-source (Reddit/GDELT/EDGAR/Yahoo RSS) credibility-weighted social aggregate read from
`sentiment_ingestion_audit` (see `data/sentiment_sources.py`, `signals/credibility.py`,
`settings.SENTIMENT_SOCIAL_BLEND_WEIGHT`). Gracefully degrades to headline-only when no
social documents exist for a symbol this trading day. Three new introspection columns
(`Credibility_Weighted_Sentiment`, `Bot_Activity_Ratio`, `Aggregated_Source_Credibility`)
surface the raw social aggregate independently of the blended score — see
[Multi-Source Credibility Blend](#multi-source-credibility-blend) below.

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
        3. Score all of this symbol's headlines in one batched score_headlines() call
           (FinBERT, preferred) or per-headline lexicon fallback.
        4. Average the collapsed (positive − negative) headline scores → raw_sentiment ∈ [-1, +1].
        5. Apply earnings proximity multiplier (see below).
        6. Store in self._news_scores[symbol] AND context.news_sentiment_scores[symbol].
        7. Store next earnings date in self._earnings_dt[symbol].

compute(row, context):
    score = context.news_sentiment_scores.get(symbol, 0.0)
    return SignalOutput(score=score, ...)
```

Rate courtesy sleep: 0.12 s per symbol ≈ 8 calls/s, safely under Finnhub's 60/min
free-tier ceiling. This paces the Finnhub *fetch* calls only — the FinBERT/lexicon
*scoring* step (see below) is local and batched, so it is never subject to this delay.

---

## Regime Gate

News/social sentiment is noisiest exactly when it matters least — during systemic panics,
headline flow reflects fear and forced deleveraging rather than idiosyncratic company
information. `NewsCatalystSignal.is_active_in_regime()` returns `False` (fully suppressing
the module's contribution to `final_score`/`score_log`, per `SignalAggregator.aggregate()`'s
handling of regime-gated modules) whenever:

- `macro.market_regime` is `RECESSION` or `CREDIT EVENT`, OR
- `macro.vix > 30.0`

This mirrors `signals/rsi2_mean_reversion.py`'s regime gate exactly (same thresholds), rather
than inventing a parallel mechanism. `compute()` still runs every cycle regardless — its raw
score remains visible in the aggregator's `outputs` dict for introspection — but a suppressed
cycle contributes nothing to the aggregate score, the explainer log, or `meta_label_composite`.

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
    Score in batches of FINBERT_BATCH_SIZE headlines per forward pass (score_headlines()),
    returning the full 3-class softmax {"positive", "neutral", "negative"} per headline.
ELSE (transformers ImportError OR FINBERT_ENABLED=False):
    Lexicon fallback, per headline:
        score = (positive_word_count − negative_word_count)
                / max(1, positive_word_count + negative_word_count)
        represented as the same softmax-shaped dict for API uniformity
        (see _lexicon_softmax — not a calibrated probability distribution).
```

The lexicon uses ~80 domain-specific words: "bullish", "beat", "exceeded", "acquisition"
(positive) vs. "miss", "downgrade", "investigation", "lawsuit" (negative).

Both paths ultimately collapse to a directional score ∈ [−1, +1] via
`positive − negative` net probability mass (`_distribution_to_signed`) wherever a single
scalar is needed (e.g. averaging a symbol's headlines, or the legacy `_score_headline()`
contract). The FinBERT path is significantly more accurate but requires a ~400 MB model
download on first use and a CPU/GPU fast enough for batched inference — see
`requirements-optional.txt` for the CPU-only PyTorch pin that activates it (`torch>=2.0`;
`transformers>=4.35.0` alone, already in `requirements.txt`, has no backend to run without
it and silently falls back to the lexicon).

### Batched scoring (`score_headlines()`)

Headlines are no longer scored one at a time. `signals.news_catalyst.score_headlines(
headlines, pipeline=...)` encodes `settings.FINBERT_BATCH_SIZE` (default 16) headlines per
forward pass, truncating each to 512 characters (matching the pipeline's own
`truncation=True, max_length=512`), and returns one full softmax dict per headline in
input order. `_score_headline(headline, pipeline)` — the pre-batching single-headline
function every existing caller (e.g. `data/sentiment_sources.py`) still depends on — is now
a thin, cache-bypassing wrapper around `score_headlines()` for a single item; its
signature and `float ∈ [-1, 1]` return contract are unchanged.

### Content-hash score cache (`finbert_score_cache`)

Without a cache, the same unchanged headline gets re-scored by FinBERT every cycle it
remains inside the `NEWS_LOOKBACK_DAYS` window. `score_headlines()` now checks
`data/historical_store.py`'s `finbert_score_cache` table first — keyed on a SHA-256 hash
of the raw headline text, **not** a date — and only scores cache misses, writing fresh
results back before returning. This is content-hash, not time-based, keying: a lookup for
unchanged text is not a lookahead risk, since the score is a pure, deterministic function
of the text alone and a cycle can only ever look up a hash for a headline it has *already*
fetched from Finnhub this cycle (see the `finbert_score_cache` DDL comment and
`tests/test_news_catalyst.py::TestFinbertScoreCacheLookaheadSafety` for the explicit proof).
Gated by `settings.FINBERT_SCORE_CACHE_ENABLED` (default `True` — a pure performance
optimization with identical outputs) and degrades gracefully to "score fresh, skip the
cache" when `settings.HISTORICAL_STORE_ENABLED` is `False` or the DB is otherwise
unavailable.

**New settings:**

| Setting | Default | Purpose |
|---------|---------|---------|
| `FINBERT_BATCH_SIZE` | `16` | Headlines per FinBERT forward pass in `score_headlines()`. Only consulted when a real pipeline is loaded. |
| `FINBERT_SCORE_CACHE_ENABLED` | `True` | Cache FinBERT/lexicon scores by headline content hash so unchanged headlines aren't re-scored every cycle. |

---

## Failure Modes

| Failure | Behaviour |
|---------|-----------|
| `FINNHUB_API_KEY` absent | `pre_compute` skips all Finnhub calls; every symbol gets `sentiment = 0.0`. Module is informationless, not broken. |
| Finnhub 429 rate limit | `FinnhubProvider` applies exponential backoff (2 s) + retry once; on persistent 429, returns empty news list. Score = 0.0 for that symbol. |
| `transformers` ImportError (no PyTorch) | Automatic fallback to lexicon. Logged at INFO, not WARNING — this is a supported configuration. |
| FinBERT batch inference error/OOM on CPU | An exception inside `score_headlines()`'s batch call is caught; that whole batch falls back to the lexicon per-headline. |
| `finbert_score_cache` read/write failure | Logged at DEBUG and swallowed; `score_headlines()` scores fresh instead (CONSTRAINT #6) — never blocks scoring. |
| No headlines in lookback window | score = 0.0 (no news ≠ neutral news, but we treat it as neutral to avoid punishing quiet periods). |
| Symbol with no Finnhub coverage | empty news list → score = 0.0. |

---

## Multi-Source Credibility Blend

**Opt-in master switch:** `pre_compute()`'s multi-source ingestion step (the write side —
`_run_multi_source_ingestion()`, calling `data/sentiment_sources.py`'s `CompositeSentimentSource`)
is gated behind `settings.SENTIMENT_INGESTION_ENABLED`, **default `False`**. Until an operator
sets it `True` in `.env`, this is a complete no-op — no network call is attempted for any symbol,
and `sentiment_ingestion_audit` never accumulates a single row no matter how much time passes.
This exists because two of the four sources (Yahoo RSS, GDELT) need no API key, so — unlike
Finnhub/Reddit/EDGAR, which already degrade to a no-op when their credentials are absent — they
have no other way to stay quiet by default. **Turning this on is the one action required** for
the point-in-time archive to start accumulating toward `SENTIMENT_PIT_MIN_MONTHS`; nothing else
needs to be done afterward — it runs automatically every cycle from then on.

**Backfill: waiting isn't the only way to reach archive depth.** GDELT, SEC EDGAR, and Finnhub
all have genuine historical archives — `scripts/backfill_sentiment_history.py` can pull real,
already-existing history (default: the last 5 months) into `sentiment_ingestion_audit` right now,
with **zero credibility bias**, since all three are policy-trusted institutional sources
(`credibility_weight=1.0` regardless of when they're scored). Reddit is also backfillable but
carries a real caveat: a backfilled post's `S_authority` reflects the author's account state
*today*, not at post time. Yahoo RSS cannot backfill at all (a live feed, no historical archive).
`HistoricalStore.get_sentiment_archive_depth_by_source()` reports depth per source, so a future
Phase 5 validation run should check institutional-source depth and Reddit's depth *separately*
rather than one blended number that would overstate confidence in the weaker component.

`pre_compute()` additionally reads the current trading day's aggregate from
`HistoricalStore.get_sentiment_aggregate_by_symbol()` — populated at ingest time by
`data/sentiment_sources.py`'s `CompositeSentimentSource` (Yahoo RSS/GDELT/Reddit/EDGAR/Finnhub
documents, deduplicated, trading-day-rolled) and `signals/credibility.py`'s per-document
credibility scoring (`S_authority`/`S_humanity`/`S_verification` sub-scores → a
`credibility_weight` in `[0.1, 1.0]` that discounts low-authority/bot-like social documents at
the aggregate level, before this signal ever sees them).

`compute()`'s final score is:

```
score = (1 - w) * headline_score + w * credibility_weighted_social_score
```

where `w = settings.SENTIMENT_SOCIAL_BLEND_WEIGHT` (default 0.4) — the two weights always sum
to 1.0 by construction. When no social documents exist for a symbol this trading day, `w`'s
contribution is skipped entirely and the score is headline-only (`News_Sentiment`'s own meaning
is never altered by this blend).

Institutional/editorial sources (Finnhub, Yahoo RSS, GDELT, EDGAR) carry no author/follower
metadata and are treated as fully credible (`credibility_weight = 1.0`) by policy, not by a
fabricated per-document measurement — this is a deliberate modeling choice documented in
`signals/credibility.py`'s module docstring, not an attempt to infer authority for editorial copy.

---

## Config / New Columns

Added to `config.COLUMN_SCHEMA`:
- `News_Sentiment` — average headline score ∈ [−1, +1] (Finnhub-headline component only, unchanged meaning)
- `Earnings_Date` — next earnings date as ISO string or empty
- `Credibility_Weighted_Sentiment` — mean credibility-weighted social score for the trading day (NaN if no social documents)
- `Bot_Activity_Ratio` — mean `is_bot` flag across the trading day's social documents (percent)
- `Aggregated_Source_Credibility` — mean `credibility_weight` across the trading day's social documents

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
