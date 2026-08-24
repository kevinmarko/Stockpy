# Implementation Plan: Fix daemon universe-divergence bug

Phase 0 of a larger 4-phase body of work scoping "Symbol Screener → universe
integration" (full context: see the interactive-session plan this branch
implements — this file documents the phase actually shipped on this branch).

## Context

Universe resolution — "which symbols does this cycle actually evaluate" —
had three divergent implementations. `main.py::_build_universe()` correctly
built `held ∪ watchlist (WATCHLIST env ∪ watchlist.txt) ∪ discovered`,
falling back to `DEFAULT_TICKERS` only when that whole union was empty.
`pipeline/production_steps.py::AsyncDataFetchStep.run()` — the step the
**persistent orchestrator daemon** (`main_orchestrator.py`/
`desktop/daemon_runtime.py`, the backend the Pilots PWA actually runs)
executes every cycle — never read `WATCHLIST`/`watchlist.txt` at all, and
dropped `DEFAULT_TICKERS` outright whenever scan-discovery had any
candidate. A symbol added via `watchlist.txt`/`POST /agentic/watch`/the
Paper Broker order ticket's "+ Add to Watchlist" button therefore reliably
reached `main.py`'s universe but never the daemon's — the likely root cause
of an operator-observed "symbols falling out of the universe" symptom.

## Approach

1. Extract the shared union/rating-exclusion/fallback core into two new
   functions in `data/portfolio_sync.py`: `compute_tracked_universe(*,
   held=(), watchlist=(), discovered=(), default_tickers=(),
   apply_rating_exclusion=True)` and `load_env_watchlist(watchlist_file)`.
2. `main.py::_build_universe()`/`_load_watchlist()` become thin wrappers
   delegating to these — verified byte-identical via the full pre-existing
   `tests/test_run_once.py` suite.
3. `pipeline/production_steps.py::AsyncDataFetchStep.run()` reads
   `ctx.watchlist_file` (already set to `"watchlist.txt"` by
   `main_orchestrator.py`, simply never read before) via
   `load_env_watchlist()`, and computes `base_symbols` via
   `compute_tracked_universe()` instead of its own narrower inline union.
4. A pre-existing `RunContext.build_universe_fn`/`watchlist_file` DI seam
   was found but not used for this fix — its signature
   (`Callable[[AccountSnapshot], List[str]]`) is too narrow to carry the
   async step's `discovered`/watchlist inputs cleanly, so this routes around
   it via the new `data/portfolio_sync.py` functions instead.

## Documentation

- New `docs/known_issues/daemon_universe_watchlist_divergence.md` (full
  incident write-up) + `docs/known_issues/README.md` index entry.
- `docs/architecture/data-layer.md`'s `data/portfolio_sync.py` entry
  extended with the two new functions.
- `docs/architecture/orchestration-entrypoints.md`'s stale `_build_universe`
  description corrected (it omitted `discovered`/`DEFAULT_TICKERS`/rating
  exclusion even before this change) and cross-linked to the known-issue doc.
- `CLAUDE.md` changelog bullet (auto-mirrored to `AGENTS.md` by the
  `sync_agent_docs.sh` hook).

## Verification

- New `tests/test_production_steps_universe.py` (6 tests): watchlist.txt
  and `WATCHLIST` env symbols reach `ctx.symbols`; a watchlist symbol
  survives alongside a discovered candidate; `DEFAULT_TICKERS`
  fallback-only semantics are unchanged.
- New tests in `tests/test_portfolio_sync.py` (10 tests) for
  `compute_tracked_universe()`/`load_env_watchlist()` in isolation.
- Full pre-existing suite re-run to confirm no regression: 401 tests across
  `test_portfolio_sync.py`, `test_production_steps_*.py`, `test_run_once.py`,
  `test_main.py`, `test_pipeline_smoke.py`, `test_progress_emission.py`,
  `test_orchestrator_daemon.py`, `test_main_body_engine_injection.py`,
  `test_advisory_pause_gate.py`, `test_main_orchestrator.py`,
  `test_quantitative_models.py` — all pass.
