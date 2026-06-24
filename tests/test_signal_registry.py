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
