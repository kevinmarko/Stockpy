import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from data.paper_account_store import Base, PaperPosition, PaperAccount
from scripts.purge_corrupt_paper_options import run_purge

@pytest.fixture
def mock_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    
    with Session() as session:
        acc = PaperAccount(id=1, cash_balance=10000.0)
        session.add(acc)
        # Valid equity
        session.add(PaperPosition(symbol="AAPL", qty=10, avg_entry_price=150.0))
        # Valid option
        session.add(PaperPosition(symbol="AAPL 260116C00150000", qty=10, avg_entry_price=5.0))
        # Corrupt option
        session.add(PaperPosition(symbol="AAPL 260116P00150000", qty=10, avg_entry_price=0.0))
        # Corrupt equity (should not be deleted because it's not an option)
        session.add(PaperPosition(symbol="TSLA", qty=10, avg_entry_price=0.0))
        session.commit()
    
    yield engine

def test_purge_corrupt_paper_options_dry_run(mock_db):
    Session = sessionmaker(bind=mock_db)
    run_purge(apply=False, engine=mock_db)
    
    with Session() as session:
        positions = session.query(PaperPosition).all()
        assert len(positions) == 4

def test_purge_corrupt_paper_options_apply(mock_db):
    Session = sessionmaker(bind=mock_db)
    run_purge(apply=True, engine=mock_db)
    
    with Session() as session:
        positions = session.query(PaperPosition).all()
        assert len(positions) == 3
        symbols = [p.symbol for p in positions]
        assert "AAPL 260116P00150000" not in symbols
        assert "AAPL 260116C00150000" in symbols
        assert "AAPL" in symbols
        assert "TSLA" in symbols
