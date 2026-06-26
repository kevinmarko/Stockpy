"""
gui/preflight_runner.py
=======================
Subprocess wrapper around ``scripts/preflight_check.py --json``.

Why a subprocess
----------------
``scripts/preflight_check.py`` reads live file state (heartbeat age,
validation report mtimes, kill-switch sentinel) and calls
``sys.exit(0|1)``.  Running it in-process would exit the whole Streamlit
worker; a subprocess isolates the exit and lets us capture machine-readable
JSON output.

Public API
----------
``PreflightCheck``  — result for a single check (frozen dataclass).
``PreflightReport`` — aggregate result + per-check list (frozen dataclass).
``run_preflight``   — execute the check subprocess and return ``PreflightReport``.

CONSTRAINT #4 — never fabricate success
----------------------------------------
A timeout, missing script, or JSON parse failure all produce a
``PreflightReport`` with ``all_passed=False``.  The UI can rely on the
invariant: ``all_passed=True`` iff *the actual checks ran and all passed*.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PREFLIGHT_SCRIPT = _REPO_ROOT / "scripts" / "preflight_check.py"

# Default subprocess timeout (seconds). Long enough for network checks.
DEFAULT_TIMEOUT: float = 60.0


@dataclass(frozen=True)
class PreflightCheck:
    """Result for a single preflight check.

    Attributes
    ----------
    name    : Canonical check identifier (e.g. ``"fred_key_configured"``).
    passed  : ``True`` when the check passed.
    reason  : Human-readable explanation (always non-empty).
    warning : ``True`` when the check is advisory (non-blocking even if not passed).
    """

    name: str
    passed: bool
    reason: str
    warning: bool = False


@dataclass(frozen=True)
class PreflightReport:
    """Aggregate result of a ``run_preflight()`` call.

    Attributes
    ----------
    all_passed : ``True`` iff *all* checks ran and every blocking check passed.
                 ``False`` on timeout, parse error, or any failing blocking check.
    checks     : Per-check details (empty list on timeout / error).
    error      : Human-readable failure reason when ``all_passed=False`` but
                 not due to a check logic failure (e.g. timeout, missing script).
    returncode : Raw exit code of the subprocess (``None`` on timeout).
    """

    all_passed: bool
    checks: List[PreflightCheck] = field(default_factory=list)
    error: Optional[str] = None
    returncode: Optional[int] = None


def _parse_checks(raw: str) -> List[PreflightCheck]:
    """Parse JSON array output of ``--json`` into :class:`PreflightCheck` list."""
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError(f"Expected JSON array, got {type(data).__name__}")
    checks: List[PreflightCheck] = []
    for item in data:
        checks.append(
            PreflightCheck(
                name=str(item.get("name", "unknown")),
                passed=bool(item.get("passed", False)),
                reason=str(item.get("reason", "")),
                warning=bool(item.get("warning", False)),
            )
        )
    return checks


def run_preflight(
    timeout: float = DEFAULT_TIMEOUT,
    skip: Optional[List[str]] = None,
) -> PreflightReport:
    """Run ``scripts/preflight_check.py --json`` and return a typed report.

    Parameters
    ----------
    timeout:
        Maximum wall-clock seconds to wait. Exceeding this returns a
        ``PreflightReport(all_passed=False, error="timeout")`` — never
        fabricates a success (CONSTRAINT #4).
    skip:
        Optional list of check names to skip (forwarded as ``--skip``).

    Returns
    -------
    PreflightReport
        ``all_passed=True`` iff the script exited 0 (all blocking checks
        passed).  ``all_passed=False`` on timeout, missing script, non-zero
        exit, or JSON parse failure.
    """
    if not _PREFLIGHT_SCRIPT.exists():
        return PreflightReport(
            all_passed=False,
            error=f"preflight script not found: {_PREFLIGHT_SCRIPT}",
        )

    cmd = [sys.executable, str(_PREFLIGHT_SCRIPT), "--json"]
    if skip:
        cmd.extend(["--skip"] + [s for s in skip])

    try:
        result = subprocess.run(
            cmd,
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.warning("preflight check timed out after %ss", timeout)
        return PreflightReport(all_passed=False, error=f"timeout after {timeout}s")
    except Exception as exc:
        logger.warning("preflight check failed to launch: %s", exc)
        return PreflightReport(all_passed=False, error=str(exc))

    stdout = (result.stdout or "").strip()
    if not stdout:
        # Script may have printed to stderr; capture as error context.
        stderr_snippet = (result.stderr or "")[:400]
        return PreflightReport(
            all_passed=False,
            returncode=result.returncode,
            error=f"empty stdout (exit {result.returncode}). stderr: {stderr_snippet}",
        )

    try:
        checks = _parse_checks(stdout)
    except Exception as exc:
        logger.warning("preflight JSON parse failed: %s", exc)
        return PreflightReport(
            all_passed=False,
            returncode=result.returncode,
            error=f"JSON parse failed: {exc}",
        )

    # all_passed mirrors the script's exit code:
    # exit 0 = all blocking checks passed, exit 1 = at least one failure.
    all_passed = result.returncode == 0
    return PreflightReport(
        all_passed=all_passed,
        checks=checks,
        returncode=result.returncode,
    )
