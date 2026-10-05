# Paper trade entry/exit context: task tracker

Branch `paper-trade-entry-exit-context`. Plan: `.claude/paper_trade_entry_exit_context_implementation_plan.md`.

- [x] Read-only live DB check (`mode=ro`): 0 `main_pipeline` snapshots, 8 open pipeline positions without one, 1 closed (SPY) with `close_reason='flatten'`
- [x] `execution/trade_context.py`: entry/exit builders, close_reason codes, `apply_fill_kwargs`
- [x] `OrderIntent.decision_context` (optional, last field)
- [x] `main_orchestrator._execute_broker_orders`: attach context after the intent is built; `_safe_decision_context` fails open
- [x] `FMPPaperBroker.submit_order`: forward context kwargs; none means today's exact call
- [x] `PaperAccountStore`: `close_reason`/`exit_context_json` kwargs, validation, column + migration, `get_full_closed_trades`
- [x] `database_setup.migrate_paper_closed_trades_schema`: new column
- [x] Composer `exit_context`/`exit_context_status`; narrative exit-trigger sentence for `signal_*` only
- [x] Webapp `types.ts`/`mock.ts` optional fields + one pipeline-style fixture (no UI change)
- [x] Tests: order-loop invariance (priority queue on/off), OrderManager coid/dedupe, broker kwargs, store, migration, composer, narrative, e2e
- [x] Docs: execution.md, data-layer.md, orchestration-entrypoints.md, observability-and-apis.md, testing.md, known_issues (+README, vocabulary cross-link)
- [x] Verification: targeted tests, ruff, webapp typecheck, `make ci`
- [ ] After merge: restart the daemon (`launchctl kickstart -k gui/$(id -u)/com.investyo.stack`), then a read-only DB check after the next in-hours cycle that trades
- [ ] Follow-up (not in this PR): render `exit_context` in `RetrospectiveDetailModal`
