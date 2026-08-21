# FMP-Primary-Source Audit — Task Tracker

- [x] Load `stockpy-quant-integrity` skill
- [x] Confirm `settings.py` FMP-primary defaults (`MARKET_DATA_PROVIDER`, `FUNDAMENTALS_SOURCE`, `BROKER_BACKEND`, 14 `FMP_*_ENABLED` flags)
- [x] Dispatch 4 parallel research agents: data-layer routing, webapp charts, LLM/MCP grounding, test coverage
- [x] Synthesize findings into a table
- [x] Fix CONSTRAINT #4 fabrication bug in `pilots/volatility_surface.py` + regression test
- [x] Fix stale `get_quote` MCP docstring
- [x] Fix CLAUDE.md `CompositeOptionsProvider` misattribution
- [x] Dispatch 3 parallel implementation agents: MCP-tool FMP routing, default-routing test, `SENTIMENT_SOURCES` decision
- [x] Personally re-verify each agent's diff (not just trust the report) — ran pytest myself on every touched file
- [x] Update `docs/architecture/observability-and-apis.md` with the MCP-tool fix
- [x] Update `CLAUDE.md` with a summary bullet of this whole audit
- [x] Run full offline test suite (`pytest -m "not network"`) as final gate — 11834 passed, 0 failed, 13 skipped, 88 deselected
- [x] Regenerate `docs/settings_field_census.{json,md}` + `docs/settings_liveness.json` (stale after additive edits shifted line numbers — purely mechanical drift, confirmed via diff)
- [x] Write PR walkthrough
- [ ] Commit, push, open PR

## Explicitly out of scope / not fixed

- `scripts/verify_fmp_bars.py` never run live — no network access in this sandbox
- `data/paper_account_store.py` bypassing `CompositeProvider` — intentional, prior operator decision (FMP paper broker is FMP-coupled by design)
