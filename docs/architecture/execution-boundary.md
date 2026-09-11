# Execution Boundary Audit & Enforcements

**Status:** Verified 2026-09.
**Verification gates:** `tests/test_execution_boundary.py`

This document records the exact code paths that determine which symbols Autopilot is allowed to execute automated strategy trades against. It defines the boundary between "what the autonomous system looks at" and "what a human looks at," and mechanical enforcements protecting that boundary.

## 1. The Tracked Universe Boundary

Autopilot's autonomous execution scope is **strictly limited to the tracked/forecast universe**. A human merely looking at a symbol (e.g., via the Symbol Screener or the Quick Trade panel) does not make it eligible for autonomous trading.

### 1a. Tracing the Code Paths (Confirmed via code read & AST guards)

The daemon's autonomous per-cycle evaluation (`StrategyEngine.evaluate_security()`) evaluates symbols from `ctx.dashboard_df`. The index of `ctx.dashboard_df` is exactly `ctx.symbols`, which is constructed in:
- `main.py::_build_universe()`
- `pipeline/production_steps.py::AsyncDataFetchStep`

Both entry points derive their base universe exclusively from:
`data.portfolio_sync.compute_tracked_universe(watchlist, discovered, default_tickers)`

This function resolves the universe as: `held ∪ watchlist (env var & text file) ∪ discovered (where action="BUY")`, falling back to `DEFAULT_TICKERS` if empty.

Crucially, **symbols explored by a human via UI tools (e.g., `fmp_screener`) are never implicitly added to this set**. They must be explicitly added via the `/agentic/watch` endpoint (the "Add to Watchlist" button).

### 1b. Mechanical Enforcement

To prevent future regressions where an explore-surface module might be accidentally imported and injected into the core loop, an AST guard was implemented:
- **`tests/test_execution_boundary.py`** asserts that `main_orchestrator.py`, `main.py`, `pipeline/production_steps.py`, `engine/advisory.py`, and `execution/options_lifecycle.py` NEVER import explore-only surfaces like `fmp_screener`.

## 2. Policy Statement: The Auto-Scan Override

The one exception to the tracked-universe boundary is the **options auto-scan operator-supplied symbol-list override** (`/pilots/paper-broker/strategy-options/execute` with the `symbols` parameter).

**Policy (Confirmed via code read):**
This override is an **intentional, bounded, human-initiated, paper-only exception** to the tracked-universe boundary.
- **Human-initiated per request:** The `symbols` list must be explicitly supplied in the POST body of the API call. If omitted, `get_actionable_directives()` falls back to `WATCHLIST`.
- **Bounded and non-persisted:** It does not mutate `.env`, `watchlist.txt`, or any database state. The symbol list dies at the end of the HTTP request.
- **Paper-only:** It executes via `OptionsPaperExecutor`, which strictly targets `PaperAccountStore`. It has no live-broker equivalent.

This capability was built to allow an operator to scan a targeted list of tickers for options setups on demand, independent of the platform's broader tracked universe. It is working as designed and is not a safety gap.

## 3. UI Legibility Tie-ins

**Status: Verified live via browser render.**

To make the "free-roaming" safety boundary legible to the operator, an honest note is rendered in the UI whenever an untracked symbol is viewed or traded:
> *"This paper trade is manual — Autopilot's automated signals only act within your tracked universe."*

This note is attached to:
- `webapp/src/screens/PaperBroker.tsx` (Quick Trade panel)
- `webapp/src/screens/SymbolScreener.tsx` (Screener results table)
- `webapp/src/components/ExplainTickerDrawer.tsx` (Untracked symbol notice)

## 4. Other Automated Execution Surfaces

A grep of `execution/` callers confirms no other scheduled/automatic surfaces exist outside of:
1. The orchestrator daemon's `main_orchestrator.py` / `pipeline/production_steps.py` (which routes through `StrategyEngine`).
2. The options auto-scan (`execution/options_paper_executor.py` / `execution/options_lifecycle.py`).
