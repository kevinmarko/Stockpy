import pytest
import os
import tempfile
import pandas as pd
from datetime import datetime, timedelta
from transactions_store import TransactionsStore, Trade, _OfflineTransactionsStore

def test_transactions_store_crud():
    # Use an in-memory SQLite database for testing CRUD
    store = TransactionsStore(db_url="sqlite:///:memory:")
    
    # 1. Insert a new open trade
    entry_ts = datetime(2026, 6, 20, 10, 0, 0)
    trade_id = store.record_trade(
        symbol="AAPL",
        side="long",
        entry_ts=entry_ts,
        entry_price=180.5,
        shares=100.0,
        strategy="Momentum",
        notes="Test trade entry"
    )
    assert isinstance(trade_id, int)
    
    # Verify it is in open trades
    open_df = store.open_trades_df()
    assert len(open_df) == 1
    assert open_df.iloc[0]["symbol"] == "AAPL"
    assert open_df.iloc[0]["side"] == "long"
    assert open_df.iloc[0]["entry_price"] == 180.5
    assert open_df.iloc[0]["shares"] == 100.0
    assert open_df.iloc[0]["strategy"] == "Momentum"
    assert open_df.iloc[0]["exit_ts"] is None
    
    # Verify trade history is returned correctly
    hist_df = store.get_trade_history("AAPL")
    assert len(hist_df) == 1
    assert hist_df.iloc[0]["trade_id"] == trade_id
    
    # 2. Close the trade
    exit_ts = datetime(2026, 6, 22, 16, 0, 0)
    store.close_trade(trade_id, exit_ts=exit_ts, exit_price=190.2)
    
    # Verify open trades is now empty
    open_df = store.open_trades_df()
    assert len(open_df) == 0
    
    # Verify closed trades has the closed trade
    closed_df = store.closed_trades_df()
    assert len(closed_df) == 1
    assert closed_df.iloc[0]["symbol"] == "AAPL"
    assert closed_df.iloc[0]["exit_price"] == 190.2
    assert closed_df.iloc[0]["exit_ts"] is not None


# ---------------------------------------------------------------------------
# readonly=True -- a DATABASE-LEVEL read-only TransactionsStore (distinct from
# _OfflineTransactionsStore below, which is a construction-failure fallback).
# ---------------------------------------------------------------------------

def test_readonly_store_reads_data_written_by_a_write_mode_store(tmp_path):
    db_url = f"sqlite:///{tmp_path / 't.db'}"
    writer = TransactionsStore(db_url=db_url)
    writer.record_trade(
        symbol="AAPL", side="long", entry_ts=datetime(2026, 6, 20), entry_price=180.5, shares=100.0,
    )

    reader = TransactionsStore(db_url=db_url, readonly=True)
    assert len(reader.open_trades_df()) == 1
    assert reader.open_trades_df().iloc[0]["symbol"] == "AAPL"


def test_readonly_store_write_methods_raise_rather_than_fabricate_success(tmp_path):
    """CONSTRAINT #4: mirrors _OfflineTransactionsStore's contract -- a
    readonly instance must not silently no-op a write."""
    db_url = f"sqlite:///{tmp_path / 't.db'}"
    TransactionsStore(db_url=db_url)  # write-mode: creates the schema first
    reader = TransactionsStore(db_url=db_url, readonly=True)
    with pytest.raises(Exception):
        reader.record_trade(symbol="AAPL", side="long", entry_ts=datetime.now(), entry_price=1.0, shares=1.0)


def test_readonly_store_does_not_write_on_a_blocked_write_attempt(tmp_path):
    """The blocked write must not have partially landed."""
    db_url = f"sqlite:///{tmp_path / 't.db'}"
    TransactionsStore(db_url=db_url)
    reader = TransactionsStore(db_url=db_url, readonly=True)
    try:
        reader.record_trade(symbol="MSFT", side="long", entry_ts=datetime.now(), entry_price=1.0, shares=1.0)
    except Exception:
        pass
    writer = TransactionsStore(db_url=db_url)
    assert writer.get_trade_history("MSFT").empty


def test_readonly_store_degrades_to_empty_df_on_missing_table(tmp_path):
    """No prior write-mode store has ever run -> the `trades` table doesn't
    exist. A readonly instance must degrade to an empty DataFrame, never crash
    (CONSTRAINT #6) -- unlike write mode, which always creates the table first."""
    db_path = tmp_path / "never_written.db"
    db_path.touch()
    reader = TransactionsStore(db_url=f"sqlite:///{db_path}", readonly=True)
    for df in (reader.open_trades_df(), reader.closed_trades_df(), reader.get_trade_history("AAPL")):
        assert isinstance(df, pd.DataFrame)
        assert df.empty


def test_readonly_store_construction_skips_ddl(tmp_path, monkeypatch):
    """readonly=True must not call Base.metadata.create_all / ALTER TABLE --
    both are writes a read-only engine would reject anyway."""
    import transactions_store as ts_module

    calls = []
    monkeypatch.setattr(
        ts_module.Base.metadata, "create_all",
        lambda *a, **k: calls.append("create_all"),
    )
    TransactionsStore(db_url=f"sqlite:///{tmp_path / 't.db'}", readonly=True)
    assert calls == []


# ---------------------------------------------------------------------------
# _OfflineTransactionsStore -- the read-only stub used when the configured
# DB backend (e.g. a Postgres/Supabase DATABASE_URL) is unreachable.
# ---------------------------------------------------------------------------

def test_offline_store_read_methods_return_empty_frames():
    store = _OfflineTransactionsStore()
    for df in (store.open_trades_df(), store.closed_trades_df(), store.get_trade_history("AAPL")):
        assert isinstance(df, pd.DataFrame)
        assert df.empty


def test_offline_store_closed_trades_df_is_compatible_with_kelly_sizing():
    """The empty frame must satisfy sizing.kelly's "no history" contract,
    not raise, so a DB outage degrades to the vol-target fallback rather
    than propagating out of evaluate_security()."""
    from sizing.kelly import estimate_win_rate_and_payoff

    store = _OfflineTransactionsStore()
    p, b, n_trades = estimate_win_rate_and_payoff(store.closed_trades_df())
    assert n_trades == 0
    assert p != p and b != b  # NaN != NaN


def test_offline_store_write_methods_raise_rather_than_fabricate_success():
    """CONSTRAINT #4: a trade that was never actually persisted must not be
    silently reported as recorded/closed."""
    store = _OfflineTransactionsStore()
    with pytest.raises(RuntimeError):
        store.record_trade(symbol="AAPL", side="long", entry_ts=datetime.now(), entry_price=1.0, shares=1.0)
    with pytest.raises(RuntimeError):
        store.close_trade(1, exit_ts=datetime.now(), exit_price=1.0)


def test_get_transactions_store_degrades_on_construction_failure(monkeypatch):
    """engine.advisory._get_transactions_store() must not propagate a DB
    connectivity failure -- it should log once and cache an offline stub so
    every symbol in the universe doesn't retry-storm the failing host."""
    import engine.advisory as advisory

    monkeypatch.setattr(advisory, "_TRANSACTIONS_STORE", None)

    def _boom(*args, **kwargs):
        raise ConnectionError("could not translate host name to address")

    monkeypatch.setattr(advisory, "TransactionsStore", _boom)
    monkeypatch.setattr(advisory, "_TransactionsStore_orig", _boom)

    store = advisory._get_transactions_store()
    assert isinstance(store, _OfflineTransactionsStore)
    # Second call must reuse the cached stub, not call the broken constructor again.
    assert advisory._get_transactions_store() is store


def test_strategy_engine_transactions_store_property_degrades_on_construction_failure(monkeypatch):
    """StrategyEngine.transactions_store must not propagate a DB connectivity
    failure out of the lazy-construction property."""
    import strategy_engine as se_module
    from strategy_engine import StrategyEngine

    def _boom(*args, **kwargs):
        raise ConnectionError("could not translate host name to address")

    monkeypatch.setattr("transactions_store.TransactionsStore", _boom)

    engine = StrategyEngine()
    store = engine.transactions_store
    assert isinstance(store, _OfflineTransactionsStore)
    assert engine.transactions_store is store  # cached, not reconstructed
