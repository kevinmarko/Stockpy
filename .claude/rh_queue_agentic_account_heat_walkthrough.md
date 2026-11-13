# Walkthrough: Robinhood queue judged against the Agentic account

Bug: the burn-in (review mode, $50 cap, started 2026-11-13) counted no days. Every BUY was
`gate_allowed=False` with `portfolio heat 34.78% > limit 11.00%`, because the queue's risk context is
the daemon's main-account snapshot while orders only run in the Agentic account. Full write-up:
`docs/known_issues/rh_queue_gated_on_main_account_2026_11.md`.

## What changed

- `execution/risk_gate.py`: `run_all(..., skip=())` drops checks by `check_name`; unknown names raise.
  New `ACCOUNT_LEVEL_CHECKS` (`max_position_size`, `portfolio_heat`, `max_correlation`,
  `daily_loss_limit`). Check order is unchanged and the default runs everything, so
  `BrokerExecutionStep` and other callers behave as before.
- `execution/queue_builder.py`: the queue skips `ACCOUNT_LEVEL_CHECKS` (`QUEUE_DEFERRED_CHECKS`) and
  writes `deferred_account_checks` and `account_checks_owner: "agentic_preflight"` into the payload.
  Market and system checks still run at queue time.
- `execution/agentic_preflight.py`: `Holding.avg_cost` (from `average_buy_price`, fallback
  `average_cost`); `portfolio_heat(account, quotes)` returning the heat fraction or `None`;
  `preflight(..., max_portfolio_heat=None)` defaulting to `settings.MAX_PORTFOLIO_HEAT`; BUY-only
  vetoes `portfolio_heat_over_limit` and `portfolio_heat_unavailable` (fail closed); report limits gain
  `portfolio_heat` and `max_portfolio_heat`. Note: the plan sketched a `(value, reason)` tuple return;
  the code returns `Optional[float]`.
- `.claude/agents/rh-risk-manager.md`: quotes now cover queue symbols and held symbols.
- Tests (written separately): `tests/test_risk_gate.py`, `tests/test_queue_builder.py`,
  `tests/test_agentic_preflight.py`, `tests/test_robinhood_e2e.py`, and the golden
  `tests/fixtures/run_once_advisory_golden/execution_queue.golden` (two new keys).
- Docs: the known-issue file and its README entry, `docs/architecture/execution.md` (queue builder and
  preflight sections), `docs/architecture/testing.md`, `docs/AGENTIC_TRADING_SAFETY_FRAMEWORK.md`.

## Why this shape

Account-level checks need the account they judge. The Agentic preflight already reads the live Agentic
account over MCP and already owns concentration, gross and buying power, so heat belongs there. Heat
was the only account-level check that actually bit; the other three were already no-ops in the queue.

## Out of scope (disclosed)

- SELL intents are generated only for symbols the main account holds, so a position opened in the
  Agentic account never gets a daemon exit intent. Fixing it needs the daemon to read the Agentic
  account (new account-number setting, second robin_stocks fetch path). It matters once the burn-in
  opens positions.
- No daily-loss-limit measurement for the Agentic account anywhere (a no-op before too).
- `max_correlation` was already a no-op in the queue (`returns_df=None`).

## Verification

All runs 2026-11-13 on this branch.

- **Targeted tests:** risk_gate, queue_builder, agentic_preflight, robinhood_e2e, agentic_cycle,
  agentic_review, execution_queue_gating, daemon_agentic_queue_primary, execute_broker_orders and
  compose: **471 passed**.
- **`make local-ci`** (first run, then re-run after the fixes):

  | step | result |
  |---|---|
  | lint | PASS |
  | offline | PASS on re-run: 12152 passed, 15 skipped. The first run failed on two goldens and the generated settings census/liveness artifacts; all were regenerated, and the only diffs are line-number shifts plus the two new queue keys. |
  | coverage | PASS |
  | slow | PASS |
  | bandit | PASS |
  | pip-audit | FAIL: new pip / pyjwt advisories. No dependency changes in this PR, so it fails the same way on main. |
  | webapp-typecheck / test / build | FAIL exit 127: `tsc` isn't installed in this worktree (no `node_modules`). This PR doesn't touch `webapp/`. |
  | ml-audit / ml-lookahead | SKIPPED (no watched path changed) |

- **Live replay, queue gate** (read-only, cached main-account snapshot, no login, `side_effects=False`):
  - Before: MFIN, SHEL and WFC all blocked at `portfolio heat 34.78% > limit 11.00%`.
  - After: all three gate-allowed.
- **Live replay, preflight** on real Agentic MCP reads (read-only `get_portfolio`, `get_equity_positions`, `get_equity_quotes`; $250 cash, no positions):
  - Heat is `0.0`.
  - MFIN passes (3 shares, $40.20).
  - SHEL and WFC veto as `qty_below_one_share`: one share costs more than the $50 cap. That veto is pre-existing and unrelated to heat.
- **MCP field check:** `get_equity_positions` returns `average_buy_price`. Its guide says the field may be omitted while a position is reconciling; in that case the preflight fails closed with `portfolio_heat_unavailable`.
- **Changes after the subagents' pass:** the `robinhood-execution` skill (both mirrors) now lists portfolio heat among the preflight's checks.
