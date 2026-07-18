"""Tests for ``pilots/agentic.py`` — output/agent_state.json read helper."""
from __future__ import annotations

import json

from pilots.agentic import agent_loop_status


class TestMissingOrCorrupt:
    def test_missing_file_returns_honest_zero_shape(self, tmp_path):
        result = agent_loop_status(path=str(tmp_path / "agent_state.json"))
        assert result["cycle_count"] == 0
        assert result["last_cycle_iso"] is None
        assert result["backlog_count"] == 0
        assert result["reason"] is not None  # never a silent zero -- always explained

    def test_corrupt_json_never_raises(self, tmp_path):
        path = tmp_path / "agent_state.json"
        path.write_text("{ not valid json", encoding="utf-8")
        result = agent_loop_status(path=str(path))
        assert result["cycle_count"] == 0
        assert result["reason"] is not None

    def test_non_object_json_treated_as_corrupt(self, tmp_path):
        path = tmp_path / "agent_state.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        result = agent_loop_status(path=str(path))
        assert result["cycle_count"] == 0
        assert result["reason"] is not None


class TestPopulated:
    def test_reads_cycle_count_and_last_cycle(self, tmp_path):
        path = tmp_path / "agent_state.json"
        path.write_text(
            json.dumps(
                {
                    "cycle_count": 12,
                    "last_cycle_iso": "2026-07-18T00:00:00+00:00",
                    "backlog": {"AAPL": {}, "MSFT": {}},
                }
            ),
            encoding="utf-8",
        )
        result = agent_loop_status(path=str(path))
        assert result == {
            "cycle_count": 12,
            "last_cycle_iso": "2026-07-18T00:00:00+00:00",
            "backlog_count": 2,
            "reason": None,
        }

    def test_empty_backlog_is_zero_not_missing(self, tmp_path):
        path = tmp_path / "agent_state.json"
        path.write_text(json.dumps({"cycle_count": 3, "backlog": {}}), encoding="utf-8")
        result = agent_loop_status(path=str(path))
        assert result["backlog_count"] == 0
        assert result["reason"] is None

    def test_missing_last_cycle_iso_is_none_not_empty_string(self, tmp_path):
        path = tmp_path / "agent_state.json"
        path.write_text(json.dumps({"cycle_count": 1, "backlog": {}}), encoding="utf-8")
        result = agent_loop_status(path=str(path))
        assert result["last_cycle_iso"] is None

    def test_malformed_backlog_type_degrades_to_zero(self, tmp_path):
        path = tmp_path / "agent_state.json"
        path.write_text(json.dumps({"cycle_count": 5, "backlog": ["not", "a", "dict"]}), encoding="utf-8")
        result = agent_loop_status(path=str(path))
        assert result["backlog_count"] == 0
        assert result["reason"] is None  # the file itself parsed fine
