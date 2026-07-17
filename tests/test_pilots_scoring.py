"""Unit tests for ``pilots/scoring.py`` — the pure, snapshot-driven Pilot scorer.

All fixtures are offline: the committed ``tests/fixtures/state_snapshot.json`` for
the holdings/sector cases, and tiny synthesized rotated snapshots in a ``tmp_path``
history dir for the ``pilot_trades`` diff cases. No network, no heavy engines.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pilots.catalog import Pilot, get_pilot
from pilots.scoring import (
    load_snapshot,
    pilot_holdings,
    pilot_trades,
    sector_allocation,
)
from settings import settings

FIXTURE = Path(__file__).parent / "fixtures" / "state_snapshot.json"


@pytest.fixture()
def snapshot() -> dict:
    snap = load_snapshot(str(FIXTURE))
    assert snap is not None, "committed fixture snapshot must load"
    return snap


# ---------------------------------------------------------------------------
# load_snapshot
# ---------------------------------------------------------------------------

class TestLoadSnapshot:
    def test_loads_committed_fixture(self, snapshot):
        assert isinstance(snapshot, dict)
        assert len(snapshot["signals"]) == 8

    def test_missing_file_returns_none(self):
        assert load_snapshot("/nonexistent/definitely/not/here.json") is None

    def test_default_path_missing_returns_none(self, tmp_path, monkeypatch):
        # Run from a dir with no output/state_snapshot.json -> None, never raises.
        monkeypatch.chdir(tmp_path)
        assert load_snapshot(None) is None

    def test_corrupt_json_returns_none(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{not valid json", encoding="utf-8")
        assert load_snapshot(str(bad)) is None

    def test_non_object_json_returns_none(self, tmp_path):
        arr = tmp_path / "arr.json"
        arr.write_text("[1, 2, 3]", encoding="utf-8")
        assert load_snapshot(str(arr)) is None


# ---------------------------------------------------------------------------
# Raw-score back-out correctness
# ---------------------------------------------------------------------------

class TestRawScoreBackout:
    def test_single_module_backout(self, snapshot):
        """trend-following = timeseries_momentum @ weight 1.0.

        AAPL's ts-momentum contribution is 9.0 and SIGNAL_WEIGHTS is 15.0, so its
        blended (== raw) score must be exactly 9.0 / 15.0 = 0.6.
        """
        pilot = get_pilot("trend-following")
        w = settings.SIGNAL_WEIGHTS["timeseries_momentum"]
        holdings = pilot_holdings(pilot, snapshot)
        by_sym = {h["symbol"]: h for h in holdings}
        assert by_sym["AAPL"]["score"] == pytest.approx(9.0 / w)
        assert by_sym["NVDA"]["score"] == pytest.approx(13.5 / w)

    def test_negative_raw_scores_excluded(self, snapshot):
        """JNJ/PG/T have negative timeseries_momentum → not held (score <= 0)."""
        pilot = get_pilot("trend-following")
        held = {h["symbol"] for h in pilot_holdings(pilot, snapshot)}
        assert held == {"NVDA", "AAPL", "MSFT", "JPM", "XOM"}
        assert not ({"JNJ", "PG", "T"} & held)

    def test_multi_module_blend(self, snapshot):
        """macd-trend = macd_momentum + aroon_trend, each @ weight 1.0."""
        pilot = get_pilot("macd-trend")
        w_macd = settings.SIGNAL_WEIGHTS["macd_momentum"]
        w_aroon = settings.SIGNAL_WEIGHTS["aroon_trend"]
        holdings = pilot_holdings(pilot, snapshot)
        aapl = next(h for h in holdings if h["symbol"] == "AAPL")
        expected = (9.0 / w_macd) * 1.0 + (7.5 / w_aroon) * 1.0
        assert aapl["score"] == pytest.approx(expected)

    def test_pilot_weight_scales_blend(self, snapshot):
        """A custom pilot weight is a linear multiplier on the raw score."""
        base = Pilot(id="b", name="b", category="x", description="",
                     weights={"timeseries_momentum": 1.0})
        doubled = Pilot(id="d", name="d", category="x", description="",
                        weights={"timeseries_momentum": 2.0})
        b = {h["symbol"]: h["score"] for h in pilot_holdings(base, snapshot)}
        d = {h["symbol"]: h["score"] for h in pilot_holdings(doubled, snapshot)}
        for sym in b:
            assert d[sym] == pytest.approx(2.0 * b[sym])

    def test_missing_component_contributes_zero(self, snapshot):
        """A module absent from score_components adds exactly 0 (never fabricated).

        rsi2_mean_reversion IS a real SIGNAL_WEIGHTS key but is absent from every
        symbol's score_components in the fixture, so adding it to a trend pilot's
        weights must not change any blended score.
        """
        plain = Pilot(id="p", name="p", category="x", description="",
                      weights={"timeseries_momentum": 1.0})
        with_missing = Pilot(id="pm", name="pm", category="x", description="",
                             weights={"timeseries_momentum": 1.0,
                                      "rsi2_mean_reversion": 1.0})
        a = {h["symbol"]: h["score"] for h in pilot_holdings(plain, snapshot)}
        b = {h["symbol"]: h["score"] for h in pilot_holdings(with_missing, snapshot)}
        assert a == pytest.approx(b)

    def test_dip_buyer_all_missing_component_is_empty(self, snapshot):
        """dip-buyer weights only rsi2_mean_reversion, absent everywhere → []."""
        assert pilot_holdings(get_pilot("dip-buyer"), snapshot) == []

    def test_regime_multiplier_weight_zero_skipped(self):
        """A weight-0 module must be skipped (no divide-by-zero, contributes 0).

        Build a snapshot where regime_multiplier carries a NON-zero contribution;
        because SIGNAL_WEIGHTS['regime_multiplier'] == 0.0 it is un-backoutable and
        must be ignored — the blend equals the timeseries_momentum term alone.
        """
        assert settings.SIGNAL_WEIGHTS["regime_multiplier"] == 0.0
        snap = {
            "signals": [{
                "symbol": "ZZZ",
                "sector": "Test",
                "price": 10.0,
                "score_components": {
                    "timeseries_momentum": 7.5,   # raw 0.5
                    "regime_multiplier": 5.0,      # weight 0 -> skipped
                },
            }],
        }
        pilot = Pilot(id="rm", name="rm", category="x", description="",
                      weights={"timeseries_momentum": 1.0, "regime_multiplier": 1.0})
        holdings = pilot_holdings(pilot, snap)
        assert len(holdings) == 1
        w = settings.SIGNAL_WEIGHTS["timeseries_momentum"]
        assert holdings[0]["score"] == pytest.approx(7.5 / w)  # regime term dropped

    def test_regime_multiplier_only_pilot_is_empty(self, snapshot):
        """A pilot weighting only the weight-0 module holds nothing."""
        pilot = Pilot(id="rmo", name="rmo", category="x", description="",
                      weights={"regime_multiplier": 1.0})
        assert pilot_holdings(pilot, snapshot) == []


# ---------------------------------------------------------------------------
# Regime-conditional weight back-out (regression: score_components is
# persisted under strategy_engine's REGIME-RESOLVED weight, not the flat
# settings.SIGNAL_WEIGHTS dict; the back-out divisor must match).
# ---------------------------------------------------------------------------

class TestRegimeConditionalWeights:
    """Coverage for the dormant regime-weight back-out bug.

    ``strategy_engine.evaluate_security()`` builds
    ``score_components[module] = output.score * effective_weight`` where
    ``effective_weight`` comes from ``signals.aggregator.resolve_regime_weights(
    market_regime, REGIME_SIGNAL_WEIGHTS, SIGNAL_WEIGHTS)`` — NOT always the
    flat ``SIGNAL_WEIGHTS`` dict. Dividing by the flat weight regardless (the
    pre-fix behavior) is only correct while ``REGIME_SIGNAL_WEIGHTS == {}``
    (the project default). These tests set a non-empty override and prove the
    back-out — and therefore every Pilot's holdings — stays correct.
    """

    def test_effective_weights_matches_aggregator_parity(self, monkeypatch):
        """``_effective_signal_weights`` is a reimplementation of
        ``signals.aggregator.resolve_regime_weights`` (kept import-light —
        see the module docstring), not a wrapper around it, so drift between
        the two is a real risk. Pin byte-identical output across a range of
        regime/override configs so any future change to the canonical
        merge semantics is caught here too.
        """
        from signals.aggregator import resolve_regime_weights
        from pilots.scoring import _effective_signal_weights

        flat = dict(settings.SIGNAL_WEIGHTS)
        configs = [
            {},  # default: no overrides configured
            {"RECESSION": {"rsi2_mean_reversion": 0.0, "macro_regime": 60.0}},
            {"_default": {"timeseries_momentum": 99.0}},
            {"RISK ON": {"timeseries_momentum": 25.0},
             "_default": {"timeseries_momentum": 1.0}},
        ]
        regimes = ["RISK ON", "RECESSION", "NEUTRAL", "CREDIT EVENT", "", "BOGUS"]

        for cfg in configs:
            monkeypatch.setattr(settings, "REGIME_SIGNAL_WEIGHTS", cfg)
            for regime in regimes:
                expected = resolve_regime_weights(regime, cfg, flat)
                actual = _effective_signal_weights(regime)
                assert actual == expected, (cfg, regime)

    def test_regime_override_for_active_regime_changes_backout_divisor(
        self, snapshot, monkeypatch
    ):
        """A REGIME_SIGNAL_WEIGHTS override active for the snapshot's own
        ``market_regime`` ("RISK ON" in the fixture) must be used as the
        back-out divisor instead of the flat SIGNAL_WEIGHTS value.

        Simulates what strategy_engine.evaluate_security() actually persists:
        if ``timeseries_momentum``'s effective weight this cycle was 3x the
        flat weight, every symbol's persisted weighted contribution is 3x
        larger for the SAME underlying raw [-1, 1] score. Backing that out
        with the correct (regime-resolved) 3x divisor must recover the exact
        same raw scores as the flat-weight baseline — proving the fix, not
        just that *some* number changed.
        """
        pilot = get_pilot("trend-following")  # weights={"timeseries_momentum": 1.0}
        flat_w = settings.SIGNAL_WEIGHTS["timeseries_momentum"]
        baseline = {h["symbol"]: h["score"] for h in pilot_holdings(pilot, snapshot)}

        rescaled = json.loads(json.dumps(snapshot))  # deep copy
        for sig in rescaled["signals"]:
            comp = sig.get("score_components") or {}
            if "timeseries_momentum" in comp:
                comp["timeseries_momentum"] *= 3.0

        monkeypatch.setattr(
            settings, "REGIME_SIGNAL_WEIGHTS",
            {"RISK ON": {"timeseries_momentum": flat_w * 3.0}},
        )
        rescaled_scores = {h["symbol"]: h["score"] for h in pilot_holdings(pilot, rescaled)}
        assert rescaled_scores == pytest.approx(baseline)

    def test_no_matching_override_leaves_holdings_unaffected(self, snapshot, monkeypatch):
        """A REGIME_SIGNAL_WEIGHTS override configured for a regime OTHER
        than the snapshot's own ``market_regime`` ("RISK ON"), with no
        ``"_default"`` catch-all, must leave ``pilot_holdings`` byte-identical
        to the flat-weight baseline (``resolve_regime_weights`` falls back to
        the flat dict when nothing matches).
        """
        pilot = get_pilot("trend-following")
        baseline = pilot_holdings(pilot, snapshot)

        monkeypatch.setattr(
            settings, "REGIME_SIGNAL_WEIGHTS",
            {"RECESSION": {"timeseries_momentum": 999.0}},
        )
        overridden = pilot_holdings(pilot, snapshot)
        assert overridden == baseline


# ---------------------------------------------------------------------------
# top-N truncation + normalization
# ---------------------------------------------------------------------------

class TestNormalizationAndTopN:
    def test_weights_sum_to_one(self, snapshot):
        holdings = pilot_holdings(get_pilot("trend-following"), snapshot)
        assert sum(h["weight"] for h in holdings) == pytest.approx(1.0)

    def test_sorted_descending_by_score(self, snapshot):
        holdings = pilot_holdings(get_pilot("trend-following"), snapshot)
        scores = [h["score"] for h in holdings]
        assert scores == sorted(scores, reverse=True)
        assert holdings[0]["symbol"] == "NVDA"  # highest ts-momentum

    def test_top_n_truncates_and_renormalizes(self, snapshot):
        holdings = pilot_holdings(get_pilot("trend-following"), snapshot, top_n=2)
        assert len(holdings) == 2
        assert [h["symbol"] for h in holdings] == ["NVDA", "AAPL"]
        # Re-normalized over the surviving 2 names, still sums to 1.0.
        assert sum(h["weight"] for h in holdings) == pytest.approx(1.0)

    def test_default_top_n_from_settings(self, snapshot, monkeypatch):
        monkeypatch.setattr(settings, "PILOTS_TOP_N", 1)
        holdings = pilot_holdings(get_pilot("trend-following"), snapshot)
        assert len(holdings) == 1
        assert holdings[0]["symbol"] == "NVDA"
        assert holdings[0]["weight"] == pytest.approx(1.0)

    def test_holding_dict_shape(self, snapshot):
        holdings = pilot_holdings(get_pilot("trend-following"), snapshot)
        h = holdings[0]
        assert set(h.keys()) == {"symbol", "weight", "score", "price", "sector"}
        assert h["price"] == pytest.approx(128.72)   # NVDA price from fixture
        assert h["sector"] == "Information Technology"


# ---------------------------------------------------------------------------
# Empty / malformed snapshots
# ---------------------------------------------------------------------------

class TestEmptyAndMalformed:
    def test_no_signals_key_is_empty(self):
        assert pilot_holdings(get_pilot("trend-following"), {}) == []

    def test_empty_signals_list_is_empty(self):
        assert pilot_holdings(get_pilot("trend-following"), {"signals": []}) == []

    def test_non_dict_snapshot_is_empty(self):
        assert pilot_holdings(get_pilot("trend-following"), None) == []  # type: ignore[arg-type]

    def test_signals_entries_without_symbol_skipped(self):
        snap = {"signals": [{"score_components": {"timeseries_momentum": 9.0}}]}
        assert pilot_holdings(get_pilot("trend-following"), snap) == []


# ---------------------------------------------------------------------------
# sector_allocation
# ---------------------------------------------------------------------------

class TestSectorAllocation:
    def test_group_by_and_sum(self, snapshot):
        holdings = pilot_holdings(get_pilot("trend-following"), snapshot)
        alloc = sector_allocation(holdings)
        by_sector = {a["sector"]: a["weight"] for a in alloc}
        # NVDA + AAPL + MSFT are all Information Technology.
        it_expected = sum(h["weight"] for h in holdings
                          if h["sector"] == "Information Technology")
        assert by_sector["Information Technology"] == pytest.approx(it_expected)
        assert set(by_sector) == {"Information Technology", "Financials", "Energy"}
        assert sum(by_sector.values()) == pytest.approx(1.0)

    def test_sorted_descending(self, snapshot):
        holdings = pilot_holdings(get_pilot("trend-following"), snapshot)
        alloc = sector_allocation(holdings)
        weights = [a["weight"] for a in alloc]
        assert weights == sorted(weights, reverse=True)
        assert alloc[0]["sector"] == "Information Technology"

    def test_missing_sector_bucketed_unknown(self):
        holdings = [
            {"symbol": "A", "weight": 0.5, "sector": ""},
            {"symbol": "B", "weight": 0.3, "sector": None},
            {"symbol": "C", "weight": 0.2, "sector": "Energy"},
        ]
        alloc = sector_allocation(holdings)
        by_sector = {a["sector"]: a["weight"] for a in alloc}
        assert by_sector["Unknown"] == pytest.approx(0.8)
        assert by_sector["Energy"] == pytest.approx(0.2)

    def test_empty_holdings(self):
        assert sector_allocation([]) == []


# ---------------------------------------------------------------------------
# pilot_trades — day-over-day holdings diff across rotated history
# ---------------------------------------------------------------------------

def _ts_signals(entries):
    """Build a snapshot ``signals`` list from ``[(symbol, sector, ts_mom_contrib)]``."""
    return [
        {
            "symbol": sym,
            "sector": sector,
            "price": 100.0,
            "score_components": {"timeseries_momentum": contrib},
        }
        for sym, sector, contrib in entries
    ]


def _write_history_snapshot(history_dir: Path, when: datetime, signals: list) -> Path:
    history_dir.mkdir(parents=True, exist_ok=True)
    fname = f"state_snapshot_{when.strftime('%Y%m%dT%H%M%SZ')}.json"
    path = history_dir / fname
    payload = {"timestamp": when.strftime("%Y-%m-%dT%H:%M:%SZ"), "signals": signals}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestPilotTrades:
    def _pilot(self):
        return Pilot(id="tf", name="tf", category="Momentum", description="",
                     weights={"timeseries_momentum": 1.0})

    def test_missing_history_dir_returns_empty(self, tmp_path):
        assert pilot_trades(self._pilot(), history_dir=str(tmp_path / "nope")) == []

    def test_single_snapshot_returns_empty(self, tmp_path):
        hist = tmp_path / "history"
        now = datetime.now(timezone.utc)
        _write_history_snapshot(hist, now, _ts_signals([("AAPL", "Tech", 9.0)]))
        assert pilot_trades(self._pilot(), history_dir=str(hist)) == []

    def test_enter_and_exit_detected(self, tmp_path):
        hist = tmp_path / "history"
        now = datetime.now(timezone.utc)
        # Day 1: AAPL + MSFT held.
        _write_history_snapshot(
            hist, now - timedelta(days=2),
            _ts_signals([("AAPL", "Tech", 9.0), ("MSFT", "Tech", 6.0)]),
        )
        # Day 2: MSFT drops out, NVDA enters (AAPL stays but reweights).
        _write_history_snapshot(
            hist, now - timedelta(days=1),
            _ts_signals([("AAPL", "Tech", 9.0), ("NVDA", "Tech", 13.5)]),
        )
        events = pilot_trades(self._pilot(), history_dir=str(hist))
        sides = {(e["symbol"], e["side"]) for e in events}
        assert ("NVDA", "ENTER") in sides
        assert ("MSFT", "EXIT") in sides
        # AAPL's normalized weight changed (0.6 -> 0.4) so it reweights.
        assert ("AAPL", "REWEIGHT") in sides
        # ENTER delta is a positive weight, EXIT delta is negative.
        enter = next(e for e in events if e["symbol"] == "NVDA")
        exit_ = next(e for e in events if e["symbol"] == "MSFT")
        assert enter["weight_delta"] > 0
        assert exit_["weight_delta"] < 0

    def test_no_change_no_events(self, tmp_path):
        hist = tmp_path / "history"
        now = datetime.now(timezone.utc)
        sigs = _ts_signals([("AAPL", "Tech", 9.0), ("MSFT", "Tech", 6.0)])
        _write_history_snapshot(hist, now - timedelta(days=2), sigs)
        _write_history_snapshot(hist, now - timedelta(days=1), sigs)
        assert pilot_trades(self._pilot(), history_dir=str(hist)) == []

    def test_lookback_window_excludes_old_snapshots(self, tmp_path):
        hist = tmp_path / "history"
        now = datetime.now(timezone.utc)
        # An old pair (well outside a 5-day window) that WOULD produce events...
        _write_history_snapshot(hist, now - timedelta(days=40),
                                _ts_signals([("AAPL", "Tech", 9.0)]))
        _write_history_snapshot(hist, now - timedelta(days=39),
                                _ts_signals([("NVDA", "Tech", 9.0)]))
        # ...and one recent snapshot. With lookback=5 only the recent one survives,
        # leaving < 2 snapshots in-window -> no events.
        _write_history_snapshot(hist, now,
                                _ts_signals([("NVDA", "Tech", 9.0)]))
        assert pilot_trades(self._pilot(), lookback_days=5, history_dir=str(hist)) == []
        # With a wide window all three are in-window and events appear.
        assert pilot_trades(self._pilot(), lookback_days=365, history_dir=str(hist))

    def test_event_date_is_later_snapshot(self, tmp_path):
        hist = tmp_path / "history"
        now = datetime.now(timezone.utc)
        _write_history_snapshot(hist, now - timedelta(days=1),
                                _ts_signals([("AAPL", "Tech", 9.0)]))
        curr = now
        _write_history_snapshot(hist, curr,
                                _ts_signals([("NVDA", "Tech", 9.0)]))
        events = pilot_trades(self._pilot(), history_dir=str(hist))
        assert events
        expected_date = curr.strftime("%Y-%m-%dT%H:%M:%SZ")
        assert all(e["date"] == expected_date for e in events)

    def test_non_matching_files_ignored(self, tmp_path):
        hist = tmp_path / "history"
        hist.mkdir(parents=True, exist_ok=True)
        (hist / "not_a_snapshot.json").write_text("{}", encoding="utf-8")
        (hist / "state_snapshot.json").write_text("{}", encoding="utf-8")  # no ts
        now = datetime.now(timezone.utc)
        _write_history_snapshot(hist, now - timedelta(days=1),
                                _ts_signals([("AAPL", "Tech", 9.0)]))
        _write_history_snapshot(hist, now,
                                _ts_signals([("NVDA", "Tech", 9.0)]))
        # Only the two well-formed rotated files are diffed → events present, no raise.
        events = pilot_trades(self._pilot(), history_dir=str(hist))
        sides = {(e["symbol"], e["side"]) for e in events}
        assert ("NVDA", "ENTER") in sides
        assert ("AAPL", "EXIT") in sides
