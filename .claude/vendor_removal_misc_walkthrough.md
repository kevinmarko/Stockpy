# Vendor removal (Finnhub, Reddit, Sentry, google-cloud-language) — walkthrough

Branch `remove-finnhub-reddit-sentry`, operator-approved plan "PR V3" (2026-09-29).

## What was removed

**Finnhub**
- `signals/news_catalyst.py`: `build_finnhub_client`, `fetch_company_news`, `fetch_next_earnings`, the
  `_score_via_finnhub` alias and every Finnhub branch. `fetch_company_headlines` /
  `fetch_next_earnings_any` are FMP-only; when FMP is off, unconfigured, failing or empty they return
  the same honest empty result as before (`[]` / `None`). `pre_compute`'s provider gate is
  `FMP_NEWS_ENABLED` + `FMP_API_KEY` only. The `_provider` tag is now only `"fmp"`.
- `data/sentiment_sources.py`: `FinnhubSentimentSource` and its `_SOURCE_REGISTRY` / `_SOURCE_PRIORITY` entries.
- `data/market_data.py`: deprecated `FinnhubProvider` and its `_SlidingWindowRateLimiter` (only Finnhub used it).
- `settings.py`: `FINNHUB_API_KEY`, `FINNHUB_RATE_LIMIT_PER_MIN`; listings in `shared/env_io.py`,
  `pilots/settings_domains.py`, `api/pilots_api.py` tunables, `shared/help_content.py`, webapp `types.ts` /
  `mock.ts` / `helpContent.ts`. `DataSource.FINNHUB` became `DataSource.FMP` in `shared/dependency_map.py`.
- `scripts/backfill_news_history.py` is FMP-only (its `client` parameters are gone).
- `signals/credibility.py`: `"finnhub"` dropped from `_INSTITUTIONAL_SOURCES` (`"fmp_news"` was already there).
- `requirements.txt`: `finnhub-python`. `Gravity AI Review Suite.py` step 26 lost its Finnhub-only checks.

**Reddit**
- `RedditSource` + registry entry, `REDDIT_*` settings and all their listings, the Reddit backfill caveat in
  `scripts/backfill_sentiment_history.py` (default backfill sources are now `gdelt,edgar`),
  `SENTIMENT_SOURCES` default `yahoo_rss,gdelt,edgar`.
- `data/sector_selection_heat.py` only referenced Reddit in a docstring (it works off `source_name` strings), so only the text changed.

**Sentry**
- `git mv observability/sentry_integration.py legacy/observability/sentry_integration.py`, the test moved to
  `legacy/tests/`, the `init_sentry` call in `desktop/orchestrator_daemon.py`, `SENTRY_*` settings and
  `shared/env_io.py` entries, `sentry-sdk` in `requirements-optional.txt`. Recorded in `legacy/README.md`.

**google-cloud-language**: removed from `requirements.txt` (never imported). `google-auth` stays: `google-genai`
(`llm/providers.py`, `api/data_api.py`, `api/ws_api.py`) needs it; a later PR removes google-genai.

## Why there is no behavior change on the live config

- `FMP_NEWS_ENABLED=true` with an FMP key: FMP was always served first, the Finnhub fallback was unreached.
- `REDDIT_CLIENT_ID` empty: `RedditSource.fetch` returned `[]` immediately.
- No `SENTRY_DSN`: `init_sentry` returned `False` before importing `sentry_sdk`.
- `google.cloud.language` was never imported.
- `Settings` uses `extra="ignore"`, so leftover `FINNHUB_*` / `REDDIT_*` / `SENTRY_*` lines in `.env` are harmless.

## Judgment calls (left in place on purpose)

- `SENTIMENT_COMMENT_SOURCES` default keeps `reddit`, and `data/sentiment_source_class.py::_KNOWN_NEWS_SOURCES`
  keeps `finnhub`, so historical `sentiment_ingestion_audit` rows written by the removed sources still classify.
- An operator `.env` that lists `reddit`/`finnhub` in `SENTIMENT_SOURCES` gets one "unknown source, skipping"
  warning at construction time (existing behavior for unknown names) instead of a crash.
- Test fixtures using `"finnhub"` / `"reddit"` purely as `source_name` labels were left alone.
- The 0.12 s per-symbol courtesy sleep in `NewsCatalystSignal._score_via_provider` stays (pacing unchanged).
- Historical narrative in `docs/FEATURE_TIER_HISTORY.md`, `docs/known_issues/*`, older `.claude/*` files still names these vendors.

## Tests run

See the PR description for the exact commands and results. Test changes: removed `TestFinnhubProvider`,
`TestSlidingWindowRateLimiter`, `TestFinnhubRateLimitAndCache` (`tests/test_market_data.py`),
`TestFinnhubSentimentSource` and `TestRedditSource` (`tests/test_sentiment_sources.py`),
Finnhub fetch-helper tests (`tests/test_news_catalyst.py`, dispatcher tests rewritten as FMP-only), the Reddit
caveat test; `tests/test_backfill_news_history.py` rewritten for the FMP-only script; webapp mock/test fixtures updated.
Generated files regenerated: `docs/settings_field_census.{json,md}`, `docs/settings_liveness.json`,
`cli_introspect/command_manifest.json`, `completions/investyo.zsh`.
