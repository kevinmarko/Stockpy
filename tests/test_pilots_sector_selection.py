"""Tests for pilots/sector_selection.py -- the Sector Selection ranking
reader powering GET /sector/selection."""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from data.sector_correlation_store import SectorCorrelationStore
from pilots.sector_selection import sector_selection_view


def _row(sector, rank, coefficient, **overrides):
    defaults = dict(
        sector=sector,
        cosine_similarity=0.6,
        ingestion_volume=10.0,
        sector_heat_factor=0.7,
        correlation_coefficient=coefficient,
        rank=rank,
        selected=rank is not None and rank <= 3,
        degraded_reason=None,
        embedder="sbert",
        pooling="max",
    )
    defaults.update(overrides)
    return defaults


class TestEmptyAndColdStart:
    def test_blank_target_short_circuits(self):
        result = sector_selection_view("", 3)
        assert result["rows"] == []
        assert result["reason"] is not None

    def test_no_history_returns_reason_not_error(self):
        store = SectorCorrelationStore(db_url="sqlite:///:memory:")
        result = sector_selection_view("NIO", 3, store=store)
        assert result["target_symbol"] == "NIO"
        assert result["rows"] == []
        assert result["as_of"] is None
        assert result["reason"] is not None

    def test_store_construction_failure_degrades_to_empty(self, monkeypatch):
        """CONSTRAINT #6: a DB-unreachable failure must never raise -- and
        must not silently fabricate a 'no data' answer that hides a real
        outage from the reason field's absence... it still gets an honest
        reason, just the generic 'nothing computed' one at this layer."""
        import data.sector_correlation_store as store_module

        def _boom(*args, **kwargs):
            raise RuntimeError("db unreachable")

        monkeypatch.setattr(store_module, "SectorCorrelationStore", _boom)
        result = sector_selection_view("NIO", 3)
        assert result["rows"] == []
        assert result["reason"] is not None


class TestRankingView:
    def test_round_trip_full_shape(self):
        store = SectorCorrelationStore(db_url="sqlite:///:memory:")
        store.record_correlations(
            [_row("Technology", 1, 0.5), _row("Healthcare", 2, 0.3)],
            as_of="2026-07-21", target_symbol="NIO",
        )
        result = sector_selection_view("NIO", 3, store=store)
        assert result["target_symbol"] == "NIO"
        assert result["as_of"] == "2026-07-21"
        assert result["top_n"] == 3
        assert result["reason"] is None
        assert result["embedder"] == "sbert"
        assert result["pooling"] == "max"
        assert [r["sector"] for r in result["rows"]] == ["Technology", "Healthcare"]

    def test_lowercase_target_uppercased(self):
        store = SectorCorrelationStore(db_url="sqlite:///:memory:")
        store.record_correlations([_row("Technology", 1, 0.5)], as_of="2026-07-21", target_symbol="NIO")
        result = sector_selection_view("nio", 3, store=store)
        assert result["target_symbol"] == "NIO"
        assert result["rows"] != []

    def test_null_fields_pass_through_as_none_not_zero(self):
        """CONSTRAINT #4: a degraded row's None fields must render as null
        in the view, never coerced to a fabricated 0.0."""
        store = SectorCorrelationStore(db_url="sqlite:///:memory:")
        store.record_correlations(
            [_row("Technology", None, None, cosine_similarity=None, selected=False,
                  degraded_reason="no_target_description")],
            as_of="2026-07-21", target_symbol="NIO",
        )
        row = sector_selection_view("NIO", 3, store=store)["rows"][0]
        assert row["cosine_similarity"] is None
        assert row["correlation_coefficient"] is None
        assert row["rank"] is None
        assert row["selected"] is False
        assert row["degraded_reason"] == "no_target_description"


class TestNSliderReRanking:
    """The whole point of the view layer: changing n re-derives `selected`
    from the already-persisted rank ordering, without touching similarity
    or heat computation at all."""

    def _seeded_store(self):
        store = SectorCorrelationStore(db_url="sqlite:///:memory:")
        store.record_correlations(
            [
                _row("First", 1, 0.9),
                _row("Second", 2, 0.7),
                _row("Third", 3, 0.5),
                _row("Fourth", 4, 0.3),
            ],
            as_of="2026-07-21", target_symbol="NIO",
        )
        return store

    def test_n_equals_2_selects_only_top_two(self):
        result = sector_selection_view("NIO", 2, store=self._seeded_store())
        selected = {r["sector"]: r["selected"] for r in result["rows"]}
        assert selected == {"First": True, "Second": True, "Third": False, "Fourth": False}

    def test_n_equals_4_selects_all(self):
        result = sector_selection_view("NIO", 4, store=self._seeded_store())
        assert all(r["selected"] for r in result["rows"])

    def test_n_equals_1_selects_only_top_one(self):
        result = sector_selection_view("NIO", 1, store=self._seeded_store())
        selected = {r["sector"]: r["selected"] for r in result["rows"]}
        assert selected["First"] is True
        assert selected["Second"] is False

    def test_unranked_row_never_selected_regardless_of_n(self):
        store = SectorCorrelationStore(db_url="sqlite:///:memory:")
        store.record_correlations(
            [_row("Unranked", None, None, selected=False, degraded_reason="no_volume_observed")],
            as_of="2026-07-21", target_symbol="NIO",
        )
        result = sector_selection_view("NIO", 5, store=store)
        assert result["rows"][0]["selected"] is False

    def test_reported_top_n_reflects_the_request_not_the_stored_value(self):
        result = sector_selection_view("NIO", 2, store=self._seeded_store())
        assert result["top_n"] == 2


class TestReadFailureResilience:
    def test_get_latest_exception_degrades_to_empty(self):
        store = MagicMock()
        store.get_latest.side_effect = RuntimeError("simulated failure")
        result = sector_selection_view("NIO", 3, store=store)
        assert result["rows"] == []
        assert result["reason"] is not None


# ─────────────────────────────────────────────────────────────────────────
# pe/change_pct -- bulk-attached FMP sector-valuation-snapshot decoration
# (data/historical_store.py::get_sector_snapshots), unrelated to the
# semantic similarity ranking above. Module 5.1.
# ─────────────────────────────────────────────────────────────────────────

class TestSectorValuationSnapshot:
    def _seeded_store(self, sectors=("Technology",)):
        store = SectorCorrelationStore(db_url="sqlite:///:memory:")
        store.record_correlations(
            [_row(sector, i + 1, 0.9 - i * 0.1) for i, sector in enumerate(sectors)],
            as_of="2026-07-21", target_symbol="NIO",
        )
        return store

    def test_pe_and_change_pct_populated_from_snapshot(self, monkeypatch):
        mock_hs = MagicMock()
        mock_hs.get_sector_snapshots.return_value = {
            "Technology": {
                "sector": "Technology", "date": "2026-08-02",
                "pe": 28.5, "change_pct": 0.012,
            },
        }
        monkeypatch.setattr("data.historical_store.HistoricalStore", lambda *a, **k: mock_hs)

        result = sector_selection_view("NIO", 3, store=self._seeded_store())
        row = result["rows"][0]
        assert row["sector"] == "Technology"
        assert row["pe"] == pytest.approx(28.5)
        assert row["change_pct"] == pytest.approx(0.012)

    def test_bulk_fetch_is_called_exactly_once_not_per_row(self, monkeypatch):
        mock_hs = MagicMock()
        mock_hs.get_sector_snapshots.return_value = {}
        monkeypatch.setattr("data.historical_store.HistoricalStore", lambda *a, **k: mock_hs)

        sector_selection_view(
            "NIO", 3, store=self._seeded_store(("Technology", "Energy", "Financials")),
        )
        mock_hs.get_sector_snapshots.assert_called_once()
        assert mock_hs.get_sector_snapshots.call_args.kwargs["as_of"] == date.today().isoformat()

    def test_sector_missing_from_snapshot_gets_none_never_a_neighboring_value(self, monkeypatch):
        mock_hs = MagicMock()
        # Only "Energy" is covered by the snapshot -- "Technology" (this
        # row's sector) must NOT borrow Energy's numbers.
        mock_hs.get_sector_snapshots.return_value = {
            "Energy": {"sector": "Energy", "date": "2026-08-02", "pe": 11.0, "change_pct": -0.02},
        }
        monkeypatch.setattr("data.historical_store.HistoricalStore", lambda *a, **k: mock_hs)

        result = sector_selection_view("NIO", 3, store=self._seeded_store(("Technology",)))
        row = result["rows"][0]
        assert row["pe"] is None
        assert row["change_pct"] is None

    def test_snapshot_table_empty_gets_none_for_every_row(self, monkeypatch):
        """The realistic default state: FMP_SECTOR_SNAPSHOT_ENABLED is off
        elsewhere, so the table is empty and get_sector_snapshots() returns
        {} -- every row's pe/change_pct honestly nulls, the similarity
        ranking is completely unaffected."""
        mock_hs = MagicMock()
        mock_hs.get_sector_snapshots.return_value = {}
        monkeypatch.setattr("data.historical_store.HistoricalStore", lambda *a, **k: mock_hs)

        result = sector_selection_view(
            "NIO", 3, store=self._seeded_store(("Technology", "Energy")),
        )
        assert result["reason"] is None
        for row in result["rows"]:
            assert row["pe"] is None
            assert row["change_pct"] is None

    def test_bulk_fetch_failure_degrades_both_fields_to_none_never_crashes(self, monkeypatch):
        def _boom(*args, **kwargs):
            raise RuntimeError("db unreachable")

        monkeypatch.setattr("data.historical_store.HistoricalStore", _boom)

        result = sector_selection_view(
            "NIO", 3, store=self._seeded_store(("Technology", "Energy")),
        )
        # CONSTRAINT #6: the similarity ranking itself is completely
        # unaffected by a HistoricalStore failure -- only pe/change_pct null.
        assert result["reason"] is None
        assert len(result["rows"]) == 2
        for row in result["rows"]:
            assert row["pe"] is None
            assert row["change_pct"] is None

    def test_historical_store_import_failure_degrades_gracefully(self, monkeypatch):
        """A totally broken import path (e.g. data.historical_store itself
        fails to import) must not crash the similarity ranking either."""
        import builtins

        real_import = builtins.__import__

        def _broken_import(name, *args, **kwargs):
            if name == "data.historical_store":
                raise ImportError("simulated broken module")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _broken_import)
        result = sector_selection_view("NIO", 3, store=self._seeded_store())
        assert result["rows"][0]["pe"] is None
        assert result["rows"][0]["change_pct"] is None
