# Known issue (2026-08-19, resolved): two independent `orchestrator_daemon` processes running concurrently against the same live database

**Status: resolved on the spot (stale process terminated, single daemon
confirmed healthy).** Found incidentally while investigating the
`forecast_tracker_local_data_root_split.md` known issue — not itself a code
bug, but a real operational hazard worth recording, since the root cause of
*why* it happened is only partially understood.

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
