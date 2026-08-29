"""Tests for sizing/cap_audit_store.py -- the durable sizing_cap_events DB
table backing the cap-aware escalation rule and the GUI's cap-event audit
trail.

Mirrors tests/test_run_history_store.py's conventions (in-memory SQLite for
CRUD, a tmp_path-backed file DB for readonly=True, missing-table degrade)."""

from datetime import datetime, timezone

import pytest

from sizing.cap_audit_store import CapAuditStore, _OfflineCapAuditStore
from sizing.position_sizer import CapEventSummary


def _event(symbol="AAPL", was_capped=True, **overrides) -> dict:
    defaults = dict(
        symbol=symbol,
        strategy_id=None,
        raw_weight=0.30,
        final_weight=0.20,
        binding_constraint="kelly_cap" if was_capped else None,
        was_capped=was_capped,
    )
    defaults.update(overrides)
    return defaults


def test_record_and_get_recent_round_trip():
    store = CapAuditStore(db_url="sqlite:///:memory:")
    store.record_cap_events([_event()], cycle_id="cycle-1")

    rows = store.get_recent(limit=10)
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "AAPL"
    assert row["cycle_id"] == "cycle-1"
    assert row["binding_constraint"] == "kelly_cap"
    assert row["was_capped"] is True
    assert row["raw_weight"] == pytest.approx(0.30)
    assert row["final_weight"] == pytest.approx(0.20)


def test_record_cap_events_is_a_noop_on_empty_list():
    store = CapAuditStore(db_url="sqlite:///:memory:")
    store.record_cap_events([], cycle_id="cycle-1")
    assert store.get_recent(limit=10) == []


def test_symbol_uppercased_on_write():
    store = CapAuditStore(db_url="sqlite:///:memory:")
    store.record_cap_events([_event(symbol="aapl")])
    rows = store.get_recent(limit=10)
    assert rows[0]["symbol"] == "AAPL"


def test_get_recent_most_recent_first():
    store = CapAuditStore(db_url="sqlite:///:memory:")
    store.record_cap_events([
        _event("OLD", timestamp=datetime(2026, 7, 18, 9, 0, tzinfo=timezone.utc)),
    ])
    store.record_cap_events([
        _event("NEW", timestamp=datetime(2026, 7, 18, 11, 0, tzinfo=timezone.utc)),
    ])

    rows = store.get_recent(limit=10)
    assert [r["symbol"] for r in rows] == ["NEW", "OLD"]


def test_get_recent_respects_limit():
    store = CapAuditStore(db_url="sqlite:///:memory:")
    for i in range(5):
        store.record_cap_events([
            _event(f"SYM{i}", timestamp=datetime(2026, 7, 18, i, 0, tzinfo=timezone.utc)),
        ])
    rows = store.get_recent(limit=2)
    assert len(rows) == 2


def test_record_cap_events_writes_whole_cycle_in_one_call():
    """A cycle with many symbols is passed as a single list -- one
    transaction, not N separate writes."""
    store = CapAuditStore(db_url="sqlite:///:memory:")
    store.record_cap_events(
        [_event("AAPL"), _event("MSFT", was_capped=False), _event("GOOG")],
        cycle_id="cycle-42",
    )
    rows = store.get_recent(limit=10)
    assert len(rows) == 3
    assert all(r["cycle_id"] == "cycle-42" for r in rows)


# ---------------------------------------------------------------------------
# get_recent_for_symbol / get_consecutive_capped_cycles
# ---------------------------------------------------------------------------


def test_get_recent_for_symbol_filters_by_symbol():
    store = CapAuditStore(db_url="sqlite:///:memory:")
    store.record_cap_events([_event("AAPL"), _event("MSFT")])
    rows = store.get_recent_for_symbol("AAPL", limit=10)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "AAPL"


def test_get_recent_for_symbol_none_strategy_id_does_not_match_a_named_strategy():
    """strategy_id=None means 'no strategy_id was recorded', not 'any
    strategy' -- a query for None must not return a row recorded with a real
    strategy_id."""
    store = CapAuditStore(db_url="sqlite:///:memory:")
    store.record_cap_events([_event("AAPL", strategy_id="rsi2_mean_reversion")])
    rows = store.get_recent_for_symbol("AAPL", strategy_id=None, limit=10)
    assert rows == []

    rows_named = store.get_recent_for_symbol("AAPL", strategy_id="rsi2_mean_reversion", limit=10)
    assert len(rows_named) == 1


def test_consecutive_capped_cycles_counts_the_unbroken_recent_run():
    """3 capped cycles, most recent first, then an uncapped cycle further
    back -- consecutive count must stop at the break, not count all 4."""
    store = CapAuditStore(db_url="sqlite:///:memory:")
    store.record_cap_events([_event("AAPL", was_capped=False,
                                     timestamp=datetime(2026, 7, 18, 8, 0, tzinfo=timezone.utc))])
    store.record_cap_events([_event("AAPL", was_capped=True, binding_constraint="max_position_weight",
                                     timestamp=datetime(2026, 7, 18, 9, 0, tzinfo=timezone.utc))])
    store.record_cap_events([_event("AAPL", was_capped=True, binding_constraint="max_position_weight",
                                     timestamp=datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc))])
    store.record_cap_events([_event("AAPL", was_capped=True, binding_constraint="kelly_cap",
                                     timestamp=datetime(2026, 7, 18, 11, 0, tzinfo=timezone.utc))])

    summary = store.get_consecutive_capped_cycles("AAPL")
    assert isinstance(summary, CapEventSummary)
    assert summary.consecutive_capped_cycles == 3
    assert summary.last_binding_constraint == "kelly_cap"


def test_consecutive_capped_cycles_zero_when_most_recent_is_uncapped():
    store = CapAuditStore(db_url="sqlite:///:memory:")
    store.record_cap_events([_event("AAPL", was_capped=True,
                                     timestamp=datetime(2026, 7, 18, 9, 0, tzinfo=timezone.utc))])
    store.record_cap_events([_event("AAPL", was_capped=False,
                                     timestamp=datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc))])

    summary = store.get_consecutive_capped_cycles("AAPL")
    assert summary.consecutive_capped_cycles == 0


def test_consecutive_capped_cycles_zero_with_no_history():
    store = CapAuditStore(db_url="sqlite:///:memory:")
    summary = store.get_consecutive_capped_cycles("NOSUCHSYMBOL")
    assert summary.consecutive_capped_cycles == 0
    assert summary.last_binding_constraint is None


# ---------------------------------------------------------------------------
# readonly=True
# ---------------------------------------------------------------------------


def test_readonly_store_reads_data_written_by_a_write_mode_store(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'cap_events.db'}"
    writer = CapAuditStore(db_url=db_url)
    writer.record_cap_events([_event()])

    reader = CapAuditStore(db_url=db_url, readonly=True)
    rows = reader.get_recent(limit=10)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "AAPL"


def test_readonly_store_write_raises_rather_than_fabricate_success(tmp_path):
    """CONSTRAINT #4: mirrors TransactionsStore/RunHistoryStore's contract --
    a readonly instance must not silently no-op a write."""
    db_url = f"sqlite:///{tmp_path / 'cap_events.db'}"
    CapAuditStore(db_url=db_url)  # write-mode: creates the schema first
    reader = CapAuditStore(db_url=db_url, readonly=True)
    with pytest.raises(Exception):
        reader.record_cap_events([_event()])


def test_readonly_store_degrades_to_empty_list_on_missing_table(tmp_path):
    """No prior write-mode store has ever run -> the sizing_cap_events table
    doesn't exist. A readonly instance must degrade to [], never crash
    (CONSTRAINT #6)."""
    db_path = tmp_path / "never_written.db"
    db_path.touch()
    reader = CapAuditStore(db_url=f"sqlite:///{db_path}", readonly=True)
    assert reader.get_recent(limit=10) == []
    assert reader.get_recent_for_symbol("AAPL", limit=10) == []
    assert reader.get_consecutive_capped_cycles("AAPL").consecutive_capped_cycles == 0


def test_readonly_store_construction_skips_ddl(tmp_path, monkeypatch):
    """readonly=True must not call Base.metadata.create_all -- a write a
    read-only engine would reject anyway."""
    import sizing.cap_audit_store as store_module

    calls = []
    monkeypatch.setattr(
        store_module.Base.metadata, "create_all",
        lambda *a, **k: calls.append("create_all"),
    )
    CapAuditStore(db_url=f"sqlite:///{tmp_path / 'cap_events.db'}", readonly=True)
    assert calls == []


# ---------------------------------------------------------------------------
# _OfflineCapAuditStore -- the read-only stub used when the configured DB
# backend is unreachable (mirrors transactions_store._OfflineTransactionsStore
# and its tests in tests/test_transactions_store.py).
# ---------------------------------------------------------------------------


class TestOfflineCapAuditStore:
    def test_write_raises(self):
        store = _OfflineCapAuditStore()
        with pytest.raises(Exception):
            store.record_cap_events([_event()])

    def test_reads_degrade_to_empty(self):
        store = _OfflineCapAuditStore()
        assert store.get_recent(limit=10) == []
        assert store.get_recent_for_symbol("AAPL", limit=10) == []

    def test_consecutive_capped_cycles_degrades_to_zero(self):
        store = _OfflineCapAuditStore()
        summary = store.get_consecutive_capped_cycles("AAPL")
        assert summary.consecutive_capped_cycles == 0
        assert summary.last_binding_constraint is None


def test_strategy_engine_cap_audit_store_property_degrades_on_construction_failure(monkeypatch):
    """StrategyEngine.cap_audit_store must not propagate a DB connectivity
    failure out of the lazy-construction property -- regression test for a
    bug where _OfflineCapAuditStore was referenced in the except branch but
    never imported, which would have raised a NameError (masking the real
    connectivity error) the first time CapAuditStore() construction ever
    failed with escalation enabled. Mirrors
    tests/test_transactions_store.py::test_strategy_engine_transactions_store_property_degrades_on_construction_failure."""
    from strategy_engine import StrategyEngine

    def _boom(*args, **kwargs):
        raise ConnectionError("could not translate host name to address")

    monkeypatch.setattr("sizing.cap_audit_store.CapAuditStore", _boom)

    engine = StrategyEngine()
    store = engine.cap_audit_store
    assert isinstance(store, _OfflineCapAuditStore)
    assert engine.cap_audit_store is store  # cached, not reconstructed
