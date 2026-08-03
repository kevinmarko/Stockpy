"""Tests for rlhf_calibration_store.py -- the durable rlhf_calibration_proposals
DB table backing the RLHF Calibration Review Queue.

Mirrors tests/test_cap_audit_store.py's conventions (in-memory SQLite for CRUD,
a tmp_path-backed file DB for readonly=True, missing-table degrade)."""

import pytest

from rlhf_calibration_store import (
    ProposalAlreadyReviewedError,
    ProposalNotFoundError,
    RlhfCalibrationStore,
    _OfflineRlhfCalibrationStore,
)
from settings import settings


def _proposal(**overrides) -> dict:
    defaults = dict(
        symbol="AAPL",
        action="BUY",
        rationale="RSI oversold with bullish sentiment shift.",
        confidence=0.65,
        quantity=10.0,
        price=180.5,
        rsi=28.0,
        sentiment_score=0.4,
        extra_context={"note": "earnings in 3 days"},
    )
    defaults.update(overrides)
    return defaults


@pytest.fixture(autouse=True)
def _auto_approve_off(monkeypatch):
    """Default every test to auto-approve disabled so create_proposal's happy
    path stays 'pending' unless a test explicitly opts in."""
    monkeypatch.setattr(settings, "RLHF_CALIBRATION_AUTO_APPROVE_ENABLED", False)
    monkeypatch.setattr(settings, "RLHF_CALIBRATION_CONFIDENCE_THRESHOLD", 0.8)


# ---------------------------------------------------------------------------
# create_proposal
# ---------------------------------------------------------------------------


def test_create_proposal_happy_path():
    store = RlhfCalibrationStore(db_url="sqlite:///:memory:")
    proposal_id = store.create_proposal(**_proposal())
    assert isinstance(proposal_id, int)

    row = store.get_by_id(proposal_id)
    assert row["symbol"] == "AAPL"
    assert row["action"] == "BUY"
    assert row["rationale"] == "RSI oversold with bullish sentiment shift."
    assert row["confidence"] == pytest.approx(0.65)
    assert row["quantity"] == pytest.approx(10.0)
    assert row["price"] == pytest.approx(180.5)
    assert row["rsi"] == pytest.approx(28.0)
    assert row["sentiment_score"] == pytest.approx(0.4)
    assert row["extra_context"] == {"note": "earnings in 3 days"}
    assert row["status"] == "pending"
    assert row["human_rating"] is None
    assert row["human_correction"] is None
    assert row["reviewed_at"] is None
    assert row["auto_approved"] is False
    assert row["sft_exported"] is False
    assert row["created_at"] is not None


def test_create_proposal_symbol_and_action_normalized():
    store = RlhfCalibrationStore(db_url="sqlite:///:memory:")
    proposal_id = store.create_proposal(**_proposal(symbol="  aapl ", action="buy"))
    row = store.get_by_id(proposal_id)
    assert row["symbol"] == "AAPL"
    assert row["action"] == "BUY"


def test_create_proposal_extra_context_none_stays_none():
    store = RlhfCalibrationStore(db_url="sqlite:///:memory:")
    proposal_id = store.create_proposal(**_proposal(extra_context=None))
    row = store.get_by_id(proposal_id)
    assert row["extra_context"] is None


def test_create_proposal_invalid_action_raises():
    store = RlhfCalibrationStore(db_url="sqlite:///:memory:")
    with pytest.raises(ValueError, match="invalid action"):
        store.create_proposal(**_proposal(action="SHORT"))


def test_create_proposal_invalid_confidence_raises():
    store = RlhfCalibrationStore(db_url="sqlite:///:memory:")
    with pytest.raises(ValueError, match=r"confidence must be in \[0,1\]"):
        store.create_proposal(**_proposal(confidence=1.5))

    with pytest.raises(ValueError, match=r"confidence must be in \[0,1\]"):
        store.create_proposal(**_proposal(confidence=-0.1))


def test_create_proposal_invalid_confidence_non_numeric_raises():
    store = RlhfCalibrationStore(db_url="sqlite:///:memory:")
    with pytest.raises(ValueError, match=r"confidence must be in \[0,1\]"):
        store.create_proposal(**_proposal(confidence="not-a-number"))


def test_create_proposal_rejects_on_validation_error_without_writing_a_row():
    store = RlhfCalibrationStore(db_url="sqlite:///:memory:")
    with pytest.raises(ValueError):
        store.create_proposal(**_proposal(action="INVALID"))
    assert store.get_pending(limit=10) == []


# ---------------------------------------------------------------------------
# Auto-approve threshold behavior
# ---------------------------------------------------------------------------


def test_auto_approve_disabled_stays_pending_even_above_threshold(monkeypatch):
    monkeypatch.setattr(settings, "RLHF_CALIBRATION_AUTO_APPROVE_ENABLED", False)
    monkeypatch.setattr(settings, "RLHF_CALIBRATION_CONFIDENCE_THRESHOLD", 0.8)
    store = RlhfCalibrationStore(db_url="sqlite:///:memory:")
    proposal_id = store.create_proposal(**_proposal(confidence=0.95))
    row = store.get_by_id(proposal_id)
    assert row["status"] == "pending"
    assert row["auto_approved"] is False
    assert row["human_rating"] is None
    assert row["reviewed_at"] is None


def test_auto_approve_enabled_above_threshold_marks_reviewed(monkeypatch):
    monkeypatch.setattr(settings, "RLHF_CALIBRATION_AUTO_APPROVE_ENABLED", True)
    monkeypatch.setattr(settings, "RLHF_CALIBRATION_CONFIDENCE_THRESHOLD", 0.8)
    store = RlhfCalibrationStore(db_url="sqlite:///:memory:")
    proposal_id = store.create_proposal(**_proposal(confidence=0.95))
    row = store.get_by_id(proposal_id)
    assert row["status"] == "reviewed"
    assert row["auto_approved"] is True
    assert row["human_rating"] is None  # never a fabricated rating
    assert row["reviewed_at"] is not None


def test_auto_approve_enabled_below_threshold_stays_pending(monkeypatch):
    monkeypatch.setattr(settings, "RLHF_CALIBRATION_AUTO_APPROVE_ENABLED", True)
    monkeypatch.setattr(settings, "RLHF_CALIBRATION_CONFIDENCE_THRESHOLD", 0.8)
    store = RlhfCalibrationStore(db_url="sqlite:///:memory:")
    proposal_id = store.create_proposal(**_proposal(confidence=0.5))
    row = store.get_by_id(proposal_id)
    assert row["status"] == "pending"
    assert row["auto_approved"] is False


def test_auto_approve_enabled_exactly_at_threshold_approves(monkeypatch):
    """confidence >= threshold -- the boundary is inclusive."""
    monkeypatch.setattr(settings, "RLHF_CALIBRATION_AUTO_APPROVE_ENABLED", True)
    monkeypatch.setattr(settings, "RLHF_CALIBRATION_CONFIDENCE_THRESHOLD", 0.8)
    store = RlhfCalibrationStore(db_url="sqlite:///:memory:")
    proposal_id = store.create_proposal(**_proposal(confidence=0.8))
    row = store.get_by_id(proposal_id)
    assert row["status"] == "reviewed"
    assert row["auto_approved"] is True


# ---------------------------------------------------------------------------
# get_pending
# ---------------------------------------------------------------------------


def test_get_pending_empty_db():
    store = RlhfCalibrationStore(db_url="sqlite:///:memory:")
    assert store.get_pending(limit=10) == []


def test_get_pending_excludes_reviewed():
    store = RlhfCalibrationStore(db_url="sqlite:///:memory:")
    pending_id = store.create_proposal(**_proposal(symbol="AAPL"))
    reviewed_id = store.create_proposal(**_proposal(symbol="MSFT"))
    store.submit_review(reviewed_id, human_rating=4)

    rows = store.get_pending(limit=10)
    assert [r["id"] for r in rows] == [pending_id]


def test_get_pending_most_recent_first():
    store = RlhfCalibrationStore(db_url="sqlite:///:memory:")
    first_id = store.create_proposal(**_proposal(symbol="AAPL"))
    second_id = store.create_proposal(**_proposal(symbol="MSFT"))
    third_id = store.create_proposal(**_proposal(symbol="GOOG"))

    rows = store.get_pending(limit=10)
    assert [r["id"] for r in rows] == [third_id, second_id, first_id]


def test_get_pending_respects_limit():
    store = RlhfCalibrationStore(db_url="sqlite:///:memory:")
    for i in range(5):
        store.create_proposal(**_proposal(symbol=f"SYM{i}"))
    rows = store.get_pending(limit=2)
    assert len(rows) == 2


def test_get_by_id_missing_returns_none():
    store = RlhfCalibrationStore(db_url="sqlite:///:memory:")
    assert store.get_by_id(999) is None


# ---------------------------------------------------------------------------
# submit_review
# ---------------------------------------------------------------------------


def test_submit_review_happy_path():
    store = RlhfCalibrationStore(db_url="sqlite:///:memory:")
    proposal_id = store.create_proposal(**_proposal())

    result = store.submit_review(proposal_id, human_rating=5, human_correction="Good call.")
    assert result["status"] == "reviewed"
    assert result["human_rating"] == 5
    assert result["human_correction"] == "Good call."
    assert result["reviewed_at"] is not None

    row = store.get_by_id(proposal_id)
    assert row["status"] == "reviewed"
    assert row["human_rating"] == 5


def test_submit_review_no_correction_is_optional():
    store = RlhfCalibrationStore(db_url="sqlite:///:memory:")
    proposal_id = store.create_proposal(**_proposal())
    result = store.submit_review(proposal_id, human_rating=3)
    assert result["human_correction"] is None


def test_submit_review_not_found_raises():
    store = RlhfCalibrationStore(db_url="sqlite:///:memory:")
    with pytest.raises(ProposalNotFoundError):
        store.submit_review(999, human_rating=3)


def test_submit_review_already_reviewed_raises():
    store = RlhfCalibrationStore(db_url="sqlite:///:memory:")
    proposal_id = store.create_proposal(**_proposal())
    store.submit_review(proposal_id, human_rating=3)
    with pytest.raises(ProposalAlreadyReviewedError):
        store.submit_review(proposal_id, human_rating=5)


def test_submit_review_already_reviewed_via_auto_approval_raises(monkeypatch):
    monkeypatch.setattr(settings, "RLHF_CALIBRATION_AUTO_APPROVE_ENABLED", True)
    monkeypatch.setattr(settings, "RLHF_CALIBRATION_CONFIDENCE_THRESHOLD", 0.8)
    store = RlhfCalibrationStore(db_url="sqlite:///:memory:")
    proposal_id = store.create_proposal(**_proposal(confidence=0.9))
    with pytest.raises(ProposalAlreadyReviewedError):
        store.submit_review(proposal_id, human_rating=5)


def test_submit_review_bad_rating_raises_value_error():
    store = RlhfCalibrationStore(db_url="sqlite:///:memory:")
    proposal_id = store.create_proposal(**_proposal())
    with pytest.raises(ValueError, match="human_rating"):
        store.submit_review(proposal_id, human_rating=0)
    with pytest.raises(ValueError, match="human_rating"):
        store.submit_review(proposal_id, human_rating=6)

    # A rejected bad-rating attempt must not have mutated the row.
    row = store.get_by_id(proposal_id)
    assert row["status"] == "pending"
    assert row["human_rating"] is None


# ---------------------------------------------------------------------------
# mark_sft_exported
# ---------------------------------------------------------------------------


def test_mark_sft_exported_updates_given_ids_only():
    store = RlhfCalibrationStore(db_url="sqlite:///:memory:")
    id_a = store.create_proposal(**_proposal(symbol="AAPL"))
    id_b = store.create_proposal(**_proposal(symbol="MSFT"))
    id_c = store.create_proposal(**_proposal(symbol="GOOG"))

    updated = store.mark_sft_exported([id_a, id_c])
    assert updated == 2

    assert store.get_by_id(id_a)["sft_exported"] is True
    assert store.get_by_id(id_b)["sft_exported"] is False
    assert store.get_by_id(id_c)["sft_exported"] is True


def test_mark_sft_exported_empty_list_is_a_noop():
    store = RlhfCalibrationStore(db_url="sqlite:///:memory:")
    assert store.mark_sft_exported([]) == 0


# ---------------------------------------------------------------------------
# get_summary_stats
# ---------------------------------------------------------------------------


def test_get_summary_stats_empty_db():
    store = RlhfCalibrationStore(db_url="sqlite:///:memory:")
    stats = store.get_summary_stats()
    assert stats == {
        "pending_count": 0,
        "reviewed_count": 0,
        "average_human_rating": None,
        "rating_distribution": {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0},
        "auto_approved_count": 0,
        "sft_exported_count": 0,
    }


def test_get_summary_stats_excludes_auto_approved_unrated_rows_from_average(monkeypatch):
    monkeypatch.setattr(settings, "RLHF_CALIBRATION_AUTO_APPROVE_ENABLED", True)
    monkeypatch.setattr(settings, "RLHF_CALIBRATION_CONFIDENCE_THRESHOLD", 0.8)
    store = RlhfCalibrationStore(db_url="sqlite:///:memory:")

    # Two auto-approved (unrated) rows -- must not pull the average toward 0.
    store.create_proposal(**_proposal(symbol="AAA", confidence=0.9))
    store.create_proposal(**_proposal(symbol="BBB", confidence=0.85))

    # Two humanly-rated rows.
    monkeypatch.setattr(settings, "RLHF_CALIBRATION_AUTO_APPROVE_ENABLED", False)
    id_c = store.create_proposal(**_proposal(symbol="CCC", confidence=0.5))
    id_d = store.create_proposal(**_proposal(symbol="DDD", confidence=0.5))
    store.submit_review(id_c, human_rating=4)
    store.submit_review(id_d, human_rating=2)

    stats = store.get_summary_stats()
    assert stats["auto_approved_count"] == 2
    assert stats["reviewed_count"] == 4  # 2 auto-approved + 2 human-reviewed
    assert stats["pending_count"] == 0
    assert stats["average_human_rating"] == pytest.approx(3.0)  # (4 + 2) / 2, NOT /4
    assert stats["rating_distribution"] == {"1": 0, "2": 1, "3": 0, "4": 1, "5": 0}


def test_get_summary_stats_pending_and_sft_exported_counts():
    store = RlhfCalibrationStore(db_url="sqlite:///:memory:")
    id_a = store.create_proposal(**_proposal(symbol="AAA"))
    store.create_proposal(**_proposal(symbol="BBB"))
    store.submit_review(id_a, human_rating=5)
    store.mark_sft_exported([id_a])

    stats = store.get_summary_stats()
    assert stats["pending_count"] == 1
    assert stats["reviewed_count"] == 1
    assert stats["sft_exported_count"] == 1
    assert stats["average_human_rating"] == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# readonly=True
# ---------------------------------------------------------------------------


def test_readonly_store_reads_data_written_by_a_write_mode_store(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'rlhf.db'}"
    writer = RlhfCalibrationStore(db_url=db_url)
    writer.create_proposal(**_proposal())

    reader = RlhfCalibrationStore(db_url=db_url, readonly=True)
    rows = reader.get_pending(limit=10)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "AAPL"


def test_readonly_store_write_methods_raise_rather_than_fabricate_success(tmp_path):
    """CONSTRAINT #4: mirrors TransactionsStore/CapAuditStore's contract -- a
    readonly instance must not silently no-op a write."""
    db_url = f"sqlite:///{tmp_path / 'rlhf.db'}"
    writer = RlhfCalibrationStore(db_url=db_url)  # write-mode: creates the schema first
    proposal_id = writer.create_proposal(**_proposal())

    reader = RlhfCalibrationStore(db_url=db_url, readonly=True)
    with pytest.raises(Exception):
        reader.create_proposal(**_proposal())
    with pytest.raises(Exception):
        reader.submit_review(proposal_id, human_rating=5)
    with pytest.raises(Exception):
        reader.mark_sft_exported([proposal_id])


def test_readonly_store_degrades_gracefully_on_missing_table(tmp_path):
    """No prior write-mode store has ever run -> the rlhf_calibration_proposals
    table doesn't exist. A readonly instance must degrade to an honest empty
    shape, never crash (CONSTRAINT #6)."""
    db_path = tmp_path / "never_written.db"
    db_path.touch()
    reader = RlhfCalibrationStore(db_url=f"sqlite:///{db_path}", readonly=True)

    assert reader.get_pending(limit=10) == []
    assert reader.get_by_id(1) is None
    assert reader.get_summary_stats() == {
        "pending_count": 0,
        "reviewed_count": 0,
        "average_human_rating": None,
        "rating_distribution": {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0},
        "auto_approved_count": 0,
        "sft_exported_count": 0,
    }


def test_readonly_store_construction_skips_ddl(tmp_path, monkeypatch):
    """readonly=True must not call Base.metadata.create_all -- a write a
    read-only engine would reject anyway."""
    import rlhf_calibration_store as store_module

    calls = []
    monkeypatch.setattr(
        store_module.Base.metadata, "create_all",
        lambda *a, **k: calls.append("create_all"),
    )
    RlhfCalibrationStore(db_url=f"sqlite:///{tmp_path / 'rlhf.db'}", readonly=True)
    assert calls == []


# ---------------------------------------------------------------------------
# _OfflineRlhfCalibrationStore -- the read-only stub used when the configured
# DB backend is unreachable (mirrors transactions_store._OfflineTransactionsStore
# / sizing.cap_audit_store._OfflineCapAuditStore).
# ---------------------------------------------------------------------------


class TestOfflineRlhfCalibrationStore:
    def test_writes_raise(self):
        store = _OfflineRlhfCalibrationStore()
        with pytest.raises(Exception):
            store.create_proposal(**_proposal())
        with pytest.raises(Exception):
            store.submit_review(1, human_rating=5)
        with pytest.raises(Exception):
            store.mark_sft_exported([1])

    def test_reads_degrade_to_empty(self):
        store = _OfflineRlhfCalibrationStore()
        assert store.get_pending(limit=10) == []
        assert store.get_by_id(1) is None
        assert store.get_summary_stats() == {
            "pending_count": 0,
            "reviewed_count": 0,
            "average_human_rating": None,
            "rating_distribution": {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0},
            "auto_approved_count": 0,
            "sft_exported_count": 0,
        }
