import pytest
from datetime import datetime
from sqlalchemy import create_engine
from data.cache_long_short_store import CacheLongShortStore, Base

@pytest.fixture
def store():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    
    # Store uses sessionmaker internally and takes db_url.
    # To mock the initialization, we can just pass the in-memory URL
    # and then manually override the engine. Wait, __init__ re-creates engine.
    # Let's just pass "sqlite:///:memory:" and it will create a new in-memory DB.
    s = CacheLongShortStore(db_url="sqlite:///:memory:")
    return s

def test_store_initialization(store):
    assert store.engine is not None
    assert store.Session is not None

def test_record_position_and_lot(store):
    pos_id = store.record_position("AAPL", "long")
    assert isinstance(pos_id, int)
    
    lot_id = store.record_tax_lot(pos_id, datetime.now(), 150.0, 100.0)
    assert isinstance(lot_id, int)

def test_get_open_tax_lots(store):
    pos_id = store.record_position("AAPL", "long")
    store.record_tax_lot(pos_id, datetime.now(), 150.0, 100.0)
    store.record_tax_lot(pos_id, datetime.now(), 160.0, 100.0)
    
    lots = store.get_open_tax_lots()
    assert len(lots) == 2
    assert lots[0].status == "open"
    assert lots[0].tlh_approved == 0

def test_approve_tax_lots(store):
    pos_id = store.record_position("AAPL", "long")
    lot_id = store.record_tax_lot(pos_id, datetime.now(), 150.0, 100.0)
    
    store.approve_tax_lots([lot_id])
    
    lots = store.get_open_tax_lots()
    assert len(lots) == 1
    assert lots[0].tlh_approved == 1
