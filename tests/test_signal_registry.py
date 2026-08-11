"""
InvestYo Quant Platform - Signal Registry Unit Tests
===================================================
Tests registration, retrieval, and execution in SignalRegistry.
"""

import pytest  # type: ignore
import pandas as pd
from signals.base import SignalModule, SignalContext, SignalOutput
from signals.registry import SignalRegistry
from dto_models import MarketBarDTO, FundamentalDataDTO, MacroEconomicDTO
from datetime import datetime


class MockFeatureSignal(SignalModule):
    name = "mock_feature"
    required_features = ["test_feature_1", "test_feature_2"]

    def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
        val1 = row["test_feature_1"]
        val2 = row["test_feature_2"]
        score = (val1 + val2) / 2.0
        return SignalOutput(score=score, confidence=0.8, explanation="Mock computed")


def test_signal_registry_roundtrip():
    registry = SignalRegistry()
    module = MockFeatureSignal()
    
    # 1. Register
    registry.register(module)
    
    # 2. Get and check name
    retrieved = registry.get("mock_feature")
    assert retrieved == module
    assert retrieved.name == "mock_feature"
    assert "mock_feature" in registry.get_all()

    # 3. Compute with valid features
    row = pd.Series({"test_feature_1": 0.5, "test_feature_2": -0.1})
    bar = MarketBarDTO(datetime.now(), "MOCK", 10.0, 10.0, 10.0, 10.0, 100)
    fundamentals = FundamentalDataDTO(
        ticker="MOCK", company_name="Mock Corp", sector="Technology",
        pe_ratio=15.0, pb_ratio=1.5, book_value=100.0, eps_trailing=5.0,
        dividend_yield=0.02, dividend_growth_rate=0.05, payout_ratio=0.30
    )
    macro = MacroEconomicDTO(0.1, 2.0, 1.0, 4.0)
    context = SignalContext(bar=bar, fundamentals=fundamentals, macro=macro)
    
    outputs = registry.compute_all(row, context)
    assert "mock_feature" in outputs
    assert outputs["mock_feature"].score == 0.2
    assert outputs["mock_feature"].confidence == 0.8
    assert outputs["mock_feature"].explanation == "Mock computed"


def test_signal_registry_missing_features():
    registry = SignalRegistry()
    module = MockFeatureSignal()
    registry.register(module)
    
    # Missing test_feature_2
    row = pd.Series({"test_feature_1": 0.5})
    bar = MarketBarDTO(datetime.now(), "MOCK", 10.0, 10.0, 10.0, 10.0, 100)
    fundamentals = FundamentalDataDTO(
        ticker="MOCK", company_name="Mock Corp", sector="Technology",
        pe_ratio=15.0, pb_ratio=1.5, book_value=100.0, eps_trailing=5.0,
        dividend_yield=0.02, dividend_growth_rate=0.05, payout_ratio=0.30
    )
    macro = MacroEconomicDTO(0.1, 2.0, 1.0, 4.0)
    context = SignalContext(bar=bar, fundamentals=fundamentals, macro=macro)
    
    with pytest.raises(ValueError, match="Required feature 'test_feature_2' for signal 'mock_feature' is missing"):
        registry.compute_all(row, context)


def test_signal_registry_invalid_registration():
    registry = SignalRegistry()

    class BadSignal(SignalModule):
        name = ""  # Invalid name
        required_features = []
        def compute(self, row, context):
            return SignalOutput(0.0, 0.0, "")

    with pytest.raises(ValueError, match="Signal module must have a non-empty 'name'"):
        registry.register(BadSignal())


# ============================================================================
# Finding 13 -- registration collision guard
# ============================================================================

class _DuplicateNameSignalA(SignalModule):
    name = "duplicate_name"
    required_features: list = []

    def compute(self, row, context):
        return SignalOutput(0.1, 0.5, "A")


class _DuplicateNameSignalB(SignalModule):
    """A DIFFERENT class that happens to share the same ``name`` as
    ``_DuplicateNameSignalA`` -- the realistic collision scenario (e.g. a
    copy-pasted module that forgot to rename itself)."""

    name = "duplicate_name"
    required_features: list = []

    def compute(self, row, context):
        return SignalOutput(-0.9, 0.9, "B")


def test_double_registration_with_different_instance_raises():
    """A second, DIFFERENT module instance registered under a name already
    in use must raise -- previously this silently overwrote the first
    registration with no signal to the caller, violating this codebase's
    'never silently drop, skip, or double-register a module' convention."""
    registry = SignalRegistry()
    registry.register(_DuplicateNameSignalA())

    with pytest.raises(ValueError, match="collision"):
        registry.register(_DuplicateNameSignalB())

    # The FIRST registration must survive untouched -- not silently
    # replaced by the (rejected) second registration attempt.
    retrieved = registry.get("duplicate_name")
    assert isinstance(retrieved, _DuplicateNameSignalA)


def test_registering_the_same_instance_twice_is_a_harmless_noop():
    """Re-registering the EXACT SAME object (e.g. via two import paths that
    resolve to the same singleton) is not a real collision and must not
    raise -- it's the same module, not two modules fighting over one name."""
    registry = SignalRegistry()
    module = _DuplicateNameSignalA()
    registry.register(module)
    registry.register(module)  # must not raise
    assert registry.get("duplicate_name") is module


def test_all_real_signal_modules_register_without_collision():
    """Exercises the FULL real signal-loading path (signals/__init__.py's
    _register_all(), which every one of the platform's registered
    SignalModule implementations goes through at import time) rather than a
    synthetic registry -- proving the collision check doesn't false-positive
    against the platform's own real module set."""
    import signals  # noqa: F401 -- triggers signals/__init__.py::_register_all()
    from signals.registry import global_registry as real_global_registry

    all_modules = real_global_registry.get_all()
    # Matches docs/signals/README.md's documented count of registered
    # SignalModule implementations; a regression here (fewer than expected)
    # would indicate a module silently failed to register.
    assert len(all_modules) >= 17
    # Every registered name is unique by construction (a dict can't hold
    # duplicate keys) -- the real assertion is that registration itself
    # never raised while loading the full real module set above.
    assert len(all_modules) == len(set(all_modules.keys()))
