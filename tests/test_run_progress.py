"""
tests/test_run_progress.py
===========================
Unit tests for :func:`gui.orchestrator_runner.compute_run_progress` and its
:class:`gui.orchestrator_runner.RunProgress` result type.

Streamlit panels can't be rendered without a live Streamlit runtime, so — like
``tests/test_pipeline_stage_status.py`` before it — these tests exercise only
the PURE resolution logic behind the Launcher tab's progress bar, mocking a
``RunHandle`` with a ``unittest.mock.MagicMock`` (mirroring
``tests/test_pipeline_stage_status.py::_make_handle``) and writing a real
``output/progress.json`` fixture under ``tmp_path`` with
``settings.OUTPUT_DIR`` patched (mirroring
``tests/test_historical_store.py``-style fixture isolation).

Verified invariants
--------------------
*   ``compute_run_progress(None)`` -> ``None`` (nothing to show).
*   Subprocess handle + FRESH ``progress.json`` -> real percent,
    ``indeterminate=False``, label carries the stage + symbol counts.
*   Subprocess handle + STALE ``progress.json`` (age > 3x the poll interval)
    while still running -> falls back to the coarse stage-count estimate
    (``indeterminate=True``), never trusting an orphaned progress file.
*   Subprocess handle + missing ``progress.json`` + running, but with a real
    log showing SOME stage markers -> coarse ``indeterminate=True`` estimate.
*   Subprocess handle + missing ``progress.json`` + running + NO log at all
    (nothing observed yet) -> ``None`` (never fabricate a hollow 0% bar).
*   Subprocess handle + missing ``progress.json`` + NOT running -> ``None``.
*   Daemon handle whose Control API run-status payload carries a ``progress``
    sub-dict -> real percent, ``indeterminate=False``, sourced from that dict
    (monkeypatches ``gui.daemon_client.get_run_status``, which
    ``orchestrator_runner._daemon_run_status`` wraps).
*   Daemon handle with no ``progress`` sub-dict falls back to the same coarse
    stage-count logic as the subprocess path.
*   Any internal failure (e.g. a corrupt/malformed progress payload) degrades
    to ``None``/coarse fallback rather than raising — CONSTRAINT #6.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# Shared handle-mock helper (mirrors tests/test_pipeline_stage_status.py)
# ---------------------------------------------------------------------------

def _make_handle(
    *,
    is_running: bool = False,
    returncode: int | None = None,
    dry_run: bool = False,
    mode: str = "orchestrator",
    backend: str = "subprocess",
    daemon_run_id: str | None = None,
    log_path: Path = Path("/tmp/__no_log__.txt"),
    started_at: float | None = None,
) -> mock.MagicMock:
    h = mock.MagicMock()
    h.is_running.return_value = is_running
    h.returncode.return_value = returncode
    h.dry_run = dry_run
    h.mode = mode
    h.backend = backend
    h.daemon_run_id = daemon_run_id
    h.log_path = log_path
    h.started_at = started_at if started_at is not None else time.time() - 10
    return h


def _write_progress_json(
    output_dir: Path,
    *,
    state: str = "running",
    stage: str = "forecasting",
    stage_index: int = 2,
    stage_total: int = 4,
    symbols_done: int = 12,
    symbols_total: int = 48,
    percent: float = 58.3,
    message: str = "Forecasting AAPL",
    age_seconds: float = 0.0,
) -> Path:
    """Write a ``progress.json`` fixture, back-dating ``updated_at`` by
    ``age_seconds`` so staleness can be exercised deterministically."""
    from datetime import datetime, timedelta, timezone

    updated_at = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    payload = {
        "run_id": "orch-test-1",
        "state": state,
        "stage": stage,
        "stage_index": stage_index,
        "stage_total": stage_total,
        "symbols_done": symbols_done,
        "symbols_total": symbols_total,
        "percent": percent,
        "message": message,
        "started_at": (updated_at - timedelta(seconds=30)).isoformat(),
        "updated_at": updated_at.isoformat(),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "progress.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ===========================================================================
# None handle
# ===========================================================================


def test_compute_run_progress_none_handle():
    from gui.orchestrator_runner import compute_run_progress

    assert compute_run_progress(None) is None


# ===========================================================================
# Subprocess backend — fresh progress.json
# ===========================================================================


def test_subprocess_fresh_progress_returns_real_percent(tmp_path):
    from gui.orchestrator_runner import compute_run_progress
    from settings import settings

    _write_progress_json(tmp_path, percent=58.3, age_seconds=0.0)
    handle = _make_handle(is_running=True, backend="subprocess")

    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        rp = compute_run_progress(handle)

    assert rp is not None
    assert rp.indeterminate is False
    assert rp.percent == pytest.approx(58.3, abs=0.01)
    assert "58" in rp.label
    assert "12" in rp.label and "48" in rp.label


def test_subprocess_fresh_terminal_succeeded_is_100_percent(tmp_path):
    """A terminal 'succeeded' snapshot always reports 100%, regardless of the
    raw stored percent field (mirrors reporting.progress.ProgressReporter.finish)."""
    from gui.orchestrator_runner import compute_run_progress
    from settings import settings

    _write_progress_json(tmp_path, state="succeeded", percent=100.0, age_seconds=0.0)
    handle = _make_handle(is_running=False, returncode=0, backend="subprocess")

    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        rp = compute_run_progress(handle)

    assert rp is not None
    assert rp.indeterminate is False
    assert rp.percent == pytest.approx(100.0)


# ===========================================================================
# Subprocess backend — stale progress.json falls back to coarse
# ===========================================================================


def test_subprocess_stale_progress_falls_back_to_coarse(tmp_path, monkeypatch):
    from gui.orchestrator_runner import StageStatus, compute_run_progress
    from settings import settings

    # PROGRESS_POLL_SECONDS may not exist on `settings` yet in this worktree
    # (Agent 5 owns it) — try to force a small, known value; ``settings`` is a
    # strict pydantic model, so setting an as-yet-undefined field raises
    # ValueError rather than the AttributeError ``raising=False`` guards
    # against. Either way ``compute_run_progress``'s own
    # ``getattr(settings, "PROGRESS_POLL_SECONDS", 5)`` fallback already
    # yields the same value (5) this test assumes.
    try:
        monkeypatch.setattr(settings, "PROGRESS_POLL_SECONDS", 5, raising=False)
    except Exception:
        pass

    # age_seconds (20s) > 3 * poll_seconds (15s) => stale.
    _write_progress_json(tmp_path, age_seconds=20.0)

    log = tmp_path / "run.log"
    log.write_text("async data fetch\ncompile_dashboard\n", encoding="utf-8")
    handle = _make_handle(
        is_running=True, backend="subprocess", log_path=log, started_at=time.time() - 30
    )

    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        rp = compute_run_progress(handle)

    assert rp is not None
    assert rp.indeterminate is True
    # Two of four stages reached (Data Acquisition + Processing) => 50%.
    assert rp.percent == pytest.approx(50.0)


# ===========================================================================
# Subprocess backend — no progress.json at all
# ===========================================================================


def test_subprocess_missing_progress_running_with_log_is_coarse(tmp_path):
    """No progress.json, but the log shows one stage marker and the run is
    still active -> coarse indeterminate estimate (not None)."""
    from gui.orchestrator_runner import compute_run_progress
    from settings import settings

    log = tmp_path / "run.log"
    log.write_text("async data fetch\n", encoding="utf-8")
    handle = _make_handle(
        is_running=True, backend="subprocess", log_path=log, started_at=time.time() - 5
    )

    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        rp = compute_run_progress(handle)

    assert rp is not None
    assert rp.indeterminate is True
    assert rp.percent == pytest.approx(25.0)  # 1 of 4 stages


def test_subprocess_missing_progress_no_log_returns_none(tmp_path):
    """No progress.json AND no log written yet (nothing observed at all) ->
    None rather than a fabricated 0% bar (CONSTRAINT #4)."""
    from gui.orchestrator_runner import compute_run_progress
    from settings import settings

    handle = _make_handle(
        is_running=True,
        backend="subprocess",
        log_path=tmp_path / "missing.txt",
        started_at=time.time() - 5,
    )

    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        rp = compute_run_progress(handle)

    assert rp is None


def test_subprocess_missing_progress_not_running_returns_none(tmp_path):
    """No progress.json and the run has already finished -> None (nothing
    fresh/terminal to show)."""
    from gui.orchestrator_runner import compute_run_progress
    from settings import settings

    handle = _make_handle(
        is_running=False,
        returncode=0,
        backend="subprocess",
        log_path=tmp_path / "missing.txt",
    )

    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        rp = compute_run_progress(handle)

    assert rp is None


# ===========================================================================
# Daemon backend
# ===========================================================================


def test_daemon_handle_with_progress_dict_returns_real_percent(monkeypatch):
    from gui.orchestrator_runner import compute_run_progress

    fake_status = {
        "run_id": "daemon-run-1",
        "state": "running",
        "progress": {
            "run_id": "daemon-run-1",
            "state": "running",
            "stage": "processing",
            "stage_index": 1,
            "stage_total": 4,
            "symbols_done": 5,
            "symbols_total": 20,
            "percent": 31.25,
            "message": "Processing MSFT",
            "started_at": "2026-07-10T00:00:00+00:00",
            "updated_at": "2026-07-10T00:00:05+00:00",
        },
    }

    monkeypatch.setattr(
        "gui.daemon_client.get_run_status", lambda run_id, timeout=2.0: fake_status
    )

    handle = _make_handle(is_running=True, backend="daemon", daemon_run_id="daemon-run-1")
    rp = compute_run_progress(handle)

    assert rp is not None
    assert rp.indeterminate is False
    assert rp.percent == pytest.approx(31.25, abs=0.01)
    assert "31" in rp.label
    assert "5" in rp.label and "20" in rp.label


def test_daemon_handle_terminal_succeeded_progress_is_100_percent(monkeypatch):
    from gui.orchestrator_runner import compute_run_progress

    fake_status = {
        "run_id": "daemon-run-2",
        "state": "succeeded",
        "progress": {
            "run_id": "daemon-run-2",
            "state": "succeeded",
            "stage": "execution",
            "stage_index": 3,
            "stage_total": 4,
            "symbols_done": 10,
            "symbols_total": 10,
            "percent": 100.0,
            "message": "succeeded",
            "started_at": "2026-07-10T00:00:00+00:00",
            "updated_at": "2026-07-10T00:05:00+00:00",
        },
    }

    monkeypatch.setattr(
        "gui.daemon_client.get_run_status", lambda run_id, timeout=2.0: fake_status
    )

    handle = _make_handle(is_running=False, returncode=0, backend="daemon", daemon_run_id="daemon-run-2")
    rp = compute_run_progress(handle)

    assert rp is not None
    assert rp.indeterminate is False
    assert rp.percent == pytest.approx(100.0)


def test_daemon_handle_no_progress_dict_falls_back_to_coarse(monkeypatch):
    """Control API reachable, run status known, but no 'progress' sub-dict
    (e.g. that piece hasn't shipped yet) -> uniform coarse status derived
    from compute_stage_status's daemon branch (all ACTIVE while running)."""
    from gui.orchestrator_runner import compute_run_progress

    fake_status = {"run_id": "daemon-run-3", "state": "running"}
    monkeypatch.setattr(
        "gui.daemon_client.get_run_status", lambda run_id, timeout=2.0: fake_status
    )

    handle = _make_handle(is_running=True, backend="daemon", daemon_run_id="daemon-run-3")
    rp = compute_run_progress(handle)

    assert rp is not None
    assert rp.indeterminate is True
    # compute_stage_status's daemon branch maps "running" -> ACTIVE for every
    # stage, so all 4 of 4 are counted as reached => 100% coarse estimate.
    assert rp.percent == pytest.approx(100.0)


def test_daemon_handle_unreachable_not_running_returns_none(monkeypatch):
    """Daemon unreachable (get_run_status -> None) and the handle reports not
    running -> None (nothing to show, matches the subprocess not-running case)."""
    from gui.orchestrator_runner import compute_run_progress

    monkeypatch.setattr(
        "gui.daemon_client.get_run_status", lambda run_id, timeout=2.0: None
    )

    handle = _make_handle(is_running=False, backend="daemon", daemon_run_id="daemon-run-4")
    rp = compute_run_progress(handle)

    assert rp is None


# ===========================================================================
# Dead-letter resilience — malformed inputs never raise
# ===========================================================================


def test_malformed_progress_payload_never_raises(tmp_path):
    from gui.orchestrator_runner import compute_run_progress
    from settings import settings

    bad_path = tmp_path / "progress.json"
    tmp_path.mkdir(parents=True, exist_ok=True)
    bad_path.write_text("{not valid json", encoding="utf-8")

    handle = _make_handle(
        is_running=True, backend="subprocess", log_path=tmp_path / "missing.txt"
    )

    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        # Must not raise; malformed progress.json degrades to coarse/None.
        rp = compute_run_progress(handle)

    assert rp is None or rp.indeterminate is True


def test_daemon_client_exception_degrades_gracefully(monkeypatch, tmp_path):
    """If gui.daemon_client.get_run_status itself raises, compute_run_progress
    must still degrade (never propagate) — CONSTRAINT #6."""
    from gui.orchestrator_runner import compute_run_progress

    def _boom(run_id, timeout=2.0):
        raise RuntimeError("network exploded")

    monkeypatch.setattr("gui.daemon_client.get_run_status", _boom)

    handle = _make_handle(is_running=False, backend="daemon", daemon_run_id="daemon-run-5")
    rp = compute_run_progress(handle)

    assert rp is None
