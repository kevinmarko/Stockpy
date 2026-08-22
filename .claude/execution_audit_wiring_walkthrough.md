# Walkthrough: Fix three `execution/` audit findings

## What was wrong

An audit of `execution/` reported three bugs. All three were independently
re-verified directly against the code (not just trusted from the report)
before any fix was planned:

1. `data/execution_audit_store.py::ExecutionAuditStore.record_audit`/`bulk_insert_audits`
   were implemented and tested but had **zero production callers**
   (`grep -rn "ExecutionAuditStore\|record_audit\|..."` outside `tests/`
   returned hits only in the store module itself and
   `execution/sec_rule_606_reporter.py`, which only *reads*). The live
   `GET /pilots/execution/sec-606/report` endpoint therefore always read an
   empty table.
2. `execution/multi_broker_gateway.py::CircuitBreaker`'s docstring promises a
   three-condition OR trip (consecutive failures / error rate / latency), but
   `record_success` only logged a WARNING on a latency breach and never
   called `trip()`; `record_failure` never looked at latency at all.
   Reproduced: `cb.record_success(latency_ms=60.0)` three times in a row with
   `latency_threshold_ms=50.0` never moved `cb.state` off `CLOSED` before the
   fix.
3. `execution/order_manager.py::make_client_order_id`'s
   `f"{strategy_id}|{symbol}|{side}|{qty}|{bucket}"` canonicalization was
   unescaped. Reproduced: `('X', 'A|B', 'buy', 1.0, ts)` and
   `('X|A', 'B', 'buy', 1.0, ts)` produced byte-identical ids before the fix.

## What changed

**`execution/order_manager.py`**
- `OrderManager.__init__` gained an optional `audit_store=` param.
- New `_record_execution_audit(intent, result)`: best-effort, dead-letter-safe
  (never raises) persistence of every real fill into `ExecutionAuditStore`.
  Called from `submit_order_with_idempotency` right after a successful result
  with `filled_qty > 0`.
- `make_client_order_id`'s canonical string is now `json.dumps([...])`
  instead of a raw `|`-joined f-string.

**`execution/multi_broker_gateway.py`**
- `CircuitBreaker` gained a self-contained `_consecutive_latency_breaches`
  counter; `record_success` now trips the breaker once it reaches
  `config.max_consecutive_failures` consecutive latency breaches;
  `record_failure`/`reset` reset the counter.
- Removed the dead `CircuitBreakerConfig.auto_reset_on_heartbeat` field.

**`conftest.py`**
- New autouse fixture `_isolate_execution_audit_db_in_tests`, mirroring the
  existing `_isolate_validation_runs_db_in_tests` fixture, so the new
  implicit `ExecutionAuditStore()` construction inside `OrderManager` can
  never write to an operator's real `~/.stockpy_local/quant_platform.db`
  during a test run.

**New test file**: `tests/test_order_manager_execution_audit_wiring.py`
(4 tests) plus additions to `tests/test_multi_broker_gateway.py` (2 tests)
and `tests/test_order_manager_idempotency.py` (1 test).

**Docs**: `docs/architecture/execution.md` (4 bullets updated), `CLAUDE.md`
(1 new bullet, auto-mirrored to `AGENTS.md`), `docs/settings_liveness.json`
regenerated (side effect of adding a new test file — the artifact-freshness
gate correctly flagged the new `files_scanned` count).

## Why this design

- **`OrderManager` is the correct single hook point.** It's the only class
  every real production order path goes through (`main_orchestrator.py`,
  `broker_live_execution_mcp.py`). `MultiBrokerGateway` subclasses
  `BrokerBase` with zero production callers today, so wiring the audit trail
  into `OrderManager` means `MultiBrokerGateway` inherits it automatically
  once it's ever passed in as the `broker=`.
- **NBBO is left `None`, not fabricated.** `OrderManager` has no generic NBBO
  source across brokers. `ExecutionAuditStore._build_record_dict` already
  treats missing NBBO honestly — `price_improvement` computes to `0.0`
  rather than being invented.
- **Injectable + lazily-constructed `audit_store`.** Matches the existing
  `risk_gate`/`kill_switch` injection pattern, and avoids paying for a DB
  engine on every `OrderManager` that never fills a real order.
- **The latency-trip fix reuses `max_consecutive_failures`** rather than
  introducing a new config field with an arbitrary default — "N consecutive
  bad signals of any kind" was already the established semantic for the
  other two OR conditions.
- **`auto_reset_on_heartbeat` was removed, not implemented**, because
  inventing new reset-bypass behavior for a flag that was never wired up
  anywhere risked unintentionally weakening the breaker (a heartbeat-
  triggered reset before cooldown expiry).
- **`json.dumps` over a list, not a dict**, for the client-order-id
  canonicalization — no key-ordering ambiguity, and a list structurally
  distinguishes any field containing `|`, `"`, or `,` from a delimiter.

## Known, disclosed limitation

The execution-audit-trail fix only covers a fill returned **synchronously**
from `broker.submit_order` (true of `FMPPaperBroker`, the default paper
broker). A broker whose fills arrive later via `stream_trade_updates` (e.g. a
live async broker) is not yet covered by this hook — a disclosed follow-up,
not silently swept under the rug.

## Verification

- `python -m ruff check . --select=F821,F822,F823,E9` → all checks passed.
- Full offline suite, `python3 -m pytest -m "not network and not slow" -n auto --dist loadgroup`:
  **11967 passed, 31 skipped**, 5 failed — all 5 confirmed pre-existing and
  unrelated to this change via `git stash -u` against clean `main`
  (`test_data_api_chat.py`'s OpenAI/local-LLM routing tests and
  `test_gemini_live_chat.py`'s tests, which fail on `ImportError: cannot
  import name 'genai' from 'google'` — a missing optional dependency in this
  environment, not a regression).
- Targeted scope from the plan (`test_order_manager_execution_audit_wiring.py`,
  `test_order_manager_idempotency.py`, `test_order_manager_rate_limit.py`,
  `test_multi_broker_gateway.py`, `test_fmp_paper_broker.py`,
  `test_sec_rule_606_reporter.py`, `test_kill_switch.py`,
  `test_reconciliation.py`, `test_execution_alerts.py`) — all green.
- Confirmed the new `conftest.py` fixture actually works: ran
  `test_fmp_paper_broker.py::test_order_manager_live_submission_reaches_the_paper_broker`
  (a real fill through `OrderManager` with no `audit_store=`) and diffed the
  real `~/.stockpy_local/quant_platform.db`'s mtime before/after — unchanged.
- Root-caused a `test_settings_liveness.py::TestCommittedArtifactIsFresh`
  failure that appeared mid-implementation: NOT a bug in the fix — adding the
  new test file shifted the repo's `files_scanned` count, which the
  artifact-freshness gate correctly caught. Fixed per its own error message
  (`python3 scripts/settings_liveness.py --write`, then committed the result).
