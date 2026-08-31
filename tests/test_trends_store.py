from datetime import date, datetime
import pytest
from unittest import mock
from data.trends_store import TrendsStore

def test_trends_store_operations():
    store = TrendsStore()
    
    dt = datetime(2023, 1, 1, 12, 0, 0)
    store.insert_raw_window("AAPL", "w1", [
        {"date": date(2023, 1, 1), "value": 10.0},
        {"date": date(2023, 1, 2), "value": 20.0}
    ], dt)
    
    raw = store.load_raw_windows("AAPL")
    assert len(raw) == 2
    assert raw[0].query_term == "AAPL"
    assert raw[0].window_id == "w1"
    assert raw[0].value == 10.0
    
    store.save_stitched_series("AAPL", [
        {"date": date(2023, 1, 1), "value": 15.0},
        {"date": date(2023, 1, 2), "value": 25.0}
    ], dt)
    
    stitched = store.get_stitched_series("AAPL")
    assert len(stitched) == 2
    assert stitched[0]["value"] == 15.0
    
    dt_early = datetime(2022, 1, 1, 12, 0, 0)
    stitched_early = store.get_stitched_series("AAPL", as_of=dt_early)
    assert len(stitched_early) == 0

def test_readonly_mode():
    with mock.patch("db_config.create_readonly_db_engine"):
        store = TrendsStore(readonly=True)
        with pytest.raises(RuntimeError, match="read-only"):
            store.insert_raw_window("AAPL", "w1", [], datetime.now())
        with pytest.raises(RuntimeError, match="read-only"):
            store.save_stitched_series("AAPL", [], datetime.now())
