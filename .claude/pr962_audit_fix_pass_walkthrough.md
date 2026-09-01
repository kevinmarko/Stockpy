# PR #962 audit + fix pass — walkthrough

**Branch:** `claude/pr-962-audit-improvements-bc1e71` (supersedes `audit-integration-pass` / PR #962 — this
branch contains every commit from #962, rebased current against `main` via a merge, plus the fixes below)

## What this pass did

1. Brought `audit-integration-pass` (PR #962, 9 commits stale against `main`) current via a clean,
   conflict-free merge (`c7143a66`) — the PR's changed files had zero overlap with what `main` gained in
   the meantime (`#957` SVI widget, `#959` FMP/EDGAR throttle fix).
2. Ran 6 parallel review-and-fix agents, one per PR #962 Work Package (A/B data-layer+watchlist, C
   alerting, D standalone scripts, E1 settings-census gate, E2 timeout guard, F options-desk contract
   test), each re-verifying against live code rather than the plan's (admittedly disclosed-as-stale) line
   numbers, per the plan's own "Review checklist for the follow-up pass."
3. Ran a 7th, manual integration pass: reconciled a discrepancy between WP-E2's full-suite report and this
   worktree's actual current state, regenerated `docs/settings_field_census.{json,md}` one final time,
   updated `CLAUDE.md`/`AGENTS.md` with a dated bullet, and ran the full offline test suite as the final
   gate.

## Findings and fixes, by Work Package

- **A/B (data layer + watchlist)**: no functional bugs. Cleaned up 4 dead `import os` statements left
  behind by the `os.environ` → `settings.X` swaps, and corrected one stale docstring in
  `pilots/watchlist_writer.py`. 207 tests pass.
- **C (alerting)**: **confirmed and fixed a real gap** — `gui/robinhood_execution_panel.py::ntfy_topic_configured`
  was missed by the original PR; it still read a bare `NTFY_TOPIC` and its own docstring still claimed
  that was correct (the bug's origin statement, verbatim). Fixed to read `settings.ALERT_NTFY_TOPIC`,
  bool-only return preserved (CONSTRAINT #3). Corrected three other stale docstrings referencing the same
  bug. Added a regression test proving a bare `NTFY_TOPIC` env var is now ignored. 167 tests pass.
- **D (standalone scripts)**: **found and fixed a real regression** — `settings.py`'s `QDRANT_URL`/
  `QDRANT_COLLECTION` fields were declared with empty-string defaults instead of the original
  `os.environ.get(key, default)` fallbacks (`http://localhost:6333` / `investyo_news`). An operator with
  neither set in `.env` would have silently pointed the RAG orchestrator at an empty URL/collection. Fixed.
- **E1 (settings census gate)**: **found the PR's own gate test was a non-functional stub** — a ~450-line
  allowlist literal followed by a bare `assert True`. Replaced with a real assertion
  (`TestFormDOsEnvironIsFullyAllowlisted`), live-verified to actually fail when a bypass is reintroduced
  (added, confirmed failure, reverted). Confirmed the final allowlist is exactly `{GCLOUD_BIN,
  NO_VENV_REEXEC}`.
- **E2 (timeout guard)**: **found the PR's own AST guard did not do what it claimed** — no real import-alias
  resolution, and `subprocess.call` was missing from the checked-method tuple, so its two-entry allowlist
  didn't even match real call sites. Fixed both; scanning the full repo post-fix found exactly 4 legitimate
  venv-reexec sites (not the 2 originally claimed) and zero false positives elsewhere. Ran the full offline
  suite as required by the review checklist.
- **F (options desk contract test)**: verified — not merely trusted — that the test actually catches a
  regression: a temporary field rename in both the live `api/pilots_api.py` response and in
  `webapp/src/api/types.ts` were each confirmed to fail the test loudly, then reverted. Flagged (not
  fixed, non-blocking) two minor gaps: it duplicates `TestClient`/token setup instead of importing from
  `tests/test_pilots_api.py`, and only covers the shared "blocked" response shape, not the four endpoints'
  distinct success-path schemas.

## A discrepancy worth recording

WP-E2's full-suite run reported 19 failures it attributed to `settings.WATCHLIST` (a pydantic singleton
read once at import) not reflecting `monkeypatch.setenv("WATCHLIST", ...)` in `tests/test_run_once.py`,
`tests/test_pilots_api.py::TestAgenticWatchWrite`, `tests/test_production_steps_universe.py`, and
`tests/test_progress_emission.py`. On direct re-verification in the integration pass, all of these files
already used the correct `monkeypatch.setattr(m.settings, "WATCHLIST", ...)` pattern and all 76 relevant
tests passed cleanly. This worktree evidently already carried these test fixes from earlier, uncommitted
work predating this session's start (visible in the very first `git status` of the session). Recorded here
rather than silently assumed resolved — if this discrepancy resurfaces, re-run
`tests/test_run_once.py tests/test_pilots_api.py::TestAgenticWatchWrite tests/test_production_steps_universe.py tests/test_progress_emission.py`
directly with the sandbox disabled (the numba/pandas_ta JIT-cache error under sandbox is a known
environment artifact, not a real failure — confirmed independently by two agents).

## Verification

- Per-Work-Package targeted tests: all pass (207 + 167 + rag_orchestrator/prompt_registry suites + 4 census
  + 1 timeout-guard + 1 contract test, individually confirmed above).
- `docs/settings_field_census.{json,md}` regenerated as the final step against the fully-merged tree.
- `CLAUDE.md`/`AGENTS.md` updated with a dated bullet covering the whole pass (mirrored identically per
  this repo's sync convention).
- Full offline suite (`pytest -q -p no:randomly -m "not network"`, `LOCAL_DATA_ROOT` isolated to a scratch
  dir to avoid the documented shared-rate-limit-file contention across concurrent worktrees): see the PR
  description for the final pass/fail count from this run.
- `make verify`'s `_live_run` step (a real `run_once()` against live data/broker) was deliberately **not**
  run in this pass — out of scope for an offline audit/fix pass and not safe to run unprompted.

## Scope not covered by this pass

Per the originating plan, direction 2 (a real backend/frontend response-contract check, repo-wide) remains
explicitly out of scope: `api/pilots_api.py` declares zero `response_model=` across all 165 routes, so
there is no machine-readable contract to diff against outside the one scoped options-desk test. This was a
disclosed, deliberate scope boundary in the original plan, not a gap introduced here.
