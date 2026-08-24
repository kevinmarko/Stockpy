# Known issue (2026-08-24): the persistent daemon's per-cycle universe never read `WATCHLIST`/`watchlist.txt`, and silently dropped `DEFAULT_TICKERS` whenever scan-discovery had any candidate

**Status: fixed and verified.** Branch `fix-daemon-universe-divergence`.

## What was found

Universe resolution — "which symbols does this cycle actually evaluate" —
had three separate implementations that disagreed:

1. **`main.py::_build_universe()`** (the `main.py --interval`/`--agent`
   backend) correctly built `held ∪ watchlist (WATCHLIST env ∪
   watchlist.txt) ∪ discovered`, falling back to `settings.DEFAULT_TICKERS`
   only when that whole union was empty.
2. **`pipeline/production_steps.py::AsyncDataFetchStep.run()`** — the step
   `main_orchestrator.py` / `desktop/daemon_runtime.py`'s **persistent
   daemon** actually executes every cycle (the backend the Pilots PWA's
   backend runs, `settings.ORCHESTRATOR_DAEMON_ENABLED`) — computed:
   ```python
   base_symbols = discovered_symbols if discovered_symbols else list(settings.DEFAULT_TICKERS)
   ```
   This **never called `main._load_watchlist()` at all**, so `WATCHLIST`/
   `watchlist.txt` had zero effect on the daemon's universe, regardless of
   how a symbol got there — including via the existing "+ Add to Watchlist"
   button (`OptionsOrderTicket.tsx` → `POST /agentic/watch` →
   `pilots/watchlist_writer.py::append_symbols`), which only ever writes
   `watchlist.txt`.
3. **`data/portfolio_sync.py::resolve_universe()`** (CLI/MCP `--tickers all`)
   had its own third variant, unioning `DEFAULT_TICKERS` unconditionally
   rather than as a fallback — a deliberate, documented, and unrelated
   difference in scope, left untouched by this fix.

## Why it mattered

`pilots/watchlist_writer.py`'s own docstring already claimed a watch-add
"takes effect on the next `main.py` / `main_orchestrator.py` universe
build" — that promise was false for the `main_orchestrator.py`/daemon path.
An operator running the daemon (the normal way to run the Pilots PWA
backend) who added a symbol via watchlist.txt, `POST /agentic/watch`, or the
Quick Trade order ticket's "+ Add to Watchlist" button would see it vanish
from the tracked universe on the very next cycle — no error, no warning,
just silently absent from signals/forecasts/sizing. This is very likely the
actual root cause behind an operator-visible symptom of "stocks falling out
of the universe," independent of anything to do with the FMP Symbol
Screener that prompted this investigation.

Separately: `AsyncDataFetchStep.run()`'s `if discovered_symbols else
DEFAULT_TICKERS` line also meant a real watchlist entry could never combine
with a discovered candidate — if scan-discovery ever returned even one
candidate, a watchlist-only symbol was silently dropped that cycle too
(since watchlist was never part of the union to begin with).

## The fix

Two new shared functions added to `data/portfolio_sync.py` — already this
repo's universe-resolution module, safely importable everywhere (no
venv-reexec guard, unlike `main.py`):

- **`load_env_watchlist(watchlist_file)`** — a verbatim port of
  `main.py`'s original `_load_watchlist()` body, parameterized by path.
- **`compute_tracked_universe(*, held=(), watchlist=(), discovered=(),
  default_tickers=(), apply_rating_exclusion=True)`** — the shared union +
  `SYMBOL_RATING_AUTO_DROP_ENABLED` exclusion + `default_tickers`-only-if-empty
  fallback core of `main.py::_build_universe()`.

`main.py::_load_watchlist()`/`_build_universe()` became thin wrappers
delegating to these (verified byte-identical: the full pre-existing
`tests/test_run_once.py` suite — the real coverage for both functions —
passes unchanged against the refactor). `main.py` still owns its own
Robinhood-snapshot `held` set, its own `discovery()` call, and its own
Google-Sheet fallback tier; only the union/exclusion/fallback math moved.

`pipeline/production_steps.py::AsyncDataFetchStep.run()` now reads
`ctx.watchlist_file` (already correctly set to `"watchlist.txt"` by both of
`main_orchestrator.py`'s `RunContext(...)` construction sites — it was
simply never read before this fix) via `load_env_watchlist()`, and computes
`base_symbols` via the same `compute_tracked_universe()`:

```python
watchlist_symbols = load_env_watchlist(ctx.watchlist_file)
base_symbols = compute_tracked_universe(
    watchlist=watchlist_symbols,
    discovered=discovered_symbols,
    default_tickers=settings.DEFAULT_TICKERS,
)
```

Robinhood held-position handling (the append loop a few lines below) is
untouched — held symbols are still folded in afterward exactly as before,
so they're never at risk of being excluded by the (held-blind) call above.

### A DI seam existed for exactly this and wasn't used

`pipeline/context.py::RunContext` already carries `build_universe_fn`/
`watchlist_file` fields specifically so pipeline steps never need to import
`main` directly. `main_orchestrator.py`'s two `RunContext(...)` construction
sites pass dead stub lambdas for `build_universe_fn` (`lambda *a: tickers`/
`lambda *a: []`) and `AsyncDataFetchStep.run()` never called it — it
reimplemented its own narrower union inline instead. `build_universe_fn`'s
signature (`Callable[[AccountSnapshot], List[str]]`) is too narrow to carry
the async step's `discovered`/watchlist inputs cleanly, so this fix routes
around it via the new shared `data/portfolio_sync.py` functions rather than
force-fitting that seam — `build_universe_fn`/`watchlist_file` remain
present on `RunContext` but `build_universe_fn` is still an unused stub on
the `main_orchestrator.py` construction sites; wiring it up (or removing it)
is optional follow-up cleanup, not required for this fix.

### Disclosed side effect

This also brings `SYMBOL_RATING_AUTO_DROP_ENABLED` exclusion to the daemon
path for the first time — previously it only applied to `main.py`'s
universe. A real, if minor, behavior change for any operator who already
has that flag on and runs the daemon.

## Tests

`tests/test_production_steps_universe.py` (new) drives
`AsyncDataFetchStep.run()` directly with a hand-built `RunContext`
(`ctx.market` pre-set so it skips the `credentials.json`/`DataEngine`
branch), mirroring `tests/test_production_steps_broker_gate.py`'s pattern:

- a `watchlist.txt`-only symbol reaches `ctx.symbols` — the core regression;
- a `WATCHLIST` env-var-only symbol reaches `ctx.symbols`;
- a watchlist symbol survives alongside a discovered candidate (the
  scenario the old `if discovered_symbols else DEFAULT_TICKERS` line got
  wrong — it would have returned only the discovered symbol);
- `DEFAULT_TICKERS` is correctly excluded when discovery alone is non-empty
  (non-regression check — this fallback-only semantic was already correct
  and must stay correct);
- `DEFAULT_TICKERS` is still used as a fallback when everything else is
  empty.

`tests/test_portfolio_sync.py` gained direct unit coverage for
`compute_tracked_universe()` (union, fallback-only semantics, rating
exclusion never drops held symbols, exclusion lookup fails open, `apply_rating_exclusion=False`
skips the store entirely) and `load_env_watchlist()` (env-only, file-only,
merged/deduped, neither configured).

Full pre-existing suite re-run to confirm the refactor is behavior-preserving
for `main.py`: `tests/test_run_once.py`, `tests/test_main.py`,
`tests/test_pipeline_smoke.py`, `tests/test_progress_emission.py`,
`tests/test_production_steps_broker_gate.py`,
`tests/test_orchestrator_daemon.py`, `tests/test_main_body_engine_injection.py`
— 190 tests total, all pass unchanged.

## What's still open

- `resolve_universe()`'s own near-duplicate rating-exclusion block (its
  `DEFAULT_TICKERS` handling is unconditional-union, not fallback-only, by
  design) was deliberately left untouched — unifying it with
  `compute_tracked_universe()` would require a mode toggle for that
  semantic difference and is out of scope for this bug fix.
- `main_orchestrator.py`'s two dead `build_universe_fn=lambda *a: ...` stubs
  on `RunContext(...)` were left in place (harmless, unused) rather than
  wired up or removed, to keep this diff minimal and reviewable.
