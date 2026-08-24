# Robinhood Closed-Trade Ingest — Walkthrough

## What was broken

The operator asked why 4 real Robinhood sells from the last ~6 months (CMCL, ARCC, PBF, IVR
— confirmed directly against their live account via read-only MCP calls) appeared nowhere in
Stockpy: not the webapp Portfolio screen, not the advisory pipeline, not any trade journal.

Root cause, confirmed in code:

1. `data/robinhood_orders.py`'s FIFO-reconstruction engine existed, was tested, and was
   Gravity-audited — but its only production caller (`pilots/realized.py`) deliberately
   injects an empty fetcher to avoid triggering a login on a web request. Nothing else ever
   called it with a real one.
2. Even a real call would have crashed: `fetch_filled_orders(orders_fetcher=None)` imported
   `data.robinhood_portfolio._login`, a symbol the 2026-08 device-approval login rewrite had
   already removed. The `ImportError` was silently swallowed and degraded to an empty cache.
3. `main.py`'s ticker universe (`held ∪ WATCHLIST ∪ watchlist.txt ∪ discovered`) drops a
   symbol the instant Robinhood reports 0 shares — no "sold" state, just gone — which is why
   the advisory pipeline stopped analyzing every one of these symbols the moment they were sold.

## What was built

**Ingest repair.** The Robinhood login worker (`data/robinhood_login_worker.py`) now fetches
and persists real order history in the SAME authenticated session as its existing account-
snapshot refresh — one device-approval push, both artifacts. Bounded by a wall-clock budget
(`RH_ORDER_INGEST_BUDGET_SECONDS`) and a resolve-count cap (`RH_ORDER_SYMBOL_RESOLVE_MAX`)
with a durable resolver cache so repeat ingests are cheap.

**Durable store.** `data/broker_fills_store.py` persists Robinhood fills (keyed by their real
`order_id`) rather than reconstructed trades — reconstructing FIFO closed trades from a
*fill* union is stable across API-window rolls in a way reconstructing from a *trade* dedup
key structurally cannot be. Idempotent re-ingest, verified.

**The single biggest design decision, made explicitly with the operator up front:** broker-
ingested trades must NEVER touch position sizing. This turned out to be load-bearing, not
theoretical — with `transactions_store.trades` at 0 rows today, the platform's aggregate
Kelly path is silently returning weight 0.0 for every symbol; the operator's real 17-round-
trip history would have moved that to 0.567 and turned live sizing ON platform-wide in one
commit had it landed in that table. `data/broker_fills_store.py` structurally cannot reach
`transactions_store` or any `sizing/`/`execution/` module — enforced by an AST-guard test,
not just a docstring promise.

**Universe retention.** A sold symbol now stays in the analysis universe for
`CLOSED_POSITION_RETENTION_DAYS` (default 180) after its last real sell, keyed off the
broker fill history — not internal paper trades. Two real ordering traps were found and
fixed while implementing this: retention must union in AFTER the symbol-rating auto-drop
subtraction (or it gets immediately re-dropped) and AFTER the empty-universe fallback
decision (or it silently suppresses `DEFAULT_TICKERS`). A third, previously undocumented bug
was found and fixed in the same pass: `async_sync_now()` was about to permanently bake every
retained symbol into `.env`'s `DEFAULT_TICKERS` (which never expires), silently converting a
180-day window into forever.

**New Trade History screen.** `GET /portfolio/trade-history` + `pilots/trade_history.py`
reads the durable store with real pagination — the pre-existing `GET /portfolio/realized`
stays cache-only and capped at 100 rows for the Portfolio screen's summary panel; this is
the full ledger. Reachable via nav, a Marketplace tile, and a new "See all →" link from the
Portfolio screen.

**Opt-in analytics.** `evaluation_engine.py` can optionally (`EVAL_BROKER_TRADES_ENABLED`,
default off) fall back to broker trades for MAE/MFE/Edge Ratio on symbols with no internal
trade history — internal history always wins, and this path is completely separate from the
sizing-isolation guarantee above.

## Verification

Full offline gate green: ruff genuine-bug lint clean, 12,195 backend tests passing (8
pre-existing, unrelated failures confirmed via `git stash` diff — 3 were actually caused by
this change, a stale committed settings census/liveness artifact, and were fixed by
regenerating them), webapp typecheck/1868-test-suite/production-build all clean.

The plan's own end-to-end verification script (§10 of the implementation plan) is written
for the operator to run against their real account and confirm the 4 known sells reconstruct
with the exact expected P&L, that a second ingest is byte-idempotent, and that sizing is
provably unchanged.
