# FMP-Primary-Source Audit — Implementation Plan

**Branch:** `claude/stockpy-fmp-primary-source-805fc6`
**Requested by:** operator, 2026-08-21 — "make sure all tests, charts, LLMs and anything else is using FMP as the main source"

## Scope

Audit whether FMP (Financial Modeling Prep) is genuinely the primary data
source at runtime — not just claimed in `settings.py` docstrings — across:
data-layer routing (quotes/bars/fundamentals), webapp charts, LLM/MCP
grounding paths, and test coverage. Fix any real gaps found; report and
document any that can't be fixed in this environment (no live market
network access).

## Method

Four parallel read-only research agents audited: (1) `data_engine.py`/
`data/market_data.py` routing, (2) webapp chart-serving endpoints, (3) LLM/
MCP grounding call sites, (4) test-suite coverage/assumptions. Findings
synthesized, then three parallel implementation agents fixed the concrete
gaps found, each independently verified against real pytest output (not
inferred) before being trusted.

## Findings and disposition

| # | Finding | Disposition |
|---|---|---|
| 1 | `pilots/volatility_surface.py::get_volatility_surface_data()` silently fabricated a hardcoded `$500`/`$150` spot price on quote failure (CONSTRAINT #4 violation) | **Fixed** — pass `spot_price=None` through, let `calculate_volatility_surface()`'s existing honest fallback (infer from chain, or `missing_data: True`) run |
| 2 | `investyo_mcp_server.py`'s `get_quote` docstring said "Alpaca when configured, else yfinance" — stale | **Fixed** — corrected to reflect FMP-primary default |
| 3 | 5 MCP tools (`get_ticker_context`, `run_backtest`, `plot_equity_curve`, `plot_portfolio_equity`, `get_portfolio_summary`) called `yfinance` directly, bypassing `MARKET_DATA_PROVIDER` | **Fixed** — routed through `get_provider()`; ~20 dependent tests rewritten to mock the new path |
| 4 | No test proved `CompositeProvider()` routes to FMP at genuinely untouched default settings (existing "default" tests actually tested the pre-FMP baseline) | **Fixed** — new `TestCompositeProviderGenuineDefaultRouting` class in `tests/test_market_data.py` |
| 5 | `settings.SENTIMENT_SOURCES` default excludes `fmp_news` | **Investigated, left as-is** — adding it would double-fetch the same endpoint `NewsCatalystSignal.pre_compute()` already calls FMP-first every cycle (identical reasoning to the existing `finnhub` exclusion). Documented explicitly in `settings.py` (previously unexplained). |
| 6 | `scripts/verify_fmp_bars.py` (the hard gate before trusting `FMP_BARS_ADJUSTMENT`) has never been run against a live account | **Cannot fix** — no live market network access in this sandbox. Disclosed prominently; pre-existing, already documented in `settings.py`/`docs/FMP_INTEGRATION.md`. |
| 7 | CLAUDE.md misattributed options-chain spot-price injection to `CompositeOptionsProvider` (actually happens in the `api/data_api.py` handler) | **Fixed** — corrected bullet |
| 8 | `data/paper_account_store.py` bypasses `CompositeProvider`, calling `fmp_client.batch_quote()` directly | **No fix** — intentional; the FMP paper broker is deliberately FMP-coupled (prior operator decision) |

## Files touched

- `pilots/volatility_surface.py` — fabrication fix
- `tests/test_volatility_surface.py` — regression test
- `investyo_mcp_server.py` — 5-tool FMP routing + docstring fix
- `tests/test_investyo_mcp_server.py`, `tests/test_investyo_mcp_widgets.py` — mock updates
- `tests/test_market_data.py` — new default-routing test class
- `settings.py` — `SENTIMENT_SOURCES` docstring clarification
- `CLAUDE.md`/`AGENTS.md` (auto-synced) — doc corrections + this audit's summary bullet
- `docs/architecture/observability-and-apis.md` — dedicated bullet for the MCP tool fix

## Verification

- `tests/test_volatility_surface.py`: 12/12
- `tests/test_market_data.py`: 121/121 (127 incl. new class run standalone)
- `tests/test_investyo_mcp_server.py` + `tests/test_investyo_mcp_widgets.py`: 367/367
- `tests/test_settings.py` + `tests/test_sentiment_sources.py`: 137/137
- Combined targeted run across all touched files: 637/637
- Full offline suite (`pytest -m "not network"`): run as final gate before PR
