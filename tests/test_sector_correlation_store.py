"""Tests for data/sector_correlation_store.py -- the durable
sector_correlations DB table backing the semantic Related Sector
Selection feature's ranking history.

Mirrors tests/test_cap_audit_store.py's conventions (in-memory SQLite for
CRUD, a tmp_path-backed file DB for readonly=True, missing-table degrade)."""
from __future__ import annotations

import pytest

from data.sector_correlation_store import (
    SectorCorrelationStore,
    _OfflineSectorCorrelationStore,
)


def _row(sector="Technology", **overrides):
    defaults = dict(
        sector=sector,
        cosine_similarity=0.65,
        ingestion_volume=42.0,
        sector_heat_factor=0.71,
        correlation_coefficient=0.4615,
        rank=1,
        selected=True,
        degraded_reason=None,
        embedder="sbert",
        pooling="max",
    )
    defaults.update(overrides)
    return defaults


class TestRecordAndGetLatest:
    def test_round_trip(self):
        store = SectorCorrelationStore(db_url="sqlite:///:memory:")
        store.record_correlations([_row()], as_of="2026-07-21", target_symbol="NIO")

        rows = store.get_latest("NIO")
        assert len(rows) == 1
        row = rows[0]
        assert row["sector"] == "Technology"
        assert row["cosine_similarity"] == pytest.approx(0.65)
        assert row["correlation_coefficient"] == pytest.approx(0.4615)
        assert row["rank"] == 1
        assert row["selected"] is True
        assert row["embedder"] == "sbert"
        assert row["pooling"] == "max"

    def test_null_fields_round_trip_as_none_not_zero(self):
        """CONSTRAINT #4: a degraded row's None fields must persist as
        NULL, never coerced to a fabricated 0.0."""
        store = SectorCorrelationStore(db_url="sqlite:///:memory:")
        store.record_correlations(
            [_row(cosine_similarity=None, correlation_coefficient=None,
                  rank=None, selected=False, degraded_reason="no_target_description")],
            as_of="2026-07-21", target_symbol="NIO",
        )
        row = store.get_latest("NIO")[0]
        assert row["cosine_similarity"] is None
        assert row["correlation_coefficient"] is None
        assert row["rank"] is None
        assert row["degraded_reason"] == "no_target_description"

    def test_empty_rows_is_a_noop(self):
        store = SectorCorrelationStore(db_url="sqlite:///:memory:")
        store.record_correlations([], as_of="2026-07-21", target_symbol="NIO")
        assert store.get_latest("NIO") == []

    def test_target_symbol_uppercased_on_write(self):
        store = SectorCorrelationStore(db_url="sqlite:///:memory:")
        store.record_correlations([_row()], as_of="2026-07-21", target_symbol="nio")
        assert store.get_latest("NIO") != []
        assert store.get_latest("nio") != []  # read side also uppercases

    def test_only_most_recent_as_of_returned(self):
        store = SectorCorrelationStore(db_url="sqlite:///:memory:")
        store.record_correlations(
            [_row(sector="A", correlation_coefficient=0.1)],
            as_of="2026-07-01", target_symbol="NIO",
        )
        store.record_correlations(
            [_row(sector="B", correlation_coefficient=0.9)],
            as_of="2026-07-21", target_symbol="NIO",
        )
        rows = store.get_latest("NIO")
        assert len(rows) == 1
        assert rows[0]["sector"] == "B"

    def test_rows_ordered_by_rank_unranked_last(self):
        store = SectorCorrelationStore(db_url="sqlite:///:memory:")
        store.record_correlations(
            [
                _row(sector="Unranked", rank=None, correlation_coefficient=None, selected=False),
                _row(sector="Second", rank=2, correlation_coefficient=0.3),
                _row(sector="First", rank=1, correlation_coefficient=0.5),
            ],
            as_of="2026-07-21", target_symbol="NIO",
        )
        rows = store.get_latest("NIO")
        assert [r["sector"] for r in rows] == ["First", "Second", "Unranked"]

    def test_different_targets_isolated(self):
        store = SectorCorrelationStore(db_url="sqlite:///:memory:")
        store.record_correlations([_row(sector="A")], as_of="2026-07-21", target_symbol="NIO")
        store.record_correlations([_row(sector="B")], as_of="2026-07-21", target_symbol="TSLA")
        assert [r["sector"] for r in store.get_latest("NIO")] == ["A"]
        assert [r["sector"] for r in store.get_latest("TSLA")] == ["B"]

    def test_no_history_returns_empty_list(self):
        store = SectorCorrelationStore(db_url="sqlite:///:memory:")
        assert store.get_latest("NIO") == []

    def test_get_latest_read_failure_returns_empty_list(self, monkeypatch):
        """CONSTRAINT #6: a read failure must never raise."""
        store = SectorCorrelationStore(db_url="sqlite:///:memory:")

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated DB failure")

        monkeypatch.setattr(store, "Session", _boom)
        assert store.get_latest("NIO") == []


class TestReadonlyMode:
    def test_readonly_store_reads_data_written_by_a_write_mode_store(self, tmp_path):
        db_url = f"sqlite:///{tmp_path / 'sector_correlations.db'}"
        writer = SectorCorrelationStore(db_url=db_url)
        writer.record_correlations([_row()], as_of="2026-07-21", target_symbol="NIO")

        reader = SectorCorrelationStore(db_url=db_url, readonly=True)
        rows = reader.get_latest("NIO")
        assert len(rows) == 1
        assert rows[0]["sector"] == "Technology"

    def test_readonly_store_write_raises_rather_than_fabricate_success(self, tmp_path):
        db_url = f"sqlite:///{tmp_path / 'sector_correlations.db'}"
        SectorCorrelationStore(db_url=db_url)  # creates the table
        reader = SectorCorrelationStore(db_url=db_url, readonly=True)
        with pytest.raises(RuntimeError):
            reader.record_correlations([_row()], as_of="2026-07-21", target_symbol="NIO")

    def test_readonly_store_degrades_to_empty_list_on_missing_table(self, tmp_path):
        db_path = tmp_path / "never_written.db"
        reader = SectorCorrelationStore(db_url=f"sqlite:///{db_path}", readonly=True)
        assert reader.get_latest("NIO") == []


class TestOfflineStandIn:
    def test_write_raises(self):
        store = _OfflineSectorCorrelationStore()
        with pytest.raises(RuntimeError):
            store.record_correlations([_row()], as_of="2026-07-21", target_symbol="NIO")

    def test_read_degrades_to_empty_list(self):
        store = _OfflineSectorCorrelationStore()
        assert store.get_latest("NIO") == []

    def test_record_correlations_mid_batch_failure_rolls_back_entire_batch(self):
        """Atomicity: if an event fails to process (e.g. KeyError), the entire batch must roll back and not persist partial data."""
        store = SectorCorrelationStore(db_url="sqlite:///:memory:")
        
        rows = [
            {"sector": "Technology", "correlation_coefficient": 0.8},
            {"missing_sector_key": True},  # This will raise KeyError
            {"sector": "Healthcare", "correlation_coefficient": 0.5}
        ]
        
        with pytest.raises(KeyError):
            store.record_correlations(rows, as_of="2026-07-21", target_symbol="NIO")
            
        # The whole batch must have aborted
        assert store.get_latest("NIO") == []
