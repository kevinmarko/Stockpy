"""Tests for pilots/retrospective_narrative.py -- the strictly-templated (no
LLM) per-trade narrative for the Retrospective Learning Loop (Trade Journal).

Fixtures construct plain dicts matching the LOCKED
``pilots.retrospective_composer.compose_trade_retrospective`` output shape
directly (per this work package's brief) rather than importing that sibling
module, which may not exist on disk / may still be under active edit.
"""

from __future__ import annotations

import copy
from typing import Any, Dict

import pytest

from pilots.retrospective_narrative import (
    _factor_clause,
    _move_sentence,
    _outcome_sentence,
    _provenance_label,
    _what_happened_sentence,
    _why_sentence,
    build_trade_narrative,
)


def _retro(**overrides: Any) -> Dict[str, Any]:
    """A fully-populated, valid retrospective record. Individual fields (top
    level or nested ``evaluation``/``decision``) can be overridden via
    dotted-ish kwargs handled below, or the caller can pass whole replacement
    sub-dicts directly."""
    base: Dict[str, Any] = {
        "trade_id": 42,
        "symbol": "AAPL",
        "strategy_id": "earnings-crush",
        "pilot_id": "earnings-crush",
        "side": "BUY",
        "qty": 10.0,
        "entry_ts": "2026-01-05T14:30:00+00:00",
        "entry_price": 150.0,
        "exit_ts": "2026-01-10T09:15:00+00:00",
        "exit_price": 162.0,
        "realized_pnl": 120.0,
        "realized_pnl_pct": 0.08,
        "holding_period_days": 5.0,
        "close_reason": "flatten",
        "evaluation": {
            "available": True,
            "mfe": 0.15,
            "mae": 0.03,
            "edge_ratio": 5.0,
            "reason": None,
        },
        "decision": {
            "state": "signal_driven",
            "provenance": "automated:options_auto_scan",
            "conviction": 0.72,
            "regime": "RISK_ON",
            "factors": {"ivr": 62.3, "trend_bias": "Bullish"},
            "notes": "auto-scanned",
        },
    }
    out = copy.deepcopy(base)
    for key, value in overrides.items():
        out[key] = value
    return out


# ---------------------------------------------------------------------------
# _what_happened_sentence
# ---------------------------------------------------------------------------


def test_what_happened_normal_values():
    sentence = _what_happened_sentence(_retro())
    assert sentence == (
        "Bought 10 AAPL — opened 2026-01-05 14:30 UTC at $150.00, "
        "closed 2026-01-10 09:15 UTC at $162.00 (flatten)."
    )


def test_what_happened_entry_ts_none():
    sentence = _what_happened_sentence(_retro(entry_ts=None))
    assert "opened at an unknown time" in sentence
    assert "closed 2026-01-10 09:15 UTC at $162.00" in sentence


def test_what_happened_sell_side():
    sentence = _what_happened_sentence(_retro(side="SELL"))
    assert sentence.startswith("Sold 10 AAPL")


def test_what_happened_unknown_side_does_not_fabricate_bought_or_sold():
    sentence = _what_happened_sentence(_retro(side="FLIP"))
    assert "Bought" not in sentence
    assert "Sold" not in sentence
    assert "FLIP" in sentence


def test_what_happened_missing_qty_and_symbol_degrades_gracefully():
    retro = _retro(qty=None, symbol=None)
    sentence = _what_happened_sentence(retro)
    assert "an unknown quantity of" in sentence
    assert "the position" in sentence


# ---------------------------------------------------------------------------
# _outcome_sentence
# ---------------------------------------------------------------------------


def test_outcome_sentence_normal_values():
    sentence = _outcome_sentence(_retro())
    assert sentence == "Realized P&L: +$120.00 (+8.00%)."


def test_outcome_sentence_negative_pnl():
    sentence = _outcome_sentence(_retro(realized_pnl=-45.5, realized_pnl_pct=-0.03))
    assert sentence == "Realized P&L: -$45.50 (-3.00%)."


def test_outcome_sentence_realized_pnl_pct_none_omits_percentage_clause():
    sentence = _outcome_sentence(_retro(realized_pnl_pct=None))
    assert sentence == "Realized P&L: +$120.00 (percentage unavailable — degenerate entry price)."


def test_outcome_sentence_realized_pnl_pct_nan_treated_like_none():
    sentence = _outcome_sentence(_retro(realized_pnl_pct=float("nan")))
    assert "percentage unavailable" in sentence
    assert "nan" not in sentence.lower()


# ---------------------------------------------------------------------------
# _move_sentence
# ---------------------------------------------------------------------------


def test_move_sentence_available():
    sentence = _move_sentence(_retro())
    assert "15.0%" in sentence
    assert "3.0%" in sentence
    assert "5.00" in sentence
    assert "in your favor" in sentence
    assert "against you" in sentence


def test_move_sentence_unavailable_renders_reason_verbatim():
    reason = "No pricing data found between entry and exit dates."
    retro = _retro(evaluation={"available": False, "mfe": None, "mae": None, "edge_ratio": None, "reason": reason})
    sentence = _move_sentence(retro)
    assert sentence == f"Evaluation data unavailable for this trade — {reason}."


def test_move_sentence_unavailable_missing_reason_uses_generic_fallback_not_none():
    retro = _retro(evaluation={"available": False, "mfe": None, "mae": None, "edge_ratio": None, "reason": None})
    sentence = _move_sentence(retro)
    assert "None" not in sentence
    assert "unavailable" in sentence


def test_move_sentence_available_but_edge_ratio_missing():
    retro = _retro(
        evaluation={"available": True, "mfe": 0.10, "mae": 0.05, "edge_ratio": None, "reason": None}
    )
    sentence = _move_sentence(retro)
    assert "Edge Ratio unavailable" in sentence
    assert "None" not in sentence


# ---------------------------------------------------------------------------
# _why_sentence -- the fabrication-sensitive branch
# ---------------------------------------------------------------------------


def test_why_sentence_signal_driven():
    sentence = _why_sentence(_retro())
    assert sentence == (
        "The model rated this a 0.72 conviction automated options trade, "
        "driven partly by an IVR of 62.3 and a bullish trend bias."
    )


def test_why_sentence_manual_exact_required_wording():
    retro = _retro(decision={"state": "manual", "provenance": "manual", "conviction": None,
                              "regime": None, "factors": None, "notes": None})
    sentence = _why_sentence(retro)
    assert sentence == "You placed this trade manually — no model signal was behind it."


def test_why_sentence_unknown_exact_required_wording():
    retro = _retro(decision={"state": "unknown", "provenance": None, "conviction": None,
                              "regime": None, "factors": None, "notes": None})
    sentence = _why_sentence(retro)
    assert sentence == "Entry context wasn't captured for this trade."


def test_why_sentence_unexpected_state_falls_back_to_unknown_wording():
    """CONSTRAINT #6: an out-of-contract decision.state defends into the
    "unknown" text rather than crashing or guessing."""
    retro = _retro(decision={"state": "some_future_state", "provenance": "whatever",
                              "conviction": 0.9, "regime": None, "factors": None, "notes": None})
    sentence = _why_sentence(retro)
    assert sentence == "Entry context wasn't captured for this trade."


def test_why_sentence_missing_decision_key_falls_back_to_unknown_wording():
    retro = _retro()
    del retro["decision"]
    sentence = _why_sentence(retro)
    assert sentence == "Entry context wasn't captured for this trade."


def test_why_sentence_signal_driven_conviction_none_never_renders_none():
    retro = _retro(decision={"state": "signal_driven", "provenance": "automated:options_auto_scan",
                              "conviction": None, "regime": "RISK_ON", "factors": None, "notes": None})
    sentence = _why_sentence(retro)
    assert "None" not in sentence
    assert "conviction" not in sentence  # the whole "X conviction" clause is dropped, not just the number
    assert "automated options trade" in sentence


def test_why_sentence_signal_driven_no_factors_omits_factor_clause():
    retro = _retro(decision={"state": "signal_driven", "provenance": "automated:options_auto_scan",
                              "conviction": 0.5, "regime": None, "factors": None, "notes": None})
    sentence = _why_sentence(retro)
    assert sentence == "The model rated this a 0.50 conviction automated options trade."
    assert "driven partly by" not in sentence


def test_why_sentence_signal_driven_factors_never_invents_a_key_not_present():
    factors = {"vix": 18.2}
    retro = _retro(decision={"state": "signal_driven", "provenance": "automated:zero_dte_engine",
                              "conviction": 0.6, "regime": None, "factors": factors, "notes": None})
    sentence = _why_sentence(retro)
    assert "18.2" in sentence
    assert "ivr" not in sentence.lower()  # never invents a factor that wasn't in the dict
    assert "trend" not in sentence.lower()


def test_why_sentence_signal_driven_factors_skip_none_valued_entries():
    factors = {"ivr": None, "vrp": 0.031}
    retro = _retro(decision={"state": "signal_driven", "provenance": "automated:options_auto_scan",
                              "conviction": 0.6, "regime": None, "factors": factors, "notes": None})
    sentence = _why_sentence(retro)
    assert "None" not in sentence
    assert "0.031" in sentence


# ---------------------------------------------------------------------------
# _provenance_label / _factor_clause -- small direct unit tests
# ---------------------------------------------------------------------------


def test_provenance_label_derivation():
    assert _provenance_label("automated:options_auto_scan") == "automated options"
    assert _provenance_label(None) == "automated"
    assert _provenance_label("") == "automated"
    assert _provenance_label("manual") == "manual"


def test_factor_clause_none_and_empty_dict_both_omit():
    assert _factor_clause(None) is None
    assert _factor_clause({}) is None


def test_factor_clause_caps_at_two_factors():
    factors = {"ivr": 62.3, "vrp": 0.031, "vix": 18.2}
    clause = _factor_clause(factors)
    assert clause is not None
    assert clause.count(" and ") == 1  # exactly two factors joined, never more


# ---------------------------------------------------------------------------
# build_trade_narrative -- full integration + robustness
# ---------------------------------------------------------------------------


def test_build_trade_narrative_normal_case_joins_four_sentences():
    narrative = build_trade_narrative(_retro())
    assert narrative.count(".") >= 4
    assert narrative.startswith("Bought 10 AAPL")
    assert "Realized P&L: +$120.00 (+8.00%)." in narrative
    assert "in your favor" in narrative
    assert "The model rated this a 0.72 conviction automated options trade" in narrative


def test_build_trade_narrative_malformed_dict_missing_decision_key_does_not_raise():
    retro = _retro()
    del retro["decision"]
    narrative = build_trade_narrative(retro)
    assert isinstance(narrative, str)
    assert "Entry context wasn't captured for this trade." in narrative


def test_build_trade_narrative_missing_evaluation_key_does_not_raise():
    retro = _retro()
    del retro["evaluation"]
    narrative = build_trade_narrative(retro)
    assert isinstance(narrative, str)
    assert "Evaluation data unavailable" in narrative


def test_build_trade_narrative_completely_empty_dict_does_not_raise():
    narrative = build_trade_narrative({})
    assert isinstance(narrative, str)
    assert narrative  # non-empty best-effort narrative


def test_build_trade_narrative_non_dict_input_does_not_raise():
    narrative = build_trade_narrative(None)  # type: ignore[arg-type]
    assert isinstance(narrative, str)
    narrative2 = build_trade_narrative("not a dict")  # type: ignore[arg-type]
    assert isinstance(narrative2, str)


def test_build_trade_narrative_manual_state_end_to_end():
    retro = _retro(decision={"state": "manual", "provenance": "manual", "conviction": None,
                              "regime": None, "factors": None, "notes": "quick trade click"})
    narrative = build_trade_narrative(retro)
    assert "You placed this trade manually — no model signal was behind it." in narrative


def test_build_trade_narrative_unknown_state_end_to_end():
    retro = _retro(decision={"state": "unknown", "provenance": None, "conviction": None,
                              "regime": None, "factors": None, "notes": None})
    narrative = build_trade_narrative(retro)
    assert "Entry context wasn't captured for this trade." in narrative


# ---------------------------------------------------------------------------
# The fabrication-risk sweep: across EVERY fixture constructed in this file,
# no rendered narrative may ever contain the literal substring
# "None"/"nan"/"NaN".
# ---------------------------------------------------------------------------


def _all_fixture_retros() -> list:
    """Every distinct retro shape exercised by the tests above, re-assembled
    here so the "never renders None/nan/NaN" sweep covers all of them in one
    place."""
    fixtures = [
        _retro(),
        _retro(entry_ts=None),
        _retro(side="SELL"),
        _retro(side="FLIP"),
        _retro(qty=None, symbol=None),
        _retro(realized_pnl_pct=None),
        _retro(realized_pnl_pct=float("nan")),
        _retro(realized_pnl=-45.5, realized_pnl_pct=-0.03),
        _retro(evaluation={"available": False, "mfe": None, "mae": None, "edge_ratio": None,
                            "reason": "No pricing data found between entry and exit dates."}),
        _retro(evaluation={"available": False, "mfe": None, "mae": None, "edge_ratio": None, "reason": None}),
        _retro(evaluation={"available": True, "mfe": 0.10, "mae": 0.05, "edge_ratio": None, "reason": None}),
        _retro(decision={"state": "manual", "provenance": "manual", "conviction": None,
                          "regime": None, "factors": None, "notes": None}),
        _retro(decision={"state": "unknown", "provenance": None, "conviction": None,
                          "regime": None, "factors": None, "notes": None}),
        _retro(decision={"state": "some_future_state", "provenance": "whatever", "conviction": 0.9,
                          "regime": None, "factors": None, "notes": None}),
        _retro(decision={"state": "signal_driven", "provenance": "automated:options_auto_scan",
                          "conviction": None, "regime": "RISK_ON", "factors": None, "notes": None}),
        _retro(decision={"state": "signal_driven", "provenance": "automated:options_auto_scan",
                          "conviction": 0.5, "regime": None, "factors": None, "notes": None}),
        _retro(decision={"state": "signal_driven", "provenance": "automated:zero_dte_engine",
                          "conviction": 0.6, "regime": None, "factors": {"vix": 18.2}, "notes": None}),
        _retro(decision={"state": "signal_driven", "provenance": "automated:options_auto_scan",
                          "conviction": 0.6, "regime": None, "factors": {"ivr": None, "vrp": 0.031},
                          "notes": None}),
        {},
    ]
    return fixtures


@pytest.mark.parametrize("retro", _all_fixture_retros())
def test_no_fixture_ever_renders_none_nan_or_NaN(retro):
    narrative = build_trade_narrative(retro)
    assert "None" not in narrative, narrative
    assert "nan" not in narrative.lower(), narrative
    assert "NaN" not in narrative, narrative


def test_malformed_and_non_dict_inputs_also_never_render_none_nan():
    for bad_input in (None, "not a dict", 42, [], {"decision": None, "evaluation": None}):
        narrative = build_trade_narrative(bad_input)  # type: ignore[arg-type]
        assert "None" not in narrative, narrative
        assert "nan" not in narrative.lower(), narrative
        assert "NaN" not in narrative, narrative


# ---------------------------------------------------------------------------
# Optional integration proof with the REAL sibling composer, if it exists on
# disk in this worktree. The CORE test suite above does not import or
# depend on pilots.retrospective_composer at all -- this is purely a bonus
# end-to-end wiring check.
# ---------------------------------------------------------------------------


def test_integration_with_real_retrospective_composer_if_present(monkeypatch):
    try:
        from pilots.retrospective_composer import compose_trade_retrospective
    except Exception:
        pytest.skip("pilots/retrospective_composer.py not available in this worktree yet")

    # Force the evaluation section to an honest "unavailable" degrade rather
    # than attempting a real network/DB bars fetch -- this test only proves
    # the two modules WIRE UP correctly end-to-end, not the composer's own
    # evaluation math (that belongs to tests/test_retrospective_composer.py).
    import data.historical_store as historical_store_module

    class _BoomHistoricalStore:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("no bars in this smoke test")

    monkeypatch.setattr(historical_store_module, "HistoricalStore", _BoomHistoricalStore)

    # Shaped exactly like a PaperAccountStore.get_full_closed_trades() row.
    closed_trade = {
        "trade_id": 999,
        "strategy_id": "sig-integration",
        "pilot_id": None,
        "experiment_arm": None,
        "symbol": "AAPL",
        "side": "BUY",
        "qty": 10.0,
        "entry_ts": "2026-01-05T14:30:00+00:00",
        "entry_price": 150.0,
        "exit_ts": "2026-01-10T09:15:00+00:00",
        "exit_price": 162.0,
        "commission": 1.0,
        "realized_pnl": 119.0,
        "realized_pnl_pct": 0.0793,
        "holding_period_days": 5.0,
        "close_reason": "flatten",
        "leg_group_id": None,
    }

    composed = compose_trade_retrospective(closed_trade)

    for key in (
        "trade_id", "symbol", "strategy_id", "side", "qty", "entry_price",
        "exit_ts", "exit_price", "realized_pnl", "close_reason", "evaluation", "decision",
    ):
        assert key in composed

    narrative = build_trade_narrative(composed)
    assert isinstance(narrative, str)
    assert "None" not in narrative
    assert "nan" not in narrative.lower()
    assert "AAPL" in narrative
