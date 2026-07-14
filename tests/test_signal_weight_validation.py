"""
tests/test_signal_weight_validation.py
=======================================
Task B4 — Signal-weight & regime-config validation tests.

Covers:
  * ``validate_signal_weight_config()`` flags an out-of-bounds
    ``SIGNAL_WEIGHTS`` entry (negative and absurdly-large) with a WARNING
    and reports it in the returned violations list.
  * A mistyped ``REGIME_SIGNAL_WEIGHTS`` key is flagged (not silently
    defaulted without any signal).
  * A valid config passes cleanly with an empty violations list and no
    warnings logged.
  * ``resolve_regime_weights()`` now logs (rather than silently no-ops) when
    a recognized regime has no matching override and no ``_default``.
  * ``SignalAggregator.__init__`` triggers validation (memoized) without
    raising, and re-validates when ``force=True``.
  * ``CANONICAL_REGIMES`` is cross-checked against ``dto_models.py``'s
    ``MacroEconomicDTO.market_regime`` by actually constructing a DTO for
    each of the four regime-triggering scenarios and asserting the
    produced string is a ``CANONICAL_REGIMES`` member -- not just
    comparing two hardcoded literal sets (which existing coverage
    elsewhere effectively did, since both sides were derived from the
    same string constants). Mirrors ``Gravity AI Review Suite.py``'s
    step_72 check 8, found (2026-07-14 test-coverage re-audit, Phase 5)
    to have no independent pytest equivalent.
"""

import logging

import pytest

import signals.aggregator as aggregator_mod
from dto_models import MacroEconomicDTO
from signals.aggregator import (
    CANONICAL_REGIMES,
    MAX_SANE_SIGNAL_WEIGHT,
    SignalAggregator,
    resolve_regime_weights,
    validate_signal_weight_config,
)
from signals.registry import SignalRegistry


@pytest.fixture(autouse=True)
def _reset_memoization():
    """Every test gets a clean memoization slate so ordering never matters."""
    aggregator_mod._signal_weight_config_validated = False
    yield
    aggregator_mod._signal_weight_config_validated = False


# =============================================================================
# Weight bounds
# =============================================================================

class TestWeightBounds:
    def test_negative_weight_is_flagged(self, caplog):
        weights = {"macro_regime": 45.0, "bad_module": -5.0}
        with caplog.at_level(logging.WARNING):
            violations = validate_signal_weight_config(weights, {}, force=True)
        assert any("bad_module" in v and "negative" in v for v in violations)
        assert any("bad_module" in rec.message for rec in caplog.records)

    def test_absurdly_large_weight_is_flagged(self, caplog):
        weights = {"macro_regime": 45.0, "runaway_module": 1000.0}
        with caplog.at_level(logging.WARNING):
            violations = validate_signal_weight_config(weights, {}, force=True)
        assert any("runaway_module" in v and "exceeds" in v for v in violations)
        assert any("runaway_module" in rec.message for rec in caplog.records)

    def test_weight_exactly_at_bound_is_not_flagged(self):
        weights = {"macro_regime": MAX_SANE_SIGNAL_WEIGHT}
        violations = validate_signal_weight_config(weights, {}, force=True)
        assert violations == []

    def test_zero_weight_is_valid(self):
        """Zero is a legitimate, intentional weight (e.g. regime_multiplier)."""
        weights = {"regime_multiplier": 0.0}
        violations = validate_signal_weight_config(weights, {}, force=True)
        assert violations == []

    def test_custom_max_weight_bound_respected(self):
        weights = {"macro_regime": 50.0}
        violations = validate_signal_weight_config(weights, {}, max_weight=10.0, force=True)
        assert any("macro_regime" in v for v in violations)

    def test_non_numeric_weight_is_flagged_not_raised(self):
        weights = {"macro_regime": "not_a_number"}
        violations = validate_signal_weight_config(weights, {}, force=True)
        assert any("macro_regime" in v and "not numeric" in v for v in violations)


# =============================================================================
# Regime key validity
# =============================================================================

class TestRegimeKeyValidity:
    def test_mistyped_regime_key_is_flagged(self, caplog):
        regime_weights = {"RISK-ON": {"timeseries_momentum": 40.0}}  # should be "RISK ON"
        with caplog.at_level(logging.WARNING):
            violations = validate_signal_weight_config({}, regime_weights, force=True)
        assert any("RISK-ON" in v for v in violations)
        assert any("RISK-ON" in rec.message for rec in caplog.records)

    def test_all_canonical_regime_keys_pass(self):
        regime_weights = {regime: {} for regime in CANONICAL_REGIMES}
        violations = validate_signal_weight_config({}, regime_weights, force=True)
        assert violations == []

    def test_default_catch_all_key_is_not_flagged(self):
        regime_weights = {"_default": {"rsi2_mean_reversion": 5.0}}
        violations = validate_signal_weight_config({}, regime_weights, force=True)
        assert violations == []

    def test_empty_regime_weights_produces_no_violations(self):
        violations = validate_signal_weight_config({"macro_regime": 45.0}, {}, force=True)
        assert violations == []

    def test_multiple_mistyped_keys_all_flagged(self):
        regime_weights = {
            "risk on": {},   # wrong case
            "Recession": {},  # wrong case
            "CREDIT_EVENT": {},  # wrong separator
        }
        violations = validate_signal_weight_config({}, regime_weights, force=True)
        assert len(violations) == 3


# =============================================================================
# Clean config passes with no warnings
# =============================================================================

class TestCleanConfigPasses:
    def test_valid_config_produces_no_violations_and_no_warnings(self, caplog):
        weights = {"macro_regime": 45.0, "edge_garch": 35.0, "regime_multiplier": 0.0}
        regime_weights = {
            "RISK ON": {"timeseries_momentum": 40.0},
            "RECESSION": {"rsi2_mean_reversion": 0.0},
            "_default": {},
        }
        with caplog.at_level(logging.WARNING):
            violations = validate_signal_weight_config(weights, regime_weights, force=True)
        assert violations == []
        assert not any(
            rec.levelno >= logging.WARNING and "validate_signal_weight_config" in rec.name
            for rec in caplog.records
        )

    def test_project_default_settings_signal_weights_pass(self):
        """The project's own settings.SIGNAL_WEIGHTS default must be clean —
        this is a regression guard against a future default weight creeping
        out of bounds."""
        from settings import settings
        violations = validate_signal_weight_config(settings.SIGNAL_WEIGHTS, {}, force=True)
        assert violations == []

    def test_project_default_regime_weights_pass(self):
        from settings import settings
        violations = validate_signal_weight_config({}, settings.REGIME_SIGNAL_WEIGHTS, force=True)
        assert violations == []


# =============================================================================
# Memoization
# =============================================================================

class TestMemoization:
    def test_second_call_without_force_is_a_noop(self):
        bad_weights = {"bad": -1.0}
        first = validate_signal_weight_config(bad_weights, {}, force=True)
        assert first != []
        second = validate_signal_weight_config(bad_weights, {})  # no force this time
        assert second == []  # memoized — does not re-run

    def test_force_true_always_reruns(self):
        bad_weights = {"bad": -1.0}
        validate_signal_weight_config(bad_weights, {}, force=True)
        again = validate_signal_weight_config(bad_weights, {}, force=True)
        assert again != []


# =============================================================================
# resolve_regime_weights() non-silent fallback
# =============================================================================

class TestResolveRegimeWeightsLogsOnFallback:
    def test_recognized_regime_with_no_override_logs_warning(self, caplog):
        flat = {"macro_regime": 45.0}
        overrides = {"RECESSION": {"macro_regime": 60.0}}  # no NEUTRAL entry, no _default
        with caplog.at_level(logging.WARNING):
            result = resolve_regime_weights("NEUTRAL", overrides, flat)
        assert result is flat
        assert any("NEUTRAL" in rec.message for rec in caplog.records)

    def test_default_catch_all_present_does_not_log(self, caplog):
        flat = {"macro_regime": 45.0}
        overrides = {"RECESSION": {"macro_regime": 60.0}, "_default": {}}
        with caplog.at_level(logging.WARNING):
            resolve_regime_weights("NEUTRAL", overrides, flat)
        assert not any("resolve_regime_weights" in rec.name and rec.levelno >= logging.WARNING
                        for rec in caplog.records)

    def test_unrecognized_regime_string_does_not_log(self, caplog):
        """An empty/garbage market_regime (e.g. during startup) is not itself
        flagged by resolve_regime_weights — that's what validate_signal_weight_config
        checks on the override dict's own keys, independent of any live regime."""
        flat = {"macro_regime": 45.0}
        overrides = {"RECESSION": {"macro_regime": 60.0}}
        with caplog.at_level(logging.WARNING):
            resolve_regime_weights("", overrides, flat)
        assert not any("resolve_regime_weights" in rec.name and rec.levelno >= logging.WARNING
                        for rec in caplog.records)


# =============================================================================
# SignalAggregator wiring
# =============================================================================

class TestSignalAggregatorWiring:
    def test_construction_never_raises_on_bad_config(self):
        registry = SignalRegistry()
        # Should not raise even with an out-of-bounds weight.
        SignalAggregator(registry, weights={"bad": -5.0})

    def test_construction_triggers_validation_warning(self, caplog):
        registry = SignalRegistry()
        with caplog.at_level(logging.WARNING):
            SignalAggregator(registry, weights={"bad_module": 5000.0})
        assert any("bad_module" in rec.message for rec in caplog.records)

    def test_construction_with_clean_weights_produces_no_warning(self, caplog):
        registry = SignalRegistry()
        with caplog.at_level(logging.WARNING):
            SignalAggregator(registry, weights={"macro_regime": 45.0})
        assert not any(
            "validate_signal_weight_config" in rec.message for rec in caplog.records
        )


class TestCanonicalRegimesMatchMacroEconomicDTO:
    """Every regime string dto_models.MacroEconomicDTO.market_regime can
    actually produce must be a CANONICAL_REGIMES member -- verified here by
    constructing a real DTO for each triggering scenario, not by comparing
    two hardcoded literal sets. hmm_risk_on_probability is left at its
    default (None) throughout so market_regime equals the undamped
    rules-based classification (see MacroEconomicDTO._rules_based_regime's
    docstring)."""

    def _dto(self, yield_curve, credit_spread, sahm=0.0):
        return MacroEconomicDTO(
            yield_curve_10y_2y=yield_curve,
            high_yield_oas=credit_spread,
            inflation_rate=2.0,
            sahm_rule_indicator=sahm,
        )

    def test_recession_via_inverted_curve_and_wide_spread(self):
        dto = self._dto(yield_curve=-0.5, credit_spread=7.0)
        assert dto.market_regime == "RECESSION"
        assert dto.market_regime in CANONICAL_REGIMES

    def test_recession_via_sahm_rule(self):
        dto = self._dto(yield_curve=0.0, credit_spread=0.0, sahm=0.6)
        assert dto.market_regime == "RECESSION"
        assert dto.market_regime in CANONICAL_REGIMES

    def test_credit_event(self):
        dto = self._dto(yield_curve=0.0, credit_spread=6.5)
        assert dto.market_regime == "CREDIT EVENT"
        assert dto.market_regime in CANONICAL_REGIMES

    def test_neutral(self):
        dto = self._dto(yield_curve=0.0, credit_spread=5.0)
        assert dto.market_regime == "NEUTRAL"
        assert dto.market_regime in CANONICAL_REGIMES

    def test_risk_on(self):
        dto = self._dto(yield_curve=0.5, credit_spread=2.0)
        assert dto.market_regime == "RISK ON"
        assert dto.market_regime in CANONICAL_REGIMES
