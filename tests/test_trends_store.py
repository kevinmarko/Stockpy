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

def test_insert_raw_window_upserts_on_duplicate_natural_key():
    """A repeat insert for the same (query_term, window_id, date) must update
    the existing row's value/downloaded_at in place, never append a second
    row -- regression test for the unbounded raw_trends_downloads growth
    (no dedup, no unique constraint) confirmed live on the real shared DB."""
    store = TrendsStore()

    dt1 = datetime(2023, 1, 1, 12, 0, 0)
    store.insert_raw_window("MSFT", "w1", [
        {"date": date(2023, 1, 1), "value": 10.0},
        {"date": date(2023, 1, 2), "value": 20.0},
    ], dt1)

    raw_before = store.load_raw_windows("MSFT")
    assert len(raw_before) == 2

    # Re-download the identical window (same query_term/window_id/date),
    # with a different value/timestamp -- simulates a daemon cycle
    # re-fetching an overlapping window.
    dt2 = datetime(2023, 1, 3, 9, 0, 0)
    store.insert_raw_window("MSFT", "w1", [
        {"date": date(2023, 1, 1), "value": 99.0},
        {"date": date(2023, 1, 2), "value": 88.0},
    ], dt2)

    raw_after = store.load_raw_windows("MSFT")
    # Row count must NOT grow -- the duplicate must upsert, not append.
    assert len(raw_after) == 2
    by_date = {r.date: r for r in raw_after}
    assert by_date[date(2023, 1, 1)].value == 99.0
    assert by_date[date(2023, 1, 1)].downloaded_at == dt2
    assert by_date[date(2023, 1, 2)].value == 88.0

    # A genuinely different window_id for the same term/date is a distinct
    # natural key and must NOT be collapsed into the existing row.
    store.insert_raw_window("MSFT", "w2", [
        {"date": date(2023, 1, 1), "value": 5.0},
    ], dt2)
    raw_final = store.load_raw_windows("MSFT")
    assert len(raw_final) == 3


def test_get_query_terms_with_raw_windows_returns_distinct_sorted_terms():
    store = TrendsStore()
    dt = datetime(2023, 1, 1, 12, 0, 0)

    store.insert_raw_window("MSFT", "w1", [{"date": date(2023, 1, 1), "value": 5.0}], dt)
    # A second window for the SAME term must not duplicate it in the result.
    store.insert_raw_window("MSFT", "w2", [{"date": date(2023, 1, 2), "value": 6.0}], dt)
    store.insert_raw_window("AAPL", "w1", [{"date": date(2023, 1, 1), "value": 7.0}], dt)

    terms = store.get_query_terms_with_raw_windows()

    # Alphabetically sorted, distinct, no "ZZZZ" (never ingested) present.
    assert terms == ["AAPL", "MSFT"]


def test_get_query_terms_with_raw_windows_empty_store():
    store = TrendsStore()
    assert store.get_query_terms_with_raw_windows() == []


def test_readonly_mode():
    with mock.patch("db_config.create_readonly_db_engine"):
        store = TrendsStore(readonly=True)
        with pytest.raises(RuntimeError, match="read-only"):
            store.insert_raw_window("AAPL", "w1", [], datetime.now())
        with pytest.raises(RuntimeError, match="read-only"):
            store.save_stitched_series("AAPL", [], datetime.now())
