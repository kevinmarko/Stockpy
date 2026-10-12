"""tests/test_forecasting_f4_archive_import_smoke.py

Forecasting rebuild F4 part 1 (BERT-LLA, attention diagnostic, ASVI, and
sector config backtest archive) closer: the 8 modules listed in
``_ARCHIVED_DOTTED_MODULES`` below were ``git mv``'d to ``legacy/`` in this
PR, together with their dedicated test files. This test proves the
promise the whole archive rests on -- that no production entry point still
needs any of them -- by blocking every one of their OLD dotted import paths
(``sys.modules[name] = None``, the standard "make this import raise
ImportError" idiom) and then actually importing each of the modules listed
in ``_ENTRY_POINTS`` in a fresh subprocess.

Blocking via ``sys.modules`` rather than relying solely on the physical
``git mv`` matters for two reasons: (1) it is explicit about intent -- a
reader doesn't have to trust that nothing at the old path silently shadows
this test's assumption; (2) it defends against a FUTURE regression where
someone recreates a stub file at one of these old paths (e.g. a careless
merge conflict resolution) that would otherwise silently reintroduce a
working import and mask the fact that production code still depends on it.

Run in a subprocess (not in-process ``sys.modules`` mutation) so a poisoned
entry doesn't leak into the rest of this test session, and so a real,
uncaught ImportError inside one of the entry points surfaces as a clean
non-zero exit code / captured traceback rather than corrupting collection
for every other test file.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_ARCHIVED_DOTTED_MODULES = [
    "forecasting.bert_lla",
    "ml.asvi_feature_engineering",
    "pilots.lstm_diagnostic",
    "scripts.backtest_sector_configs",
    "validation.forecast_accuracy_metrics",
    "validation.sector_config_io",
    "validation.sector_forecast_backtest",
    "validation.sector_forecast_types",
]

_ENTRY_POINTS = [
    "main_orchestrator",
    "desktop.orchestrator_daemon",
    "desktop.daemon_runtime",
    "api.control_api",
    "api.pilots_api",
    "api.data_api",
    "api.metrics_api",
    "investyo_mcp_server",
    "pipeline.production_steps",
    "forecasting_engine",
    "forecasting.forecast_tracker",
    "cnn_lstm_worker",
]


def test_archive_module_count_matches_expected():
    """Regression guard for the list above itself: if a future PR moves a
    9th module into (or one of these 8 back out of) legacy/, this test
    should fail loudly rather than silently under-covering the guard below."""
    assert len(_ARCHIVED_DOTTED_MODULES) == 8
    assert len(set(_ARCHIVED_DOTTED_MODULES)) == 8, "duplicate entry in _ARCHIVED_DOTTED_MODULES"


def test_entry_points_import_cleanly_with_archived_modules_blocked():
    """Blocks every archived module's old dotted path, then imports each
    entry point in one fresh subprocess. A failure prints exactly which
    entry point failed and the real traceback (surfaced via the subprocess's
    captured stderr), not just a bare non-zero exit code."""
    script = textwrap.dedent(
        f"""
        import sys

        for _name in {_ARCHIVED_DOTTED_MODULES!r}:
            sys.modules[_name] = None

        failures = []
        for _entry in {_ENTRY_POINTS!r}:
            try:
                __import__(_entry)
            except Exception as exc:  # noqa: BLE001 -- report every failure, not just the first
                import traceback
                failures.append(_entry + ": " + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))

        if failures:
            print("IMPORT_SMOKE_FAILURES:", file=sys.stderr)
            for f in failures:
                print(f, file=sys.stderr)
            sys.exit(1)
        print("IMPORT_SMOKE_OK")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
        env={**os.environ, "NO_VENV_REEXEC": "1"},
    )
    assert result.returncode == 0, (
        f"one or more entry points failed to import with the archived forecasting "
        f"modules blocked:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert "IMPORT_SMOKE_OK" in result.stdout


def test_no_archived_module_is_importable_from_its_old_path():
    """Belt-and-suspenders companion to the blocked-import smoke test above:
    proves the physical git mv actually happened for every one of the 8
    modules -- each genuinely isn't importable from its pre-archive dotted
    path any more (as opposed to merely being blocked by this test file's
    own sys.modules trick). One subprocess for all 8, not 8 subprocesses,
    to keep this test's wall-clock cost down."""
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
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "NO_VENV_REEXEC": "1"},
    )
    assert result.returncode == 0, (
        f"one or more archived modules are still importable from their pre-archive "
        f"path (expected all 8 to have moved to legacy/):\n{result.stderr}"
    )
    assert "ARCHIVE_MOVE_CONFIRMED" in result.stdout
