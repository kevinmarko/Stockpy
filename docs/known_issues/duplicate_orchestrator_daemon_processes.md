# Known issue (2026-08-19, resolved): two independent `orchestrator_daemon` processes running concurrently against the same live database

**Status: root cause found and fixed (2026-08-24 follow-up below).** The
2026-08-19 incident below was resolved on the spot (stale process killed)
with the root cause only partially understood. It recurred on 2026-08-24 —
same symptom, same two processes (an always-on `com.investyo.stack` instance
and a manually-launched one) — and that second occurrence was actually
traced to a real race condition in `launch_webapp.command`, now fixed. See
"2026-08-24 follow-up" at the bottom for the mechanism and the fix.

## What was found

While reconciling the `forecast_errors` split (see
`forecast_tracker_local_data_root_split.md`), an audit of this machine's
running processes found **two separate `python -m desktop.orchestrator_daemon`
processes alive at the same time**:

- **PID 71563** — running since 2026-08-13 17:25:48 (nearly a week), launched
  with `--interval 300`, no controlling terminal (`??` in `ps`). Held no
  listening sockets on the Control/Pilots API ports, but **was** the process
  actively holding `~/.stockpy_local/quant_platform.db` open with multiple
  file descriptors and doing live outbound data-provider fetches — i.e. it
  was mid-cycle, actively writing to the shared production database.
- **PID 22740** — started 2026-08-19 ~14:59:58 from an attached terminal
  (`s001`), no `--interval` flag (so it used `.env`'s
  `ORCHESTRATOR_INTERVAL_SECONDS=1000`), correctly bound both the Control API
  (`:8601`) and Pilots API (`:8602`) ports and had a fresh `output/daemon.json`
  reflecting it as the "current" daemon.

Both processes' automatic timers were free to fire (well inside the
`ORCHESTRATOR_EXTENDED_HOURS_ONLY` weekday window) — PID 22740's first
automatic cycle was due in minutes. Had both fired, the risk was real:
overlapping SQLite writers to the same `quant_platform.db`, duplicated
`run_history`/`state_snapshot.json` writes, doubled outbound data-provider
load, and — if paper-broker auto-execute is enabled — **two independent
order-sizing/submission passes for the same signals** with slightly
different timestamps feeding `make_client_order_id`.

## Root cause: partially understood

PID 71563 turned out to be the process supervised by the machine's
`com.investyo.stack` launchd job (`~/Library/LaunchAgents/com.investyo.stack.plist`,
`RunAtLoad` + `KeepAlive=true`, running `scripts/investyo_stack_service.sh`)
— the intended, always-on backend stack for the Pilots PWA. `ps`/`lsof`
evidence and `investyo.log` both confirm this: it never held the API ports at
any point covered by the retained logs, but it started before the oldest
retained log file, so there's no direct record of *why* its own API bind
never succeeded (or was lost) at some point during the ~6 days it ran.

`investyo.log` separately shows a repeated pattern of the **same
"interval=1000s" daemon lineage** being cleanly started and stopped via
`SIGTERM` roughly half a dozen times over the two days before this was
found — a lineage distinct from the always-on launchd service (which always
launches with a hardcoded `--interval 300`, not `.env`'s configured 1000s).
PID 22740 was the latest instance of that separate, apparently
manually-managed lineage. **It is not fully established who or what was
starting/stopping that second lineage** — most likely an operator or an
earlier agent session running `python -m desktop.orchestrator_daemon`
directly from a terminal for interactive testing, distinct from (and
unaware of) the always-on launchd-managed service — but this was not
confirmed with certainty.

## What was done

The operator was asked and explicitly approved killing the process judged
to be the stale one before the two could collide:

```
kill -TERM 71563
```

launchd's `KeepAlive=true` immediately respawned the `com.investyo.stack`
job (as designed — this is not a bug, it's the intended crash-recovery
behavior for an always-on service) as a fresh process. **Separately, and not
fully explained**, PID 22740 exited around the same ~1-2 minute window —
`investyo.log` contains no shutdown-signal log line for it (only for 71563),
so it did not receive a logged graceful signal from anything this
investigation touched; the two events' close timing may be coincidental
rather than causal. The end state, verified directly, is healthy: exactly
one `orchestrator_daemon` process, cleanly holding both API ports and the
database, with `output/daemon.json` accurately reflecting it.

## What is still open

- **Why PID 22740 exited is not conclusively known.** No process was killed
  by this investigation other than 71563. If a similar "second daemon
  appears, then disappears near a restart" pattern recurs, it's worth
  checking whether `scripts/investyo_stack_service.sh` or any other tooling
  has logic that reaps a competing process on the API ports — a `grep` of
  that script at the time of this investigation found no such logic, but
  that isn't proof none exists anywhere in the chain.
- **No automated detection exists** for "more than one orchestrator_daemon
  process is running" — this was caught by manual `ps`/`lsof` inspection
  during an unrelated investigation, not by any test, alert, or
  `scripts/preflight_check.py` check. A cheap, high-value follow-up would be
  a preflight/observability check that counts `orchestrator_daemon`
  processes and warns (or fails) above one.
- If an operator is in the habit of manually running
  `python -m desktop.orchestrator_daemon` from a terminal for testing
  alongside the always-on `com.investyo.stack` launchd service, that's worth
  stopping deliberately (`launchctl unload
  ~/Library/LaunchAgents/com.investyo.stack.plist` first) rather than
  running both — this incident is exactly the failure mode that produces.

## Related

- `docs/known_issues/forecast_tracker_local_data_root_split.md` — the
  investigation this was found during.
- `scripts/investyo_stack_service.sh`, `~/Library/LaunchAgents/com.investyo.stack.plist`
  — the always-on service whose behavior (KeepAlive respawn on any exit,
  including a graceful one) explains PID 71563's replacement.
- CLAUDE.md's "Persistent orchestrator daemon" bullet and its documented
  "stale/orphaned daemon holding the port" operational note — a *different*,
  previously-documented failure mode (a new daemon's own bind failing
  because an old one holds the port) from the one found here (two daemons
  both fully alive, neither failing to start).

## 2026-08-24 follow-up: root cause found and fixed

Recurred with the identical two-process signature — one `com.investyo.stack`
launchd instance (`--interval 300`), one manually-launched instance from an
open `launch_webapp.command --live` terminal session — surfaced this time as
the Pilots PWA's Pipeline screen repeatedly showing the same "FULL" run
recorded multiple times in quick succession, and the screen appearing to
refresh continuously (both explained by two daemons independently ticking
their own timers against the same shared `pipeline_runs` DB, and the
webapp's "poll every 3s while a run is in flight" logic never settling
because `is_running` kept flipping as the two daemons alternated).

**Immediate cleanup done on the affected machine** (same remedy as the
2026-08-19 incident, this time by explicit operator decision to fully
decouple the two mechanisms rather than just kill the extra process):
`launchctl unload ~/Library/LaunchAgents/com.investyo.stack.plist`, plist
moved to `~/Library/LaunchAgents/disabled/` (not deleted — trivially
restorable), and the orphaned process killed. The operator explicitly chose
"app-controlled only" going forward on this machine (daemon runs only while
the app is open; no background collection while it's closed) over "keep
always-on, fix the race" — a real trade-off, not a bug fix, and specific to
this operator's stated preference; another operator could reasonably choose
the other side.

**Root cause, this time actually found**: `launch_webapp.command`'s
`_bring_up_control_and_pilots_api()` decided whether to start a daemon based
on a single, non-retried `_port_up 8601 || _port_up 8602` health check —
unlike every other reuse check in the same script (`_start_api`), which
retries for up to ~20s before giving up. A real daemon process can be alive
but not yet answering `/health` for several genuine reasons: engines still
warming on startup (the same "can take several seconds" window
`_start_api`'s own retry loop already accounts for), or — specific to the
`com.investyo.stack` case — mid-restart inside `launchd`'s own
`ThrottleInterval` window after a crash. Any one-shot check that lands
inside that window reads as "nothing is running" and proceeds to start a
second daemon process. Only one of the two ever wins the actual port bind;
the loser keeps running, unbound and outside this script's shutdown trap
(never added to `STARTED_PIDS`), independently ticking its own interval
timer against the same shared database — exactly the two-daemon signature
both incidents in this file describe. This mechanism was not identified in
the 2026-08-19 investigation above; "why it happened" was previously
unresolved, and this is the actual answer for at least this occurrence.

**Fix**: `launch_webapp.command` now checks for an already-running
`desktop.orchestrator_daemon` *process* (`pgrep -f`) before deciding to
start a new one — a much stronger signal than "does the health endpoint
answer right now" for the one decision that matters here. If a matching
process is already running, the script waits (same ~20s grace `_start_api`
already gives a fresh daemon) for it to become healthy instead of racing it
with a second one, and never adds its PID to `STARTED_PIDS` — leaving it
running untouched on exit, same as any daemon this script didn't start.
This does not change behavior for the common case (daemon already healthy,
or genuinely nothing running) — it only closes the specific silent-but-alive
window that produced both incidents in this file.

**What is still open, honestly**: this fix addresses the race inside
`launch_webapp.command` specifically. It does not add the "count
`orchestrator_daemon` processes and warn above one" preflight/observability
check the 2026-08-19 investigation flagged as a cheap follow-up — that
remains undone. Nor does it change anything about `desktop/daemon_runtime.py`
or `desktop/orchestrator_daemon.py` themselves; a daemon started by some
other means entirely (not via `launch_webapp.command`) is not protected by
this fix.
