# Robinhood queue: judge account risk against the Agentic account, not the main one

Branch: `rh-queue-agentic-account-heat` → PR. Bug fix (allowed under the step-7 freeze). Touches execution gating, so it goes through a PR, not a direct commit to `main`.

## Context

The burn-in that started on 2026-11-13 hasn't counted a single day. Every BUY in `execution_queue.json` has `gate_allowed=False` with `portfolio_heat: 34.78% > limit 11.00%`. With nothing actionable, the 15-minute cycle (`execution/agentic_cycle.py::gate`) skips as `no_actionable_intents`. The Reviewer's READY verdict needs 10 days with a completed review cycle, so the count stays at 0.

The root cause is a cross-account mismatch:
- `execution/queue_builder.py::_build_risk_context` builds the `PreTradeRiskGate` context from the daemon's Robinhood snapshot (`data/robinhood_portfolio.py`). That snapshot is robin_stocks `build_holdings()` / `load_portfolio_profile()` with no account number, which reads the **default (main) account**.
- The queue is only ever executed in the **Agentic** account. The `robinhood-execution` skill says "Agentic account only. Never act against the operator's main account." The Agentic account is a separate cash account, $250 and no positions as of 11-03.
- So the main account's unrealized losses are vetoing orders for a different account. The other three account-level checks are no-ops today, so heat is the only one that bites:
  - `max_correlation`: `returns_df=None`
  - `daily_loss_limit`: `start_of_day_equity` equals equity
  - `max_position_size`: $50 against main-account equity

The fix: account-level checks for the queue run where the Agentic account is actually visible, which is the Phase-2 preflight (`execution/agentic_preflight.py`). It already reads the live Agentic account over MCP and already owns concentration, gross and buying power.

## Changes

### 1. `execution/risk_gate.py`: optional check skip (default unchanged)
- Add `ACCOUNT_LEVEL_CHECKS = frozenset({"max_position_size", "portfolio_heat", "max_correlation", "daily_loss_limit"})`.
- `run_all(intent, context, *, skip: Iterable[str] = ())`: drop the checks whose `check_name` is in `skip`. Map each check function to its name with a small dict, so the names aren't recomputed by running the check.
- The default `skip=()` leaves behavior byte-identical for `BrokerExecutionStep` (paper ledger) and every other caller.

### 2. `execution/queue_builder.py`: the queue skips the account-level checks
- `gate_intent(..., skip=ACCOUNT_LEVEL_CHECKS)` passes the skip set through to `run_all`.
- The queue payload gets a top-level `"deferred_account_checks": [sorted names]` and `"account_checks_owner": "agentic_preflight"`, so the webapp and agents can see the deferral.
- Market and system checks stay at queue time: kill switch, macro, HMM, stress, market hours, validation, order rate. Because `run_all` short-circuits, a check that used to sit behind heat can now be the first failure. That's honest and expected.
- The snapshot is still used for BUY notional (equity × pct, capped at `ROBINHOOD_MAX_NOTIONAL_PER_ORDER`, so $50 in practice) and for SELL eligibility. Both are left alone here (see Out of scope).

### 3. `execution/agentic_preflight.py`: portfolio heat against the live Agentic account
- `Holding` gains `avg_cost: Optional[float]`, parsed in `AccountState.from_mcp` from `average_buy_price`, falling back to `average_cost`. A missing or garbage value becomes `None`, never 0.
- New pure helper `portfolio_heat(account, quotes) -> Optional[float]` (as built; the draft had a `(value, reason)` tuple):
  - heat = Σ max(0, (avg_cost − price) × qty) / `total_value`, priced off `Quote.last`, else the bid.
  - No positions → `0.0`.
  - Positions unreadable, any held symbol missing an avg cost or quote, or `total_value` ≤ 0 → `None`. Fails closed.
- `preflight(..., max_portfolio_heat: Optional[float] = None)` defaults to `settings.MAX_PORTFOLIO_HEAT` via the existing `_settings_default`. For **BUY** intents only:
  - heat > cap → veto `PORTFOLIO_HEAT_OVER_LIMIT = "portfolio_heat_over_limit"`
  - heat is None → veto `PORTFOLIO_HEAT_UNAVAILABLE = "portfolio_heat_unavailable"`
  - SELLs are never blocked, matching the risk gate.
- Report the measured `portfolio_heat` and the cap in the report's account block (next to `max_portfolio_gross`).
- The CLI needs no new arguments. Quotes for held symbols arrive through the existing `--quotes` file (step 4).

### 4. `.claude/agents/rh-risk-manager.md` (and the `.agents/` mirror if one exists)
- `quotes.json` ← `get_equity_quotes(symbols=[every queue symbol] ∪ [every held symbol])`, so the heat check can price held positions.
- Add the two new reason codes to the explanation list.

### 5. Tests
- `tests/test_risk_gate.py`:
  - `run_all(skip=…)` skips exactly the named checks.
  - The default is unchanged: same results list as before, for a blocking heat context.
- `tests/test_queue_builder.py`:
  - Main-snapshot positions with 35% heat no longer block a BUY.
  - A market-level block (kill switch, HMM) still blocks.
  - The payload carries `deferred_account_checks`.
- `tests/test_agentic_preflight.py`:
  - Heat under the cap passes; heat over the cap vetoes BUYs, not SELLs.
  - Missing avg cost or quote for a held symbol → `portfolio_heat_unavailable`.
  - No positions → heat 0 → pass.
  - The `average_buy_price` / `average_cost` parse.
- `tests/test_robinhood_e2e.py`: the fake `get_equity_positions` payload adds `average_buy_price`. The existing e2e flow still passes.

### 6. Docs
- `docs/architecture/execution.md`: queue-builder section (account checks deferred) and preflight section (new heat veto).
- `docs/AGENTIC_TRADING_SAFETY_FRAMEWORK.md`: one dated line, "2026-11-13: account-level gate checks moved from the queue (main-account snapshot) to the Agentic preflight."
- `docs/known_issues/rh_queue_gated_on_main_account_2026_11.md`: root cause, fix, status, and the out-of-scope gaps below. Add it to `docs/known_issues/README.md`.
- PR artifacts: `.claude/rh_queue_agentic_account_heat_{implementation_plan,task,walkthrough}.md`.
- No CLAUDE.md change (no rule or command changes).

## Out of scope (disclosed in the known-issues doc, not fixed here)
- **SELL intents come only from main-account holdings.** A position the Agentic account opens never gets an exit intent from the daemon. Fixing that needs the daemon to read the Agentic account, which means a new account-number setting and a second robin_stocks fetch path. It's a follow-up, and it matters once the burn-in actually opens positions.
- **The daily loss limit isn't measured for the Agentic account** anywhere. It was a no-op before this change too.

## Verification
1. Targeted tests:
   ```bash
   pytest tests/test_risk_gate.py tests/test_queue_builder.py tests/test_agentic_preflight.py tests/test_robinhood_e2e.py tests/test_agentic_cycle.py -q
   ```
2. `make ci`, then `make local-ci`. Paste the table into the PR (no remote CI).
3. Live, read-only replay: rebuild the queue from the daemon's latest run result (or rerun `build_execution_queue` against the cached snapshot with `side_effects=False`). Confirm the MFIN/SHEL/WFC BUYs show `gate_allowed=True` and `deferred_account_checks` is present. Then run `python -m execution.agentic_cycle` in gate-only/status mode (no launch) and confirm the reason is no longer `no_actionable_intents`.
4. Preflight against real Agentic MCP reads, read-only (`get_portfolio`, `get_equity_positions`, `get_equity_quotes`): heat 0.0 for the empty account, BUYs pass the heat check. Check the real positions payload for the avg-cost field name.
5. After merge, sync the main checkout and restart the stack with `launchctl kickstart -k gui/$UID/com.investyo.stack`, since the daemon doesn't hot-reload. The next hourly queue should be actionable, and the next 15-minute tick should run a review cycle. The 16:40 Reviewer run is the first that can count a day.
