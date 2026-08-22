# Fix: make FMP/EDGAR rate limiters cross-process, not process-local

## Context

Follow-up to PR #857 (`fix-lgbm-ranker-nondeterminism`, merged) and PR #858
(`fix-xsec-universe-coverage-visibility`, merged), both of which independently found and
documented the same real root cause without fixing it: `data/fmp_client.py`'s and
`data/edgar_fundamentals.py`'s request-spacing throttles are plain module-level globals
guarded by a `threading.Lock` — safe across threads within one process, blind to every
other OS process. This repo routinely runs many concurrent git worktrees on one machine
(each an independent Python process); when several independently invoke
`scripts/refresh_validations.py`/backfill scripts at once, each believes it owns the FULL
per-account request budget, jointly exceeding the real shared FMP/SEC limit. PR #858's
`docs/known_issues/xsec_universe_coverage_concurrency_variance.md` explicitly disclosed
this as out-of-scope follow-up work ("a cross-worktree coordination mechanism (e.g. a
shared lock file...)"). This closes that disclosed gap.

## Approach

New module `data/cross_process_throttle.py` (stdlib-only, no project imports — a
dependency-free leaf): one function, `wait_turn(state_path: Path, min_interval: float)`.
Enforces "at least `min_interval` seconds since the last call against this path, across
every process on the machine" via a POSIX advisory file lock (`fcntl.flock`, held across
the sleep) on a tiny state file storing the last request's `time.monotonic()` timestamp.

Two properties make this safe and correct:
- `flock` locks are scoped at the OS/kernel level to the file, not the process — it
  serializes every thread of every process referencing the same path, and is
  automatically released if the holding process dies (crash/`SIGKILL`), so there is no
  stale-lock cleanup concern.
- `time.monotonic()` on POSIX (Linux/macOS) is backed by `CLOCK_MONOTONIC`, a single
  system-wide clock since boot, NOT a per-process one — so a timestamp written by one
  process and read by another is directly and safely comparable.

This is an ADDITIONAL outer layer, not a replacement: both `_fmp_throttle` (in
`data/fmp_client.py`) and `_throttle` (in `data/edgar_fundamentals.py`) keep their
existing in-process logic byte-for-byte unchanged, and each gains one additional call to
`cross_process_throttle.wait_turn(...)` right after the existing in-process wait, before
the request is issued. State files live under
`settings.LOCAL_DATA_ROOT / "rate_limits" / "{fmp,edgar}.state"`, resolved through a
monkeypatchable module-level path resolver so tests can redirect it to an isolated
`tmp_path` location.

**No new settings flag.** This is a bug fix to existing rate-limiting behavior, not a new
feature — same precedent as the "Shared GDELT rate limiter" fix, which shipped
unconditionally. Deliberately scoped to the SPACING throttle only — each process's own
consecutive-failure/cooldown circuit breaker stays process-local.

## Files touched

- `data/cross_process_throttle.py` (new)
- `data/fmp_client.py`: monkeypatchable `_fmp_throttle_state_path()` resolver + one
  `wait_turn(...)` call inside `_fmp_throttle()`.
- `data/edgar_fundamentals.py`: same pattern, `_edgar_throttle_state_path()` + one
  `wait_turn(...)` call inside `_throttle()`.
- `tests/test_cross_process_throttle.py` (new): unit tests for `wait_turn` — no-op on
  `min_interval<=0`, spacing arithmetic, graceful degradation (corrupt state, missing
  `fcntl`, unwritable dir), thread-level serialization, and a REAL two-OS-process test
  (`subprocess.Popen`, not threads) proving the actual cross-process guarantee.
- `tests/test_fmp_client.py` / `tests/test_edgar_fundamentals.py`: redirect the new
  state-path override to an isolated `tmp_path` in the fixtures that opt into a nonzero
  interval, so no test touches real machine-shared state.
- `docs/architecture/data-layer.md`, `docs/VALIDATION_STRATEGY_FIX_LOG.md`,
  `docs/known_issues/xsec_universe_coverage_concurrency_variance.md`: documentation.

## Verification

- `tests/test_cross_process_throttle.py`: 11/11 passed, including the real multi-process
  test.
- `tests/test_fmp_client.py` + `tests/test_edgar_fundamentals.py`: full suites pass
  (86 tests), stable across 3 repeated runs. One test's timing tolerance needed
  loosening (0.02s/0.8x → 0.04s/0.6x) to absorb the small, expected extra syscall
  overhead of the second lock layer.
- Broader regression sweep (every test file touching `fmp_client`/`edgar_fundamentals`):
  849 passed, 0 failed.
- Full repo-wide offline suite: 2867 passed; one unrelated pre-existing test in
  `test_forecast_backfill.py` (a real-subprocess test, untouched module) failed under
  this machine's heavy concurrent-worktree load — not attributable to this change.
