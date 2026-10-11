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


def test_readonly_mode():
    with mock.patch("db_config.create_readonly_db_engine"):
        store = TrendsStore(readonly=True)
        with pytest.raises(RuntimeError, match="read-only"):
            store.insert_raw_window("AAPL", "w1", [], datetime.now())
        with pytest.raises(RuntimeError, match="read-only"):
            store.save_stitched_series("AAPL", [], datetime.now())


# ── Download batches, atomic re-stitch, retention ────────────────────────────

from datetime import timedelta

from data.trends_store import (
    BATCH_COMPLETE,
    BATCH_FAILED,
    BATCH_PARTIAL,
    batch_window_id,
)


def _record(store, term, batch_id, started, status=BATCH_COMPLETE, n_windows=2):
    windows = []
    for i in range(n_windows):
        d0 = date(2026, 1, 1) + timedelta(days=60 * i)
        d1 = d0 + timedelta(days=2)
        rows = [{"date": d0 + timedelta(days=k), "value": float(10 * (i + 1) + k)} for k in range(3)]
        windows.append((batch_window_id(batch_id, d0, d1), rows))
    store.record_batch(
        batch_id=batch_id, query_term=term, started_at=started,
        finished_at=started + timedelta(minutes=1), windows_expected=n_windows,
        windows=windows if status != BATCH_FAILED else [],
        status=status, reason=None if status == BATCH_COMPLETE else "test",
    )


def test_batch_window_id_is_deterministic_and_sorts_chronologically():
    a = batch_window_id("b1", date(2026, 1, 1), date(2026, 3, 31))
    b = batch_window_id("b1", date(2026, 3, 1), date(2026, 5, 30))
    assert a == "b1/2026-01-01/2026-03-31"
    assert sorted([b, a]) == [a, b]


def test_record_batch_and_load_batch_windows():
    store = TrendsStore()
    t0 = datetime(2026, 10, 1, 6, 0)
    _record(store, "AAL", "b1", t0)
    _record(store, "AAL", "b2", t0 + timedelta(days=1))

    rows = store.load_batch_windows("b2")
    assert len(rows) == 6
    assert {r.window_id.split("/")[0] for r in rows} == {"b2"}
    # Windows are contiguous and in chronological order.
    assert [r.window_id for r in rows] == sorted(r.window_id for r in rows)

    latest = store.latest_batch("AAL")
    assert latest["batch_id"] == "b2"
    assert latest["windows_fetched"] == 2
    assert latest["status"] == BATCH_COMPLETE


def test_latest_batch_complete_only_skips_partial_and_failed():
    store = TrendsStore()
    t0 = datetime(2026, 10, 1, 6, 0)
    _record(store, "AAL", "good", t0)
    _record(store, "AAL", "part", t0 + timedelta(hours=1), status=BATCH_PARTIAL, n_windows=1)
    _record(store, "AAL", "fail", t0 + timedelta(hours=2), status=BATCH_FAILED)

    assert store.latest_batch("AAL")["batch_id"] == "good"
    assert store.latest_batch("AAL", complete_only=False)["batch_id"] == "fail"
    assert store.latest_batch("NOPE") is None
    assert store.list_terms_with_batches() == ["AAL"]


def test_last_attempts_reports_latest_attempt_and_latest_complete():
    store = TrendsStore()
    t0 = datetime(2026, 10, 1, 6, 0)
    _record(store, "AAL", "good", t0)
    _record(store, "AAL", "fail", t0 + timedelta(hours=2), status=BATCH_FAILED)
    _record(store, "ABR", "only_fail", t0, status=BATCH_FAILED)

    out = store.last_attempts()
    assert out["AAL"] == {"attempt": t0 + timedelta(hours=2), "complete": t0}
    assert out["ABR"] == {"attempt": t0, "complete": None}


def test_save_stitched_series_replace_leaves_no_orphan_days():
    """The old upsert-only save kept older dates at an older scale when a new
    stitch covered a later range (36 orphan days for AAL in the live DB)."""
    store = TrendsStore()
    old = [{"date": date(2025, 9, 1) + timedelta(days=i), "value": 50.0} for i in range(10)]
    store.save_stitched_series("AAL", old, datetime(2026, 9, 1))
    new = [{"date": date(2025, 9, 6) + timedelta(days=i), "value": 70.0} for i in range(10)]
    store.save_stitched_series("AAL", new, datetime(2026, 10, 1), replace=True)

    got = store.get_stitched_series("AAL")
    assert [r["date"] for r in got] == [r["date"] for r in new]
    assert all(r["value"] == 70.0 for r in got)


def test_save_stitched_series_replace_only_touches_its_own_term():
    store = TrendsStore()
    store.save_stitched_series("ABR", [{"date": date(2026, 1, 1), "value": 1.0}], datetime(2026, 1, 2))
    store.save_stitched_series("AAL", [{"date": date(2026, 1, 1), "value": 2.0}], datetime(2026, 1, 2), replace=True)
    assert store.get_stitched_series("ABR") == [{"date": date(2026, 1, 1), "value": 1.0}]


def test_save_stitched_series_replace_is_atomic_on_failure():
    """If the insert fails, the delete must roll back with it."""
    store = TrendsStore()
    store.save_stitched_series("AAL", [{"date": date(2026, 1, 1), "value": 5.0}], datetime(2026, 1, 2))
    with pytest.raises(Exception):
        # value=None violates NOT NULL, so the transaction fails at commit.
        store.save_stitched_series(
            "AAL", [{"date": date(2026, 2, 1), "value": None}], datetime(2026, 2, 2), replace=True
        )
    assert store.get_stitched_series("AAL") == [{"date": date(2026, 1, 1), "value": 5.0}]


def test_prune_raw_batches_keeps_exactly_k_complete_batches():
    store = TrendsStore()
    t0 = datetime(2026, 10, 1, 6, 0)
    for i in range(5):
        _record(store, "AAL", f"b{i}", t0 + timedelta(days=i))
    # A failed batch older than the kept ones is pruned; a newer partial one is kept.
    _record(store, "AAL", "oldfail", t0 - timedelta(days=1), status=BATCH_FAILED)
    _record(store, "AAL", "newpart", t0 + timedelta(days=9), status=BATCH_PARTIAL, n_windows=1)
    # Another term is untouched.
    _record(store, "ABR", "x0", t0)

    deleted = store.prune_raw_batches("AAL", keep=2)
    assert deleted == 3 * 6  # b0, b1, b2: 2 windows x 3 rows each

    remaining = {r.window_id.split("/")[0] for r in store.load_raw_windows("AAL")}
    assert remaining == {"b3", "b4", "newpart"}
    assert store.latest_batch("AAL", complete_only=False)["batch_id"] == "newpart"
    assert store.load_batch_windows("x0")


def test_prune_raw_batches_removes_legacy_uuid_rows_only_once_a_batch_exists():
    store = TrendsStore()
    store.insert_raw_window("AAL", "3f2b6c1e-0000-4000-8000-000000000000",
                            [{"date": date(2026, 1, 1), "value": 1.0}], datetime(2026, 1, 2))
    # No complete batch yet: legacy rows are the only raw data, so keep them.
    assert store.prune_raw_batches("AAL", keep=14) == 0
    assert len(store.load_raw_windows("AAL")) == 1

    _record(store, "AAL", "b0", datetime(2026, 10, 1))
    assert store.prune_raw_batches("AAL", keep=14) == 1
    assert all("/" in r.window_id for r in store.load_raw_windows("AAL"))


def test_batch_reads_never_raise_on_db_error():
    store = TrendsStore()
    with mock.patch.object(store, "Session", side_effect=RuntimeError("db down")):
        assert store.latest_batch("AAL") is None
        assert store.load_batch_windows("b1") == []
        assert store.last_attempts() == {}
        assert store.list_terms_with_batches() == []


def test_batch_writes_refused_when_readonly():
    with mock.patch("db_config.create_readonly_db_engine"):
        store = TrendsStore(readonly=True)
        with pytest.raises(RuntimeError, match="read-only"):
            store.record_batch(batch_id="b", query_term="AAL", started_at=datetime.now(),
                               finished_at=datetime.now(), windows_expected=1, windows=[],
                               status=BATCH_FAILED)
        with pytest.raises(RuntimeError, match="read-only"):
            store.prune_raw_batches("AAL", 1)


def test_default_store_is_in_memory_under_tests():
    """The root conftest redirects the default TrendsStore DB so the suite can
    never write to the operator's live database (it covers the batch table
    too, which shares the same engine)."""
    assert str(TrendsStore().engine.url) == "sqlite:///:memory:"


def test_prune_bounds_a_term_that_never_completes():
    """Partial/failed batches must not pile up for a term Google has no
    complete data for (audit finding on the first version)."""
    store = TrendsStore()
    t0 = datetime(2026, 10, 1, 6, 0)
    for i in range(5):
        _record(store, "DEAD", f"p{i}", t0 + timedelta(days=i), status=BATCH_PARTIAL, n_windows=1)
    store.prune_raw_batches("DEAD", keep=2)
    assert {r.window_id.split("/")[0] for r in store.load_raw_windows("DEAD")} == {"p3", "p4"}


def test_replace_with_empty_series_is_refused():
    store = TrendsStore()
    store.save_stitched_series("AAL", [{"date": date(2026, 1, 1), "value": 5.0}], datetime(2026, 1, 2))
    store.save_stitched_series("AAL", [], datetime(2026, 2, 2), replace=True)
    assert store.get_stitched_series("AAL") == [{"date": date(2026, 1, 1), "value": 5.0}]


# ── Viewer read helpers: latest_batches / stitched_summary / get_stitched_rows ──


def test_latest_batches_pairs_latest_with_latest_complete():
    store = TrendsStore()
    t0 = datetime(2026, 10, 1, 6, 0)
    _record(store, "AAL", "a1", t0)
    _record(store, "AAL", "a2", t0 + timedelta(days=1), status=BATCH_PARTIAL, n_windows=1)
    _record(store, "DEAD", "d1", t0, status=BATCH_FAILED)

    out = store.latest_batches()

    assert out["AAL"]["latest"]["batch_id"] == "a2"
    assert out["AAL"]["latest"]["status"] == BATCH_PARTIAL
    assert out["AAL"]["complete"]["batch_id"] == "a1"
    assert out["DEAD"]["latest"]["batch_id"] == "d1"
    assert out["DEAD"]["complete"] is None
    assert set(out["AAL"]["latest"]) >= {
        "batch_id", "started_at", "finished_at", "windows_expected",
        "windows_fetched", "status", "reason",
    }


def test_latest_batches_empty_store_is_empty_dict():
    assert TrendsStore().latest_batches() == {}


def test_latest_batches_swallows_session_errors():
    store = TrendsStore()
    with mock.patch.object(store, "Session", side_effect=RuntimeError("db down")):
        assert store.latest_batches() == {}


def test_stitched_summary_counts_and_bounds_per_term():
    store = TrendsStore()
    t1, t2 = datetime(2026, 2, 1), datetime(2026, 3, 1)
    store.save_stitched_series("AAL", [
        {"date": date(2026, 1, 1), "value": 1.0},
        {"date": date(2026, 1, 5), "value": 2.0},
        {"date": date(2026, 1, 3), "value": 3.0},
    ], t1)
    store.save_stitched_series("ABR", [{"date": date(2026, 1, 9), "value": 4.0}], t2)

    out = store.stitched_summary()

    assert out["AAL"] == {
        "days": 3, "first_date": date(2026, 1, 1),
        "last_date": date(2026, 1, 5), "stitched_at": t1,
    }
    assert out["ABR"]["days"] == 1
    assert out["ABR"]["stitched_at"] == t2


def test_stitched_summary_swallows_session_errors():
    store = TrendsStore()
    with mock.patch.object(store, "Session", side_effect=RuntimeError("db down")):
        assert store.stitched_summary() == {}


def test_get_stitched_rows_ordered_with_stamp():
    store = TrendsStore()
    at = datetime(2026, 2, 1, 12)
    store.save_stitched_series("AAL", [
        {"date": date(2026, 1, 3), "value": 3.0},
        {"date": date(2026, 1, 1), "value": 1.0},
    ], at)
    store.save_stitched_series("OTHER", [{"date": date(2026, 1, 1), "value": 9.0}], at)

    assert store.get_stitched_rows("AAL") == [
        {"date": date(2026, 1, 1), "value": 1.0, "stitched_at": at},
        {"date": date(2026, 1, 3), "value": 3.0, "stitched_at": at},
    ]
    assert store.get_stitched_rows("NONE") == []


def test_get_stitched_rows_swallows_session_errors():
    store = TrendsStore()
    with mock.patch.object(store, "Session", side_effect=RuntimeError("db down")):
        assert store.get_stitched_rows("AAL") == []
