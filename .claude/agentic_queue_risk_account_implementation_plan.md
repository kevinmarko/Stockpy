# Plan: queue gate checks the wrong Robinhood account (portfolio heat)

Branch: `fix-agentic-queue-risk-account` · PR artifacts: `.claude/agentic_queue_risk_account_{implementation_plan,task,walkthrough}.md`

## Context
The agentic burn-in (review mode, $50 cap, started 2026-11-13) has run **zero** cycles. Every BUY in the
daemon's `execution_queue.json` is `gate_allowed=False` for one reason only:
`portfolio_heat: 34.78% > limit 11.00%`. Today's queue: MFIN, SHEL and WFC, $50 each, all blocked.

Root cause: `execution/queue_builder.py::_build_risk_context` builds the gate's `RiskContext` from
`ctx.snapshot`. That snapshot is `main_orchestrator.fetch_account_snapshot()` (robin_stocks, the default
**main** margin account ••5702). The orders go to the **Agentic** cash account ••4848. That account
currently holds nothing, so its real heat is 0%. The main account's unrealized losses are vetoing trades on a
different account. `agentic_cycle.gate` then skips with `no_actionable_intents`, so no review day ever counts.

Two of the gate's checks depend on the account: `portfolio_heat` and `max_position_size`.
`daily_loss_limit` is effectively a no-op here (start-of-day equity equals equity), and
`max_correlation` is skipped (`returns_df=None`). The preflight (`execution/agentic_preflight.py`) already
reads the real Agentic account over the MCP and enforces the position weight, gross and buying power. It
has **no heat check**.

Fix: check heat on the account that actually trades. Keep it fail-closed.

## Changes

### 1. Setting: which account the queue gate's account checks use
`settings.py`: `AGENTIC_QUEUE_RISK_ACCOUNT: Literal["main", "agentic"] = "main"`.
- `"main"` is the default and today's exact behavior, per the default-value policy for trading behavior.
- `"agentic"` defers `portfolio_heat` and `max_position_size` to the preflight. That is where the Agentic
  account is known.
- Add it to `DANGEROUS_KEYS` in `settings_keysets.py` and to `shared/env_io.py::ALLOWED_KEYS` (it is
  non-secret).
- Regenerate `docs/settings_field_census.*` and `docs/settings_liveness.json` with their `--write` scripts.

### 2. Risk gate: optional deferred checks (`execution/risk_gate.py`)
- Add the attribute `deferred_checks: frozenset[str]`, default empty. It is set after construction, like
  `side_effects`, so no-arg test doubles keep working.
- In `run_all`, a check whose name is in it returns `RiskCheckResult(name, True, "deferred to agentic
  preflight (account-dependent)")` without being evaluated.
- With the default (empty) set, every check behaves exactly as today, and every other caller (paper
  `BrokerExecutionStep`, `OrderManager`) is untouched.

### 3. Queue builder (`execution/queue_builder.py`)
- In `build_execution_queue`, when the setting is `"agentic"`, set
  `gate.deferred_checks = {"portfolio_heat", "max_position_size"}`.
- Add a top-level `"deferred_checks": [...]` to the payload (absent in `"main"` mode, so that queue stays byte-identical) so the
  queue says honestly what it did not check.
- The kill switch, macro, HMM, stress, market-hours, validation and rate checks are unchanged.

### 4. Preflight heat check on the Agentic account (`execution/agentic_preflight.py`)
- `Holding` gains `avg_cost: Optional[float]`, parsed from MCP `average_buy_price`. Robinhood says this
  can be omitted while a position is reconciling, and then it is `None`, never 0.
- New pure helper `account_heat(account, quotes) -> Optional[float]`:
  Σ max(0, (avg_cost − last) × qty) / total_value. The adverse-only definition matches
  `PreTradeRiskGate.portfolio_heat_check`. It returns `None` if positions are unreadable, total_value is
  missing or ≤ 0, or any held symbol lacks avg_cost or a quote.
- New check 9 for every BUY, always on, in both modes. It is veto-only, so it can only make the preflight
  stricter:
  - heat `None` → `HEAT_UNAVAILABLE` (fail closed)
  - heat > `MAX_PORTFOLIO_HEAT` → `OVER_PORTFOLIO_HEAT`
  - an empty account → heat 0 → passes
- Add `max_portfolio_heat` / `portfolio_heat` to the report's limits block. Kwarg override:
  `max_portfolio_heat=None` → `_settings_default("MAX_PORTFOLIO_HEAT")`.
- CLI `main()`: quotes must cover queue symbols **∪ held symbols**. The helper reports which held
  symbols lack a quote.

### 5. Risk Manager agent (`.claude/agents/rh-risk-manager.md`, mirrored where the repo keeps agent copies)
- Fetch quotes for held symbols as well as queued ones.
- Explain the two new veto reasons.
- This is instruction text only; the agent's tool list does not change.

### 6. Tests
- `tests/test_queue_builder*.py`:
  - `"main"` reproduces today's heat block on a losing snapshot. Pin the payload, except for the new empty
    `deferred_checks`.
  - `"agentic"` lets the same intent through with `deferred_checks` listed.
  - The kill switch and macro still block in `"agentic"`.
- `tests/test_risk_gate*.py`:
  - deferred names pass without being evaluated
  - the default empty set changes nothing
- `tests/test_agentic_preflight*.py`:
  - an empty account passes
  - a losing held position over the limit vetoes
  - a missing avg_cost, quote or total_value vetoes `HEAT_UNAVAILABLE`
  - SELLs are never heat-checked
- The settings census and liveness tests stay green after the regen.

### 7. Documentation
- `docs/known_issues/agentic_queue_gate_wrong_account_heat_2026_11.md`: new write-up covering root cause,
  fix and status. Add it to the index in `docs/known_issues/README.md`.
- `docs/architecture/execution.md`: queue gate vs. preflight account scope; the new setting and deferred
  checks.
- `docs/AGENTIC_TRADING_SAFETY_FRAMEWORK.md`: the guardrail table gets heat on the Agentic account.
- `docs/RUNBOOK.md`, agentic burn-in section: set `AGENTIC_QUEUE_RISK_ACCOUNT=agentic` for the burn-in.
- `CLAUDE.md`/`AGENTS.md`: no change expected. No rule or command changes.

## Rollout (after merge, operator-visible)
1. Set `AGENTIC_QUEUE_RISK_ACCOUNT=agentic` through the runtime flags store
   (`runtime_flags_writer.write_override`).
2. The daemon picks it up on its next wake if `RUNTIME_FLAGS_REFRESH_ENABLED`; otherwise
   `launchctl kickstart -k` the stack. Memory warns about a libomp/LightGBM segfault risk on restart, so
   watch the first cycle.
3. The next market-hours queue should show `gate_allowed=True` for the BUYs. The 15-min timer then runs
   the four agents in review mode and the burn-in day counter starts.

## Out of scope
- Reading the Agentic account inside the daemon. That would mean a robin_stocks `account_number` path,
  which is a new login/data path.
- Changing `MAX_PORTFOLIO_HEAT` (11%).
- Unloading the dead `com.investyo.daily-advisory` timer. That is separate.

## Verification
- `pytest tests/test_queue_builder*.py tests/test_risk_gate*.py tests/test_agentic_preflight*.py tests/test_agentic_cycle*.py tests/test_measure_settings_census.py tests/test_runtime_flags*.py`
- `make local-ci`, with its table pasted into the PR.
- Replay today's real queue offline: rebuild it with the setting on `"agentic"` and confirm MFIN, SHEL and
  WFC become `gate_allowed=True`. Run the preflight CLI on them with the empty Agentic account and its
  quotes, and confirm they pass with heat 0%.
