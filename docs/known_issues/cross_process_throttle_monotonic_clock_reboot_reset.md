# Cross-process FMP/EDGAR throttle: multi-day hang from a monotonic-clock reset across a reboot

**Status: Fixed.**

## Symptom

The Pilots PWA's Data & Schedule screen showed a pipeline "running" since
`2026-08-31T14:32:15Z` with zero progress for 20+ minutes, `Run now` doing
nothing (a run was already active), and `Restart daemon` disabled ("Disabled
while a pipeline run is active"). The daemon process itself
(`desktop.orchestrator_daemon`) was alive, ~0.1% CPU, with **zero open
outbound TCP connections** — not blocked on a slow network call.

## Root cause

`data/cross_process_throttle.py::wait_turn()` — the shared spacing primitive
`data/fmp_client.py` and `data/edgar_fundamentals.py` both call immediately
before issuing a request — persists the last request's `time.monotonic()`
timestamp to a small state file under
`settings.LOCAL_DATA_ROOT/rate_limits/{fmp,edgar}.state`, shared across every
process on the machine (every git worktree, `main.py`, the orchestrator
daemon, `refresh_validations.py`, backfill scripts, …). The design deliberately
holds an exclusive `fcntl.flock()` across the sleep, so every other process's
call to `wait_turn()` on the same file queues up behind whichever process
currently holds it.

The bug: `elapsed = now - last` never checked whether `last` could be *larger*
than `now`. `time.monotonic()` resets to near-zero on every reboot (it counts
time since boot, not wall-clock time) — so a value written during a **prior**
boot session always reads as "in the future" to a process running in a new
one. Confirmed directly on the affected machine:

```
$ cat ~/.stockpy_local/rate_limits/fmp.state
914497.303190          # written ~10.6 days into the PREVIOUS boot session
$ python3 -c "import time; print(time.monotonic())"
232906.394726791       # this boot session is only ~2.7 days old
```

`elapsed = 232906 - 914497 ≈ -681,591` seconds. `wait_turn()` then computed
`time.sleep(min_interval - elapsed) ≈ time.sleep(681,591.25)` — a ~7.9-day
sleep — **while holding the exclusive cross-process lock the entire time**,
freezing every process on the machine that ever calls FMP or EDGAR behind
that one lock. `edgar.state` carried an equally stale timestamp
(`914254.614080`) and would have triggered the identical failure mode the
next time anything called EDGAR.

This is the same *symptom class* CLAUDE.md already documents twice
(`watchlist_env_inline_comment_hang.md`, `data_pipeline_fred_unbounded_timeout_stall.md`
— a cycle wedged in `state: "running"` for an extended period, restart the
only recovery) but a genuinely different root cause: not an unbounded
network call, but an unbounded *sleep* triggered by an unguarded clock
comparison across a machine reboot.

## Fix

`wait_turn()` now guards `last > now` (only possible if `last` was written
during an earlier boot session) and treats it exactly like the function's
existing corrupt/unreadable-state-file case: fall back to "no prior request
known" (`last = 0.0`), i.e. do not throttle this call. See
[`data/cross_process_throttle.py`](../../data/cross_process_throttle.py)'s
`wait_turn()`.

Regression tests: `tests/test_cross_process_throttle.py::TestStaleTimestampFromPriorBootSession`
— a state file seeded with a `time.monotonic() + 900_000.0` "future" stamp
must not sleep more than ~1s, must not deadlock a second real process racing
for the same lock, and must overwrite the stale value with a fresh one.

## What this does NOT fix

- The already-poisoned state files (`fmp.state`, `edgar.state`) needed **no**
  manual cleanup — the `last > now` guard makes the next read of either file
  self-healing: the stale value is treated as "no prior request" and
  immediately overwritten with a fresh, current-session timestamp. (No
  cleanup was performed as part of this fix; the files were left in place to
  prove this.)
- The already-hung daemon process (PID `60956` at the time of this incident)
  had already read the bad timestamp and entered the multi-day sleep with the
  old code in memory; a code fix on disk does not reach a process already
  running old bytecode. It needed a restart to pick up the fix.
- This module is POSIX-only (`fcntl`); a missing-`fcntl` platform already
  degrades to "cross-process spacing disabled" (unaffected by this bug, and
  unaffected by this fix).
