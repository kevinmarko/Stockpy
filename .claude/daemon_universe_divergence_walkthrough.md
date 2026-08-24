# Walkthrough: Fix daemon universe-divergence bug

## What changed and why

An operator asked about the Symbol Screener feeding models and symbols
"falling out of the universe." Research (see the full 4-phase scoping plan
this branch is Phase 0 of) traced the "falling out of the universe" symptom
to a real, previously-undocumented bug independent of the screener:
universe resolution had three divergent implementations, and the one the
**persistent orchestrator daemon actually runs every cycle**
(`pipeline/production_steps.py::AsyncDataFetchStep`) never read `WATCHLIST`
env var or `watchlist.txt` at all, and separately dropped `DEFAULT_TICKERS`
outright whenever scan-discovery had any candidate that cycle.

## The fix, in order

1. **`data/portfolio_sync.py`** gained two new functions,
   `load_env_watchlist()` and `compute_tracked_universe()`, placed right
   before the existing `resolve_universe()` (a related-but-distinct third
   universe variant, deliberately left untouched — its `DEFAULT_TICKERS`
   handling is an unconditional union, not fallback-only, by design).
2. **`main.py`**'s `_load_watchlist()` became a two-line wrapper around
   `load_env_watchlist(WATCHLIST_FILE)` — `WATCHLIST_FILE` stays a module
   attribute so `monkeypatch.setattr(main, "WATCHLIST_FILE", ...)` in the
   test suite keeps working. `_build_universe()` kept its own Robinhood
   `held` fetch, its own `discovery()` call, and its own Sheet2 fallback
   tier, but delegated the union/exclusion/fallback math to
   `compute_tracked_universe()`.
3. **`pipeline/production_steps.py::AsyncDataFetchStep.run()`** — the
   actual bug — got a surgical one-block replacement: instead of
   `base_symbols = discovered_symbols if discovered_symbols else
   list(settings.DEFAULT_TICKERS)`, it now calls
   `load_env_watchlist(ctx.watchlist_file)` and
   `compute_tracked_universe(watchlist=..., discovered=...,
   default_tickers=...)`. `ctx.watchlist_file` was already being set to
   `"watchlist.txt"` by both of `main_orchestrator.py`'s `RunContext(...)`
   construction sites — it was just never read until now.

## What I verified before calling this done

- Confirmed the exact root-cause claim by reading `pipeline/context.py`'s
  `RunContext` dataclass and both `main_orchestrator.py` construction sites
  directly (not just trusting a research summary) — `watchlist_file` truly
  was set but unused, and `build_universe_fn` is a dead stub lambda.
- Confirmed `main.py`'s venv-reexec guard is unconditional (not
  `if __name__`-gated), which is why this fix routes through
  `data/portfolio_sync.py` rather than importing `main` from
  `pipeline/production_steps.py`.
- Ran the full pre-existing `tests/test_run_once.py` suite (the real
  coverage for `_build_universe`/`_load_watchlist`) before AND after the
  refactor — identical pass count, confirming the `main.py` change is
  behavior-preserving.
- Wrote a new regression test that specifically reproduces the OLD bug's
  failure mode (a watchlist symbol alongside a discovered candidate — the
  old code would have silently dropped the watchlist symbol) and confirmed
  it demonstrates the fix.
- Caught and corrected my own first draft of a test
  (`test_default_tickers_survive_when_discovery_has_candidates`) that
  encoded a WRONG expectation (DEFAULT_TICKERS should be unioned in
  whenever discovery has candidates) — re-checked against `main.py`'s own
  actual, long-standing fallback-ONLY-when-the-whole-union-is-empty
  semantics and fixed the test to match reality rather than my assumption.
- Ran `ruff check` on every touched file; fixed the two genuinely-new
  issues my code introduced (an unused `pytest` import, a stale `# noqa:
  BLE001` on a narrowed `except OSError`); left pre-existing lint findings
  in files I touched but didn't otherwise change (confirmed via a targeted
  grep that the same finding classes — `UP006`, `DTZ005` — already exist in
  sibling, previously-merged files, so they're a repo-wide pre-existing
  style backlog, not something this PR introduced).
- Ran the full relevant test surface together (401 tests across
  `test_portfolio_sync.py`, every `test_production_steps_*.py`,
  `test_run_once.py`, `test_main.py`, `test_pipeline_smoke.py`,
  `test_progress_emission.py`, `test_orchestrator_daemon.py`,
  `test_main_body_engine_injection.py`, `test_advisory_pause_gate.py`,
  `test_main_orchestrator.py`, `test_quantitative_models.py`) — all pass.

## Disclosed, not hidden

- This fix incidentally brings `SYMBOL_RATING_AUTO_DROP_ENABLED` exclusion
  to the daemon path for the first time (it only ever applied to `main.py`'s
  universe before). Called out in the known-issue doc and the CLAUDE.md
  bullet, not silently shipped.
- `main_orchestrator.py`'s two `build_universe_fn=lambda *a: ...` stubs on
  `RunContext(...)` remain unused/dead — left in place rather than wired up
  or removed, to keep this diff minimal and reviewable. Noted as optional
  follow-up cleanup in the known-issue doc.
