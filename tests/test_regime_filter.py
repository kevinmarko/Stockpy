"""
tests/test_regime_filter.py
===========================
Unit tests for the cross-tab macro-regime filter (``gui/regime_filter.py``) and

These are fully offline and Streamlit-free.
"""

from __future__ import annotations

import pytest

from shared.regime_filter import (
    ALL_REGIMES_LABEL,
    apply_regime_filter,
    filter_snapshot,
    is_all_regimes,
)


def _sig(symbol: str, regime: str) -> dict:
    return {"symbol": symbol, "macro_status": regime}


# ---------------------------------------------------------------------------
# apply_regime_filter — filtering behavior
# ---------------------------------------------------------------------------


def test_filters_to_matching_regime():
    signals = [
        _sig("AAPL", "RISK ON"),
        _sig("MSFT", "RECESSION"),
        _sig("NVDA", "RISK ON"),
    ]
    out = apply_regime_filter(signals, "RISK ON")
    assert [s["symbol"] for s in out] == ["AAPL", "NVDA"]


def test_match_is_case_insensitive_and_trimmed():
    signals = [_sig("AAPL", "  risk on "), _sig("MSFT", "RECESSION")]
    out = apply_regime_filter(signals, "Risk On")
    assert [s["symbol"] for s in out] == ["AAPL"]


def test_no_match_returns_empty_list():
    signals = [_sig("AAPL", "RISK ON")]
    out = apply_regime_filter(signals, "CREDIT EVENT")
    assert out == []


def test_falls_back_to_market_regime_key():
    # Advisory-style signals carry "market_regime" (not "macro_status").
    signals = [{"symbol": "AAPL", "market_regime": "NEUTRAL"}]
    assert apply_regime_filter(signals, "NEUTRAL")[0]["symbol"] == "AAPL"
    assert apply_regime_filter(signals, "RISK ON") == []


def test_default_regime_fallback_for_keyless_signals():
    # Signals with no per-signal regime key match via the default_regime.
    signals = [{"symbol": "AAPL"}, {"symbol": "MSFT"}]
    out = apply_regime_filter(signals, "RECESSION", default_regime="RECESSION")
    assert [s["symbol"] for s in out] == ["AAPL", "MSFT"]
    # ...and do NOT match a different concrete selection.
    assert apply_regime_filter(signals, "RISK ON", default_regime="RECESSION") == []
    # Without a default, keyless signals never match a concrete filter.
    assert apply_regime_filter(signals, "RECESSION") == []


# ---------------------------------------------------------------------------
# apply_regime_filter — "All" / None no-op pass-through (identity)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "regime", [None, "All regimes", "all", "ALL", "  All Regimes  ", "", "*", "any"]
)
def test_all_sentinels_are_identity_noop(regime):
    signals = [_sig("AAPL", "RISK ON"), _sig("MSFT", "RECESSION")]
    out = apply_regime_filter(signals, regime)
    # Same object returned — a true no-op the caller can detect via identity.
    assert out is signals


def test_all_regimes_label_constant_is_a_sentinel():
    assert is_all_regimes(ALL_REGIMES_LABEL) is True
    assert is_all_regimes("RISK ON") is False
    assert is_all_regimes(None) is True


# ---------------------------------------------------------------------------
# apply_regime_filter — dead-letter safety on malformed input
# ---------------------------------------------------------------------------


def test_empty_list_returns_empty():
    assert apply_regime_filter([], "RISK ON") == []


@pytest.mark.parametrize("bad", [None, {"not": "a list"}, 42, "string"])
def test_non_list_input_returned_unchanged(bad):
    assert apply_regime_filter(bad, "RISK ON") is bad


def test_malformed_signal_entries_are_skipped_not_raised():
    signals = [_sig("AAPL", "RISK ON"), None, 7, "junk", {"symbol": "X"}]
    out = apply_regime_filter(signals, "RISK ON")
    assert out == [{"symbol": "AAPL", "macro_status": "RISK ON"}]


# ---------------------------------------------------------------------------
# filter_snapshot — snapshot-aware wrapper
# ---------------------------------------------------------------------------


def test_filter_snapshot_all_regimes_returns_identity():
    snap = {"market_regime": "RISK ON", "signals": [_sig("AAPL", "RISK ON")]}
    assert filter_snapshot(snap, "All regimes") is snap
    assert filter_snapshot(snap, None) is snap


def test_filter_snapshot_filters_and_shallow_copies():
    original_signals = [_sig("AAPL", "RISK ON"), _sig("MSFT", "RECESSION")]
    snap = {"market_regime": "RISK ON", "signals": original_signals, "vix": 18.0}
    out = filter_snapshot(snap, "RISK ON")
    assert out is not snap  # shallow copy, original untouched
    assert snap["signals"] is original_signals  # original list not mutated
    assert [s["symbol"] for s in out["signals"]] == ["AAPL"]
    assert out["vix"] == 18.0  # other keys preserved


def test_filter_snapshot_uses_top_level_regime_fallback():
    # Advisory snapshot: signals carry no per-signal regime; the top-level
    # market_regime drives the match.
    snap = {
        "market_regime": "NEUTRAL",
        "signals": [{"symbol": "AAPL"}, {"symbol": "MSFT"}],
    }
    matched = filter_snapshot(snap, "NEUTRAL")
    assert [s["symbol"] for s in matched["signals"]] == ["AAPL", "MSFT"]
    none = filter_snapshot(snap, "RISK ON")
    assert none["signals"] == []


@pytest.mark.parametrize("bad", [None, [], "x", 5])
def test_filter_snapshot_non_dict_returned_unchanged(bad):
    assert filter_snapshot(bad, "RISK ON") is bad


def test_filter_snapshot_missing_signals_key_unchanged():
    snap = {"market_regime": "RISK ON"}  # no "signals"
    assert filter_snapshot(snap, "RISK ON") is snap


# ---------------------------------------------------------------------------
# styling — shared palette exposes light + dark; Styler helpers return CSS
# ---------------------------------------------------------------------------
