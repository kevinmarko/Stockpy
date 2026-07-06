"""Tests for gui/robinhood_execution_panel.py — Tier 8 execution bridge read side.

Covers:
- QueuedIntent / ExecutionQueueSnapshot dataclass contracts.
- read_execution_queue() against valid, corrupt, empty, and missing files.
- read_execution_receipts() tail behavior + malformed-line tolerance.
- queue_age_seconds() / is_queue_stale() against fixed clocks.
- mfa_secret_configured() truth table.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from gui.robinhood_execution_panel import (
    EXECUTION_PLACED_PATH,
    EXECUTION_QUEUE_PATH,
    EXECUTION_RECEIPTS_PATH,
    NOTIFIED_STATE_PATH,
    STATUS_BLOCKED,
    STATUS_PLACED,
    STATUS_PREVIEWED,
    STATUS_QUEUED,
    STATUS_SKIPPED,
    ExecutionQueueSnapshot,
    IntentStatus,
    NotificationState,
    QueuedIntent,
    ReconciliationSummary,
    STALE_QUEUE_SECONDS,
    build_reconciliation_summary,
    derive_intent_status,
    is_queue_stale,
    mfa_secret_configured,
    notification_age_seconds,
    ntfy_topic_configured,
    queue_age_seconds,
    read_execution_queue,
    read_execution_receipts,
    read_notification_state,
    read_placed_ledger,
)


def _make_intent(**overrides) -> QueuedIntent:
    base = dict(
        symbol="AAPL", action="BUY", side="buy", qty=None, target_notional=25.0,
        conviction=0.85, gate_allowed=True, gate_reasons=[], allow_place=False,
        rationale="x", client_order_id="1",
    )
    base.update(overrides)
    return QueuedIntent(**base)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _sample_intent(**overrides) -> dict:
    base = {
        "client_order_id": "abc123",
        "symbol": "aapl",
        "action": "BUY",
        "side": "buy",
        "qty": None,
        "target_notional": 25.0,
        "conviction": 0.85,
        "gate_allowed": True,
        "gate_reasons": [],
        "allow_place": False,
        "rationale": "test rationale",
    }
    base.update(overrides)
    return base


def _sample_payload(*, mode="review", intents=None, generated_at=None) -> dict:
    intents = intents if intents is not None else [_sample_intent()]
    return {
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "kill_switch_active": False,
        "max_notional_per_order": 25.0,
        "n_intents": len(intents),
        "n_placeable": sum(1 for i in intents if isinstance(i, dict) and i.get("allow_place")),
        "intents": intents,
    }


def _write_json(tmp_path: Path, payload) -> Path:
    p = tmp_path / "execution_queue.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------

class TestModuleSurface:
    def test_canonical_paths_point_at_output_dir(self):
        assert EXECUTION_QUEUE_PATH.name == "execution_queue.json"
        assert EXECUTION_RECEIPTS_PATH.name == "execution_receipts.jsonl"
        assert EXECUTION_QUEUE_PATH.parent.name == "output"
        assert EXECUTION_RECEIPTS_PATH.parent.name == "output"

    def test_stale_threshold_is_thirty_minutes(self):
        assert STALE_QUEUE_SECONDS == pytest.approx(30 * 60.0)

    def test_dataclasses_are_frozen(self):
        intent = QueuedIntent(
            symbol="AAPL", action="BUY", side="buy", qty=None, target_notional=25.0,
            conviction=0.85, gate_allowed=True, gate_reasons=[], allow_place=False,
            rationale="x", client_order_id="1",
        )
        with pytest.raises(Exception):
            intent.symbol = "MSFT"  # type: ignore[misc]

        snap = ExecutionQueueSnapshot(
            generated_at="2026-01-01T00:00:00+00:00", mode="review",
            kill_switch_active=False, max_notional_per_order=25.0,
            n_intents=0, n_placeable=0, intents=[],
        )
        with pytest.raises(Exception):
            snap.mode = "live"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# read_execution_queue
# ---------------------------------------------------------------------------

class TestReadExecutionQueue:
    def test_missing_file_returns_none(self, tmp_path):
        assert read_execution_queue(tmp_path / "nope.json") is None

    def test_empty_file_returns_none(self, tmp_path):
        p = tmp_path / "execution_queue.json"
        p.write_text("", encoding="utf-8")
        assert read_execution_queue(p) is None

    def test_corrupt_json_returns_none(self, tmp_path):
        p = tmp_path / "execution_queue.json"
        p.write_text("{not json", encoding="utf-8")
        assert read_execution_queue(p) is None

    def test_non_object_json_returns_none(self, tmp_path):
        p = tmp_path / "execution_queue.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        assert read_execution_queue(p) is None

    def test_valid_payload_round_trips(self, tmp_path):
        payload = _sample_payload(mode="review")
        p = _write_json(tmp_path, payload)
        snap = read_execution_queue(p)
        assert snap is not None
        assert snap.mode == "review"
        assert snap.n_intents == 1
        assert len(snap.intents) == 1
        assert snap.intents[0].symbol == "AAPL"  # upper-cased

    def test_malformed_single_intent_is_skipped_not_fatal(self, tmp_path):
        payload = _sample_payload(intents=[_sample_intent(), "not-a-dict"])
        p = _write_json(tmp_path, payload)
        snap = read_execution_queue(p)
        assert snap is not None
        assert len(snap.intents) == 1

    def test_empty_intents_list(self, tmp_path):
        payload = _sample_payload(intents=[])
        p = _write_json(tmp_path, payload)
        snap = read_execution_queue(p)
        assert snap is not None
        assert snap.intents == []
        assert snap.n_intents == 0

    def test_n_placeable_reflects_allow_place_flags(self, tmp_path):
        payload = _sample_payload(
            intents=[_sample_intent(symbol="AAPL", allow_place=True),
                     _sample_intent(symbol="MSFT", allow_place=False)]
        )
        p = _write_json(tmp_path, payload)
        snap = read_execution_queue(p)
        assert snap.n_placeable == 1

    def test_default_path_used_when_none_given(self, monkeypatch, tmp_path):
        fake_path = tmp_path / "execution_queue.json"
        _write_json(tmp_path, _sample_payload())
        import gui.robinhood_execution_panel as mod
        monkeypatch.setattr(mod, "EXECUTION_QUEUE_PATH", fake_path)
        snap = read_execution_queue()
        assert snap is not None


# ---------------------------------------------------------------------------
# read_execution_receipts
# ---------------------------------------------------------------------------

class TestReadExecutionReceipts:
    def test_missing_file_returns_empty_list(self, tmp_path):
        assert read_execution_receipts(tmp_path / "nope.jsonl") == []

    def test_valid_lines_round_trip(self, tmp_path):
        p = tmp_path / "execution_receipts.jsonl"
        lines = [
            json.dumps({"ts": "t1", "symbol": "AAPL", "action": "placed"}),
            json.dumps({"ts": "t2", "symbol": "MSFT", "action": "skipped"}),
        ]
        p.write_text("\n".join(lines), encoding="utf-8")
        entries = read_execution_receipts(p)
        assert len(entries) == 2
        assert entries[0]["symbol"] == "AAPL"

    def test_malformed_lines_are_skipped(self, tmp_path):
        p = tmp_path / "execution_receipts.jsonl"
        p.write_text(
            "{not json}\n" + json.dumps({"symbol": "AAPL"}) + "\n\n[1,2]\n",
            encoding="utf-8",
        )
        entries = read_execution_receipts(p)
        assert len(entries) == 1
        assert entries[0]["symbol"] == "AAPL"

    def test_max_lines_tails_the_file(self, tmp_path):
        p = tmp_path / "execution_receipts.jsonl"
        lines = [json.dumps({"symbol": f"SYM{i}"}) for i in range(10)]
        p.write_text("\n".join(lines), encoding="utf-8")
        entries = read_execution_receipts(p, max_lines=3)
        assert len(entries) == 3
        assert entries[-1]["symbol"] == "SYM9"

    def test_empty_file_returns_empty_list(self, tmp_path):
        p = tmp_path / "execution_receipts.jsonl"
        p.write_text("", encoding="utf-8")
        assert read_execution_receipts(p) == []


# ---------------------------------------------------------------------------
# queue_age_seconds / is_queue_stale
# ---------------------------------------------------------------------------

class TestQueueAge:
    def test_fresh_queue_age_near_zero(self):
        now = datetime.now(timezone.utc)
        snap = ExecutionQueueSnapshot(
            generated_at=now.isoformat(), mode="review", kill_switch_active=False,
            max_notional_per_order=25.0, n_intents=0, n_placeable=0, intents=[],
        )
        age = queue_age_seconds(snap, now=now + timedelta(seconds=5))
        assert age == pytest.approx(5.0, abs=1.0)

    def test_unparsable_timestamp_returns_nan(self):
        snap = ExecutionQueueSnapshot(
            generated_at="not-a-timestamp", mode="review", kill_switch_active=False,
            max_notional_per_order=25.0, n_intents=0, n_placeable=0, intents=[],
        )
        age = queue_age_seconds(snap)
        assert age != age  # NaN

    def test_fresh_queue_is_not_stale(self):
        now = datetime.now(timezone.utc)
        snap = ExecutionQueueSnapshot(
            generated_at=now.isoformat(), mode="review", kill_switch_active=False,
            max_notional_per_order=25.0, n_intents=0, n_placeable=0, intents=[],
        )
        assert is_queue_stale(snap, now=now) is False

    def test_old_queue_is_stale(self):
        now = datetime.now(timezone.utc)
        old = now - timedelta(minutes=45)
        snap = ExecutionQueueSnapshot(
            generated_at=old.isoformat(), mode="review", kill_switch_active=False,
            max_notional_per_order=25.0, n_intents=0, n_placeable=0, intents=[],
        )
        assert is_queue_stale(snap, now=now) is True

    def test_boundary_just_under_threshold_is_not_stale(self):
        now = datetime.now(timezone.utc)
        old = now - timedelta(seconds=STALE_QUEUE_SECONDS - 10)
        snap = ExecutionQueueSnapshot(
            generated_at=old.isoformat(), mode="review", kill_switch_active=False,
            max_notional_per_order=25.0, n_intents=0, n_placeable=0, intents=[],
        )
        assert is_queue_stale(snap, now=now) is False

    def test_unparsable_timestamp_treated_as_stale(self):
        snap = ExecutionQueueSnapshot(
            generated_at="garbage", mode="review", kill_switch_active=False,
            max_notional_per_order=25.0, n_intents=0, n_placeable=0, intents=[],
        )
        assert is_queue_stale(snap) is True


# ---------------------------------------------------------------------------
# mfa_secret_configured
# ---------------------------------------------------------------------------

class TestMfaSecretConfigured:
    def test_set_secret_returns_true(self):
        fake_settings = SimpleNamespace(RH_MFA_SECRET="ABCDEFGHIJKLMNOP")
        assert mfa_secret_configured(fake_settings) is True

    def test_empty_string_returns_false(self):
        fake_settings = SimpleNamespace(RH_MFA_SECRET="")
        assert mfa_secret_configured(fake_settings) is False

    def test_none_returns_false(self):
        fake_settings = SimpleNamespace(RH_MFA_SECRET=None)
        assert mfa_secret_configured(fake_settings) is False

    def test_whitespace_only_returns_false(self):
        fake_settings = SimpleNamespace(RH_MFA_SECRET="   ")
        assert mfa_secret_configured(fake_settings) is False

    def test_missing_attribute_returns_false(self):
        fake_settings = SimpleNamespace()
        assert mfa_secret_configured(fake_settings) is False

    def test_object_raising_on_getattr_degrades_to_false(self):
        class Boom:
            @property
            def RH_MFA_SECRET(self):
                raise RuntimeError("boom")

        assert mfa_secret_configured(Boom()) is False


# ---------------------------------------------------------------------------
# read_notification_state / notification_age_seconds / ntfy_topic_configured
# ---------------------------------------------------------------------------

class TestReadNotificationState:
    def test_missing_file_returns_none(self, tmp_path):
        assert read_notification_state(tmp_path / "nope.json") is None

    def test_empty_file_returns_none(self, tmp_path):
        p = tmp_path / "execution_queue_notified.json"
        p.write_text("", encoding="utf-8")
        assert read_notification_state(p) is None

    def test_corrupt_json_returns_none(self, tmp_path):
        p = tmp_path / "execution_queue_notified.json"
        p.write_text("{not json", encoding="utf-8")
        assert read_notification_state(p) is None

    def test_non_object_json_returns_none(self, tmp_path):
        p = tmp_path / "execution_queue_notified.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        assert read_notification_state(p) is None

    def test_dedup_only_sidecar_with_no_push_yet_returns_none(self, tmp_path):
        # keys recorded but no notification ever attempted (e.g. the queue has
        # only ever contained intents the operator was already told about).
        p = tmp_path / "execution_queue_notified.json"
        p.write_text(json.dumps({"keys": ["AAPL:buy:False"]}), encoding="utf-8")
        assert read_notification_state(p) is None

    def test_valid_state_round_trips(self, tmp_path):
        p = tmp_path / "execution_queue_notified.json"
        p.write_text(
            json.dumps({
                "keys": ["AAPL:buy:True"],
                "last_notified_at": "2026-07-05T12:00:00+00:00",
                "last_notified_title": "InvestYo — Trades Ready to Place",
                "last_notified_count": 2,
                "last_notified_priority": "high",
            }),
            encoding="utf-8",
        )
        state = read_notification_state(p)
        assert state is not None
        assert state.last_notified_count == 2
        assert state.last_notified_priority == "high"

    def test_default_path_used_when_none_given(self, monkeypatch, tmp_path):
        fake_path = tmp_path / "execution_queue_notified.json"
        fake_path.write_text(
            json.dumps({"keys": [], "last_notified_at": "2026-07-05T12:00:00+00:00",
                        "last_notified_title": "t", "last_notified_count": 1,
                        "last_notified_priority": "default"}),
            encoding="utf-8",
        )
        import gui.robinhood_execution_panel as mod
        monkeypatch.setattr(mod, "NOTIFIED_STATE_PATH", fake_path)
        assert read_notification_state() is not None


class TestNotificationAge:
    def test_fresh_notification_age_near_zero(self):
        now = datetime.now(timezone.utc)
        state = NotificationState(
            last_notified_at=now.isoformat(), last_notified_title="t",
            last_notified_count=1, last_notified_priority="default",
        )
        age = notification_age_seconds(state, now=now + timedelta(seconds=5))
        assert age == pytest.approx(5.0, abs=1.0)

    def test_unparsable_timestamp_returns_nan(self):
        state = NotificationState(
            last_notified_at="not-a-timestamp", last_notified_title="t",
            last_notified_count=1, last_notified_priority="default",
        )
        age = notification_age_seconds(state)
        assert age != age  # NaN


class TestNtfyTopicConfigured:
    def test_set_returns_true(self, monkeypatch):
        monkeypatch.setenv("NTFY_TOPIC", "my-unguessable-topic")
        assert ntfy_topic_configured() is True

    def test_unset_returns_false(self, monkeypatch):
        monkeypatch.delenv("NTFY_TOPIC", raising=False)
        assert ntfy_topic_configured() is False

    def test_whitespace_only_returns_false(self, monkeypatch):
        monkeypatch.setenv("NTFY_TOPIC", "   ")
        assert ntfy_topic_configured() is False


class TestNotifiedStatePathConvention:
    def test_canonical_path_points_at_output_dir(self):
        assert NOTIFIED_STATE_PATH.name == "execution_queue_notified.json"
        assert NOTIFIED_STATE_PATH.parent.name == "output"


# ---------------------------------------------------------------------------
# derive_intent_status
# ---------------------------------------------------------------------------

class TestDeriveIntentStatus:
    def test_no_receipt_placeable_is_queued(self):
        intent = _make_intent(allow_place=True)
        status = derive_intent_status(intent, [])
        assert isinstance(status, IntentStatus)
        assert status.status == STATUS_QUEUED
        assert status.color == "neutral"

    def test_no_receipt_not_placeable_is_blocked_with_reasons(self):
        intent = _make_intent(allow_place=False, gate_reasons=["max_position_size", "heat"])
        status = derive_intent_status(intent, [])
        assert status.status == STATUS_BLOCKED
        assert status.color == "warning"
        assert "max_position_size" in status.detail
        assert "heat" in status.detail

    def test_blocked_without_reasons_has_fallback_detail(self):
        intent = _make_intent(allow_place=False, gate_reasons=[])
        status = derive_intent_status(intent, [])
        assert status.status == STATUS_BLOCKED
        assert status.detail  # non-empty fallback

    def test_placed_receipt_wins(self):
        intent = _make_intent(symbol="AAPL", side="buy", allow_place=True)
        receipts = [{"symbol": "AAPL", "side": "buy", "action": "placed",
                     "mcp_order_id": "ord-1", "note": "filled"}]
        status = derive_intent_status(intent, receipts)
        assert status.status == STATUS_PLACED
        assert status.color == "success"
        assert status.detail == "filled"

    def test_reviewed_receipt_maps_to_previewed(self):
        intent = _make_intent(symbol="AAPL", side="buy", allow_place=True)
        receipts = [{"symbol": "AAPL", "side": "buy", "action": "reviewed"}]
        status = derive_intent_status(intent, receipts)
        assert status.status == STATUS_PREVIEWED

    def test_skipped_receipt_maps_to_skipped(self):
        intent = _make_intent(symbol="AAPL", side="buy", allow_place=True)
        receipts = [{"symbol": "AAPL", "side": "buy", "action": "skipped", "note": "declined"}]
        status = derive_intent_status(intent, receipts)
        assert status.status == STATUS_SKIPPED
        assert status.color == "warning"
        assert status.detail == "declined"

    def test_matching_is_case_insensitive(self):
        intent = _make_intent(symbol="AAPL", side="buy", allow_place=True)
        receipts = [{"symbol": "aapl", "side": "BUY", "action": "placed"}]
        status = derive_intent_status(intent, receipts)
        assert status.status == STATUS_PLACED

    def test_side_mismatch_does_not_match(self):
        intent = _make_intent(symbol="AAPL", side="buy", allow_place=True)
        receipts = [{"symbol": "AAPL", "side": "sell", "action": "placed"}]
        status = derive_intent_status(intent, receipts)
        # No matching receipt -> falls through to queued (placeable)
        assert status.status == STATUS_QUEUED

    def test_most_advanced_receipt_wins_over_earlier(self):
        intent = _make_intent(symbol="AAPL", side="buy", allow_place=True)
        receipts = [
            {"symbol": "AAPL", "side": "buy", "action": "reviewed"},
            {"symbol": "AAPL", "side": "buy", "action": "placed", "mcp_order_id": "z9"},
        ]
        status = derive_intent_status(intent, receipts)
        assert status.status == STATUS_PLACED
        assert status.detail == "order z9"

    def test_placed_wins_regardless_of_line_order(self):
        intent = _make_intent(symbol="AAPL", side="buy", allow_place=True)
        receipts = [
            {"symbol": "AAPL", "side": "buy", "action": "placed"},
            {"symbol": "AAPL", "side": "buy", "action": "reviewed"},
        ]
        status = derive_intent_status(intent, receipts)
        assert status.status == STATUS_PLACED

    def test_unknown_receipt_action_ignored_falls_back_to_gate(self):
        intent = _make_intent(symbol="AAPL", side="buy", allow_place=False,
                              gate_reasons=["macro_kill_switch"])
        receipts = [{"symbol": "AAPL", "side": "buy", "action": "totally-bogus"}]
        status = derive_intent_status(intent, receipts)
        assert status.status == STATUS_BLOCKED

    def test_frozen_dataclass(self):
        status = derive_intent_status(_make_intent(allow_place=True), [])
        with pytest.raises(Exception):
            status.status = "placed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# read_placed_ledger
# ---------------------------------------------------------------------------

class TestReadPlacedLedger:
    def test_missing_file_returns_empty_list(self, tmp_path):
        assert read_placed_ledger(tmp_path / "nope.jsonl") == []

    def test_valid_lines_round_trip(self, tmp_path):
        p = tmp_path / "execution_placed.jsonl"
        lines = [
            json.dumps({"ts": "t1", "dedup_key": "k1", "symbol": "AAPL", "side": "buy",
                        "qty": 1, "target_notional": 25.0, "client_order_id": "c1",
                        "mcp_order_id": "m1"}),
            json.dumps({"ts": "t2", "symbol": "MSFT", "side": "buy"}),
        ]
        p.write_text("\n".join(lines), encoding="utf-8")
        entries = read_placed_ledger(p)
        assert len(entries) == 2
        assert entries[0]["symbol"] == "AAPL"

    def test_malformed_lines_are_skipped(self, tmp_path):
        p = tmp_path / "execution_placed.jsonl"
        p.write_text(
            "{broken\n" + json.dumps({"symbol": "AAPL"}) + "\n\n[1,2,3]\n",
            encoding="utf-8",
        )
        entries = read_placed_ledger(p)
        assert len(entries) == 1
        assert entries[0]["symbol"] == "AAPL"

    def test_empty_file_returns_empty_list(self, tmp_path):
        p = tmp_path / "execution_placed.jsonl"
        p.write_text("", encoding="utf-8")
        assert read_placed_ledger(p) == []

    def test_max_lines_tails(self, tmp_path):
        p = tmp_path / "execution_placed.jsonl"
        lines = [json.dumps({"symbol": f"S{i}"}) for i in range(10)]
        p.write_text("\n".join(lines), encoding="utf-8")
        entries = read_placed_ledger(p, max_lines=3)
        assert len(entries) == 3
        assert entries[-1]["symbol"] == "S9"

    def test_default_path_used_when_none_given(self, monkeypatch, tmp_path):
        fake = tmp_path / "execution_placed.jsonl"
        fake.write_text(json.dumps({"symbol": "AAPL"}), encoding="utf-8")
        import gui.robinhood_execution_panel as mod
        monkeypatch.setattr(mod, "EXECUTION_PLACED_PATH", fake)
        assert read_placed_ledger() == [{"symbol": "AAPL"}]


# ---------------------------------------------------------------------------
# build_reconciliation_summary
# ---------------------------------------------------------------------------

class TestBuildReconciliationSummary:
    def test_empty_ledger_yields_zero_counts(self):
        summary = build_reconciliation_summary([], [])
        assert isinstance(summary, ReconciliationSummary)
        assert summary.placed_count == 0
        assert summary.matched == []
        assert summary.unmatched == []

    def test_matched_entry(self):
        ledger = [{"symbol": "AAPL", "side": "buy", "mcp_order_id": "m1"}]
        receipts = [{"symbol": "AAPL", "side": "buy", "action": "placed"}]
        summary = build_reconciliation_summary(ledger, receipts)
        assert summary.placed_count == 1
        assert len(summary.matched) == 1
        assert summary.unmatched == []

    def test_unmatched_entry_flagged(self):
        ledger = [{"symbol": "AAPL", "side": "buy"}]
        receipts = []  # no placed receipt
        summary = build_reconciliation_summary(ledger, receipts)
        assert summary.placed_count == 1
        assert summary.matched == []
        assert len(summary.unmatched) == 1

    def test_only_placed_receipts_count_as_matches(self):
        ledger = [{"symbol": "AAPL", "side": "buy"}]
        # a "reviewed" receipt must NOT confirm a placement
        receipts = [{"symbol": "AAPL", "side": "buy", "action": "reviewed"}]
        summary = build_reconciliation_summary(ledger, receipts)
        assert len(summary.unmatched) == 1

    def test_case_insensitive_matching(self):
        ledger = [{"symbol": "aapl", "side": "BUY"}]
        receipts = [{"symbol": "AAPL", "side": "buy", "action": "placed"}]
        summary = build_reconciliation_summary(ledger, receipts)
        assert len(summary.matched) == 1

    def test_mixed_ledger(self):
        ledger = [
            {"symbol": "AAPL", "side": "buy"},
            {"symbol": "MSFT", "side": "sell"},
        ]
        receipts = [{"symbol": "AAPL", "side": "buy", "action": "placed"}]
        summary = build_reconciliation_summary(ledger, receipts)
        assert summary.placed_count == 2
        assert len(summary.matched) == 1
        assert len(summary.unmatched) == 1
        assert summary.unmatched[0]["symbol"] == "MSFT"

    def test_non_dict_ledger_rows_ignored(self):
        summary = build_reconciliation_summary(["junk", {"symbol": "AAPL", "side": "buy"}], [])
        assert len(summary.unmatched) == 1


# ---------------------------------------------------------------------------
# Help-content keys for the new sections/metrics
# ---------------------------------------------------------------------------

class TestHelpContentKeys:
    def test_section_help_keys_present(self):
        from gui.help_content import SECTION_HELP
        assert SECTION_HELP.get("robinhood_execution.intent_status")
        assert SECTION_HELP.get("robinhood_execution.reconciliation")

    def test_metric_help_keys_present(self):
        from gui.help_content import metric_help
        assert metric_help("robinhood_execution.placed_count")
        assert metric_help("robinhood_execution.matched")
        assert metric_help("robinhood_execution.unmatched")

    def test_unknown_metric_key_returns_empty_string(self):
        from gui.help_content import metric_help
        assert metric_help("robinhood_execution.does_not_exist") == ""

    def test_placed_count_help_cites_settings_notional_cap(self):
        from gui.help_content import metric_help
        from settings import settings
        text = metric_help("robinhood_execution.placed_count")
        assert f"{settings.ROBINHOOD_MAX_NOTIONAL_PER_ORDER:,.2f}" in text


class TestPlacedLedgerPathConvention:
    def test_canonical_path_points_at_output_dir(self):
        assert EXECUTION_PLACED_PATH.name == "execution_placed.jsonl"
        assert EXECUTION_PLACED_PATH.parent.name == "output"


# ---------------------------------------------------------------------------
# Panel wiring
# ---------------------------------------------------------------------------

class TestPanelWiring:
    def test_launcher_references_robinhood_execution_status(self):
        source = Path("gui/panels/launcher.py").read_text(encoding="utf-8")
        assert "_render_robinhood_execution_status" in source
        assert "gui.robinhood_execution_panel" in source
