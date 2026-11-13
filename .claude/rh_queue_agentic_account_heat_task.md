# Task tracker: rh_queue_agentic_account_heat

Plan: `.claude/rh_queue_agentic_account_heat_implementation_plan.md`

- [x] 1. `execution/risk_gate.py`: `run_all(skip=...)` and `ACCOUNT_LEVEL_CHECKS`
- [x] 2. `execution/queue_builder.py`: skip account-level checks, add `deferred_account_checks` / `account_checks_owner`
- [x] 3. `execution/agentic_preflight.py`: `Holding.avg_cost`, `portfolio_heat()`, BUY heat veto, report limits
- [x] 4. `.claude/agents/rh-risk-manager.md`: quotes for queue and held symbols
- [x] 5. Tests: `test_risk_gate.py`, `test_queue_builder.py`, `test_agentic_preflight.py`, `test_robinhood_e2e.py`, golden `execution_queue.golden`
- [x] 6. Docs: known issue + README index, `docs/architecture/execution.md`, `docs/architecture/testing.md`, `docs/AGENTIC_TRADING_SAFETY_FRAMEWORK.md`
- [x] 7. PR artifacts (plan, task, walkthrough)

## Verification and merge
- [x] Targeted tests green (risk_gate, queue_builder, agentic_preflight, robinhood_e2e, agentic_cycle)
- [x] `make ci`
- [x] `make local-ci` (paste the table into the PR)
- [x] Live read-only replay: queue gate passes the 3 BUYs on the cached snapshot; preflight heat 0.0 on real Agentic reads
- [ ] After restart: a daemon-written queue has `deferred_account_checks` and the cycle reason is no longer `no_actionable_intents`
- [x] MCP positions field check: real avg-cost field name in the Agentic `get_equity_positions` payload
- [ ] PR opened, self-reviewed, merged; main checkout synced
- [ ] Stack restarted (`launchctl kickstart -k gui/$UID/com.investyo.stack`)
- [ ] First Reviewer run after the restart counts a day (`python -m execution.agentic_review status`)
