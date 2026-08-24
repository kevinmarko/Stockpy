# Walkthrough — sentiment dedup / confidence / NaN guard fix

## What changed and why

### 1. `data/sentiment_sources.py` — cross-source duplicate counting

`CompositeSentimentSource.fetch_all()`'s existing `_dedup_key()` hash includes
`source_name`, so it only ever caught a duplicate document returned twice by the *same*
source (e.g. overlapping fetch windows). It could not catch the same underlying wire story
picked up by two *different* sources — e.g. Yahoo RSS and Google News both carrying an
identical headline — which meant a widely-syndicated story could silently contribute twice
to the credibility-weighted sentiment aggregate that feeds `NewsCatalystSignal`'s social
blend.

The fix adds a second dedup pass, run per `trading_day` (so the same story reported on two
different days is correctly treated as distinct, real coverage), reusing the exact
technique and threshold `GoogleNewsRSSSource` already uses internally for its own
within-source fuzzy-title dedup (`_normalize_title` + 0.8 Jaccard token-overlap). Sources
are polled in priority order, so when two sources carry the same story, the higher-priority
source's copy survives. A detected duplicate is skipped *before* the per-cycle document
budget counter is incremented, so it can never wrongly crowd out a genuinely distinct
document from a lower-priority source.

### 2. `signals/news_catalyst.py` — misleading `confidence` on zero-headline days

`compute()`'s old `confidence = 0.75 if symbol in self._news_scores else 0.5` reported the
same "high confidence" 0.75 on a genuine zero-headline/fetch-error day as on a day with
real scored headlines — because `_news_scores[symbol]` is always populated with a
deliberate `0.0` fallback whenever a provider is configured, regardless of whether the
symbol actually had any real headlines that cycle.

`_news_archive_scores[symbol]` already stays honestly `NaN` on that same zero-headline day
(this was already correct, feeding `news_history` honestly) — `confidence` is now derived
from that field instead: `0.75` when the archive score is a real (non-NaN) value, `0.5`
otherwise. `confidence` is not consumed by `SignalAggregator`'s actual weighted-sum trading
math (confirmed by reading `signals/aggregator.py`'s aggregation loop), so this is a
diagnostics/LLM-commentary-facing honesty fix, not a trading-math change.

### 3. `signals/news_catalyst.py` — unguarded NaN read in the live social blend

`compute()`'s `social_entry.get("credibility_weighted_sentiment", 0.0)` had no NaN guard,
unlike the parallel `_build_archive_scores()` read of the identical field, which already
checks `not math.isnan(...)`. `credibility_weighted_sentiment` can be NaN when
`HistoricalStore.get_sentiment_aggregate_by_symbol`'s `weight_sum` underflows
(`<= 1e-12`) with a document still present — not currently reachable in production since
`credibility_weight` floors at 0.1, but an unguarded NaN here would propagate into
`SignalOutput.score` and corrupt `final_score` for every other signal module that cycle via
the aggregator's unguarded `score * weight` multiply.

Fixed by adding the same guard `_build_archive_scores()` already has: a NaN social score
now degrades to the headline-only score instead of ever returning NaN.

## Test coverage added

- `tests/test_sentiment_sources.py::TestCrossSourceDedup` — 5 new tests covering exact
  cross-source duplicates, near-identical (casing/whitespace) duplicates via the Jaccard
  path, genuinely distinct stories (not deduped), the same story on two different trading
  days (not deduped), and that a detected duplicate doesn't consume the document budget.
- `tests/test_news_catalyst.py::TestSignalCompute` — 4 new tests (zero-headline-day
  confidence, real-headline-day confidence, NaN-social-score guard, finite-social-score
  no-regression sanity check) plus one existing test (`test_compute_reads_cached_score`)
  updated to set `_news_archive_scores` so it reflects a genuine real-headline day under
  the new honest semantics, preserving its original intent.

## Verification

- `python3 -m ruff check . --select=F821,F822,F823,E9` (the repo's genuine-bug-only lint
  gate) — all checks passed.
- `python3 -m pytest tests/test_sentiment_sources.py tests/test_news_catalyst.py tests/test_pilots_news_catalyst.py -q`
  — 249 passed, 0 failed.
- Full offline suite (`pytest -m "not network and not slow"`): 12031 passed, 9 pre-existing
  failures unrelated to this change. Verified via `git stash` (running the same 9 tests
  against clean `main`) that all 9 fail identically without any of this PR's changes
  applied — `test_data_api_chat.py::TestMultiProviderRouting` ×3, `test_gemini_live_chat.py`
  ×2, `test_portfolio_context.py::TestSelfIndexing::test_rag_index_lookback_days_default`,
  `test_sector_selection_review_populated.py`, `test_settings_liveness.py` (flaky — passed
  on the clean-baseline re-run), and `test_forecast_backfill.py::test_kill_mid_step_5_leaves_partial_export_with_completed_combos`
  (a timing-sensitive subprocess-kill test, also reproduces identically on clean `main`).
