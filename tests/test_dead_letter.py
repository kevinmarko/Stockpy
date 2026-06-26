"""Tests for gui/dead_letter.py — dead-letter queue read side.

Covers:
- DeadLetterEntry and DeadLetterReport frozen dataclass contracts.
- read_dead_letter() against valid, corrupt, empty-entries, and missing files.
- Report convenience properties (symbols, is_clean).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from gui.dead_letter import (
    DEAD_LETTER_PATH,
    DeadLetterEntry,
    DeadLetterReport,
    read_dead_letter,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _sample_payload(entries: list[dict] | None = None) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "run_id": now,
        "generated_at": now,
        "entries": entries if entries is not None else [],
    }


def _write_payload(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "dead_letter.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# DeadLetterEntry
# ---------------------------------------------------------------------------

class TestDeadLetterEntry:
    def test_frozen(self) -> None:
        e = DeadLetterEntry(symbol="AAPL", stage="strategy", error="boom", timestamp="2026-06-26T00:00:00")
        with pytest.raises((AttributeError, TypeError)):
            e.symbol = "MSFT"  # type: ignore[misc]

    def test_fields_preserved(self) -> None:
        e = DeadLetterEntry(symbol="HKIT", stage="edge_ratio", error="ZeroDivisionError: blah", timestamp="T")
        assert e.symbol == "HKIT"
        assert e.stage == "edge_ratio"
        assert "ZeroDivisionError" in e.error
        assert e.timestamp == "T"


# ---------------------------------------------------------------------------
# DeadLetterReport
# ---------------------------------------------------------------------------

class TestDeadLetterReport:
    def test_is_clean_empty(self) -> None:
        r = DeadLetterReport(run_id="X", generated_at="Y", entries=[])
        assert r.is_clean

    def test_is_clean_with_entries(self) -> None:
        entry = DeadLetterEntry("AAPL", "strategy", "boom", "T")
        r = DeadLetterReport(run_id="X", generated_at="Y", entries=[entry])
        assert not r.is_clean

    def test_symbols_property(self) -> None:
        entries = [
            DeadLetterEntry("AAPL", "strategy", "e", "T"),
            DeadLetterEntry("MSFT", "edge_ratio", "e", "T"),
        ]
        r = DeadLetterReport(run_id="X", generated_at="Y", entries=entries)
        assert r.symbols == ["AAPL", "MSFT"]

    def test_frozen(self) -> None:
        r = DeadLetterReport(run_id="X", generated_at="Y", entries=[])
        with pytest.raises((AttributeError, TypeError)):
            r.run_id = "Z"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# read_dead_letter
# ---------------------------------------------------------------------------

class TestReadDeadLetter:
    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        result = read_dead_letter(path=tmp_path / "nonexistent.json")
        assert result is None

    def test_clean_run(self, tmp_path: Path) -> None:
        p = _write_payload(tmp_path, _sample_payload(entries=[]))
        report = read_dead_letter(path=p)
        assert report is not None
        assert report.is_clean
        assert report.symbols == []

    def test_one_entry_parsed(self, tmp_path: Path) -> None:
        payload = _sample_payload(entries=[
            {"symbol": "HKIT", "stage": "strategy", "error": "float division", "timestamp": "T"}
        ])
        p = _write_payload(tmp_path, payload)
        report = read_dead_letter(path=p)
        assert report is not None
        assert len(report.entries) == 1
        assert report.entries[0].symbol == "HKIT"
        assert report.entries[0].stage == "strategy"

    def test_multiple_entries(self, tmp_path: Path) -> None:
        payload = _sample_payload(entries=[
            {"symbol": "AAPL", "stage": "dto_construction", "error": "e1", "timestamp": "T1"},
            {"symbol": "MSFT", "stage": "edge_ratio", "error": "e2", "timestamp": "T2"},
        ])
        p = _write_payload(tmp_path, payload)
        report = read_dead_letter(path=p)
        assert report is not None
        assert report.symbols == ["AAPL", "MSFT"]

    def test_corrupt_json_returns_none(self, tmp_path: Path) -> None:
        p = tmp_path / "dead_letter.json"
        p.write_text("{not valid json", encoding="utf-8")
        result = read_dead_letter(path=p)
        assert result is None

    def test_missing_entries_key_yields_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "dead_letter.json"
        p.write_text(json.dumps({"run_id": "X", "generated_at": "Y"}), encoding="utf-8")
        report = read_dead_letter(path=p)
        assert report is not None
        assert report.is_clean

    def test_partial_entry_fields_tolerated(self, tmp_path: Path) -> None:
        """Entries with missing fields should use empty string defaults, not crash."""
        payload = _sample_payload(entries=[{"symbol": "TSLA"}])
        p = _write_payload(tmp_path, payload)
        report = read_dead_letter(path=p)
        assert report is not None
        assert report.entries[0].symbol == "TSLA"
        assert report.entries[0].stage == "unknown"  # default from get("stage", "unknown")

    def test_run_id_and_generated_at_preserved(self, tmp_path: Path) -> None:
        payload = {"run_id": "RUN-123", "generated_at": "2026-06-26T12:00:00Z", "entries": []}
        p = _write_payload(tmp_path, payload)
        report = read_dead_letter(path=p)
        assert report is not None
        assert report.run_id == "RUN-123"
        assert "2026" in report.generated_at

    def test_default_path_constant(self) -> None:
        """DEAD_LETTER_PATH should point to output/dead_letter.json under the repo root."""
        assert DEAD_LETTER_PATH.name == "dead_letter.json"
        assert DEAD_LETTER_PATH.parent.name == "output"

    def test_empty_file_returns_none(self, tmp_path: Path) -> None:
        p = tmp_path / "dead_letter.json"
        p.write_text("", encoding="utf-8")
        result = read_dead_letter(path=p)
        assert result is None
