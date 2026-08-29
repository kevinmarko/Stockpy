"""Tests for data/broker_fills_store.py -- the durable broker_order_fills DB
table backing pilots/trade_history.py and universe retention.

Covers: idempotent re-ingest, empty order_id handling, divergent-value
convergence, pagination/filtering, read-degrade-to-empty, the
recently_closed_symbols retention boundary, and the sizing-isolation AST
guards (this module must never import transactions_store, and nothing under
sizing/ or execution/ may import it)."""

from __future__ import annotations

import ast
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from data.broker_fills_store import (
    BrokerFillsStore,
    ingest_filled_orders,
    recently_closed_symbols,
)
from data.robinhood_orders import OrderFill


def _fill(symbol="AAPL", side="buy", qty=10.0, price=100.0, ts=None, order_id="ord-1") -> OrderFill:
    return OrderFill(
        symbol=symbol,
        side=side,
        quantity=qty,
        price=price,
        timestamp=ts or datetime(2026, 1, 1, tzinfo=timezone.utc),
        order_id=order_id,
    )


# ---------------------------------------------------------------------------
# record_fills: idempotency, empty order_id, divergence
# ---------------------------------------------------------------------------


class TestRecordFills:
    def test_insert_and_count(self):
        store = BrokerFillsStore(db_url="sqlite:///:memory:")
        counts = store.record_fills([_fill(order_id="a"), _fill(order_id="b", side="sell")])
        assert counts["inserted"] == 2
        assert counts["updated"] == 0
        assert counts["skipped_no_order_id"] == 0
        assert counts["divergent"] == 0
        assert len(store.all_fills()) == 2

    def test_reingesting_identical_fills_is_a_pure_noop(self):
        """The whole point of persisting by order_id: re-running ingest must
        never double-insert, or every downstream reader double-counts P&L."""
        store = BrokerFillsStore(db_url="sqlite:///:memory:")
        fills = [
            _fill(order_id="a", side="buy", qty=10, price=100.0),
            _fill(order_id="b", side="sell", qty=10, price=120.0, ts=datetime(2026, 1, 5, tzinfo=timezone.utc)),
        ]
        first = store.record_fills(fills)
        assert first["inserted"] == 2

        second = store.record_fills(fills)
        assert second["inserted"] == 0
        assert second["updated"] == 0
        assert second["divergent"] == 0

        all_fills = store.all_fills()
        assert len(all_fills) == 2  # not 4

        trades = store.closed_trades()
        assert len(trades) == 1
        assert trades[0].realized_pnl == pytest.approx(200.0)  # (120-100)*10, not doubled

    def test_empty_order_id_skipped_not_fabricated(self):
        store = BrokerFillsStore(db_url="sqlite:///:memory:")
        counts = store.record_fills([_fill(order_id=""), _fill(order_id="   ")])
        assert counts["skipped_no_order_id"] == 2
        assert counts["inserted"] == 0
        assert store.all_fills() == []

    def test_no_order_id_attribute_never_raises(self):
        """A fill whose order_id is None (defensive -- OrderFill.order_id is
        typed str, but a caller-supplied duck-typed object shouldn't crash
        the ingest)."""
        store = BrokerFillsStore(db_url="sqlite:///:memory:")

        class _Fill:
            symbol = "AAPL"
            side = "buy"
            quantity = 1.0
            price = 1.0
            timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
            order_id = None

        counts = store.record_fills([_Fill()])
        assert counts["skipped_no_order_id"] == 1

    def test_divergent_refetch_keeps_latest_and_logs(self, caplog):
        store = BrokerFillsStore(db_url="sqlite:///:memory:")
        store.record_fills([_fill(order_id="a", qty=10, price=100.0)])

        with caplog.at_level("WARNING"):
            counts = store.record_fills([_fill(order_id="a", qty=12, price=101.0)])
        assert counts["divergent"] == 1
        assert counts["updated"] == 1
        assert counts["inserted"] == 0
        assert any("diverged" in r.message for r in caplog.records)

        fills = store.all_fills()
        assert len(fills) == 1
        assert fills[0].quantity == pytest.approx(12.0)
        assert fills[0].price == pytest.approx(101.0)

    def test_write_raises_on_readonly_store(self, tmp_path):
        db_url = f"sqlite:///{tmp_path}/ro1.db"
        BrokerFillsStore(db_url=db_url)  # create the schema first
        store = BrokerFillsStore(db_url=db_url, readonly=True)
        with pytest.raises(RuntimeError):
            store.record_fills([_fill()])

    def test_write_raises_on_readonly_instrument_symbols(self, tmp_path):
        db_url = f"sqlite:///{tmp_path}/ro2.db"
        BrokerFillsStore(db_url=db_url)  # create the schema first
        store = BrokerFillsStore(db_url=db_url, readonly=True)
        with pytest.raises(RuntimeError):
            store.record_instrument_symbols({"url": "AAPL"})

    def test_record_fills_empty_list_is_noop(self):
        store = BrokerFillsStore(db_url="sqlite:///:memory:")
        counts = store.record_fills([])
        assert counts == {"inserted": 0, "updated": 0, "skipped_no_order_id": 0, "divergent": 0}


# ---------------------------------------------------------------------------
# closed_trades: pagination / filtering / read-degrade
# ---------------------------------------------------------------------------


class TestClosedTrades:
    def test_pagination_and_symbol_filter(self):
        store = BrokerFillsStore(db_url="sqlite:///:memory:")
        store.record_fills([
            _fill(symbol="AAPL", side="buy", qty=10, price=100, order_id="a1", ts=datetime(2026, 1, 1, tzinfo=timezone.utc)),
            _fill(symbol="AAPL", side="sell", qty=10, price=110, order_id="a2", ts=datetime(2026, 1, 2, tzinfo=timezone.utc)),
            _fill(symbol="MSFT", side="buy", qty=5, price=200, order_id="m1", ts=datetime(2026, 1, 1, tzinfo=timezone.utc)),
            _fill(symbol="MSFT", side="sell", qty=5, price=190, order_id="m2", ts=datetime(2026, 1, 3, tzinfo=timezone.utc)),
        ])

        all_trades = store.closed_trades()
        assert len(all_trades) == 2
        # most-recent-exit-first
        assert all_trades[0].symbol == "MSFT"
        assert all_trades[1].symbol == "AAPL"

        aapl_only = store.closed_trades(symbol="aapl")
        assert len(aapl_only) == 1
        assert aapl_only[0].symbol == "AAPL"

        page = store.closed_trades(limit=1, offset=0)
        assert len(page) == 1
        assert store.closed_trade_count() == 2

    def test_empty_store_returns_empty_never_raises(self):
        store = BrokerFillsStore(db_url="sqlite:///:memory:")
        assert store.closed_trades() == []
        assert store.closed_trade_count() == 0
        assert store.all_fills() == []
        assert store.last_exit_ts_by_symbol() == {}
        assert store.last_ingested_at() is None
        assert store.instrument_symbol_map() == {}

    def test_read_degrades_on_torn_down_engine(self):
        store = BrokerFillsStore(db_url="sqlite:///:memory:")
        store.record_fills([_fill(order_id="a")])
        store.engine.dispose()
        # Force the underlying sqlite in-memory connection to vanish by
        # reassigning to a bogus URL's engine-less session factory.
        store.Session = None  # type: ignore[assignment]
        assert store.closed_trades() == []
        assert store.closed_trade_count() == 0
        assert store.all_fills() == []
        assert store.last_exit_ts_by_symbol() == {}
        assert store.last_ingested_at() is None
        assert store.instrument_symbol_map() == {}


# ---------------------------------------------------------------------------
# instrument symbol resolver cache
# ---------------------------------------------------------------------------


class TestInstrumentSymbolCache:
    def test_upsert_round_trip(self):
        store = BrokerFillsStore(db_url="sqlite:///:memory:")
        n = store.record_instrument_symbols({"url-a": "AAPL", "url-b": None})
        assert n == 2
        m = store.instrument_symbol_map()
        assert m == {"url-a": "AAPL", "url-b": None}

    def test_upsert_overwrites(self):
        store = BrokerFillsStore(db_url="sqlite:///:memory:")
        store.record_instrument_symbols({"url-a": None})
        store.record_instrument_symbols({"url-a": "AAPL"})
        assert store.instrument_symbol_map() == {"url-a": "AAPL"}


# ---------------------------------------------------------------------------
# ingest_filled_orders
# ---------------------------------------------------------------------------


class TestIngestFilledOrders:
    def test_ingest_persists_fetched_fills(self, monkeypatch):
        import data.robinhood_orders as rho

        def _fake_fetch(*, force=False, **kw):
            return [_fill(order_id="x1"), _fill(order_id="x2", side="sell")]

        monkeypatch.setattr(rho, "fetch_filled_orders", _fake_fetch)
        counts = ingest_filled_orders(force=True)
        assert counts["inserted"] == 2
        assert counts["n_fetched"] == 2

    def test_ingest_propagates_fetch_exceptions_uncaught(self, monkeypatch):
        """ingest_filled_orders does NOT swallow a fetch failure into an
        empty-but-successful result -- the best-effort boundary belongs to
        the caller (the login worker), which decides whether a failed
        ingest should still report the overall login as a success."""
        import data.robinhood_orders as rho

        def _boom(*, force=False, **kw):
            raise RuntimeError("network down")

        monkeypatch.setattr(rho, "fetch_filled_orders", _boom)
        with pytest.raises(RuntimeError, match="network down"):
            ingest_filled_orders(force=True)


# ---------------------------------------------------------------------------
# recently_closed_symbols
# ---------------------------------------------------------------------------


class TestRecentlyClosedSymbols:
    def _seeded_store(self, db_url):
        store = BrokerFillsStore(db_url=db_url)
        now = datetime.now(timezone.utc)
        store.record_fills([
            _fill(symbol="AAPL", side="buy", qty=10, price=100, order_id="a1", ts=now - timedelta(days=200)),
            _fill(symbol="AAPL", side="sell", qty=10, price=110, order_id="a2", ts=now - timedelta(days=10)),
            _fill(symbol="MSFT", side="buy", qty=5, price=200, order_id="m1", ts=now - timedelta(days=200)),
            _fill(symbol="MSFT", side="sell", qty=5, price=190, order_id="m2", ts=now - timedelta(days=200)),
        ])
        return store

    def test_within_window_included_outside_excluded(self, tmp_path, monkeypatch):
        db_url = f"sqlite:///{tmp_path}/retention1.db"
        self._seeded_store(db_url)
        import data.broker_fills_store as bfs

        monkeypatch.setattr(bfs, "resolve_database_url", lambda: db_url)
        symbols = recently_closed_symbols(retention_days=180, max_symbols=25)
        assert "AAPL" in symbols  # sold 10d ago
        assert "MSFT" not in symbols  # sold 200d ago

    def test_zero_retention_days_returns_empty(self, tmp_path, monkeypatch):
        db_url = f"sqlite:///{tmp_path}/retention2.db"
        import data.broker_fills_store as bfs

        self._seeded_store(db_url)
        monkeypatch.setattr(bfs, "resolve_database_url", lambda: db_url)
        assert recently_closed_symbols(retention_days=0, max_symbols=25) == []

    def test_max_symbols_caps_and_prefers_most_recent(self, tmp_path, monkeypatch):
        import data.broker_fills_store as bfs

        db_url = f"sqlite:///{tmp_path}/retention3.db"
        store = BrokerFillsStore(db_url=db_url)
        now = datetime.now(timezone.utc)
        store.record_fills([
            _fill(symbol="A", side="sell", qty=1, price=1, order_id="a", ts=now - timedelta(days=1)),
            _fill(symbol="B", side="sell", qty=1, price=1, order_id="b", ts=now - timedelta(days=2)),
            _fill(symbol="C", side="sell", qty=1, price=1, order_id="c", ts=now - timedelta(days=3)),
        ])
        monkeypatch.setattr(bfs, "resolve_database_url", lambda: db_url)
        symbols = recently_closed_symbols(retention_days=180, max_symbols=2)
        assert symbols == ["A", "B"]

    def test_store_failure_degrades_to_empty_never_shrinks_universe(self, monkeypatch):
        import data.broker_fills_store as bfs

        def _boom(*a, **kw):
            raise RuntimeError("db unreachable")

        monkeypatch.setattr(bfs, "BrokerFillsStore", _boom)
        assert recently_closed_symbols(retention_days=180, max_symbols=25) == []


# ---------------------------------------------------------------------------
# Sizing-isolation AST guards -- load-bearing, not incidental (see module
# docstring). Confirms Decision A is structural: broker fills can never
# reach a Kelly/vol-target sizing path via an import.
# ---------------------------------------------------------------------------


class TestSizingIsolation:
    def _import_roots(self, path: pathlib.Path) -> set:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    roots.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
        return roots

    def test_broker_fills_store_never_imports_transactions_store(self):
        path = pathlib.Path("data/broker_fills_store.py")
        roots = self._import_roots(path)
        assert "transactions_store" not in roots

    def test_no_sizing_or_execution_module_imports_broker_fills_store(self):
        offenders = []
        for pattern in ("sizing/*.py", "execution/*.py"):
            for path in pathlib.Path(".").glob(pattern):
                src = path.read_text(encoding="utf-8")
                if "broker_fills_store" in src:
                    offenders.append(str(path))
        assert offenders == [], f"broker_fills_store must never be imported by sizing/execution: {offenders}"

def test_record_instrument_symbols_mid_batch_failure_rolls_back_entire_batch():
    """Atomicity: if an event fails to process, the entire batch must roll back."""
    store = BrokerFillsStore(db_url="sqlite:///:memory:")
    
    mapping = {
        "url1": "AAPL",
        123: "MSFT",  # Intentionally passing integer to potentially trigger error
        "url3": "GOOG"
    }
    
    # We mock something to fail or just pass an invalid mapping that raises during processing
    # The dictionary iteration doesn't easily raise. Let's create an object that raises when .items() is processed?
    # Or just mock existing.get
    import pytest
    class ExplodingStr:
        def __str__(self):
            raise ValueError("Boom")
        def upper(self):
            raise ValueError("Boom")
            
    mapping["url2"] = ExplodingStr()
    
    with pytest.raises(ValueError):
        store.record_instrument_symbols(mapping)
        
    assert store.instrument_symbol_map() == {}
