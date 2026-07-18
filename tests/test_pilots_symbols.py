"""Unit tests for ``pilots/symbols.py`` — the symbol-centric snapshot readers.

Offline: the committed ``tests/fixtures/state_snapshot.json`` (advisory-style,
8 signals: AAPL/MSFT/NVDA/JPM/XOM/JNJ/PG/T) for the real cases, plus tiny inline
synthetic snapshots for the honesty/degradation edges. No network, no engines.

The fixture is the honest-null bed: it genuinely lacks ``mfe``/``mae``/
``edge_ratio``/``macro_status``/``xsec_12_1m``/``xsec_momentum_rank`` on every
signal, so those must surface as ``None`` (never ``0.0``).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pilots.catalog import get_pilot
from pilots.scoring import load_snapshot
from pilots.symbols import (
    find_signal,
    held_by_pilots,
    list_recommendations,
    list_universe,
    symbol_detail,
)

FIXTURE = Path(__file__).parent / "fixtures" / "state_snapshot.json"


@pytest.fixture()
def snapshot() -> dict:
    snap = load_snapshot(str(FIXTURE))
    assert snap is not None, "committed fixture snapshot must load"
    return snap


# ---------------------------------------------------------------------------
# find_signal
# ---------------------------------------------------------------------------

class TestFindSignal:
    def test_found(self, snapshot):
        sig = find_signal(snapshot, "AAPL")
        assert sig is not None
        assert sig["symbol"] == "AAPL"
        assert sig["price"] == 224.15

    def test_case_insensitive(self, snapshot):
        assert find_signal(snapshot, "aapl")["symbol"] == "AAPL"

    def test_strips_whitespace(self, snapshot):
        assert find_signal(snapshot, "  aApL  ")["symbol"] == "AAPL"

    def test_miss_returns_none(self, snapshot):
        assert find_signal(snapshot, "ZZZ") is None

    def test_empty_ticker_none(self, snapshot):
        assert find_signal(snapshot, "") is None
        assert find_signal(snapshot, "   ") is None

    def test_malformed_never_raises(self):
        assert find_signal(None, "AAPL") is None
        assert find_signal({}, "AAPL") is None
        assert find_signal({"signals": None}, "AAPL") is None
        assert find_signal({"signals": "nope"}, "AAPL") is None
        assert find_signal({"signals": [{"no_symbol": 1}]}, "AAPL") is None
        assert find_signal({"signals": [123, "x"]}, "AAPL") is None


# ---------------------------------------------------------------------------
# symbol_detail — shape, coercion, honesty
# ---------------------------------------------------------------------------

class TestSymbolDetail:
    def test_top_level_keys(self, snapshot):
        d = symbol_detail(snapshot, "AAPL")
        assert set(d) == {
            "symbol", "as_of", "reason",
            "identity", "advisory", "factors", "ranges", "risk",
            "held_by_pilots",
        }

    def test_symbol_and_as_of(self, snapshot):
        d = symbol_detail(snapshot, "AAPL")
        assert d["symbol"] == "AAPL"
        assert d["as_of"] == "2026-07-11T21:05:00+00:00"
        assert d["reason"] is None

    def test_case_insensitive_input(self, snapshot):
        assert symbol_detail(snapshot, "aapl")["symbol"] == "AAPL"

    def test_identity_group_exact(self, snapshot):
        # identity carries EXACTLY these four keys (no score, no symbol).
        assert symbol_detail(snapshot, "AAPL")["identity"] == {
            "sector": "Information Technology",
            "price": pytest.approx(224.15),
            "action": "BUY",
            "shares": pytest.approx(40.0),
        }

    def test_advisory_group(self, snapshot):
        adv = symbol_detail(snapshot, "AAPL")["advisory"]
        assert adv["action"] == "BUY"
        assert adv["conviction"] == pytest.approx(0.72)
        assert adv["position_pct"] == pytest.approx(0.041)
        assert adv["kelly_target"] == pytest.approx(0.041)
        assert adv["score"] == pytest.approx(96.8)
        assert adv["rationale"].startswith("Strong momentum")

    def test_factors_present_values(self, snapshot):
        f = symbol_detail(snapshot, "AAPL")["factors"]
        assert f["value_z"] == pytest.approx(-0.42)
        assert f["quality_z"] == pytest.approx(1.15)
        assert f["lowvol_z"] == pytest.approx(0.31)
        assert f["size_z"] == pytest.approx(-1.85)
        assert f["multifactor_composite"] == pytest.approx(0.21)
        assert isinstance(f["score_components"], dict) and f["score_components"]

    def test_ranges_are_strings(self, snapshot):
        r = symbol_detail(snapshot, "AAPL")["ranges"]
        assert r["buy_range"] == "Buy Zone: $210.00 - $222.00"
        assert r["sell_range"] == "Sell Zone: $238.00 - $255.00 | Stop @ $205.00"

    def test_risk_present_values(self, snapshot):
        risk = symbol_detail(snapshot, "AAPL")["risk"]
        assert risk["news_sentiment"] == pytest.approx(0.28)
        assert risk["covar_proxy"] == pytest.approx(0.34)
        assert risk["realized_slippage"] == pytest.approx(0.0009)
        assert risk["hmm_risk_on"] == pytest.approx(0.78)

    def test_fixture_absent_risk_fields_are_none(self, snapshot):
        risk = symbol_detail(snapshot, "AAPL")["risk"]
        for k in ("mfe", "mae", "edge_ratio", "macro_status"):
            assert risk[k] is None, k
            assert risk[k] != 0.0  # honest null, never a fabricated 0.0

    def test_fixture_absent_factor_fields_are_none(self, snapshot):
        f = symbol_detail(snapshot, "AAPL")["factors"]
        for k in ("xsec_12_1m", "xsec_momentum_rank"):
            assert f[k] is None, k
            assert f[k] != 0.0

    def test_empty_score_components_is_none(self):
        snap = {"timestamp": "t", "signals": [
            {"symbol": "ZZ", "sector": "X", "price": 10.0, "score_components": {}}]}
        assert symbol_detail(snap, "ZZ")["factors"]["score_components"] is None

    def test_blank_string_fields_are_none(self):
        snap = {"timestamp": "t", "signals": [
            {"symbol": "ZZ", "price": 10.0, "buy_range": "   ", "advisory_rationale": ""}]}
        d = symbol_detail(snap, "ZZ")
        assert d["ranges"]["buy_range"] is None
        assert d["advisory"]["rationale"] is None

    def test_nonpositive_price_is_none(self):
        for bad in (0.0, -5.0):
            snap = {"timestamp": "t", "signals": [{"symbol": "ZZ", "price": bad}]}
            assert symbol_detail(snap, "ZZ")["identity"]["price"] is None

    def test_positive_price_kept(self):
        snap = {"timestamp": "t", "signals": [{"symbol": "ZZ", "price": 12.5}]}
        assert symbol_detail(snap, "ZZ")["identity"]["price"] == pytest.approx(12.5)

    def test_shares_zero_kept(self):
        # shares == 0.0 is a genuine "hold none", not a placeholder → kept (unlike price).
        snap = {"timestamp": "t", "signals": [{"symbol": "ZZ", "price": 10.0, "shares": 0.0}]}
        assert symbol_detail(snap, "ZZ")["identity"]["shares"] == 0.0

    def test_missing_symbol_returns_none(self, snapshot):
        assert symbol_detail(snapshot, "ZZZ") is None

    def test_malformed_never_raises(self):
        assert symbol_detail(None, "AAPL") is None
        assert symbol_detail({}, "AAPL") is None
        assert symbol_detail({"signals": "bad"}, "AAPL") is None


# ---------------------------------------------------------------------------
# held_by_pilots — the reverse cross-link
# ---------------------------------------------------------------------------

class TestHeldByPilots:
    """Membership = a symbol surviving a Pilot's blend into its top-N. With
    PILOTS_TOP_N (20) > 8 fixture symbols there is no truncation, so a symbol is
    held iff its blended score is strictly > 0. Values below are computed by the
    real code against the committed fixture (not hand-derived)."""

    @staticmethod
    def _ids(tkr, snap):
        return {e["pilot_id"] for e in held_by_pilots(tkr, snap)}

    def test_membership_aapl(self, snapshot):
        assert self._ids("AAPL", snapshot) == {
            "cross-sectional-momentum", "macd-trend", "trend-following",
            "balanced-blend", "multifactor", "dividend-income", "value-quality",
            "edge-garch",
            # Single-module Pilots for the newly-covered modules; the fixture
            # scores each of these positively for AAPL.
            "forecast-aligned", "news-catalyst", "regime-navigator",
            "relative-strength", "risk-adjusted",
        }
        # deep-value (graham_value −3.0) and dip-buyer (holds nothing) excluded.
        assert "deep-value" not in self._ids("AAPL", snapshot)
        assert "dip-buyer" not in self._ids("AAPL", snapshot)

    def test_membership_nvda_excludes_dividend_income(self, snapshot):
        # dividend_quality contribution is a clean 0.0 for NVDA → excluded.
        ids = self._ids("NVDA", snapshot)
        assert ids == {
            "cross-sectional-momentum", "macd-trend", "trend-following",
            "balanced-blend", "multifactor", "edge-garch",
            # Newly-covered single-module Pilots that score NVDA positively.
            "forecast-aligned", "news-catalyst", "regime-navigator",
            "relative-strength", "risk-adjusted",
        }
        assert "dividend-income" not in ids

    def test_membership_t_excludes_momentum_and_blend(self, snapshot):
        ids = self._ids("T", snapshot)
        assert ids == {
            "deep-value", "dividend-income", "value-quality", "multifactor",
            # macro_regime scores T (a Communication-sector defensive name)
            # positively in the fixture.
            "regime-navigator",
        }
        assert "balanced-blend" not in ids
        assert "trend-following" not in ids

    def test_entry_shape_and_name(self, snapshot):
        for e in held_by_pilots("AAPL", snapshot):
            assert set(e) == {"pilot_id", "name", "weight"}
        tf = next(e for e in held_by_pilots("AAPL", snapshot) if e["pilot_id"] == "trend-following")
        assert tf["name"] == "Trend Follower"

    def test_aapl_weights_strictly_positive_and_unit_bounded(self, snapshot):
        # AAPL is held with a positive weight by every one of its holders.
        for e in held_by_pilots("AAPL", snapshot):
            assert 0.0 < e["weight"] <= 1.0

    def test_aapl_trend_following_weight_value(self, snapshot):
        tf = next(e for e in held_by_pilots("AAPL", snapshot) if e["pilot_id"] == "trend-following")
        assert tf["weight"] == pytest.approx(0.25)

    def test_sorted_weight_descending(self, snapshot):
        w = [e["weight"] for e in held_by_pilots("AAPL", snapshot)]
        assert w == sorted(w, reverse=True)

    def test_no_pilot_symbol_returns_empty(self):
        # All modules negative → every Pilot's blend ≤ 0 → held by none.
        dead = {"timestamp": "t", "signals": [{
            "symbol": "DEAD", "sector": "X", "price": 10.0,
            "score_components": {
                "timeseries_momentum": -1.0, "cross_sectional_momentum": -1.0,
                "macd_momentum": -1.0, "aroon_trend": -1.0, "multifactor": -1.0,
                "dividend_quality": -1.0, "graham_value": -1.0,
                "rsi2_mean_reversion": -1.0,
            },
        }]}
        assert held_by_pilots("DEAD", dead) == []

    def test_absent_ticker_returns_empty(self, snapshot):
        assert held_by_pilots("NOTHERE", snapshot) == []

    def test_custom_pilots_arg(self, snapshot):
        only = [get_pilot("trend-following")]
        assert self._ids("AAPL", snapshot) != {"trend-following"}  # default = full catalog
        assert {e["pilot_id"] for e in held_by_pilots("AAPL", snapshot, pilots=only)} == {"trend-following"}

    def test_malformed_never_raises(self):
        assert held_by_pilots(None, None) == []
        assert held_by_pilots("AAPL", None) == []
        assert held_by_pilots("AAPL", {"signals": "bad"}) == []


# ---------------------------------------------------------------------------
# list_universe — the symbol-autocomplete source (GET /universe)
# ---------------------------------------------------------------------------

class TestListUniverse:
    def test_all_eight_fixture_symbols_present_sorted(self, snapshot):
        rows = list_universe(snapshot)
        symbols = [r["symbol"] for r in rows]
        assert symbols == sorted(symbols)
        assert set(symbols) == {"AAPL", "MSFT", "NVDA", "JPM", "XOM", "JNJ", "PG", "T"}

    def test_row_shape(self, snapshot):
        rows = list_universe(snapshot)
        for r in rows:
            assert set(r) == {"symbol", "action"}

    def test_action_prefers_advisory_action_over_raw_action(self):
        # advisory_action and action disagree → advisory_action (holding-aware
        # overlay) wins, matching symbol_detail's precedence.
        snap = {"signals": [{"symbol": "AAPL", "advisory_action": "HOLD", "action": "BUY"}]}
        assert list_universe(snap) == [{"symbol": "AAPL", "action": "HOLD"}]

    def test_action_falls_back_to_raw_action(self):
        snap = {"signals": [{"symbol": "AAPL", "action": "BUY"}]}
        assert list_universe(snap) == [{"symbol": "AAPL", "action": "BUY"}]

    def test_action_null_when_neither_present(self):
        # Honest null (CONSTRAINT #4) — never a fabricated default like "HOLD".
        snap = {"signals": [{"symbol": "AAPL"}]}
        assert list_universe(snap) == [{"symbol": "AAPL", "action": None}]

    def test_dedupes_symbol_first_entry_wins(self):
        snap = {"signals": [
            {"symbol": "AAPL", "action": "BUY"},
            {"symbol": "aapl", "action": "SELL"},  # case-insensitive dup, later entry
        ]}
        assert list_universe(snap) == [{"symbol": "AAPL", "action": "BUY"}]

    def test_uppercases_and_strips(self):
        snap = {"signals": [{"symbol": "  nvda  ", "action": "BUY"}]}
        assert list_universe(snap) == [{"symbol": "NVDA", "action": "BUY"}]

    def test_skips_blank_and_malformed_signal_entries(self):
        snap = {"signals": [
            {"symbol": "", "action": "BUY"},
            {"symbol": "   ", "action": "BUY"},
            {"not_a": "symbol_field"},
            "garbage",
            123,
            {"symbol": "MSFT", "action": "HOLD"},
        ]}
        assert list_universe(snap) == [{"symbol": "MSFT", "action": "HOLD"}]

    def test_cold_start_empty(self):
        assert list_universe(None) == []

    def test_no_signals_key_empty(self):
        assert list_universe({}) == []

    def test_malformed_never_raises(self):
        assert list_universe("not a dict") == []
        assert list_universe({"signals": "nope"}) == []
        assert list_universe({"signals": None}) == []
        assert list_universe(123) == []


# ---------------------------------------------------------------------------
# list_recommendations — the ranked BUY-picks feed (GET /recommendations)
# ---------------------------------------------------------------------------

class TestListRecommendations:
    def test_only_buys_from_fixture_ranked_by_conviction(self, snapshot):
        # Fixture BUYs: NVDA(0.88) AAPL(0.72) JPM(0.64) XOM(0.58); HOLDs/SELL dropped.
        rows = list_recommendations(snapshot)
        assert [r["symbol"] for r in rows] == ["NVDA", "AAPL", "JPM", "XOM"]

    def test_row_shape(self, snapshot):
        rows = list_recommendations(snapshot)
        assert rows, "fixture has BUYs"
        for r in rows:
            assert set(r) == {"symbol", "action", "conviction", "score", "buy_range", "sector", "price"}

    def test_values_are_carried_through(self, snapshot):
        nvda = list_recommendations(snapshot)[0]
        assert nvda == {
            "symbol": "NVDA",
            "action": "BUY",
            "conviction": 0.88,
            "score": 118.4,
            "buy_range": "Buy Zone: $118.00 - $126.00",
            "sector": "Information Technology",
            "price": 128.72,
        }

    def test_strong_buy_is_included(self):
        snap = {"signals": [{"symbol": "ZZ", "advisory_action": "STRONG BUY", "advisory_conviction": 0.9}]}
        assert [r["symbol"] for r in list_recommendations(snap)] == ["ZZ"]

    def test_hold_and_sell_excluded(self):
        snap = {"signals": [
            {"symbol": "H", "advisory_action": "HOLD"},
            {"symbol": "S", "advisory_action": "SELL"},
            {"symbol": "B", "advisory_action": "BUY"},
        ]}
        assert [r["symbol"] for r in list_recommendations(snap)] == ["B"]

    def test_action_prefers_advisory_over_raw(self):
        # advisory_action HOLD overrides a raw BUY → not a recommendation.
        snap = {"signals": [{"symbol": "AAPL", "advisory_action": "HOLD", "action": "BUY"}]}
        assert list_recommendations(snap) == []

    def test_action_falls_back_to_raw_action(self):
        snap = {"signals": [{"symbol": "AAPL", "action": "BUY"}]}
        assert [r["symbol"] for r in list_recommendations(snap)] == ["AAPL"]

    def test_missing_numeric_fields_are_null_not_zero(self):
        # Honesty (CONSTRAINT #4): absent conviction/score/price → None, never 0.0.
        snap = {"signals": [{"symbol": "ZZ", "action": "BUY"}]}
        row = list_recommendations(snap)[0]
        assert row["conviction"] is None
        assert row["score"] is None
        assert row["price"] is None
        assert row["buy_range"] is None
        assert row["sector"] is None

    def test_nonpositive_price_nulled(self):
        snap = {"signals": [{"symbol": "ZZ", "action": "BUY", "price": 0.0}]}
        assert list_recommendations(snap)[0]["price"] is None

    def test_null_conviction_sorts_after_real_conviction(self):
        snap = {"signals": [
            {"symbol": "NOCONV", "action": "BUY"},
            {"symbol": "REAL", "action": "BUY", "advisory_conviction": 0.10},
        ]}
        assert [r["symbol"] for r in list_recommendations(snap)] == ["REAL", "NOCONV"]

    def test_score_tiebreaks_equal_conviction(self):
        snap = {"signals": [
            {"symbol": "LO", "action": "BUY", "advisory_conviction": 0.5, "score": 10},
            {"symbol": "HI", "action": "BUY", "advisory_conviction": 0.5, "score": 90},
        ]}
        assert [r["symbol"] for r in list_recommendations(snap)] == ["HI", "LO"]

    def test_symbol_tiebreaks_equal_conviction_and_score(self):
        snap = {"signals": [
            {"symbol": "BBB", "action": "BUY", "advisory_conviction": 0.5, "score": 10},
            {"symbol": "AAA", "action": "BUY", "advisory_conviction": 0.5, "score": 10},
        ]}
        assert [r["symbol"] for r in list_recommendations(snap)] == ["AAA", "BBB"]

    def test_limit_clamped_and_applied(self, snapshot):
        assert [r["symbol"] for r in list_recommendations(snapshot, limit=2)] == ["NVDA", "AAPL"]
        # Clamp: <1 → 1, non-int → default; never raises, never empties everything.
        assert len(list_recommendations(snapshot, limit=0)) == 1
        assert len(list_recommendations(snapshot, limit="bad")) == 4  # type: ignore[arg-type]

    def test_uppercases_and_strips_symbol(self):
        snap = {"signals": [{"symbol": "  nvda  ", "action": "BUY"}]}
        assert list_recommendations(snap)[0]["symbol"] == "NVDA"

    def test_cold_start_and_malformed_never_raise(self):
        assert list_recommendations(None) == []
        assert list_recommendations({}) == []
        assert list_recommendations("not a dict") == []
        assert list_recommendations({"signals": "nope"}) == []
        assert list_recommendations({"signals": None}) == []
        assert list_recommendations({"signals": [123, "x", {"no_symbol": 1}]}) == []
