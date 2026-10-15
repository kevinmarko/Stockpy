"""tests/test_main_py_archive_import_smoke.py

Shrink step 5.5 closer: ``main.py`` and the modules only it used were ``git mv``'d
to ``legacy/``. Same proof as ``tests/test_options_archive_import_smoke.py``
(step 4b): block every archived module's OLD dotted path in ``sys.modules`` and
import each runtime entry point in a fresh subprocess, so a production import of
an archived module fails here instead of in the live daemon.

``engine.advisory_agent`` is blocked whole: its market-hours helpers, the only
part the daemon used, moved to ``shared/market_hours.py``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Literal list (not derived from legacy/) so a forgotten update fails loudly.
_ARCHIVED_DOTTED_MODULES = [
    "main",
    "pipeline.steps",
    "reporting.html_publisher",
    "engine.trade_signals",
    "engine.advisory_agent",
]

# Every runtime entry point of the one remaining runtime, plus the modules that
# used to import an archived one (market hours, snapshot helpers).
_ENTRY_POINTS = [
    "main_orchestrator",
    "desktop.orchestrator_daemon",
    "desktop.daemon_runtime",
    "api.control_api",
    "api.pilots_api",
    "api.data_api",
    "api.metrics_api",
    "investyo_mcp_server",
    "broker_live_execution_mcp",
    "pipeline.production_steps",
    "pipeline.advisory_inputs",
    "pipeline.agentic_queue",
    "shared.engine_status",
    "shared.market_hours",
    "shared.orchestrator_runner",
    "reporting.state_snapshot",
    "pilots.agentic",
    "scripts.preflight_check",
    "scripts.step5_4_verify_cycle",
]


def _run(script: str, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "NO_VENV_REEXEC": "1"},
    )


def test_archive_module_list_is_unique():
    assert len(set(_ARCHIVED_DOTTED_MODULES)) == len(_ARCHIVED_DOTTED_MODULES) == 5


def test_entry_points_import_cleanly_with_archived_modules_blocked():
    script = textwrap.dedent(
        f"""
        import sys
        import traceback

        for _name in {_ARCHIVED_DOTTED_MODULES!r}:
            sys.modules[_name] = None

        failures = []
        for _entry in {_ENTRY_POINTS!r}:
            try:
                __import__(_entry)
            except BaseException as exc:  # noqa: BLE001 -- report every failure, incl. SystemExit
                failures.append(_entry + ": " + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))

        if failures:
            print("IMPORT_SMOKE_FAILURES:", file=sys.stderr)
            for f in failures:
                print(f, file=sys.stderr)
            sys.exit(1)
        print("IMPORT_SMOKE_OK")
        """
    )
    result = _run(script, timeout=240)
    assert result.returncode == 0, (
        "one or more entry points failed to import with main.py's archived modules "
        f"blocked:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert "IMPORT_SMOKE_OK" in result.stdout


def test_no_archived_module_is_importable_from_its_old_path():
    script = textwrap.dedent(
        f"""
        import importlib
        import sys

        still_importable = []
        for _name in {_ARCHIVED_DOTTED_MODULES!r}:
            try:
                importlib.import_module(_name)
                still_importable.append(_name)
            except Exception:
                pass

        if still_importable:
            print("STILL_IMPORTABLE:", still_importable, file=sys.stderr)
            sys.exit(1)
        print("ARCHIVE_MOVE_CONFIRMED")
        """
    )
    result = _run(script, timeout=120)
    assert result.returncode == 0, f"archived modules still importable:\n{result.stderr}"
    assert "ARCHIVE_MOVE_CONFIRMED" in result.stdout


def test_archived_files_exist_in_legacy():
    for rel in (
        "legacy/main.py",
        "legacy/pipeline/steps.py",
        "legacy/reporting/html_publisher.py",
        "legacy/reporting/state_snapshot.py",
        "legacy/engine/trade_signals.py",
        "legacy/engine/advisory_agent.py",
        "legacy/scripts/com.investyo.daily-advisory.plist",
    ):
        assert (REPO_ROOT / rel).is_file(), rel


def test_snapshot_module_keeps_only_the_orchestrator_helpers():
    """``main_orchestrator.py`` is the only state-snapshot writer now; the advisory
    writer must not creep back into the active tree."""
    import reporting.state_snapshot as ss

    for name in ("_safe_float_or_none", "_rating_consecutive_cycles", "_rating_is_excluded"):
        assert hasattr(ss, name), name
    for gone in ("write_state_snapshot", "load_snapshot_diff"):
        assert not hasattr(ss, gone), gone
