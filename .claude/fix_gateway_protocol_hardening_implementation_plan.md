# FIX gateway protocol-correctness fixes (F1–F5) — Implementation Plan

## Context

An independent from-scratch protocol-correctness audit of `execution/fix_gateway.py`
(this repo's fully-simulated FIX 4.4 gateway — no real venue network calls, so nothing
here has touched real capital) found five real integrity gaps in the simulation's own
message-framing and session-lifecycle layers, plus two trivial adjacent issues. Checksum
algorithm, BodyLength-on-write, and the sequence-gap-detection/resend/drain state machine
were independently verified correct and are untouched. This plan closes the five findings,
the two minor items, adds regression tests for each, and corrects
`docs/architecture/execution.md`'s "Strict `FixSessionState` lifecycle management" language
(already self-contradicted later in the same bullet, which says "WARNING, not a hard
block") to honestly describe what is enforced vs. warn-only vs. newly-added.

This is an `execution/` tier change → feature branch + PR per `CLAUDE.md`'s workflow.
Branch: `fix-gateway-protocol-hardening`.

## Findings and fixes (summary — see PR diff for full detail)

- **F1 (HIGH)** — `from_fix_str()` silently accepted a message with Tag 10 (CheckSum)
  entirely missing (e.g. a transport-truncated message), skipping all integrity checking.
  Fixed: `validate_checksum=True` now unconditionally requires Tag 10 to be present and
  locatable, raising `FixChecksumError` otherwise.
- **F2 (MEDIUM)** — Tag 9 (BodyLength) was computed correctly on write but never
  independently verified on receipt; a tampered BodyLength combined with a checksum
  recomputed over the tampered prefix parsed cleanly. Fixed: `from_fix_str()` now verifies
  the actual body byte count against Tag 9 when present, raising `FixParseError` on
  mismatch.
- **F3 (HIGH)** — Zero SOH/`=`/`|` injection protection in the message layer itself (only
  `api/pilots_api.py`'s `FixRouteOrderRequest.symbol` guarded this at the API boundary).
  Fixed: new `FixValueError` + `_reject_fix_delimiter_chars()` helper, called from both
  `set_tag()` and the universal `to_fix_str()` choke point (covers header fields and every
  tag, regardless of which of the 11 message-subclass constructors populated it).
  Required a pre-fix cleanup: `MultiVenueAggregator.route_order()`'s cosmetic `|`-separated
  `Text` field was reformatted to use `/` instead.
- **F4 (HIGH)** — The order lifecycle (`OrdStatus`) had zero transition-legality
  enforcement — a Filled order could be reverted to New via a spoofed/buggy
  `ExecutionReport`. Fixed: a new, narrowly-scoped `_is_legal_order_transition()` guard in
  `_process_message_payload`'s `EXECUTION_REPORT` branch only (the separate
  `ORDER_CANCEL_REJECT` branch's own legitimate pending-status-revert logic is untouched)
  rejects (logs ERROR, does not apply) a terminal-status reversal or a Filled/PartiallyFilled
  → New/PendingNew reversal.
- **F5 (MEDIUM-HIGH)** — A genuinely unresponsive counterparty triggered `TestRequest`
  forever; nothing ever disconnected. Fixed: `FixSession` now tracks one outstanding
  `TestRequest` (`_pending_test_request_id`/`_sent_at`), cleared on any inbound message, and
  disconnects (`_disconnect_sync()` for the sync `check_watchdog()` path, `disconnect()` for
  the async `_heartbeat_loop()` path) if unanswered for a further `heartbeat_int`.
- **Minor #1** — the `idx == -1` checksum-boundary-not-found case now raises instead of
  silently skipping validation (folded into F1).
- **Minor #2** — `api/pilots_api.py`'s `POST /pilots/execution/fix/session/reconnect` now
  uses `session._set_state(...)` instead of a raw `session.state = ...` assignment, so it
  observes the same warn-only transition-legality logging as every other state assignment
  in the module.

## Documentation

`docs/architecture/execution.md`'s `execution/fix_gateway.py` bullet extended in place
(not a new bullet, per this doc's established convention): reworded the
self-contradictory "Strict `FixSessionState` lifecycle management" phrase, and appended a
"2026-08 protocol-correctness audit fixes" sentence describing all five fixes.

## Tests

`tests/test_fix_gateway.py` gained a new `# --- 8. Protocol-Correctness Audit Fixes
(2026-08) ---` section with one regression test per finding plus companion sanity tests
(a legal-transition-still-applies test and a reply-clears-pending-testrequest test), 9
tests total. Full file: 55/55 passing (46 pre-existing + 9 new).

## Execution note

Implemented via 4 parallel subagents (F3, F4, F5+pilots_api minor fix, tests+docs), each
working in an isolated git worktree on disjoint regions of `execution/fix_gateway.py`, then
reconciled by hand into a single working tree. All four diffs applied cleanly with zero
conflicts; the full existing test suite passed unchanged after each stage.

## Verification

1. `pytest tests/test_fix_gateway.py -q` — 55 passed.
2. `pytest tests/test_pilots_api.py -q -k fix` — 31 passed.
3. `pytest tests/test_multi_broker_gateway.py -q` — 38 passed (sibling execution suite
   sanity check).
4. Full `pytest tests/ -q -p no:randomly -m "not network"` — see walkthrough for result.
