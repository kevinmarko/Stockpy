"""desktop/orchestrator_daemon.py
=================================
Standalone process entrypoint for the persistent orchestrator daemon.

``desktop/daemon_runtime.py`` provides ``OrchestratorDaemon`` — a thread-safe
single-flight run-engine state machine with NO signal handling of its own
(that separation of concerns is deliberate: the run engine shouldn't know or
care whether it's hosted inside this standalone process, a future desktop
shell, or a test harness). THIS module is the process-lifecycle wrapper
around that class: `.env` loading, CLI argument parsing, a discovery file for
external tooling, and OS-signal-safe shutdown.

Run via::

    python -m desktop.orchestrator_daemon [--interval N] [--dry-run] [--strict]

This process always hosts the Control API (``api/control_api.py``) on
``settings.ORCHESTRATOR_API_PORT``. It ALSO hosts the Pilots API
(``api/pilots_api.py``) on ``settings.PILOTS_API_PORT`` when
``settings.PILOTS_API_ENABLED`` is ``True`` (default ``False`` — the Pilots
API otherwise remains an independently-launched standalone service, as
documented in ``api/pilots_api.py`` and ``CLAUDE.md``). Both services are
127.0.0.1-bound only and share this process's lifecycle (started after
``daemon.start()``, stopped during teardown) but not each other's failure
modes — a Pilots API startup failure is logged and swallowed, never aborting
the orchestrator daemon itself.

SIGTERM hardening
------------------
This reuses the EXACT pattern already proven in ``app_shell.py`` (see that
module's docstring for the full "why" — the short version: a plain
``signal.signal(SIGTERM, ...)`` handler is only invoked when the interpreter's
bytecode loop regains control, which is not guaranteed while a blocking
native call/loop owns a thread; a real ``kill -TERM`` against
``app_shell.py`` with a naive handler provably did NOT terminate the process
in this codebase — see PRs #160/#163). The fix: block SIGTERM (and SIGINT,
for interactive Ctrl-C parity) at the process level via
``signal.pthread_sigmask``, then spawn a dedicated daemon thread that calls
the genuinely-blocking ``signal.sigwait()``. The kernel wakes that thread
directly the instant a signal arrives, independent of whatever the main
thread is doing.

This entrypoint has no native window event loop pinning the main thread the
way ``app_shell.py``'s pywebview call does — after ``daemon.start()`` returns,
the main thread would otherwise just fall through and exit. The blocking
``sigwait()`` call (run on the watcher thread) still owns the actual "block
until told to stop" duty; the main thread joins that watcher thread so the
process stays alive until a signal arrives, exactly mirroring the shape of
``app_shell.py``'s teardown/idempotency guard even though the specific
blocking primitive differs.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import uvicorn

# ---------------------------------------------------------------------------
# .env loading convention (mirrors main_orchestrator.py's async main() /
# app_shell.py's main() — invoked inside run_forever(), NOT at module top, so
# importing this module never pollutes os.environ as a side effect of import
# alone (same pytest-pollution rationale documented on main.py's run_once()
# and repeated in app_shell.py's module docstring).
# ---------------------------------------------------------------------------
from dotenv import load_dotenv as _load_dotenv

logger = logging.getLogger("InvestYo.orchestrator_daemon")


def _write_daemon_file(
    daemon,
    output_dir: Path,
    *,
    port: Optional[int] = None,
    pilots_api_port: Optional[int] = None,
) -> None:
    """Write ``<output_dir>/daemon.json`` — a discovery file for external
    tooling (e.g. a future CLI/GUI probe) to find this daemon's pid and
    basic state without talking to it directly.

    ``port`` (when given) is the TCP port the Control API
    (``api/control_api.py``) is bound to, so external tooling can discover
    it from this one file alongside pid/state/interval_seconds/started_at.
    ``pilots_api_port`` (when given — only when ``settings.PILOTS_API_ENABLED``)
    is the TCP port the Pilots API (``api/pilots_api.py``) is bound to;
    ``None`` when that service isn't hosted by this daemon process (the
    default — it remains a manually-launched standalone service). Callers
    should only pass a port once its server has actually started listening
    — see ``run_forever``'s call site for the ordering rationale.

    Uses the same atomic write-then-rename idiom as
    ``execution/kill_switch.py``'s ``GlobalKillSwitch.activate()`` and
    ``main_orchestrator.py``'s ``dead_letter.json`` write: write to a
    ``.tmp`` sibling path, then ``Path.replace()`` it into place, so a
    concurrent reader never observes a half-written file.

    This is a discovery convenience only, not load-bearing correctness —
    any failure (permissions, disk full, etc.) is logged as a warning and
    swallowed; it must never abort daemon startup.
    """
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        status = daemon.status()
        payload = {
            "pid": os.getpid(),
            "state": "running" if status.get("is_running") else "started",
            "interval_seconds": status.get("interval_seconds"),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "port": port,
            "pilots_api_port": pilots_api_port,
        }
        final_path = output_dir / "daemon.json"
        tmp_path = final_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_path.replace(final_path)
        logger.info("Wrote daemon discovery file: %s", final_path)
    except Exception as exc:  # noqa: BLE001 - discovery file is best-effort only
        logger.warning("Failed to write daemon discovery file: %s", exc)


def run_forever(interval_seconds: int, *, dry_run: bool = False, strict: bool = False) -> int:
    """Construct the daemon, start it, write the discovery file, block the
    calling thread until SIGTERM/SIGINT, then shut down gracefully.

    Returns an exit code (0 on clean shutdown). In real operation this call
    does not return until the process receives SIGTERM/SIGINT — the signal
    watcher thread force-exits via ``os._exit(0)`` once teardown completes,
    exactly mirroring ``app_shell.py``'s SIGTERM path.
    """
    _load_dotenv(override=False)

    # SIGTERM/SIGINT MUST be blocked here, before ANY other thread is
    # created (daemon.start()'s optional interval-timer thread, the Control
    # API's uvicorn thread below, the sigwait watcher thread itself) --
    # signal.pthread_sigmask() sets the CALLING THREAD's mask, not a
    # process-wide one. Every new thread inherits its creator's mask at the
    # moment of creation; a thread spawned before this call would keep
    # SIGTERM UNBLOCKED for its own lifetime regardless of what the main
    # thread does afterward. Since a plain `kill -TERM <pid>` is delivered
    # by the kernel to ANY one thread that doesn't have the signal blocked,
    # such a thread would silently take the signal's default disposition
    # (process termination) instead of the sigwait watcher below -- bypassing
    # all of this module's teardown logic with zero log output, since the
    # process dies before our code ever runs. Confirmed by direct testing:
    # moving this call after starting the Control API's uvicorn thread
    # reproduced exactly that silent-kill failure mode.
    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM, signal.SIGINT})

    # Deferred import: mirrors app_shell.py's deferred `from desktop.xxx
    # import yyy` imports -- keeps `import desktop.orchestrator_daemon`
    # side-effect-free/importable even before desktop.daemon_runtime exists
    # for real, and lets tests patch this module's OrchestratorDaemon name
    # directly via unittest.mock.patch.
    from desktop.daemon_runtime import OrchestratorDaemon

    logger.info(
        "Starting InvestYo orchestrator daemon — interval=%ds dry_run=%s strict=%s",
        interval_seconds, dry_run, strict,
    )

    daemon = OrchestratorDaemon(interval_seconds=interval_seconds, dry_run=dry_run, strict=strict)
    daemon.start()

    from settings import settings

    # Deferred import (mirrors the OrchestratorDaemon import above): host the
    # Control API (api/control_api.py) inside this process so external
    # callers can query run status / trigger a cycle over HTTP without a
    # second process. `set_daemon` wires the just-started daemon instance
    # into that module's registry.
    from api.control_api import app as control_api_app, set_daemon as set_control_api_daemon

    set_control_api_daemon(daemon)

    # uvicorn.Config + uvicorn.Server (NOT uvicorn.run(), which blocks the
    # calling thread and has no clean stop hook) so the API server runs in a
    # background thread and can be told to stop gracefully during teardown
    # via `api_server.should_exit = True` (uvicorn's documented mechanism).
    # 127.0.0.1-bound only -- this is a local-machine-only service, defense
    # -in-depth alongside the auth tokens (no external network exposure).
    api_config = uvicorn.Config(
        control_api_app,
        host="127.0.0.1",
        port=settings.ORCHESTRATOR_API_PORT,
        log_level="warning",
    )
    api_server = uvicorn.Server(api_config)
    api_thread = threading.Thread(target=api_server.run, daemon=True, name="OrchestratorControlAPI")
    api_thread.start()

    # Optional second service: the Pilots API (api/pilots_api.py), hosted
    # alongside the Control API when settings.PILOTS_API_ENABLED (default
    # False -- pilots_api.py otherwise remains a manually-launched standalone
    # `uvicorn` process, exactly as before this flag existed). Deferred
    # import + conditional construction so an operator who never sets the
    # flag pays zero extra import/startup cost and the Pilots API's own
    # dependencies need not be importable in every daemon deployment.
    # `pilots_api_server`/`pilots_api_thread` stay `None` when disabled so
    # the readiness poll and `_teardown()` below skip them cleanly.
    pilots_api_server = None
    pilots_api_thread = None
    pilots_api_port: Optional[int] = None
    if settings.PILOTS_API_ENABLED:
        try:
            from api.pilots_api import app as pilots_api_app

            pilots_api_config = uvicorn.Config(
                pilots_api_app,
                host="127.0.0.1",
                port=settings.PILOTS_API_PORT,
                log_level="warning",
            )
            pilots_api_server = uvicorn.Server(pilots_api_config)
            pilots_api_thread = threading.Thread(
                target=pilots_api_server.run, daemon=True, name="PilotsAPI",
            )
            pilots_api_thread.start()
            pilots_api_port = settings.PILOTS_API_PORT
        except Exception as exc:  # noqa: BLE001 - optional service, never abort daemon startup
            logger.warning("Failed to start Pilots API (PILOTS_API_ENABLED=True): %s", exc)
            pilots_api_server = None
            pilots_api_thread = None

    # Bounded poll for the API server(s) to report ready (uvicorn.Server
    # exposes a `started` flag) before writing the discovery file -- a
    # discovery file pointing at a not-yet-bound port is worse than no file
    # at all. Bounded to avoid ever blocking daemon startup indefinitely if a
    # server fails to come up; falls through and writes the file anyway
    # after the deadline so discovery isn't silently lost on a slow-starting
    # server. One shared deadline covers both servers so enabling the
    # optional Pilots API never doubles the worst-case startup delay.
    _servers_to_await = [("Control API", api_server)]
    if pilots_api_server is not None:
        _servers_to_await.append(("Pilots API", pilots_api_server))
    _api_ready_deadline = time.monotonic() + 5.0
    while (
        any(not getattr(srv, "started", False) for _, srv in _servers_to_await)
        and time.monotonic() < _api_ready_deadline
    ):
        time.sleep(0.05)
    for _name, _srv in _servers_to_await:
        if not getattr(_srv, "started", False):
            logger.warning(
                "%s did not report 'started' within 5s; writing discovery "
                "file anyway (port may not be bound yet).", _name,
            )

    _write_daemon_file(
        daemon, settings.OUTPUT_DIR,
        port=settings.ORCHESTRATOR_API_PORT,
        pilots_api_port=pilots_api_port,
    )

    _torn_down = False

    def _teardown() -> None:
        """Idempotent teardown of the daemon, the Control API server, and
        (when enabled) the Pilots API server.

        Shared by the SIGTERM/SIGINT watcher path and the normal-return
        ``finally`` block below -- safe to call more than once (mirrors
        app_shell.py's ``_teardown`` idempotency-guard pattern) since it
        no-ops after the first call.
        """
        nonlocal _torn_down
        if _torn_down:
            return
        _torn_down = True
        try:
            api_server.should_exit = True
            api_thread.join(timeout=5.0)
        except Exception as exc:  # noqa: BLE001
            logger.error("Error shutting down orchestrator Control API: %s", exc)
        if pilots_api_server is not None:
            try:
                pilots_api_server.should_exit = True
                pilots_api_thread.join(timeout=5.0)
            except Exception as exc:  # noqa: BLE001
                logger.error("Error shutting down Pilots API: %s", exc)
        try:
            daemon.shutdown(timeout=10.0)
            logger.info("Orchestrator daemon shut down cleanly.")
        except Exception as exc:  # noqa: BLE001
            logger.error("Error shutting down orchestrator daemon: %s", exc)

    def _run_signal_watcher() -> None:
        """Block (in a dedicated thread) until SIGTERM or SIGINT is pending,
        then tear down and force-exit.

        See this module's docstring "SIGTERM hardening" section — this is
        the exact pattern proven in ``app_shell.py``'s
        ``_run_sigterm_watcher``: ``signal.sigwait()`` is a genuine blocking
        syscall the kernel wakes directly, independent of what the main
        thread is doing. The signals must be blocked via
        ``pthread_sigmask`` before this thread is spawned so it inherits
        the blocked mask -- required for ``sigwait()`` to receive them
        instead of the (would-be ineffective) default disposition.
        """
        received = signal.sigwait({signal.SIGTERM, signal.SIGINT})
        logger.warning(
            "Received signal %s (pid=%d) — tearing down orchestrator daemon before exit.",
            received, os.getpid(),
        )
        _teardown()
        logging.shutdown()
        os._exit(0)

    # The signal mask was already blocked at the top of run_forever(), before
    # daemon.start()/the Control API thread were created -- this thread,
    # created here, inherits that already-blocked mask.
    _watcher_thread = threading.Thread(target=_run_signal_watcher, daemon=True)
    _watcher_thread.start()

    try:
        # In real operation the watcher thread's sigwait() call blocks
        # forever until a signal arrives, at which point it tears down and
        # os._exit(0)s directly -- this join() never returns in practice.
        # Structuring it as a join (rather than e.g. `while True: sleep()`)
        # means there is no polling loop and no busy-wait; the main thread
        # is genuinely parked. The `finally` below is belt-and-suspenders
        # for the (unlikely in production, but reachable in tests) case
        # where the watcher thread returns/raises without calling
        # os._exit -- mirroring app_shell.py's belt-and-suspenders finally
        # block after its own blocking call.
        _watcher_thread.join()
        return 0
    finally:
        try:
            signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGTERM, signal.SIGINT})
        except Exception as exc:  # noqa: BLE001 - restoring the mask must never mask the real error
            logger.error("Error restoring SIGTERM/SIGINT signal mask: %s", exc)
        _teardown()


def _parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    """Parse CLI args.

    ``--interval`` mirrors main_orchestrator.py's/app_shell.py's ``--interval``
    flag naming (``type=int``); ``--dry-run``/``--strict`` mirror
    main_orchestrator.py's own argparse block exactly in naming/help-text
    style. ``--interval`` defaults to ``None`` here (rather than a concrete
    number) so the caller in ``__main__`` can distinguish "flag omitted" from
    "flag explicitly set to 0" and fall back to
    ``settings.ORCHESTRATOR_INTERVAL_SECONDS``.
    """
    parser = argparse.ArgumentParser(
        prog="desktop.orchestrator_daemon",
        description="InvestYo persistent orchestrator daemon (standalone process entrypoint).",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        dest="interval",
        help=(
            "Seconds between automatic orchestrator cycles. Defaults to "
            "settings.ORCHESTRATOR_INTERVAL_SECONDS when omitted."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Log intended orders but do not submit to broker.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        default=False,
        help="Treat DashboardSchema validation failures as fatal (exit 1). For CI / schema-drift gating.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    try:
        from alerting import setup_logging as _setup_logging
        _setup_logging()
    except Exception:  # pragma: no cover - logging setup must never block startup
        logging.basicConfig(level=logging.INFO)

    _args = _parse_args()
    from settings import settings

    _interval = _args.interval if _args.interval is not None else settings.ORCHESTRATOR_INTERVAL_SECONDS
    raise SystemExit(run_forever(_interval, dry_run=_args.dry_run, strict=_args.strict))
