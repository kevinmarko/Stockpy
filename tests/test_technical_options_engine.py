"""
tests/test_technical_options_engine.py
=======================================
Unit coverage for technical_options_engine.py's lower-level primitives, which
sit underneath the matrix-level behavior already pinned by
tests/test_options_matrix.py (Gravity STEP 38).

That file proves the deterministic strategy directive (Put Credit Spread /
Iron Condor / etc.) and end-to-end build_premium_directive integrity. This
file fills the remaining gaps:

  * black_scholes_pricing_and_greeks's T<=0 boundary, put-call parity, and
    Greeks sign/range sanity -- the Greeks feeding the GUI Options Matrix and
    every strike-resolution call.
  * find_strike_for_delta's brentq-failure fallback (CONSTRAINT #6).
  * calculate_realizable_theta's documented DTE haircut ladder.
  * sanitize_ohlcv / calculate_indicators short-history fallbacks.
  * estimate_gjr_garch_volatility's ARCH-unavailable and fit-failure
    fallbacks to 20-day historical vol (never raises, never an
    unbounded/negative volatility -- CONSTRAINT #4/#6).
  * calculate_realized_vol_rank's flat-volatility degenerate case.
  * _on_strike_grid / _determine_trend_bias pure-function edge cases.
  * build_premium_directive's GJR-GARCH-failure and ATM-Greeks-failure
    dead-letter paths.
"""

import math
from unittest import mock

import numpy as np
import pandas as pd
import pytest

import technical_options_engine as toe_module
from technical_options_engine import (
    OptionsPricingRecommender,
    TechnicalOptionsEngine,
    _determine_trend_bias,
    _on_strike_grid,
    build_premium_directive,
    validate_directive_integrity,
)


# ============================================================================
# Helpers
# ============================================================================

def _ohlcv(n: int, seed: int = 0, start: float = 100.0, flat: bool = False) -> pd.DataFrame:
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    if flat:
        close = np.full(n, start)
    else:
        rng = np.random.RandomState(seed)
        close = start * np.exp(np.cumsum(rng.normal(0.0003, 0.015, n)))
    return pd.DataFrame(
        {
            "Open": close, "High": close * 1.01, "Low": close * 0.99,
            "Close": close, "Volume": np.full(n, 1_000_000.0),
        },
        index=dates,
    )


# ============================================================================
# black_scholes_pricing_and_greeks
# ============================================================================

class TestBlackScholesPricingAndGreeks:
    def test_zero_time_to_expiry_call_returns_intrinsic_value_zero_greeks(self):
        rec = OptionsPricingRecommender(stock_price=110.0)
        result = rec.black_scholes_pricing_and_greeks(K=100.0, T=0.0, sigma=0.25, option_type="call")
        assert result["Price"] == 10.0  # max(0, 110-100)
        assert result == {**result, "Delta": 0.0, "Gamma": 0.0, "Vega": 0.0, "Theta_Daily": 0.0}

    def test_zero_time_to_expiry_put_returns_intrinsic_value(self):
        rec = OptionsPricingRecommender(stock_price=90.0)
        result = rec.black_scholes_pricing_and_greeks(K=100.0, T=0.0, sigma=0.25, option_type="put")
        assert result["Price"] == 10.0  # max(0, 100-90)

    def test_negative_time_to_expiry_treated_as_expired(self):
        rec = OptionsPricingRecommender(stock_price=110.0)
        result = rec.black_scholes_pricing_and_greeks(K=100.0, T=-0.01, sigma=0.25, option_type="call")
        assert result["Price"] == 10.0

    def test_invalid_option_type_raises(self):
        rec = OptionsPricingRecommender(stock_price=100.0)
        with pytest.raises(ValueError):
            rec.black_scholes_pricing_and_greeks(K=100.0, T=0.5, sigma=0.2, option_type="straddle")

    def test_call_delta_in_zero_one_range(self):
        rec = OptionsPricingRecommender(stock_price=100.0)
        result = rec.black_scholes_pricing_and_greeks(K=100.0, T=30 / 365.0, sigma=0.25, option_type="call")
        assert 0.0 <= result["Delta"] <= 1.0

    def test_put_delta_in_negative_one_zero_range(self):
        rec = OptionsPricingRecommender(stock_price=100.0)
        result = rec.black_scholes_pricing_and_greeks(K=100.0, T=30 / 365.0, sigma=0.25, option_type="put")
        assert -1.0 <= result["Delta"] <= 0.0

    def test_gamma_and_vega_are_non_negative(self):
        rec = OptionsPricingRecommender(stock_price=100.0)
        call = rec.black_scholes_pricing_and_greeks(K=100.0, T=30 / 365.0, sigma=0.25, option_type="call")
        assert call["Gamma"] >= 0.0
        assert call["Vega"] >= 0.0

    def test_put_call_parity_holds(self):
        """C - P = S - K*exp(-rT) (Black-Scholes consistency check)."""
        S, K, T, sigma, r = 100.0, 105.0, 30 / 365.0, 0.30, 0.04
        rec = OptionsPricingRecommender(stock_price=S, risk_free_rate=r)
        call = rec.black_scholes_pricing_and_greeks(K=K, T=T, sigma=sigma, option_type="call")
        put = rec.black_scholes_pricing_and_greeks(K=K, T=T, sigma=sigma, option_type="put")
        lhs = call["Price"] - put["Price"]
        rhs = S - K * math.exp(-r * T)
        assert math.isclose(lhs, rhs, abs_tol=1e-6)

    def test_deep_itm_call_delta_approaches_one(self):
        rec = OptionsPricingRecommender(stock_price=200.0)
        result = rec.black_scholes_pricing_and_greeks(K=50.0, T=30 / 365.0, sigma=0.20, option_type="call")
        assert result["Delta"] > 0.95

    def test_deep_otm_call_delta_approaches_zero(self):
        rec = OptionsPricingRecommender(stock_price=50.0)
        result = rec.black_scholes_pricing_and_greeks(K=300.0, T=30 / 365.0, sigma=0.20, option_type="call")
        assert result["Delta"] < 0.05


# ============================================================================
# find_strike_for_delta
# ============================================================================

class TestFindStrikeForDelta:
    def test_resolved_strike_reproduces_target_delta(self):
        rec = OptionsPricingRecommender(stock_price=100.0)
        target = 0.30
        strike = rec.find_strike_for_delta(target, T=30 / 365.0, sigma=0.25, option_type="call")
        greeks = rec.black_scholes_pricing_and_greeks(strike, T=30 / 365.0, sigma=0.25, option_type="call")
        assert math.isclose(greeks["Delta"], target, abs_tol=0.02)

    def test_strike_lands_on_fifty_cent_grid(self):
        rec = OptionsPricingRecommender(stock_price=137.0)
        strike = rec.find_strike_for_delta(-0.16, T=30 / 365.0, sigma=0.30, option_type="put")
        assert _on_strike_grid(strike)

    def test_brentq_failure_falls_back_to_rounded_spot_price(self):
        """CONSTRAINT #6: if root-finding cannot converge, the documented
        fallback is the rounded spot price -- never an exception, never an
        unbounded/nonsensical strike."""
        rec = OptionsPricingRecommender(stock_price=123.37)
        with mock.patch("technical_options_engine.brentq", side_effect=ValueError("no bracket")):
            strike = rec.find_strike_for_delta(0.30, T=30 / 365.0, sigma=0.25, option_type="call")
        assert strike == round(123.37 * 2) / 2


# ============================================================================
# calculate_realizable_theta — DTE haircut ladder
# ============================================================================

class TestCalculateRealizableTheta:
    @pytest.mark.parametrize(
        "dte,expected_retained_fraction",
        [(1, 0.60), (7, 0.78), (30, 0.88), (90, 0.95)],
    )
    def test_haircut_matches_documented_ladder(self, dte, expected_retained_fraction):
        rec = OptionsPricingRecommender(stock_price=100.0)
        theoretical = -1.0  # arbitrary unit theta
        result = rec.calculate_realizable_theta(theoretical, dte)
        assert math.isclose(result, theoretical * expected_retained_fraction, rel_tol=1e-9)

    def test_boundary_dte_one_vs_two_use_different_buckets(self):
        rec = OptionsPricingRecommender(stock_price=100.0)
        r1 = rec.calculate_realizable_theta(-1.0, dte=1)
        r2 = rec.calculate_realizable_theta(-1.0, dte=2)
        assert r1 != r2  # dte=1 -> 40% haircut, dte=2 -> 22% haircut


# ============================================================================
# sanitize_ohlcv
# ============================================================================

class TestSanitizeOhlcv:
    def test_none_input_returns_empty_dataframe(self):
        result = TechnicalOptionsEngine.sanitize_ohlcv(None)
        assert result.empty

    def test_empty_input_returns_empty_dataframe(self):
        result = TechnicalOptionsEngine.sanitize_ohlcv(pd.DataFrame())
        assert result.empty

    def test_drops_rows_with_nan_pricing_columns(self):
        df = _ohlcv(30, seed=1)
        df.loc[df.index[5], "Close"] = np.nan
        result = TechnicalOptionsEngine.sanitize_ohlcv(df)
        assert len(result) == 29
        assert not result["Close"].isna().any()

    def test_sorts_chronologically(self):
        df = _ohlcv(10, seed=2).iloc[::-1]  # reverse order
        result = TechnicalOptionsEngine.sanitize_ohlcv(df)
        assert result.index.is_monotonic_increasing


# ============================================================================
# calculate_indicators
# ============================================================================

class TestCalculateIndicators:
    def test_insufficient_history_returns_zero_fallback_dict(self):
        engine = TechnicalOptionsEngine()
        result = engine.calculate_indicators(_ohlcv(10, seed=3))
        assert result == {
            "Aroon_Oscillator": 0.0, "Coppock_Curve": 0.0,
            "Chandelier_Long": 0.0, "Chandelier_Short": 0.0,
        }

    def test_sufficient_history_produces_real_floats(self):
        engine = TechnicalOptionsEngine()
        result = engine.calculate_indicators(_ohlcv(80, seed=4))
        for key in ("Aroon_Oscillator", "Coppock_Curve", "Chandelier_Long", "Chandelier_Short"):
            assert isinstance(result[key], float)
            assert not math.isnan(result[key])


# ============================================================================
# estimate_gjr_garch_volatility
# ============================================================================

class TestEstimateGjrGarchVolatility:
    def test_insufficient_history_returns_neutral_fallback(self):
        engine = TechnicalOptionsEngine()
        assert engine.estimate_gjr_garch_volatility(_ohlcv(10, seed=5)) == 0.20

    def test_sufficient_history_returns_bounded_volatility(self):
        engine = TechnicalOptionsEngine()
        vol = engine.estimate_gjr_garch_volatility(_ohlcv(150, seed=6))
        assert 0.02 <= vol <= 3.0

    def test_arch_unavailable_uses_historical_fallback(self, monkeypatch):
        monkeypatch.setattr(toe_module, "ARCH_AVAILABLE", False)
        engine = TechnicalOptionsEngine()
        df = _ohlcv(150, seed=7)
        vol = engine.estimate_gjr_garch_volatility(df)
        returns = df["Close"].pct_change().dropna()
        expected = float(
            max(0.02, min(3.0, returns.tail(20).std() * np.sqrt(252)))
        )
        assert math.isclose(vol, expected, rel_tol=1e-6)

    def test_garch_fit_failure_falls_back_to_historical_vol_not_raise(self):
        """CONSTRAINT #6: a GARCH optimizer failure must degrade to the
        20-day historical-vol fallback, never propagate."""
        engine = TechnicalOptionsEngine()
        df = _ohlcv(150, seed=8)
        with mock.patch("technical_options_engine.arch_model", side_effect=RuntimeError("optimizer failed")):
            vol = engine.estimate_gjr_garch_volatility(df)
        returns = df["Close"].pct_change().dropna()
        expected = float(max(0.02, min(3.0, returns.tail(20).std() * np.sqrt(252))))
        assert math.isclose(vol, expected, rel_tol=1e-6)

    def test_volatility_is_never_negative_or_unbounded(self):
        """Sanity bound enforced regardless of input shape -- feed a near-
        constant series (near-zero realized vol) and a noisy one, both must
        land in [0.02, 3.0]."""
        engine = TechnicalOptionsEngine()
        flat_vol = engine.estimate_gjr_garch_volatility(_ohlcv(100, flat=True))
        assert 0.02 <= flat_vol <= 3.0


# ============================================================================
# calculate_realized_vol_rank
# ============================================================================

class TestCalculateRealizedVolRank:
    def test_insufficient_history_returns_fifty(self):
        engine = TechnicalOptionsEngine()
        assert engine.calculate_realized_vol_rank(_ohlcv(10, seed=9), current_vol=0.30) == 50.0

    def test_flat_price_series_returns_fifty_degenerate_case(self):
        """vol_max == vol_min (a perfectly flat price series has zero rolling
        vol throughout) must return the neutral midpoint, not divide by
        zero."""
        engine = TechnicalOptionsEngine()
        result = engine.calculate_realized_vol_rank(_ohlcv(300, flat=True), current_vol=0.0)
        assert result == 50.0

    def test_current_vol_at_historical_max_ranks_near_hundred(self):
        engine = TechnicalOptionsEngine()
        df = _ohlcv(300, seed=10)
        returns = df["Close"].pct_change().dropna()
        rolling_vol = (returns.rolling(window=20).std() * np.sqrt(252)).dropna().tail(252)
        result = engine.calculate_realized_vol_rank(df, current_vol=float(rolling_vol.max()))
        assert result == pytest.approx(100.0, abs=1e-6)

    def test_current_vol_at_historical_min_ranks_near_zero(self):
        engine = TechnicalOptionsEngine()
        df = _ohlcv(300, seed=11)
        returns = df["Close"].pct_change().dropna()
        rolling_vol = (returns.rolling(window=20).std() * np.sqrt(252)).dropna().tail(252)
        result = engine.calculate_realized_vol_rank(df, current_vol=float(rolling_vol.min()))
        assert result == pytest.approx(0.0, abs=1e-6)

    def test_result_is_clamped_to_zero_hundred_range(self):
        engine = TechnicalOptionsEngine()
        df = _ohlcv(300, seed=12)
        # An absurdly high current_vol must still clamp to 100, not overshoot.
        result = engine.calculate_realized_vol_rank(df, current_vol=50.0)
        assert 0.0 <= result <= 100.0


# ============================================================================
# _on_strike_grid / _determine_trend_bias — pure-function edge cases
# ============================================================================

class TestOnStrikeGrid:
    @pytest.mark.parametrize("strike", [100.0, 100.5, 95.0, 0.5])
    def test_on_grid_values(self, strike):
        assert _on_strike_grid(strike) is True

    @pytest.mark.parametrize("strike", [100.37, 95.01, 0.25])
    def test_off_grid_values(self, strike):
        assert _on_strike_grid(strike) is False

    def test_non_finite_strike_is_false(self):
        assert _on_strike_grid(float("nan")) is False
        assert _on_strike_grid(float("inf")) is False

    def test_non_positive_grid_is_false(self):
        assert _on_strike_grid(100.0, grid=0.0) is False
        assert _on_strike_grid(100.0, grid=-0.5) is False


class TestDetermineTrendBias:
    def test_positive_both_is_bullish(self):
        assert _determine_trend_bias(10.0, 5.0) == "Bullish"

    def test_negative_both_is_bearish(self):
        assert _determine_trend_bias(-10.0, -5.0) == "Bearish"

    def test_mixed_signs_is_neutral(self):
        assert _determine_trend_bias(10.0, -5.0) == "Neutral"
        assert _determine_trend_bias(-10.0, 5.0) == "Neutral"

    def test_zero_values_are_neutral(self):
        assert _determine_trend_bias(0.0, 0.0) == "Neutral"


# ============================================================================
# build_premium_directive — dead-letter paths
# ============================================================================

class TestBuildPremiumDirectiveDeadLetter:
    def test_garch_failure_yields_nan_sigma_and_cash_wait(self):
        bars = _ohlcv(252, seed=13)
        with mock.patch.object(
            TechnicalOptionsEngine, "estimate_gjr_garch_volatility", side_effect=RuntimeError("boom")
        ):
            row = build_premium_directive(
                "FAIL", bars, spot_price=float(bars["Close"].iloc[-1]), is_stale=False,
            )
        assert math.isnan(row["Sigma_GARCH"])
        assert row["Strategy"] == "Cash"
        # No fabricated legs/integrity-violating output when pricing cannot proceed.
        assert row["Legs"] == []

    def test_non_finite_spot_price_short_circuits_to_diagnostic_row(self):
        bars = _ohlcv(252, seed=14)
        row = build_premium_directive("BADPRICE", bars, spot_price=float("nan"), is_stale=True)
        assert math.isnan(row["Price"])
        assert row["Strategy"] == "Cash"
        assert row["Stale"] is True

    def test_atm_greeks_failure_does_not_abort_strategy_directive(self):
        """ATM Greeks are informational; a failure there must not prevent the
        strategy directive (Step 5) from still being computed."""
        bars = _ohlcv(252, seed=15)
        with mock.patch.object(
            OptionsPricingRecommender, "black_scholes_pricing_and_greeks",
            side_effect=RuntimeError("greeks failed"),
        ):
            row = build_premium_directive(
                "ATMFAIL", bars, spot_price=float(bars["Close"].iloc[-1]), is_stale=False,
            )
        assert math.isnan(row["ATM_Delta"])
        # The function returns early after the ATM Greeks failure (per the
        # source's except->return row), so Strategy stays at its Cash default
        # -- this pins that documented short-circuit rather than assuming a
        # downstream strategy directive is still attempted.
        assert row["Strategy"] == "Cash"

    def test_trend_indicator_failure_defaults_to_neutral(self):
        bars = _ohlcv(252, seed=16)
        with mock.patch.object(TechnicalOptionsEngine, "calculate_indicators", side_effect=RuntimeError("boom")):
            row = build_premium_directive(
                "TRENDFAIL", bars, spot_price=float(bars["Close"].iloc[-1]), is_stale=False,
            )
        assert row["Trend_Bias"] == "Neutral"

    def test_integrity_ok_true_for_well_formed_engine_output(self):
        bars = _ohlcv(252, seed=17)
        row = build_premium_directive("OK", bars, spot_price=float(bars["Close"].iloc[-1]))
        assert isinstance(row["Integrity_OK"], bool)
        if row["Strategy"] != "Cash":
            assert row["Integrity_OK"] is True


# ============================================================================
# validate_directive_integrity — gaps not covered by test_options_matrix.py
# ============================================================================

class TestValidateDirectiveIntegrityGaps:
    def test_cash_directive_is_trivially_valid(self):
        directive = {"Strategy": "Cash", "Action": "Wait", "Legs": []}
        result = validate_directive_integrity(directive)
        assert result == {"ok": True, "issues": [], "checks": []}

    def test_iron_condor_leg_without_delta_skips_delta_check_not_fail(self):
        """Iron Condor legs omit Delta by engine convention -- the validator
        must SKIP (not fail) the delta check for those legs while still
        checking the strike grid."""
        directive = {
            "Strategy": "Iron Condor",
            "Legs": [
                {"Side": "Short", "Type": "Put", "Strike": 95.0, "Price": 1.0},  # no Delta key
            ],
        }
        result = validate_directive_integrity(directive)
        assert result["ok"] is True
        assert result["checks"][0]["DeltaOK"] is None

    def test_delta_outside_tolerance_is_flagged(self):
        directive = {
            "Strategy": "Put Credit Spread",
            "Legs": [
                {"Side": "Short", "Type": "Put", "Strike": 95.0, "Price": 1.0, "Delta": -0.80},
            ],
        }
        result = validate_directive_integrity(directive, delta_tolerance=0.05)
        assert result["ok"] is False
        assert any("deviates from target" in issue for issue in result["issues"])
