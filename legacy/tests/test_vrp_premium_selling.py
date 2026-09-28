"""
Tests for signals/vrp_premium_selling.py -- the VRP options-premium-selling
regime-gate signal module. Covers score-range invariants, all four gate
branches (met / IVR too low / VRP too low / data missing), the macro-level
is_active_in_regime suppression, and the mandatory no-lookahead-bias
perturbation test (this module is purely row-wise -- True_IVR/VRP are
independent per-row inputs with no rolling window or cross-row state, and
compute_vectorized() never touches `context`, so context=None is safe here,
unlike the AttributeError trap tests/test_signals_lookahead.py documents for
context-dependent modules).
"""

import numpy as np
import pandas as pd
import pytest

from dto_models import MacroEconomicDTO
from signals.vrp_premium_selling import (
    IVR_SELL_THRESHOLD,
    VRP_MIN_THRESHOLD,
    VIX_MAX_THRESHOLD,
    VRPPremiumSellingSignal,
)
from tests.lookahead_check import verify_no_lookahead


def _make_macro(vix: float = 15.0, regime: str = "RISK ON") -> MacroEconomicDTO:
    """Minimal real MacroEconomicDTO -- not a mock -- with the two fields
    this module's is_active_in_regime() reads. Uses YC/OAS/Sahm values that
    keep the rules-based regime at the caller-requested value for any
    regime other than the ones this module cares about."""
    if regime == "CREDIT EVENT":
        return MacroEconomicDTO(
            yield_curve_10y_2y=1.0, high_yield_oas=7.0,
            inflation_rate=2.0, sahm_rule_indicator=0.0, vix_value=vix,
        )
    return MacroEconomicDTO(
        yield_curve_10y_2y=1.0, high_yield_oas=2.0,
        inflation_rate=2.0, sahm_rule_indicator=0.0, vix_value=vix,
    )


class TestScoreRange:
    def test_score_always_in_zero_one(self):
        signal = VRPPremiumSellingSignal()
        df = pd.DataFrame({
            "True_IVR": [0.0, 50.0, 60.0, 100.0, np.nan, 999.0],
            "VRP": [0.0, 0.02, 0.05, 1.0, 0.05, -5.0],
        })
        out = signal.compute_vectorized(df, None)
        assert (out["score"] >= 0.0).all()
        assert (out["score"] <= 1.0).all()
        assert (out["confidence"] >= 0.0).all()
        assert (out["confidence"] <= 1.0).all()

    def test_score_saturates_at_extreme_readings(self):
        signal = VRPPremiumSellingSignal()
        row = pd.Series({"True_IVR": 500.0, "VRP": 5.0})
        out = signal.compute(row, None)
        assert out.score == pytest.approx(1.0)
        assert out.confidence == 1.0


class TestGateBranches:
    def test_gate_met_scores_positive(self):
        signal = VRPPremiumSellingSignal()
        row = pd.Series({"True_IVR": 75.0, "VRP": 0.06})
        out = signal.compute(row, None)
        assert out.score > 0.0
        assert out.confidence == 1.0
        assert "favors selling premium" in out.explanation

    def test_ivr_too_low_gates_closed(self):
        signal = VRPPremiumSellingSignal()
        row = pd.Series({"True_IVR": IVR_SELL_THRESHOLD, "VRP": 0.10})
        out = signal.compute(row, None)
        assert out.score == 0.0
        assert out.confidence == 0.0
        assert "True_IVR<=50" in out.explanation

    def test_vrp_too_low_gates_closed(self):
        signal = VRPPremiumSellingSignal()
        row = pd.Series({"True_IVR": 80.0, "VRP": VRP_MIN_THRESHOLD})
        out = signal.compute(row, None)
        assert out.score == 0.0
        assert out.confidence == 0.0
        assert "VRP<=2%" in out.explanation

    def test_both_conditions_fail_names_both_reasons(self):
        signal = VRPPremiumSellingSignal()
        row = pd.Series({"True_IVR": 10.0, "VRP": 0.0})
        out = signal.compute(row, None)
        assert out.score == 0.0
        assert "True_IVR<=50" in out.explanation
        assert "VRP<=2%" in out.explanation

    def test_missing_true_ivr_degrades_honestly(self):
        signal = VRPPremiumSellingSignal()
        row = pd.Series({"True_IVR": np.nan, "VRP": 0.10})
        out = signal.compute(row, None)
        assert out.score == 0.0
        assert out.confidence == 0.0
        assert "not available" in out.explanation

    def test_missing_vrp_degrades_honestly(self):
        signal = VRPPremiumSellingSignal()
        row = pd.Series({"True_IVR": 80.0, "VRP": np.nan})
        out = signal.compute(row, None)
        assert out.score == 0.0
        assert out.confidence == 0.0
        assert "not available" in out.explanation

    def test_columns_entirely_absent_degrades_honestly(self):
        """A dashboard row with neither column at all (e.g.
        OPTIONS_TRUE_IVR_ENABLED off and OptionsAnalysisStep never ran) must
        never raise -- covered separately from the NaN-value case above."""
        signal = VRPPremiumSellingSignal()
        row = pd.Series({"Close": 100.0})
        out = signal.compute(row, None)
        assert out.score == 0.0
        assert out.confidence == 0.0

    def test_compute_vectorized_matches_compute_per_row(self):
        signal = VRPPremiumSellingSignal()
        df = pd.DataFrame({
            "True_IVR": [75.0, 30.0, np.nan, 55.0],
            "VRP": [0.06, 0.06, 0.06, 0.01],
        })
        vec_out = signal.compute_vectorized(df, None)
        for i in range(len(df)):
            scalar_out = signal.compute(df.iloc[i], None)
            assert vec_out["score"].iloc[i] == pytest.approx(scalar_out.score)
            assert vec_out["confidence"].iloc[i] == pytest.approx(scalar_out.confidence)


class TestRegimeGate:
    def test_active_by_default(self):
        signal = VRPPremiumSellingSignal()
        assert signal.is_active_in_regime(_make_macro(vix=15.0, regime="RISK ON")) is True

    def test_suppressed_above_vix_threshold(self):
        signal = VRPPremiumSellingSignal()
        assert signal.is_active_in_regime(_make_macro(vix=VIX_MAX_THRESHOLD + 1.0)) is False

    def test_active_just_below_vix_threshold(self):
        signal = VRPPremiumSellingSignal()
        assert signal.is_active_in_regime(_make_macro(vix=VIX_MAX_THRESHOLD - 0.01)) is True

    def test_suppressed_in_credit_event(self):
        signal = VRPPremiumSellingSignal()
        macro = _make_macro(vix=15.0, regime="CREDIT EVENT")
        assert macro.market_regime == "CREDIT EVENT"  # sanity: fixture reaches the real regime
        assert signal.is_active_in_regime(macro) is False

    def test_none_macro_defaults_active(self):
        signal = VRPPremiumSellingSignal()
        assert signal.is_active_in_regime(None) is True


class TestNoLookaheadBias:
    def test_vrp_premium_selling_lookahead(self):
        dates = pd.date_range("2026-01-01", periods=100)
        rng = np.random.RandomState(7)
        df = pd.DataFrame(
            {
                "True_IVR": rng.uniform(0.0, 100.0, 100),
                "VRP": rng.uniform(-0.05, 0.10, 100),
            },
            index=dates,
        )
        signal = VRPPremiumSellingSignal()

        def func(data, t):
            out = signal.compute_vectorized(data, None)
            return out["score"].iloc[t]

        assert verify_no_lookahead(func, df, 50)
