# Task: agentic queue gate judged portfolio heat on the wrong account

- [x] `AGENTIC_QUEUE_RISK_ACCOUNT` setting (main | agentic, default main), validator, DANGEROUS_KEYS, ALLOWED_KEYS
- [x] `PreTradeRiskGate.deferred_checks` (default empty) — run_all reports deferred checks as passed, unevaluated
- [x] `queue_builder`: defer portfolio_heat + max_position_size when `agentic`; payload `deferred_checks`
- [x] `agentic_preflight`: check 9 heat on the Agentic account (`over_portfolio_heat`, `heat_unavailable`), `Holding.avg_cost`
- [x] `rh-risk-manager` agent: quote held symbols too; explain the new reasons
- [x] Tests: queue builder scope, gate deferral, preflight heat, e2e fake positions carry average_buy_price
- [x] Docs: known-issue write-up + index, execution.md, safety framework, RUNBOOK; census/liveness regenerated
- [x] Offline replay of the 2026-11-13 queue (main blocks, agentic passes, preflight heat 0%)
- [x] `make local-ci`: all steps PASS except pip-audit (pre-existing advisories in unchanged deps), table in the PR
- [ ] Operator: set `AGENTIC_QUEUE_RISK_ACCOUNT=agentic` after merge
