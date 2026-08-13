"""desktop/daemon_status.py
================================
Fast, unambiguous CLI answer to "is the orchestrator daemon actually alive
right now, and did my restart actually take effect."

Run via::

    python -m desktop.daemon_status              # human-readable
    python -m desktop.daemon_status --json        # machine-readable

Why this exists
----------------
``python -m desktop.orchestrator_daemon`` prints an ambiguous message on
shutdown ("Process exiting in ~0.5s. Whether it comes back up depends on the
process supervisor...") and there was previously no fast, dedicated way to
answer "is it actually back up." ``scripts/preflight_check.py`` exists but is
a much heavier, broad-scope gate (credentials, kill switch, validation
reports, ...) not meant for a quick "did my restart take" sanity check, and
its own ``heartbeat_fresh`` check is a poor liveness signal for a daemon
deployment in the first place -- ``heartbeat.txt`` is written only by
``main_orchestrator.main()``'s per-call lifecycle; the persistent daemon
calls ``main_orchestrator._main_body()`` directly and never writes it (see
``pilots/run_status.py``'s module docstring). This module is deliberately a
STATUS CHECK, not the daemon process itself -- it must be usable regardless
of whether the daemon is currently running.

This is a thin CLI wrapper around ``pilots.run_status.read_daemon_json()``,
which already does the hard part: reading ``<OUTPUT_DIR>/daemon.json``
(written by ``desktop/orchestrator_daemon.py`` at startup, and again with
``state="stopped"`` at a graceful shutdown) AND independently, externally
verifying whether the recorded pid is still alive on this host via
``os.kill(pid, 0)`` (``pid_alive``: True/False/None -- see that function's
docstring for why a SIGKILLed daemon can never correct its own file, which is
exactly why the externally-verified probe is the load-bearing signal here,
not the file's self-reported ``state`` field). This module does not
reimplement any of that logic -- it only formats it for a human or for JSON.

No daemon-side network calls are made (no HTTP to the Control API) -- this
reads a file and probes a pid directly, so it works even when the daemon's
API server is unresponsive or the daemon is fully down.
"""
from __future__ import annotations

import argparse
import json as _json
import sys
from datetime import datetime, timezone
from typing import Optional


def _format_duration(seconds: float) -> str:
    """Render a non-negative duration in seconds as e.g. ``"3h 12m"`` /
    ``"1d 2h 5m"`` / ``"45m"``. Always includes minutes; hours/days are
    included only when non-zero (or when a larger unit is already shown, so
    "1d 0h 5m" stays readable rather than "1d 5m")."""
    total = max(0, int(seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if days or hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    """Best-effort ISO-8601 parse; ``None`` (never raises) on any failure or
    missing input. Naive timestamps are assumed UTC, matching every other
    ISO timestamp writer in this codebase (state_snapshot.json, heartbeat.txt,
    daemon.json itself)."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def get_status() -> dict:
    """Build the full status dict. Never raises (CONSTRAINT #6) -- any
    failure reading/probing ``daemon.json`` is already absorbed by
    ``pilots.run_status.read_daemon_json()``, which degrades to ``None``
    rather than raising.

    Imports ``pilots.run_status`` lazily (inside the function body, matching
    this codebase's convention -- see ``data/historical_store.py``'s lazy
    ``HistoricalStore`` imports -- and ``scripts/preflight_check.py``'s own
    lazy ``GlobalKillSwitch``/``is_extended_hours`` imports) so that
    importing this module at CLI-parse time stays cheap and free of import
    cycles.
    """
    from pilots.run_status import read_daemon_json

    data = read_daemon_json()
    now = datetime.now(timezone.utc)

    if data is None:
        return {
            "found": False,
            "pid": None,
            "pid_alive": None,
            "state": None,
            "started_at": None,
            "stopped_at": None,
            "port": None,
            "pilots_api_port": None,
            "interval_seconds": None,
            "uptime_seconds": None,
            "summary": (
                "NEVER STARTED (or unreadable) -- no daemon.json found under "
                "settings.OUTPUT_DIR. Either the orchestrator daemon has never "
                "been launched on this machine, or OUTPUT_DIR is misconfigured. "
                "Start it with: python -m desktop.orchestrator_daemon --interval 300"
            ),
        }

    pid = data.get("pid")
    pid_alive = data.get("pid_alive")
    state = data.get("state")
    started_at = data.get("started_at")
    stopped_at = data.get("stopped_at")

    started_dt = _parse_iso(started_at)
    uptime_seconds = (now - started_dt).total_seconds() if started_dt is not None else None

    if pid_alive is True:
        if uptime_seconds is not None:
            summary = (
                f"ALIVE -- daemon pid {pid} has been up for "
                f"{_format_duration(uptime_seconds)} (started {started_at})."
            )
        else:
            summary = f"ALIVE -- daemon pid {pid} is running (start time unknown)."
        if state == "stopped":
            # A real, if unusual, case: daemon.json's own self-reported state
            # says "stopped" (e.g. left over from a prior process that this
            # pid happened to be recycled into) but the pid answers anyway.
            # pid_alive is the externally-verified signal -- say so plainly
            # rather than silently picking one field over the other.
            summary += (
                " NOTE: daemon.json's own 'state' field says 'stopped' -- "
                "trust pid_alive (externally verified) over that self-report."
            )
    elif pid_alive is False:
        if state == "stopped" and stopped_at:
            summary = (
                f"DOWN -- last known state was 'stopped' as of {stopped_at}, and "
                f"no process with pid {pid} exists. The daemon shut down cleanly "
                "and has not been restarted since."
            )
        else:
            summary = (
                f"DOWN -- daemon.json names pid {pid} (self-reported state="
                f"'{state}') but no process with that pid exists on this host. "
                "It most likely crashed or was SIGKILLed without performing its "
                "graceful-shutdown file write."
            )
    else:
        summary = (
            f"UNKNOWN -- could not verify whether pid {pid} (self-reported "
            f"state='{state}') is alive on this host (e.g. a malformed or "
            "unusable pid value in daemon.json)."
        )

    return {
        "found": True,
        "pid": pid,
        "pid_alive": pid_alive,
        "state": state,
        "started_at": started_at,
        "stopped_at": stopped_at,
        "port": data.get("port"),
        "pilots_api_port": data.get("pilots_api_port"),
        "interval_seconds": data.get("interval_seconds"),
        "uptime_seconds": uptime_seconds,
        "summary": summary,
    }


def _print_human(status: dict) -> None:
    print("Orchestrator Daemon Status")
    print("=" * 26)
    print(f"  daemon.json found:      {status['found']}")
    print(f"  pid:                    {status['pid']}")
    print(f"  pid_alive (verified):   {status['pid_alive']}")
    print(f"  state (self-reported):  {status['state']}")
    print(f"  started_at:             {status['started_at']}")
    print(f"  stopped_at:             {status['stopped_at']}")
    print(f"  control api port:       {status['port']}")
    print(f"  pilots api port:        {status['pilots_api_port']}")
    print(f"  interval_seconds:       {status['interval_seconds']}")
    print()
    print(status["summary"])


def _parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="desktop.daemon_status",
        description=(
            "Report whether the persistent orchestrator daemon "
            "(desktop/orchestrator_daemon.py) is actually alive right now."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit machine-readable JSON instead of a human-readable summary.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list] = None) -> int:
    """Returns 0 when the daemon is verified alive, 1 otherwise (down,
    unknown, or never started) -- so this doubles as a scriptable liveness
    gate, e.g. ``python -m desktop.daemon_status --json || alert_me``."""
    args = _parse_args(argv)
    status = get_status()
    if args.json:
        print(_json.dumps(status, indent=2))
    else:
        _print_human(status)
    return 0 if status.get("pid_alive") is True else 1


if __name__ == "__main__":
    sys.exit(main())
