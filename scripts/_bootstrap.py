"""
scripts/_bootstrap.py
======================
Shared bootstrap for every ``scripts/*.py`` entry point: (1) re-exec under
the project's ``.venv`` interpreter when not already running there, and (2)
load ``.env`` before any project import reads a setting.

Why this exists
----------------
``main.py`` / ``main_orchestrator.py`` / ``app_shell.py`` each carry their own
venv-reexec guard (``main.py``'s is the original: lines 52-64) and their own
``load_dotenv(ENV_PATH, ...)`` call — but no script under ``scripts/`` did
either, which produced two related, easily-confused failure modes for a
script launched with a bare ``python3 scripts/foo.py``:

  1. If the invoking ``python3`` is not the project's ``.venv`` interpreter
     (e.g. a Homebrew/system Python), a dependency installed only inside
     ``.venv`` (``finnhub-python``, ``pandas``, ...) raises
     ``ModuleNotFoundError`` — or, worse, degrades silently into a
     "not configured" log line that looks like a missing ``.env`` key
     instead of the real cause (wrong interpreter).
  2. Even under the right interpreter, ``.env`` is never copied into
     ``os.environ`` unless something calls ``load_dotenv()`` — so any
     downstream module that still reads ``os.environ.get(...)`` directly
     (rather than the ``settings`` singleton) sees empty strings and raises
     "required environment variable is missing" even when ``.env`` is fully
     populated.

Both were reproduced from a real operator report (2026-08): running
``python3 scripts/backfill_news_history.py`` under system Python 3.14
produced a spurious ``RH_USERNAME missing`` error (mechanism 2, now also
fixed at the read site — see ``data/robinhood_portfolio.py``'s
``_require_setting``) immediately followed by a genuine
``FINNHUB_API_KEY is not set ... (or finnhub-python is not installed)``
error (mechanism 1 — ``finnhub-python`` was only ever installed in
``.venv``).

Usage
-----
Call :func:`bootstrap` as the FIRST executable statement in a
``scripts/*.py`` entry point — before any third-party or project import
(only stdlib imports, and the existing repo-root ``sys.path`` shim, may
precede it)::

    import sys
    from pathlib import Path

    _REPO_ROOT = Path(__file__).resolve().parent.parent
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))

    from scripts._bootstrap import bootstrap
    bootstrap()

    # Everything below this line may safely import pandas, data.*, settings, etc.

This module is deliberately stdlib-only AT MODULE SCOPE (``os``, ``sys``,
``subprocess``, ``pathlib``) — it must be importable under ANY interpreter,
including a bare system Python with no project dependencies installed at
all. ``python-dotenv`` is imported lazily, inside :func:`bootstrap`'s own
body, strictly AFTER the venv-reexec guard has either re-executed the
process or confirmed the current interpreter already IS ``.venv``'s — never
before it. If it were imported at module scope, or before the reexec check,
this module would raise ``ModuleNotFoundError`` on exactly the interpreter
this guard exists to detect and correct.
"""

from __future__ import annotations

import os
import subprocess as _sp
import sys
from pathlib import Path

# Repo root = parent of scripts/ (this file's own directory).
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _venv_python_path() -> Path:
    venv_dir = _REPO_ROOT / ".venv" / "bin"
    candidate = venv_dir / "python3"
    if not candidate.exists():
        candidate = venv_dir / "python"
    return candidate


def bootstrap() -> None:
    """Re-exec under ``.venv``'s interpreter (if not already there), then
    load ``.env`` via ``python-dotenv``, anchored at the repo root.

    Mirrors ``main.py``'s venv-reexec guard exactly (same
    ``os.path.realpath`` comparison, same ``subprocess.call([...] +
    sys.argv)`` re-exec, same ``python3``-then-``python`` fallback) so a
    script behaves identically to ``python3 main.py`` regardless of which
    interpreter actually launched it.

    Degrades gracefully (CONSTRAINT #6 — never raises) rather than crashing
    a script over environment setup: a missing ``.venv`` or missing
    ``python-dotenv`` logs an actionable warning to stderr and lets the
    script continue under whatever interpreter/environment it already has —
    the same posture ``main.py``'s own guard takes (it only re-execs when a
    ``.venv`` interpreter is actually found; otherwise it silently proceeds
    under the current interpreter).
    """
    venv_python = _venv_python_path()
    if venv_python.exists():
        if os.path.realpath(sys.executable) != os.path.realpath(str(venv_python)):
            sys.exit(_sp.call([str(venv_python)] + sys.argv))
    else:
        print(
            f"WARNING: {_REPO_ROOT / '.venv'} not found — run ./setup.sh first. "
            "Continuing under the current interpreter; project-only imports may fail.",
            file=sys.stderr,
        )

    # Deferred import — see module docstring for why this MUST come after
    # the venv-reexec guard above, never before it.
    try:
        from dotenv import load_dotenv as _load_dotenv
    except ImportError:
        print(
            "WARNING: python-dotenv is not installed in this interpreter — "
            ".env will not be loaded. Run via ./setup.sh's .venv, or "
            "`pip install python-dotenv`.",
            file=sys.stderr,
        )
        return

    # override=False: an explicit shell export always wins over .env,
    # matching main.py's / main_orchestrator.py's identical convention.
    _load_dotenv(_REPO_ROOT / ".env", override=False)
