"""tests/test_daemon_status.py — unit tests for desktop/daemon_status.py.

This CLI is a thin formatting layer over ``pilots.run_status.read_daemon_json()``
(already unit-tested in ``tests/test_run_status.py``) -- these tests focus on
the summary-sentence construction, the JSON/human printouts, and the exit
code contract, using the same ``mock.patch.object(settings, "OUTPUT_DIR", ...)``
/ ``mock.patch.object(run_status.os, "kill", ...)`` isolation pattern already
established there so no real daemon.json or pid is ever touched.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

from settings import settings
from pilots import run_status
from desktop import daemon_status


def _write_daemon_json(tmp_path, **overrides) -> dict:
    payload = {
        "pid": 4242,
        "state": "started",
        "interval_seconds": 300,
        "started_at": (datetime.now(timezone.utc) - timedelta(hours=3, minutes=12)).isoformat(),
        "stopped_at": None,
        "port": 8601,
        "pilots_api_port": 8602,
    }
    payload.update(overrides)
    (tmp_path / "daemon.json").write_text(json.dumps(payload), encoding="utf-8")
    return payload


# ---------------------------------------------------------------------------
# get_status() -- the three required scenarios
# ---------------------------------------------------------------------------

class TestGetStatusLivePid:
    """(a) A live pid -> reports alive with the correct fields."""

    def test_reports_alive_with_correct_fields(self, tmp_path):
        payload = _write_daemon_json(tmp_path)
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path), \
             mock.patch.object(run_status.os, "kill", return_value=None):
            status = daemon_status.get_status()

        assert status["found"] is True
        assert status["pid"] == payload["pid"]
        assert status["pid_alive"] is True
        assert status["state"] == "started"
        assert status["started_at"] == payload["started_at"]
        assert status["stopped_at"] is None
        assert status["port"] == 8601
        assert status["pilots_api_port"] == 8602
        assert status["interval_seconds"] == 300
        # ~3h12m uptime
        assert status["uptime_seconds"] is not None
        assert 3 * 3600 + 11 * 60 <= status["uptime_seconds"] <= 3 * 3600 + 13 * 60
        assert "ALIVE" in status["summary"]
        assert str(payload["pid"]) in status["summary"]

    def test_own_real_pid_reports_alive_end_to_end(self, tmp_path):
        """No os.kill patching at all -- exercises the real syscall against
        this test process's own pid, matching test_run_status.py's
        test_own_pid_is_alive."""
        _write_daemon_json(tmp_path, pid=os.getpid())
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            status = daemon_status.get_status()
        assert status["pid_alive"] is True
        assert "ALIVE" in status["summary"]

    def test_alive_but_self_reported_stopped_gets_explicit_note(self, tmp_path):
        """A pid answering despite state='stopped' (e.g. pid reuse) must be
        surfaced honestly rather than silently trusting one field."""
        _write_daemon_json(tmp_path, state="stopped", stopped_at="2026-08-01T00:00:00+00:00")
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path), \
             mock.patch.object(run_status.os, "kill", return_value=None):
            status = daemon_status.get_status()
        assert status["pid_alive"] is True
        assert "NOTE" in status["summary"]
        assert "trust pid_alive" in status["summary"]


class TestGetStatusDeadPid:
    """(b) A stale daemon.json naming a pid that no longer exists -> DOWN."""

    def test_reports_down_clearly(self, tmp_path):
        payload = _write_daemon_json(tmp_path, state="started", stopped_at=None)
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path), \
             mock.patch.object(run_status.os, "kill", side_effect=ProcessLookupError):
            status = daemon_status.get_status()

        assert status["found"] is True
        assert status["pid"] == payload["pid"]
        assert status["pid_alive"] is False
        assert "DOWN" in status["summary"]
        assert str(payload["pid"]) in status["summary"]

    def test_clean_shutdown_then_dead_pid_mentions_stopped_at(self, tmp_path):
        """state='stopped' + stopped_at + dead pid -- the daemon shut down
        gracefully and simply hasn't been restarted; the summary should say
        so rather than implying a crash."""
        stopped_at = datetime.now(timezone.utc).isoformat()
        payload = _write_daemon_json(tmp_path, state="stopped", stopped_at=stopped_at)
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path), \
             mock.patch.object(run_status.os, "kill", side_effect=ProcessLookupError):
            status = daemon_status.get_status()

        assert status["pid_alive"] is False
        assert "DOWN" in status["summary"]
        assert "shut down cleanly" in status["summary"]
        assert stopped_at in status["summary"]

    def test_crash_without_clean_shutdown_does_not_claim_clean_shutdown(self, tmp_path):
        """state='started' (never reached the terminal write) + dead pid ->
        the summary must NOT claim a clean shutdown that never happened."""
        payload = _write_daemon_json(tmp_path, state="started", stopped_at=None)
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path), \
             mock.patch.object(run_status.os, "kill", side_effect=ProcessLookupError):
            status = daemon_status.get_status()

        assert status["pid_alive"] is False
        assert "DOWN" in status["summary"]
        assert "shut down cleanly" not in status["summary"]


class TestGetStatusNeverStarted:
    """(c) No daemon.json at all -> "never started", never crashes."""

    def test_missing_file_reports_never_started(self, tmp_path):
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            status = daemon_status.get_status()

        assert status["found"] is False
        assert status["pid"] is None
        assert status["pid_alive"] is None
        assert status["state"] is None
        assert status["uptime_seconds"] is None
        assert "NEVER STARTED" in status["summary"]

    def test_malformed_json_also_degrades_cleanly(self, tmp_path):
        (tmp_path / "daemon.json").write_text("{not valid json", encoding="utf-8")
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            status = daemon_status.get_status()
        assert status["found"] is False
        assert "NEVER STARTED" in status["summary"]


class TestGetStatusUnknownPidAlive:
    """pid_alive is None (unknowable) -- e.g. a malformed pid value on disk."""

    def test_unparseable_pid_reports_unknown(self, tmp_path):
        _write_daemon_json(tmp_path, pid="not-a-pid")
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            status = daemon_status.get_status()
        assert status["pid_alive"] is None
        assert "UNKNOWN" in status["summary"]


# ---------------------------------------------------------------------------
# _format_duration
# ---------------------------------------------------------------------------

class TestFormatDuration:
    def test_minutes_only(self):
        assert daemon_status._format_duration(5 * 60) == "5m"

    def test_hours_and_minutes(self):
        assert daemon_status._format_duration(3 * 3600 + 12 * 60) == "3h 12m"

    def test_days_hours_minutes(self):
        assert daemon_status._format_duration(86400 + 2 * 3600 + 5 * 60) == "1d 2h 5m"

    def test_negative_clamped_to_zero(self):
        assert daemon_status._format_duration(-10) == "0m"

    def test_zero(self):
        assert daemon_status._format_duration(0) == "0m"


# ---------------------------------------------------------------------------
# main() -- CLI wiring, --json flag, exit codes
# ---------------------------------------------------------------------------

class TestMainCLI:
    def test_human_output_exit_zero_when_alive(self, tmp_path, capsys):
        _write_daemon_json(tmp_path)
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path), \
             mock.patch.object(run_status.os, "kill", return_value=None):
            code = daemon_status.main([])
        out = capsys.readouterr().out
        assert code == 0
        assert "Orchestrator Daemon Status" in out
        assert "ALIVE" in out

    def test_json_output_is_valid_json_and_exit_one_when_down(self, tmp_path, capsys):
        payload = _write_daemon_json(tmp_path)
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path), \
             mock.patch.object(run_status.os, "kill", side_effect=ProcessLookupError):
            code = daemon_status.main(["--json"])
        out = capsys.readouterr().out
        assert code == 1
        parsed = json.loads(out)
        assert parsed["pid"] == payload["pid"]
        assert parsed["pid_alive"] is False

    def test_never_started_exits_nonzero_and_never_raises(self, tmp_path, capsys):
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            code = daemon_status.main(["--json"])
        out = capsys.readouterr().out
        assert code == 1
        parsed = json.loads(out)
        assert parsed["found"] is False

    def test_help_does_not_raise(self):
        with pytest.raises(SystemExit) as exc_info:
            daemon_status.main(["--help"])
        assert exc_info.value.code == 0
