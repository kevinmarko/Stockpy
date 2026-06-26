"""
tests/test_watch_alerts.py — Symbol Watch Alert Engine tests
=============================================================
Covers: WatchRule, WatchAlert, SymbolWatchState, load_watch_rules,
load/save_watch_state, evaluate_watch_rules (action_change, conviction_above,
conviction_below, wildcard, edge-trigger, no-lookahead), dispatch_watch_alerts.

All tests are fully offline — no network calls, no filesystem side-effects
outside tmp_path.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from watch_engine import (
    SymbolWatchState,
    WatchAlert,
    WatchRule,
    dispatch_watch_alerts,
    evaluate_watch_rules,
    load_watch_rules,
    load_watch_state,
    save_watch_state,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_rec(
    symbol: str,
    action: str,
    conviction: float,
    position_pct: float = 0.04,
    rationale: str = "Test rationale for the signal.",
) -> Any:
    """Create a duck-typed Recommendation-like mock object."""
    rec = mock.MagicMock()
    rec.symbol = symbol
    rec.action = action
    rec.conviction = conviction
    rec.suggested_position_pct = position_pct
    rec.rationale = rationale
    return rec


# ---------------------------------------------------------------------------
# TestWatchRule
# ---------------------------------------------------------------------------


class TestWatchRule:
    def test_frozen(self) -> None:
        r = WatchRule(symbol="AAPL", alert_on="action_change")
        with pytest.raises((AttributeError, TypeError)):
            r.symbol = "MSFT"  # type: ignore[misc]

    def test_defaults(self) -> None:
        r = WatchRule(symbol="*", alert_on="action_change")
        assert r.threshold is None
        assert r.priority == "default"
        assert r.label == ""

    def test_all_fields(self) -> None:
        r = WatchRule(
            symbol="AAPL",
            alert_on="conviction_above",
            threshold=0.85,
            priority="high",
            label="Siren",
        )
        assert r.symbol == "AAPL"
        assert r.alert_on == "conviction_above"
        assert r.threshold == pytest.approx(0.85)
        assert r.priority == "high"
        assert r.label == "Siren"


# ---------------------------------------------------------------------------
# TestWatchAlert
# ---------------------------------------------------------------------------


class TestWatchAlert:
    def test_frozen(self) -> None:
        a = WatchAlert(
            symbol="AAPL",
            rule_type="action_change",
            priority="default",
            title="T",
            message="M",
            trigger_detail="d",
        )
        with pytest.raises((AttributeError, TypeError)):
            a.symbol = "X"  # type: ignore[misc]

    def test_all_fields_set(self) -> None:
        a = WatchAlert(
            symbol="TSLA",
            rule_type="conviction_above",
            priority="high",
            title="Title",
            message="Body",
            trigger_detail="detail",
        )
        assert a.symbol == "TSLA"
        assert a.rule_type == "conviction_above"
        assert a.priority == "high"


# ---------------------------------------------------------------------------
# TestSymbolWatchState
# ---------------------------------------------------------------------------


class TestSymbolWatchState:
    def test_round_trip_serialisation(self) -> None:
        s = SymbolWatchState(
            action="BUY",
            conviction=0.75,
            alerted_conviction_above={"0.85": False},
            alerted_conviction_below={"0.5": True},
            timestamp="2026-06-26T10:00:00+00:00",
        )
        d = s.to_dict()
        s2 = SymbolWatchState.from_dict(d)
        assert s2.action == "BUY"
        assert s2.conviction == pytest.approx(0.75)
        assert s2.alerted_conviction_above == {"0.85": False}
        assert s2.alerted_conviction_below == {"0.5": True}
        assert s2.timestamp == "2026-06-26T10:00:00+00:00"

    def test_from_dict_empty_gives_defaults(self) -> None:
        s = SymbolWatchState.from_dict({})
        assert s.action == ""
        assert s.conviction == pytest.approx(0.0)
        assert s.alerted_conviction_above == {}
        assert s.alerted_conviction_below == {}


# ---------------------------------------------------------------------------
# TestLoadWatchRules
# ---------------------------------------------------------------------------


class TestLoadWatchRules:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        rules = load_watch_rules(tmp_path / "nonexistent.yaml")
        assert rules == []

    def test_malformed_yaml_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.yaml"
        p.write_text("{bad yaml: [\n", encoding="utf-8")
        rules = load_watch_rules(p)
        assert rules == []

    def test_non_mapping_root_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.yaml"
        p.write_text("- just a list\n", encoding="utf-8")
        rules = load_watch_rules(p)
        assert rules == []

    def test_empty_rules_list_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "r.yaml"
        p.write_text("rules: []\n", encoding="utf-8")
        assert load_watch_rules(p) == []

    def test_action_change_rule_parsed(self, tmp_path: Path) -> None:
        p = tmp_path / "r.yaml"
        p.write_text(
            textwrap.dedent(
                """\
                rules:
                  - symbol: AAPL
                    alert_on: action_change
                    priority: default
                    label: "AAPL Flip"
                """
            ),
            encoding="utf-8",
        )
        rules = load_watch_rules(p)
        assert len(rules) == 1
        r = rules[0]
        assert r.symbol == "AAPL"
        assert r.alert_on == "action_change"
        assert r.threshold is None
        assert r.priority == "default"
        assert r.label == "AAPL Flip"

    def test_conviction_above_rule_parsed(self, tmp_path: Path) -> None:
        p = tmp_path / "r.yaml"
        p.write_text(
            textwrap.dedent(
                """\
                rules:
                  - symbol: "*"
                    alert_on: conviction_above
                    threshold: 0.85
                    priority: high
                    label: Siren
                """
            ),
            encoding="utf-8",
        )
        rules = load_watch_rules(p)
        assert len(rules) == 1
        r = rules[0]
        assert r.symbol == "*"
        assert r.threshold == pytest.approx(0.85)
        assert r.priority == "high"

    def test_conviction_above_missing_threshold_is_skipped(self, tmp_path: Path) -> None:
        p = tmp_path / "r.yaml"
        p.write_text(
            "rules:\n  - symbol: AAPL\n    alert_on: conviction_above\n",
            encoding="utf-8",
        )
        rules = load_watch_rules(p)
        assert rules == []

    def test_unknown_alert_on_is_skipped(self, tmp_path: Path) -> None:
        p = tmp_path / "r.yaml"
        p.write_text(
            "rules:\n  - symbol: AAPL\n    alert_on: price_above\n    threshold: 150\n",
            encoding="utf-8",
        )
        rules = load_watch_rules(p)
        assert rules == []

    def test_threshold_out_of_range_is_skipped(self, tmp_path: Path) -> None:
        p = tmp_path / "r.yaml"
        p.write_text(
            "rules:\n  - symbol: AAPL\n    alert_on: conviction_above\n    threshold: 1.5\n",
            encoding="utf-8",
        )
        rules = load_watch_rules(p)
        assert rules == []

    def test_invalid_priority_falls_back_to_default(self, tmp_path: Path) -> None:
        p = tmp_path / "r.yaml"
        p.write_text(
            "rules:\n  - symbol: AAPL\n    alert_on: action_change\n    priority: extreme\n",
            encoding="utf-8",
        )
        rules = load_watch_rules(p)
        assert len(rules) == 1
        assert rules[0].priority == "default"

    def test_symbol_normalised_to_uppercase(self, tmp_path: Path) -> None:
        p = tmp_path / "r.yaml"
        p.write_text(
            "rules:\n  - symbol: aapl\n    alert_on: action_change\n",
            encoding="utf-8",
        )
        rules = load_watch_rules(p)
        assert rules[0].symbol == "AAPL"

    def test_multiple_rules_loaded(self, tmp_path: Path) -> None:
        p = tmp_path / "r.yaml"
        p.write_text(
            textwrap.dedent(
                """\
                rules:
                  - symbol: AAPL
                    alert_on: action_change
                  - symbol: "*"
                    alert_on: conviction_above
                    threshold: 0.85
                    priority: high
                  - symbol: MSFT
                    alert_on: conviction_below
                    threshold: 0.40
                """
            ),
            encoding="utf-8",
        )
        rules = load_watch_rules(p)
        assert len(rules) == 3

    def test_bad_rule_does_not_block_good_rules(self, tmp_path: Path) -> None:
        """A skipped rule must not prevent subsequent valid rules from loading."""
        p = tmp_path / "r.yaml"
        p.write_text(
            textwrap.dedent(
                """\
                rules:
                  - symbol: BAD
                    alert_on: unknown_type
                  - symbol: AAPL
                    alert_on: action_change
                """
            ),
            encoding="utf-8",
        )
        rules = load_watch_rules(p)
        assert len(rules) == 1
        assert rules[0].symbol == "AAPL"


# ---------------------------------------------------------------------------
# TestLoadSaveWatchState
# ---------------------------------------------------------------------------


class TestLoadSaveWatchState:
    def test_missing_file_returns_empty_dict(self, tmp_path: Path) -> None:
        state = load_watch_state(tmp_path / "watch_state.json")
        assert state == {}

    def test_corrupt_json_returns_empty_dict(self, tmp_path: Path) -> None:
        p = tmp_path / "watch_state.json"
        p.write_text("this is not json", encoding="utf-8")
        state = load_watch_state(p)
        assert state == {}

    def test_non_object_root_returns_empty_dict(self, tmp_path: Path) -> None:
        p = tmp_path / "watch_state.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        state = load_watch_state(p)
        assert state == {}

    def test_round_trip(self, tmp_path: Path) -> None:
        p = tmp_path / "watch_state.json"
        state_in = {
            "AAPL": SymbolWatchState(
                action="BUY",
                conviction=0.82,
                alerted_conviction_above={"0.85": False},
                alerted_conviction_below={},
                timestamp="2026-06-26T10:00:00+00:00",
            )
        }
        save_watch_state(state_in, p)
        state_out = load_watch_state(p)
        assert "AAPL" in state_out
        assert state_out["AAPL"].action == "BUY"
        assert state_out["AAPL"].conviction == pytest.approx(0.82)
        assert state_out["AAPL"].alerted_conviction_above == {"0.85": False}

    def test_atomic_write_leaves_no_tmp_file(self, tmp_path: Path) -> None:
        p = tmp_path / "watch_state.json"
        save_watch_state({}, p)
        assert p.exists()
        assert not p.with_suffix(".tmp").exists()

    def test_symbols_uppercased_on_load(self, tmp_path: Path) -> None:
        p = tmp_path / "watch_state.json"
        raw = {"aapl": {"action": "BUY", "conviction": 0.5}}
        p.write_text(json.dumps(raw), encoding="utf-8")
        state = load_watch_state(p)
        assert "AAPL" in state

    def test_save_creates_parent_dirs(self, tmp_path: Path) -> None:
        nested = tmp_path / "deep" / "output" / "watch_state.json"
        save_watch_state({}, nested)
        assert nested.exists()


# ---------------------------------------------------------------------------
# TestEvaluateWatchRules — core alert logic
# ---------------------------------------------------------------------------


class TestEvaluateWatchRules:
    # ----- baseline behaviour -----------------------------------------------

    def test_no_rules_returns_empty_alerts_and_updates_state(self) -> None:
        rec = _make_rec("AAPL", "BUY", 0.80)
        alerts, new_state = evaluate_watch_rules([], [rec], {})
        assert alerts == []
        assert "AAPL" in new_state
        assert new_state["AAPL"].action == "BUY"

    def test_no_recommendations_returns_empty_state(self) -> None:
        rule = WatchRule(symbol="AAPL", alert_on="action_change")
        alerts, new_state = evaluate_watch_rules([rule], [], {})
        assert alerts == []
        assert new_state == {}

    # ----- action_change ----------------------------------------------------

    def test_action_change_fires_on_hold_to_buy(self) -> None:
        rule = WatchRule(symbol="AAPL", alert_on="action_change")
        prev = {"AAPL": SymbolWatchState(action="HOLD", conviction=0.5)}
        rec = _make_rec("AAPL", "BUY", 0.80)
        alerts, _ = evaluate_watch_rules([rule], [rec], prev)
        assert len(alerts) == 1
        assert alerts[0].rule_type == "action_change"
        assert "HOLD" in alerts[0].trigger_detail
        assert "BUY" in alerts[0].trigger_detail

    def test_action_change_fires_on_buy_to_sell(self) -> None:
        rule = WatchRule(symbol="AAPL", alert_on="action_change")
        prev = {"AAPL": SymbolWatchState(action="BUY", conviction=0.80)}
        rec = _make_rec("AAPL", "SELL", 0.30)
        alerts, _ = evaluate_watch_rules([rule], [rec], prev)
        assert len(alerts) == 1
        assert "BUY" in alerts[0].trigger_detail and "SELL" in alerts[0].trigger_detail

    def test_action_change_no_fire_when_action_same(self) -> None:
        rule = WatchRule(symbol="AAPL", alert_on="action_change")
        prev = {"AAPL": SymbolWatchState(action="BUY", conviction=0.80)}
        rec = _make_rec("AAPL", "BUY", 0.82)
        alerts, _ = evaluate_watch_rules([rule], [rec], prev)
        assert alerts == []

    def test_action_change_no_fire_on_first_run_empty_prev(self) -> None:
        """First run: no prior action to compare against → no alert."""
        rule = WatchRule(symbol="AAPL", alert_on="action_change")
        rec = _make_rec("AAPL", "BUY", 0.80)
        alerts, _ = evaluate_watch_rules([rule], [rec], {})
        assert alerts == []

    def test_action_change_new_state_captures_current_action(self) -> None:
        rule = WatchRule(symbol="AAPL", alert_on="action_change")
        prev = {"AAPL": SymbolWatchState(action="HOLD", conviction=0.5)}
        rec = _make_rec("AAPL", "BUY", 0.80)
        _, new_state = evaluate_watch_rules([rule], [rec], prev)
        assert new_state["AAPL"].action == "BUY"

    # ----- conviction_above -------------------------------------------------

    def test_conviction_above_fires_on_rising_edge(self) -> None:
        rule = WatchRule(symbol="AAPL", alert_on="conviction_above", threshold=0.85)
        prev = {
            "AAPL": SymbolWatchState(
                action="BUY",
                conviction=0.82,
                alerted_conviction_above={"0.85": False},
            )
        }
        rec = _make_rec("AAPL", "BUY", 0.90)
        alerts, new_state = evaluate_watch_rules([rule], [rec], prev)
        assert len(alerts) == 1
        assert alerts[0].rule_type == "conviction_above"
        # Edge state updated: was below, now above → tracked as True
        assert new_state["AAPL"].alerted_conviction_above.get("0.85") is True

    def test_conviction_above_no_fire_when_already_above(self) -> None:
        """Already above threshold — must NOT re-fire (spam prevention)."""
        rule = WatchRule(symbol="AAPL", alert_on="conviction_above", threshold=0.85)
        prev = {
            "AAPL": SymbolWatchState(
                action="BUY",
                conviction=0.90,
                alerted_conviction_above={"0.85": True},
            )
        }
        rec = _make_rec("AAPL", "BUY", 0.92)
        alerts, _ = evaluate_watch_rules([rule], [rec], prev)
        assert alerts == []

    def test_conviction_above_resets_and_refires_after_drop_below(self) -> None:
        """Drops below threshold → state resets → next crossing fires again."""
        rule = WatchRule(symbol="AAPL", alert_on="conviction_above", threshold=0.85)

        # Step 1: was above (alerted), now drops below
        prev_above = {
            "AAPL": SymbolWatchState(
                action="BUY",
                conviction=0.90,
                alerted_conviction_above={"0.85": True},
            )
        }
        rec_below = _make_rec("AAPL", "HOLD", 0.70)
        alerts1, state1 = evaluate_watch_rules([rule], [rec_below], prev_above)
        assert alerts1 == []  # dropping below does NOT fire conviction_above
        assert state1["AAPL"].alerted_conviction_above.get("0.85") is False  # reset

        # Step 2: rises above again → fires
        rec_above = _make_rec("AAPL", "BUY", 0.88)
        alerts2, _ = evaluate_watch_rules([rule], [rec_above], state1)
        assert len(alerts2) == 1
        assert alerts2[0].rule_type == "conviction_above"

    def test_conviction_above_fires_on_first_run_when_already_above(self) -> None:
        """First run with no prev state: fires if condition is already met."""
        rule = WatchRule(symbol="AAPL", alert_on="conviction_above", threshold=0.85)
        rec = _make_rec("AAPL", "BUY", 0.90)
        alerts, _ = evaluate_watch_rules([rule], [rec], {})
        assert len(alerts) == 1

    def test_conviction_above_no_fire_on_first_run_when_below(self) -> None:
        rule = WatchRule(symbol="AAPL", alert_on="conviction_above", threshold=0.85)
        rec = _make_rec("AAPL", "HOLD", 0.70)
        alerts, _ = evaluate_watch_rules([rule], [rec], {})
        assert alerts == []

    # ----- conviction_below -------------------------------------------------

    def test_conviction_below_fires_on_falling_edge(self) -> None:
        rule = WatchRule(symbol="AAPL", alert_on="conviction_below", threshold=0.50)
        prev = {
            "AAPL": SymbolWatchState(
                action="HOLD",
                conviction=0.60,
                alerted_conviction_below={"0.5": False},
            )
        }
        rec = _make_rec("AAPL", "HOLD", 0.40)
        alerts, new_state = evaluate_watch_rules([rule], [rec], prev)
        assert len(alerts) == 1
        assert alerts[0].rule_type == "conviction_below"
        assert new_state["AAPL"].alerted_conviction_below.get("0.5") is True

    def test_conviction_below_no_spam_while_sustained_below(self) -> None:
        rule = WatchRule(symbol="AAPL", alert_on="conviction_below", threshold=0.50)
        prev = {
            "AAPL": SymbolWatchState(
                action="HOLD",
                conviction=0.40,
                alerted_conviction_below={"0.5": True},
            )
        }
        rec = _make_rec("AAPL", "HOLD", 0.38)
        alerts, _ = evaluate_watch_rules([rule], [rec], prev)
        assert alerts == []

    def test_conviction_below_resets_and_refires_after_recovery(self) -> None:
        rule = WatchRule(symbol="AAPL", alert_on="conviction_below", threshold=0.50)
        # Step 1: was below (alerted), rises above
        prev_below = {
            "AAPL": SymbolWatchState(
                action="HOLD",
                conviction=0.40,
                alerted_conviction_below={"0.5": True},
            )
        }
        rec_above = _make_rec("AAPL", "BUY", 0.70)
        alerts1, state1 = evaluate_watch_rules([rule], [rec_above], prev_below)
        assert alerts1 == []
        assert state1["AAPL"].alerted_conviction_below.get("0.5") is False  # reset

        # Step 2: drops below again → fires again
        rec_below = _make_rec("AAPL", "HOLD", 0.35)
        alerts2, _ = evaluate_watch_rules([rule], [rec_below], state1)
        assert len(alerts2) == 1

    # ----- wildcard rule ----------------------------------------------------

    def test_wildcard_rule_matches_all_symbols_in_universe(self) -> None:
        rule = WatchRule(symbol="*", alert_on="action_change")
        prev = {
            "AAPL": SymbolWatchState(action="HOLD", conviction=0.5),
            "MSFT": SymbolWatchState(action="HOLD", conviction=0.6),
        }
        recs = [
            _make_rec("AAPL", "BUY", 0.80),
            _make_rec("MSFT", "SELL", 0.30),
        ]
        alerts, _ = evaluate_watch_rules([rule], recs, prev)
        syms = {a.symbol for a in alerts}
        assert "AAPL" in syms
        assert "MSFT" in syms

    def test_wildcard_skips_symbols_not_in_universe(self) -> None:
        rule = WatchRule(symbol="*", alert_on="action_change")
        prev = {
            "NVDA": SymbolWatchState(action="HOLD", conviction=0.5),
        }
        recs = [_make_rec("AAPL", "BUY", 0.80)]
        # NVDA is in prev_state but not in current recs — must not fire
        alerts, _ = evaluate_watch_rules([rule], recs, prev)
        for a in alerts:
            assert a.symbol != "NVDA"

    def test_specific_symbol_rule_ignores_other_symbols(self) -> None:
        rule = WatchRule(symbol="NVDA", alert_on="action_change")
        prev = {
            "AAPL": SymbolWatchState(action="HOLD", conviction=0.5),
            "NVDA": SymbolWatchState(action="HOLD", conviction=0.6),
        }
        recs = [
            _make_rec("AAPL", "BUY", 0.80),  # flipped but not the target symbol
            _make_rec("NVDA", "HOLD", 0.62),  # no flip
        ]
        alerts, _ = evaluate_watch_rules([rule], recs, prev)
        assert alerts == []

    # ----- resilience -------------------------------------------------------

    def test_bad_rule_does_not_abort_other_rules(self) -> None:
        """A rule whose evaluation raises must not prevent subsequent rules firing."""
        # Manufacture a rule with threshold=None that will cause an AssertionError
        # inside _evaluate_conviction_above.
        broken_rule = WatchRule(
            symbol="AAPL", alert_on="conviction_above", threshold=None  # type: ignore[arg-type]
        )
        good_rule = WatchRule(symbol="MSFT", alert_on="action_change")
        prev = {"MSFT": SymbolWatchState(action="HOLD", conviction=0.6)}
        recs = [
            _make_rec("AAPL", "BUY", 0.90),
            _make_rec("MSFT", "BUY", 0.75),
        ]
        # Must not raise; MSFT alert must still fire
        alerts, _ = evaluate_watch_rules([broken_rule, good_rule], recs, prev)
        msft_alerts = [a for a in alerts if a.symbol == "MSFT"]
        assert len(msft_alerts) == 1

    def test_data_quality_partial_rec_still_triggers_alert(self) -> None:
        """PARTIAL data quality does not exclude a rec from watch evaluation."""
        rule = WatchRule(symbol="AAPL", alert_on="action_change")
        prev = {"AAPL": SymbolWatchState(action="HOLD", conviction=0.5)}
        rec = _make_rec("AAPL", "BUY", 0.70)
        rec.data_quality = "PARTIAL"
        alerts, _ = evaluate_watch_rules([rule], [rec], prev)
        assert len(alerts) == 1

    # ----- no-lookahead invariant -------------------------------------------

    def test_evaluate_uses_only_past_state_and_current_recs(self) -> None:
        """evaluate_watch_rules must NOT reach into market data providers.

        This test verifies the no-lookahead contract structurally: we patch
        data.market_data.get_provider to raise, then confirm evaluate_watch_rules
        still succeeds — proving it never calls the market data layer.
        """
        rule = WatchRule(symbol="AAPL", alert_on="action_change")
        prev = {"AAPL": SymbolWatchState(action="HOLD", conviction=0.5)}
        rec = _make_rec("AAPL", "BUY", 0.80)

        with mock.patch("data.market_data.get_provider", side_effect=RuntimeError("NO FETCH")):
            alerts, _ = evaluate_watch_rules([rule], [rec], prev)

        assert len(alerts) == 1  # alert still fires despite market-data patch

    # ----- multiple rules for same symbol -----------------------------------

    def test_multiple_rules_can_fire_independently_for_same_symbol(self) -> None:
        rules = [
            WatchRule(symbol="AAPL", alert_on="action_change"),
            WatchRule(symbol="AAPL", alert_on="conviction_above", threshold=0.85),
        ]
        prev = {
            "AAPL": SymbolWatchState(
                action="HOLD",
                conviction=0.70,
                alerted_conviction_above={"0.85": False},
            )
        }
        rec = _make_rec("AAPL", "BUY", 0.90)
        alerts, _ = evaluate_watch_rules(rules, [rec], prev)
        rule_types = {a.rule_type for a in alerts}
        assert "action_change" in rule_types
        assert "conviction_above" in rule_types


# ---------------------------------------------------------------------------
# TestDispatchWatchAlerts
# ---------------------------------------------------------------------------


class TestDispatchWatchAlerts:
    def test_empty_list_is_noop(self) -> None:
        with mock.patch("alerting.notify") as mock_n:
            dispatch_watch_alerts([])
            mock_n.assert_not_called()

    def test_dispatches_one_notify_per_alert(self) -> None:
        alerts = [
            WatchAlert(
                symbol="AAPL",
                rule_type="action_change",
                priority="default",
                title="Title 1",
                message="Msg 1",
                trigger_detail="HOLD→BUY",
            ),
            WatchAlert(
                symbol="MSFT",
                rule_type="conviction_above",
                priority="high",
                title="Title 2",
                message="Msg 2",
                trigger_detail="0.90≥0.85",
            ),
        ]
        with mock.patch("alerting.notify") as mock_n:
            dispatch_watch_alerts(alerts)
        assert mock_n.call_count == 2

    def test_alert_title_passed_correctly(self) -> None:
        alert = WatchAlert(
            symbol="AAPL",
            rule_type="action_change",
            priority="default",
            title="MyTitle",
            message="Body",
            trigger_detail="d",
        )
        with mock.patch("alerting.notify") as mock_n:
            dispatch_watch_alerts([alert])
        call_kwargs = mock_n.call_args[1]
        assert call_kwargs.get("title") == "MyTitle"

    def test_dashboard_url_appended_to_message(self) -> None:
        alert = WatchAlert(
            symbol="AAPL",
            rule_type="action_change",
            priority="default",
            title="T",
            message="Base message",
            trigger_detail="d",
        )
        with mock.patch("alerting.notify") as mock_n:
            dispatch_watch_alerts([alert], dashboard_url="http://localhost:8501")
        msg = mock_n.call_args[1].get("message", "")
        assert "localhost:8501" in msg
        assert "Base message" in msg

    def test_notify_failure_does_not_raise(self) -> None:
        alert = WatchAlert(
            symbol="AAPL",
            rule_type="action_change",
            priority="default",
            title="T",
            message="M",
            trigger_detail="d",
        )
        with mock.patch("alerting.notify", side_effect=RuntimeError("boom")):
            dispatch_watch_alerts([alert])  # must not raise

    def test_priority_passed_through_to_notify(self) -> None:
        alert = WatchAlert(
            symbol="AAPL",
            rule_type="conviction_above",
            priority="urgent",
            title="T",
            message="M",
            trigger_detail="d",
        )
        with mock.patch("alerting.notify") as mock_n:
            dispatch_watch_alerts([alert])
        assert mock_n.call_args[1].get("priority") == "urgent"


# ---------------------------------------------------------------------------
# TestMainPyIntegration — structural guard
# ---------------------------------------------------------------------------


class TestMainPyIntegration:
    def test_main_py_references_watch_engine(self) -> None:
        """main.py must import or reference watch_engine (no silent drop)."""
        src = Path("main.py").read_text(encoding="utf-8")
        assert "watch_engine" in src, "main.py must reference watch_engine"

    def test_main_py_calls_evaluate_watch_rules(self) -> None:
        src = Path("main.py").read_text(encoding="utf-8")
        assert "evaluate_watch_rules" in src

    def test_main_py_calls_save_watch_state(self) -> None:
        src = Path("main.py").read_text(encoding="utf-8")
        assert "save_watch_state" in src

    def test_settings_has_watch_rules_file(self) -> None:
        from settings import settings

        assert hasattr(settings, "WATCH_RULES_FILE")
        assert isinstance(settings.WATCH_RULES_FILE, str)
        assert "watch_rules" in settings.WATCH_RULES_FILE

    def test_watch_rules_yaml_exists_at_project_root(self) -> None:
        assert Path("watch_rules.yaml").exists(), (
            "watch_rules.yaml must exist at the project root to serve as the "
            "default example configuration for operators."
        )
