"""
tests/test_disabled_signal_modules.py
======================================
Unit tests for the ``settings.DISABLED_SIGNAL_MODULES`` override wired into
``signals.aggregator.SignalAggregator.aggregate()`` (used by the Command
Center's Strategy Matrix tab).

Invariant under test
--------------------
A module whose name appears in ``settings.DISABLED_SIGNAL_MODULES`` contributes
*nothing* to the aggregate ``final_score`` — exactly like a regime-gated
module — while an empty disabled list reproduces the legacy behavior where the
module's weighted score is added. The module's raw output remains available in
the returned ``outputs`` dict for introspection.
"""

from datetime import datetime

import pandas as pd
import pytest

from signals.base import SignalModule, SignalContext, SignalOutput
from signals.registry import SignalRegistry
from signals.aggregator import SignalAggregator
from dto_models import MarketBarDTO, FundamentalDataDTO, MacroEconomicDTO
from settings import settings


class _AlwaysPositiveSignal(SignalModule):
    """Trivial module that always returns the maximum +1.0 score."""

    name = "always_positive"
    required_features: list[str] = []

    def is_active_in_regime(self, macro: MacroEconomicDTO) -> bool:
        return True

    def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
        return SignalOutput(
            score=1.0,
            confidence=1.0,
            explanation="always_positive fired",
            meta_label_proba=1.0,
        )


def _make_context() -> SignalContext:
    bar = MarketBarDTO(datetime.now(), "TEST", 100.0, 100.0, 100.0, 100.0, 1000)
    fund = FundamentalDataDTO(
        ticker="TEST", pe_ratio=None, pb_ratio=None, dividend_yield=0.0,
        book_value=0.0, eps_trailing=0.0, dividend_growth_rate=0.0,
        payout_ratio=0.0, sector="Unknown", company_name="Unknown",
    )
    macro = MacroEconomicDTO(
        yield_curve_10y_2y=0.5, high_yield_oas=2.0, inflation_rate=0.03,
        vix_value=15.0, hmm_risk_on_probability=None,
    )
    return SignalContext(bar=bar, fundamentals=fund, macro=macro)


@pytest.fixture()
def _registry_and_aggregator():
    registry = SignalRegistry()
    registry.register(_AlwaysPositiveSignal())
    aggregator = SignalAggregator(registry, weights={"always_positive": 20.0})
    return registry, aggregator


def test_enabled_module_contributes_to_score(_registry_and_aggregator, monkeypatch):
    """With an empty disabled list, score = base(50) + score(1.0)*weight(20)."""
    monkeypatch.setattr(settings, "DISABLED_SIGNAL_MODULES", [])
    _registry, aggregator = _registry_and_aggregator
    final_score, _log, _warn, _detail, outputs, _meta = aggregator.aggregate(
        pd.Series({"Symbol": "TEST"}), _make_context()
    )
    assert final_score == pytest.approx(70.0)  # 50 + 1.0 * 20
    assert "always_positive" in outputs


def test_disabled_module_contributes_nothing(_registry_and_aggregator, monkeypatch):
    """When the module is disabled, its weighted contribution is dropped."""
    monkeypatch.setattr(settings, "DISABLED_SIGNAL_MODULES", ["always_positive"])
    _registry, aggregator = _registry_and_aggregator
    final_score, _log, _warn, _detail, outputs, _meta = aggregator.aggregate(
        pd.Series({"Symbol": "TEST"}), _make_context()
    )
    assert final_score == pytest.approx(50.0)  # neutral base, unchanged
    # Raw output is still computed and available for introspection.
    assert "always_positive" in outputs
    assert outputs["always_positive"].score == 1.0


def test_disabled_module_omits_explanation(_registry_and_aggregator, monkeypatch):
    """A disabled module's explanation lines are not surfaced in score_log."""
    monkeypatch.setattr(settings, "DISABLED_SIGNAL_MODULES", ["always_positive"])
    _registry, aggregator = _registry_and_aggregator
    _score, score_log, _warn, _detail, _outputs, _meta = aggregator.aggregate(
        pd.Series({"Symbol": "TEST"}), _make_context()
    )
    assert all("always_positive fired" not in line for line in score_log)


def test_default_disabled_list_is_empty():
    """Default-constructed Settings has no modules disabled (legacy behavior)."""
    from settings import Settings

    assert Settings().DISABLED_SIGNAL_MODULES == []
