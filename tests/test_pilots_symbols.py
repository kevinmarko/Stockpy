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
from pilots.symbols import find_signal, held_by_pilots, symbol_detail

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
        }
        assert "dividend-income" not in ids

    def test_membership_t_excludes_momentum_and_blend(self, snapshot):
        ids = self._ids("T", snapshot)
        assert ids == {"deep-value", "dividend-income", "value-quality", "multifactor"}
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
