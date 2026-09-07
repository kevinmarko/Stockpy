"""Unit tests for ``pilots/radar_ranking.py`` — "Today's Radar" ranking helper.

Empty-table/missing-snapshot cases are tested FIRST (per the introducing
plan's own instruction) since a fresh clone/CI genuinely has no
``state_snapshot.json`` yet. Offline: the committed
``tests/fixtures/state_snapshot.json`` (8 signals: AAPL/MSFT/NVDA/JPM/XOM/
JNJ/PG/T, all with real, non-null ``multifactor_composite``) for the happy
path, plus small inline synthetic snapshots for the honesty/degradation
edges. No network, no engines.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pilots.scoring import load_snapshot
from pilots.radar_ranking import radar_feed

FIXTURE = Path(__file__).parent / "fixtures" / "state_snapshot.json"


@pytest.fixture()
def snapshot() -> dict:
    snap = load_snapshot(str(FIXTURE))
    assert snap is not None, "committed fixture snapshot must load"
    return snap


# ---------------------------------------------------------------------------
# Empty / cold-start / malformed snapshot — written FIRST per the plan
# ---------------------------------------------------------------------------

class TestRadarFeedEmptyStates:
    def test_no_snapshot_yet(self):
        result = radar_feed(None)
        assert result["items"] == []
        assert result["as_of"] is None
        assert result["reason"] == "No state snapshot yet — run the pipeline first."

    def test_malformed_snapshot_not_a_dict(self):
        result = radar_feed("not a dict")
        assert result["items"] == []
        assert result["reason"] == "No state snapshot yet — run the pipeline first."

    def test_missing_signals_key(self):
        result = radar_feed({"timestamp": "2026-01-01T00:00:00Z", "tickers": ["AAPL"]})
        assert result["items"] == []
        assert result["reason"] == "State snapshot malformed or missing signals."
        assert result["as_of"] == "2026-01-01T00:00:00Z"

    def test_signals_not_a_list(self):
        result = radar_feed({"timestamp": "t", "tickers": ["AAPL"], "signals": "nope"})
        assert result["items"] == []
        assert result["reason"] == "State snapshot malformed or missing signals."

    def test_missing_tickers_key(self):
        result = radar_feed({"timestamp": "t", "signals": [{"symbol": "AAPL", "multifactor_composite": 1.0}]})
        assert result["items"] == []
        assert result["reason"] == "No tracked universe in the latest snapshot."

    def test_empty_tickers_list(self):
        result = radar_feed({"timestamp": "t", "tickers": [], "signals": []})
        assert result["items"] == []
        assert result["reason"] == "No tracked universe in the latest snapshot."

    def test_real_snapshot_but_no_symbol_has_a_composite(self):
        snap = {
            "timestamp": "t",
            "tickers": ["AAPL", "MSFT"],
            "signals": [
                {"symbol": "AAPL", "multifactor_composite": None},
                {"symbol": "MSFT", "multifactor_composite": float("nan")},
            ],
        }
        result = radar_feed(snap)
        assert result["items"] == []
        assert result["reason"] == "No signals computed yet for this cycle."
        assert result["as_of"] == "t"

    def test_never_raises_on_garbage_signal_entries(self):
        snap = {
            "timestamp": "t",
            "tickers": ["AAPL"],
            "signals": [None, 123, "x", {"no_symbol": 1}, {"symbol": ""}],
        }
        result = radar_feed(snap)
        assert result["items"] == []
        assert result["reason"] == "No signals computed yet for this cycle."


# ---------------------------------------------------------------------------
# Ranking — real fixture, known expected Top-N ordering
# ---------------------------------------------------------------------------

class TestRadarFeedRanking:
    def test_ranks_by_multifactor_composite_descending(self, snapshot):
        result = radar_feed(snapshot, limit=10)
        assert result["reason"] is None
        assert result["as_of"] == snapshot["timestamp"]
        symbols = [item["symbol"] for item in result["items"]]
        # Expected order from the fixture's real values:
        # XOM .50, PG .33, JNJ .30, JPM .40 -> sorted desc: XOM .50, JPM .40,
        # PG .33, JNJ .30, NVDA .25, MSFT .28, AAPL .21, T .10
        expected_desc = sorted(
            (s["symbol"] for s in snapshot["signals"]),
            key=lambda sym: -next(
                s["multifactor_composite"] for s in snapshot["signals"] if s["symbol"] == sym
            ),
        )
        assert symbols == expected_desc
        assert symbols[0] == "XOM"  # highest composite (0.50) in the fixture

    def test_rank_field_is_1_indexed_and_sequential(self, snapshot):
        result = radar_feed(snapshot, limit=10)
        ranks = [item["rank"] for item in result["items"]]
        assert ranks == list(range(1, len(ranks) + 1))

    def test_limit_clamps_result_size(self, snapshot):
        result = radar_feed(snapshot, limit=3)
        assert len(result["items"]) == 3

    def test_limit_out_of_range_falls_back_to_default(self, snapshot):
        # int(limit) succeeds but is coerced into [1, 50] by max/min, never a
        # crash on a wild value.
        result = radar_feed(snapshot, limit=0)
        assert len(result["items"]) == 1
        result = radar_feed(snapshot, limit=999)
        assert len(result["items"]) == len(snapshot["signals"])

    def test_limit_non_numeric_falls_back_to_default(self, snapshot):
        result = radar_feed(snapshot, limit="garbage")  # type: ignore[arg-type]
        assert len(result["items"]) == min(10, len(snapshot["signals"]))

    def test_item_carries_expected_fields(self, snapshot):
        result = radar_feed(snapshot, limit=1)
        item = result["items"][0]
        assert set(item) == {
            "symbol", "rank", "multifactor_composite", "value_z", "quality_z",
            "lowvol_z", "size_z", "sector", "price", "reason",
        }
        assert item["symbol"] == "XOM"
        assert item["multifactor_composite"] == 0.5

    def test_ties_broken_by_symbol_ascending(self):
        snap = {
            "timestamp": "t",
            "tickers": ["ZZZ", "AAA"],
            "signals": [
                {"symbol": "ZZZ", "multifactor_composite": 0.5},
                {"symbol": "AAA", "multifactor_composite": 0.5},
            ],
        }
        result = radar_feed(snap)
        assert [i["symbol"] for i in result["items"]] == ["AAA", "ZZZ"]


# ---------------------------------------------------------------------------
# Universe restriction — never beyond the tracked universe
# ---------------------------------------------------------------------------

class TestRadarFeedUniverseRestriction:
    def test_excludes_signal_beyond_tracked_universe(self):
        """A benchmark/proxy row (e.g. SPY) present in signals[] but absent
        from tickers[] must never appear in the feed -- matches the real,
        live-observed state_snapshot.json shape (signals[] can carry one more
        entry than tickers[])."""
        snap = {
            "timestamp": "t",
            "tickers": ["AAPL"],
            "signals": [
                {"symbol": "AAPL", "multifactor_composite": 0.1},
                {"symbol": "SPY", "multifactor_composite": 999.0},
            ],
        }
        result = radar_feed(snap)
        symbols = [i["symbol"] for i in result["items"]]
        assert "SPY" not in symbols
        assert symbols == ["AAPL"]

    def test_excludes_partial_coverage_symbol(self):
        """A symbol with no computed composite this cycle is excluded
        entirely -- never backfilled with a placeholder."""
        snap = {
            "timestamp": "t",
            "tickers": ["AAPL", "MSFT", "SRET"],
            "signals": [
                {"symbol": "AAPL", "multifactor_composite": 0.1},
                {"symbol": "MSFT", "multifactor_composite": 0.2},
                {"symbol": "SRET", "multifactor_composite": None},
            ],
        }
        result = radar_feed(snap)
        symbols = [i["symbol"] for i in result["items"]]
        assert "SRET" not in symbols
        assert set(symbols) == {"AAPL", "MSFT"}

    def test_case_insensitive_ticker_matching(self):
        snap = {
            "timestamp": "t",
            "tickers": ["aapl"],
            "signals": [{"symbol": "AAPL", "multifactor_composite": 0.1}],
        }
        result = radar_feed(snap)
        assert [i["symbol"] for i in result["items"]] == ["AAPL"]


# ---------------------------------------------------------------------------
# Reason-string templating — never a clause about a None/NaN field
# ---------------------------------------------------------------------------

class TestRadarFeedReasonStrings:
    def test_reason_never_mentions_a_none_subfactor(self):
        snap = {
            "timestamp": "t",
            "tickers": ["AAPL"],
            "signals": [{
                "symbol": "AAPL",
                "multifactor_composite": 0.5,
                "value_z": None,
                "quality_z": None,
                "lowvol_z": None,
                "size_z": None,
            }],
        }
        result = radar_feed(snap)
        reason = result["items"][0]["reason"]
        assert "None" not in reason
        assert "nan" not in reason.lower()
        assert reason == "Highest Multifactor Composite in the tracked universe today."

    def test_reason_never_mentions_a_nan_subfactor(self):
        snap = {
            "timestamp": "t",
            "tickers": ["AAPL"],
            "signals": [{
                "symbol": "AAPL",
                "multifactor_composite": 0.5,
                "value_z": float("nan"),
                "quality_z": 1.8,
                "lowvol_z": float("nan"),
                "size_z": None,
            }],
        }
        result = radar_feed(snap)
        reason = result["items"][0]["reason"]
        assert "nan" not in reason.lower()
        assert "Quality Z +1.8" in reason
        assert "Value Z" not in reason
        assert "Low-Vol Z" not in reason
        assert "Size Z" not in reason

    def test_reason_names_top_two_subfactors_by_magnitude(self):
        snap = {
            "timestamp": "t",
            "tickers": ["AAPL"],
            "signals": [{
                "symbol": "AAPL",
                "multifactor_composite": 0.5,
                "value_z": 0.1,
                "quality_z": 1.8,
                "lowvol_z": -1.2,
                "size_z": 0.05,
            }],
        }
        result = radar_feed(snap)
        reason = result["items"][0]["reason"]
        assert "Quality Z +1.8" in reason
        assert "Low-Vol Z -1.2" in reason
        assert "Value Z" not in reason
        assert "Size Z" not in reason

    def test_rank_zero_uses_highest_phrasing_others_use_hash_n(self):
        snap = {
            "timestamp": "t",
            "tickers": ["A", "B"],
            "signals": [
                {"symbol": "A", "multifactor_composite": 1.0},
                {"symbol": "B", "multifactor_composite": 0.5},
            ],
        }
        result = radar_feed(snap)
        assert result["items"][0]["reason"].startswith("Highest Multifactor Composite")
        assert result["items"][1]["reason"].startswith("#2 by Multifactor Composite")

    def test_reason_is_never_empty_string(self, snapshot):
        result = radar_feed(snapshot, limit=10)
        for item in result["items"]:
            assert item["reason"]
            assert isinstance(item["reason"], str)
