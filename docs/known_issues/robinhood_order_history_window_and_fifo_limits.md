# Known limitation: Robinhood order-history ingest has a real, disclosed ceiling

**Status (2026-08): by design, not a bug — documented so the Trade History screen and the
underlying data never implicitly claim to be a complete ledger.**

`data/robinhood_orders.py`'s FIFO reconstruction, now durably persisted by
`data/broker_fills_store.py` and ingested during every `--refresh-account` device-approval
login (`data/robinhood_login_worker.py`), is built entirely on top of `robin_stocks`'
`get_all_stock_orders()`. That single API call defines everything this feature can and
cannot see.

## 1. Equities only

`get_all_stock_orders()` returns Robinhood **equity** orders only. Options fills and crypto
activity are entirely invisible to this pipeline — there is no fallback, no partial coverage,
and no warning surfaced per-symbol, because the gap is structural, not per-request. The
Trade History screen's copy states this plainly rather than implying it's a complete record
of "everything you traded."

## 2. Short sales are dropped, not fabricated

`reconstruct_closed_trades()` matches sells against the oldest open buy lot (FIFO), per
symbol. A sell that exceeds the available open lots — a genuine short sale, or a buy that
predates whatever window the API happened to return — has the unmatched excess **logged and
dropped**, never invented as a zero-cost entry (CONSTRAINT #4). This means:

- A short-selling account's closed round-trips are underrepresented, not wrong — the ones
  that ARE reconstructed are real, but short positions never appear at all.
- A very old position (bought before the earliest order Robinhood's API returns for this
  account) will produce a sell with no matching entry the first time it's sold — dropped the
  same way.

## 3. The persisted-fill union is what makes this shrink over time, not grow

Unlike a naive "cache the API response" design, `data/broker_fills_store.py` persists
**fills** (keyed by Robinhood's own `order_id`), not reconstructed trades, and the persisted
set only ever grows across every ingest. Concretely: if today's fetch window happens to miss
an old buy (case 2 above), and a FUTURE Robinhood API response includes that missing buy
(e.g. Robinhood's own window widens, or the operator's account history simply accumulates
more visible history over time), the next ingest will correctly pick it up and the
previously-dropped sell will reconstruct correctly on the NEXT read — because
`closed_trades()` re-runs FIFO over the full persisted fill history every time, not a cached
result. This is a real, measured design property, not a promise about what Robinhood's API
will actually return in the future (which this codebase cannot verify or control).

## 4. Divergent re-fetch handling is a correction, not a discrepancy

If a previously-ingested fill's reported `quantity`/`price`/`filled_at` differs on a later
fetch (Robinhood corrected or finalized the order after the fact), `BrokerFillsStore.record_fills`
keeps the LATEST value and logs a WARNING — the assumption being that a filled order's final
`cumulative_quantity`/`average_price` is authoritative. This has not been observed against a
real account; it's a designed-for case, not a confirmed one.

## What is, and isn't, verified

**Verified:** the pure FIFO reconstruction logic (`tests/test_robinhood_orders.py`, 29+ cases
including partial-lot splits, multi-symbol isolation, excess-sell dropping, chronological
ordering) and the durable store's idempotency (`tests/test_broker_fills_store.py` — re-ingesting
the same fills twice inserts nothing new and reports identical realized P&L).

**Not verified, and cannot be from a sandboxed dev environment with no real Robinhood account
in hand:** whether a real account's full order history fits inside the ingest's wall-clock
budget on a cold first run (see `robinhood_device_approval_login_hang_risk.md`'s follow-up
section), and whether the divergent-refetch case (§4) actually occurs in practice.
