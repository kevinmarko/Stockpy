"""Unit tests for :mod:`scripts.snapshot_diff`.

Covers the four invariants the daily report depends on:

1. Rotation writes one timestamped file per call, prunes files older
   than ``max_age_days``, and never raises on a write/prune failure.
2. ``compute_diff`` correctly classifies new BUYs, action flips,
   material conviction moves, holdings added/dropped, and regime change.
3. The conviction-delta threshold suppresses sub-threshold noise.
4. Corrupt / missing / first-run inputs degrade to an empty diff with a
   note — never raise (CONSTRAINT #4 + #6).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from scripts.snapshot_diff import (
    DEFAULT_CONVICTION_DELTA_THRESHOLD,
    SnapshotDiff,
    compute_diff,
    compute_diff_from_history,
    list_rotated_snapshots,
    load_snapshot,
    rotate_snapshot,
    format_markdown,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _snap(
    *,
    ts: str = "2026-06-26T12:00:00+00:00",
    regime: str = "RISK ON",
    signals=None,
    holdings=None,
):
    """Build a minimal snapshot dict the diff engine can consume."""
    return {
        "timestamp": ts,
        "market_regime": regime,
        "holdings": list(holdings) if holdings is not None else [],
        "signals": list(signals) if signals is not None else [],
    }


def _sig(symbol, action="HOLD", conviction=0.5, advisory_action=None):
    return {
        "symbol": symbol,
        "action": action,
        "advisory_action": advisory_action if advisory_action is not None else action,
        "advisory_conviction": conviction,
    }


# ---------------------------------------------------------------------------
# load_snapshot tolerance
# ---------------------------------------------------------------------------

class TestLoadSnapshot:
    """``load_snapshot`` must never raise on bad input."""

    def test_missing_file_returns_none(self, tmp_path: Path):
        assert load_snapshot(tmp_path / "nope.json") is None

    def test_empty_file_returns_none(self, tmp_path: Path):
        p = tmp_path / "empty.json"
        p.write_text("", encoding="utf-8")
        assert load_snapshot(p) is None

    def test_corrupt_json_returns_none(self, tmp_path: Path):
        p = tmp_path / "broken.json"
        p.write_text("{not json", encoding="utf-8")
        assert load_snapshot(p) is None

    def test_non_object_json_returns_none(self, tmp_path: Path):
        p = tmp_path / "list.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        assert load_snapshot(p) is None

    def test_valid_json_round_trips(self, tmp_path: Path):
        snap = _snap()
        p = tmp_path / "ok.json"
        p.write_text(json.dumps(snap), encoding="utf-8")
        assert load_snapshot(p) == snap


# ---------------------------------------------------------------------------
# Rotation + pruning
# ---------------------------------------------------------------------------

class TestRotation:
    """``rotate_snapshot`` is the only writer; tests pin its contract."""

    def test_rotation_writes_history_file(self, tmp_path: Path):
        out = rotate_snapshot(_snap(), tmp_path)
        assert out is not None
        assert out.exists()
        assert out.parent.name == "history"
        assert out.name.startswith("state_snapshot_")
        assert out.name.endswith(".json")

    def test_filename_encodes_snapshot_timestamp(self, tmp_path: Path):
        ts = "2026-01-15T08:30:00+00:00"
        out = rotate_snapshot(_snap(ts=ts), tmp_path)
        # 20260115T083000Z
        assert "20260115T083000Z" in out.name

    def test_prune_drops_files_older_than_max_age(self, tmp_path: Path):
        now = datetime(2026, 6, 26, 12, 0, tzinfo=timezone.utc)
        # Plant an old snapshot directly on disk.
        history = tmp_path / "history"
        history.mkdir()
        old_name = "state_snapshot_20260101T000000Z.json"
        (history / old_name).write_text(json.dumps(_snap()), encoding="utf-8")
        assert (history / old_name).exists()

        # Rotate "now" with a 30-day window — the Jan 1 file is > 30 days old.
        rotate_snapshot(_snap(ts=now.isoformat()), tmp_path,
                        max_age_days=30, now=now)

        files = list_rotated_snapshots(tmp_path)
        # Old file should have been pruned; current one remains.
        assert all("20260101" not in p.name for p in files)
        assert any("20260626" in p.name for p in files)

    def test_prune_disabled_when_max_age_zero(self, tmp_path: Path):
        now = datetime(2026, 6, 26, 12, 0, tzinfo=timezone.utc)
        history = tmp_path / "history"
        history.mkdir()
        (history / "state_snapshot_20240101T000000Z.json").write_text(
            json.dumps(_snap()), encoding="utf-8"
        )
        rotate_snapshot(_snap(ts=now.isoformat()), tmp_path,
                        max_age_days=0, now=now)
        files = [p.name for p in list_rotated_snapshots(tmp_path)]
        assert any("20240101" in n for n in files)

    def test_list_ignores_unrelated_files(self, tmp_path: Path):
        history = tmp_path / "history"
        history.mkdir()
        (history / "README.txt").write_text("notes", encoding="utf-8")
        (history / "state_snapshot.json").write_text(
            json.dumps(_snap()), encoding="utf-8"
        )  # current snapshot misplaced — ignored
        rotate_snapshot(_snap(), tmp_path)
        files = list_rotated_snapshots(tmp_path)
        assert len(files) == 1  # only the rotation file


# ---------------------------------------------------------------------------
# compute_diff classification
# ---------------------------------------------------------------------------

class TestComputeDiff:
    """Core diff logic — each test pins one classification rule."""

    def test_first_run_lists_buys_and_holdings(self):
        curr = _snap(
            signals=[_sig("AAPL", action="BUY"), _sig("MSFT", action="HOLD")],
            holdings=["AAPL"],
        )
        diff = compute_diff(prev=None, curr=curr)
        assert diff.new_buys == ["AAPL"]
        assert diff.added_holdings == ["AAPL"]
        # Without a prev, an unchanged HOLD is not a "flip".
        assert diff.action_flips == []
        assert diff.regime_change is None

    def test_identical_snapshots_yield_empty_diff(self):
        snap = _snap(
            signals=[_sig("AAPL", action="BUY", conviction=0.6)],
            holdings=["AAPL"],
        )
        diff = compute_diff(prev=snap, curr=snap)
        assert diff.is_empty
        # The empty-state markdown renders without crashing.
        assert "No material changes" in format_markdown(diff)

    def test_action_flip_buy_to_hold(self):
        prev = _snap(signals=[_sig("JNJ", action="BUY", conviction=0.6)])
        curr = _snap(signals=[_sig("JNJ", action="HOLD", conviction=0.4)],
                     ts="2026-06-27T12:00:00+00:00")
        diff = compute_diff(prev=prev, curr=curr)
        assert diff.action_flips == [
            {"symbol": "JNJ", "before": "BUY", "after": "HOLD"}
        ]
        # Not classified as new_buy (it lost BUY status).
        assert diff.new_buys == []

    def test_new_buy_takes_precedence_over_flip(self):
        # AAPL went HOLD → BUY: surfaces as new_buy, NOT as an action_flip.
        prev = _snap(signals=[_sig("AAPL", action="HOLD")])
        curr = _snap(signals=[_sig("AAPL", action="BUY")],
                     ts="2026-06-27T12:00:00+00:00")
        diff = compute_diff(prev=prev, curr=curr)
        assert diff.new_buys == ["AAPL"]
        assert diff.action_flips == []

    def test_conviction_delta_threshold_filters_noise(self):
        # 0.21 surfaces; 0.19 is suppressed.
        prev = _snap(signals=[
            _sig("AAA", conviction=0.50),
            _sig("BBB", conviction=0.50),
        ])
        curr = _snap(
            ts="2026-06-27T12:00:00+00:00",
            signals=[
                _sig("AAA", conviction=0.71),  # +0.21
                _sig("BBB", conviction=0.69),  # +0.19
            ],
        )
        diff = compute_diff(prev=prev, curr=curr,
                            conviction_delta_threshold=0.2)
        symbols = {d["symbol"] for d in diff.conviction_deltas}
        assert "AAA" in symbols
        assert "BBB" not in symbols

    def test_regime_change_detected(self):
        prev = _snap(regime="RISK ON")
        curr = _snap(regime="RECESSION", ts="2026-06-27T12:00:00+00:00")
        diff = compute_diff(prev=prev, curr=curr)
        assert diff.regime_change == ("RISK ON", "RECESSION")

    def test_no_regime_change_when_equal(self):
        prev = _snap(regime="RISK ON")
        curr = _snap(regime="RISK ON", ts="2026-06-27T12:00:00+00:00")
        diff = compute_diff(prev=prev, curr=curr)
        assert diff.regime_change is None

    def test_holdings_added_and_dropped(self):
        prev = _snap(holdings=["AAPL", "MSFT"])
        curr = _snap(holdings=["AAPL", "NVDA"],
                     ts="2026-06-27T12:00:00+00:00")
        diff = compute_diff(prev=prev, curr=curr)
        assert diff.added_holdings == ["NVDA"]
        assert diff.dropped_holdings == ["MSFT"]

    def test_holdings_backfilled_from_shares(self):
        # No explicit holdings list — derive from signals[].shares > 0.
        snap = {
            "timestamp": "2026-06-27T12:00:00+00:00",
            "market_regime": "RISK ON",
            "signals": [
                {"symbol": "AAPL", "shares": 10, "action": "HOLD"},
                {"symbol": "MSFT", "shares": 0, "action": "BUY"},
            ],
        }
        diff = compute_diff(prev=None, curr=snap)
        assert "AAPL" in diff.added_holdings
        assert "MSFT" not in diff.added_holdings


# ---------------------------------------------------------------------------
# compute_diff_from_history end-to-end
# ---------------------------------------------------------------------------

class TestHistoryIntegration:
    """End-to-end: rotate two snapshots, read them back, diff."""

    def test_two_rotations_yield_real_diff(self, tmp_path: Path):
        t1 = datetime(2026, 6, 26, 9, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 6, 26, 17, 0, tzinfo=timezone.utc)
        rotate_snapshot(_snap(ts=t1.isoformat(),
                              signals=[_sig("AAPL", "HOLD", 0.4)]),
                        tmp_path, now=t1)
        rotate_snapshot(_snap(ts=t2.isoformat(),
                              signals=[_sig("AAPL", "BUY", 0.7)]),
                        tmp_path, now=t2)
        diff = compute_diff_from_history(tmp_path)
        assert "AAPL" in diff.new_buys
        assert any(d["symbol"] == "AAPL" for d in diff.conviction_deltas)

    def test_single_rotation_returns_first_run_shape(self, tmp_path: Path):
        rotate_snapshot(_snap(signals=[_sig("AAPL", "BUY")]), tmp_path)
        diff = compute_diff_from_history(tmp_path)
        # Only one rotated snapshot exists → prev is None → first-run treatment.
        assert "AAPL" in diff.new_buys

    def test_no_history_returns_empty_with_note(self, tmp_path: Path):
        diff = compute_diff_from_history(tmp_path)
        assert diff.is_empty
        assert any("No rotated snapshots" in n for n in diff.notes)


# ---------------------------------------------------------------------------
# Defaults & exports
# ---------------------------------------------------------------------------

class TestModuleSurface:
    """Pin the public constants the rest of the platform depends on."""

    def test_default_threshold_is_0_2(self):
        assert DEFAULT_CONVICTION_DELTA_THRESHOLD == pytest.approx(0.2)

    def test_snapshot_diff_to_dict_is_jsonable(self):
        diff = SnapshotDiff(prev_ts="a", curr_ts="b",
                            regime_change=("RISK ON", "RECESSION"),
                            new_buys=["AAPL"])
        d = diff.to_dict()
        # Round-trip through json must not raise.
        json.dumps(d)
        assert d["regime_change"] == ["RISK ON", "RECESSION"]
        assert d["is_empty"] is False
