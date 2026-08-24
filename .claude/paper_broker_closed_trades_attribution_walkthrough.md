# Paper Broker: Closed Trades + Strategy Attribution UI — Walkthrough

PR: https://github.com/kevinmarko/Stockpy/pull/890
Branch: `feat/paper-broker-closed-trades-attribution`
Implementation plan: `.claude/paper_broker_closed_trades_attribution_implementation_plan.md`

## What was found

PR #872's remediation pass (see `CLAUDE.md`'s dated bullet in the Project
section) added a real `paper_closed_trades` table and already stamps
`strategy_id`/`pilot_id`/`experiment_arm` onto `PaperPosition`/`PaperOrder`
rows — but nothing outside the backend could read any of it: no API
endpoint for `paper_closed_trades`, and no webapp UI for either the
closed-trades history or the attribution fields (even though Orders'
backend response already included `strategy_id`).

## What changed

**Backend**
- `data/paper_account_store.py::get_full_closed_trades(symbol=None,
  limit=100)` — new read method, mirrors `get_full_orders`'s cold-start
  guard, `session_scope` query, and most-recent-first/`limit` shape.
  `realized_pnl_pct` is passed through raw (CONSTRAINT #4 — never coerced
  from `None` to `0.0`).
- `pilots/paper_broker.py::get_closed_trades()` — passthrough wrapper.
- `pilots/paper_broker.py::get_positions()` — **bug fix**: was silently
  dropping `strategy_id`/`pilot_id`/`experiment_arm` from its output dict
  despite `PositionSnapshot` (its own input) already carrying them.
- `api/pilots_api.py` — new `GET /pilots/paper-broker/closed-trades`,
  fail-open read tier (`Depends(require_read_token)` only), same as
  `/positions`/`/orders`.

**Webapp**
- `types.ts` — new `PaperBrokerClosedTrade` interface; `PaperBrokerPosition`
  and `PaperBrokerOrder` extended with the three attribution fields.
- `client.ts` — `getPaperBrokerClosedTrades(limit, symbol?)`.
- `mock.ts` — new `paperClosedTrades` array + `pushMockClosedTrade()`
  helper, wired into the 3 existing SELL/flatten-to-zero mock branches so a
  fully-closed mock position produces a synthetic closed-trade record
  (side = the position's own opening side, matching the real backend's
  `get_full_closed_trades` contract). `getPaperBrokerClosedTrades` mock
  method + `resetPaperBroker` clearing it.
- `PaperBroker.tsx` — `closedTrades` hook; Strategy column on Positions
  (colSpan 10→11) and Orders (colSpan 7→8) tables, rendering `strategy_id`
  raw (`"untagged"`/`"Manual Trade"` are intentional buckets per
  `docs/known_issues/paper_trade_strategy_id_vocabulary.md`, not
  placeholder text — only a genuine `null` renders `"—"`); new "Closed
  Trades" table section after Orders.

**Tests**
- `tests/test_paper_account_store.py`: 4 new tests (attribution/PnL
  correctness, untagged default + degenerate-pct None, limit/ordering,
  readonly cold-start).
- `tests/test_pilots_paper_broker.py`: `test_get_positions` updated to
  assert the fixed fields (regression test for the bug); new
  `test_get_closed_trades`; new `TestGetPaperBrokerClosedTradesEndpoint`
  HTTP-level class (200 + passthrough, 401 wrong token).
- `webapp/src/screens/PaperBroker.test.tsx`: main render test extended
  with a closed-trade fixture + attribution assertions; two new
  loading/error cases for the Closed Trades section; a `beforeEach`
  default (`getPaperBrokerClosedTrades` → `[]`) added so the ~40
  pre-existing tests that don't care about closed trades don't need
  individual edits.

**Docs**
- `docs/architecture/execution.md` — appended a sentence to the existing
  `data/paper_account_store.py` bullet documenting the new read surface.
- `docs/architecture/webapp-and-gui.md` — new bullet for
  `PaperBroker.tsx`'s Closed Trades + attribution UI (no per-screen bullet
  existed for Paper Broker before this).
- `CLAUDE.md`/`AGENTS.md` (auto-synced by the repo's own hook) — item 9
  appended to the existing PR 872 remediation numbered list.
- `docs/settings_field_census.{json,md}` — regenerated via
  `python3 scripts/measure_settings_census.py --write` (the new route
  changed the census; this is expected and documented by that script's own
  test failure message).

## Verification performed

- `python3 -m ruff check . --select=F821,F822,F823,E9` — clean.
- `python3 -m pytest -m "not network and not slow" -n auto --dist
  loadgroup` — **12140 passed, 31 skipped, 5 failed**. The 5 failures
  (`tests/test_data_api_chat.py` ×3, `tests/test_gemini_live_chat.py` ×2)
  are `ModuleNotFoundError: No module named 'openai'` — a required
  dependency per `requirements.txt` that this sandbox's ad hoc system
  Python (no `.venv` provisioned in this worktree) never had installed.
  Confirmed pre-existing and unrelated to this change (this PR touches no
  chat/LLM code); not something I could fix without provisioning a full
  `.venv` via `setup.sh`, which was out of scope for this change.
- `npm run --prefix webapp typecheck` — clean.
- `npx vitest run` (full webapp suite) — 1863 passed.
- `docs/settings_field_census.json`'s own freshness test
  (`tests/test_measure_settings_census.py`) — confirmed it failed before
  the regeneration (caused by this PR's new route) and passes after.

## Known limitation, disclosed not hidden

The mock-mode synthetic closed-trade generation is scoped to a **full**
close only (`qty` reaching exactly zero in one fill) — a partial close
(selling part of a position) does not produce a mock closed-trade row,
whereas the real backend records realized PnL for the closed portion on
every reducing fill, partial or full. This was a deliberate, narrow scope
per the approved implementation plan (mock parity is a "close enough to
demo correctly" bar, not a full behavioral port); a partial-close mock
closed-trade record is a reasonable, low-risk follow-up if it's ever
needed.
