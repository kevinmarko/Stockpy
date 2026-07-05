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
import pytest

from data_engine import MockDataEngine
from dto_models import MacroEconomicDTO
from macro_engine import MacroEngine


@pytest.fixture(autouse=True)
def _disable_historical_store(disable_historical_store):
    """settings.HISTORICAL_STORE_ENABLED defaults True, which would route
    MacroEngine.compute_hmm_risk_on_probability()'s macro-history reads
    through the real, on-disk HistoricalStore (see the Tier 2.3 Phase 3
    wiring note in macro_engine.py) instead of DataEngine.fetch_macro_history()
    directly. Confirmed by direct execution -- NOT just the "succeeds" test
    below, but also the "insufficient rows" graceful-degradation test --
    that this write happens before the eventual None short-circuit, so it
    is disabled file-wide via this autouse shim (wrapping the shared
    tests/conftest.py fixture) rather than per-test. The DTO-only tests in
    sections 1/2 above do no I/O and are unaffected by this setting."""
    yield


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


# =============================================================================
# 4. Task A4 -- per-process MacroEngine/HMMRegimeDetector reuse
# =============================================================================
# main.py's --interval / agent-loop mode calls run_once() -> _build_macro_dto()
# repeatedly WITHIN THE SAME PROCESS. The HMMRegimeDetector.fit() retrain gate
# (retrain_freq_days) is only meaningful if the SAME detector instance
# persists across those cycles -- a fresh MacroEngine (and therefore a fresh,
# never-fitted HMMRegimeDetector) every cycle makes the gate a no-op. These
# tests simulate two consecutive cycles by calling
# compute_hmm_risk_on_probability() TWICE on the SAME MacroEngine instance
# (mirroring main.py's module-level _get_macro_engine() singleton) and assert
# the underlying HMM does NOT refit on the second call.

def test_same_macro_engine_instance_does_not_refit_on_second_cycle():
    """Two consecutive compute_hmm_risk_on_probability() calls on the SAME
    MacroEngine (simulating two --interval cycles within one process) must
    NOT trigger a second real HMM fit -- last_fit_date must be unchanged and
    the model parameters must be identical."""
    mde = MockDataEngine()
    me = MacroEngine(data_engine=mde)
    macro_hist = mde.fetch_macro_history()

    rng = np.random.RandomState(7)
    n = len(macro_hist)
    prices = 400 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, n)))
    spy_df = pd.DataFrame({"Close": prices}, index=macro_hist.index)

    # Cycle 1
    result_1 = me.compute_hmm_risk_on_probability(spy_df)
    assert result_1 is not None
    fit_date_after_cycle_1 = me._hmm_detector.last_fit_date
    means_after_cycle_1 = me._hmm_detector.feature_means_.copy()
    assert fit_date_after_cycle_1 is not None

    # Cycle 2 -- SAME MacroEngine instance, SAME (or a slightly refreshed)
    # SPY frame, well within the default 7-day retrain_freq_days window.
    result_2 = me.compute_hmm_risk_on_probability(spy_df)
    assert result_2 is not None
    fit_date_after_cycle_2 = me._hmm_detector.last_fit_date
    means_after_cycle_2 = me._hmm_detector.feature_means_

    assert fit_date_after_cycle_2 == fit_date_after_cycle_1, (
        "HMMRegimeDetector refit on the second cycle despite being called on "
        "the SAME MacroEngine instance within the retrain_freq_days window -- "
        "the retrain gate is only meaningful if the detector persists across "
        "cycles (see main.py's _get_macro_engine() singleton)."
    )
    np.testing.assert_array_equal(means_after_cycle_1, means_after_cycle_2)
    # Second call's probability must be byte-identical (same model, same data).
    assert result_1 == result_2


def test_fresh_macro_engine_per_cycle_would_refit_every_time():
    """Contrast case: constructing a NEW MacroEngine per cycle (the bug this
    task fixes) gives each cycle a never-fitted HMMRegimeDetector, so both
    "cycles" perform a real fit (last_fit_date is set fresh both times,
    rather than being carried over). This documents why reuse (not
    reconstruction) is required to make the retrain gate meaningful."""
    mde = MockDataEngine()
    macro_hist = mde.fetch_macro_history()
    rng = np.random.RandomState(7)
    n = len(macro_hist)
    prices = 400 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, n)))
    spy_df = pd.DataFrame({"Close": prices}, index=macro_hist.index)

    me_cycle_1 = MacroEngine(data_engine=mde)
    me_cycle_1.compute_hmm_risk_on_probability(spy_df)
    assert me_cycle_1._hmm_detector.last_fit_date is not None

    me_cycle_2 = MacroEngine(data_engine=mde)  # fresh instance == the bug
    assert me_cycle_2._hmm_detector.last_fit_date is None, (
        "a fresh MacroEngine's HMMRegimeDetector must start unfit -- this is "
        "exactly why per-cycle reconstruction defeats the retrain gate."
    )
    me_cycle_2.compute_hmm_risk_on_probability(spy_df)
    assert me_cycle_2._hmm_detector.last_fit_date is not None


def test_hmm_n_states_and_retrain_freq_days_read_from_settings(monkeypatch):
    """MacroEngine must construct its HMMRegimeDetector from
    settings.HMM_N_STATES / settings.HMM_RETRAIN_FREQ_DAYS rather than
    hardcoded literals, so an operator can tune them via .env."""
    from settings import settings as _settings

    monkeypatch.setattr(_settings, "HMM_N_STATES", 4)
    monkeypatch.setattr(_settings, "HMM_RETRAIN_FREQ_DAYS", 14)

    mde = MockDataEngine()
    me = MacroEngine(data_engine=mde)

    assert me._hmm_detector.n_states == 4
    assert me._hmm_detector.retrain_freq_days == 14
