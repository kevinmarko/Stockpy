"""Tests for ``pilots/scan_config_store.py`` — atomic local scan-config persistence."""
from __future__ import annotations

import itertools
import json
from typing import Any
from pathlib import Path

import pytest

from pilots.scan_config_store import ScanConfigStore


def _store(tmp_path: Path, clock: Any = None) -> ScanConfigStore:
    return ScanConfigStore(path=str(tmp_path / "scan_configs.json"), clock=clock, seed_defaults=False)


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
        ScanConfigStore(path=str(path), seed_defaults=False).upsert(
            "breakout", {"min_price": 5}, enabled=True
        )
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


# ---------------------------------------------------------------------------
# Seeded defaults (``seed_defaults=True``, the constructor default) —
# regression coverage for the module-level-list-frozen-at-import-time bug:
# `_DEFAULT_SCANS`' `created_at`/`updated_at` used to call `_utc_now_iso()` at
# module import time rather than at actual seed time, so every store's
# defaults carried a stale, process-start timestamp instead of the real seed
# time. `_default_scans()` is now a plain function called fresh from
# `ScanConfigStore._load()`'s seeding branch.
# ---------------------------------------------------------------------------
class TestDefaultSeeding:
    def test_fresh_store_is_seeded_with_ten_defaults(self, tmp_path):
        s = ScanConfigStore(path=str(tmp_path / "scan_configs.json"))
        rows = s.list_all()
        assert len(rows) == 10
        names = {r["name"] for r in rows}
        assert names == {
            "momentum-leaders", "trend-follower", "dip-buyer",
            "edge-and-volatility", "multifactor", "forecast-aligned",
            "news-catalyst", "risk-adjusted", "dividend-income",
            "balanced-blend",
        }
        assert all(r["enabled"] is True for r in rows)

    def test_seeding_writes_the_file_to_disk(self, tmp_path):
        path = tmp_path / "scan_configs.json"
        assert not path.exists()
        ScanConfigStore(path=str(path)).list_all()
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data["scan_configs"]) == 10

    def test_seed_defaults_false_leaves_store_empty(self, tmp_path):
        s = ScanConfigStore(path=str(tmp_path / "scan_configs.json"), seed_defaults=False)
        assert s.list_all() == []
        assert not (tmp_path / "scan_configs.json").exists()

    def test_existing_file_is_never_reseeded(self, tmp_path):
        """A store that already has a file (even with zero rows written by an
        explicit upsert-then-delete-all-style flow) must not be re-seeded --
        seeding is strictly a missing-file event."""
        path = tmp_path / "scan_configs.json"
        s = ScanConfigStore(path=str(path))
        s.upsert("custom-only", {"min_price": 1}, enabled=True)
        rows = s.list_all()
        # upsert() on a fresh (seeded) store appends after the 10 defaults.
        assert len(rows) == 11
        assert rows[-1]["name"] == "custom-only"

        # A second store instance pointed at the SAME (now-existing) file must
        # see the persisted 11 rows, not re-seed another 10 defaults on top.
        s2 = ScanConfigStore(path=str(path))
        assert len(s2.list_all()) == 11

    def test_default_timestamps_are_computed_at_seed_time_not_import_time(self, tmp_path, monkeypatch):
        """Regression test for the frozen-at-import-time bug: two stores
        seeded at two different (mocked) "now" moments must get two different
        timestamps -- a module-level list computed once at import would give
        both the same frozen value."""
        import pilots.scan_config_store as scs_mod

        monkeypatch.setattr(scs_mod, "_utc_now_iso", lambda: "2020-01-01T00:00:00+00:00")
        rows_early = ScanConfigStore(path=str(tmp_path / "a.json")).list_all()
        assert all(r["created_at"] == "2020-01-01T00:00:00+00:00" for r in rows_early)

        monkeypatch.setattr(scs_mod, "_utc_now_iso", lambda: "2030-06-15T12:00:00+00:00")
        rows_later = ScanConfigStore(path=str(tmp_path / "b.json")).list_all()
        assert all(r["created_at"] == "2030-06-15T12:00:00+00:00" for r in rows_later)
