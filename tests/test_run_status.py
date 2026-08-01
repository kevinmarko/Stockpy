"""tests/test_run_status.py — unit tests for pilots/run_status.py's pure
file-reading helpers, independent of the FastAPI layer (see
tests/test_pilots_api.py's TestAutomationStatus/TestAutomationSchedule for the
endpoint-level composition tests, which cover most of this indirectly — this
file adds direct coverage for paths those don't reach, notably
heartbeat_age_seconds (no pilots_api test writes a heartbeat.txt fixture) and
parse_crontab's comment-stripping edge cases (separator lines, blank-line
resets) beyond what the one real deploy/crontab.txt exercises."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

from settings import settings
from pilots import run_status


class TestSnapshotAgeSeconds:
    def test_missing_file_returns_none_missing(self, tmp_path):
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            age, source = run_status.snapshot_age_seconds()
        assert age is None
        assert source == "missing"

    def test_timestamp_field_used_when_present(self, tmp_path):
        ts = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
        (tmp_path / "state_snapshot.json").write_text(
            json.dumps({"timestamp": ts}), encoding="utf-8"
        )
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            age, source = run_status.snapshot_age_seconds()
        assert source == "timestamp"
        assert 25 <= age <= 35

    def test_naive_timestamp_treated_as_utc(self, tmp_path):
        """fromisoformat on a naive string produces a naive datetime; the
        function must attach UTC rather than raising on the tz-aware subtraction."""
        naive = (datetime.now(timezone.utc) - timedelta(seconds=10)).replace(tzinfo=None)
        (tmp_path / "state_snapshot.json").write_text(
            json.dumps({"timestamp": naive.isoformat()}), encoding="utf-8"
        )
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            age, source = run_status.snapshot_age_seconds()
        assert source == "timestamp"
        assert age is not None and age >= 0

    def test_missing_timestamp_field_falls_back_to_mtime(self, tmp_path):
        (tmp_path / "state_snapshot.json").write_text(json.dumps({}), encoding="utf-8")
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            age, source = run_status.snapshot_age_seconds()
        assert source == "mtime"
        assert age is not None and age < 5.0

    def test_malformed_json_degrades_to_missing(self, tmp_path):
        (tmp_path / "state_snapshot.json").write_text("{not json", encoding="utf-8")
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            age, source = run_status.snapshot_age_seconds()
        assert age is None
        assert source == "missing"


class TestHeartbeatAgeSeconds:
    def test_missing_file_returns_none(self, tmp_path):
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            assert run_status.heartbeat_age_seconds() is None

    def test_fresh_heartbeat_returns_small_age(self, tmp_path):
        ts = datetime.now(timezone.utc).isoformat()
        (tmp_path / "heartbeat.txt").write_text(ts, encoding="utf-8")
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            age = run_status.heartbeat_age_seconds()
        assert age is not None and age < 5.0

    def test_stale_heartbeat_returns_large_age(self, tmp_path):
        ts = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        (tmp_path / "heartbeat.txt").write_text(ts, encoding="utf-8")
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            age = run_status.heartbeat_age_seconds()
        assert age is not None and age > 3600 * 2.9

    def test_malformed_content_degrades_to_none(self, tmp_path):
        (tmp_path / "heartbeat.txt").write_text("not a timestamp", encoding="utf-8")
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            assert run_status.heartbeat_age_seconds() is None


class TestReadDaemonJson:
    def test_missing_file_returns_none(self, tmp_path):
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            assert run_status.read_daemon_json() is None

    def test_valid_file_round_trips_plus_derived_pid_alive(self, tmp_path):
        """read_daemon_json's contract changed deliberately: the returned
        dict is the file's contents PLUS a derived "pid_alive" key -- see
        the function's docstring for why this is additive-on-the-record
        rather than a second helper a caller could forget to call."""
        payload = {"pid": 123, "interval_seconds": 60, "started_at": "x", "port": 8601, "pilots_api_port": None}
        (tmp_path / "daemon.json").write_text(json.dumps(payload), encoding="utf-8")
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path), \
             mock.patch.object(run_status.os, "kill", side_effect=ProcessLookupError):
            result = run_status.read_daemon_json()
        assert result == {**payload, "pid_alive": False}

    def test_malformed_json_degrades_to_none(self, tmp_path):
        (tmp_path / "daemon.json").write_text("{not json", encoding="utf-8")
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            assert run_status.read_daemon_json() is None

    def test_non_dict_json_degrades_to_none(self, tmp_path):
        (tmp_path / "daemon.json").write_text("[1, 2, 3]", encoding="utf-8")
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            assert run_status.read_daemon_json() is None


class TestPidAlive:
    """run_status._pid_alive -- the reader-side half of the daemon.json
    staleness fix. A SIGKILLed daemon can never update its own file, so
    this probe is what actually detects that case; read_daemon_json's
    "pid_alive" key is a thin wrapper around it."""

    def test_own_pid_is_alive(self):
        assert run_status._pid_alive(os.getpid()) is True

    def test_dead_pid_reports_false(self):
        with mock.patch.object(run_status.os, "kill", side_effect=ProcessLookupError):
            assert run_status._pid_alive(123) is False

    def test_foreign_pid_permission_error_reports_true(self):
        """Owned by another user: the process exists, so this is "alive",
        not "unknown"."""
        with mock.patch.object(run_status.os, "kill", side_effect=PermissionError):
            assert run_status._pid_alive(123) is True

    def test_missing_pid_is_none_never_false(self):
        """CONSTRAINT #4: absent evidence must never collapse to a
        fabricated False."""
        result = run_status._pid_alive(None)
        assert result is None
        assert result is not False

    @pytest.mark.parametrize("bad_pid", ["abc", None, 12.5, {}, [], object()])
    def test_unparseable_pid_is_none(self, bad_pid):
        assert run_status._pid_alive(bad_pid) is None

    def test_bool_pid_is_none_not_treated_as_int(self):
        """bool is an int subclass in Python -- a JSON `true` must not
        silently become os.kill(1, 0) (PID 1, essentially always alive)."""
        with mock.patch.object(run_status.os, "kill") as mock_kill:
            assert run_status._pid_alive(True) is None
        mock_kill.assert_not_called()

    @pytest.mark.parametrize("bad_pid", [0, -1])
    def test_zero_and_negative_pid_is_none_and_never_signals_a_process_group(self, bad_pid):
        """pid 0 or a negative pid targets an entire PROCESS GROUP for
        os.kill -- must be rejected before ever reaching the syscall,
        regardless of how harmless signal 0 itself is."""
        with mock.patch.object(run_status.os, "kill") as mock_kill:
            assert run_status._pid_alive(bad_pid) is None
        mock_kill.assert_not_called()

    def test_unexpected_oserror_is_none_never_raises(self):
        with mock.patch.object(run_status.os, "kill", side_effect=OSError("weird")):
            assert run_status._pid_alive(123) is None


class TestReadDeadLetter:
    def test_missing_file_returns_empty_shape(self, tmp_path):
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            result = run_status.read_dead_letter()
        assert result == {"generated_at": None, "entry_count": 0, "entries": []}

    def test_entries_present_and_within_limit(self, tmp_path):
        payload = {"generated_at": "t", "entries": [{"symbol": "AAPL"}]}
        (tmp_path / "dead_letter.json").write_text(json.dumps(payload), encoding="utf-8")
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            result = run_status.read_dead_letter()
        assert result["entry_count"] == 1
        assert result["entries"] == [{"symbol": "AAPL"}]

    def test_entry_count_is_true_total_even_when_capped(self, tmp_path):
        entries = [{"symbol": f"S{i}"} for i in range(10)]
        payload = {"generated_at": "t", "entries": entries}
        (tmp_path / "dead_letter.json").write_text(json.dumps(payload), encoding="utf-8")
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            result = run_status.read_dead_letter(limit=3)
        assert result["entry_count"] == 10
        assert len(result["entries"]) == 3

    def test_entries_not_a_list_degrades_to_empty(self, tmp_path):
        payload = {"generated_at": "t", "entries": "not a list"}
        (tmp_path / "dead_letter.json").write_text(json.dumps(payload), encoding="utf-8")
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            result = run_status.read_dead_letter()
        assert result["entries"] == []
        assert result["entry_count"] == 0


class TestParseCrontab:
    def test_missing_file_returns_empty_list(self, tmp_path):
        assert run_status.parse_crontab(tmp_path / "nope.txt") == []

    def test_single_entry_with_comment(self, tmp_path):
        p = tmp_path / "crontab.txt"
        p.write_text(
            "# Daily briefing\n"
            "# Runs the advisory pipeline.\n"
            "0 21 * * 1-5 cd /opt && python x.py\n",
            encoding="utf-8",
        )
        entries = run_status.parse_crontab(p)
        assert len(entries) == 1
        assert entries[0]["schedule"] == "0 21 * * 1-5"
        assert entries[0]["command"] == "cd /opt && python x.py"
        assert "Daily briefing" in entries[0]["comment"]
        assert "Runs the advisory pipeline." in entries[0]["comment"]

    def test_pure_separator_lines_are_not_treated_as_comment_content(self, tmp_path):
        p = tmp_path / "crontab.txt"
        p.write_text(
            "# ===========\n"
            "# Real label\n"
            "# ───────────\n"
            "0 3 * * * cmd here\n",
            encoding="utf-8",
        )
        entries = run_status.parse_crontab(p)
        assert entries[0]["comment"] == "Real label"

    def test_blank_line_resets_the_comment_buffer(self, tmp_path):
        """A comment block that isn't immediately followed by its schedule
        line (separated by a blank line) must not leak onto a later entry."""
        p = tmp_path / "crontab.txt"
        p.write_text(
            "# Orphaned comment, no schedule line follows it\n"
            "\n"
            "0 3 * * * cmd here\n",
            encoding="utf-8",
        )
        entries = run_status.parse_crontab(p)
        assert len(entries) == 1
        assert entries[0]["comment"] == ""

    def test_multiple_entries_each_get_their_own_comment(self, tmp_path):
        p = tmp_path / "crontab.txt"
        p.write_text(
            "# First job\n"
            "0 1 * * * cmd-one\n"
            "\n"
            "# Second job\n"
            "0 2 * * * cmd-two\n",
            encoding="utf-8",
        )
        entries = run_status.parse_crontab(p)
        assert len(entries) == 2
        assert entries[0]["comment"] == "First job"
        assert entries[0]["command"] == "cmd-one"
        assert entries[1]["comment"] == "Second job"
        assert entries[1]["command"] == "cmd-two"

    def test_non_cron_lines_are_skipped(self, tmp_path):
        p = tmp_path / "crontab.txt"
        p.write_text("not a valid cron line at all\n0 1 * * * real-cmd\n", encoding="utf-8")
        entries = run_status.parse_crontab(p)
        assert len(entries) == 1
        assert entries[0]["command"] == "real-cmd"

    def test_default_path_reads_the_real_repo_crontab(self):
        """No path override -> reads deploy/crontab.txt relative to the repo
        root computed from this module's own location."""
        entries = run_status.parse_crontab()
        assert len(entries) >= 1
        assert all({"schedule", "command", "comment"} <= e.keys() for e in entries)
