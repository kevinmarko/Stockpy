# Walkthrough: fix Robinhood live-snapshot $0 fabrication on a swallowed API failure

## What was wrong

`data/robinhood_portfolio.py::_fetch_live_snapshot()` read account equity
and buying power like this:

```python
portfolio_profile: dict = r.load_portfolio_profile() or {}
account_profile: dict = r.load_account_profile() or {}

equity_str = portfolio_profile.get("equity") or portfolio_profile.get("extended_hours_equity") or "0"
buying_power_str = account_profile.get("buying_power") or account_profile.get("cash") or "0"
```

`robin_stocks`' own `request_get(..., dataType='indexzero')` — what both
`load_portfolio_profile()`/`load_account_profile()` call internally —
swallows any non-200 response, missing `"results"` key, or empty
`"results"` list and returns bare `None` instead of raising. The `or {}` /
`or "0"` chain then silently turned that swallowed failure into a
fabricated `$0` reading, and because nothing raised,
`fetch_account_snapshot()`'s existing three-tier DB → JSON-cache → live
fallback was never triggered — the fabricated snapshot was written to both
caches and served as genuinely "fresh" for up to 20 hours.

## The fix

```python
portfolio_profile: dict = r.load_portfolio_profile() or {}
account_profile: dict = r.load_account_profile() or {}

if not portfolio_profile:
    raise RuntimeError(...)
if not account_profile:
    raise RuntimeError(...)
```

A raised exception flows into the pre-existing, already-correct fallback
logic — no new fallback code needed, just making the failure observable.

**Why this is unambiguous**: confirmed against the installed `robin_stocks`
source that a genuine success response (including for a real, all-zero
account) is always a non-empty `indexzero` dict; `None`/empty is the
library's own swallowed-failure signal, nothing else.

**Why `build_holdings()` returning `{}` is deliberately NOT guarded**:
`build_holdings()` internally calls the same two profile functions itself
(plus `get_open_stock_positions()`) and returns `{}` if any of the three is
falsy — so `build_holdings()=={}` is genuinely ambiguous with a real
zero-position account (e.g. a fresh cash deposit, nothing bought yet).
Guarding on it would risk misclassifying every legitimate empty-of-positions
account as a failure. This is a disclosed scope boundary, not a residual
gap — see the known-issues doc for the full reasoning and the two
end-to-end reproduced scenarios (A: malformed profile with real holdings,
raises; B: `build_holdings()`'s own internal profile hiccup with the
module's later profile calls succeeding, does not raise).

A second, smaller, related fix: the live-fetch *exception* handler in
`fetch_account_snapshot()` previously checked only the JSON cache on
failure, never the DB tier — asymmetric with the "auto-refresh disabled"
branch a few lines above it. Now both check DB first, then JSON.

## Tests added

`tests/test_robinhood_portfolio.py`:
- `TestFetchLiveSnapshotFailsClosedOnMalformedProfile` — Scenario A raises
  (empty dict, and the real `None` robin_stocks actually returns), plus an
  end-to-end test through the public `fetch_account_snapshot(force=True)`
  proving the raise reaches the existing DB/JSON fallback.
- `TestFetchLiveSnapshotHoldingsAmbiguity` — locks in that Scenario B and a
  genuinely-empty new account both do NOT raise (the scope boundary).
- `TestLiveFetchExceptionFallbackChecksBothTiers` — DB-then-JSON fallback
  order on a live-fetch exception.

One pre-existing test
(`test_worker_marker_set_calls_login_with_device_approval_mode`) was
updated — it used empty profile dicts purely to check `r.login()`'s call
args, not to exercise the fabrication path, so it now uses well-formed
`"0.00"` dicts to keep testing what it was written to test.

## Verification

- `pytest tests/test_robinhood_portfolio.py` → 67/67 pass.
- `pytest tests/test_robinhood_login.py tests/test_brokerage_connect.py
  tests/test_robinhood_client.py tests/test_portfolio_sync.py
  tests/test_robinhood_login_worker.py
  tests/test_robinhood_login_worker_orders_ingest.py tests/test_run_once.py`
  → 180/180 pass.
- `/verify` skill: ruff `F821,F822,F823,E9` → clean. Full offline suite
  (`pytest -m "not network and not slow"`) → 12294 passed, 31 skipped, 5
  failed. The 5 failures (`test_data_api_chat.py::TestMultiProviderRouting::*`,
  `test_gemini_live_chat.py::TestLiveChatSession::*`) are pre-existing and
  unrelated — confirmed by stashing this change out and re-running just
  those 5 tests against the unmodified checkout, which fail identically
  with `ModuleNotFoundError: No module named 'openai'` /
  `ImportError: cannot import name 'genai' from 'google'` — missing
  optional deps in this sandbox, nothing to do with this change.

## An incident encountered mid-task, disclosed in full

While doing the "does this fail on main too" comparison above, I used
`git stash` / `git stash pop` twice to temporarily clear my working tree.
**This repo's git worktrees share a single `refs/stash` ref** — not
per-worktree as I assumed — and a concurrent session in a different
worktree pushed its own stash entry in between my push and my pop. My
second `git stash pop` popped THAT session's entry instead of mine,
applying five unrelated files
(`investyo_mcp_server.py`, `prompt_registry/registry.py`,
`scripts/preflight_check.py`, `tests/test_preflight.py`,
`tests/test_prompt_registry_resolution.py`) into my working tree while my
own actual changes were buried in the stack.

**Recovery, nothing lost**: my own stash commit was still recoverable as a
dangling git object via `git fsck --unreachable --no-reflog` (stash commits
survive as dangling objects after being popped/dropped, until GC) — matched
by its `WIP on fix-rh-snapshot-fabrication: <head-subject>` message and
timestamp, and confirmed byte-identical in file count/line count to what I
had written. Recovered via `git stash apply <hash>` (not `pop`, so the
dangling commit stayed as a safety net). The other session's mistakenly-
applied five files were NOT discarded — backed up to a scratchpad copy and
re-pushed onto the same shared stash stack with a clearly-labeled recovery
message (`git stash list` from any worktree on this machine will show it)
so whichever session owns them can reclaim them.

A project memory
(`git_stash_shared_across_worktrees.md` in this user's Claude memory) now
documents this so no future session in this repo repeats the mistake —
the practical rule is: never use `git stash` here for a "temporarily clear
and restore" pattern; use `git diff > patch` + `git restore` instead.

This incident did not touch, lose, or corrupt any of my own work, and (as
far as could be determined) did not lose the other session's work either —
but it's disclosed here in full rather than silently worked around, since
it briefly put another session's uncommitted work at risk on a shared
machine.
