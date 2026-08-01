"""Tests for skills/discovery_skill.py.

Covers the fix from querying a nonexistent 'dashboard' SQL table to reading
the live advisory engine's per-symbol output from output/state_snapshot.json
(the same file api/state_api.py's /state endpoint serves).
"""
from __future__ import annotations

import json

import pytest

from settings import settings
import skills.discovery_skill as discovery_skill
from skills.discovery_skill import (
    _db_advisory_scores,
    create_scan,
    run_scan,
    update_scan_filters,
)


@pytest.fixture(autouse=True)
def _isolated_scans_and_cwd(tmp_path, monkeypatch):
    """Each test gets an empty in-process scan registry and its own cwd, so
    run_scan's relative 'output/scan_candidates.json' write doesn't touch the
    real repo output/ directory."""
    discovery_skill._SCANS.clear()
    monkeypatch.chdir(tmp_path)
    yield
    discovery_skill._SCANS.clear()


def _write_snapshot(tmp_path, monkeypatch, signals):
    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
    (tmp_path / "state_snapshot.json").write_text(
        json.dumps({"signals": signals}), encoding="utf-8"
    )


class TestDbAdvisoryScores:
    def test_no_snapshot_file_degrades_to_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
        assert _db_advisory_scores(min_score=0.0) == []

    def test_reads_real_snapshot_fields(self, tmp_path, monkeypatch):
        _write_snapshot(
            tmp_path,
            monkeypatch,
            [
                {"symbol": "NVDA", "score": 0.82, "action": "STRONG BUY"},
                {"symbol": "AAPL", "score": 0.41, "action": "HOLD"},
            ],
        )
        result = _db_advisory_scores(min_score=0.0)
        assert result == [
            {"symbol": "NVDA", "score": 0.82, "recommendation": "STRONG BUY"},
            {"symbol": "AAPL", "score": 0.41, "recommendation": "HOLD"},
        ]

    def test_min_score_filters(self, tmp_path, monkeypatch):
        _write_snapshot(
            tmp_path,
            monkeypatch,
            [
                {"symbol": "NVDA", "score": 0.82, "action": "STRONG BUY"},
                {"symbol": "XOM", "score": 0.15, "action": "SELL"},
            ],
        )
        result = _db_advisory_scores(min_score=0.5)
        assert [c["symbol"] for c in result] == ["NVDA"]

    def test_malformed_json_degrades_to_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
        (tmp_path / "state_snapshot.json").write_text("{not valid json", encoding="utf-8")
        assert _db_advisory_scores(min_score=0.0) == []

    def test_missing_symbol_skipped(self, tmp_path, monkeypatch):
        _write_snapshot(tmp_path, monkeypatch, [{"score": 0.9, "action": "BUY"}])
        assert _db_advisory_scores(min_score=0.0) == []


class TestRunScan:
    def test_end_to_end_min_score_and_writes_output(self, tmp_path, monkeypatch):
        _write_snapshot(
            tmp_path,
            monkeypatch,
            [
                {"symbol": "NVDA", "score": 0.82, "action": "STRONG BUY"},
                {"symbol": "AAPL", "score": 0.41, "action": "HOLD"},
            ],
        )
        create_scan("s1", {"min_score": 0.5})
        msg = run_scan("s1")
        assert "1 candidates" in msg

        # run_scan writes relative to cwd (monkeypatched to tmp_path above).
        out_path = tmp_path / "output" / "scan_candidates.json"
        payload = json.loads(out_path.read_text())
        assert payload["candidates"] == [
            {"symbol": "NVDA", "score": 0.82, "recommendation": "STRONG BUY"}
        ]

    def test_recommendation_filter(self, tmp_path, monkeypatch):
        _write_snapshot(
            tmp_path,
            monkeypatch,
            [
                {"symbol": "NVDA", "score": 0.82, "action": "STRONG BUY"},
                {"symbol": "XOM", "score": 0.15, "action": "SELL"},
            ],
        )
        create_scan("s2", {"min_score": 0.0, "recommendation": "SELL"})
        run_scan("s2")
        out_path = tmp_path / "output" / "scan_candidates.json"
        payload = json.loads(out_path.read_text())
        assert [c["symbol"] for c in payload["candidates"]] == ["XOM"]

    def test_max_rsi_criterion_is_ignored_not_fabricated(self, tmp_path, monkeypatch, caplog):
        """max_rsi has no backing field in state_snapshot.json — it must be
        logged as unsupported, never silently applied against fake data."""
        _write_snapshot(
            tmp_path, monkeypatch, [{"symbol": "NVDA", "score": 0.9, "action": "BUY"}]
        )
        create_scan("s3", {"min_score": 0.0, "max_rsi": 30})
        import logging
        with caplog.at_level(logging.WARNING, logger="skills.discovery_skill"):
            run_scan("s3")
        assert any("max_rsi" in rec.message for rec in caplog.records)
        out_path = tmp_path / "output" / "scan_candidates.json"
        payload = json.loads(out_path.read_text())
        # Not filtered out just because a (currently unsupported) max_rsi was set.
        assert [c["symbol"] for c in payload["candidates"]] == ["NVDA"]

    def test_unknown_scan_id_errors_cleanly(self):
        assert "not found" in run_scan("does-not-exist")


class TestUpdateScanFilters:
    def test_update_then_run_reflects_new_criteria(self, tmp_path, monkeypatch):
        _write_snapshot(
            tmp_path,
            monkeypatch,
            [{"symbol": "AAPL", "score": 0.6, "action": "BUY"}],
        )
        create_scan("s4", {"min_score": 0.9})
        assert "0 candidates" in run_scan("s4")
        update_scan_filters("s4", {"min_score": 0.1})
        assert "1 candidates" in run_scan("s4")
