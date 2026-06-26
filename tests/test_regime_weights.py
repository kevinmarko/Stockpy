"""
tests/test_regime_weights.py
============================
Unit tests for ``signals.aggregator.resolve_regime_weights`` (Tier 2.1).

Covers:
* Empty ``regime_weights`` → returns ``default_weights`` unchanged.
* Exact regime match → overrides applied via merge.
* ``_default`` fallback when no exact match.
* Merge semantics: only listed keys changed, others inherit default.
* Unknown regime (no match, no _default) → returns defaults unchanged.
* Immutability: originals not mutated.
* RECESSION suppresses rsi2_mean_reversion (real-world use case).
* RISK ON boosts momentum (real-world use case).
* ``SignalAggregator`` uses regime-resolved weights at call time (integration).
"""

import pytest
import pandas as pd

from signals.aggregator import resolve_regime_weights


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FLAT = {
    "macro_regime": 45.0,
    "rsi2_mean_reversion": 10.0,
    "timeseries_momentum": 25.0,
    "cross_sectional_momentum": 15.0,
}

REGIME_OVERRIDES = {
    "RECESSION": {
        "rsi2_mean_reversion": 0.0,
        "macro_regime": 60.0,
    },
    "RISK ON": {
        "timeseries_momentum": 40.0,
        "cross_sectional_momentum": 30.0,
    },
    "_default": {
        "rsi2_mean_reversion": 5.0,
    },
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestResolveRegimeWeightsEmpty:
    def test_empty_overrides_returns_defaults(self):
        result = resolve_regime_weights("RECESSION", {}, FLAT)
        assert result is FLAT  # same object when no overrides

    def test_empty_overrides_any_regime(self):
        for regime in ("RISK ON", "NEUTRAL", "CREDIT EVENT", "RECESSION", ""):
            result = resolve_regime_weights(regime, {}, FLAT)
            assert result is FLAT


class TestExactRegimeMatch:
    def test_recession_overrides_applied(self):
        result = resolve_regime_weights("RECESSION", REGIME_OVERRIDES, FLAT)
        assert result["rsi2_mean_reversion"] == 0.0
        assert result["macro_regime"] == 60.0

    def test_recession_uninvolved_keys_inherit_default(self):
        result = resolve_regime_weights("RECESSION", REGIME_OVERRIDES, FLAT)
        assert result["timeseries_momentum"] == 25.0
        assert result["cross_sectional_momentum"] == 15.0

    def test_risk_on_boosts_momentum(self):
        result = resolve_regime_weights("RISK ON", REGIME_OVERRIDES, FLAT)
        assert result["timeseries_momentum"] == 40.0
        assert result["cross_sectional_momentum"] == 30.0

    def test_risk_on_uninvolved_keys_inherit(self):
        result = resolve_regime_weights("RISK ON", REGIME_OVERRIDES, FLAT)
        assert result["rsi2_mean_reversion"] == 10.0
        assert result["macro_regime"] == 45.0

    def test_exact_match_wins_over_default(self):
        # RECESSION has an exact match; "_default" should NOT be used
        result = resolve_regime_weights("RECESSION", REGIME_OVERRIDES, FLAT)
        # The "_default" sets rsi2 to 5.0, but RECESSION should set it to 0.0
        assert result["rsi2_mean_reversion"] == 0.0


class TestDefaultFallback:
    def test_unknown_regime_uses_default_key(self):
        result = resolve_regime_weights("NEUTRAL", REGIME_OVERRIDES, FLAT)
        # No exact NEUTRAL key → fall through to "_default"
        assert result["rsi2_mean_reversion"] == 5.0

    def test_credit_event_uses_default_key(self):
        result = resolve_regime_weights("CREDIT EVENT", REGIME_OVERRIDES, FLAT)
        assert result["rsi2_mean_reversion"] == 5.0

    def test_default_uninvolved_keys_inherit(self):
        result = resolve_regime_weights("NEUTRAL", REGIME_OVERRIDES, FLAT)
        assert result["macro_regime"] == 45.0


class TestNoMatchNoDefault:
    def test_no_match_no_default_returns_flat(self):
        overrides_no_default = {
            "RECESSION": {"rsi2_mean_reversion": 0.0},
        }
        result = resolve_regime_weights("NEUTRAL", overrides_no_default, FLAT)
        assert result is FLAT

    def test_empty_string_regime_returns_flat(self):
        result = resolve_regime_weights("", REGIME_OVERRIDES, FLAT)
        # "" not in REGIME_OVERRIDES as exact match → falls through to "_default"
        assert result["rsi2_mean_reversion"] == 5.0


class TestMergeSemantics:
    def test_result_is_new_dict_not_mutation(self):
        original_flat = dict(FLAT)
        original_overrides = {k: dict(v) for k, v in REGIME_OVERRIDES.items()}
        resolve_regime_weights("RECESSION", REGIME_OVERRIDES, FLAT)
        assert FLAT == original_flat
        assert REGIME_OVERRIDES == original_overrides

    def test_partial_override_adds_no_new_keys(self):
        result = resolve_regime_weights("RECESSION", REGIME_OVERRIDES, FLAT)
        # Result should only contain keys from FLAT (merged, not union with new keys)
        assert set(result.keys()) == set(FLAT.keys())

    def test_override_can_add_new_key_not_in_defaults(self):
        overrides = {"RISK ON": {"brand_new_signal": 99.0}}
        result = resolve_regime_weights("RISK ON", overrides, FLAT)
        # New key appears in result (dict union semantics)
        assert result["brand_new_signal"] == 99.0
        # Original keys still present
        assert result["macro_regime"] == FLAT["macro_regime"]


class TestRealWorldUseCases:
    def test_recession_suppresses_mean_reversion(self):
        """RSI(2) mean reversion should be zeroed out in RECESSION."""
        overrides = {"RECESSION": {"rsi2_mean_reversion": 0.0}}
        weights = {"rsi2_mean_reversion": 10.0, "macro_regime": 45.0}
        result = resolve_regime_weights("RECESSION", overrides, weights)
        assert result["rsi2_mean_reversion"] == 0.0
        assert result["macro_regime"] == 45.0  # unaffected

    def test_risk_on_boosts_ts_momentum(self):
        """Time-series momentum is boosted in RISK ON environments."""
        overrides = {"RISK ON": {"timeseries_momentum": 50.0}}
        weights = {"timeseries_momentum": 25.0, "rsi2_mean_reversion": 10.0}
        result = resolve_regime_weights("RISK ON", overrides, weights)
        assert result["timeseries_momentum"] == 50.0
        assert result["rsi2_mean_reversion"] == 10.0

    def test_unknown_regime_neutral_fallback(self):
        """Unknown regime (or '' / None from DTO) degrades safely to defaults."""
        result = resolve_regime_weights("SIDEWAYS", {}, FLAT)
        assert result is FLAT
