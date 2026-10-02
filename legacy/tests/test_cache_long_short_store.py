import pytest
from datetime import datetime, timedelta, timezone
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


def test_close_tax_lot(store):
    pos_id = store.record_position("AAPL", "long")
    lot_id = store.record_tax_lot(pos_id, datetime.now(), 150.0, 100.0)

    store.close_tax_lot(lot_id, realized_pnl=-250.0, close_date=datetime.now(timezone.utc))

    assert store.get_open_tax_lots() == []
    closed = store.get_closed_lots_since(datetime.now(timezone.utc) - timedelta(days=1))
    assert len(closed) == 1
    assert closed[0].status == "closed"
    assert closed[0].realized_pnl == -250.0


def test_record_tax_lot_normalizes_non_utc_tz_to_naive_utc(store):
    # A tz-aware datetime in a non-UTC zone must be converted to UTC before
    # the tzinfo is stripped -- a bare .replace(tzinfo=None) on the raw
    # value (the pre-fix behavior) would silently mis-stamp it by the zone
    # offset, the same bug class this repo hit with FMP's Eastern-time
    # publishedDate.
    from zoneinfo import ZoneInfo

    eastern = ZoneInfo("America/New_York")
    # 2026-01-15 09:00 ET == 2026-01-15 14:00 UTC (EST, UTC-5).
    aware_eastern = datetime(2026, 1, 15, 9, 0, 0, tzinfo=eastern)

    pos_id = store.record_position("AAPL", "long")
    lot_id = store.record_tax_lot(pos_id, aware_eastern, 150.0, 100.0)

    session = store.Session()
    try:
        from data.cache_long_short_store import CacheLongShortTaxLot

        lot = session.query(CacheLongShortTaxLot).filter(CacheLongShortTaxLot.lot_id == lot_id).first()
        assert lot.acquisition_date == datetime(2026, 1, 15, 14, 0, 0)
    finally:
        session.close()


def test_close_tax_lot_normalizes_non_utc_tz_to_naive_utc(store):
    from zoneinfo import ZoneInfo

    eastern = ZoneInfo("America/New_York")
    aware_eastern = datetime(2026, 1, 15, 9, 0, 0, tzinfo=eastern)

    pos_id = store.record_position("AAPL", "long")
    lot_id = store.record_tax_lot(pos_id, datetime.now(), 150.0, 100.0)
    store.close_tax_lot(lot_id, realized_pnl=-100.0, close_date=aware_eastern)

    session = store.Session()
    try:
        from data.cache_long_short_store import CacheLongShortTaxLot

        lot = session.query(CacheLongShortTaxLot).filter(CacheLongShortTaxLot.lot_id == lot_id).first()
        assert lot.close_date == datetime(2026, 1, 15, 14, 0, 0)
    finally:
        session.close()


def test_close_tax_lot_missing_raises():
    s = CacheLongShortStore(db_url="sqlite:///:memory:")
    with pytest.raises(ValueError):
        s.close_tax_lot(9999, realized_pnl=-1.0, close_date=datetime.now(timezone.utc))


def test_get_closed_lots_since_excludes_lots_before_cutoff(store):
    pos_id = store.record_position("AAPL", "long")
    old_lot = store.record_tax_lot(pos_id, datetime.now(), 150.0, 100.0)
    recent_lot = store.record_tax_lot(pos_id, datetime.now(), 150.0, 100.0)

    store.close_tax_lot(old_lot, realized_pnl=-100.0, close_date=datetime.now(timezone.utc) - timedelta(days=45))
    store.close_tax_lot(recent_lot, realized_pnl=-100.0, close_date=datetime.now(timezone.utc) - timedelta(days=5))

    closed = store.get_closed_lots_since(datetime.now(timezone.utc) - timedelta(days=30))
    assert len(closed) == 1
    assert closed[0].lot_id == recent_lot


def test_tax_bank_sums_only_realized_losses(store):
    pos_id = store.record_position("AAPL", "long")
    loss_lot = store.record_tax_lot(pos_id, datetime.now(), 150.0, 100.0)
    gain_lot = store.record_tax_lot(pos_id, datetime.now(), 150.0, 100.0)
    open_lot = store.record_tax_lot(pos_id, datetime.now(), 150.0, 100.0)

    store.close_tax_lot(loss_lot, realized_pnl=-500.0, close_date=datetime.now(timezone.utc))
    store.close_tax_lot(gain_lot, realized_pnl=800.0, close_date=datetime.now(timezone.utc))
    # open_lot stays open -- must not count toward the tax bank.

    assert store.tax_bank() == pytest.approx(500.0)


def test_tax_bank_zero_when_no_closed_losses(store):
    assert store.tax_bank() == 0.0


def test_exposure_summary_groups_by_position_type(store):
    long_pos = store.record_position("AAPL", "long")
    short_pos = store.record_position("XLK", "short")
    store.record_tax_lot(long_pos, datetime.now(), 100.0, 10.0)  # $1000 long
    store.record_tax_lot(short_pos, datetime.now(), 50.0, 10.0)  # $500 short

    summary = store.exposure_summary()
    assert summary["long_exposure"] == pytest.approx(1000.0)
    assert summary["short_exposure"] == pytest.approx(500.0)
    assert summary["net_exposure"] == pytest.approx(500.0)
    assert summary["gross_exposure"] == pytest.approx(1500.0)


def test_exposure_summary_empty_store_is_all_zero(store):
    summary = store.exposure_summary()
    assert summary == {
        "long_exposure": 0.0,
        "short_exposure": 0.0,
        "net_exposure": 0.0,
        "gross_exposure": 0.0,
    }


def test_upsert_and_get_security_proxy(store):
    assert store.get_security_proxy("AAPL") is None

    store.upsert_security_proxy("AAPL", "XLK", 0.85)
    proxy = store.get_security_proxy("AAPL")
    assert proxy["proxy_ticker"] == "XLK"
    assert proxy["correlation_coefficient"] == 0.85

    # Upsert updates in place, not a duplicate row.
    store.upsert_security_proxy("AAPL", "QQQ", 0.5)
    proxy = store.get_security_proxy("AAPL")
    assert proxy["proxy_ticker"] == "QQQ"
    assert proxy["correlation_coefficient"] == 0.5


def test_flag_lot_for_tlh_and_get_pending(store):
    pos_id = store.record_position("AAPL", "long")
    flagged_lot = store.record_tax_lot(pos_id, datetime.now(), 150.0, 100.0)
    unflagged_lot = store.record_tax_lot(pos_id, datetime.now(), 150.0, 100.0)

    store.flag_lot_for_tlh(flagged_lot, unrealized_loss_pct=-0.10)

    pending = store.get_pending_tlh_lots()
    assert len(pending) == 1
    assert pending[0].lot_id == flagged_lot
    assert pending[0].unrealized_loss_pct == pytest.approx(-0.10)
    assert all(l.lot_id != unflagged_lot for l in pending)


def test_flagged_lot_disappears_from_pending_once_approved(store):
    pos_id = store.record_position("AAPL", "long")
    lot_id = store.record_tax_lot(pos_id, datetime.now(), 150.0, 100.0)
    store.flag_lot_for_tlh(lot_id, unrealized_loss_pct=-0.10)
    assert len(store.get_pending_tlh_lots()) == 1

    store.approve_tax_lots([lot_id])
    assert store.get_pending_tlh_lots() == []


# ---------------------------------------------------------------------------
# Readonly-mode guards -- every write method must refuse, every read method
# must degrade to an honest empty/zero shape rather than raise.
# ---------------------------------------------------------------------------


@pytest.fixture
def readonly_store(tmp_path):
    db_file = tmp_path / "missing_cls.db"
    return CacheLongShortStore(db_url=f"sqlite:///{db_file}", readonly=True)


class TestReadonlyGuards:
    def test_record_position_raises(self, readonly_store):
        with pytest.raises(RuntimeError):
            readonly_store.record_position("AAPL", "long")

    def test_record_tax_lot_raises(self, readonly_store):
        with pytest.raises(RuntimeError):
            readonly_store.record_tax_lot(1, datetime.now(), 150.0, 100.0)

    def test_close_tax_lot_raises(self, readonly_store):
        with pytest.raises(RuntimeError):
            readonly_store.close_tax_lot(1, realized_pnl=-1.0, close_date=datetime.now(timezone.utc))

    def test_approve_tax_lots_raises(self, readonly_store):
        with pytest.raises(RuntimeError):
            readonly_store.approve_tax_lots([1])

    def test_upsert_security_proxy_raises(self, readonly_store):
        with pytest.raises(RuntimeError):
            readonly_store.upsert_security_proxy("AAPL", "XLK", 0.5)

    def test_flag_lot_for_tlh_raises(self, readonly_store):
        with pytest.raises(RuntimeError):
            readonly_store.flag_lot_for_tlh(1, unrealized_loss_pct=-0.1)

    def test_reads_degrade_to_empty_against_a_missing_db(self, readonly_store):
        assert readonly_store.get_open_positions() == []
        assert readonly_store.get_open_tax_lots() == []
        assert readonly_store.get_pending_tlh_lots() == []
        assert readonly_store.get_security_proxy("AAPL") is None
        assert readonly_store.tax_bank() == 0.0
        assert readonly_store.exposure_summary()["gross_exposure"] == 0.0
