"""Tests for pilots/trade_history.py -- the durable, paginated broker
closed-trade history reader backing GET /portfolio/trade-history.

Covers: pagination, symbol filter, summary computed over the FULL filtered
set (not just the page), cold-store -> available=false, NaN->null, and that
it never raises even when the store is broken."""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from data.broker_fills_store import BrokerFillsStore
from data.robinhood_orders import OrderFill
from pilots.trade_history import trade_history_view


def _fill(symbol, side, qty, price, order_id, ts):
    return OrderFill(symbol=symbol, side=side, quantity=qty, price=price, timestamp=ts, order_id=order_id)


def _seed(db_url, monkeypatch):
    import data.broker_fills_store as bfs

    monkeypatch.setattr(bfs, "resolve_database_url", lambda: db_url)
    store = BrokerFillsStore(db_url=db_url)
    store.record_fills([
        _fill("AAPL", "buy", 10, 100, "a1", datetime(2026, 1, 1, tzinfo=timezone.utc)),
        _fill("AAPL", "sell", 10, 120, "a2", datetime(2026, 1, 5, tzinfo=timezone.utc)),
        _fill("MSFT", "buy", 5, 200, "m1", datetime(2026, 2, 1, tzinfo=timezone.utc)),
        _fill("MSFT", "sell", 5, 190, "m2", datetime(2026, 2, 5, tzinfo=timezone.utc)),
    ])
    return store


class TestTradeHistoryView:
    def test_cold_store_returns_honest_empty_view(self, tmp_path, monkeypatch):
        import data.broker_fills_store as bfs

        db_url = f"sqlite:///{tmp_path}/cold.db"
        BrokerFillsStore(db_url=db_url)  # create schema, no fills
        monkeypatch.setattr(bfs, "resolve_database_url", lambda: db_url)

        result = trade_history_view()
        assert result["available"] is False
        assert result["trades"] == []
        assert result["total"] == 0
        assert result["source"] == "durable_store"
        assert result["last_ingested_at"] is None
        assert result["summary"]["n_trades"] == 0
        assert result["summary"]["win_rate"] is None  # NaN -> null, never 0.0

    def test_available_and_populated_after_ingest(self, tmp_path, monkeypatch):
        _seed(f"sqlite:///{tmp_path}/pop.db", monkeypatch)
        result = trade_history_view()
        assert result["available"] is True
        assert result["total"] == 2
        assert len(result["trades"]) == 2
        assert set(result["symbols"]) == {"AAPL", "MSFT"}
        assert result["last_ingested_at"] is not None

    def test_pagination(self, tmp_path, monkeypatch):
        _seed(f"sqlite:///{tmp_path}/page.db", monkeypatch)
        page1 = trade_history_view(limit=1, offset=0)
        page2 = trade_history_view(limit=1, offset=1)
        assert page1["total"] == 2
        assert page2["total"] == 2
        assert len(page1["trades"]) == 1
        assert len(page2["trades"]) == 1
        assert page1["trades"][0]["symbol"] != page2["trades"][0]["symbol"]
        # Newest exit first.
        assert page1["trades"][0]["symbol"] == "MSFT"

    def test_summary_is_over_full_filtered_set_not_just_page(self, tmp_path, monkeypatch):
        _seed(f"sqlite:///{tmp_path}/summary.db", monkeypatch)
        full = trade_history_view(limit=50)
        page = trade_history_view(limit=1, offset=0)
        assert page["summary"] == full["summary"]  # identical regardless of page size

    def test_symbol_filter(self, tmp_path, monkeypatch):
        _seed(f"sqlite:///{tmp_path}/filter.db", monkeypatch)
        result = trade_history_view(symbol="aapl")
        assert result["total"] == 1
        assert result["trades"][0]["symbol"] == "AAPL"
        # `symbols` (the filter control's own option list) is unaffected by
        # the active filter -- always every distinct symbol in the store.
        assert set(result["symbols"]) == {"AAPL", "MSFT"}

    def test_limit_clamped_to_max_page_size(self, tmp_path, monkeypatch):
        _seed(f"sqlite:///{tmp_path}/clamp.db", monkeypatch)
        result = trade_history_view(limit=999999)
        assert result["limit"] <= 500

    def test_negative_offset_clamped_to_zero(self, tmp_path, monkeypatch):
        _seed(f"sqlite:///{tmp_path}/neg.db", monkeypatch)
        result = trade_history_view(offset=-5)
        assert result["offset"] == 0

    def test_store_construction_failure_degrades_to_empty_view(self, monkeypatch):
        import data.broker_fills_store as bfs

        def _boom(*a, **kw):
            raise RuntimeError("db unreachable")

        monkeypatch.setattr(bfs, "BrokerFillsStore", _boom)
        result = trade_history_view()  # must not raise
        assert result["available"] is False
        assert result["trades"] == []

    def test_no_nan_values_ever_serialized(self, tmp_path, monkeypatch):
        """A profit_factor of NaN (no losing trades) must be None, not the
        float nan (which json.dumps would reject) and not a fabricated 0.0."""
        import data.broker_fills_store as bfs

        db_url = f"sqlite:///{tmp_path}/nan.db"
        monkeypatch.setattr(bfs, "resolve_database_url", lambda: db_url)
        store = BrokerFillsStore(db_url=db_url)
        store.record_fills([
            _fill("AAPL", "buy", 1, 100, "a1", datetime(2026, 1, 1, tzinfo=timezone.utc)),
            _fill("AAPL", "sell", 1, 110, "a2", datetime(2026, 1, 2, tzinfo=timezone.utc)),
        ])
        result = trade_history_view()
        for v in result["summary"].values():
            if isinstance(v, float):
                assert not math.isnan(v)
        assert result["summary"]["profit_factor"] is None
