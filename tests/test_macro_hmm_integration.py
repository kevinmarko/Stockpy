"""
InvestYo Quant Platform - HMM/Rules-Based Macro Integration Tests
======================================================================
Unit tests for:
1. MacroEconomicDTO.market_regime's HMM disagreement downgrade (RISK ON ->
   NEUTRAL when HMM risk_on_probability < 0.3).
2. MacroEconomicDTO.killSwitch's HMM agreement fast-trigger (lowered
   thresholds when rules say RECESSION and HMM agrees risk_off > 0.7).
3. MacroEngine.compute_hmm_risk_on_probability's graceful degradation to
   None (never fabricated) when SPY history or macro history is unavailable.
"""

import numpy as np
import pandas as pd

from data_engine import MockDataEngine
from dto_models import MacroEconomicDTO
from macro_engine import MacroEngine


# =============================================================================
# 1. market_regime HMM disagreement downgrade
# =============================================================================
def test_no_hmm_input_behaves_identically_to_pre_hmm_baseline():
    """hmm_risk_on_probability=None (default) -- behavior must be byte-for-byte
    identical to before this feature existed."""
    dto = MacroEconomicDTO(yield_curve_10y_2y=0.5, high_yield_oas=2.0, inflation_rate=2.0)
    assert dto.market_regime == "RISK ON"
    assert dto.killSwitch is False


def test_risk_on_downgraded_to_neutral_when_hmm_disagrees():
    dto = MacroEconomicDTO(
        yield_curve_10y_2y=0.5, high_yield_oas=2.0, inflation_rate=2.0,
        hmm_risk_on_probability=0.1,  # < 0.3 threshold
    )
    assert dto.market_regime == "NEUTRAL"


def test_risk_on_confirmed_when_hmm_agrees():
    dto = MacroEconomicDTO(
        yield_curve_10y_2y=0.5, high_yield_oas=2.0, inflation_rate=2.0,
        hmm_risk_on_probability=0.8,  # >= 0.3 threshold
    )
    assert dto.market_regime == "RISK ON"


def test_downgrade_threshold_boundary_is_exclusive_below():
    dto_at_threshold = MacroEconomicDTO(
        yield_curve_10y_2y=0.5, high_yield_oas=2.0, inflation_rate=2.0,
        hmm_risk_on_probability=0.3,
    )
    assert dto_at_threshold.market_regime == "RISK ON"  # 0.3 is NOT < 0.3

    dto_below_threshold = MacroEconomicDTO(
        yield_curve_10y_2y=0.5, high_yield_oas=2.0, inflation_rate=2.0,
        hmm_risk_on_probability=0.2999,
    )
    assert dto_below_threshold.market_regime == "NEUTRAL"


def test_hmm_never_overrides_recession_or_credit_event():
    """HMM downgrade only ever applies to RISK ON -> NEUTRAL -- it must never
    touch an already-worse rules-based regime."""
    recession_dto = MacroEconomicDTO(
        yield_curve_10y_2y=-0.5, high_yield_oas=7.0, inflation_rate=2.0,
        hmm_risk_on_probability=0.9,  # high risk-on -- irrelevant here
    )
    assert recession_dto.market_regime == "RECESSION"

    credit_event_dto = MacroEconomicDTO(
        yield_curve_10y_2y=0.5, high_yield_oas=6.5, inflation_rate=2.0,
        hmm_risk_on_probability=0.9,
    )
    assert credit_event_dto.market_regime == "CREDIT EVENT"


# =============================================================================
# 2. killSwitch HMM agreement fast-trigger
# =============================================================================
def test_killswitch_fires_at_lowered_threshold_when_recession_and_hmm_agree():
    """VIX=27 is between the lowered (25) and base (30) thresholds -- must
    fire ONLY because rules=RECESSION and HMM agrees (risk_off > 0.7)."""
    dto = MacroEconomicDTO(
        yield_curve_10y_2y=-0.5, high_yield_oas=7.0, inflation_rate=2.0,
        vix_value=27.0, hmm_risk_on_probability=0.1,  # risk_off = 0.9 > 0.7
    )
    assert dto.market_regime == "RECESSION"
    assert dto.killSwitch is True


def test_killswitch_does_not_fire_at_same_vix_when_hmm_disagrees():
    """Same VIX=27, same RECESSION regime, but HMM does NOT agree
    (risk_off=0.5, not > 0.7) -- base threshold (30) is not met, so no fire."""
    dto = MacroEconomicDTO(
        yield_curve_10y_2y=-0.5, high_yield_oas=7.0, inflation_rate=2.0,
        vix_value=27.0, hmm_risk_on_probability=0.5,  # risk_off = 0.5, not > 0.7
    )
    assert dto.market_regime == "RECESSION"
    assert dto.killSwitch is False


def test_killswitch_agreement_check_requires_recession_not_just_high_risk_off():
    """A NEUTRAL/CREDIT EVENT regime with a high HMM risk-off probability
    must NOT get the lowered threshold -- the agreement check is gated on
    the rules-based regime actually being RECESSION."""
    dto = MacroEconomicDTO(
        yield_curve_10y_2y=0.5, high_yield_oas=5.0, inflation_rate=2.0,  # NEUTRAL, not RECESSION
        vix_value=27.0, hmm_risk_on_probability=0.05,  # risk_off = 0.95 > 0.7
    )
    assert dto.market_regime == "NEUTRAL"
    assert dto.killSwitch is False  # base thresholds (sahm>=0.5 or vix>30) not met


def test_killswitch_base_condition_unaffected_by_hmm():
    """The base kill-switch condition (sahm>=0.5 or vix>30) must still fire
    regardless of HMM agreement -- the agreement check only ever ADDS
    sensitivity, never removes the base trigger."""
    dto = MacroEconomicDTO(
        yield_curve_10y_2y=0.5, high_yield_oas=2.0, inflation_rate=2.0,
        vix_value=35.0, hmm_risk_on_probability=0.9,  # high risk-on, RISK ON regime
    )
    assert dto.killSwitch is True  # vix > 30 alone is sufficient


# =============================================================================
# 3. MacroEngine.compute_hmm_risk_on_probability graceful degradation
# =============================================================================
def test_compute_hmm_risk_on_probability_none_data_engine():
    me = MacroEngine(data_engine=None)
    result = me.compute_hmm_risk_on_probability(pd.DataFrame({"Close": [100.0] * 50}))
    assert result is None


def test_compute_hmm_risk_on_probability_no_spy_data():
    mde = MockDataEngine()
    me = MacroEngine(data_engine=mde)
    assert me.compute_hmm_risk_on_probability(None) is None
    assert me.compute_hmm_risk_on_probability(pd.DataFrame()) is None


def test_compute_hmm_risk_on_probability_insufficient_rows():
    mde = MockDataEngine()
    me = MacroEngine(data_engine=mde)
    short_spy = pd.DataFrame({"Close": [100.0 + i for i in range(30)]},
                              index=pd.bdate_range(end=pd.Timestamp.now(), periods=30))
    result = me.compute_hmm_risk_on_probability(short_spy)
    assert result is None


def test_compute_hmm_risk_on_probability_succeeds_with_sufficient_aligned_data():
    mde = MockDataEngine()
    me = MacroEngine(data_engine=mde)
    macro_hist = mde.fetch_macro_history()

    rng = np.random.RandomState(2)
    n = len(macro_hist)
    prices = 400 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, n)))
    spy_df = pd.DataFrame({"Close": prices}, index=macro_hist.index)

    result = me.compute_hmm_risk_on_probability(spy_df)
    assert result is not None
    assert 0.0 <= result <= 1.0
