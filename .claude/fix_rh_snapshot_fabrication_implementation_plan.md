# Implementation plan: fix Robinhood live-snapshot $0 fabrication on a swallowed API failure

**Branch:** `fix-rh-snapshot-fabrication`
**Severity:** HIGH — confirmed CONSTRAINT #4 violation feeding sizing/risk-gate decisions.

## Problem

`data/robinhood_portfolio.py::_fetch_live_snapshot()` reads
`total_equity`/`buying_power` via `... or {}` / `... or "0"` fallbacks around
`r.load_portfolio_profile()`/`r.load_account_profile()`. `robin_stocks`'
`request_get(..., dataType='indexzero')` (which both calls use internally)
does not raise on a non-200/malformed response — it returns bare `None` —
so a swallowed auth failure/rate limit/transient error silently produces a
fabricated `$0` equity/buying-power reading with no error, no warning.
Because nothing raises, `fetch_account_snapshot()`'s existing (already
correct) three-tier DB → JSON-cache → live fallback is never triggered —
the fabricated snapshot is written to both caches and served as "fresh" for
up to 20h.

## Root-cause confirmation (done before any code change)

- Read `data/robinhood_portfolio.py` in full.
- Read the installed `robin_stocks` source (`profiles.py`, `helper.py`) to
  confirm `request_get(..., dataType='indexzero')`'s exact non-200/failure
  behavior: returns `None`, never raises.
- Confirmed a genuine success response is always a non-empty dict with the
  full `indexzero` record field set — including for a legitimately
  all-zero-valued account — making "empty dict" an unambiguous failure
  signal for these two calls specifically.
- Read `build_holdings()`'s source: it internally calls
  `get_open_stock_positions()`/`load_portfolio_profile()`/
  `load_account_profile()` itself and returns `{}` if ANY of the three is
  falsy — confirming Scenario B's mechanism (a transient hiccup inside
  `build_holdings()`'s own internal profile round-trip, distinct from this
  module's later, separate direct profile calls) is real, not contrived.

## Scope decision (the crux of the fix)

Guard ONLY `load_portfolio_profile()`/`load_account_profile()` returning
empty — NOT `build_holdings()` returning `{}`. The latter is genuinely
ambiguous with a real zero-position account (e.g. a fresh cash deposit with
nothing bought yet); guarding on it would risk false-failing every
legitimate zero-position account. This is an explicit, disclosed scope
boundary, not an oversight — documented in the known-issues writeup.

## Changes

1. `data/robinhood_portfolio.py::_fetch_live_snapshot()` — raise
   `RuntimeError` immediately after `r.load_portfolio_profile()`/
   `r.load_account_profile()` if either normalizes to an empty dict, before
   any field is read from them.
2. `data/robinhood_portfolio.py::fetch_account_snapshot()` — the live-fetch
   exception handler now checks the DB tier (not just JSON cache) on
   failure, matching the "auto-refresh disabled" branch's order a few lines
   above it (a pre-existing, related asymmetry noticed while implementing
   fix #1).
3. Update one pre-existing test
   (`TestFetchLiveSnapshotLoginDelegation::test_worker_marker_set_calls_login_with_device_approval_mode`)
   that incidentally relied on empty profile dicts being tolerated — not
   its actual point (it only checks `r.login()`'s call args) — to use
   well-formed dicts instead.
4. New regression tests: Scenario A (raises), the genuinely-empty-account
   and Scenario-B-shaped cases (deliberately do NOT raise — locking in the
   scope decision), the end-to-end fallback-engagement test via the public
   `fetch_account_snapshot()` entry point, and the DB-tier exception-fallback
   fix.
5. `docs/known_issues/robinhood_snapshot_fabricated_zero_on_swallowed_api_failure.md`
   + index entry in `docs/known_issues/README.md`.
6. CLAUDE.md bullet (auto-mirrored to AGENTS.md via `sync_agent_docs.sh`).

## Documentation-update step (CLAUDE.md requirement)

- `docs/known_issues/README.md` — new index row. Done.
- `docs/known_issues/robinhood_snapshot_fabricated_zero_on_swallowed_api_failure.md`
  — new writeup. Done.
- `CLAUDE.md` / `AGENTS.md` — new bullet under the existing "Robinhood
  auto-refresh gate" bullet. Done.
- No `docs/architecture/*.md` or `docs/signals/<name>.md` changes needed —
  this is a bugfix within an already-documented module's already-documented
  three-tier read order, not a new architectural capability.

## Verification

- `pytest tests/test_robinhood_portfolio.py` — 67/67 pass.
- Related Robinhood test files (`test_robinhood_login.py`,
  `test_brokerage_connect.py`, `test_robinhood_client.py`,
  `test_portfolio_sync.py`, `test_robinhood_login_worker*.py`,
  `test_run_once.py`) — 180/180 pass.
- `/verify` skill gate: ruff genuine-bug rules (`F821,F822,F823,E9`) —
  clean. Full offline suite (`pytest -m "not network and not slow"`) —
  12294 passed, 31 skipped, 5 pre-existing unrelated failures (missing
  `openai`/`google.genai` optional deps in this sandbox — confirmed
  pre-existing by reproducing on a clean checkout of the same commit,
  unrelated to this change's files).
