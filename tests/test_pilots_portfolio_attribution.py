"""Unit tests for ``pilots/portfolio_attribution.py`` — the pure proxy
attribution math behind ``get_portfolio_by_pilot``.

All fixtures are offline: hand-built ``AccountSnapshot``-shaped ``SimpleNamespace``
objects (duck-typed, mirroring ``execution/compose.py``'s own account_snapshot
convention) and plain follow-row dicts matching ``FollowsStore.list_all()``'s
real schema. No network, no heavy engines, no MCP server involved (see
``tests/test_investyo_mcp_server.py::TestGetPortfolioByPilot`` for the
tool-wiring level).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from pilots.portfolio_attribution import ATTRIBUTION_NOTE, attribute_portfolio_by_pilot


def _position(market_value, unrealized_pl=0.0):
    return SimpleNamespace(market_value=market_value, unrealized_pl=unrealized_pl)


def _snapshot(positions, fetched_at=None):
    return SimpleNamespace(
        positions=positions,
        fetched_at=fetched_at or datetime(2026, 8, 1, tzinfo=timezone.utc),
    )


def _follow(pilot_id, mirrored=None, status="active", mirrored_updated_at="t1"):
    row = {"pilot_id": pilot_id, "status": status}
    if mirrored is not None:
        row["mirrored"] = mirrored
        row["mirrored_updated_at"] = mirrored_updated_at
    return row


# ---------------------------------------------------------------------------
# Degenerate inputs -- honest empty shapes, never raise
# ---------------------------------------------------------------------------


class TestDegenerateInputs:
    def test_no_account_snapshot(self):
        result = attribute_portfolio_by_pilot(None, [])
        assert result["pilots"] == []
        assert result["unattributed"] == []
        assert result["reason"] == "no account snapshot on record"
        assert result["attribution_basis"] == "proxy"
        assert result["note"] == ATTRIBUTION_NOTE

    def test_snapshot_with_no_positions(self):
        result = attribute_portfolio_by_pilot(_snapshot({}), [])
        assert result["pilots"] == []
        assert result["reason"] == "no positions on the account snapshot"

    def test_snapshot_with_only_non_positive_market_value(self):
        snap = _snapshot({"AAPL": _position(0.0)})
        result = attribute_portfolio_by_pilot(snap, [_follow("trend-following", [
            {"symbol": "AAPL", "weight": 1.0, "target_notional": 500.0},
        ])])
        assert result["pilots"] == []
        assert "positive market value" in result["reason"]

    def test_no_follows_at_all(self):
        snap = _snapshot({"AAPL": _position(1000.0, 100.0)})
        result = attribute_portfolio_by_pilot(snap, None)
        assert result["pilots"] == []
        assert result["unattributed"] == []
        assert result["reason"] is not None

    def test_never_raises_on_malformed_follow_rows(self):
        snap = _snapshot({"AAPL": _position(1000.0, 100.0)})
        malformed = [None, "not-a-dict", {}, {"pilot_id": ""}, {"pilot_id": "x", "mirrored": "not-a-list"}]
        result = attribute_portfolio_by_pilot(snap, malformed)
        assert result["pilots"] == []


# ---------------------------------------------------------------------------
# Missing mirrored field -- absent from the breakdown, never zero-filled
# ---------------------------------------------------------------------------


class TestMissingMirroredField:
    def test_follow_without_mirrored_key_is_excluded(self):
        snap = _snapshot({"AAPL": _position(1000.0, 100.0)})
        follows = [_follow("trend-following")]  # no "mirrored" key at all
        result = attribute_portfolio_by_pilot(snap, follows)
        assert result["pilots"] == []
        assert "no follow has an attributable claim" in result["reason"]

    def test_follow_with_empty_mirrored_list_is_excluded(self):
        snap = _snapshot({"AAPL": _position(1000.0, 100.0)})
        follows = [_follow("trend-following", mirrored=[])]
        result = attribute_portfolio_by_pilot(snap, follows)
        assert result["pilots"] == []

    def test_mirrored_entry_missing_target_notional_contributes_nothing(self):
        snap = _snapshot({"AAPL": _position(1000.0, 100.0)})
        follows = [_follow("trend-following", mirrored=[{"symbol": "AAPL", "weight": 0.5}])]
        result = attribute_portfolio_by_pilot(snap, follows)
        assert result["pilots"] == []

    def test_mirrored_symbol_not_currently_held_contributes_nothing(self):
        snap = _snapshot({"AAPL": _position(1000.0, 100.0)})
        follows = [_follow("trend-following", mirrored=[
            {"symbol": "TSLA", "weight": 1.0, "target_notional": 500.0},
        ])]
        result = attribute_portfolio_by_pilot(snap, follows)
        assert result["pilots"] == []

    def test_mixed_unclaimed_symbol_alongside_a_real_claim(self):
        """AAPL is held but claimed by no follow; MSFT IS claimed. AAPL must
        surface honestly in the unattributed bucket rather than being
        silently dropped just because it has no claimant."""
        snap = _snapshot({
            "AAPL": _position(1000.0, 100.0),
            "MSFT": _position(500.0, 0.0),
        })
        follows = [
            _follow("trend-following", mirrored=[
                {"symbol": "TSLA", "weight": 1.0, "target_notional": 500.0},  # not held
                {"symbol": "MSFT", "weight": 1.0, "target_notional": 500.0},
            ]),
        ]
        result = attribute_portfolio_by_pilot(snap, follows)
        assert len(result["pilots"]) == 1
        assert result["pilots"][0]["attributed_market_value"] == 500.0
        assert result["unattributed"] == [{"symbol": "AAPL", "value": 1000.0}]


# ---------------------------------------------------------------------------
# Single-pilot claim -- no overlap, exact pass-through
# ---------------------------------------------------------------------------


class TestSinglePilotClaim:
    def test_full_claim_under_market_value(self):
        snap = _snapshot({"AAPL": _position(1000.0, 200.0)})
        follows = [_follow("trend-following", mirrored=[
            {"symbol": "AAPL", "weight": 1.0, "target_notional": 600.0},
        ])]
        result = attribute_portfolio_by_pilot(snap, follows, pilot_names={"trend-following": "Trend Follower"})

        assert result["reason"] is None
        assert len(result["pilots"]) == 1
        p = result["pilots"][0]
        assert p["pilot_id"] == "trend-following"
        assert p["pilot_name"] == "Trend Follower"
        assert p["attributed_market_value"] == 600.0
        # pro-rated P&L: (600/1000) * 200 == 120.0
        assert p["attributed_unrealized_pl"] == 120.0
        assert p["attributed_unrealized_pl_pct"] == 0.2
        assert p["positions"] == [
            {"symbol": "AAPL", "attributed_value": 600.0, "attributed_unrealized_pl": 120.0, "overlap_scaled": False}
        ]
        # $400 of the $1000 position is unattributed.
        assert result["unattributed"] == [{"symbol": "AAPL", "value": 400.0}]

    def test_claim_capped_at_market_value_when_target_exceeds_holding(self):
        """target_notional (2000) > market_value (1000) -> capped at 1000,
        the same min(last target notional, currently held market value)
        formula pilots/mirror.py's own force-exit logic uses."""
        snap = _snapshot({"AAPL": _position(1000.0, 100.0)})
        follows = [_follow("trend-following", mirrored=[
            {"symbol": "AAPL", "weight": 1.0, "target_notional": 2000.0},
        ])]
        result = attribute_portfolio_by_pilot(snap, follows)

        p = result["pilots"][0]
        assert p["attributed_market_value"] == 1000.0
        assert p["attributed_unrealized_pl"] == 100.0
        assert result["unattributed"] == []
        assert p["positions"][0]["overlap_scaled"] is False

    def test_cancelled_follow_still_attributed(self):
        """A cancelled (unfollowed) Pilot's residual mirrored holdings must
        still be attributed -- unfollow_pilot's whole honesty promise
        depends on this."""
        snap = _snapshot({"AAPL": _position(1000.0, 50.0)})
        follows = [_follow("trend-following", mirrored=[
            {"symbol": "AAPL", "weight": 1.0, "target_notional": 500.0},
        ], status="cancelled")]
        result = attribute_portfolio_by_pilot(snap, follows)
        assert len(result["pilots"]) == 1
        assert result["pilots"][0]["attributed_market_value"] == 500.0


# ---------------------------------------------------------------------------
# Overlapping-pilot claim -- requires scale-down
# ---------------------------------------------------------------------------


class TestOverlapNormalization:
    def test_two_pilots_overclaiming_same_symbol_are_scaled_down(self):
        snap = _snapshot({"AAPL": _position(1000.0, 100.0)})
        follows = [
            _follow("trend-following", mirrored=[
                {"symbol": "AAPL", "weight": 1.0, "target_notional": 800.0},
            ]),
            _follow("dip-buyer", mirrored=[
                {"symbol": "AAPL", "weight": 1.0, "target_notional": 800.0},
            ]),
        ]
        result = attribute_portfolio_by_pilot(snap, follows)

        by_id = {p["pilot_id"]: p for p in result["pilots"]}
        assert set(by_id) == {"trend-following", "dip-buyer"}
        # scale = 1000 / (800+800) = 0.625 -> each scaled claim = 500.0
        assert by_id["trend-following"]["attributed_market_value"] == 500.0
        assert by_id["dip-buyer"]["attributed_market_value"] == 500.0
        # Combined attribution never exceeds the real market value.
        total_attributed = sum(p["attributed_market_value"] for p in result["pilots"])
        assert total_attributed == 1000.0
        # P&L is pro-rated on the SCALED claim, not the raw one.
        assert by_id["trend-following"]["attributed_unrealized_pl"] == 50.0
        assert by_id["dip-buyer"]["attributed_unrealized_pl"] == 50.0
        # Every affected row is labelled.
        assert by_id["trend-following"]["positions"][0]["overlap_scaled"] is True
        assert by_id["dip-buyer"]["positions"][0]["overlap_scaled"] is True
        # Fully attributed -> nothing left in the unattributed bucket.
        assert result["unattributed"] == []

    def test_overlap_scaling_is_per_symbol_not_global(self):
        """Pilot A claims AAPL+MSFT, Pilot B claims only AAPL (overclaiming
        it). MSFT (uncontested) must NOT be scaled down by AAPL's overlap."""
        snap = _snapshot({
            "AAPL": _position(1000.0, 0.0),
            "MSFT": _position(500.0, 0.0),
        })
        follows = [
            _follow("trend-following", mirrored=[
                {"symbol": "AAPL", "weight": 0.5, "target_notional": 800.0},
                {"symbol": "MSFT", "weight": 0.5, "target_notional": 400.0},
            ]),
            _follow("dip-buyer", mirrored=[
                {"symbol": "AAPL", "weight": 1.0, "target_notional": 800.0},
            ]),
        ]
        result = attribute_portfolio_by_pilot(snap, follows)
        by_id = {p["pilot_id"]: p for p in result["pilots"]}

        msft_row = next(pos for pos in by_id["trend-following"]["positions"] if pos["symbol"] == "MSFT")
        assert msft_row["attributed_value"] == 400.0
        assert msft_row["overlap_scaled"] is False

        aapl_row = next(pos for pos in by_id["trend-following"]["positions"] if pos["symbol"] == "AAPL")
        assert aapl_row["overlap_scaled"] is True
        # scale = 1000 / (800+800) = 0.625
        assert aapl_row["attributed_value"] == 500.0


# ---------------------------------------------------------------------------
# Zero-market-value position
# ---------------------------------------------------------------------------


class TestZeroMarketValuePosition:
    def test_zero_market_value_position_excluded_no_divide_by_zero(self):
        snap = _snapshot({
            "AAPL": _position(0.0, 0.0),
            "MSFT": _position(1000.0, 100.0),
        })
        follows = [_follow("trend-following", mirrored=[
            {"symbol": "AAPL", "weight": 0.5, "target_notional": 500.0},
            {"symbol": "MSFT", "weight": 0.5, "target_notional": 500.0},
        ])]
        result = attribute_portfolio_by_pilot(snap, follows)

        p = result["pilots"][0]
        symbols = {pos["symbol"] for pos in p["positions"]}
        assert "AAPL" not in symbols  # zero-value position never attributed
        assert "MSFT" in symbols
        assert p["attributed_market_value"] == 500.0

    def test_negative_market_value_position_excluded(self):
        snap = _snapshot({"AAPL": _position(-10.0, 0.0)})
        follows = [_follow("trend-following", mirrored=[
            {"symbol": "AAPL", "weight": 1.0, "target_notional": 500.0},
        ])]
        result = attribute_portfolio_by_pilot(snap, follows)
        assert result["pilots"] == []


# ---------------------------------------------------------------------------
# Stale account snapshot -- as_of surfaces the real (possibly old) timestamp
# ---------------------------------------------------------------------------


class TestStaleAccountSnapshot:
    def test_as_of_reflects_stale_fetched_at_honestly(self):
        old_ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
        snap = _snapshot({"AAPL": _position(1000.0, 100.0)}, fetched_at=old_ts)
        follows = [_follow("trend-following", mirrored=[
            {"symbol": "AAPL", "weight": 1.0, "target_notional": 500.0},
        ])]
        result = attribute_portfolio_by_pilot(snap, follows)

        assert result["as_of"] == old_ts.isoformat()
        # Staleness is surfaced, not hidden -- but math still runs normally.
        assert result["pilots"][0]["attributed_market_value"] == 500.0

    def test_mirrored_updated_at_surfaced_per_pilot(self):
        snap = _snapshot({"AAPL": _position(1000.0, 100.0)})
        follows = [_follow(
            "trend-following",
            mirrored=[{"symbol": "AAPL", "weight": 1.0, "target_notional": 500.0}],
            mirrored_updated_at="2026-06-01T00:00:00+00:00",
        )]
        result = attribute_portfolio_by_pilot(snap, follows)
        assert result["pilots"][0]["mirrored_updated_at"] == "2026-06-01T00:00:00+00:00"

    def test_as_of_none_when_fetched_at_not_datetime(self):
        """Duck-typed input: a plain dict position/snapshot (no .isoformat())
        degrades to whatever raw value is present rather than raising."""
        snap = {"positions": {"AAPL": {"market_value": 1000.0, "unrealized_pl": 0.0}}, "fetched_at": None}
        follows = [_follow("trend-following", mirrored=[
            {"symbol": "AAPL", "weight": 1.0, "target_notional": 500.0},
        ])]
        result = attribute_portfolio_by_pilot(snap, follows)
        assert result["as_of"] is None
        assert result["pilots"][0]["attributed_market_value"] == 500.0


# ---------------------------------------------------------------------------
# Never fabricates -- pilot_names lookup miss stays honestly None
# ---------------------------------------------------------------------------


class TestPilotNameLookup:
    def test_unknown_pilot_id_gets_none_name_not_fabricated(self):
        snap = _snapshot({"AAPL": _position(1000.0, 0.0)})
        follows = [_follow("ghost-pilot", mirrored=[
            {"symbol": "AAPL", "weight": 1.0, "target_notional": 500.0},
        ])]
        result = attribute_portfolio_by_pilot(snap, follows, pilot_names={})
        assert result["pilots"][0]["pilot_name"] is None

    def test_no_pilot_names_arg_is_optional(self):
        snap = _snapshot({"AAPL": _position(1000.0, 0.0)})
        follows = [_follow("trend-following", mirrored=[
            {"symbol": "AAPL", "weight": 1.0, "target_notional": 500.0},
        ])]
        result = attribute_portfolio_by_pilot(snap, follows)
        assert result["pilots"][0]["pilot_name"] is None
