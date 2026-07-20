"""tests/test_circuit_breakers.py — coverage for the Safety-tab breaker derivation.

Exercises ``gui/circuit_breakers.py`` without Streamlit. Three groups:

1.  ``read_block_log`` — happy path, missing file, corrupt-line tolerance.
2.  ``derive_kill_switch_trip`` — sentinel absent / present / with reason.
3.  ``derive_block_log_trips`` — known checks classified correctly, unknown
    checks bubble through tagged WARNING, ``window`` filter drops old rows,
    most-recent-per-(name,strategy) deduping.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gui.circuit_breakers import (
    CircuitBreakerTrip,
    collect_circuit_breaker_trips,
    derive_block_log_trips,
    derive_kill_switch_trip,
    read_block_log,
    summarise_trips,
)


# ---------------------------------------------------------------------------
# 1. Block-log reader
# ---------------------------------------------------------------------------

class TestReadBlockLog:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert read_block_log(tmp_path / "nope.jsonl") == []

    def test_happy_path_newest_first(self, tmp_path: Path) -> None:
        path = tmp_path / "blocks.jsonl"
        rows = [
            {"check_name": "max_position_size", "symbol": "AAPL",
             "timestamp": "2026-06-26T08:00:00+00:00"},
            {"check_name": "portfolio_heat", "threshold": 0.06,
             "timestamp": "2026-06-26T09:00:00+00:00"},
        ]
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        out = read_block_log(path)
        # Newest first: portfolio_heat at 09:00, then max_position_size.
        assert out[0]["check_name"] == "portfolio_heat"
        assert out[1]["check_name"] == "max_position_size"

    def test_corrupt_line_dropped_not_raised(self, tmp_path: Path) -> None:
        path = tmp_path / "blocks.jsonl"
        path.write_text(
            '{"check_name": "max_position_size"}\n'
            "this is not json\n"
            '{"check_name": "portfolio_heat"}\n'
        )
        out = read_block_log(path)
        assert len(out) == 2
        names = sorted(r["check_name"] for r in out)
        assert names == ["max_position_size", "portfolio_heat"]


# ---------------------------------------------------------------------------
# 2. Kill switch derivation
# ---------------------------------------------------------------------------

class TestDeriveKillSwitchTrip:
    def test_absent_returns_none(self, tmp_path: Path) -> None:
        assert derive_kill_switch_trip(tmp_path / "KILL_SWITCH") is None

    def test_sentinel_present_makes_critical_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "KILL_SWITCH"
        path.write_text("Manual halt from operator")
        trip = derive_kill_switch_trip(path)
        assert trip is not None
        assert trip.severity == "CRITICAL"
        assert trip.name == "global_kill_switch"
        assert "Manual halt" in trip.summary
        assert trip.triggered_at is not None  # mtime present


# ---------------------------------------------------------------------------
# 3. Block-log → trip projection
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 6, 26, 12, 0, 0, tzinfo=timezone.utc)


def _block(check: str, *, ts_offset: timedelta = timedelta(), **kwargs) -> dict:
    base = {
        "check_name": check,
        "timestamp": (_NOW + ts_offset).isoformat(),
    }
    base.update(kwargs)
    return base


class TestDeriveBlockLogTrips:
    def test_known_check_classified(self) -> None:
        trips = derive_block_log_trips(
            [_block("daily_loss_limit", threshold=0.05, strategy_id="momentum")],
            now=_NOW,
        )
        assert len(trips) == 1
        t = trips[0]
        assert t.name == "daily_loss_limit"
        assert t.severity == "CRITICAL"
        assert t.threshold == 0.05

    def test_unknown_check_bubbles_through_as_warning(self) -> None:
        trips = derive_block_log_trips(
            [_block("some_new_check", strategy_id="x")], now=_NOW,
        )
        assert len(trips) == 1
        assert trips[0].name == "some_new_check"
        assert trips[0].severity == "WARNING"

    def test_window_filter_drops_old(self) -> None:
        old_block = _block("max_position_size",
                           ts_offset=-timedelta(days=2),
                           symbol="AAPL")
        recent_block = _block("max_position_size",
                              ts_offset=-timedelta(minutes=5),
                              symbol="MSFT")
        trips = derive_block_log_trips(
            [old_block, recent_block], window=timedelta(hours=24), now=_NOW,
        )
        symbols = [t.detail.get("symbol") for t in trips]
        assert symbols == ["MSFT"]

    def test_dedup_per_breaker_keeps_newest(self) -> None:
        """Two daily_loss_limit blocks for the same strategy → keep newest."""
        blocks = [
            _block("daily_loss_limit", strategy_id="momentum",
                   ts_offset=-timedelta(hours=2), observed=0.04),
            _block("daily_loss_limit", strategy_id="momentum",
                   ts_offset=-timedelta(minutes=1), observed=0.06),
        ]
        trips = derive_block_log_trips(blocks, now=_NOW)
        assert len(trips) == 1
        assert trips[0].observed == 0.06

    def test_dedup_keyed_on_strategy(self) -> None:
        """Same breaker but different strategies → both surface."""
        blocks = [
            _block("daily_loss_limit", strategy_id="momentum"),
            _block("daily_loss_limit", strategy_id="mean_reversion"),
        ]
        trips = derive_block_log_trips(blocks, now=_NOW)
        strategies = sorted(t.detail.get("strategy_id") for t in trips)
        assert strategies == ["mean_reversion", "momentum"]

    @pytest.mark.parametrize("check", ["portfolio_heat", "daily_loss_limit"])
    def test_missing_threshold_never_renders_fabricated_nan_percent(self, check: str) -> None:
        """Regression: a known check whose summary template needs
        ``{threshold:.0%}`` but has no recorded threshold must fall back to
        the generic summary, never silently render the literal string
        "nan%" (CONSTRAINT #4). Before the fix, the missing threshold was
        smuggled in as ``float("nan")``, and
        ``"{:.0%}".format(float("nan"))`` renders "nan%" without raising —
        so the surrounding ``except (KeyError, ValueError)`` never caught it
        and the fabricated value reached the operator undetected."""
        trips = derive_block_log_trips([_block(check)], now=_NOW)
        assert len(trips) == 1
        assert "nan" not in trips[0].summary.lower()
        assert trips[0].summary == f"{check} blocked order"
        assert trips[0].threshold is None


class TestCollectAndSummarise:
    def test_collect_orders_kill_switch_first(self, tmp_path: Path) -> None:
        sentinel = tmp_path / "KILL_SWITCH"
        sentinel.write_text("manual")
        block_log = tmp_path / "blocks.jsonl"
        block_log.write_text(json.dumps(_block("portfolio_heat",
                                               ts_offset=-timedelta(minutes=5))) + "\n")
        trips = collect_circuit_breaker_trips(
            kill_switch_sentinel=sentinel, block_log_path=block_log, now=_NOW,
        )
        assert trips[0].name == "global_kill_switch"

    def test_summarise_tally(self) -> None:
        trips = [
            CircuitBreakerTrip("a", "CRITICAL", "x"),
            CircuitBreakerTrip("b", "WARNING", "y"),
            CircuitBreakerTrip("c", "WARNING", "z"),
        ]
        s = summarise_trips(trips)
        assert s["CRITICAL"] == 1
        assert s["WARNING"] == 2
        assert s["TOTAL"] == 3
