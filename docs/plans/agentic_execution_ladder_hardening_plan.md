# Agentic Execution-Ladder Hardening Plan

## Context
This plan specifies the implementation steps for execution-ladder enhancements and options-trading additions requested by the operator, corrected against actual codebase state via a fact-checking audit. Options trading remains paper-only (no live order routing).

## Low-risk tier (docs/tests only — commit straight to main after self-review)

- **T1 — Close the real test gap.** The actual ladder named in the pasted content is `settings.ROBINHOOD_EXECUTION_MODE` (`off → review → live`, `settings.py:1503-1506`, fail-safe validator at `settings.py:5159`), edited via `PUT /settings/feature-flags` (`api/pilots_api.py:5109-5170`). It currently has only generic/shared structural tests (`tests/test_pilots_api_tunables.py:660-690`), no dedicated happy-path / invalid-enum / confirmation-gate test for this specific field. Add one, modeled on `tests/test_pilots_api_tunables.py`'s existing `MARKET_DATA_PROVIDER` enum-rejection test (~line 589) and `tests/test_pilots_api.py::TestExecutionModeConfirmation`.
- **T2 — Doc-drift fix.** `docs/plans/MCP_EXPANSION_PLAN.md` / its `MCP_EXPANSION_WALKTHROUGH.md` companion mark Phase 4 (execution-boundary redesign) as "ready for a fresh agent to build," but it has already shipped (`broker_live_execution_mcp.py`, `execution/live_trade_proposals_store.py`, the `/pilots/execution/pending|{token}/approve|{token}/reject` endpoints all exist on disk). Update both files' Phase 4 status.

## "Everything else" tier (execution/orchestrator-adjacent — branch + PR, plan reviewed before code)

- **T3 — Fix the `ExecutionLadder` widget bug.** `webapp/src/screens/AgenticTrading.tsx` (~lines 410-477, invoked ~line 341) renders the 4-step *platform*-mode ladder (`advisory/simulation/paper/live`) but is fed `AgenticStatus.mode`, which is actually the 3-step *queue*-mode value (`off/review/live`) from `execution/queue_builder.py` via `api/pilots_api.py`'s `get_agentic_status` (~line 2709). `steps.indexOf("off")` / `indexOf("review")` both return `-1`, so the widget silently shows nothing highlighted in the default (`off`) and most common (`review`) states. Fix: change the component's step array/labels to the real `off/review/live` ladder it's actually displaying. Add a regression test in `AgenticTrading.test.tsx` covering all three states.
- **T4 — Harden `simulation_engine.py`'s optional-dependency handling.** Match the `TENSORFLOW_AVAILABLE` pattern already established in `forecasting_engine.py:33-57`: export `VECTORBT_AVAILABLE`/`BACKTRADER_AVAILABLE` flags from the existing `try/except ImportError` block (`simulation_engine.py:21-29`), make the module-level `class InstitutionalStrategy(bt.Strategy)` definition (line 151) conditional on the flag, and guard `run_backtrader_simulation`/`optimize_strategy_vectorbt` call sites. Extend `tests/test_simulation_engine.py` to actually exercise the missing-dependency path (monkeypatch the flag off, assert graceful degradation).
- **T5 — Closed-loop paper mode (the substantial new feature).** Wire `agentic-discovery` → advisory cross-reference → `execute_paper_trade` into a scheduled, paper-only loop. Design constraints:
  - Never touches the Robinhood MCP's write tools or `execution/order_manager.py`'s live path — fills exclusively into the existing paper book (`data/paper_account_store.py` via `execution/fmp_paper_broker.py`).
  - Reuse the discovery→universe merge that's *already* automatic every cycle (`main.py::_build_universe`, `pilots/discovery.py::discovery()`).
  - New flag `AGENTIC_PAPER_LOOP_ENABLED` (default `False`).
  - Cadence: hook into `desktop/daemon_runtime.py`'s existing `_timer_loop` with a new `maybe_run_agentic_paper_loop()`, gated on the flag, never raising out of the loop.
  - Must check `output/scan_candidates.json` freshness and skip the cycle if stale.
  - Tag every simulated trade with `strategy_id`/`pilot_id` (matching `data/paper_account_store.py`'s existing convention).
  - Documentation-update step: a new `docs/signals/` or `docs/known_issues/`-adjacent write-up, plus surfacing the loop's on/off state and last outcome on the Agentic Trading tab.

## Explicitly out of scope
- Live options order execution (`place_option_order`/`review_option_order`) — reverses 8 recorded decisions and multiple regression-tested refusal guards; user chose to keep options paper-only.
- SecProve Agent Safety Kit — unvetted third party; this repo already has native equivalents.
- New narrowly-scoped skills for other Robinhood MCP tool categories (fundamentals/earnings/watchlists) — plausible future work, not concrete enough to scope here.

## What Claude Code will audit afterward
- Re-run the relevant `pytest` files for T1/T3/T4/T5.
- Re-run the `run-investyo-mcp` driver to confirm the MCP server still boots and answers a tool call after Antigravity's changes land.
