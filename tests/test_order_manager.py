import pytest
from transactions_store import TransactionsStore
from execution.order_manager import OrderManager
from execution.fmp_paper_broker import FMPPaperBroker
from data.paper_account_store import PaperClosedTrade

@pytest.mark.anyio
async def test_paper_roundtrip_resolves_reconciliation_drift(monkeypatch, tmp_path):
    paper_path = str(tmp_path / "paper.db")
    tx_path = str(tmp_path / "tx.db")
    
    broker = FMPPaperBroker(db_url=f"sqlite:///{paper_path}")
    paper_store = broker.store
    tx_store = TransactionsStore(db_url=f"sqlite:///{tx_path}")
    
    # Mock tx store so paper_store._execute_bridges sees it
    monkeypatch.setattr("transactions_store.TransactionsStore", lambda *args, **kwargs: tx_store)
    
    om = OrderManager(broker, dry_run=False)
    
    # Check initial state
    report = await om.reconcile_state(tx_store)
    assert not report.has_drift
    
    # Open trade
    paper_store.apply_fill("ord1", "AAPL", "buy", 10.0, 150.0, strategy_id="strat_1")
    
    # Check if there is drift when OPEN
    report = await om.reconcile_state(tx_store)
    assert report.has_drift
    
    # Close trade
    paper_store.apply_fill("ord2", "AAPL", "sell", 10.0, 160.0, strategy_id="strat_1")
    
    # Now it is closed!
    report = await om.reconcile_state(tx_store)
    
    # Assert no drift
    assert not report.has_drift, f"Drift items: {report.drift_items}"
    
    # Assert paper_closed_trades has correct row
    with paper_store.Session() as session:
        closed = session.query(PaperClosedTrade).all()
        assert len(closed) == 1
        assert closed[0].strategy_id == "strat_1"
        assert closed[0].realized_pnl == 100.0 # (160 - 150) * 10
        assert closed[0].close_reason == "flatten"
        
    # Assert transactions_store has one closed trade
    closed_df = tx_store.closed_trades_df()
    assert len(closed_df) == 1
    assert closed_df.iloc[0]["strategy"] == "strat_1"

