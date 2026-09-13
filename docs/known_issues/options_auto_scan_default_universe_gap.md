# Known issue (2026-09-11): the fully-automated options auto-scan's default universe reads only the raw `WATCHLIST` env var, not the full tracked universe

**Status: disclosed, not fixed.** Found during the `decouple-explore-execute`
plan's Wave 0 dependency-check (§3 of
`.claude/decouple-explore-execute_wave0_checklist.md`), while confirming the
options auto-scan's operator-supplied symbol-list override. Fixing this is
explicitly **out of scope** for that plan — its task tracker forbids any
change to `main.py::_build_universe()` or universe-resolution logic, and this
gap lives in a sibling universe-resolution path, not that function itself.
See [`docs/architecture/execution-boundary.md`](../architecture/execution-boundary.md)
(§5) for how this fits into the broader explore/execute boundary write-up.

**Incident level**: Low/informational — a coverage gap in an opt-in automated
feature, not a boundary violation and not reachable by anything a human
merely browsed. See "Why this is not a boundary leak" below.

## Root cause

The Pilots PWA's "Automated Strategy Options Execution" feature has two
independent call paths, gated by two different settings flags, that
resolve their symbol universe completely differently:

1. **Manual, on-demand path** — `POST /pilots/paper-broker/strategy-options/execute`
   (`api/pilots_api.py:6161-6172`), gated by `settings.PAPER_BROKER_WRITES_ENABLED`.
   An operator-supplied `symbols` list in the request body is used verbatim.
   This path is documented and intentional (see
   `docs/architecture/execution-boundary.md` §4) and is not this issue.
2. **Fully-automated daemon-cycle path** — gated by
   `settings.PAPER_OPTIONS_AUTO_EXECUTE_ENABLED` (default `False`), reached
   from `execution/options_lifecycle.py::run_automated_options_lifecycle()`,
   itself called from both `main.py`'s cycle and
   `desktop/daemon_runtime.py::OrchestratorDaemon.trigger_run()`
   (`desktop/daemon_runtime.py:570-571`). **This is the path with the gap.**

Tracing path 2 end to end:

```python
# execution/options_lifecycle.py:161-165
if getattr(settings, "PAPER_OPTIONS_AUTO_EXECUTE_ENABLED", False):
    ...
    _exec_res = executor.execute_strategy_directives(macro_dto=macro_dto)
```

No `symbols`/`directives` argument is passed. Inside
`execute_strategy_directives()` (`execution/options_paper_executor.py:288-327`),
when `directives is None` it calls
`self.get_actionable_directives(macro_dto=macro_dto, vrp=vrp)` — again with
no `symbols` and no `run_result`. Inside `get_actionable_directives()`
(`execution/options_paper_executor.py:192-201`):

```python
if symbols is None:
    if run_result is not None:
        symbols = _resolve_symbols(run_result)
    else:
        symbols = []

if not symbols:
    # Fallback to watchlist or default tickers
    raw = getattr(settings, "WATCHLIST", "") or ""
    symbols = [s.strip().upper() for s in raw.split(",") if s.strip()]
```

`run_result` is never passed by any production caller (grepped every
non-test call site of `execute_strategy_directives`/`get_actionable_directives`
repo-wide — none pass it; `_resolve_symbols()` in
`execution/options_queue_builder.py:394-407` is dead code in production), so
`symbols` is always `[]` on entry to this fallback in the automated path. The
fallback then reads **only the raw `WATCHLIST` environment variable**, split
on commas, with no plausible-ticker validation applied to the result.

This is narrower than, and structurally different from,
`data.portfolio_sync.compute_tracked_universe()` — the shared function both
orchestrators' main signal-generation cycle actually uses (see
`docs/architecture/execution-boundary.md` §1). Specifically, this fallback:

- does **not** read `watchlist.txt` — unlike
  `data.portfolio_sync.load_env_watchlist()` (`data/portfolio_sync.py:618-643`),
  which reads both the `WATCHLIST` env var and a plain-text watchlist file
  and merges them;
- does **not** include held positions;
- does **not** include `settings.DEFAULT_TICKERS`;
- does **not** include discovered scan candidates
  (`output/scan_candidates.json`, see `docs/architecture/execution-boundary.md` §2);
- applies no ticker-plausibility validation (unlike
  `load_env_watchlist()`'s `_is_plausible_ticker()` guard, added after the
  incident documented in
  [`watchlist_env_inline_comment_hang.md`](watchlist_env_inline_comment_hang.md)).

## Practical effect

If an operator has `PAPER_OPTIONS_AUTO_EXECUTE_ENABLED=True` and manages
their tracked symbols primarily through `watchlist.txt`, held Robinhood
positions, `DEFAULT_TICKERS`, or the agentic-discovery skill's scan
candidates — rather than the raw `WATCHLIST` env var directly — the
fully-automated options auto-scan silently finds **zero** symbols every
cycle. No error, no warning; the step's own `try/except` around this call
(`execution/options_lifecycle.py`) logs only at `warning`/`debug` level on an
actual exception, and an empty symbol list is not an exception — it simply
runs a no-op scan.

## Why this is not a boundary leak

The `decouple-explore-execute` plan's actual concern is whether a symbol a
human only *browsed* (via the Symbol Screener or Quick Trade) can reach
Autopilot's *autonomous* execution surface. This gap does not create that
risk in either direction:

- It cannot **widen** what the automated scan reaches beyond the tracked
  universe — `WATCHLIST` is still an operator-set `.env`/environment value,
  not something a browsed-but-untracked symbol can populate.
- It can only **narrow** what the automated scan covers relative to what the
  operator otherwise intends to track, which is a coverage/completeness gap,
  not a scope-of-authority gap.

## Current status / disposition

**Disclosed, not fixed, by design.** The `decouple-explore-execute` plan's
task tracker (`.claude/decouple-explore-execute_task.md`) explicitly excludes
"any change to `main.py::_build_universe()` or universe-resolution logic"
from its scope, and this gap — while technically a different function
(`execution/options_paper_executor.py::get_actionable_directives()`, not
`main.py::_build_universe()` itself) — is squarely a universe-resolution
change of the same kind that boundary is meant to keep out of this task. A
correct fix would thread `data.portfolio_sync.compute_tracked_universe()`'s
result (or an equivalent) into this fallback instead of the raw `WATCHLIST`
split — the same shared-function pattern that already closed the analogous
daemon-vs-`main.py` divergence documented in
[`daemon_universe_watchlist_divergence.md`](daemon_universe_watchlist_divergence.md).
That fix is left to a dedicated follow-up task, not attempted here.

## What's still open

- The fix itself (threading `compute_tracked_universe()` or an equivalent
  into `get_actionable_directives()`'s no-override fallback).
- `tests/test_execution_universe_boundary.py::TestOptionsAutoScanDefaultScopeIsWatchlistOnly`
  now pins this gap's exact *current* boundaries (confirmed passing,
  13/13 in the full file) — including that a `watchlist.txt`-only symbol,
  a held-but-not-in-`WATCHLIST` symbol, and a mocked Symbol-Screener hit are
  all excluded from the automated scan today, and that an empty `WATCHLIST`
  yields zero scanned symbols rather than a broader fallback. That test
  pins today's behavior (so a future silent change is caught, not silently
  shipped) — it does not itself fix the gap. A future fix should extend or
  replace it with a before/after pair — one asserting the fallback still
  excludes `watchlist.txt`, one (post-fix) asserting it's included — mirroring
  `daemon_universe_watchlist_divergence.md`'s own before/after test pattern.
