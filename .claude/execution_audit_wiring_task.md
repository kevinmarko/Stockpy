# Task Tracker: Fix three `execution/` audit findings

Branch: `fix-execution-audit-and-circuit-breaker-gaps`

- [x] Independently re-verify all three audit findings against the code
      (grep + direct repro of the collision) before planning fixes.
- [x] Plan approved via `EnterPlanMode`/`ExitPlanMode`.
- [x] Fix 1: wire `ExecutionAuditStore` into `OrderManager` (`audit_store=`
      constructor param, `_record_execution_audit`, call site in
      `submit_order_with_idempotency`, docstring update).
- [x] Fix 1: add `conftest.py::_isolate_execution_audit_db_in_tests` autouse
      fixture to prevent real-DB pollution from the new implicit construction.
- [x] Fix 1: new tests in `tests/test_order_manager_execution_audit_wiring.py`
      (real-fill record, non-fill no-record, dry-run no-record, and the
      flagship real-paper-fill → non-empty SEC 606 report regression).
- [x] Fix 2: `CircuitBreaker._consecutive_latency_breaches` + trip condition;
      removed dead `CircuitBreakerConfig.auto_reset_on_heartbeat`.
- [x] Fix 2: new tests `test_circuit_breaker_latency_trip` /
      `test_circuit_breaker_latency_streak_reset_by_fast_response`.
- [x] Fix 3: `make_client_order_id` canonicalization switched to `json.dumps`.
- [x] Fix 3: new test `test_client_order_id_no_delimiter_collision`.
- [x] Docs: `docs/architecture/execution.md` updated (order_manager,
      multi_broker_gateway, sec_rule_606_reporter, execution_audit_store).
- [x] Docs: `CLAUDE.md` bullet added under "Recent Architecture Updates"
      (auto-mirrored to `AGENTS.md` by the `sync_agent_docs.sh` hook).
- [x] Regenerated `docs/settings_liveness.json` (new test file shifted
      `files_scanned`; required by `tests/test_settings_liveness.py`'s
      artifact-freshness gate — root-caused, not a real bug).
- [x] Root-caused and confirmed as pre-existing/unrelated the 5 other offline
      suite failures (LLM chat routing tests, Gemini Live `google.genai`
      import) — verified via `git stash -u` against clean `main`.
- [x] `python -m ruff check . --select=F821,F822,F823,E9` — clean.
- [x] Full offline suite (`pytest -m "not network and not slow" -n auto
      --dist loadgroup`) — green apart from the confirmed-pre-existing 5.
- [x] Wrote implementation plan / walkthrough PR artifacts under `.claude/`.
- [ ] Push branch and open PR.
