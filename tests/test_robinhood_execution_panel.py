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
    EXECUTION_QUEUE_PATH,
    EXECUTION_RECEIPTS_PATH,
    ExecutionQueueSnapshot,
    QueuedIntent,
    STALE_QUEUE_SECONDS,
    is_queue_stale,
    mfa_secret_configured,
    queue_age_seconds,
    read_execution_queue,
    read_execution_receipts,
)


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
# Panel wiring
# ---------------------------------------------------------------------------

class TestPanelWiring:
    def test_launcher_references_robinhood_execution_status(self):
        source = Path("gui/panels/launcher.py").read_text(encoding="utf-8")
        assert "_render_robinhood_execution_status" in source
        assert "gui.robinhood_execution_panel" in source
