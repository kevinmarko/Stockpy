# FMP-Primary-Source Audit — Walkthrough

## Request

> make sure all tests, charts, LLMs and anything else is using FMP as the main source

## Approach

Rather than trust `settings.py`'s own docstrings (which already claim FMP
is primary everywhere), this audit independently verified the actual
routing logic, running code, and test coverage — per the
`stockpy-quant-integrity` skill's core rule: a claim isn't a verification.

Four parallel research agents covered: core data-layer routing
(`data_engine.py`/`data/market_data.py`), webapp chart/quote endpoints, LLM
and MCP grounding paths, and test-suite assumptions. Three parallel
implementation agents then fixed the concrete, real gaps the research
surfaced, each independently re-verified (real pytest runs, not inferred
success) before being trusted.

## Bottom line

FMP genuinely is the primary source almost everywhere it's configured to
be — `CompositeProvider` (quotes/bars/fundamentals), the news-catalyst
dispatchers, the screener/peers/universe endpoints, and every chart-serving
webapp route all correctly trace back to `settings.MARKET_DATA_PROVIDER=
"fmp"`/`FUNDAMENTALS_SOURCE="fmp"` as their primary path, with documented,
honest fallback chains on failure — not fabrication.

That said, the audit found and fixed real issues:

### 1. A genuine CONSTRAINT #4 violation (fabricated data)

`pilots/volatility_surface.py::get_volatility_surface_data()` silently
substituted a hardcoded placeholder spot price (`$500.0` for SPY, `$150.0`
for anything else) whenever the live quote call failed — indistinguishable
downstream from a real FMP-sourced quote, and inconsistent with the
sibling `GET /data/options/chain/{symbol}` handler, which correctly
503s rather than fabricating. Fixed to let the function's own already-
correct fallback ladder run instead (infer spot from the option chain's
median strike, or return an honest `missing_data: True`). Pinned with a
new regression test.

### 2. Five MCP tools bypassing FMP entirely

`get_ticker_context`, `run_backtest`, `plot_equity_curve`,
`plot_portfolio_equity`, and `get_portfolio_summary` in
`investyo_mcp_server.py` all called `yfinance` directly — the one
un-migrated corner of an otherwise fully FMP-routed MCP server. All five
now go through `data.market_data.get_provider()`, with a new
`_period_to_lookback_days()` helper preserving each tool's existing
yfinance-style `period` string signature. Every error/empty-result message
is unchanged. ~20 dependent tests were rewritten to mock the new path.

### 3. A real test-coverage gap

Every existing `CompositeProvider` test monkeypatched `MARKET_DATA_PROVIDER`
explicitly — several even hardcoded the *pre-FMP* baseline as their own
"default" fixture. No test proved the platform's actual, untouched
defaults route to FMP. Closed with a new
`TestCompositeProviderGenuineDefaultRouting` class that reads the real
`settings.py` field declarations and asserts the unpatched singleton
routes quotes/bars/fundamentals to FMP.

### 4. A judgment call, resolved with evidence — not a fix

`settings.SENTIMENT_SOURCES`'s default excludes `fmp_news`. Investigated
rather than reflexively "fixed": adding it would double-fetch the exact
same FMP `stock_news` endpoint `NewsCatalystSignal.pre_compute()` already
calls FMP-first every cycle — the identical reasoning the codebase already
uses to exclude `finnhub` from the same list. Left the default as-is and
documented the `fmp_news` exclusion explicitly (it was previously an
unexplained gap that read like an oversight).

### 5. One disclosed, unfixable risk

`scripts/verify_fmp_bars.py` — the hard gate this repo's own docs call
mandatory before trusting `FMP_BARS_ADJUSTMENT` — has never been run
against a live account, despite `FMP_BARS_ENABLED=True` being the live
default. This sandbox has no live-market network access, so this audit
could not close that gap — it's surfaced prominently rather than silently
left as-is.

## Verification

Every change was independently re-run by the orchestrating session (not
just reported by the implementing subagent):

- `tests/test_volatility_surface.py`: 12/12 passed
- `tests/test_market_data.py`: 121/121 passed
- `tests/test_investyo_mcp_server.py` + `tests/test_investyo_mcp_widgets.py`: 367/367 passed
- `tests/test_settings.py` + `tests/test_sentiment_sources.py`: 137/137 passed
- Combined run across all touched test files: 637/637 passed
- Full offline suite (`pytest -m "not network"`): see PR body for final result

## Documentation updated

- `docs/architecture/observability-and-apis.md` — new bullet for the MCP-tool FMP-routing fix
- `CLAUDE.md`/`AGENTS.md` (auto-synced) — corrected the `CompositeOptionsProvider` misattribution, added a summary bullet for this whole audit
- `settings.py` — `SENTIMENT_SOURCES` field description now explains the `fmp_news` exclusion explicitly
