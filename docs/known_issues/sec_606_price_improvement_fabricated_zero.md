# SEC Rule 606 "price improvement" was structurally always $0.00/0% in production — a fabricated, not measured, zero

**Status: Fixed and verified.** Found during a secondary audit pass (2026-08-24)
scoped to `execution/multi_broker_gateway.py`'s NBBO/price-improvement math — which
turned out to contain none: the audit's first, and most important, finding was that
the file it was asked to look at has zero NBBO/price-improvement logic at all. The
real logic lives in `data/execution_audit_store.py` (the computation) and
`execution/sec_rule_606_reporter.py` (the aggregation/reporting), both audited here
instead.

## How this was found

Tracing the real production write path for every field the SEC Rule 606 report
consumes: `execution/order_manager.py::OrderManager._record_execution_audit` (the
one production caller of `ExecutionAuditStore.record_audit`, fired on every real
fill) → `data/execution_audit_store.py::_build_record_dict` (computes
`price_improvement`) → `execution/sec_rule_606_reporter.py::_compute_report`
(aggregates it into a regulatory-shaped report).

## Root cause

`_record_execution_audit` never passed `nbbo_bid`/`nbbo_ask` — by design at the time,
per its own (since-corrected) docstring claim that "this class has no generic NBBO
source across brokers." `calculate_price_improvement(nbbo_bid=None, ...)` then
returns `0.0` — a deliberate, CONSTRAINT #4-compliant choice for the *function's own*
contract (never raise, never fabricate a nonzero number). The column itself,
`ExecutionAuditRecord.price_improvement`, is `nullable=False, default=0.0`, so this
`0.0` was persisted identically for two structurally different situations:

1. A genuine measurement: NBBO was available, and the fill was compared against it
   and found to be exactly at (or worse than) the best price. A real `$0.00`.
2. An unmeasurable order: NBBO was never available at all. Also `$0.00`, but for a
   completely different reason — there was nothing to measure against.

Since production NBBO coverage was ~0% (nothing ever populated the two fields), case
2 dominated: `GET /pilots/execution/sec-606/report`'s `overall_price_improvement_rate`
and `total_price_improvement_dollars` were **structurally always zero, every
quarter, regardless of actual execution quality** — a regulatory-shaped compliance
report presenting an artifact of missing instrumentation as a measured fact. A
second, related dead-code bug compounded this: `classify_limit_order` (the NBBO-aware
Marketable-vs-Non-Marketable Limit classifier) existed, was unit-tested, and had zero
write-path callers — `normalize_order_type` unconditionally mapped any "limit" order
to `Non-Marketable Limit` regardless of the order's real marketability, so the
"Marketable Limit" category was structurally unreachable in every real report too.

## Fix

**Store (`data/execution_audit_store.py`)**: a new additive column,
`ExecutionAuditRecord.nbbo_available` (`Boolean, nullable=False, default=False`,
migrated via a tolerant `ALTER TABLE` on an existing DB, matching
`data/paper_account_store.py`'s established idiom), stamped `True` only when both
`nbbo_bid` and `nbbo_ask` were genuinely supplied and finite. `price_improvement`
itself is left as a `0.0`-default NOT NULL column (a real schema-migration decision,
not revisited here) — `nbbo_available` is what now distinguishes "measured zero"
from "unmeasurable," rather than relaxing the column's nullability.
`classify_limit_order` is now actually wired into `_build_record_dict`: when a real
`limit_price` AND real NBBO are both present, the order's category is recomputed via
the genuine classifier instead of `normalize_order_type`'s blind default. A new
`limit_price` field flows from `OrderIntent.limit_price` through
`order_manager.py::_record_execution_audit`.

**Reporter (`execution/sec_rule_606_reporter.py`)**: every price-improvement RATE
(overall, per-category, per-venue) is now denominated by NBBO-covered orders, not
total orders — an uncovered order can never be "improved" by construction, so
dividing by total orders silently deflated the rate toward 0% as coverage dropped.
Dollar SUMS are untouched (an uncovered order correctly contributes $0.00 to a sum;
only the *rate* had the fabrication problem). A new `nbbo_coverage_pct`/
`nbbo_covered_orders_count` pair is surfaced in the summary, every category
breakdown, and every venue breakdown, so "0% improved" can never be read without
also seeing "0% measurable" — and the Markdown summary now prints an explicit
warning banner whenever coverage is below 100%. `expected_cols`'s `nbbo_bid`/
`nbbo_ask` defaults were also fixed from a fabricated `0.0` to `NaN` (only reachable
via a hand-built `generate_report_for_records()` call missing these keys — the real
`get_records()` path already supplied `None` correctly).

**`execution/fix_gateway.py::synthesize_nbbo`** (the only NBBO *synthesis* code in
the repo, currently entirely simulated data — see below) gained a defensive
crossed-market guard: `best_ask < best_bid` now fails closed to
`spread=None, mid_price=None` with a logged warning, instead of silently propagating
a negative spread. Unreachable today (every self-generated venue quote individually
satisfies `bid < ref_px < ask`, so a crossed market is mathematically impossible
through this function's own construction) but real insurance for the moment it
accepts externally-supplied per-venue quotes. The NBBO derivation was factored into
a standalone pure function, `derive_nbbo_from_venue_quotes`, specifically so this
guard could be unit-tested with a hand-constructed crossed input — `synthesize_nbbo`
alone can never reach it.

## Verification

- `tests/test_sec_rule_606_reporter.py`: 25 passed (21 pre-existing + 4 new — zero
  coverage reports honest zero-coverage rather than a fabricated rate, partial
  coverage denominates correctly, `classify_limit_order` reclassifies when
  `limit_price`+NBBO are present, and stays at the old default when `limit_price` is
  absent). Existing tests' fixtures were updated to include real `nbbo_bid`/
  `nbbo_ask` where they previously relied on an explicit `price_improvement`
  override with no backing NBBO data.
- `tests/test_order_manager_execution_audit_wiring.py`,
  `tests/test_order_manager_idempotency.py`: 14 passed, unaffected by the
  `limit_price` passthrough.
- `tests/test_fix_gateway.py`: 57 passed (55 pre-existing + 2 new — normal
  uncrossed-quote sanity check, and the crossed-market guard via the extracted pure
  function).

## What this does NOT fix / disclosed scope

- **No real NBBO source is wired into production yet.** `nbbo_available` is
  additive instrumentation that makes the *absence* of NBBO honest — it does not
  create NBBO where none exists. `data.market_data.Quote` does carry a real `bid`/
  `ask` from whichever provider is configured, and could in principle populate these
  fields, but doing so was deliberately deferred: it would add a synchronous network
  call to the post-fill audit hot path with no bounded timeout (a latency/reliability
  tradeoff that deserves its own design pass), and a single provider's quote is at
  best an approximation of a true cross-exchange NBBO, which should be labeled
  honestly if wired in rather than folded quietly into an audit-fixing change. Until
  that follow-up lands, every real SEC 606 report will continue to show
  `nbbo_coverage_pct: 0.0` — now honestly, instead of silently.
- `execution/fix_gateway.py::synthesize_nbbo`'s NBBO synthesis remains entirely
  simulated (`random.uniform`-generated per-venue quotes) — it is never called from
  the real audit-recording write path, only from the explicitly-simulated
  `POST /pilots/execution/fix/route` demo endpoint.
