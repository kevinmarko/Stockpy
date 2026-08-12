"""Tests for execution/live_trade_proposals_store.py -- the durable
live_trade_proposals DB table backing the human-in-the-loop approval gate
between broker_live_execution_mcp.py's execute_live_trade and confirm_live_trade.

Mirrors tests/test_rlhf_calibration_store.py's conventions (in-memory SQLite
for CRUD, a tmp_path-backed file DB for readonly=True, missing-table degrade)."""

from datetime import datetime, timedelta, timezone

import pytest

from execution.live_trade_proposals_store import (
    LiveTradeProposal,
    LiveTradeProposalAlreadyDecidedError,
    LiveTradeProposalNotFoundError,
    LiveTradeProposalStore,
    PROPOSAL_TTL_SECONDS,
)


def _proposal(**overrides) -> dict:
    defaults = dict(
        symbol="AAPL",
        side="buy",
        qty=10.0,
        order_type="market",
    )
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# create_proposal
# ---------------------------------------------------------------------------


def test_create_proposal_happy_path():
    store = LiveTradeProposalStore(db_url="sqlite:///:memory:")
    token = store.create_proposal(**_proposal())
    assert isinstance(token, str)
    assert len(token) == 32  # uuid4().hex

    row = store.get_by_token(token)
    assert row is not None
    assert row.symbol == "AAPL"
    assert row.side == "buy"
    assert row.qty == pytest.approx(10.0)
    assert row.order_type == "market"
    assert row.limit_price is None
    assert row.strategy_id == "mcp-agent"
    assert row.status == "pending_approval"
    assert row.approved_at is None
    assert row.approved_by is None
    assert row.broker_order_id is None
    assert row.error_message is None
    assert row.proposed_at is not None
    assert row.expires_at is not None


def test_create_proposal_normalizes_symbol_and_side():
    store = LiveTradeProposalStore(db_url="sqlite:///:memory:")
    token = store.create_proposal(**_proposal(symbol="  aapl ", side="BUY"))
    row = store.get_by_token(token)
    assert row.symbol == "AAPL"
    assert row.side == "buy"


def test_create_proposal_limit_order_stores_limit_price():
    store = LiveTradeProposalStore(db_url="sqlite:///:memory:")
    token = store.create_proposal(**_proposal(order_type="limit", limit_price=123.45))
    row = store.get_by_token(token)
    assert row.order_type == "limit"
    assert row.limit_price == pytest.approx(123.45)


def test_create_proposal_custom_strategy_id():
    store = LiveTradeProposalStore(db_url="sqlite:///:memory:")
    token = store.create_proposal(**_proposal(strategy_id="custom-strat"))
    row = store.get_by_token(token)
    assert row.strategy_id == "custom-strat"


def test_create_proposal_expires_at_is_ttl_seconds_after_proposed_at():
    store = LiveTradeProposalStore(db_url="sqlite:///:memory:")
    token = store.create_proposal(**_proposal())
    row = store.get_by_token(token)
    delta = (row.expires_at - row.proposed_at).total_seconds()
    assert delta == pytest.approx(PROPOSAL_TTL_SECONDS, abs=1)


def test_create_proposal_empty_symbol_raises():
    store = LiveTradeProposalStore(db_url="sqlite:///:memory:")
    with pytest.raises(ValueError, match="invalid symbol"):
        store.create_proposal(**_proposal(symbol=""))


def test_create_proposal_empty_side_raises():
    store = LiveTradeProposalStore(db_url="sqlite:///:memory:")
    with pytest.raises(ValueError, match="invalid side"):
        store.create_proposal(**_proposal(side=""))


def test_create_proposal_non_positive_qty_raises():
    store = LiveTradeProposalStore(db_url="sqlite:///:memory:")
    with pytest.raises(ValueError, match="qty must be > 0"):
        store.create_proposal(**_proposal(qty=0))
    with pytest.raises(ValueError, match="qty must be > 0"):
        store.create_proposal(**_proposal(qty=-5))


def test_create_proposal_non_numeric_qty_raises():
    store = LiveTradeProposalStore(db_url="sqlite:///:memory:")
    with pytest.raises(ValueError, match="qty must be > 0"):
        store.create_proposal(**_proposal(qty="not-a-number"))


# ---------------------------------------------------------------------------
# get_by_token
# ---------------------------------------------------------------------------


def test_get_by_token_missing_returns_none():
    store = LiveTradeProposalStore(db_url="sqlite:///:memory:")
    assert store.get_by_token("does-not-exist") is None


# ---------------------------------------------------------------------------
# get_pending
# ---------------------------------------------------------------------------


def test_get_pending_empty_db():
    store = LiveTradeProposalStore(db_url="sqlite:///:memory:")
    assert store.get_pending(limit=10) == []


def test_get_pending_excludes_approved_and_rejected():
    store = LiveTradeProposalStore(db_url="sqlite:///:memory:")
    pending_token = store.create_proposal(**_proposal(symbol="AAPL"))
    approved_token = store.create_proposal(**_proposal(symbol="MSFT"))
    rejected_token = store.create_proposal(**_proposal(symbol="GOOG"))
    store.approve_proposal(approved_token)
    store.reject_proposal(rejected_token)

    rows = store.get_pending(limit=10)
    assert [r.token for r in rows] == [pending_token]


def test_get_pending_most_recent_first():
    store = LiveTradeProposalStore(db_url="sqlite:///:memory:")
    first = store.create_proposal(**_proposal(symbol="AAPL"))
    second = store.create_proposal(**_proposal(symbol="MSFT"))
    third = store.create_proposal(**_proposal(symbol="GOOG"))

    rows = store.get_pending(limit=10)
    assert [r.token for r in rows] == [third, second, first]


def test_get_pending_respects_limit():
    store = LiveTradeProposalStore(db_url="sqlite:///:memory:")
    for i in range(5):
        store.create_proposal(**_proposal(symbol=f"SYM{i}"))
    rows = store.get_pending(limit=2)
    assert len(rows) == 2


def test_get_pending_excludes_expired():
    store = LiveTradeProposalStore(db_url="sqlite:///:memory:")
    token = store.create_proposal(**_proposal())
    # Force expiry directly via the ORM session (bypassing the public API,
    # which has no "set expires_at" write path -- this mirrors how a real
    # proposal ages out under wall-clock time).
    with store.Session() as session:
        row = session.query(LiveTradeProposal).filter_by(token=token).first()
        row.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        session.commit()

    assert store.get_pending(limit=10) == []


# ---------------------------------------------------------------------------
# approve_proposal / reject_proposal
# ---------------------------------------------------------------------------


def test_approve_proposal_happy_path():
    store = LiveTradeProposalStore(db_url="sqlite:///:memory:")
    token = store.create_proposal(**_proposal())

    row = store.approve_proposal(token)
    assert row.status == "approved"
    assert row.approved_by == "operator"
    assert row.approved_at is not None

    fetched = store.get_by_token(token)
    assert fetched.status == "approved"


def test_approve_proposal_custom_approved_by():
    store = LiveTradeProposalStore(db_url="sqlite:///:memory:")
    token = store.create_proposal(**_proposal())
    row = store.approve_proposal(token, approved_by="kevin")
    assert row.approved_by == "kevin"


def test_reject_proposal_happy_path():
    store = LiveTradeProposalStore(db_url="sqlite:///:memory:")
    token = store.create_proposal(**_proposal())

    row = store.reject_proposal(token)
    assert row.status == "rejected"
    # approved_at/approved_by stay null on rejection.
    assert row.approved_at is None
    assert row.approved_by is None


def test_approve_proposal_not_found_raises():
    store = LiveTradeProposalStore(db_url="sqlite:///:memory:")
    with pytest.raises(LiveTradeProposalNotFoundError):
        store.approve_proposal("does-not-exist")


def test_reject_proposal_not_found_raises():
    store = LiveTradeProposalStore(db_url="sqlite:///:memory:")
    with pytest.raises(LiveTradeProposalNotFoundError):
        store.reject_proposal("does-not-exist")


def test_approve_proposal_already_approved_raises():
    store = LiveTradeProposalStore(db_url="sqlite:///:memory:")
    token = store.create_proposal(**_proposal())
    store.approve_proposal(token)
    with pytest.raises(LiveTradeProposalAlreadyDecidedError):
        store.approve_proposal(token)


def test_approve_proposal_after_reject_raises():
    store = LiveTradeProposalStore(db_url="sqlite:///:memory:")
    token = store.create_proposal(**_proposal())
    store.reject_proposal(token)
    with pytest.raises(LiveTradeProposalAlreadyDecidedError):
        store.approve_proposal(token)


def test_reject_proposal_after_approve_raises():
    store = LiveTradeProposalStore(db_url="sqlite:///:memory:")
    token = store.create_proposal(**_proposal())
    store.approve_proposal(token)
    with pytest.raises(LiveTradeProposalAlreadyDecidedError):
        store.reject_proposal(token)


def test_approve_proposal_after_expiry_raises():
    store = LiveTradeProposalStore(db_url="sqlite:///:memory:")
    token = store.create_proposal(**_proposal())
    with store.Session() as session:
        row = session.query(LiveTradeProposal).filter_by(token=token).first()
        row.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        session.commit()

    with pytest.raises(LiveTradeProposalAlreadyDecidedError):
        store.approve_proposal(token)

    # The lazy-expiry side effect must have actually persisted the status.
    fetched = store.get_by_token(token)
    assert fetched.status == "expired"


# ---------------------------------------------------------------------------
# lazy expiry via get_by_token
# ---------------------------------------------------------------------------


def test_get_by_token_lazily_expires_stale_pending_row():
    store = LiveTradeProposalStore(db_url="sqlite:///:memory:")
    token = store.create_proposal(**_proposal())
    with store.Session() as session:
        row = session.query(LiveTradeProposal).filter_by(token=token).first()
        row.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        session.commit()

    fetched = store.get_by_token(token)
    assert fetched.status == "expired"

    # Persisted, not just returned transiently.
    with store.Session() as session:
        row = session.query(LiveTradeProposal).filter_by(token=token).first()
        assert row.status == "expired"


def test_get_by_token_does_not_expire_a_row_still_within_ttl():
    store = LiveTradeProposalStore(db_url="sqlite:///:memory:")
    token = store.create_proposal(**_proposal())
    fetched = store.get_by_token(token)
    assert fetched.status == "pending_approval"


def test_get_by_token_does_not_touch_an_already_decided_row():
    store = LiveTradeProposalStore(db_url="sqlite:///:memory:")
    token = store.create_proposal(**_proposal())
    store.approve_proposal(token)
    with store.Session() as session:
        row = session.query(LiveTradeProposal).filter_by(token=token).first()
        row.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        session.commit()

    # Already approved -- expiry must not retroactively flip an approved row.
    fetched = store.get_by_token(token)
    assert fetched.status == "approved"


# ---------------------------------------------------------------------------
# mark_executed / mark_failed
# ---------------------------------------------------------------------------


def test_mark_executed_sets_status_and_broker_order_id():
    store = LiveTradeProposalStore(db_url="sqlite:///:memory:")
    token = store.create_proposal(**_proposal())
    store.approve_proposal(token)
    store.mark_executed(token, "broker-order-123")

    row = store.get_by_token(token)
    assert row.status == "executed"
    assert row.broker_order_id == "broker-order-123"


def test_mark_failed_sets_status_and_error_message():
    store = LiveTradeProposalStore(db_url="sqlite:///:memory:")
    token = store.create_proposal(**_proposal())
    store.approve_proposal(token)
    store.mark_failed(token, "insufficient buying power")

    row = store.get_by_token(token)
    assert row.status == "failed"
    assert row.error_message == "insufficient buying power"


def test_mark_executed_missing_token_raises():
    store = LiveTradeProposalStore(db_url="sqlite:///:memory:")
    with pytest.raises(LiveTradeProposalNotFoundError):
        store.mark_executed("does-not-exist", "broker-order-1")


def test_mark_failed_missing_token_raises():
    store = LiveTradeProposalStore(db_url="sqlite:///:memory:")
    with pytest.raises(LiveTradeProposalNotFoundError):
        store.mark_failed("does-not-exist", "boom")


# ---------------------------------------------------------------------------
# readonly=True
# ---------------------------------------------------------------------------


def test_readonly_store_reads_data_written_by_a_write_mode_store(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'live_trade.db'}"
    writer = LiveTradeProposalStore(db_url=db_url)
    token = writer.create_proposal(**_proposal())

    reader = LiveTradeProposalStore(db_url=db_url, readonly=True)
    rows = reader.get_pending(limit=10)
    assert len(rows) == 1
    assert rows[0].symbol == "AAPL"
    assert reader.get_by_token(token).symbol == "AAPL"


def test_readonly_store_write_methods_raise_rather_than_fabricate_success(tmp_path):
    """CONSTRAINT #4: a readonly instance must not silently no-op a write."""
    db_url = f"sqlite:///{tmp_path / 'live_trade.db'}"
    writer = LiveTradeProposalStore(db_url=db_url)  # write-mode: creates the schema first
    token = writer.create_proposal(**_proposal())

    reader = LiveTradeProposalStore(db_url=db_url, readonly=True)
    with pytest.raises(RuntimeError):
        reader.create_proposal(**_proposal())
    with pytest.raises(RuntimeError):
        reader.approve_proposal(token)
    with pytest.raises(RuntimeError):
        reader.reject_proposal(token)
    with pytest.raises(RuntimeError):
        reader.mark_executed(token, "broker-order-1")
    with pytest.raises(RuntimeError):
        reader.mark_failed(token, "boom")


def test_readonly_store_degrades_gracefully_on_missing_table(tmp_path):
    """No prior write-mode store has ever run -> the live_trade_proposals
    table doesn't exist. A readonly instance must degrade to an honest empty
    shape, never crash (CONSTRAINT #6)."""
    db_path = tmp_path / "never_written.db"
    db_path.touch()
    reader = LiveTradeProposalStore(db_url=f"sqlite:///{db_path}", readonly=True)

    assert reader.get_pending(limit=10) == []
    assert reader.get_by_token("anything") is None


def test_readonly_store_construction_skips_ddl(tmp_path, monkeypatch):
    """readonly=True must not call Base.metadata.create_all -- a write a
    read-only engine would reject anyway."""
    import execution.live_trade_proposals_store as store_module

    calls = []
    monkeypatch.setattr(
        store_module.Base.metadata, "create_all",
        lambda *a, **k: calls.append("create_all"),
    )
    LiveTradeProposalStore(db_url=f"sqlite:///{tmp_path / 'live_trade.db'}", readonly=True)
    assert calls == []
