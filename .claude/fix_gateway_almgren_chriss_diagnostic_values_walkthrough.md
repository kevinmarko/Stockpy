# FIX gateway + Almgren-Chriss router: fabricated diagnostic values fixed

Branch: `fix-hardcoded-fix-almgren-diagnostic-values`

## Bug 1: `POST /pilots/execution/fix/session/test-request` returned a hardcoded `round_trip_ms`

**Location:** `api/pilots_api.py`, `post_pilots_execution_fix_session_test_request` (the handler
for `/pilots/execution/fix/session/test-request`). The underlying primitives it calls
(`FixSession.send_test_request`, `FixSession.simulate_receive`) live in `execution/fix_gateway.py`.

**Before:** the handler called `session.send_test_request(...)`, synthesized a `Heartbeat` response,
fed it through `session.simulate_receive(...)`, and then unconditionally returned
`"round_trip_ms": 1.25` — a constant, regardless of how long the send/receive actually took.

**Fix:** wrapped the send/receive pair with `time.perf_counter()` (a monotonic clock, immune to
system-clock adjustments, and already imported at module top) and returned the real elapsed
delta in milliseconds:

```python
t_start = time.perf_counter()
session.send_test_request(test_req_id=tid)
hb_resp = Heartbeat(session.target_comp_id, session.sender_comp_id, session.inbound_seq_num, test_req_id=tid)
session.simulate_receive(hb_resp)
round_trip_ms = (time.perf_counter() - t_start) * 1000.0
...
"round_trip_ms": round_trip_ms,
```

Since this gateway is fully simulated (no real network hop — `execution/fix_gateway.py`'s
`FixSession.send_test_request`/`simulate_receive` are synchronous in-process calls), the measured
value is genuinely tiny (sub-millisecond in practice). That's an honest reflection of "no real
network round trip occurred," not a regression — the field is a real measurement now rather than
a fabricated one.

**Response shape:** unchanged (`round_trip_ms` stays a plain `float`, always present, never null) —
no webapp type changes needed for this endpoint.

### Tests

- `tests/test_pilots_api.py::TestFixGatewaySessionEndpoints::test_post_fix_session_test_request_success`
  — extended to assert `round_trip_ms` is a real non-negative number.
- `tests/test_pilots_api.py::TestFixGatewaySessionEndpoints::test_post_fix_session_test_request_round_trip_reflects_real_elapsed_time`
  — new. Injects a real `time.sleep()` into `FixSession.simulate_receive` (this repo's established
  pattern for timing-sensitive tests — see `tests/test_market_data.py`'s `time.sleep`-based latency
  tests) and asserts the returned `round_trip_ms` reflects the injected delay, and that a longer
  injected delay produces a larger measured value.
- `tests/test_fix_gateway.py` — three new tests exercising the same endpoint end-to-end
  (`test_fix_session_test_request_round_trip_ms_is_measured_not_hardcoded`,
  `test_fix_session_test_request_round_trip_ms_varies_with_injected_delay`,
  `test_fix_session_test_request_round_trip_ms_reflects_real_unmocked_timing`), the last of which
  runs with no clock mocking at all and just asserts the value is a small, real, non-negative
  float rather than the old `1.25` literal.

Note: an earlier draft of these tests tried to fully control `time.perf_counter()` globally via a
patched iterator. That broke because FastAPI/Starlette/anyio internals make their own untracked
`perf_counter()` calls while dispatching an `async def` endpoint through `TestClient`, exhausting a
naive 2-value queue (`StopIteration` inside the ASGI stack) or silently consuming the controlled
values before the endpoint's own code ran. The final version instead injects a real, measurable
`time.sleep()` into the FIX session's own `simulate_receive` call — a pattern already used
elsewhere in this repo (`tests/test_market_data.py`) — which sidesteps the internal-call-count
problem entirely.

## Bug 2: `POST /pilots/execution/optimize/almgren-chriss` computed `expected_price` off a hardcoded `100.0` base

**Location:** `api/pilots_api.py`, `post_execution_optimize_almgren_chriss`. The pure trajectory
math it wraps lives in `execution/almgren_chriss_router.py::compute_trading_trajectory` (unchanged
by this fix — it never had a price concept, only shares/impact-cost math).

**Before:**

```python
"expected_price": 100.0 - (0.01 * (req.quantity - traj_arr[i + 1])) # Dummy impact price
```

regardless of `req.symbol`'s real current price.

**Fix:** source the real current spot price via `pilots.price_provider.get_latest_price(req.symbol)`
— the same `data.market_data.CompositeProvider`-backed helper the real-time risk streamer
(`pilots/realtime_risk_streamer.py`) already uses for exactly this purpose, per this codebase's
market-data-layer convention (`CLAUDE.md`: "All quote/bar/fundamentals fetches outside the existing
`DataEngine.fetch_technical_raw()` path MUST go through `CompositeProvider`"). `get_latest_price`
returns `0.0` (its documented "no live quote available" sentinel) rather than raising, so the
handler treats `spot_price <= 0.0` as unavailable and degrades honestly instead of fabricating
another placeholder number (CONSTRAINT #4):

```python
spot_price = get_latest_price(req.symbol)
spot_price_available = spot_price > 0.0

for i in range(len(trade_arr)):
    expected_price = (
        spot_price - (0.01 * (req.quantity - traj_arr[i + 1]))
        if spot_price_available
        else None
    )
    trajectory.append({... "expected_price": expected_price})

return {
    ...
    "spot_price": spot_price if spot_price_available else None,
    "spot_price_reason": (
        None if spot_price_available
        else f"No live quote available for {req.symbol}; expected_price omitted."
    ),
}
```

When no live quote exists, every trajectory point's `expected_price` is `null` and the response
carries a `spot_price_reason` explaining why — the rest of the response (the real
`expected_shortfall`/`variance`/`half_life` math, which has no dependency on price) is still
returned normally.

**Response shape changed:**
- `AlmgrenChrissTrajectoryPoint.expected_price` is now `number | null` (was `number`).
- `AlmgrenChrissOptimizeResponse` gained two new optional fields: `spot_price?: number | null` and
  `spot_price_reason?: string | null`.

Updated `webapp/src/api/types.ts` to match, and `webapp/src/api/mock.ts`'s
`optimizeAlmgrenChriss` mock to also return `spot_price: 100.0, spot_price_reason: null` for
mock/live parity (the mock's own per-step price synthesis was left as-is — it was already a
plausible mock value, not the bug; only the LIVE endpoint fabricated a base price masquerading as
real data). `AlmgrenChrissRouterView.tsx` never renders `expected_price` directly (only
`shares_remaining`/`trade_size` are charted), so the nullable type change needed no UI changes.

### Tests

`tests/test_almgren_chriss_router.py` — three new endpoint-level tests (the pure-math tests already
in this file were untouched, since `compute_trading_trajectory` itself didn't change):

- `test_almgren_chriss_endpoint_uses_real_spot_price_as_impact_base` — mocks
  `pilots.price_provider.get_latest_price` to return `250.0` and asserts every trajectory point's
  `expected_price` is anchored near that real price, not `100.0`.
- `test_almgren_chriss_endpoint_different_spot_price_changes_expected_price` — two requests with
  different mocked spot prices (`50.0` vs `500.0`) must produce materially different
  `expected_price` values, proving the base price is read per-request rather than a disguised
  constant.
- `test_almgren_chriss_endpoint_degrades_honestly_when_no_live_quote` — mocks
  `get_latest_price` to return `0.0` (the documented unavailable sentinel) and asserts
  `spot_price` is `null`, `spot_price_reason` is a non-null string naming the symbol, every
  trajectory point's `expected_price` is `null`, and the rest of the response
  (`expected_shortfall`/`variance`/`half_life`) is still populated.

## Verification performed

- `npm run --prefix webapp typecheck` — clean, no errors (after `npm install`, since
  `webapp/node_modules` wasn't present in this worktree).
- `npx vitest run src/api src/components/execution` (webapp) — 125 tests passed, including
  `AlmgrenChrissRouterView.test.tsx` unaffected by the nullable-type change.
- `/Users/kevinlee/Stockpy-live/.venv/bin/python -m pytest tests/test_fix_gateway.py tests/test_almgren_chriss_router.py -q`
  — 57 passed (this worktree had no `.venv` of its own; reused the sibling checkout's venv, same
  Python 3.12 interpreter and installed dependencies `setup.sh` would have produced).
- `/Users/kevinlee/Stockpy-live/.venv/bin/python -m pytest tests/test_pilots_api.py tests/test_fix_gateway.py tests/test_almgren_chriss_router.py -q`
  — 492 passed (full `test_pilots_api.py` suite plus the two targeted files, confirming no
  regression elsewhere in the Pilots API test surface). The `RuntimeWarning`s emitted during this
  run come from an unrelated, pre-existing `ml/transformer_vol_forecaster.py` test and are not
  connected to this change.

## Scope note

Per the task instructions, this change is strictly scoped to the two hardcoded-value bugs above.
No other finding from the broader Paper Broker / options-desk audit was touched.
