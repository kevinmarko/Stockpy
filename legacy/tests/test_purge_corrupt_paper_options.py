import pytest
import os
from data.paper_account_store import PaperAccountStore, PaperPosition, PaperAccount
from db_config import session_scope

def test_purge_corrupt_paper_options(monkeypatch, tmp_path):
    """
    Test that the purge script deletes option positions with entry_price <= 0
    and reverses their cash impact, leaving valid positions intact.
    """
    db_file = tmp_path / "test_purge.db"
    TEST_DB_URL = f"sqlite:///{db_file}"
    store = PaperAccountStore(db_url=TEST_DB_URL)
    
    with session_scope(store.Session) as session:
        acc = session.query(PaperAccount).filter_by(id=1).first()
        acc.cash_balance = 100000.0
        
        # Valid equity
        session.add(PaperPosition(symbol="AAPL", qty=10, avg_entry_price=150.0))
        # Valid option
        session.add(PaperPosition(symbol="AAPL 2026-09-18 $150.00 CALL", qty=1, avg_entry_price=5.0))
        # Corrupt option 1 (0 price)
        session.add(PaperPosition(symbol="QQQ 2026-10-16 $500.00 PUT", qty=-3, avg_entry_price=0.0))
        # Corrupt option 2 (negative price)
        session.add(PaperPosition(symbol="QQQ 2026-10-16 $500.00 CALL", qty=-3, avg_entry_price=-1.0))
        
    # Mock resolve_database_url
    monkeypatch.setattr("scripts.purge_corrupt_paper_options.resolve_database_url", lambda: TEST_DB_URL)
    
    # Run dry-run
    import sys
    monkeypatch.setattr(sys, "argv", ["purge_corrupt_paper_options.py"])
    from scripts.purge_corrupt_paper_options import main
    main()
    
    # Verify no changes in dry-run
    with session_scope(store.Session) as session:
        assert session.query(PaperPosition).count() == 4
        acc = session.query(PaperAccount).filter_by(id=1).first()
        assert acc.cash_balance == 100000.0

    # Run apply
    monkeypatch.setattr(sys, "argv", ["purge_corrupt_paper_options.py", "--apply"])
    main()

    # Verify changes
    with session_scope(store.Session) as session:
        positions = session.query(PaperPosition).all()
        assert len(positions) == 2
        symbols = [p.symbol for p in positions]
        assert "AAPL" in symbols
        assert "AAPL 2026-09-18 $150.00 CALL" in symbols
        
        # Cash reversal:
        # Corrupt 1: qty=-3, entry=0.0 -> impact = 0
        # Corrupt 2: qty=-3, entry=-1.0 -> impact = -3 * -1.0 * 100 = 300.0
        # Cash should be 100000.0 + 300.0 = 100300.0
        acc = session.query(PaperAccount).filter_by(id=1).first()
        assert acc.cash_balance == 100300.0
