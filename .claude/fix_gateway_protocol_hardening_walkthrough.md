# FIX gateway protocol-correctness fixes (F1–F5) — Walkthrough

## What changed and why

A from-scratch protocol-correctness audit of `execution/fix_gateway.py` (this repo's
fully-simulated FIX 4.4 gateway — zero real venue network calls, so nothing here has ever
touched real capital) found five real integrity gaps in the simulation's own message-framing
and session-lifecycle layers. Checksum computation, BodyLength-on-write, and the
sequence-gap-detection/resend/drain state machine were independently verified correct and
are untouched by this PR.

### F1 — missing CheckSum silently accepted
`from_fix_str()`'s `if "10" in tag_dict and validate_checksum:` skipped *all* integrity
checking whenever Tag 10 was simply absent — e.g. a transport-truncated message. Now
`validate_checksum=True` unconditionally requires Tag 10 to be present and locatable in the
raw string, raising `FixChecksumError` otherwise. (Folded in: the pre-existing `idx == -1`
case, previously silently skipped, now also raises.)

### F2 — BodyLength never independently verified
Tag 9 was computed correctly when writing a message but never checked against the actual
body byte count on receipt. A checksum alone can't catch a lying BodyLength, because the
checksum is just a byte-sum of whatever bytes are actually present — a tampered Tag 9 with
the checksum recomputed to match the (now-tampered) prefix is internally self-consistent by
construction. `from_fix_str()` now verifies `len(body_str.encode("latin1"))` against Tag 9
when present, raising `FixParseError` on mismatch.

### F3 — zero SOH/`=`/`|` injection protection in the message layer
Only `api/pilots_api.py`'s `FixRouteOrderRequest.symbol` validator guarded against delimiter
injection, and only at the API boundary — the protocol layer itself had no protection, so a
free-text `Text(58)` field carrying a raw SOH byte could forge an independently-legitimate
numeric tag on the wire. Added `FixValueError` + `_reject_fix_delimiter_chars()`, wired into
both `set_tag()` (the public API) and `to_fix_str()` (the actual universal choke point —
every one of the 11 message-subclass constructors writes directly to `self.tags`, bypassing
`set_tag()`, and header fields never pass through `self.tags` at all). This required a
pre-fix cleanup: `MultiVenueAggregator.route_order()`'s auto-generated `Text` field used `|`
as a cosmetic separator (the only production call site doing so, confirmed by grep) — swapped
for `/` so the new guard doesn't trip on legitimate output. Also added a WARNING log (not a
hard reject, matching the module's established warn-don't-block convention) when
`from_fix_str`'s tag-parsing loop sees a duplicate tag.

### F4 — order lifecycle had zero transition-legality enforcement
A `Filled` order could be reverted to `New` via a spoofed/buggy `ExecutionReport`, since
`_process_message_payload`'s `EXECUTION_REPORT` branch unconditionally overwrote
`order_rec["status"]`. Added a narrowly-scoped `_is_legal_order_transition()` guard —
blocks a terminal status (Filled/Canceled/Rejected/Expired/DoneForDay) reverting to anything
else, and Filled/PartiallyFilled reverting to New/PendingNew. Deliberately scoped to the
`EXECUTION_REPORT` branch only: the separate `ORDER_CANCEL_REJECT` branch has its own
legitimate pending-status-revert logic (PENDING_CANCEL/PENDING_REPLACE → NEW) and is
untouched.

### F5 — unresponsive counterparty triggered TestRequest forever
`check_watchdog()`/`_heartbeat_loop()` only ever emitted a Heartbeat/TestRequest on a timer;
there was no "outstanding TestRequest, awaiting reply by deadline X" tracking, and no code
path ever disconnected on timeout. `FixSession` now tracks one outstanding TestRequest
(`_pending_test_request_id`/`_sent_at`, cleared on any inbound message via
`simulate_receive()`) and disconnects — `_disconnect_sync()` for the sync `check_watchdog()`
path, the real `disconnect()` for the async `_heartbeat_loop()` path — if it goes unanswered
for a further `heartbeat_int`.

### Minor fixes
- `api/pilots_api.py`'s `POST /pilots/execution/fix/session/reconnect` now calls
  `session._set_state(...)` instead of a raw `session.state = ...` assignment, so it
  observes the same warn-only transition-legality logging every other state assignment in
  the module does.

## How this was built

Implemented via 4 parallel subagents, each working in its own isolated git worktree on
disjoint regions of the file (F3: exceptions/`set_tag`/`to_fix_str`/`route_order`; F4: the
new transition table + `EXECUTION_REPORT` branch; F5: heartbeat/pending-tracking methods +
the `pilots_api.py` one-liner; tests+docs). Each agent's exact diff was verified and applied
by hand into a single working tree — all four diffs applied cleanly with zero conflicts on
the first pass, and the full existing test suite (46 tests) passed unchanged after each
stage was layered in.

## Verification

- `pytest tests/test_fix_gateway.py -q` → **55 passed** (46 pre-existing + 9 new regression
  tests, one per finding plus 2 companion sanity tests).
- `pytest tests/test_pilots_api.py -q -k fix` → **31 passed**.
- `pytest tests/test_multi_broker_gateway.py -q` → **38 passed** (sibling execution suite,
  confirms no cross-import breakage from the new `FixValueError` exception).
- Full `pytest tests/ -q -p no:randomly -m "not network"` → **12056 passed, 5 failed, 31
  skipped, 88 deselected** in 330s. The 5 failures (`test_data_api_chat.py`'s
  `TestMultiProviderRouting` x3, `test_gemini_live_chat.py`'s `TestLiveChatSession` x2) were
  confirmed **pre-existing and unrelated** — reproduced identically (`ImportError` at
  `test_gemini_live_chat.py:249`) with this PR's changes fully `git stash`ed, i.e. against
  the branch's base commit. Neither failing file references `fix_gateway`/`pilots_api` at
  all.

## Documentation

`docs/architecture/execution.md`'s `execution/fix_gateway.py` bullet extended in place (the
doc's own established convention — a dated addendum, not a new bullet): reworded the
self-contradictory "Strict `FixSessionState` lifecycle management" phrase (the same bullet
already said later "WARNING, not a hard block") to
"`FixSessionState` lifecycle management (session-level transitions are WARN-only, not
hard-enforced — see below)", and appended a "2026-08 protocol-correctness audit fixes"
sentence summarizing all five fixes, placed before the bullet's existing final
"Surfaced via ..." sentence.
