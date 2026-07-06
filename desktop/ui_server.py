"""
desktop/ui_server.py
====================
Supervisor for the Streamlit Command Center UI (``gui/app.py``), launched as a
background subprocess so a native shell (WS4's ``app_shell.py``, built on
``pywebview``) can host it in a real window instead of a browser tab.

Why a subprocess (mirrors ``gui/orchestrator_runner.py``)
----------------------------------------------------------
Streamlit owns its own server loop and cannot be embedded in-process alongside
a native windowing library without fighting over the event loop / GIL.
Launching it as a detached subprocess — exactly the pattern already used by
``gui/orchestrator_runner.launch_orchestrator`` for ``main_orchestrator.py`` —
keeps the native shell responsive and isolates any Streamlit-side crash from
the shell process (CONSTRAINT #5/#6).

WS4 imports :func:`start_ui_server` / :func:`stop_ui_server` by these exact
frozen signatures — do not change them without coordinating across
workstreams.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from settings import settings

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Streamlit UI server launch log — analogous to gui/orchestrator_runner.py's
# RUN_LOG_PATH, but distinct so the native shell tails only its own UI-server
# stdout/stderr without mixing in orchestrator pipeline log lines.
UI_LOG_PATH: Path = settings.OUTPUT_DIR / "gui_ui.log"


def start_ui_server(port: int, *, headless: bool = True) -> subprocess.Popen:
    """Launch the Streamlit Command Center UI as a background subprocess.

    Parameters
    ----------
    port:
        TCP port for the Streamlit server to bind on ``127.0.0.1``.
    headless:
        Passed through to Streamlit's ``--server.headless`` flag. ``True``
        (the default) suppresses Streamlit's own "open browser tab" behavior
        and the email-collection prompt, since the native shell (not a
        browser) is the intended host.

    Returns
    -------
    subprocess.Popen
        Handle to the launched Streamlit process. stdout/stderr are
        redirected to :data:`UI_LOG_PATH` (truncated at launch). The child
        inherits the current environment so ``.env``-derived settings flow
        through unchanged.

    Notes
    -----
    Mirrors the launch pattern in ``gui/orchestrator_runner.py``'s
    ``launch_orchestrator``: ``cwd`` is the repo root, ``sys.executable``
    guarantees the ``.venv`` interpreter is used, and the log file is
    truncated at each launch for a clean per-run tail.
    """
    settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        "gui/app.py",
        "--server.headless",
        str(headless).lower(),
        "--server.port",
        str(port),
        "--server.address",
        "127.0.0.1",
    ]

    # Truncate the log for a clean per-run tail.
    log_file = open(UI_LOG_PATH, "w", encoding="utf-8")  # noqa: SIM115 - kept open for the child
    log_file.write(
        f"# InvestYo Streamlit UI server launch @ {time.strftime('%Y-%m-%d %H:%M:%S')} "
        f"(port={port}, headless={headless})\n"
    )
    log_file.flush()

    popen = subprocess.Popen(
        cmd,
        cwd=str(_REPO_ROOT),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=os.environ.copy(),
        bufsize=1,
        text=True,
    )
    logger.info("Launched Streamlit UI server pid=%s cmd=%s", popen.pid, " ".join(cmd))

    return popen


def stop_ui_server(popen: subprocess.Popen, *, timeout: float = 5.0) -> bool:
    """Terminate the Streamlit UI server subprocess.

    Sends SIGTERM via ``Popen.terminate()``, waits up to ``timeout`` seconds,
    then escalates to ``Popen.kill()`` if still alive. Returns ``True`` once
    the process is confirmed stopped (or if it was never running / already
    exited). Never raises — mirrors ``gui/orchestrator_runner.py``'s
    ``stop_run`` teardown pattern (CONSTRAINT #6).

    Parameters
    ----------
    popen:
        The ``Popen`` handle returned by :func:`start_ui_server`.
    timeout:
        Seconds to wait after SIGTERM before escalating to SIGKILL.

    Returns
    -------
    bool
        ``True`` if the process is confirmed stopped (or never running),
        ``False`` if it could not be confirmed.
    """
    if popen is None:
        return True
    try:
        if popen.poll() is not None:
            return True  # already exited

        popen.terminate()
        try:
            popen.wait(timeout=timeout)
        except Exception:
            popen.kill()
            try:
                popen.wait(timeout=timeout)
            except Exception:
                pass
        return popen.poll() is not None
    except Exception as exc:
        logger.warning("stop_ui_server failed for pid=%s: %s", getattr(popen, "pid", "?"), exc)
        return False
