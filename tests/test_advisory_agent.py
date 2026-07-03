"""
tests/test_advisory_agent.py — Advisory Agent policy tests
==========================================================
Covers ``engine/advisory_agent.py``:

* market-hours detection (RTH / extended hours / weekend)
* adaptive cadence (RTH normal / vol-spike / open-close boost / extended /
  off-hours / error back-off)
* actionable-backlog lifecycle (insert / actioned / expired)
* reminder escalation tiers (1 h / 4 h / 24 h)
* state round-trip (load_agent_state / save_agent_state) and tolerance
* dispatch helper (no-op on empty, never raises on broken notify)

All tests are fully offline — no network calls, no filesystem side-effects
outside tmp_path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, List, Optional
from unittest import mock

import pytest

from engine.advisory_agent import (
    CONFIG,
    AgentState,
    BacklogEntry,
    BacklogReminder,
    apply_reminder_dispatch,
    compute_backlog_reminders,
    compute_next_run_delay,
    dispatch_backlog_reminders,
    is_extended_hours,
    is_us_market_open,
    load_agent_state,
    process_run_result,
    save_agent_state,
    update_backlog,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _utc(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _et(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """Construct a UTC datetime that maps to the given America/New_York wall-clock.

    We compute it by going through ZoneInfo so DST is correctly respected.
    """
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    local = datetime(year, month, day, hour, minute, tzinfo=et)
    return local.astimezone(timezone.utc)


@dataclass
class _Rec:
    """Duck-typed Recommendation."""
    symbol: str
    action: str
    conviction: float


@dataclass
class _DecisionEntry:
    """Duck-typed gui.decision_log.DecisionEntry."""
    symbol: str
    action_taken: str
    timestamp: str  # ISO-8601 UTC


@dataclass
class _RunResult:
    """Duck-typed main.RunResult — only the .errors attribute is read."""
    errors: List[Any]


# ---------------------------------------------------------------------------
# Market-hours detection
# ---------------------------------------------------------------------------


class TestMarketHours:
    def test_rth_open_at_0930_et(self):
        # Pick a known weekday: 2025-06-30 is a Monday.
        # 09:30 ET = 13:30 UTC during DST.
        assert is_us_market_open(_et(2025, 6, 30, 9, 30)) is True

    def test_rth_close_at_1600_et(self):
        assert is_us_market_open(_et(2025, 6, 30, 16, 0)) is True

    def test_rth_just_before_open(self):
        assert is_us_market_open(_et(2025, 6, 30, 9, 29)) is False

    def test_rth_just_after_close(self):
        assert is_us_market_open(_et(2025, 6, 30, 16, 1)) is False

    def test_weekend_is_closed(self):
        # 2025-06-28 is a Saturday.
        assert is_us_market_open(_et(2025, 6, 28, 12, 0)) is False

    def test_sunday_is_closed(self):
        # 2025-06-29 is a Sunday.
        assert is_us_market_open(_et(2025, 6, 29, 12, 0)) is False

    def test_naive_datetime_is_promoted_to_utc(self):
        # A naive datetime at 14:00 UTC = 10:00 ET on a Monday → open.
        naive = datetime(2025, 6, 30, 14, 0)
        assert is_us_market_open(naive) is True

    def test_extended_hours_includes_premarket(self):
        # 07:00 ET on a Monday → extended hours yes, RTH no.
        t = _et(2025, 6, 30, 7, 0)
        assert is_extended_hours(t) is True
        assert is_us_market_open(t) is False

    def test_extended_hours_excludes_overnight(self):
        # 03:00 ET on a Monday → before extended hours window.
        t = _et(2025, 6, 30, 3, 0)
        assert is_extended_hours(t) is False

    def test_extended_hours_excludes_weekend(self):
        t = _et(2025, 6, 28, 12, 0)
        assert is_extended_hours(t) is False


# ---------------------------------------------------------------------------
# Adaptive cadence — compute_next_run_delay
# ---------------------------------------------------------------------------


class TestCadence:
    def test_rth_normal_default_delay(self):
        # Midday Monday, no vol-spike, no error streak.
        state = AgentState()
        delay = compute_next_run_delay(
            _et(2025, 6, 30, 12, 0),
            state=state,
            vix=15.0,
            market_regime="RISK ON",
        )
        assert delay == CONFIG["rth_normal_delay_s"]

    def test_rth_open_window_boost(self):
        # 09:45 ET = 15 minutes into the open → inside the 30-min boost window.
        state = AgentState()
        delay = compute_next_run_delay(
            _et(2025, 6, 30, 9, 45),
            state=state, vix=15.0, market_regime="RISK ON",
        )
        assert delay == CONFIG["rth_open_close_delay_s"]

    def test_rth_close_window_boost(self):
        # 15:45 ET = 15 minutes before the close → inside the boost window.
        state = AgentState()
        delay = compute_next_run_delay(
            _et(2025, 6, 30, 15, 45),
            state=state, vix=15.0, market_regime="RISK ON",
        )
        assert delay == CONFIG["rth_open_close_delay_s"]

    def test_rth_high_vix_tightens_cadence(self):
        # Midday RTH but VIX > 25 → high-vol delay.
        state = AgentState()
        delay = compute_next_run_delay(
            _et(2025, 6, 30, 12, 0),
            state=state, vix=30.0, market_regime="RISK ON",
        )
        assert delay == CONFIG["rth_high_vol_delay_s"]

    def test_rth_recession_regime_tightens_cadence(self):
        # Midday RTH, VIX low but regime is RECESSION → high-vol delay.
        state = AgentState()
        delay = compute_next_run_delay(
            _et(2025, 6, 30, 12, 0),
            state=state, vix=15.0, market_regime="RECESSION",
        )
        assert delay == CONFIG["rth_high_vol_delay_s"]

    def test_extended_hours_delay(self):
        # 07:00 ET Monday — premarket.
        state = AgentState()
        delay = compute_next_run_delay(
            _et(2025, 6, 30, 7, 0),
            state=state, vix=15.0, market_regime="RISK ON",
        )
        assert delay == CONFIG["extended_hours_delay_s"]

    def test_off_hours_delay(self):
        # 02:00 ET Monday — overnight.
        state = AgentState()
        delay = compute_next_run_delay(
            _et(2025, 6, 30, 2, 0),
            state=state, vix=15.0, market_regime="RISK ON",
        )
        assert delay == CONFIG["off_hours_delay_s"]

    def test_weekend_uses_off_hours(self):
        state = AgentState()
        delay = compute_next_run_delay(
            _et(2025, 6, 28, 12, 0),
            state=state, vix=15.0, market_regime="RISK ON",
        )
        assert delay == CONFIG["off_hours_delay_s"]

    def test_error_backoff_short_circuits(self):
        state = AgentState(consecutive_error_cycles=3)
        # Even though it is RTH, error back-off wins.
        delay = compute_next_run_delay(
            _et(2025, 6, 30, 12, 0),
            state=state, vix=15.0, market_regime="RISK ON",
        )
        expected = min(
            CONFIG["error_backoff_base_s"] * 3,
            CONFIG["error_backoff_max_s"],
        )
        assert delay == max(CONFIG["min_delay_s"], expected)

    def test_error_backoff_caps_at_max(self):
        state = AgentState(consecutive_error_cycles=999)
        delay = compute_next_run_delay(
            _et(2025, 6, 30, 12, 0),
            state=state, vix=None, market_regime=None,
        )
        assert delay == CONFIG["error_backoff_max_s"]

    def test_min_delay_floor(self):
        # Override CONFIG to force a very small delay, then assert the floor wins.
        state = AgentState()
        delay = compute_next_run_delay(
            _et(2025, 6, 30, 12, 0),
            state=state, vix=15.0, market_regime="RISK ON",
            config={"rth_normal_delay_s": 1},
        )
        assert delay >= CONFIG["min_delay_s"]


# ---------------------------------------------------------------------------
# Backlog management
# ---------------------------------------------------------------------------


class TestBacklog:
    def test_buy_above_threshold_enters_backlog(self):
        state = AgentState()
        rec = _Rec(symbol="AAPL", action="BUY", conviction=0.90)
        now = _utc(2025, 6, 30, 14)
        update_backlog(state, [rec], [], now)
        assert "AAPL:BUY" in state.backlog
        b = state.backlog["AAPL:BUY"]
        assert b.symbol == "AAPL" and b.action == "BUY" and b.conviction == 0.90

    def test_below_threshold_does_not_enter(self):
        state = AgentState()
        rec = _Rec(symbol="AAPL", action="BUY", conviction=0.50)
        update_backlog(state, [rec], [], _utc(2025, 6, 30))
        assert state.backlog == {}

    def test_hold_action_does_not_enter(self):
        state = AgentState()
        rec = _Rec(symbol="AAPL", action="HOLD", conviction=0.99)
        update_backlog(state, [rec], [], _utc(2025, 6, 30))
        assert state.backlog == {}

    def test_first_seen_iso_preserved_on_resurface(self):
        state = AgentState()
        rec1 = _Rec(symbol="AAPL", action="BUY", conviction=0.90)
        update_backlog(state, [rec1], [], _utc(2025, 6, 30, 14))
        first_seen = state.backlog["AAPL:BUY"].first_seen_iso
        # A later cycle resurfaces with higher conviction.
        rec2 = _Rec(symbol="AAPL", action="BUY", conviction=0.95)
        update_backlog(state, [rec2], [], _utc(2025, 6, 30, 18))
        # first_seen unchanged; conviction refreshed.
        assert state.backlog["AAPL:BUY"].first_seen_iso == first_seen
        assert state.backlog["AAPL:BUY"].conviction == 0.95

    def test_acted_entry_clears_backlog(self):
        state = AgentState()
        rec = _Rec(symbol="AAPL", action="BUY", conviction=0.90)
        first_cycle = _utc(2025, 6, 30, 14)
        update_backlog(state, [rec], [], first_cycle)
        assert "AAPL:BUY" in state.backlog
        # Operator logs an acted decision for AAPL 1h later.
        entry = _DecisionEntry(
            symbol="AAPL",
            action_taken="acted",
            timestamp=(first_cycle + timedelta(hours=1)).isoformat(),
        )
        # Next cycle the same signal recurs; the decision-log entry should pop
        # the backlog item.  Even though the recommendation is still firing,
        # the operator has already committed a decision and re-pinging would
        # be noise — the agent must NOT re-nag.
        update_backlog(state, [rec], [entry], first_cycle + timedelta(hours=2))
        assert "AAPL:BUY" not in state.backlog

    def test_passed_decision_does_not_clear(self):
        state = AgentState()
        rec = _Rec(symbol="AAPL", action="BUY", conviction=0.90)
        first_cycle = _utc(2025, 6, 30, 14)
        update_backlog(state, [rec], [], first_cycle)
        entry = _DecisionEntry(
            symbol="AAPL",
            action_taken="passed",   # explicitly NOT "acted"
            timestamp=(first_cycle + timedelta(hours=1)).isoformat(),
        )
        # Empty recommendations this cycle — but the backlog persists.
        update_backlog(state, [], [entry], first_cycle + timedelta(hours=2))
        assert "AAPL:BUY" in state.backlog
        # first_seen unchanged.
        assert _iso_to_dt(state.backlog["AAPL:BUY"].first_seen_iso) == first_cycle

    def test_expired_entry_dropped_silently(self):
        state = AgentState()
        rec = _Rec(symbol="AAPL", action="BUY", conviction=0.90)
        first_cycle = _utc(2025, 6, 30, 14)
        update_backlog(state, [rec], [], first_cycle)
        # Fast-forward past the expiry window with NO new recs.
        far_future = first_cycle + timedelta(hours=CONFIG["backlog_expiry_hours"] + 1)
        update_backlog(state, [], [], far_future)
        assert "AAPL:BUY" not in state.backlog

    def test_separate_actions_separate_backlog_keys(self):
        state = AgentState()
        recs = [
            _Rec(symbol="AAPL", action="BUY", conviction=0.90),
            _Rec(symbol="AAPL", action="SELL", conviction=0.90),
        ]
        update_backlog(state, recs, [], _utc(2025, 6, 30))
        assert "AAPL:BUY" in state.backlog
        assert "AAPL:SELL" in state.backlog


# ---------------------------------------------------------------------------
# Reminder escalation
# ---------------------------------------------------------------------------


class TestReminders:
    def test_no_reminder_when_too_young(self):
        # Backlog entry first seen 30 minutes ago; tier 1 is at 1h.
        first_seen = _utc(2025, 6, 30, 14)
        state = AgentState(backlog={
            "AAPL:BUY": BacklogEntry(
                symbol="AAPL", action="BUY", conviction=0.90,
                first_seen_iso=first_seen.isoformat(),
                last_pinged_iso="", reminders_sent=0,
            )
        })
        reminders = compute_backlog_reminders(state, first_seen + timedelta(minutes=30))
        assert reminders == []

    def test_tier1_fires_after_1h(self):
        first_seen = _utc(2025, 6, 30, 14)
        state = AgentState(backlog={
            "AAPL:BUY": BacklogEntry(
                symbol="AAPL", action="BUY", conviction=0.90,
                first_seen_iso=first_seen.isoformat(),
                last_pinged_iso="", reminders_sent=0,
            )
        })
        reminders = compute_backlog_reminders(
            state, first_seen + timedelta(hours=1, minutes=1),
        )
        assert len(reminders) == 1
        assert reminders[0].tier == 1
        assert reminders[0].symbol == "AAPL"
        assert reminders[0].priority == CONFIG["backlog_tier_priorities"][0]

    def test_tier2_fires_after_4h(self):
        first_seen = _utc(2025, 6, 30, 14)
        state = AgentState(backlog={
            "AAPL:BUY": BacklogEntry(
                symbol="AAPL", action="BUY", conviction=0.90,
                first_seen_iso=first_seen.isoformat(),
                # Tier 1 already sent.
                last_pinged_iso=(first_seen + timedelta(hours=1)).isoformat(),
                reminders_sent=1,
            )
        })
        reminders = compute_backlog_reminders(
            state, first_seen + timedelta(hours=4, minutes=1),
        )
        assert len(reminders) == 1
        assert reminders[0].tier == 2
        assert reminders[0].priority == CONFIG["backlog_tier_priorities"][1]

    def test_no_more_reminders_after_cap(self):
        first_seen = _utc(2025, 6, 30, 14)
        state = AgentState(backlog={
            "AAPL:BUY": BacklogEntry(
                symbol="AAPL", action="BUY", conviction=0.90,
                first_seen_iso=first_seen.isoformat(),
                last_pinged_iso=(first_seen + timedelta(hours=24)).isoformat(),
                reminders_sent=CONFIG["backlog_max_reminders"],
            )
        })
        reminders = compute_backlog_reminders(
            state, first_seen + timedelta(hours=48),
        )
        assert reminders == []

    def test_apply_reminder_dispatch_advances_counter(self):
        first_seen = _utc(2025, 6, 30, 14)
        state = AgentState(backlog={
            "AAPL:BUY": BacklogEntry(
                symbol="AAPL", action="BUY", conviction=0.90,
                first_seen_iso=first_seen.isoformat(),
                last_pinged_iso="", reminders_sent=0,
            )
        })
        now = first_seen + timedelta(hours=1, minutes=1)
        reminders = compute_backlog_reminders(state, now)
        assert len(reminders) == 1
        apply_reminder_dispatch(state, reminders, now)
        assert state.backlog["AAPL:BUY"].reminders_sent == 1
        assert state.backlog["AAPL:BUY"].last_pinged_iso == now.isoformat()


# ---------------------------------------------------------------------------
# State round-trip
# ---------------------------------------------------------------------------


class TestStateIO:
    def test_save_then_load_roundtrip(self, tmp_path: Path):
        path = tmp_path / "agent_state.json"
        state = AgentState(
            cycle_count=5,
            last_cycle_iso=_utc(2025, 6, 30).isoformat(),
            last_error_count=2,
            consecutive_error_cycles=1,
            backlog={
                "AAPL:BUY": BacklogEntry(
                    symbol="AAPL", action="BUY", conviction=0.90,
                    first_seen_iso=_utc(2025, 6, 30, 14).isoformat(),
                    last_pinged_iso="",
                    reminders_sent=0,
                )
            },
        )
        save_agent_state(state, path)
        loaded = load_agent_state(path)
        assert loaded.cycle_count == 5
        assert loaded.consecutive_error_cycles == 1
        assert "AAPL:BUY" in loaded.backlog
        assert loaded.backlog["AAPL:BUY"].conviction == 0.90

    def test_load_missing_file_returns_fresh_state(self, tmp_path: Path):
        path = tmp_path / "does_not_exist.json"
        loaded = load_agent_state(path)
        assert loaded.cycle_count == 0
        assert loaded.backlog == {}

    def test_load_corrupt_json_degrades_to_fresh(self, tmp_path: Path):
        path = tmp_path / "agent_state.json"
        path.write_text("this is not valid json", encoding="utf-8")
        loaded = load_agent_state(path)
        assert loaded.cycle_count == 0
        assert loaded.backlog == {}

    def test_load_empty_file_degrades_to_fresh(self, tmp_path: Path):
        path = tmp_path / "agent_state.json"
        path.write_text("", encoding="utf-8")
        loaded = load_agent_state(path)
        assert loaded.cycle_count == 0

    def test_save_tolerates_unwritable_dir(self, tmp_path: Path, caplog):
        # Point at a path that cannot be created (file in path).
        existing_file = tmp_path / "blocked"
        existing_file.write_text("x", encoding="utf-8")
        blocked_path = existing_file / "agent_state.json"
        # Must NOT raise.
        save_agent_state(AgentState(cycle_count=1), blocked_path)

    def test_atomic_write_uses_tmp_then_rename(self, tmp_path: Path):
        path = tmp_path / "agent_state.json"
        save_agent_state(AgentState(cycle_count=3), path)
        # No stray .tmp left behind after successful save.
        stray = list(tmp_path.glob("*.tmp"))
        assert stray == []
        assert path.exists()


# ---------------------------------------------------------------------------
# process_run_result
# ---------------------------------------------------------------------------


class TestProcessRunResult:
    def test_increments_cycle_count(self):
        state = AgentState(cycle_count=5)
        result = _RunResult(errors=[])
        process_run_result(state, result, _utc(2025, 6, 30))
        assert state.cycle_count == 6

    def test_error_streak_increments_on_errors(self):
        state = AgentState(consecutive_error_cycles=2)
        result = _RunResult(errors=[{"symbol": "AAPL", "stage": "X"}])
        process_run_result(state, result, _utc(2025, 6, 30))
        assert state.consecutive_error_cycles == 3
        assert state.last_error_count == 1

    def test_error_streak_resets_on_clean_run(self):
        state = AgentState(consecutive_error_cycles=5)
        result = _RunResult(errors=[])
        process_run_result(state, result, _utc(2025, 6, 30))
        assert state.consecutive_error_cycles == 0
        assert state.last_error_count == 0

    def test_naive_now_promoted_to_utc(self):
        state = AgentState()
        result = _RunResult(errors=[])
        naive = datetime(2025, 6, 30, 12, 0)
        process_run_result(state, result, naive)
        # Stored ISO string must have timezone info.
        assert "+00:00" in state.last_cycle_iso or state.last_cycle_iso.endswith("Z")


# ---------------------------------------------------------------------------
# Dispatch helper
# ---------------------------------------------------------------------------


class TestDispatch:
    def test_empty_is_noop(self):
        # Must NOT import `alerting` (verified by not raising even if alerting
        # could fail).  We don't easily prove the import isn't called, but the
        # function returns silently.
        dispatch_backlog_reminders([])

    def test_one_per_reminder(self):
        reminders = [
            BacklogReminder(
                symbol="AAPL", action="BUY", conviction=0.9, tier=1,
                age_hours=1.0, priority="default",
                title="t1", message="m1",
            ),
            BacklogReminder(
                symbol="MSFT", action="SELL", conviction=0.9, tier=1,
                age_hours=1.0, priority="high",
                title="t2", message="m2",
            ),
        ]
        with mock.patch("alerting.notify") as m_notify:
            dispatch_backlog_reminders(reminders)
            assert m_notify.call_count == 2

    def test_failure_does_not_block_subsequent_reminders(self):
        reminders = [
            BacklogReminder(
                symbol="AAPL", action="BUY", conviction=0.9, tier=1,
                age_hours=1.0, priority="default",
                title="t1", message="m1",
            ),
            BacklogReminder(
                symbol="MSFT", action="SELL", conviction=0.9, tier=1,
                age_hours=1.0, priority="high",
                title="t2", message="m2",
            ),
        ]
        side_effects = [RuntimeError("network down"), None]
        with mock.patch("alerting.notify", side_effect=side_effects) as m_notify:
            # Must NOT raise.
            dispatch_backlog_reminders(reminders)
            assert m_notify.call_count == 2

    def test_dashboard_url_appended_to_message(self):
        reminders = [
            BacklogReminder(
                symbol="AAPL", action="BUY", conviction=0.9, tier=1,
                age_hours=1.0, priority="default",
                title="t1", message="m1",
            ),
        ]
        with mock.patch("alerting.notify") as m_notify:
            dispatch_backlog_reminders(reminders, dashboard_url="http://x")
            kwargs = m_notify.call_args.kwargs
            assert "http://x" in kwargs["message"]


# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------


class TestModuleSurface:
    def test_backlog_entry_to_dict_roundtrip(self):
        b = BacklogEntry(
            symbol="AAPL", action="BUY", conviction=0.9,
            first_seen_iso="2025-06-30T14:00:00+00:00",
            last_pinged_iso="",
            reminders_sent=0,
        )
        assert BacklogEntry.from_dict(b.to_dict()) == b

    def test_agent_state_to_dict_roundtrip(self):
        s = AgentState(
            cycle_count=3,
            backlog={
                "AAPL:BUY": BacklogEntry(
                    symbol="AAPL", action="BUY", conviction=0.9,
                    first_seen_iso="2025-06-30T14:00:00+00:00",
                    last_pinged_iso="",
                    reminders_sent=0,
                )
            },
        )
        round_tripped = AgentState.from_dict(s.to_dict())
        assert round_tripped.cycle_count == 3
        assert round_tripped.backlog == s.backlog

    def test_agent_state_from_dict_drops_corrupt_backlog_entry(self):
        bad = {"backlog": {"AAPL:BUY": "not-a-dict"}}
        s = AgentState.from_dict(bad)
        # Corrupt entries are silently dropped — never raise.
        assert s.backlog == {}

    def test_agent_state_roundtrips_trade_signal_fields(self):
        # New Tier 6.1 state: conviction history + per-ability debounce flags.
        s = AgentState(
            conviction_history={"AAPL": [0.5, 0.6, 0.7]},
            momentum_alerted={"AAPL": "building"},
            price_trigger_alerted={"NVDA": "stop"},
        )
        rt = AgentState.from_dict(s.to_dict())
        assert rt.conviction_history == {"AAPL": [0.5, 0.6, 0.7]}
        assert rt.momentum_alerted == {"AAPL": "building"}
        assert rt.price_trigger_alerted == {"NVDA": "stop"}

    def test_agent_state_from_dict_drops_corrupt_history(self):
        # A non-numeric history value must not crash rehydration (CONSTRAINT #6).
        s = AgentState.from_dict({"conviction_history": {"X": ["bad", 0.5]},
                                  "momentum_alerted": None})
        assert "X" not in s.conviction_history
        assert s.momentum_alerted == {}

    def test_config_has_expected_keys(self):
        required = {
            "rth_normal_delay_s", "rth_high_vol_delay_s",
            "rth_open_close_delay_s", "rth_open_close_window_minutes",
            "extended_hours_delay_s", "off_hours_delay_s",
            "error_backoff_base_s", "error_backoff_max_s",
            "vol_spike_vix_threshold", "high_vol_regimes",
            "min_delay_s",
            "backlog_conviction_threshold", "backlog_tier_hours",
            "backlog_tier_priorities", "backlog_max_reminders",
            "backlog_expiry_hours",
            "decision_log_match_window_hours",
        }
        assert required.issubset(set(CONFIG.keys()))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso_to_dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))
