# Known issue (2026-08-24): the persistent daemon's per-cycle universe never read `WATCHLIST`/`watchlist.txt`, and silently dropped `DEFAULT_TICKERS` whenever scan-discovery had any candidate

**Status: fixed and verified.** Branch `fix-daemon-universe-divergence`.
The two remaining differences (held symbols left out of the fallback
decision, and no closed-position retention in the daemon) were closed in
step 5.1 (2026-09); see "Resolution: one universe builder" at the end.

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

## Resolution: one universe builder (step 5.1, 2026-09)

The fix above shared the union math but left two differences between the
daemon and `main.py`:

1. **Held symbols and the fallback.** The daemon called
   `compute_tracked_universe()` without `held` and appended held symbols
   afterwards. So when the watchlist and discovery were both empty, the
   daemon pulled in all of `DEFAULT_TICKERS` even while positions were held.
   `main.py` passes `held` in, so there the fallback only fires when held,
   watchlist and discovered are all empty.
2. **Closed-position retention.** `main.py` keeps a fully-sold symbol for
   `CLOSED_POSITION_RETENTION_DAYS` after its last Robinhood SELL fill. The
   daemon had no retention, so those symbols dropped out of its universe.

Step 5.1 of `.claude/shrink_step5_retire_main_py_implementation_plan.md`
makes both orchestrators use one builder:
`pipeline/advisory_inputs.py::build_universe_detailed(snapshot, *,
watchlist_file=None)`. `main.py::_build_universe` wraps it via
`build_universe(snapshot)`. `AsyncDataFetchStep` now fetches the account
snapshot first (the same `main_orchestrator.fetch_account_snapshot()` call
as before, only moved up; no new login path) and passes it with
`ctx.watchlist_file`. The builder reads WATCHLIST/watchlist.txt, discovery,
the rating auto-drop and `DEFAULT_TICKERS` through
`compute_tracked_universe(held=...)`, then unions retention last.

What did not change:

- `RunContext.build_universe_fn` is still an unused stub in the daemon. The
  builder needs the watchlist file and returns per-source sets for the
  funnel, which that seam can't carry.
- MockDataEngine cycles (no live data configured) still use `AAPL` plus
  held.
- `ctx.symbols` is now sorted (held symbols used to be appended at the end).

`universe_funnel` keys are unchanged, plus `recently_closed_added`.
`default_tickers_is_fallback` now also counts held symbols.
`tracked_universe_before_held` now counts the universe symbols that are
neither held nor retained, since held symbols are no longer appended as a
separate stage.

**Measured on the operator's live inputs (2026-09-28, read-only).** Account
snapshot served from cache (fetched 2026-09-22 12:45 UTC, 25 positions),
real `watchlist.txt` (6 tickers; AQN is excluded by the rating auto-drop),
`WATCHLIST` in `.env` (the inline-comment artifact, rejected), 3 discovered
candidates (IBN, SKHY, T), 27 `DEFAULT_TICKERS`, retention 180 days / 25
symbols. The old daemon universe had 28 symbols. The new one has 30 and
equals `main.py`'s:

| Symbol | Change | Reason |
|---|---|---|
| CMCL | added | closed-position retention (recent SELL fill) |
| PBF | added | closed-position retention (recent SELL fill) |

No symbol was removed. The fallback rule did not change anything today,
because the watchlist and discovery are not empty.

The larger cross-section moves every symbol's 12-1m momentum percentile
rank. Recomputed from the stored bars (skip 22, lookback 252, over the
universe plus SPY as the daemon's `tech_raw` does), 27 of 28 existing
symbols moved, by at most 0.033 (KRO 1.000 to 0.967; PBF takes the top
rank). The `cross_sectional_momentum` score is `2 * (rank - 0.5)`, so its
raw score moves by at most 0.067 before weighting. Multifactor z-scores
also shift because their cross-sectional mean and standard deviation now
include two more names; that was not recomputed.

Tests: `tests/test_production_steps_universe.py` (`TestDaemonHeldInFallbackRule`,
`TestDaemonClosedPositionRetention`, and `TestDaemonMatchesMainUniverse`,
which checks the daemon and `main._build_universe()` give the same universe
for 8 input scenarios).
