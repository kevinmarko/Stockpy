"""Tests for ``pilots/discovery.py`` — output/scan_candidates.json read helper."""
from __future__ import annotations

import json

from pilots.discovery import discovery
from pilots.scan_config_store import ScanConfigStore


def _candidates_path(tmp_path):
    return str(tmp_path / "scan_candidates.json")


def _configs_path(tmp_path):
    return str(tmp_path / "scan_configs.json")


class TestColdStart:
    def test_no_artifact_no_configs(self, tmp_path):
        result = discovery(
            candidates_path=_candidates_path(tmp_path),
            scan_config_path=_configs_path(tmp_path),
        )
        assert result["generated_at"] is None
        assert result["candidates"] == []
        assert result["scan_configs"] == []
        assert result["reason"] is not None

    def test_no_artifact_but_configs_exist_reflects_them(self, tmp_path):
        ScanConfigStore(path=_configs_path(tmp_path)).upsert(
            "breakout", {"min_price": 5}, enabled=True
        )
        result = discovery(
            candidates_path=_candidates_path(tmp_path),
            scan_config_path=_configs_path(tmp_path),
        )
        assert result["candidates"] == []
        assert len(result["scan_configs"]) == 1
        assert result["scan_configs"][0]["name"] == "breakout"
        assert result["reason"] is not None

    def test_corrupt_candidates_file_never_raises(self, tmp_path):
        path = tmp_path / "scan_candidates.json"
        path.write_text("{ not valid json", encoding="utf-8")
        result = discovery(
            candidates_path=str(path), scan_config_path=_configs_path(tmp_path)
        )
        assert result["candidates"] == []
        assert result["reason"] is not None


class TestPopulated:
    def _write_candidates(self, tmp_path, candidates, generated_at="2026-07-18T00:00:00+00:00"):
        path = tmp_path / "scan_candidates.json"
        path.write_text(
            json.dumps({"generated_at": generated_at, "candidates": candidates}),
            encoding="utf-8",
        )
        return str(path)

    def test_scored_and_unscored_candidates_both_surface(self, tmp_path):
        path = self._write_candidates(
            tmp_path,
            [
                {
                    "symbol": "nvda",
                    "scan_name": "breakout",
                    "scan_reason": "RSI 58",
                    "action": "BUY",
                    "conviction": 0.71,
                    "discovered_at": "2026-07-18T00:00:00+00:00",
                },
                {
                    "symbol": "PLTR",
                    "scan_name": "breakout",
                    "action": None,
                    "conviction": None,
                },
            ],
        )
        result = discovery(candidates_path=path, scan_config_path=_configs_path(tmp_path))
        assert result["generated_at"] == "2026-07-18T00:00:00+00:00"
        assert result["reason"] is None
        symbols = {c["symbol"]: c for c in result["candidates"]}
        assert symbols["NVDA"]["action"] == "BUY"  # upper-cased
        assert symbols["NVDA"]["conviction"] == 0.71
        assert symbols["PLTR"]["action"] is None  # never fabricated
        assert symbols["PLTR"]["conviction"] is None

    def test_garbage_rows_filtered_not_crashed_on(self, tmp_path):
        path = self._write_candidates(
            tmp_path,
            [
                {"symbol": "AAPL", "action": "BUY"},
                {"no_symbol": True},
                "not even a dict",
                {"symbol": "", "action": "SELL"},  # empty symbol -> dropped
            ],
        )
        result = discovery(candidates_path=path, scan_config_path=_configs_path(tmp_path))
        assert [c["symbol"] for c in result["candidates"]] == ["AAPL"]

    def test_non_numeric_conviction_degrades_to_none(self, tmp_path):
        path = self._write_candidates(
            tmp_path, [{"symbol": "AAPL", "action": "BUY", "conviction": "not-a-number"}]
        )
        result = discovery(candidates_path=path, scan_config_path=_configs_path(tmp_path))
        assert result["candidates"][0]["conviction"] is None

    def test_limit_caps_candidate_count(self, tmp_path):
        rows = [{"symbol": f"SYM{i}", "action": "BUY"} for i in range(5)]
        path = self._write_candidates(tmp_path, rows)
        result = discovery(
            limit=2, candidates_path=path, scan_config_path=_configs_path(tmp_path)
        )
        assert len(result["candidates"]) == 2

    def test_default_limit_reads_from_settings(self, tmp_path, monkeypatch):
        from settings import settings

        monkeypatch.setattr(settings, "AGENTIC_MAX_CANDIDATES", 1)
        rows = [{"symbol": "AAPL", "action": "BUY"}, {"symbol": "MSFT", "action": "SELL"}]
        path = self._write_candidates(tmp_path, rows)
        result = discovery(candidates_path=path, scan_config_path=_configs_path(tmp_path))
        assert len(result["candidates"]) == 1

    def test_empty_candidates_list_gets_honest_reason(self, tmp_path):
        path = self._write_candidates(tmp_path, [])
        result = discovery(candidates_path=path, scan_config_path=_configs_path(tmp_path))
        assert result["candidates"] == []
        assert result["reason"] is not None
