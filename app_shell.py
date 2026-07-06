"""
app_shell.py — InvestYo native desktop supervisor
==================================================
Ties the existing Streamlit "Command Center" GUI (``gui/app.py``) and the
always-on advisory refresh loop (``main.py --interval N``) together into ONE
native desktop window via ``pywebview`` — no browser tab, no visible terminal.

This module is WS4 of a 10-workstream effort to unify the platform into a
single always-on native desktop app.  It depends on three sibling modules
built by parallel workstreams (WS1/WS2/WS3), imported here against their
FROZEN signatures:

    from desktop.net_util import find_free_port, wait_for_http
    from desktop.ui_server import start_ui_server, stop_ui_server
    from desktop.engine_supervisor import start_engine, stop_engine

These modules may not exist yet in any given worktree/branch — this file
still must import cleanly at RUNTIME only when actually invoked; the imports
below are deferred into ``main()`` (not module top) precisely so that:
  (a) test collection / mocking via ``sys.modules`` patching works cleanly
      before ``app_shell`` is imported, and
  (b) importing ``app_shell`` itself never fails just because pywebview or
      the desktop/ package isn't installed yet in a given environment.

Supervisor sequence (main())
-----------------------------
  1. Load .env (mirrors main.py's main() / gui/app.py's module-top convention).
  2. Resolve a UI port (explicit ``ui_port`` arg, or ``find_free_port()``).
  3. Start the Streamlit UI server as a headless subprocess.
  4. Start the always-on advisory engine loop (``main.py --interval N``
     equivalent) as a supervised background process/thread.
  5. Wait (best-effort, bounded) for the UI server to answer HTTP.
  6. Open a native desktop window pointed at the local UI server; block until
     the user closes it.
  7. ALWAYS tear down both child processes in a ``finally`` block — including
     on KeyboardInterrupt or any exception raised while creating or running
     the window — so no orphaned child processes are left behind.

SIGTERM hardening
------------------
Python's default SIGTERM disposition terminates the interpreter immediately
WITHOUT running pending ``finally`` blocks, and pywebview's native event loop
can hold the GIL for long stretches without the interpreter checking for
pending signals — so an external ``kill <pid>`` (as opposed to the user
clicking the window's own close button) is NOT guaranteed to unwind back
through ``main()``'s ``finally`` block. ``main()`` therefore installs its own
``signal.signal(SIGTERM, ...)`` handler that runs the same idempotent
teardown directly and then force-exits the process, so a hard kill of the
supervisor still reaps the Streamlit UI server and the advisory engine loop
instead of orphaning them. (SIGKILL can never be caught by any process, by
design — nothing running inside the killed process can prevent that one.)
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
from typing import Optional

# ---------------------------------------------------------------------------
# .env loading convention (mirrors main.py's main() / gui/app.py's module top)
# ---------------------------------------------------------------------------
# Invoked inside main(), NOT at module top, so importing app_shell never
# pollutes os.environ as a side effect of import alone (same rationale as
# main.py's run_once()/main() split — see main.py's docstring for the
# pytest-pollution explanation this convention exists to avoid).
from dotenv import load_dotenv as _load_dotenv

logger = logging.getLogger("InvestYo.app_shell")


def main(interval_seconds: int = 300, ui_port: Optional[int] = None) -> int:
    """Run the InvestYo native desktop shell.

    Starts the Streamlit UI server and the always-on advisory engine loop as
    child processes, opens a native (browser-less) desktop window over the
    local UI server via ``pywebview``, and blocks until that window is
    closed by the user. Both child processes are ALWAYS torn down on exit,
    including on exceptions, KeyboardInterrupt, or SIGTERM — so closing the
    window (or Ctrl-C'ing the supervisor) never leaves an orphaned
    ``streamlit`` or ``main.py --interval`` process running.

    Parameters
    ----------
    interval_seconds :
        Refresh cadence (seconds) for the always-on advisory engine loop.
        Mirrors ``main.py --interval N``. Default 300s (5 minutes).
    ui_port :
        TCP port for the local Streamlit UI server. When ``None`` (default),
        a free port is resolved automatically via
        ``desktop.net_util.find_free_port()``.

    Returns
    -------
    int
        Process exit code — ``0`` on a normal window-close shutdown. A
        SIGTERM forces process exit directly (see ``_handle_sigterm`` below)
        and never returns to the caller.
    """
    _load_dotenv(override=False)

    # Deferred imports: keeps `import app_shell` side-effect-free when
    # desktop/* or pywebview aren't installed, and lets tests patch
    # sys.modules['desktop.net_util'] / etc. BEFORE these names are resolved.
    from desktop.net_util import find_free_port, wait_for_http
    from desktop.ui_server import start_ui_server, stop_ui_server
    from desktop.engine_supervisor import start_engine, stop_engine

    resolved_port = ui_port if ui_port is not None else find_free_port()
    logger.info("Starting InvestYo desktop shell — ui_port=%d interval=%ds",
                resolved_port, interval_seconds)

    ui_popen = None
    engine_handle = None
    _torn_down = False

    def _teardown() -> None:
        """Idempotent teardown of both child processes.

        Shared by the normal-return ``finally`` block below and the SIGTERM
        handler — safe to call more than once (e.g. once from the signal
        handler and once from ``finally`` unwinding afterward) since it
        no-ops after the first call.
        """
        nonlocal _torn_down
        if _torn_down:
            return
        _torn_down = True
        if engine_handle is not None:
            try:
                stop_engine(engine_handle)
                logger.info("Engine supervisor stopped.")
            except Exception as exc:  # noqa: BLE001
                logger.error("Error stopping engine supervisor: %s", exc)
        if ui_popen is not None:
            try:
                stop_ui_server(ui_popen)
                logger.info("UI server subprocess stopped.")
            except Exception as exc:  # noqa: BLE001
                logger.error("Error stopping UI server subprocess: %s", exc)

    def _handle_sigterm(signum, frame) -> None:  # noqa: ARG001 - required signal handler signature
        """Tear down child processes on an external SIGTERM, then force exit.

        Relying solely on this function's ``finally`` block to unwind is not
        safe here: pywebview's native event loop can hold the GIL for long
        stretches without the interpreter checking for pending signals, so a
        plain ``kill <pid>`` is not guaranteed to reach Python control flow.
        This handler runs the same idempotent ``_teardown()`` directly, then
        force-exits via ``os._exit`` rather than trying to unwind back
        through code that may never resume.
        """
        logger.warning(
            "Received SIGTERM (pid=%d) — tearing down child processes before exit.",
            os.getpid(),
        )
        _teardown()
        logging.shutdown()
        os._exit(0)

    previous_sigterm_handler = signal.signal(signal.SIGTERM, _handle_sigterm)

    try:
        # ── Start UI server (headless Streamlit, no browser tab) ────────────
        ui_popen = start_ui_server(resolved_port, headless=True)
        logger.info("UI server subprocess started (pid=%s).",
                    getattr(ui_popen, "pid", "?"))

        # ── Start always-on advisory engine loop ─────────────────────────────
        engine_handle = start_engine(interval_seconds)
        logger.info("Engine supervisor started.")

        # ── Wait for the UI server to come up (best-effort, bounded) ────────
        url = f"http://127.0.0.1:{resolved_port}"
        ready = wait_for_http(url, timeout=15.0)
        if not ready:
            logger.error(
                "UI server did not respond at %s within timeout; "
                "attempting to open the window anyway (best-effort).",
                url,
            )
        else:
            logger.info("UI server ready at %s.", url)

        # ── Open native desktop window (blocks until user closes it) ────────
        # Imported lazily so app_shell still imports cleanly in environments
        # without pywebview installed, and so tests can mock it easily via
        # sys.modules['webview'] = MagicMock().
        import webview

        webview.create_window("InvestYo", url, width=1440, height=900)
        webview.start()

        logger.info("Desktop window closed by user; shutting down.")
        return 0

    finally:
        # Always tear down both child processes — this branch runs even if
        # start_ui_server/start_engine/wait_for_http/webview.* raised, or on
        # KeyboardInterrupt, or on the normal window-close return path — so
        # no orphaned child process survives the supervisor exiting through
        # ordinary Python control flow. (The SIGTERM path above is handled
        # separately since it may never reach this point at all.)
        try:
            signal.signal(signal.SIGTERM, previous_sigterm_handler)
        except Exception as exc:  # noqa: BLE001 - restoring the handler must never mask the real error
            logger.error("Error restoring previous SIGTERM handler: %s", exc)
        _teardown()


def _parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    """Parse CLI args, mirroring main.py's ``--interval`` flag naming."""
    parser = argparse.ArgumentParser(
        prog="app_shell.py",
        description="InvestYo native desktop supervisor (UI server + advisory engine loop).",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=300,
        dest="interval_seconds",
        help="Advisory engine refresh cadence in seconds (default: 300).",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    try:
        from alerting import setup_logging as _setup_logging
        _setup_logging()
    except Exception:  # pragma: no cover - logging setup must never block startup
        logging.basicConfig(level=logging.INFO)

    _args = _parse_args()
    raise SystemExit(main(interval_seconds=_args.interval_seconds))
