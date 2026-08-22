# Implementation Plan: Fix three `execution/` audit findings

Branch: `fix-execution-audit-and-circuit-breaker-gaps`

## Context

An audit of `execution/` surfaced three independently-verified bugs (each
re-verified directly against the code before planning a fix — repro'd, not
just trusted from the report):

1. **High** — `data/execution_audit_store.py`'s `record_audit`/`bulk_insert_audits`
   were never called from any production order path (grep: zero hits outside
   the module itself, `execution/sec_rule_606_reporter.py`, and tests). The
   live `GET /pilots/execution/sec-606/report` endpoint always read an empty
   table and returned an honest all-zero report (correct per CONSTRAINT #4),
   but the whole SEC Rule 606 feature was disconnected end-to-end from real
   fills. Same bug class as the previously-confirmed `pilots/zero_dte_engine.py`
   15:45 ET gate incident.

2. **Medium** — `execution/multi_broker_gateway.py`'s `CircuitBreaker` docstring
   claims a three-condition OR trip ("consecutive failures >= threshold OR
   error rate >= threshold OR latency > threshold"). Confirmed: `record_success`
   only logged a WARNING on a latency breach and never tripped; `record_failure`
   never inspected latency at all. A consistently-slow-but-never-erroring
   broker never tripped. Also confirmed dead: `CircuitBreakerConfig.auto_reset_on_heartbeat`,
   referenced nowhere else in the codebase.

3. **Medium, not currently exploitable** — `execution/order_manager.py::make_client_order_id`'s
   raw `|`-joined canonicalization let a literal `|` inside `strategy_id`/`symbol`
   forge a collision between two distinct order tuples. Reproduced directly.
   Not reachable via any current call site (all pass fixed literals/validated
   tickers), but unguarded against a future free-text caller.

## Fix 1 — Wire `ExecutionAuditStore` into the real order path

`execution/order_manager.py::OrderManager` is the single production choke
point (only `main_orchestrator.py`/`broker_live_execution_mcp.py` construct
it outside tests). `execution/multi_broker_gateway.py::MultiBrokerGateway`
itself subclasses `BrokerBase` with zero production callers today — when
eventually wired as `broker=`, it inherits this audit trail automatically, so
no separate hook was added there.

- New optional `audit_store: Optional["ExecutionAuditStore"] = None`
  constructor param (injectable, same pattern as `risk_gate`/`kill_switch`),
  `TYPE_CHECKING`-only import for the type hint, real import kept lazy.
- New `_record_execution_audit(intent, result)`: lazily constructs the store
  on first use if not injected; builds the record (order_id, client_order_id,
  symbol, side, venue derived from `broker_id` or the broker's class name,
  order_type, routing_timestamp, fill_price, executed_shares, is_option);
  NBBO left `None` (no generic NBBO source at this layer — honest, not
  fabricated); wrapped in try/except logging a WARNING, never raises.
- Called from `submit_order_with_idempotency` right after a successful result
  with `filled_qty > 0` — a dry-run or a bare ACCEPTED-with-no-fill-yet result
  never records a phantom row.
- **Required test-pollution guard**: `ExecutionAuditStore()`'s default
  constructor resolves to the real shared `~/.stockpy_local/quant_platform.db`,
  and ~15+ pre-existing test files construct `OrderManager(broker, ...)`
  directly with no `audit_store=`. Added `conftest.py::_isolate_execution_audit_db_in_tests`
  (autouse, mirrors the existing `_isolate_validation_runs_db_in_tests`
  fixture exactly) to point the default resolver at `sqlite:///:memory:` for
  any test that doesn't pass its own `db_url`/`sqlite_path`.
- New tests: `tests/test_order_manager_execution_audit_wiring.py` — a real
  fill creates exactly one audit record; a non-fill/dry-run creates none; and
  the flagship regression the audit asked for — a real paper fill through
  `FMPPaperBroker` produces a non-empty audit record AND a non-all-zero SEC
  606 report via `SecRule606Reporter`.

## Fix 2 — Circuit breaker latency trip + dead config

- Self-contained `_consecutive_latency_breaches` counter added to
  `CircuitBreaker` (no call-site changes needed — every existing caller
  already passes `latency_ms`, including the heartbeat path). A latency
  breach increments it; any success within threshold or any failure resets
  it; reaching `config.max_consecutive_failures` (reused, not a new field
  with an arbitrary default) trips the breaker.
- Removed `CircuitBreakerConfig.auto_reset_on_heartbeat` — confirmed dead,
  removal chosen over inventing new reset-bypass behavior for an ambiguous
  flag (a heartbeat-triggered reset before cooldown expiry would have risked
  unintentionally weakening the breaker).
- New tests in `tests/test_multi_broker_gateway.py`: `test_circuit_breaker_latency_trip`
  and `test_circuit_breaker_latency_streak_reset_by_fast_response`.

## Fix 3 — Escape `make_client_order_id`'s canonicalization

- Switched to `json.dumps([strategy_id, symbol.upper(), side.lower(), f"{qty:.6f}", bucket])`
  in place of the raw `|`-joined f-string. Determinism preserved (same inputs
  → same id); collision-resistance gained (no field value can forge a
  delimiter).
- New test: `tests/test_order_manager_idempotency.py::test_client_order_id_no_delimiter_collision`,
  reproducing the exact audit scenario and asserting the ids now differ.

## Documentation

- `docs/architecture/execution.md`: updated the `execution/order_manager.py`,
  `execution/multi_broker_gateway.py`, `execution/sec_rule_606_reporter.py`,
  and `data/execution_audit_store.py` bullets.
- `CLAUDE.md` (auto-mirrored to `AGENTS.md` by `sync_agent_docs.sh`): added
  one bullet under "Recent Architecture Updates" documenting all three fixes.
- `docs/settings_liveness.json` regenerated (`python3 scripts/settings_liveness.py --write`)
  since adding a new test file shifted `files_scanned`; unrelated to the
  actual fix content but required by `tests/test_settings_liveness.py`'s
  artifact-freshness gate.

## Verification

- `pytest tests/test_order_manager_execution_audit_wiring.py tests/test_order_manager_idempotency.py tests/test_order_manager_rate_limit.py tests/test_multi_broker_gateway.py tests/test_fmp_paper_broker.py tests/test_sec_rule_606_reporter.py tests/test_kill_switch.py tests/test_reconciliation.py tests/test_execution_alerts.py -q` — all green.
- Confirmed the new `conftest.py` fixture actually prevents a real DB write:
  ran `tests/test_fmp_paper_broker.py::test_order_manager_live_submission_reaches_the_paper_broker`
  (a pre-existing test that drives a real fill through `OrderManager` with no
  `audit_store=`) and diffed `~/.stockpy_local/quant_platform.db`'s mtime
  before/after — unchanged.
- `python -m ruff check . --select=F821,F822,F823,E9` — all checks passed.
- `python3 -m pytest -m "not network and not slow" -n auto --dist loadgroup`
  (the offline CI-mirroring gate) — full suite green apart from 6 pre-existing
  failures confirmed unrelated to this change (LLM chat provider routing,
  Gemini Live `google.genai` import, a settings-liveness artifact staleness
  that was self-inflicted by adding a new test file and is now fixed by
  regenerating `docs/settings_liveness.json`).
