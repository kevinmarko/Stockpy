"""Tests for ``pilots/scan_config_store.py`` — atomic local scan-config persistence."""
from __future__ import annotations

import itertools
import json

import pytest

from pilots.scan_config_store import ScanConfigStore


def _store(tmp_path, clock=None):
    return ScanConfigStore(path=str(tmp_path / "scan_configs.json"), clock=clock)


# ---------------------------------------------------------------------------
# Empty / missing / corrupt file resilience
# ---------------------------------------------------------------------------
class TestReadResilience:
    def test_missing_file_is_empty(self, tmp_path):
        s = _store(tmp_path)
        assert s.list_all() == []
        assert s.list_enabled() == []
        assert s.get("anything") is None

    def test_corrupt_file_treated_as_empty(self, tmp_path):
        path = tmp_path / "scan_configs.json"
        path.write_text("{ this is not json", encoding="utf-8")
        s = ScanConfigStore(path=str(path))
        assert s.list_all() == []  # never raises

    def test_non_object_json_treated_as_empty(self, tmp_path):
        path = tmp_path / "scan_configs.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        s = ScanConfigStore(path=str(path))
        assert s.list_all() == []


# ---------------------------------------------------------------------------
# upsert / remove
# ---------------------------------------------------------------------------
class TestUpsert:
    def test_create_new_config(self, tmp_path):
        s = _store(tmp_path)
        row = s.upsert("high_momentum_breakout", {"min_price": 5}, enabled=True)
        assert row["name"] == "high_momentum_breakout"
        assert row["filters"] == {"min_price": 5}
        assert row["enabled"] is True
        assert row["created_at"] == row["updated_at"]
        assert len(s.list_all()) == 1

    def test_update_existing_preserves_created_at(self, tmp_path):
        clock = itertools.count()
        s = _store(tmp_path, clock=lambda: f"t{next(clock)}")
        first = s.upsert("breakout", {"min_price": 5}, enabled=True)
        second = s.upsert("breakout", {"min_price": 10}, enabled=False)
        assert first["created_at"] == "t0"
        assert second["created_at"] == "t0"  # preserved across update
        assert second["updated_at"] == "t1"
        assert second["filters"] == {"min_price": 10}
        assert second["enabled"] is False
        # Still exactly one row.
        assert len(s.list_all()) == 1

    def test_filters_stored_verbatim_not_validated(self, tmp_path):
        s = _store(tmp_path)
        # This store has no knowledge of the scanner's filter schema -- any
        # JSON-safe dict passes through untouched (never fabricated/coerced).
        row = s.upsert("weird", {"nonsense_key": "whatever", "n": 3.5}, enabled=True)
        assert row["filters"] == {"nonsense_key": "whatever", "n": 3.5}

    def test_empty_name_rejected(self, tmp_path):
        s = _store(tmp_path)
        with pytest.raises(ValueError):
            s.upsert("", {}, enabled=True)

    def test_whitespace_only_name_rejected(self, tmp_path):
        s = _store(tmp_path)
        with pytest.raises(ValueError):
            s.upsert("   ", {}, enabled=True)

    def test_remove(self, tmp_path):
        s = _store(tmp_path)
        s.upsert("breakout", {}, enabled=True)
        assert s.remove("breakout") is True
        assert s.get("breakout") is None
        assert s.remove("breakout") is False  # already gone


# ---------------------------------------------------------------------------
# list_enabled
# ---------------------------------------------------------------------------
class TestListEnabled:
    def test_only_enabled_rows_returned(self, tmp_path):
        s = _store(tmp_path)
        s.upsert("a", {}, enabled=True)
        s.upsert("b", {}, enabled=False)
        s.upsert("c", {}, enabled=True)
        names = {c["name"] for c in s.list_enabled()}
        assert names == {"a", "c"}
        assert len(s.list_all()) == 3


# ---------------------------------------------------------------------------
# Roundtrip / persistence / atomicity
# ---------------------------------------------------------------------------
class TestRoundtrip:
    def test_persisted_across_instances(self, tmp_path):
        path = str(tmp_path / "scan_configs.json")
        ScanConfigStore(path=path).upsert("breakout", {"min_price": 5}, enabled=True)
        reloaded = ScanConfigStore(path=path)
        row = reloaded.get("breakout")
        assert row is not None
        assert row["filters"] == {"min_price": 5}

    def test_on_disk_schema(self, tmp_path):
        path = tmp_path / "scan_configs.json"
        ScanConfigStore(path=str(path)).upsert("breakout", {"min_price": 5}, enabled=True)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["version"] == 1
        assert isinstance(data["scan_configs"], list)
        assert data["scan_configs"][0]["name"] == "breakout"
        assert set(data["scan_configs"][0]) == {
            "name", "filters", "enabled", "created_at", "updated_at",
        }

    def test_atomic_no_tmp_left_behind(self, tmp_path):
        s = _store(tmp_path)
        s.upsert("a", {}, enabled=True)
        s.upsert("b", {}, enabled=True)
        leftovers = list(tmp_path.glob("*.tmp"))
        assert leftovers == []
        assert (tmp_path / "scan_configs.json").exists()

    def test_creates_parent_dir(self, tmp_path):
        nested = tmp_path / "deep" / "nested" / "scan_configs.json"
        s = ScanConfigStore(path=str(nested))
        s.upsert("a", {}, enabled=True)
        assert nested.exists()
