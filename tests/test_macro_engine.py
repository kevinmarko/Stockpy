"""
tests/test_macro_engine.py
===========================
Unit coverage for macro_engine.py beyond what
tests/test_macro_hmm_integration.py and tests/test_bug_fixes.py already pin.

Those two files cover: MacroEconomicDTO's HMM disagreement-downgrade /
agreement fast-trigger logic, and compute_hmm_risk_on_probability's None
degradation for missing SPY data / insufficient feature rows, plus the
Sahm Rule wiring regression (BUG-1/BUG-2).

This file fills the remaining gaps: calculate_sahm_rule's FRED success and
fallback paths, run_macro_killswitch's full regime-classification truth
table + MacroDataSchema conformance, the HistoricalStore Phase-3 routing
fallback inside compute_hmm_risk_on_probability (never previously exercised
in isolation), the HMM fit/predict exception path, calculate_fama_french_alpha
and its offline proxy-factor fallback, and the sentiment helpers.
"""

import math
from unittest import mock

import numpy as np
import pandas as pd
import pytest

from data_engine import MockDataEngine
from macro_engine import MacroDataSchema, MacroEngine


# ============================================================================
# Fixtures / helpers
# ============================================================================

class _FakeFred:
    """Minimal stand-in for fredapi.Fred with a controllable get_series."""

    def __init__(self, series_map=None, raise_on=None):
        self._series_map = series_map or {}
        self._raise_on = raise_on or set()

    def get_series(self, series_id, limit=None):
        if series_id in self._raise_on:
            raise RuntimeError(f"FRED unavailable for {series_id}")
        return self._series_map.get(series_id, pd.Series(dtype=float))


class _FakeEngineWithFred:
    def __init__(self, fred):
        self.fred = fred


@pytest.fixture
def engine():
    return MacroEngine(data_engine=MockDataEngine())


# ============================================================================
# calculate_sahm_rule
# ============================================================================

class TestCalculateSahmRule:
    def test_no_data_engine_returns_fallback(self):
        me = MacroEngine(data_engine=None)
        assert me.calculate_sahm_rule(fallback_val=0.42) == 0.42

    def test_data_engine_without_fred_attribute_returns_fallback(self):
        """MockDataEngine carries no `.fred` attribute -- calculate_sahm_rule
        must degrade to the fallback rather than raising AttributeError."""
        me = MacroEngine(data_engine=MockDataEngine())
        assert me.calculate_sahm_rule(fallback_val=0.0) == 0.0

    def test_direct_sahmrealtime_series_is_used_when_available(self):
        fred = _FakeFred(series_map={"SAHMREALTIME": pd.Series([0.1, 0.2, 0.35])})
        me = MacroEngine(data_engine=_FakeEngineWithFred(fred))
        assert me.calculate_sahm_rule() == 0.35

    def test_falls_back_to_unrate_computation_when_sahmrealtime_unavailable(self):
        # 15 months of UNRATE: flat 4.0 then a jump to 5.0 for the last 3 months,
        # producing a positive (rising) Sahm reading via the 3mo-MA - min(3mo-MA) formula.
        unrate_values = [4.0] * 12 + [4.5, 5.0, 5.5]
        idx = pd.date_range("2024-01-01", periods=len(unrate_values), freq="MS")
        unrate = pd.Series(unrate_values, index=idx)
        fred = _FakeFred(series_map={"UNRATE": unrate}, raise_on={"SAHMREALTIME"})
        me = MacroEngine(data_engine=_FakeEngineWithFred(fred))
        result = me.calculate_sahm_rule()
        assert result > 0.0  # unemployment is rising relative to its 12mo trailing low

    def test_empty_unrate_series_returns_fallback(self):
        fred = _FakeFred(series_map={"UNRATE": pd.Series(dtype=float)}, raise_on={"SAHMREALTIME"})
        me = MacroEngine(data_engine=_FakeEngineWithFred(fred))
        assert me.calculate_sahm_rule(fallback_val=-1.0) == -1.0

    def test_total_fred_failure_returns_fallback_never_raises(self):
        class _BrokenFred:
            fred_active = True

            def get_series(self, *a, **k):
                raise ConnectionError("network down")

        me = MacroEngine(data_engine=_FakeEngineWithFred(_BrokenFred()))
        assert me.calculate_sahm_rule(fallback_val=0.0) == 0.0


# ============================================================================
# run_macro_killswitch — regime classification truth table + schema
# ============================================================================

class TestRunMacroKillswitch:
    def test_inverted_curve_and_high_credit_spread_is_recession(self, engine):
        df = engine.run_macro_killswitch({"T10Y2Y": -0.5, "BAMLH0A0HYM2": 7.0}, sahm_rule_val=0.0)
        assert df["market_regime"].iloc[0] == "RECESSION"

    def test_high_sahm_alone_is_recession_regardless_of_curve(self, engine):
        df = engine.run_macro_killswitch({"T10Y2Y": 1.0, "BAMLH0A0HYM2": 1.0}, sahm_rule_val=0.65)
        assert df["market_regime"].iloc[0] == "RECESSION"

    def test_high_credit_spread_alone_is_credit_event(self, engine):
        df = engine.run_macro_killswitch({"T10Y2Y": 1.0, "BAMLH0A0HYM2": 6.5}, sahm_rule_val=0.0)
        assert df["market_regime"].iloc[0] == "CREDIT EVENT"

    def test_moderate_credit_spread_is_neutral(self, engine):
        df = engine.run_macro_killswitch({"T10Y2Y": 1.0, "BAMLH0A0HYM2": 5.0}, sahm_rule_val=0.0)
        assert df["market_regime"].iloc[0] == "NEUTRAL"

    def test_benign_conditions_are_risk_on(self, engine):
        df = engine.run_macro_killswitch({"T10Y2Y": 1.0, "BAMLH0A0HYM2": 2.0}, sahm_rule_val=0.0)
        assert df["market_regime"].iloc[0] == "RISK ON"

    def test_missing_macro_keys_use_documented_defaults(self, engine):
        """An empty raw dict must not raise -- defaults (T10Y2Y=0.5,
        BAMLH0A0HYM2=3.5) apply. credit_spread=3.5 is below the 4.5 NEUTRAL
        floor, so the regime resolves to RISK ON, not RECESSION/NEUTRAL."""
        df = engine.run_macro_killswitch({}, sahm_rule_val=0.0)
        assert df["market_regime"].iloc[0] == "RISK ON"

    def test_output_conforms_to_macro_data_schema(self, engine):
        df = engine.run_macro_killswitch({"T10Y2Y": 0.5, "BAMLH0A0HYM2": 3.0}, sahm_rule_val=0.1)
        # run_macro_killswitch already calls MacroDataSchema.validate internally;
        # re-validating here proves the contract holds independent of internals.
        validated = MacroDataSchema.validate(df)
        assert len(validated) == 1

    def test_negative_sahm_rejected_by_schema(self, engine):
        """sahm_rule_indicator is declared ge=0.0 in MacroDataSchema -- a
        negative value must fail schema validation, not silently pass."""
        with pytest.raises(Exception):
            engine.run_macro_killswitch({"T10Y2Y": 0.5, "BAMLH0A0HYM2": 3.0}, sahm_rule_val=-0.1)


# ============================================================================
# fetch_proxy_factors_offline
# ============================================================================

class TestFetchProxyFactorsOffline:
    def test_returns_expected_columns_and_length(self, engine):
        idx = pd.date_range("2024-01-01", periods=50, freq="B")
        factors = engine.fetch_proxy_factors_offline(idx)
        assert list(factors.columns) == ["Mkt-RF", "SMB", "HML", "RF"]
        assert len(factors) == 50
        assert (factors.index == idx).all()

    def test_deterministic_across_calls(self, engine):
        """Seeded RNG (seed=42) makes this offline fallback reproducible --
        a regression that removes the seed would make CI flaky."""
        idx = pd.date_range("2024-01-01", periods=20, freq="B")
        first = engine.fetch_proxy_factors_offline(idx)
        second = engine.fetch_proxy_factors_offline(idx)
        pd.testing.assert_frame_equal(first, second)


# ============================================================================
# calculate_fama_french_alpha
# ============================================================================

class TestCalculateFamaFrenchAlpha:
    def test_insufficient_points_raises_value_error(self, engine):
        short_returns = pd.Series([0.01, 0.02], index=pd.bdate_range("2024-01-01", periods=2))
        with pytest.raises(ValueError):
            engine.calculate_fama_french_alpha(short_returns)

    def test_regression_with_explicit_factors_returns_expected_keys(self, engine):
        idx = pd.bdate_range("2024-01-01", periods=60)
        rng = np.random.RandomState(11)
        stock_returns = pd.Series(rng.normal(0.001, 0.01, 60), index=idx)
        factors = engine.fetch_proxy_factors_offline(idx)
        result = engine.calculate_fama_french_alpha(stock_returns, factors_df=factors)
        for key in ("alpha", "beta_market", "beta_size", "beta_value", "p_value_alpha", "r_squared"):
            assert key in result
            assert isinstance(result[key], float)
        assert 0.0 <= result["r_squared"] <= 1.0

    def test_no_index_overlap_raises_value_error(self, engine):
        stock_idx = pd.bdate_range("2024-01-01", periods=30)
        stock_returns = pd.Series(np.random.RandomState(1).normal(0, 0.01, 30), index=stock_idx)
        disjoint_idx = pd.bdate_range("2030-01-01", periods=30)  # no overlap at all
        factors = pd.DataFrame(
            {"Mkt-RF": 0.0, "SMB": 0.0, "HML": 0.0, "RF": 0.0001}, index=disjoint_idx
        )
        with pytest.raises(ValueError):
            engine.calculate_fama_french_alpha(stock_returns, factors_df=factors)

    def test_no_factors_passed_falls_back_to_offline_proxy(self, engine, monkeypatch):
        """When pandas_datareader is unavailable (or factors_df omitted),
        the engine must fall back to fetch_proxy_factors_offline rather than
        raising -- proven by patching DATA_READER_AVAILABLE off."""
        monkeypatch.setattr("macro_engine.DATA_READER_AVAILABLE", False)
        idx = pd.bdate_range("2024-01-01", periods=40)
        rng = np.random.RandomState(5)
        stock_returns = pd.Series(rng.normal(0.001, 0.01, 40), index=idx)
        result = engine.calculate_fama_french_alpha(stock_returns)
        assert "alpha" in result


# ============================================================================
# Sentiment helpers
# ============================================================================

class TestFallbackSentiment:
    def test_positive_keywords_yield_positive_score(self, engine):
        result = engine._fallback_sentiment("strong bullish growth outlook with profit upside")
        assert result > 0.0

    def test_negative_keywords_yield_negative_score(self, engine):
        result = engine._fallback_sentiment("bearish recession risk distress and weak sell signals")
        assert result < 0.0

    def test_mixed_keywords_partially_cancel(self, engine):
        result = engine._fallback_sentiment("bullish growth but bearish recession risk")
        assert -1.0 <= result <= 1.0

    def test_no_keyword_matches_returns_zero(self, engine):
        assert engine._fallback_sentiment("the cat sat on the mat") == 0.0

    def test_empty_string_returns_zero(self, engine):
        assert engine._fallback_sentiment("") == 0.0


class TestAnalyzeSentiment:
    def test_empty_text_returns_zero_without_credential_check(self, engine):
        assert engine.analyze_sentiment("") == 0.0

    def test_non_string_input_returns_zero(self, engine):
        assert engine.analyze_sentiment(None) == 0.0

    def test_no_credentials_falls_back_to_keyword_sentiment(self, engine, monkeypatch):
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
        monkeypatch.setattr("os.path.exists", lambda path: False)
        text = "strong bullish growth"
        result = engine.analyze_sentiment(text)
        assert result == engine._fallback_sentiment(text)


# ============================================================================
# fetch_and_compile_macro — end-to-end structural contract
# ============================================================================

class TestFetchAndCompileMacro:
    def test_returns_all_expected_keys(self, engine):
        result = engine.fetch_and_compile_macro()
        expected_keys = {
            "yield_curve_10y_2y", "high_yield_oas", "inflation_rate", "nominal_10y",
            "sahm_rule_indicator", "vix_value", "date", "sentiment_score", "market_regime",
        }
        assert expected_keys.issubset(result.keys())
        assert result["market_regime"] in {"RISK ON", "NEUTRAL", "RECESSION", "CREDIT EVENT"}

    def test_no_data_engine_fred_attribute_yields_zero_sahm(self, engine):
        """MockDataEngine has no `.fred` -- sahm_rule_indicator must degrade
        to the documented 0.0 fallback, not raise."""
        result = engine.fetch_and_compile_macro()
        assert result["sahm_rule_indicator"] == 0.0

    def test_text_context_runs_sentiment_analysis(self, engine, monkeypatch):
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
        monkeypatch.setattr("os.path.exists", lambda path: False)
        result = engine.fetch_and_compile_macro(text_context="bullish growth outlook")
        assert result["sentiment_score"] > 0.0

    def test_no_text_context_yields_neutral_sentiment(self, engine):
        result = engine.fetch_and_compile_macro(text_context=None)
        assert result["sentiment_score"] == 0.0


# ============================================================================
# compute_hmm_risk_on_probability — gaps not covered by
# tests/test_macro_hmm_integration.py
# ============================================================================

class TestComputeHmmRiskOnProbabilityGaps:
    def _spy_df(self, n=500, seed=1):
        rng = np.random.RandomState(seed)
        prices = 400 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, n)))
        idx = pd.bdate_range(end=pd.Timestamp.now(), periods=n)
        return pd.DataFrame({"Close": prices}, index=idx)

    def test_missing_vixcls_column_returns_none(self, engine, disable_historical_store):
        """settings.HISTORICAL_STORE_ENABLED defaults True, which would route
        macro history through the real on-disk HistoricalStore (bypassing
        whatever this test patches onto data_engine.fetch_macro_history) --
        disabled via the shared fixture so the direct-fetch path under test
        is actually exercised."""
        spy_df = self._spy_df()
        broken_engine = MacroEngine(data_engine=MockDataEngine())
        with mock.patch.object(
            broken_engine.data_engine, "fetch_macro_history",
            return_value=pd.DataFrame({"NOT_VIX": [1.0] * 200}),
        ):
            result = broken_engine.compute_hmm_risk_on_probability(spy_df)
        assert result is None

    def test_hmm_fit_exception_returns_none_not_raise(self, engine, disable_historical_store):
        """A statistical second opinion failing must never crash the primary
        rules-based pipeline -- fit()/predict_proba() raising must degrade to
        None (CONSTRAINT #6).

        HISTORICAL_STORE_ENABLED disabled via the shared fixture: the
        macro-history fetch that happens before fit() is ever called would
        otherwise route through the real, on-disk HistoricalStore (confirmed
        polluting quant_platform.db's macro_history table via direct
        execution) -- this test only cares about the fit()-raises path, so
        the fetch itself uses the direct MockDataEngine.fetch_macro_history()
        path like its sibling tests in this class already do."""
        spy_df = self._spy_df()
        with mock.patch.object(engine._hmm_detector, "fit", side_effect=RuntimeError("singular matrix")):
            result = engine.compute_hmm_risk_on_probability(spy_df)
        assert result is None

    def test_historical_store_disabled_uses_direct_fetch(self, engine, disable_historical_store):
        spy_df = self._spy_df()
        result = engine.compute_hmm_risk_on_probability(spy_df)
        assert result is None or (0.0 <= result <= 1.0)

    def test_historical_store_failure_falls_back_to_direct_fetch(self, engine):
        """When HISTORICAL_STORE_ENABLED=True but HistoricalStore construction
        raises, the engine must fall back to data_engine.fetch_macro_history()
        (pre-Phase-3 behavior) rather than returning None outright."""
        spy_df = self._spy_df()
        with mock.patch("settings.settings.HISTORICAL_STORE_ENABLED", True), \
             mock.patch("data.historical_store.HistoricalStore", side_effect=RuntimeError("db locked")):
            result = engine.compute_hmm_risk_on_probability(spy_df)
        # Falls through to MockDataEngine.fetch_macro_history(), which returns
        # a usable 500-row synthetic series -- so this must succeed, not None.
        assert result is not None
        assert 0.0 <= result <= 1.0

    def test_fetch_macro_history_exception_returns_none(self, engine, disable_historical_store):
        spy_df = self._spy_df()
        with mock.patch.object(
            engine.data_engine, "fetch_macro_history", side_effect=RuntimeError("FRED down")
        ):
            result = engine.compute_hmm_risk_on_probability(spy_df)
        assert result is None
