# Vendor removal: Alpaca (2026-09-30) — walkthrough

Operator decision: Alpaca is removed entirely. Part of the vendor-removal series (see also the
Finnhub/Reddit/Sentry removal, PR #1093).

## What was removed

- **Broker:** `execution/alpaca_broker.py` archived to `legacy/execution/`. The automated
  pipeline's only broker is `FMPPaperBroker` (local SQLite paper ledger, fills at live FMP quotes
  plus `TieredCostModel`). `BROKER_BACKEND` is `Literal["fmp_paper"]`.
- **Market data:** the `AlpacaProvider` class was cut from `data/market_data.py` to
  `legacy/data/alpaca_provider.py`. Provider chain is FMP first, yfinance fallback.
  `MARKET_DATA_PROVIDER` accepts `fmp`|`yfinance`.
- **Streams/HTTP:** `data/alpaca_http.py`, `data/market_data_ws.py`, `data/websocket_streamer.py`
  archived to `legacy/data/`. The `/ws/ticks/{symbol}` WebSocket still exists but pushes REST quotes.
- **Settings removed:** `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ALPACA_REQUEST_TIMEOUT_SECONDS`,
  `ALPACA_KEY_ROTATED_DATE`, `MARKET_DATA_WS_*`.
- **Preflight:** `alpaca_configured` and `alpaca_key_rotation_recent` removed;
  `alpaca_paper_mode` renamed `paper_trading_mode`.
- **Tests:** the Alpaca tests moved to `legacy/tests/` (not collected).

## Behavior change when going live

`execution/broker_selection.py::resolve_broker_backend()` used to return Alpaca for a going-live run
(`ADVISORY_ONLY=false` and paper off). It now returns `None`: the automated pipeline places **no
orders**, logs CRITICAL and sends an alert. Real money moves only through the Robinhood execution
queue with per-trade human confirmation. `check_broker_backend_matches_live_intent` now passes with a
warning ("automated pipeline places no orders") when going live.

## `PAPER_TRADING` alias

`ALPACA_PAPER` was renamed `PAPER_TRADING`. An existing `.env` that still says `ALPACA_PAPER`
keeps working through a pydantic alias, so no `.env` edit is required.

## Removed fatal trap

`main_orchestrator.main()` used to raise `PipelineFatalError` whenever `ADVISORY_ONLY=false` and the
Alpaca keys were missing. That check is gone with the keys.

## Docs updated

`CLAUDE.md`/`AGENTS.md` (mirrors), `docs/architecture/{execution,data-layer,observability-and-apis,
webapp-and-gui,orchestration-entrypoints,testing}.md`, `docs/architecture.md`, `docs/FMP_INTEGRATION.md`,
`docs/GO_LIVE_CHECKLIST.md`, `docs/RUNBOOK.md`, `docs/HOW_TO_GUIDE.md`,
`docs/AGENTIC_TRADING_SAFETY_FRAMEWORK.md`, `docs/README.md`, `docs/test_coverage_analysis.md`,
`README.md`, and one dated line in `docs/FEATURE_TIER_HISTORY.md`. Dated historical write-ups
(`docs/known_issues/*`, older walkthroughs, `docs/VALIDATION_STRATEGY_FIX_LOG.md`) and the generated
`docs/settings_field_census.*` / `docs/settings_liveness.json` were left as-is.

## Verification

- Docs branch: `tests/test_agent_docs_sync.py` and `tests/test_help_content.py` (HOW_TO_GUIDE
  anchors still resolve) pass.
- Python and webapp verification (targeted tests, `make ci`, typecheck, browser check): see PR.
