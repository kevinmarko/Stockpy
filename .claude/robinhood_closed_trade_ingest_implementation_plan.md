# Robinhood Closed-Trade Ingest — Implementation Plan

## Context

The operator sold 4 stocks in Robinhood over the last ~6 months (CMCL, ARCC, PBF, IVR —
verified directly against the operator's real Robinhood account via read-only MCP calls:
17 realized round-trips total going back to 2024, most recently CMCL +$251.97 on
2026-08-20). None of these appear anywhere in Stockpy — not on the webapp Portfolio
screen, not in "the analyst" (the advisory pipeline), not in any trade journal.

Root cause, confirmed by reading the code and the operator's real local machine state
(not hypothesized):

1. **The ingest engine already exists and is unused.** `data/robinhood_orders.py`
   implements a complete, tested, Gravity-audited pipeline: `fetch_filled_orders()` →
   pure-FIFO `reconstruct_closed_trades()` → a frozen `ClosedTrade` dataclass
   (symbol/quantity/entry_ts/exit_ts/entry_price/exit_price/realized_pnl/return_pct/
   holding_days) → `realized_pnl_summary()`. It's wired to a "Realized performance"
   panel that already exists on the webapp Portfolio screen
   ([Portfolio.tsx:286-369](webapp/src/screens/Portfolio.tsx:286)).
2. **But nothing ever calls it with a real fetcher.** The only production caller,
   [pilots/realized.py:119](pilots/realized.py:119), deliberately injects
   `orders_fetcher=lambda: []` so a web request never triggers a Robinhood login —
   it's cache-only by design. On the operator's machine,
   `~/.stockpy_local/robinhood_cache/robinhood_orders.json` contains literally
   `{"fills": []}`.
3. **Even if called, it would crash.** [data/robinhood_orders.py:414](data/robinhood_orders.py:414)
   does `from data.robinhood_portfolio import _login as _rh_login` — that symbol no
   longer exists after the 2026-08 device-approval login rewrite (replaced by
   `_login_with`, gated to run only inside the isolated login worker). The
   `ImportError` is silently swallowed and degrades to the empty cache.

Confirmed: the `trades` table in `~/.stockpy_local/quant_platform.db` has **0 rows**.

**Why this also explains "the analyst" not seeing them**: `main.py`'s ticker universe
is `held positions ∪ WATCHLIST ∪ watchlist.txt ∪ discovered`. The operator's
`WATCHLIST` is empty and there's no `watchlist.txt`, so the moment Robinhood reports 0
shares, a symbol silently drops out of analysis — no "sold" state, just gone.

### Decisions made with the operator (binding on this plan)

- **A. Broker-ingested trades must NOT influence position sizing.** Production calls
  `StrategyEngine.evaluate_security()` with no `strategy_id`
  ([pipeline/production_steps.py:2339](pipeline/production_steps.py:2339)), which routes
  Kelly sizing through the **global aggregate** path
  ([strategy_engine.py:765](strategy_engine.py:765)) — reading `transactions_store`'s
  `trades` table unfiltered by strategy. Today that table is empty, so
  `_raw_kelly_or_vol_target_sizing` returns weight `0.0` for every symbol
  (`scale_in = min(1.0, 0/30) = 0.0`). Writing 17 discretionary broker trades into
  `trades` would immediately move `scale_in` to `0.567` — silently turning live
  position sizing **on** platform-wide from a manual trade history the strategy engine
  was never meant to measure. This is the single highest-stakes risk in the whole
  feature and the design must make it structurally impossible, not just
  policy-avoided.
- **B. Universe retention: configurable, default 180 days**, keyed off a symbol's
  **last SELL fill only** (a bought-but-never-sold symbol is unaffected — it's
  governed by the existing held/watchlist rules).
- **C. One PR** covering ingest repair + durable store + universe retention + a new
  Trade History screen + safe (non-sizing) analytics wiring.

---

## 1. Repair the live ingest

**Fetch orders inside the login worker, not via a second `login_blocking` call.**
`robin_stocks` auth state lives only in the process that logged in — a parent-side
`login_blocking` call has no session of its own and would just delegate to the worker
again, meaning a **second device-approval push** per refresh. That's an unacceptable
UX/risk tradeoff (`docs/known_issues/robinhood_device_approval_login_hang_risk.md`
already flags repeated-login lockout risk as unverified). One login produces both
artifacts instead.

### 1a. `data/robinhood_login_worker.py`
In the `mode == "refresh"` branch, immediately after `rp.fetch_account_snapshot(force=True)`:
- Call a new `_ingest_orders_best_effort(emit)`, wrapped in its **own** nested
  try/except so an orders-fetch failure can never flip the worker's `result` event to
  `ok: false` for a snapshot refresh that actually succeeded.
- Runs **strictly after** the snapshot write — never allowed to delay or endanger the
  artifact the caller is actually blocking on.
- **`connect` mode does not run it** — that mode only verifies credentials, on a
  tighter deadline.
- Bounded by new `settings.RH_ORDER_INGEST_BUDGET_SECONDS` (default 60) — full-history
  pagination plus one `get_symbol_by_url` HTTP call per unresolved instrument could
  otherwise approach `RH_LOGIN_DEADLINE_SECONDS` (180) and get the whole worker
  SIGKILLed mid-ingest, turning a successful refresh into a reported timeout.
- Emits `{"event":"phase","phase":"fetching_orders"}` — our own string, not scraped
  from `robin_stocks` output, so `tests/test_robinhood_login_worker.py`'s pin against
  installed `robin_stocks` source strings is untouched. Add `"fetching_orders"` to the
  `LoginPhase` `Literal` in [data/robinhood_login.py:52](data/robinhood_login.py:52),
  and to the webapp's brokerage-connect phase-label map.

### 1b. `data/robinhood_orders.py:410-419`
Replace the dead `_login` import with an explicit two-branch structure mirroring
`_fetch_live_snapshot`: inside the worker (`RH_LOGIN_WORKER == "1"`) use
`r.get_all_stock_orders()` directly; outside it, raise the existing
`RobinhoodApprovalRequired` (already defined at
[data/robinhood_portfolio.py:206](data/robinhood_portfolio.py:206)) with a message
pointing at `main.py --refresh-account`. This is caught by the existing
line-426 `except Exception` and degrades to cache exactly as today — no behavior
change for `pilots/realized.py` or `execution/receipts_store.py`, both of which
already inject their own fetchers.

Also give `_default_symbol_resolver` a persistent seed/write-back via the store (§2c)
so only the *first* ingest pays the full instrument→symbol resolution cost.

### 1c. Entry point
No new CLI flag, no new endpoint. `main.py --refresh-account` and the webapp's
brokerage refresh flow already funnel through `login_blocking(mode="refresh")` →
the worker. Gated by new `settings.BROKER_TRADE_INGEST_ENABLED` (default **`True`** —
this flag *is* the fix, a deliberate exception to "new flags default to today's
behavior").

---

## 2. Durable store — persist fills, not reconstructed trades

The JSON cache is overwritten wholesale on every fetch and has no history beyond
whatever window Robinhood's API returns. A durable store is needed for stability and
idempotency. **Persist `OrderFill`s (keyed by Robinhood's own `order_id`), not
`ClosedTrade`s** — FIFO pairing shifts whenever the API window rolls (a sell pairs
with a different buy once the original buy ages out), so any dedup key *derived from*
a `ClosedTrade` would eventually produce a duplicate row and double-count realized
P&L. Fills have a real natural key and their persisted union only ever grows;
`reconstruct_closed_trades()` — pure, tested, untouched — is simply re-run over the
full fill history on read. Recomputing FIFO over a few thousand fills costs
microseconds, so no separate closed-trades table is needed.

### New file: `data/broker_fills_store.py`
Follows this repo's established store convention exactly (see
`validation/validation_history_store.py`, `sizing/cap_audit_store.py`): own
`Base = declarative_base()`, `db_config.resolve_database_url()` /
`create_db_engine()` / `create_readonly_db_engine()` / `session_scope`, naive-UTC
datetimes, `Column(Text)` for JSON blobs, writes raise, reads degrade to
`[]`/`{}`/`0`. **Must live under `data/`, not `desktop/`** —
[tests/test_pilots_api.py:2107](tests/test_pilots_api.py:2107) AST-forbids
`api/pilots_api.py` from importing `desktop` (which transitively pulls in
`main_orchestrator`).

```python
class BrokerOrderFill(Base):        # __tablename__ = "broker_order_fills"
    id, order_id (unique, indexed — RH's natural key), symbol, side,
    quantity, price, filled_at, first_seen_at, last_seen_at, raw_json

class BrokerInstrumentSymbol(Base): # __tablename__ = "broker_instrument_symbols"
    instrument_url (PK), symbol (nullable), resolved_at   # resolver cache, §2c

class BrokerFillsStore:
    def record_fills(self, fills) -> dict           # insert-the-diff by order_id;
                                                      # a diverging re-fetch keeps the
                                                      # latest value + logs a WARNING;
                                                      # empty order_id skipped+counted,
                                                      # never given a fabricated key
    def record_instrument_symbols(self, mapping) -> int
    def all_fills(self) -> list[OrderFill]
    def closed_trades(self, *, symbol=None, limit=None, offset=0) -> list[ClosedTrade]
    def closed_trade_count(self, *, symbol=None) -> int
    def last_exit_ts_by_symbol(self) -> dict[str, datetime]   # drives §3
    def last_ingested_at(self) -> datetime | None
    def instrument_symbol_map(self) -> dict[str, str | None]

def ingest_filled_orders(*, force=True) -> dict     # fetch -> record_fills; worker calls this
def recently_closed_symbols(*, retention_days, max_symbols, now=None) -> list[str]
```

**Import direction is one-way**: the store imports `data.robinhood_orders`
(`OrderFill`, `reconstruct_closed_trades`); `data/robinhood_orders.py` never imports
the store (avoids a circular import — persistence happens at the worker call site).

### Mandatory conftest isolation (lands in the *same commit* as the store)
```python
@pytest.fixture(autouse=True)
def _isolate_broker_fills_db_in_tests(monkeypatch):
    import data.broker_fills_store as _bfs
    monkeypatch.setattr(_bfs, "resolve_database_url", lambda: "sqlite:///:memory:")
```
Mirrors `_isolate_validation_runs_db_in_tests` / `_isolate_execution_audit_db_in_tests`
in root `conftest.py`. Non-negotiable: `last_exit_ts_by_symbol()` becomes reachable
from `main._build_universe` and `portfolio_sync.build_sync_report`, both exercised by
dozens of pre-existing test files with no store of their own — without this fixture,
the write-mode constructor's `Base.metadata.create_all` alone would mutate the
operator's real `~/.stockpy_local/quant_platform.db` schema on every test run, and any
reachable write path would seed it with fake rows (this repo has been burned by
exactly this twice before).

---

## 3. Universe retention

New settings: `CLOSED_POSITION_RETENTION_DAYS` (int, default **180** — a deliberate
behavior change; `0` restores today's universe exactly) and
`CLOSED_POSITION_RETENTION_MAX_SYMBOLS` (int, default `25`, bounding the added
pipeline cost). Both universe implementations gain a 4th source via the shared
`recently_closed_symbols()` helper on the store (single source of truth), each call
wrapped in try/except → `set()` so a store failure never shrinks the universe.

### `main.py::_build_universe` ([main.py:323-395](main.py:323))
Union the retained set **last** — after the `SYMBOL_RATING_AUTO_DROP_ENABLED`
subtraction and after the `if not combined:` fallback decision, both unchanged:
```python
combined = held | watchlist | discovered
if settings.SYMBOL_RATING_AUTO_DROP_ENABLED:
    combined -= (excluded - held)          # UNCHANGED
if not combined:
    combined = DEFAULT_TICKERS or sheet2   # UNCHANGED — decided on the pre-retention set
combined |= recently_closed_symbols(...)   # 4th source, applied last, try/except -> set()
```
This placement fixes two traps in one move: (a) a recently-closed symbol has
`held=False`, so unioning it *before* the auto-drop line would let it be
immediately re-subtracted — unioning after makes retention immune by construction;
(b) unioning it *before* the empty-fallback check would silently suppress the
`DEFAULT_TICKERS`/Sheet2 fallback on an otherwise-cold account.

### `data/portfolio_sync.py::build_sync_report` (~[line 396](data/portfolio_sync.py:396))
Inject the retained set as a synthetic watchlist (`watchlists["closed:recent"] = [...]`)
just before `sym_to_lists` is built — the same mechanism already used for
`file:<name>` watchlists. This gets retention into `SyncReport.symbols`, coverage
probing, the GUI's watchlist-attribution column, and `resolve_universe` for free.

### `data/portfolio_sync.py::resolve_universe` ([lines 626-657](data/portfolio_sync.py:626))
Unlike `main.py`, `tracked` here is built from `report.symbols.keys()` *before* the
auto-drop subtraction, so the trap fires on this path unless the protected set is
widened to include `report.watchlists["closed:recent"]` alongside `held_now`.

### `async_sync_now` — a real leak this plan closes ([portfolio_sync.py:705-717](data/portfolio_sync.py:705))
This function persists `sorted(report.symbols.keys())` to `.env`'s `DEFAULT_TICKERS`,
which `resolve_universe` unions in **unconditionally** and which never expires. Left
as-is, every retained symbol would get permanently baked into `DEFAULT_TICKERS` on
the next sync — silently converting a 180-day retention window into forever, and
surviving even after `CLOSED_POSITION_RETENTION_DAYS` is set back to `0`. Fix:
exclude the synthetic `"closed:recent"` list from what gets persisted.

`main_orchestrator.py` needs **no change** — confirmed its `build_universe_fn` is
always pre-resolved (`lambda *a: tickers`).

---

## 4. Trade History screen

### Backend
**New `pilots/trade_history.py`** (not an extension of `pilots/realized.py`, whose
docstring pins it as strictly cache-capped-at-100 for the Portfolio summary use case)
reusing `realized.py`'s existing null-shaping helpers (`_summary_to_json`,
`_trade_to_json`) rather than re-deriving them:
```python
def trade_history_view(*, limit=50, offset=0, symbol=None) -> dict:
    # {trades, summary, total, limit, offset, symbols, available,
    #  source: "durable_store", last_ingested_at}
    # summary computed over the FULL filtered set, not just the page.
    # Reads BrokerFillsStore(readonly=True) via a lazy import. Never raises.
```
Needs an override entry in `tests/test_pilots_strategy_matrix.py`'s auto-discovered
dependency-light allowlist (~line 560) — new `pilots/*.py` files are picked up by glob
and fail by default.

New endpoint on `api/pilots_api.py`, copying `GET /portfolio/realized`
([pilots_api.py:1635](api/pilots_api.py:1635)) exactly: `GET /portfolio/trade-history`,
`dependencies=[Depends(require_read_token)]` (fail-open read tier), one-line body.

### Frontend — `.claude/skills/new-pwa-screen/SKILL.md` fixed order
1. `types.ts` — new `TradeHistoryPage` reusing existing `RealizedSummary`/`RealizedTrade`.
2. `client.ts` — `getTradeHistory()` with an explicit `http<TradeHistoryPage>` generic.
3. `mock.ts` — honesty fixtures: cold-start (`available:false`), a row with null
   `return_pct`/`holding_days`, a summary with null `profit_factor` (no losing
   trades), `total > limit` (paging), and a negative-P&L row (tone branches).
4. `screens/TradeHistory.tsx` — summary tiles + paginated table + symbol filter;
   reuses `Loading`/`EmptyState`/`ErrorState`; `null` renders `"—"`, never `0`.
5. `App.tsx` route `/trade-history`.
6. `navigation.tsx` entry, `section: "research"` (not `primary` — that bar stays
   Dashboard/Portfolio/Activity/Agent), reachable via the mobile "More" modal.
7. Two extra discovery paths: a Marketplace "Explore" tile, and a **"See all →"**
   link added to the existing Portfolio "Realized performance" panel header (which
   today silently truncates to 8 rows).
8. `helpContent.ts` — `TAB_HELP["trade-history"]` entry + `<TabGuide tabKey=...>` in
   the screen.
9. `TradeHistory.test.tsx` — must explicitly assert
   `getByTestId("tab-guide-trade-history")` (a missing/typo'd `TAB_HELP` key renders
   `null` with no test failure otherwise), plus the honesty branches above.

Gates: `npm run typecheck`, `npm test`, `npm run build`.

---

## 5. Safe analytics wiring — everything that must NOT touch sizing

Nothing writes `transactions_store.trades`. Broker data lives entirely in
`broker_order_fills`, a table no sizing path reads. New AST-guard test
(`tests/test_broker_fills_store.py`) asserts: `data/broker_fills_store.py` never
imports `transactions_store`, and no module under `sizing/` or `execution/` imports
`data.broker_fills_store`.

Confirmed `trades`-table readers that stay untouched and why:
- `strategy_engine.py:765` (aggregate Kelly) / `engine/advisory.py:1553` (advisory's
  own aggregate path) — **Decision A**.
- `execution/order_manager.py:400 reconcile_state` — reads *open* trades and fires
  CRITICAL drift alerts; the broker store only ever holds *closed* round-trips, so
  there's nothing to feed it and feeding it would invert the alert's meaning.
- `main_orchestrator.py:1021` per-strategy daily P&L digest — broker trades have no
  `strategy` tag, would all land under `"unattributed"`.

### One opt-in wiring: `evaluation_engine.evaluate_portfolio` ([line 463](evaluation_engine.py:463))
New `settings.EVAL_BROKER_TRADES_ENABLED` (bool, default **`False`** — preserves
today's exact NaN behavior). When `True`: for a symbol with **no** internal
`transactions_store` trade history, fall back to `BrokerFillsStore`-reconstructed
closed trades (shaped to the same columns, `side="long"`, `conviction=NaN`) so
MAE/MFE/'Edge Ratio' become real for that symbol for the first time. Internal history
always wins when present. Never writes `trades`. `calibration_curve` needs no
separate gate — its existing `dropna(subset=["conviction", ...])` drops these rows
automatically, since a broker trade genuinely has no platform-issued conviction to
report (fabricating one would violate CONSTRAINT #4).

Explicitly out of scope, with reasons: `calibration_curve` itself (structural —
no conviction), `ml/*` (reads no trades table today, nothing to wire), a materialized
closed-trades table (reintroduces the staleness problem persisting fills was meant to
solve), a separate `--refresh-orders` CLI (would mean a second device-approval push).

---

## 6. Settings summary

| Setting | Type | Default | Note |
|---|---|---|---|
| `BROKER_TRADE_INGEST_ENABLED` | bool | `True` | **This is the fix** — deliberate exception to "defaults preserve today's behavior" |
| `CLOSED_POSITION_RETENTION_DAYS` | int | `180` | Deliberate behavior change per Decision B; `0` = old universe exactly |
| `CLOSED_POSITION_RETENTION_MAX_SYMBOLS` | int | `25` | Bounds added pipeline cost |
| `RH_ORDER_INGEST_BUDGET_SECONDS` | int | `60` | Keeps in-worker orders fetch inside the 180s login deadline |
| `RH_ORDER_SYMBOL_RESOLVE_MAX` | int | `200` | Bounds the per-instrument symbol-resolution fanout |
| `EVAL_BROKER_TRADES_ENABLED` | bool | `False` | Preserves today's exact NaN MAE/MFE/Edge-Ratio behavior |

All six: add to `settings.py` (near the existing Robinhood block) + `.env.example` +
`gui/env_io.py ALLOWED_KEYS` (all non-secret, no dedicated write-endpoint needed).

---

## 7. Tests

- `tests/test_broker_fills_store.py` (new) — idempotent re-ingest (identical row
  count + identical realized P&L on a second insert), empty-`order_id` skip,
  divergent-value convergence, pagination/filter, degrade-to-empty on a torn-down
  engine, `recently_closed_symbols` boundary at exactly N days, the AST import guards.
- `tests/test_robinhood_orders.py` (extend) — non-worker path raises
  `RobinhoodApprovalRequired` and still degrades to cache; worker-env path uses
  `r.get_all_stock_orders`; resolver cache seed/write-back. All existing FIFO tests
  (incl. the positional `ClosedTrade(...)` construction at line 367) stay untouched —
  no fields added to `ClosedTrade`.
- `tests/test_robinhood_login_worker.py` / `tests/test_robinhood_login.py` (extend) —
  `_PHRASES` pin still passes; `fetching_orders` phase emitted only in `refresh`
  mode; an orders-fetch exception still yields `{"ok": true}`; budget exhaustion
  returns before the deadline; `LoginPhase` accepts the new value.
- `tests/test_universe_retention.py` (new) — retention survives auto-drop; the
  `DEFAULT_TICKERS`/Sheet2 fallback still fires when retention is the only source;
  the `MAX_SYMBOLS` cap; `CLOSED_POSITION_RETENTION_DAYS=0` byte-identical to today;
  `build_sync_report`/`resolve_universe` wiring; the `async_sync_now`
  `DEFAULT_TICKERS`-leak fix; store failure leaves the universe unaffected.
- `tests/test_pilots_trade_history.py` (new), `tests/test_pilots_api.py` (extend —
  new endpoint, existing AST guard still passes), `tests/test_pilots_strategy_matrix.py`
  (extend — new override block).
- `tests/test_evaluation_engine*.py` (extend) — flag off ⇒ byte-identical to today;
  flag on ⇒ real values for a broker-only symbol, internal history still wins,
  `trades` never written, `calibration_curve` unaffected either way.
- `webapp/src/screens/TradeHistory.test.tsx` (new) — honesty branches +
  `tab-guide-trade-history` assertion.
- **Explicit sizing-safety regression test**: after seeding `BrokerFillsStore` with
  the operator's real 17 round-trips (or a synthetic equivalent) and running a full
  cycle, `strategy_engine._raw_kelly_or_vol_target_sizing(...)` still reports the
  cold-start tag with `n=0` and `scale_in=0.0` — proving Decision A holds structurally,
  not just by convention.

---

## 8. Documentation (part of the deliverable, not a follow-up)

- `CLAUDE.md` only (auto-syncs to `AGENTS.md` via the existing hook) — new store/tables,
  the sizing-isolation invariant + its AST guard, all six settings.
- `docs/architecture/data-layer.md` — `broker_order_fills`/`broker_instrument_symbols`
  schema, persist-fills-not-trades rationale, the conftest fixture.
- `docs/architecture/webapp-and-gui.md` — the new endpoint, screen, and nav doors.
- `docs/architecture/orchestration-entrypoints.md` — retention as the 4th universe
  source and its precedence vs. `SYMBOL_RATING_AUTO_DROP_ENABLED`.
- `docs/known_issues/robinhood_device_approval_login_hang_risk.md` — append the
  in-worker orders-fetch addition, its budget guard, and what remains unverified
  (whether a cold first-run ingest fits the budget on a real account).
- New `docs/known_issues/robinhood_order_history_window_and_fifo_limits.md` —
  pre-window buys produce logged-and-dropped sell excess; short sales unsupported;
  options/crypto orders are invisible (`get_all_stock_orders` is equities-only) — must
  be stated plainly in the PWA copy too, so the screen doesn't implicitly claim to be
  a complete ledger.
- PR artifact: `.claude/robinhood_closed_trade_ingest_implementation_plan.md`
  (project-scoped filename per this repo's convention).

---

## 9. Implementation order

1. `data/broker_fills_store.py` + its tests + the conftest isolation fixture **in the
   same commit** as the store.
2. Settings + `.env.example` + GUI allowlist.
3. `data/robinhood_orders.py` fetcher-branch fix + persistent resolver cache.
4. `data/robinhood_login_worker.py` + `LoginPhase` + webapp phase label — the
   highest-risk step; verify its tests in isolation before moving on.
5. Universe retention (shared helper + all three `main.py`/`portfolio_sync.py` sites,
   including the `async_sync_now` leak fix) + tests.
6. `pilots/trade_history.py` + endpoint + strategy-matrix override + backend tests.
7. Webapp, in the skill's fixed order, then `typecheck`/`test`/`build`.
8. `evaluation_engine` opt-in wiring + tests.
9. Docs, then `/verify`.

---

## 10. End-to-end verification (on the operator's real machine)

```bash
# Baseline: trades table is 0 and stays 0 throughout.
sqlite3 ~/.stockpy_local/quant_platform.db "select count(*) from trades;"   # 0

/verify   # full offline gate FIRST — proves the conftest fixture protects the real DB
sqlite3 ~/.stockpy_local/quant_platform.db \
  "select name from sqlite_master where name like 'broker_%';"             # empty after tests

# ONE device-approval login — phone in hand, expect a SINGLE push.
python3 main.py --refresh-account
# log: "fetching_snapshot" -> "fetching_orders" -> result ok, "Fetched N filled orders."

sqlite3 ~/.stockpy_local/quant_platform.db "select count(*) from broker_order_fills;"  # > 0
sqlite3 ~/.stockpy_local/quant_platform.db "select count(*) from trades;"              # STILL 0

python3 -c "
from data.broker_fills_store import BrokerFillsStore
for t in BrokerFillsStore(readonly=True).closed_trades():
    if t.symbol in ('CMCL','ARCC','PBF','IVR'): print(t.symbol, t.exit_ts.date(), round(t.realized_pnl,2))"
# CMCL 2026-08-20 +251.97 | ARCC 2026-06-29 -24.40 | PBF 2026-06-22 +858.80 | IVR 2026-01-30 +54.97

# IDEMPOTENCY — second login, phone in hand again.
python3 main.py --refresh-account
sqlite3 ~/.stockpy_local/quant_platform.db "select count(*) from broker_order_fills;"  # IDENTICAL count

# Universe retention (IVR's 2026-01-30 exit is > 180d before 2026-08-24 — excluded; the rest included).
python3 -c "
from data.portfolio_sync import resolve_universe
u = resolve_universe('all', allow_live_broker_fetch=False)
print([s for s in ('CMCL','ARCC','PBF','IVR') if s in u])"   # CMCL, ARCC, PBF present; IVR absent

# Sizing is unchanged — the Decision A proof.
python3 -c "
from strategy_engine import StrategyEngine
print(StrategyEngine()._raw_kelly_or_vol_target_sizing(0.25))"   # still cold-start, n=0, NOT aggregate_kelly

# API + PWA
curl -s localhost:8602/portfolio/trade-history?limit=5 | jq '.total, .available'
curl -s localhost:8602/portfolio/realized | jq '.available'   # now true
# Browser: Portfolio "Realized performance" populated with a "See all →" link;
# /trade-history reachable via More → Research and via Marketplace Explore; TabGuide renders.
```

---

## 11. Known risks / open items carried into the PR description

1. `RH_LOGIN_DEADLINE_SECONDS` (180s) overrun on a cold first ingest is the one
   failure mode that can't be validated without a real account — if it happens, the
   fix is raising `RH_ORDER_INGEST_BUDGET_SECONDS`, never the login deadline itself.
2. `get_all_stock_orders` is equities-only — options/crypto activity stays invisible;
   the PWA copy and the new known-issue doc must say so plainly.
3. 17 round-trips sits below `MIN_TRADES_REQUIRED=30` even hypothetically — but
   `scale_in` (not just the Kelly point estimate) would still move from `0.00` to
   `0.57` the moment the count crosses zero, which is why Decision A is structural,
   not just "under threshold anyway."
