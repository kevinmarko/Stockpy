# Paper Broker: Closed Trades + Strategy Attribution UI

## Context

PR #872's remediation pass added a real `paper_closed_trades` table
(`PaperClosedTrade` in `data/paper_account_store.py`) that records realized
PnL, entry/exit prices, holding period, and close reason every time a paper
position is flattened, rolled, or expires — and it already stamps
`strategy_id`/`pilot_id`/`experiment_arm` onto every `PaperPosition` and
`PaperOrder` row too. None of this is reachable from outside the backend: no
API endpoint reads `paper_closed_trades`, and the webapp's Paper Broker
screen renders neither a closed-trades history nor any attribution column,
even though the Orders table's backend response already includes
`strategy_id` today. The user asked to close this gap: add a read endpoint +
webapp section for closed trades, and surface strategy attribution on the
existing Positions/Orders tables.

Confirmed while investigating: `pilots/paper_broker.py::get_positions()` has
a small existing bug — it manually rebuilds its response dict from
`PositionSnapshot` (which already carries `.strategy_id`/`.pilot_id`/
`.experiment_arm`) but drops those three fields. Fixing it is in scope here
since it's the same gap for Positions that this whole task is about closing
for Orders/Closed Trades.

This touches `data/paper_account_store.py` and `api/pilots_api.py` (paper-
trading data layer), so per this repo's branch workflow it needs its own
branch + PR, not a direct `main` commit.

## Backend

**`data/paper_account_store.py`** — add `get_full_closed_trades(symbol=None,
limit=100)` right after `get_full_orders` (~line 1482), mirroring it
exactly: the `readonly` → `has_table("paper_closed_trades")` guard
(try/except, return `[]` on any inspection failure), `session_scope` query,
optional `.filter_by(symbol=symbol.upper())`, `.order_by(exit_ts.desc())
.limit(limit)`, and a snake_case dict per row covering every
`PaperClosedTrade` column. `realized_pnl_pct` passes through raw — never
coerce `None` to `0.0` (CONSTRAINT #4; the column already carries this
convention). `entry_ts` is nullable — guard before `.replace(tzinfo=...)
.isoformat()`; `exit_ts` is not.

**`pilots/paper_broker.py`**:
- Fix `get_positions()`: add `strategy_id`/`pilot_id`/`experiment_arm` to
  the returned dict, sourced from the `PositionSnapshot` already in scope.
- Add `get_closed_trades(symbol=None, limit=100)`, a pure passthrough to
  `PaperAccountStore(readonly=True).get_full_closed_trades(...)`, matching
  `get_orders()`'s shape.

**`api/pilots_api.py`** — add, right after `GET /pilots/paper-broker/orders`
(~line 5756):
```python
@app.get("/pilots/paper-broker/closed-trades", dependencies=[Depends(require_read_token)])
def get_paper_broker_closed_trades(symbol: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    from pilots.paper_broker import get_closed_trades
    return get_closed_trades(symbol=symbol, limit=limit)
```
Same fail-open read tier as positions/orders — no new flag needed, this is a
`GET` over already-persisted, non-sensitive state.

## Webapp

**`webapp/src/api/types.ts`**:
- Extend `PaperBrokerPosition` and `PaperBrokerOrder` with `strategy_id:
  string | null; pilot_id: string | null; experiment_arm: string | null;`
  (Orders' backend response already sends these; Positions will after the
  backend fix above).
- New `PaperBrokerClosedTrade` interface, flat snake_case matching the store
  dict field-for-field (`trade_id`, `strategy_id`, `pilot_id`,
  `experiment_arm`, `symbol`, `side`, `qty`, `entry_ts`, `entry_price`,
  `exit_ts`, `exit_price`, `commission`, `realized_pnl`,
  `realized_pnl_pct: number | null`, `holding_period_days: number | null`,
  `close_reason`, `leg_group_id: string | null`).

**`webapp/src/api/client.ts`** — one-liner next to `getPaperBrokerOrders`:
`getPaperBrokerClosedTrades: (limit = 100, symbol?: string) => http<PaperBrokerClosedTrade[]>(...)`.

**`webapp/src/api/mock.ts`**:
- New `let paperClosedTrades: PaperBrokerClosedTrade[] = [];` alongside
  `paperPositions`/`paperOrders`; `getPaperBrokerClosedTrades` one-liner
  returning it; clear it in `resetPaperBroker`.
- Wire the existing SELL/flatten-to-zero mock branches (`:~9740, 9826,
  9897`) to also push a synthetic `PaperBrokerClosedTrade` record when a
  position fully closes, computing `realized_pnl` from the fill vs. the
  closing position's `avg_cost` (sign-adjusted for short covers — confirm
  each branch's side semantics before copying the formula). Scope this
  narrowly: construct-and-push at each site, no broader mock refactor.
- Stamp `strategy_id/pilot_id/experiment_arm: null` (or real values already
  threaded, e.g. `execute_roll`'s manual-trade tag) onto the existing
  `paperPositions.push`/`paperOrders.unshift` calls so the new optional
  fields aren't silently `undefined` in mock mode.

**`webapp/src/screens/PaperBroker.tsx`**:
- `closedTrades = useApi(() => api.getPaperBrokerClosedTrades(100))` next to
  `orders` (~line 89).
- Positions table (~1354-1476): new `Strategy` `<th>`/`<td>` column (after
  Symbol); render `p.strategy_id` raw when present, `"—"` only on a genuine
  `null`. Per `docs/known_issues/paper_trade_strategy_id_vocabulary.md`,
  `"untagged"` and `"Manual Trade"` are intentional, documented buckets —
  **do not** substitute `"—"` for them, only for a real null. Bump the
  loading/error/empty `colSpan` from `10` to `11`.
- Orders table (~1481-1529): same `Strategy` column treatment; bump
  `colSpan` from `7` to `8`.
- New **Closed Trades** section, placed after the Orders table, matching the
  Positions/Orders structure exactly (`<h2>` → bordered `theme.surface` div
  → `<table>` → 4-state loading/error/empty/populated `<tr>` block with the
  literal strings `"Loading closed trades..."` / `"Failed to load closed
  trades: {closedTrades.error}"` / `"No closed trades"`, matching this
  file's existing test-string convention). Columns: Exit Time, Symbol, Side,
  Qty, Entry Price, Exit Price, Realized P&L ($, colored via
  `theme.growth`/`theme.decline`), Realized P&L (%, rendering `"—"` when
  `null` — never `0.00%`), Strategy, Close Reason.

## Tests

- `tests/test_paper_account_store.py`: new `get_full_closed_trades` tests —
  content/attribution correctness (reuse the existing fill-sequence and
  `strategy_id="untagged"` fixture patterns already in this file), a
  readonly cold-start case (`readonly_store.get_full_closed_trades() == []`
  on a missing DB file, mirroring `test_readonly_degradation`), and a
  `limit`/`symbol` filter case.
- `tests/test_pilots_paper_broker.py`: update `test_get_positions` to assert
  the fixed attribution fields appear (this is the regression test for the
  bug fix); new `test_get_closed_trades` mirroring `test_get_orders`; new
  HTTP-level test(s) for `GET /pilots/paper-broker/closed-trades` following
  `TestStrategyOptionsEndpoints`'s pattern (200 shape + passthrough, 401 on
  a wrong token).
- `webapp/src/screens/PaperBroker.test.tsx`: extend the main render test to
  mock `getPaperBrokerClosedTrades` and assert a row renders; new
  loading/error cases for the Closed Trades section; a case asserting
  Strategy renders correctly on Positions/Orders. Audit every existing
  `mockResolvedValue`/`mockRejectedValue` call on
  `getPaperBrokerPositions`/`getPaperBrokerOrders` in this 854-line file and
  add the matching `getPaperBrokerClosedTrades` stub wherever needed, so the
  new `useApi` call doesn't hang other tests.

## Docs

- `docs/architecture/execution.md`'s existing `data/paper_account_store.py`
  bullet: append a clause noting `get_full_closed_trades()` is the new read
  path backing `GET /pilots/paper-broker/closed-trades`.
- `docs/architecture/webapp-and-gui.md`: add a new bullet for the Paper
  Broker screen (none exists today) covering the Positions/Orders/Closed
  Trades tables and the strategy-attribution columns.
- CLAUDE.md (auto-syncs to AGENTS.md): one dated bullet, sized
  proportionately to this change (smaller than the PR-872 remediation
  bullet) — the new endpoint, the new UI section, and the
  `get_positions()` attribution bug fixed along the way.

## Branch & PR

- Branch off current HEAD (identical to `main`):
  `feat/paper-broker-closed-trades-attribution`.
- Single PR bundling backend + webapp + tests + docs (matches this repo's
  norm for a feature of this size).

## Verification

1. `pytest tests/test_paper_account_store.py tests/test_pilots_paper_broker.py -v`
2. Full offline gate via the `verify` skill (ruff + offline pytest) before
   calling it done — this touches `data/`, `pilots/`, `api/`.
3. `npm run --prefix webapp typecheck` (types ripple through
   `client.ts`/`mock.ts`/`PaperBroker.tsx`).
4. `npm run --prefix webapp test -- PaperBroker` for the updated component
   test file.
5. UI-visible change → run the `verify-webapp` skill: launch the PWA,
   navigate to Paper Broker, place then sell a mock stock order to trigger
   the new synthetic closed-trade mock record, confirm the Closed Trades
   section renders and Strategy columns show sensible values on
   Positions/Orders.
