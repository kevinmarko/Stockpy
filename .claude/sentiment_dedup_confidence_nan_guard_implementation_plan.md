# Fix 3 sentiment/news-scoring audit findings — Implementation Plan

## Context

An audit of `signals/news_catalyst.py` and `data/sentiment_sources.py` surfaced three
independently-confirmed findings (none corrupt today's actual weighted-sum trading math,
but all are worth closing):

1. **Cross-source duplicate counting** — `_dedup_key()` includes `source_name`, so
   `CompositeSentimentSource.fetch_all()`'s dedup only catches a duplicate *within* one
   source, never the same wire story picked up by two *different* sources (e.g. Yahoo RSS
   and Google News both carrying an identical headline). A widely-syndicated story can
   contribute more than once to the credibility-weighted sentiment aggregate.
2. **Misleading `confidence` on zero-headline days** — `compute()`'s
   `confidence = 0.75 if symbol in self._news_scores else 0.5` reports the same 0.75 on a
   genuine zero-headline/fetch-error day as on a day with real scored headlines, because
   `_news_scores[symbol]` is always populated with a deliberate `0.0` fallback. Not consumed
   by the actual trading math (confirmed via `signals/aggregator.py`), but a
   diagnostics/LLM-commentary-facing honesty gap.
3. **Unguarded NaN read in the live social blend** — `compute()`'s
   `social_entry.get("credibility_weighted_sentiment", 0.0)` has no NaN guard, unlike the
   parallel `_build_archive_scores()` path which correctly checks `not math.isnan(...)`.
   Not currently reachable (`credibility_weight` floors at 0.1) but a real, undefended path
   to corrupting `final_score` for every other signal module that cycle if it ever were.

This is a `signals/` + `data/` tier change, so per `CLAUDE.md` it goes through a feature
branch + PR with task-scoped PR artifacts under `.claude/`.

## Fix 1 — Cross-source duplicate counting (`data/sentiment_sources.py`)

- `_dedup_key()` stays unchanged — it's the within-source exact-hash check and several
  existing tests pin its current source-name-inclusive behavior.
- Added a cross-source near-duplicate pass inside `CompositeSentimentSource.fetch_all()`,
  scoped per `trading_day` (the same story on two different days is real, distinct
  coverage, not a duplicate), applied after the exact-hash check but before
  `merged.append(doc)` / the budget increment — so a detected duplicate does not consume
  the per-cycle document budget.
- Reuses `GoogleNewsRSSSource._normalize_title` and `GoogleNewsRSSSource._SIMILARITY_THRESHOLD`
  (0.8 Jaccard token-overlap) — the same technique/threshold `GoogleNewsRSSSource` already
  uses for its own within-source fuzzy dedup — generalized across all sources' combined
  per-symbol candidate list. No new setting.
- A local `Dict[str, List[set]]` (trading_day → kept token sets) is built incrementally as
  docs are accepted in `_SOURCE_PRIORITY` order, so a lower-priority source's duplicate of
  an already-kept higher-priority document is dropped, keeping the higher-priority copy.
- Updated the module docstring and `_dedup_key`'s own docstring to describe the new pass.

## Fix 2 — Misleading `confidence` (`signals/news_catalyst.py::compute()`)

- `confidence` is now derived from `self._news_archive_scores` (which already stays
  genuinely `NaN` on a zero-headline/fetch-error day — see `_score_via_provider`) instead
  of from mere presence in `self._news_scores` (always populated with a `0.0` fallback).
- `had_real_headlines = not math.isnan(self._news_archive_scores.get(symbol, float("nan")))`;
  `confidence = 0.75 if had_real_headlines else 0.5`.
- `_news_scores`'s own fallback-`0.0` semantics are untouched — only `confidence`'s
  derivation changes.

## Fix 3 — Unguarded NaN read in live social blend (`compute()`)

- Guards `social_entry.get("credibility_weighted_sentiment", 0.0)` the same way
  `_build_archive_scores()` already guards the identical read: checks
  `not math.isnan(social_score)` before blending. On NaN, degrades to headline-only
  (`score = headline_score`, no `blend_suffix`) instead of propagating NaN.
- Defensive only — not currently reachable in production per the audit.

## Tests

`tests/test_sentiment_sources.py::TestCrossSourceDedup` (new class):
- Identical story from two sources → deduped, higher-priority source's copy kept.
- Near-identical (casing/whitespace) story from two sources → deduped via the Jaccard path.
- Genuinely distinct stories from two sources → NOT deduped.
- Same story on two different trading days → NOT deduped.
- A cross-source duplicate does not consume the per-cycle document budget (proven via a
  3-source, budget=2 scenario where a shed real document would only happen if the
  duplicate had wrongly been counted).

`tests/test_news_catalyst.py::TestSignalCompute` (extended):
- `test_compute_reads_cached_score` updated to also set `_news_archive_scores` (a genuine
  real-headline day), preserving its original intent under the new honest semantics.
- `test_compute_confidence_reflects_genuine_headlines_not_mere_presence` — zero-headline day
  (mirrors `_score_via_provider`'s own fallback shape) → `confidence == 0.5`.
- `test_compute_confidence_high_on_genuine_headline_day` — real archived score →
  `confidence == 0.75`.
- `test_compute_social_score_nan_degrades_to_headline_only` — NaN social score → finite
  headline-only score, no blend suffix.
- `test_compute_social_score_finite_still_blends` — no-regression sanity check that a real
  finite social score still blends exactly as before.

## Docs

- `data/sentiment_sources.py` module docstring: new "Cross-source dedup" section.
- `docs/signals/news_catalyst.md`: expanded "deduplicated" mention in the Multi-Source
  Credibility Blend section; added a `confidence` semantics paragraph to Failure Modes.

## Verification

- `python3 -m ruff check . --select=F821,F822,F823,E9` — all checks passed.
- `python3 -m pytest tests/test_sentiment_sources.py tests/test_news_catalyst.py tests/test_pilots_news_catalyst.py -q` — 249 passed.
- Full offline suite (`pytest -m "not network and not slow"`, `make ci`'s equivalent since
  this worktree has no local `.venv`): 12031 passed, 9 pre-existing failures — verified via
  `git stash` that all 9 fail identically on clean `main` (`test_data_api_chat.py` ×3,
  `test_forecast_backfill.py` ×1 (timing-flaky), `test_gemini_live_chat.py` ×2,
  `test_portfolio_context.py` ×1, `test_sector_selection_review_populated.py` ×1,
  `test_settings_liveness.py` ×1 (flaky — passed on the clean-baseline re-run)) — none
  related to this change.
