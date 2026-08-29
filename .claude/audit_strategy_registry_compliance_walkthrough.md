# Audit Strategy Registry Compliance — Walkthrough

## Correction (2026-08-29, pre-PR review pass)

This branch's original walkthrough (superseded below) described its goal as
enforcing a "Constraint #1: Advisory-only is absolute" and its primary
accomplishment as deleting this repo's real, deliberately-built, tested
Robinhood live-execution infrastructure (`broker_live_execution_mcp.py`,
`execution/live_trade_proposals_store.py`, `pilots/live_trade_proposals.py`,
both `robinhood-execution` `SKILL.md` files, and the corresponding
`api/pilots_api.py` endpoints + tests). **That premise was false** — no
"Constraint #1" of that description exists anywhere in this repo's real
`CLAUDE.md`/`AGENTS.md`/`docs/RUNBOOK.md`/`docs/GO_LIVE_CHECKLIST.md` or the
`stockpy-quant-integrity` skill (verified via grep, zero hits). This repo's
actual documentation treats the deleted infrastructure as a deliberately
built, carefully gated, real capability (`RUNBOOK` Tier 8, `GO_LIVE_CHECKLIST`'s
Robinhood Live Sign-Off section, `docs/plans/MCP_EXPANSION_PLAN.md`'s Phase 4
"SHIPPED" status), not a forbidden one. Commit `65bc2da9` already reverted
that deletion, restoring every deleted file byte-identical to its last known
good state (`b656fe02` on `main`). This walkthrough is updated to describe
what the branch actually does now, not what an earlier pass on it believed
it was doing.

A second, independent issue was found and fixed in this same pre-PR review:
the "Fix STRATEGY_REGISTRY entries" commit (`d4b27144`) had, alongside its
legitimate registry work, **unintentionally deleted the
`override_deployability_gate` enforcement** from three of the four
options-desk execute endpoints (`earnings_crush`, `dispersion_trading`,
`zero_dte_engine`) — the request field, the blocking `UNGATEABLE_DATA_GAP`
check, and the `override_applied` response echo were all removed, leaving
those three endpoints executing real (paper) trades unconditionally with no
gate check at all. This reverted `CLAUDE.md`'s documented 2026-08-29 fix
("all four options-desk execute endpoints now ENFORCE their gate
identically") back to pre-fix behavior for 3 of 4 endpoints. Confirmed via
`git log <merge-base>..main -- api/pilots_api.py` returning empty — `main`
never touched this file after the branch's fork point, so this was an
active regression introduced by this branch's own commit, not staleness.
Restored byte-for-byte from `main`'s version of the three execute functions
and their request models (commit `d1e79154` on this branch).

A third issue: the same commit also added `news_catalyst`, `regime_multiplier`,
and `forecast_alignment` to `STRATEGY_REGISTRY` as `_build_ungateable_adapter`
stubs — beyond this branch's own stated scope (`docs/signals/*.md` and the
implementation plan only ever named `earnings_crush`/`dispersion_trading`/
`zero_dte_engine`/`gamma_scalper`/`vol_mispricing`/`copula_stat_arb`). Unlike
those four, none of the three is an order-submitting Pilot with its own P&L.
Registering `news_catalyst` also set `pilots/catalog.py`'s news-catalyst
Pilot to `validation_strategy_id="news_catalyst"`, directly contradicting
that field's own adjacent, deliberate comment ("stays None until enough real
history exists") and breaking
`tests/test_pilots_api.py::TestStrategyHealth::test_pilot_without_backtest_is_honest_never_fabricated`.
`regime_multiplier`/`forecast_alignment` were given `turnover=0.0`, failing
`tests/test_refresh_validations.py::TestRegistryStructure`'s `turnover > 0`
invariant, and neither is referenced by any Pilot's `validation_strategy_id`
in the first place — their own reason strings argue against registering
them. All three reverted; see `docs/VALIDATION_STRATEGY_FIX_LOG.md`'s
"2026-08-29 ... reverted" entry for the full detail.

## What this branch actually does (current state)

1. **Registry honesty for four order-submitting options Pilots**:
   `earnings_crush`, `dispersion_trading`, `zero_dte_engine`, and
   `gamma_scalper` are explicitly registered in `STRATEGY_REGISTRY` via
   `_build_ungateable_adapter` — each raises `RuntimeError` with a documented
   reason, so the validation harness reports their true `UNGATEABLE_DATA_GAP`/
   `UNGATEABLE_NOT_A_STRATEGY` status instead of silently omitting them. This
   is NOT a real backtest and carries no PBO/DSR/Sharpe/MaxDD numbers.
   `docs/VALIDATION_STRATEGY_FIX_LOG.md` and each pilot's own
   `docs/signals/<name>.md` updated to match.
2. **0DTE coverage**: `manage_0dte_exits()` is now also called from the
   standalone `main_orchestrator.py` CLI runner's `main()` (it was already
   wired into `desktop/daemon_runtime.py`'s `_timer_loop` for the daemon
   path) — so a one-off `python main_orchestrator.py` run also evaluates the
   15:45 ET hard stop, not just the always-on daemon.
3. **API parity**: `gamma_scalper` added to `OPTIONS_DESK_DEPLOYABILITY_GATES`;
   its simulate-only endpoint (`post_options_gamma_scalp_simulate`) now
   echoes `gate_status` in its response, matching the other options-desk
   endpoints. (It has no execute path, so it needed no blocking check.)
4. **Robinhood live-execution infrastructure**: intact, unchanged from
   `main` — this branch neither deletes nor modifies it.

## Validation Results

- `tests/test_pilots_api.py`, `tests/test_options_desk_deployability_runtime_gap.py`,
  `tests/test_refresh_validations.py` (including the full
  `test_all_registered_adapters_run_end_to_end`), `tests/test_broker_live_execution_mcp.py`,
  `tests/test_live_trade_proposals_store.py`, `tests/test_robinhood_e2e.py`,
  and `tests/test_pilots_paper_broker.py` all pass (re-run in this review
  pass, not inferred from an earlier pass).
- `api/pilots_api.py` diffed against `main`: differs only by the two
  legitimate `gamma_scalper` additions (item 3 above) — everything else,
  including all four gate enforcement blocks, is byte-identical to `main`.
- `pilots/catalog.py` diffed against `main`: byte-identical.
