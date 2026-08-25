# Feature: Semantic Related Sector Selection

**File (heat term):** `data/sector_selection_heat.py` (`compute_spec_sector_heat`)
**File (orchestration):** `sector_selection_engine.py` (`run_sector_selection`)
**Master switch:** `settings.SECTOR_SELECTION_ENABLED` (default `False`)
**Status:** fully wired. The semantic similarity term, the persisted
`sector_correlations` store (`data/sector_correlation_store.py`), and
`sector_selection_engine.py`'s daily ranking pass all shipped in follow-on
PRs — but for a while none of them was ever actually *called* from either
orchestrator, so with the flag on the engine still never ran and the webapp
Sector Selection screen permanently showed its empty state for every
symbol. `pipeline/production_steps.py::_apply_sector_selection`, called
from `StrategyEvalStep.run`, closes that gap: once
`SECTOR_SELECTION_ENABLED=true`, every tracked symbol whose most recent
persisted ranking isn't from today's trading day gets recomputed once per
day (a per-symbol `get_latest()` freshness check prevents duplicate rows
from piling up under `main.py --interval N`).

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
(default 3) are selected. The `cosine_similarity` term (SBERT max-pooled, or
OpenAI, embeddings via `data/sector_embeddings.py::resolve_target_description`
/`embed_text`/`cosine_similarity`, selected by `settings.
SECTOR_SIMILARITY_EMBEDDER`) is now fully wired — this document previously
said it "ships in a follow-on PR" and covered the `SHF` term only; that PR
landed and this section is corrected accordingly. `NaN` (never a fabricated
value) whenever either input is unavailable — see "Honest degradation" below
for the SHF side and "No-lookahead-bias fix" below for the similarity side.
A row's `degraded_reason` records why: `similarity_reason` (a similarity-side
blocking failure — `no_target_description`/`no_embedder`/
`no_sector_description`/`embedding_failed`) takes priority over
`heat_degraded_reason` (SHF's own, non-blocking provenance flag,
e.g. `"review_unavailable"`) whenever the two disagree, fixed 2026-08-24 — see
below.

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
semantic-similarity term) live in the committed `data/sector_descriptions.yaml`
(`sectors:` block, keyed by the same sector names `ticker_sectors.csv` uses —
enforced by `tests/test_sector_descriptions.py::TestSectorDescriptionsKeySuperset`).
A target symbol's own description is resolved by
`data.sector_embeddings.resolve_target_description`, in priority order: (1)
an operator-authored override in the same YAML's `targets:` block, (2)
`fundamentals_history.raw_json['longBusinessSummary']` (read-only, never a
live fetch), (3) `None` — never synthesized from ticker+sector name
(CONSTRAINT #4).

## No-lookahead-bias fix (2026-08-24)

`resolve_target_description` previously had NO point-in-time awareness at
all — it always resolved the target's CURRENT business description
regardless of what `as_of` date a caller was scoring, defeating the
lookahead-safety design the Sector Heat Factor term above already has (that
term correctly threads `as_of` into its trailing-window query). Dormant at
the time it was found (`run_sector_selection`'s one real production caller,
`pipeline/production_steps.py`, never scores anything but "now," where
current and as-of descriptions are the same thing) but a real gap for the
first backtest/replay caller that would ever exercise it. Fixed via a new
point-in-time lookup, `HistoricalStore.get_fundamentals_raw_json_asof(symbol,
as_of_date)` (mirroring the existing `get_fundamentals_asof` convention —
`report_date`, the causal filing date, not `as_of`, the cache-write
timestamp, is the filter column), threaded through
`resolve_target_description(..., as_of=...)` and
`_rank_one_target`/`run_sector_selection`. `run_sector_selection` now passes
`as_of=resolved_now` internally on EVERY call (not only when a caller
supplies an explicit historical `as_of`), so this has a real, disclosed
effect on the live daily pipeline too — see
[`docs/known_issues/sector_selection_similarity_lookahead.md`](../known_issues/sector_selection_similarity_lookahead.md)
for the full write-up, including the verified (not assumed) analysis of
which real fundamentals-provider payload shapes are and aren't affected.
