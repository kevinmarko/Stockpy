"""Tests for ``pilots/follows_store.py`` — atomic local follow persistence."""
from __future__ import annotations

import itertools
import json

import pytest

from pilots.follows_store import (
    STATUS_ACTIVE,
    STATUS_CANCELLED,
    FollowsStore,
)


def _store(tmp_path, clock=None):
    return FollowsStore(path=str(tmp_path / "follows.json"), clock=clock)


# ---------------------------------------------------------------------------
# Empty / missing / corrupt file resilience
# ---------------------------------------------------------------------------
class TestReadResilience:
    def test_missing_file_is_empty(self, tmp_path):
        s = _store(tmp_path)
        assert s.list_all() == []
        assert s.list_active() == []
        assert s.get("anything") is None
        assert s.aum_proxy() == 0.0
        assert s.followers_proxy() == 0

    def test_corrupt_file_treated_as_empty(self, tmp_path):
        path = tmp_path / "follows.json"
        path.write_text("{ this is not json", encoding="utf-8")
        s = FollowsStore(path=str(path))
        assert s.list_all() == []  # never raises

    def test_non_object_json_treated_as_empty(self, tmp_path):
        path = tmp_path / "follows.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        s = FollowsStore(path=str(path))
        assert s.list_all() == []


# ---------------------------------------------------------------------------
# upsert / cancel / remove
# ---------------------------------------------------------------------------
class TestUpsert:
    def test_create_new_follow(self, tmp_path):
        s = _store(tmp_path)
        row = s.upsert("trend-following", 500.0)
        assert row["pilot_id"] == "trend-following"
        assert row["amount"] == 500.0
        assert row["status"] == STATUS_ACTIVE
        assert row["created_at"] == row["updated_at"]
        assert len(s.list_active()) == 1

    def test_update_existing_preserves_created_at(self, tmp_path):
        clock = itertools.count()
        s = _store(tmp_path, clock=lambda: f"t{next(clock)}")
        first = s.upsert("dip-buyer", 100.0)
        second = s.upsert("dip-buyer", 250.0)
        assert first["created_at"] == "t0"
        assert second["created_at"] == "t0"  # preserved across update
        assert second["updated_at"] == "t1"  # clock called once per upsert -> bumped
        assert second["amount"] == 250.0
        # Still exactly one row.
        assert len(s.list_all()) == 1

    def test_amount_zero_cancels(self, tmp_path):
        s = _store(tmp_path)
        s.upsert("macd-trend", 300.0)
        cancelled = s.upsert("macd-trend", 0.0)
        assert cancelled["status"] == STATUS_CANCELLED
        assert cancelled["amount"] == 0.0
        # Row retained, but no longer active.
        assert len(s.list_all()) == 1
        assert s.list_active() == []
        assert s.get("macd-trend")["status"] == STATUS_CANCELLED

    def test_negative_amount_rejected(self, tmp_path):
        s = _store(tmp_path)
        with pytest.raises(ValueError):
            s.upsert("x", -5.0)

    def test_empty_pilot_id_rejected(self, tmp_path):
        s = _store(tmp_path)
        with pytest.raises(ValueError):
            s.upsert("", 100.0)

    def test_remove(self, tmp_path):
        s = _store(tmp_path)
        s.upsert("multifactor", 10.0)
        assert s.remove("multifactor") is True
        assert s.get("multifactor") is None
        assert s.remove("multifactor") is False  # already gone


# ---------------------------------------------------------------------------
# Roundtrip / persistence / atomicity
# ---------------------------------------------------------------------------
class TestRoundtrip:
    def test_persisted_across_instances(self, tmp_path):
        path = str(tmp_path / "follows.json")
        FollowsStore(path=path).upsert("trend-following", 400.0)
        reloaded = FollowsStore(path=path)
        row = reloaded.get("trend-following")
        assert row is not None
        assert row["amount"] == 400.0

    def test_on_disk_schema(self, tmp_path):
        path = tmp_path / "follows.json"
        FollowsStore(path=str(path)).upsert("dip-buyer", 50.0)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["version"] == 1
        assert isinstance(data["follows"], list)
        assert data["follows"][0]["pilot_id"] == "dip-buyer"
        assert set(data["follows"][0]) == {
            "pilot_id",
            "amount",
            "created_at",
            "updated_at",
            "status",
        }

    def test_atomic_no_tmp_left_behind(self, tmp_path):
        s = _store(tmp_path)
        s.upsert("a", 1.0)
        s.upsert("b", 2.0)
        leftovers = list(tmp_path.glob("*.tmp"))
        assert leftovers == []
        assert (tmp_path / "follows.json").exists()

    def test_creates_parent_dir(self, tmp_path):
        nested = tmp_path / "deep" / "nested" / "follows.json"
        s = FollowsStore(path=str(nested))
        s.upsert("a", 1.0)
        assert nested.exists()


# ---------------------------------------------------------------------------
# AUM / followers proxies
# ---------------------------------------------------------------------------
class TestProxies:
    def test_aum_and_followers(self, tmp_path):
        s = _store(tmp_path)
        s.upsert("trend-following", 500.0)
        s.upsert("dip-buyer", 250.0)
        s.upsert("macd-trend", 0.0)  # cancelled — excluded
        assert s.aum_proxy() == 750.0
        assert s.followers_proxy() == 2

    def test_per_pilot_proxies(self, tmp_path):
        s = _store(tmp_path)
        s.upsert("trend-following", 500.0)
        s.upsert("dip-buyer", 250.0)
        assert s.aum_for("trend-following") == 500.0
        assert s.followers_for("trend-following") == 1
        assert s.aum_for("dip-buyer") == 250.0
        assert s.followers_for("nobody") == 0.0 or s.followers_for("nobody") == 0

    def test_cancelled_excluded_from_proxies(self, tmp_path):
        s = _store(tmp_path)
        s.upsert("trend-following", 500.0)
        s.upsert("trend-following", 0.0)  # cancel it
        assert s.aum_proxy() == 0.0
        assert s.followers_proxy() == 0
        assert s.aum_for("trend-following") == 0.0
        assert s.followers_for("trend-following") == 0
