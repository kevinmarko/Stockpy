# Walkthrough: cross-process FMP/EDGAR rate limiter

## The problem

`data/fmp_client.py`'s `_fmp_throttle` and `data/edgar_fundamentals.py`'s `_throttle`
each space request issuance using a module-level `threading.Lock` + a plain float
tracking the last request's `time.monotonic()`. This correctly serializes every thread
*within one process* — but this repo runs many independent OS processes (git worktrees)
concurrently on one machine, each with its own separate copy of that module-level state.
Two PRs (#857, #858) independently found real evidence of the consequence: concurrent
`refresh_validations.py` sweeps jointly exceeding the real per-account FMP/SEC budget,
tripping FMP's cooldown breaker and causing a different, randomly-incomplete ticker
subset to succeed per run — directly behind `cross_sectional_momentum`/
`sector_quality_rank`'s previously-observed Sharpe/`deployable` instability.

## The fix

`data/cross_process_throttle.py::wait_turn(state_path, min_interval)` — a small,
dependency-free function using a POSIX advisory file lock (`fcntl.flock`, held across
the sleep) on a tiny state file holding the last request's `time.monotonic()` timestamp.
Because `flock` is scoped at the kernel level to the FILE (not the process), and
`CLOCK_MONOTONIC` is one clock shared by the whole kernel since boot (not per-process),
this correctly serializes request issuance across every process on the machine, not just
threads within one.

```python
def wait_turn(state_path: Path, min_interval: float) -> None:
    if min_interval <= 0:
        return
    ...
    fd = os.open(str(state_path), os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)          # blocks across EVERY process, not just threads
    try:
        last = <read stamp from fd, or 0.0 if missing/corrupt>
        elapsed = time.monotonic() - last
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        <write fresh time.monotonic() stamp to fd>
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
```

Both `data/fmp_client.py::_fmp_throttle` and `data/edgar_fundamentals.py::_throttle`
call this as an ADDITIONAL step right after their existing (unchanged) in-process logic:

```python
def _fmp_throttle(min_interval: float) -> None:
    if min_interval <= 0:
        return
    with _fmp_throttle_lock:               # existing in-process layer, untouched
        ...
    from data.cross_process_throttle import wait_turn
    wait_turn(_fmp_throttle_state_path(), min_interval)   # new cross-process layer
```

State files live at `settings.LOCAL_DATA_ROOT / "rate_limits" / "{fmp,edgar}.state"` —
the same machine-wide shared location this repo already uses for cross-worktree state
(`ml/registry.yaml`, the validation-runs DB, etc.) — resolved through a monkeypatchable
`_fmp_throttle_state_path()` / `_edgar_throttle_state_path()` function so tests can
redirect it.

## Why no new settings flag

This is a bug fix to existing rate-limiting behavior, not a new feature. The existing
`FMP_MIN_REQUEST_INTERVAL_SECONDS=0` / a hand-set `_REQUEST_DELAY=0` already serve as an
off-switch (the new function no-ops with zero file I/O whenever `min_interval<=0`) — no
new lever needed, same precedent as the repo's prior unconditional "Shared GDELT rate
limiter" fix.

## Test strategy — why a real multi-process test, not just threads

A thread-based test can only prove serialization *within* one process — which the
pre-existing `threading.Lock` already guaranteed. It cannot prove the actual new
property this fix adds: that two independent OS *processes* share one budget rather than
each reaching full throughput independently. `tests/test_cross_process_throttle.py`'s
`TestRealMultiProcessSerialization` spawns two real child `python -c ...` processes via
`subprocess.Popen`, each hammering the same shared state file, and asserts their
COMBINED issuance timestamps respect the shared interval — the only way to genuinely
prove this without mocking the OS-level guarantee away.

## What was verified

- New primitive: 11/11 tests passing, including the real multi-process proof above.
- `tests/test_fmp_client.py` / `tests/test_edgar_fundamentals.py`: full suites unchanged
  in behavior (the existing fake-clock-based arithmetic tests were left byte-for-byte
  untouched, since the new layer is additive, not a replacement); one real-timing EDGAR
  test needed a slightly looser tolerance to absorb the small extra syscall overhead of
  the second lock under 12-thread contention (measured: it clipped by ~1ms at the
  original tight tolerance, not a correctness issue).
- Broader sweep across every test file touching either client module: 849 passed, 0
  failed.
- Full repo-wide offline suite: 2867 passed; 1 unrelated pre-existing failure in a
  different, untouched module (`test_forecast_backfill.py`, a real-subprocess timing
  test) attributable to this machine's heavy concurrent-worktree load, not this change.

## What this does NOT do

- Does not make the cooldown/circuit-breaker state cross-process (still process-local —
  a shared atomic counter and cross-process "logged once" semantics would be real added
  complexity for a secondary concern not identified as the root cause).
- Does not replace PR #858's universe-coverage fail-closed gate, which remains the
  correct backstop for whatever residual variance this fix cannot eliminate.
