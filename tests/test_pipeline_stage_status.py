"""
tests/test_pipeline_stage_status.py
=====================================
Unit tests for :class:`gui.orchestrator_runner.StageStatus` and the updated
:func:`gui.orchestrator_runner.compute_stage_status`.

Verified invariants
-------------------
*   ``StageStatus`` is a ``str`` subclass (legacy string-comparison compat).
*   All five members exist: SUCCESS, ACTIVE, ERROR, PENDING, SKIPPED.
*   String equality with lowercase literals works (backwards compat).
*   ``STAGES`` has exactly 4 elements.
*   ``compute_stage_status(None)`` → all PENDING.
*   ``compute_stage_status(finished, rc=0, snapshot_fresh)`` → all SUCCESS.
*   ``compute_stage_status(dry_run=True, orchestrator)`` → Execution SKIPPED.
*   ``compute_stage_status(finished, rc=1)`` → last-active stage is ERROR.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest import mock

import pytest


# ===========================================================================
# StageStatus enum
# ===========================================================================

def test_stage_status_is_str_subclass():
    from gui.orchestrator_runner import StageStatus
    assert issubclass(StageStatus, str)


def test_stage_status_members_exist():
    from gui.orchestrator_runner import StageStatus
    required = {"SUCCESS", "ACTIVE", "ERROR", "PENDING", "SKIPPED"}
    assert required.issubset({m.name for m in StageStatus})


def test_stage_status_string_equality_legacy():
    """StageStatus members must == their string values (backwards compat)."""
    from gui.orchestrator_runner import StageStatus
    assert StageStatus.SUCCESS == "success"
    assert StageStatus.ACTIVE == "active"
    assert StageStatus.ERROR == "error"
    assert StageStatus.PENDING == "pending"
    assert StageStatus.SKIPPED == "skipped"


def test_stage_status_in_dict_lookup():
    """StageStatus values work as dict keys interchangeably with strings."""
    from gui.orchestrator_runner import StageStatus
    d = {"success": "S", "active": "A"}
    assert d[StageStatus.SUCCESS] == "S"
    assert d[StageStatus.ACTIVE] == "A"


# ===========================================================================
# STAGES list
# ===========================================================================

def test_stages_has_four_elements():
    from gui.orchestrator_runner import STAGES
    assert len(STAGES) == 4


def test_stages_labels():
    from gui.orchestrator_runner import STAGES
    labels = [s[0] for s in STAGES]
    assert "Data Acquisition" in labels
    assert "Processing" in labels
    assert "Forecasting" in labels
    assert "Execution" in labels


# ===========================================================================
# compute_stage_status
# ===========================================================================

def _make_handle(
    *,
    is_running: bool = False,
    returncode: int | None = None,
    dry_run: bool = False,
    mode: str = "orchestrator",
    log_path: Path = Path("/tmp/__no_log__.txt"),
    started_at: float | None = None,
) -> mock.MagicMock:
    h = mock.MagicMock()
    h.is_running.return_value = is_running
    h.returncode.return_value = returncode
    h.dry_run = dry_run
    h.mode = mode
    h.log_path = log_path
    h.started_at = started_at or time.time() - 10
    return h


def test_compute_stage_status_none_handle():
    from gui.orchestrator_runner import StageStatus, compute_stage_status

    result = compute_stage_status(None)
    assert all(v == StageStatus.PENDING for v in result.values())


def test_compute_stage_status_no_log(tmp_path):
    """No log file → all PENDING."""
    from gui.orchestrator_runner import StageStatus, compute_stage_status

    handle = _make_handle(is_running=True, log_path=tmp_path / "missing.txt")
    result = compute_stage_status(handle)
    assert all(v == StageStatus.PENDING for v in result.values())


def test_compute_stage_status_finished_clean(tmp_path):
    """Finished rc=0 + fresh snapshot → all SUCCESS."""
    from gui.orchestrator_runner import StageStatus, compute_stage_status
    from settings import settings

    log = tmp_path / "run.log"
    log.write_text("async data fetch\ncompile_dashboard\nforecast\n", encoding="utf-8")
    snap = tmp_path / "state_snapshot.json"
    snap.write_text("{}", encoding="utf-8")

    started = time.time() - 5
    handle = _make_handle(is_running=False, returncode=0, log_path=log, started_at=started)

    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        result = compute_stage_status(handle)

    assert all(v == StageStatus.SUCCESS for v in result.values())


def test_compute_stage_status_dry_run_execution_skipped(tmp_path):
    """dry_run=True + orchestrator → Execution stage is SKIPPED."""
    from gui.orchestrator_runner import StageStatus, compute_stage_status
    from settings import settings

    log = tmp_path / "run.log"
    log.write_text(
        "async data fetch\ncompile_dashboard\nforecast\nbroker execution\n",
        encoding="utf-8",
    )
    started = time.time() - 5
    handle = _make_handle(
        is_running=True,
        dry_run=True,
        mode="orchestrator",
        log_path=log,
        started_at=started,
    )

    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        result = compute_stage_status(handle)

    assert result.get("Execution") == StageStatus.SKIPPED


def test_compute_stage_status_error_sets_error_status(tmp_path):
    """rc != 0 and last active stage → ERROR; prior stages → SUCCESS."""
    from gui.orchestrator_runner import StageStatus, compute_stage_status
    from settings import settings

    log = tmp_path / "run.log"
    # Only Data Acquisition + Processing markers present
    log.write_text("async data fetch\ncompile_dashboard\n", encoding="utf-8")

    started = time.time() - 5
    handle = _make_handle(
        is_running=False, returncode=1, log_path=log, started_at=started
    )

    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        result = compute_stage_status(handle)

    statuses = list(result.values())
    # Data Acquisition: SUCCESS (before last), Processing: ERROR (last reached),
    # Forecasting/Execution: PENDING
    assert statuses[0] == StageStatus.SUCCESS, f"expected SUCCESS, got {statuses[0]}"
    assert statuses[1] == StageStatus.ERROR, f"expected ERROR, got {statuses[1]}"
    assert statuses[2] == StageStatus.PENDING
