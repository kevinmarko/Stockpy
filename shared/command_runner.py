"""
gui/command_runner.py
=====================
Subprocess wrapper for the *fast, blocking* maintenance commands surfaced in
the Launcher tab's "Maintenance & Diagnostics" section.

Why a subprocess (and why blocking)
------------------------------------
These commands (``scripts.daily_briefing --print`` and ``database_setup.py``)
run in a fraction of a second and produce their full result before returning,
so the GUI blocks on them via ``subprocess.run`` and renders the captured
output inline — no polling, no log-tailing.  Running them in-process would risk
``sys.exit`` propagating into the Streamlit worker and would pollute the
process's module/DB state; a subprocess isolates both.

Long-running commands (``pytest``, ``make verify``, the orchestrator itself)
intentionally live in ``gui/orchestrator_runner.py`` instead, which uses a
non-blocking ``subprocess.Popen`` so the UI stays responsive while they run.

Public API
----------
``CommandResult``       — frozen dataclass holding the outcome of one command.
``run_command``         — generic blocking-subprocess core (never raises).
``run_daily_briefing``  — run ``python -m scripts.daily_briefing --print``.
``run_database_setup``  — run ``python database_setup.py``.

CONSTRAINT #4 — never fabricate success
----------------------------------------
A timeout, missing interpreter, or any other launch failure all produce a
``CommandResult`` with ``ok=False``.  The invariant the UI relies on is:
``ok=True`` iff *the command actually ran and exited 0*.  ``run_command``
never raises — it always returns a ``CommandResult``.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Default subprocess timeout (seconds). Generous for fast maintenance commands.
DEFAULT_TIMEOUT: float = 120.0


@dataclass(frozen=True)
class CommandResult:
    """Outcome of a single blocking maintenance command.

    Attributes
    ----------
    ok         : ``True`` iff the command ran and exited 0. ``False`` on any
                 non-zero exit, timeout, or launch failure (CONSTRAINT #4).
    stdout     : Captured standard output ("" when none / on error).
    stderr     : Captured standard error ("" when none / on error).
    returncode : Raw process exit code (``None`` on timeout / launch failure).
    error      : Human-readable failure reason when the command could not run
                 (timeout, missing interpreter, etc.); ``None`` otherwise.
    """

    ok: bool
    stdout: str
    stderr: str
    returncode: Optional[int]
    error: Optional[str] = None


def run_command(
    cmd: List[str],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    label: str = "",
) -> CommandResult:
    """Run ``cmd`` as a blocking subprocess and return a typed result.

    Parameters
    ----------
    cmd:
        The argv list to execute (e.g. ``[sys.executable, "database_setup.py"]``).
    timeout:
        Maximum wall-clock seconds to wait. Exceeding it returns a
        ``CommandResult(ok=False, error="timeout after ...")`` — never a
        fabricated success (CONSTRAINT #4).
    label:
        Optional short identifier used only in log messages.

    Returns
    -------
    CommandResult
        ``ok=True`` iff the process exited 0. On timeout or any other launch
        exception, ``ok=False`` with ``returncode=None`` and ``error`` set.
        This function never raises.
    """
    tag = label or (cmd[0] if cmd else "command")

    try:
        result = subprocess.run(
            cmd,
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.warning("command %s timed out after %ss", tag, timeout)
        return CommandResult(
            ok=False,
            stdout="",
            stderr="",
            returncode=None,
            error=f"timeout after {timeout}s",
        )
    except Exception as exc:
        logger.warning("command %s failed to launch: %s", tag, exc)
        return CommandResult(
            ok=False,
            stdout="",
            stderr="",
            returncode=None,
            error=str(exc),
        )

    return CommandResult(
        ok=result.returncode == 0,
        stdout=result.stdout or "",
        stderr=result.stderr or "",
        returncode=result.returncode,
        error=None,
    )


def run_daily_briefing(timeout: float = DEFAULT_TIMEOUT) -> CommandResult:
    """Run ``python -m scripts.daily_briefing --print`` and return the result."""
    return run_command(
        [sys.executable, "-m", "scripts.daily_briefing", "--print"],
        timeout=timeout,
        label="daily_briefing",
    )


def run_database_setup(timeout: float = DEFAULT_TIMEOUT) -> CommandResult:
    """Run ``python database_setup.py`` and return the result."""
    return run_command(
        [sys.executable, "database_setup.py"],
        timeout=timeout,
        label="database_setup",
    )
