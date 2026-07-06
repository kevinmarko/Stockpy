"""
tests/test_track_record_status.py
=================================
Unit tests for ``scripts/track_record_status.py``.

Fully offline — every field is derived from local files (fixtures written into
``tmp_path``) plus an injected reference date, so no network or real ``output/``
state is touched.  Covers:

* gate math (days-elapsed / days-remaining / gate_met) against a fixture
  ``PAPER_TRADING_START_DATE`` and an injected ``today``;
* ``decision_log.jsonl`` row counting (blank-line tolerant);
* staleness derived from file mtimes;
* ``--json`` CLI output is valid JSON with the expected top-level keys;
* missing-file paths degrade gracefully (no crash, sane defaults).
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts import track_record_status as trs

_REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Gate math
# ---------------------------------------------------------------------------

class TestGateStatus:
    def test_days_elapsed_and_remaining(self):
        today = date(2026, 7, 5)
        start = (today - timedelta(days=40)).isoformat()  # 40 days elapsed
        g = trs.compute_gate_status(start, today=today)
        assert g["days_elapsed"] == 40
        assert g["days_remaining"] == 50  # 90 - 40
        assert g["gate_met"] is False
        assert g["start_date"] == start
        assert g["gate_days"] == 90
        # go-live date = start + 90 days
        assert g["go_live_date"] == (date.fromisoformat(start) + timedelta(days=90)).isoformat()

    def test_gate_met(self):
        today = date(2026, 7, 5)
        start = (today - timedelta(days=100)).isoformat()
        g = trs.compute_gate_status(start, today=today)
        assert g["days_elapsed"] == 100
        assert g["days_remaining"] == 0  # clamped, never negative
        assert g["gate_met"] is True

    def test_unset_start_date(self):
        g = trs.compute_gate_status(None, today=date(2026, 7, 5))
        assert g["days_elapsed"] is None
        assert g["days_remaining"] is None
        assert g["gate_met"] is False
        assert "not set" in g["note"].lower()

    def test_invalid_start_date(self):
        g = trs.compute_gate_status("not-a-date", today=date(2026, 7, 5))
        assert g["days_elapsed"] is None
        assert g["gate_met"] is False
        assert "invalid" in g["note"].lower()

    def test_custom_gate_days(self):
        today = date(2026, 7, 5)
        start = (today - timedelta(days=10)).isoformat()
        g = trs.compute_gate_status(start, today=today, gate_days=30)
        assert g["days_elapsed"] == 10
        assert g["days_remaining"] == 20
        assert g["gate_days"] == 30


# ---------------------------------------------------------------------------
# decision_log.jsonl row counting
# ---------------------------------------------------------------------------

class TestDecisionLogCount:
    def test_counts_nonblank_rows(self, tmp_path: Path):
        log = tmp_path / "decision_log.jsonl"
        rows = [
            {"symbol": "AAL", "action_taken": "passed"},
            {"symbol": "AAPL", "action_taken": "passed"},
            {"symbol": "MSFT", "action_taken": "recorded"},
        ]
        log.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        assert trs.count_decision_log_rows(tmp_path) == 3

    def test_ignores_blank_lines(self, tmp_path: Path):
        log = tmp_path / "decision_log.jsonl"
        log.write_text('{"a": 1}\n\n   \n{"b": 2}\n', encoding="utf-8")
        assert trs.count_decision_log_rows(tmp_path) == 2

    def test_missing_file_returns_zero(self, tmp_path: Path):
        assert trs.count_decision_log_rows(tmp_path) == 0


# ---------------------------------------------------------------------------
# Staleness
# ---------------------------------------------------------------------------

class TestStaleness:
    def test_missing_files_are_none(self, tmp_path: Path):
        s = trs.compute_staleness(tmp_path)
        assert s["heartbeat_age_seconds"] is None
        assert s["heartbeat_age_hours"] is None
        assert s["snapshot_age_seconds"] is None
        assert s["snapshot_age_hours"] is None

    def test_ages_derived_from_mtime(self, tmp_path: Path):
        hb = tmp_path / "heartbeat.txt"
        snap = tmp_path / "state_snapshot.json"
        hb.write_text("ts", encoding="utf-8")
        snap.write_text("{}", encoding="utf-8")
        now = datetime.now(timezone.utc) + timedelta(hours=2)
        s = trs.compute_staleness(tmp_path, now=now)
        # Both files created ~2h before `now`.
        assert s["heartbeat_age_hours"] == pytest.approx(2.0, abs=0.05)
        assert s["snapshot_age_hours"] == pytest.approx(2.0, abs=0.05)


# ---------------------------------------------------------------------------
# Assembler + graceful degradation
# ---------------------------------------------------------------------------

class TestBuildStatus:
    def test_build_status_shape(self, tmp_path: Path, monkeypatch):
        # Fixture decision log + heartbeat.
        (tmp_path / "decision_log.jsonl").write_text('{"x":1}\n{"x":2}\n', encoding="utf-8")
        (tmp_path / "heartbeat.txt").write_text("ts", encoding="utf-8")

        today = date(2026, 7, 5)
        start = (today - timedelta(days=45)).isoformat()
        monkeypatch.setattr(trs, "_read_paper_trading_start_date", lambda: start)

        status = trs.build_status(tmp_path, today=today)
        assert set(status.keys()) >= {
            "generated_at", "gate", "calibration_history_rows",
            "calibration", "staleness",
        }
        assert status["calibration_history_rows"] == 2
        assert status["gate"]["days_elapsed"] == 45
        assert status["gate"]["days_remaining"] == 45

    def test_build_status_all_missing_no_crash(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(trs, "_read_paper_trading_start_date", lambda: None)
        status = trs.build_status(tmp_path, today=date(2026, 7, 5))
        assert status["calibration_history_rows"] == 0
        assert status["gate"]["gate_met"] is False
        assert status["staleness"]["heartbeat_age_hours"] is None
        # format_status must also never raise on a degraded status.
        text = trs.format_status(status)
        assert "Track-Record Status" in text


# ---------------------------------------------------------------------------
# CLI --json
# ---------------------------------------------------------------------------

class TestCLI:
    def test_json_output_valid(self, tmp_path: Path):
        (tmp_path / "decision_log.jsonl").write_text('{"x":1}\n{"x":2}\n{"x":3}\n', encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "scripts/track_record_status.py",
             "--json", "--output-dir", str(tmp_path)],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)  # must be valid JSON
        assert set(payload.keys()) >= {
            "generated_at", "gate", "calibration_history_rows",
            "calibration", "staleness",
        }
        assert payload["calibration_history_rows"] == 3
        # No secret-looking keys leaked into the payload.
        blob = json.dumps(payload).lower()
        for banned in ("password", "secret", "api_key", "mfa", "token"):
            assert banned not in blob

    def test_human_output_runs(self, tmp_path: Path):
        result = subprocess.run(
            [sys.executable, "scripts/track_record_status.py",
             "--output-dir", str(tmp_path)],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "90-Day Go-Live Gate" in result.stdout
