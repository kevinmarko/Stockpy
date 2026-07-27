# Feature: Semantic Related Sector Selection

**File (heat term):** `data/sector_selection_heat.py` (`compute_spec_sector_heat`)
**Master switch:** `settings.SECTOR_SELECTION_ENABLED` (default `False`)
**Status:** heat term only so far — the semantic similarity term, the
persisted `sector_correlations` store, and `sector_selection_engine.py`'s
daily ranking pass ship in a follow-on PR.

**This is NOT a registered `SignalModule`.** It does not appear in
`settings.SIGNAL_WEIGHTS` and does not feed `StrategyEngine.
evaluate_security()`. It is a standalone ranking feature for selecting
which upstream/downstream industry sectors' sentiment to feed into a
forecasting model — see the source methodology this is modeled on
(MDPI *Mathematics*, 2024, "Incorporating Multi-Source Market Sentiment and
Price Data for Stock Price Prediction").

See also: [`docs/signals/sector_heat_factor.md`](sector_heat_factor.md)'s
"Two features, one name" section — this feature's Sector Heat Factor is a
**different construct** from that file's `Sector_Heat_Factor` dashboard
column, despite the shared name.

## Rationale

A target stock's own news/comment coverage is often sparse, especially on
low-visibility trading days. This feature selects the top-N most relevant
upstream/downstream industry sectors (by a combination of semantic
similarity to the target's business description and how much attention
each candidate sector is currently getting) so that sector-level sentiment
can supplement a thin single-stock signal.

## Final ranking formula

```
correlation_coefficient = cosine_similarity(target, sector) * SHF(sector)
```

Sectors are ranked descending by `correlation_coefficient`; the top N
(default 3) are selected. The `cosine_similarity` term (SBERT max-pooled
embeddings) ships in a follow-on PR; this document currently covers the
`SHF` term only.

## Sector Heat Factor (SHF)

```
x = min_max_normalize(numNews + Review)   # normalized ACROSS candidate sectors
SHF = a * exp(-(x - b)^2 / (2 * c^2))
```

Empirically calibrated defaults: `a=0.8` (`SECTOR_SELECTION_HEAT_A`),
`b=1.0` (`SECTOR_SELECTION_HEAT_B`), `c=0.6` (`SECTOR_SELECTION_HEAT_C`).
`numNews`/`Review` are summed per-sector document counts (across that
sector's member tickers) over a trailing `SECTOR_SELECTION_HEAT_LOOKBACK_
DAYS` (default 22) **trading**-day window, sourced from
`sentiment_ingestion_audit` via `HistoricalStore.
get_sentiment_daily_by_source_class` (Sentiment Source Class Phase 0) and
classified news-vs-comment by `data.sentiment_source_class.classify_source`.

## Honest degradation — the Review term (CONSTRAINT #4)

The comment/investor-forum ("Review") volume term has no genuinely active
data source in this repository today (Reddit requires credentials that are
typically unset; StockTwits doesn't exist yet). `compute_spec_sector_heat`
checks whether the comment channel has **ever** produced a single document
(via `HistoricalStore.get_sentiment_archive_depth_by_source`, an
all-time, not per-window, check). If it never has:

- every sector's `review_volume` is `NaN`, not a fabricated zero,
- the SHF input degrades to `numNews` alone (news-only volume),
- every sector's result carries `degraded_reason="review_unavailable"`.

A sector whose member tickers were never ingested at all (independent of
the review question) gets `news_volume=NaN`, `review_volume=NaN`,
`shf=NaN`, `degraded_reason="no_volume_observed"` — excluded entirely from
the cross-sector min-max normalization rather than folded in as a zero
(an unknown quantity cannot be compared against known ones).

Once the comment channel has produced at least one real document (see
Sentiment Source Class Phase 4/5's Reddit-enablement and StockTwits work),
`review_volume` reflects genuine per-window sums, including genuine zeros
for a quiet window on an otherwise-active channel — `degraded_reason`
becomes `None` for every sector with observed volume.

## Sector membership and descriptions

Sector membership (`{symbol: sector}`) is supplied by the caller — the
existing `forecasting/data/ticker_sectors.csv` (regenerable via
`scripts/build_ticker_sector_map.py`) is the intended source, reused rather
than duplicated in a new table. Sector *descriptions* (used by the
semantic-similarity term) will live in a new committed
`data/sector_descriptions.yaml` in the follow-on PR — this module does no
description loading of its own.
