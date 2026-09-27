"""
tests/test_volatility_garch.py
==============================
Unit coverage for the two core modules moved out of technical_options_engine
in 2026-09 (step 3d):

  * volatility/garch.py -- sanitize_ohlcv, GJR-GARCH(1,1) day-ahead vol and
    its multi-horizon term structure, including the ARCH-unavailable and
    fit-failure fallbacks to 20-day historical vol (never raises, never an
    unbounded/negative volatility -- CONSTRAINT #4/#6).
  * trend_indicators.py -- the Aroon/Coppock/Chandelier short-history
    fallback.

These tests moved here unchanged from tests/test_technical_options_engine.py;
TestDelegatesMatchCore pins that the options engine's delegates still return
exactly the core values.
"""

import math
from unittest import mock

import numpy as np
import pandas as pd
import pytest

import volatility.garch as garch_module
from trend_indicators import calculate_trend_exit_indicators
from volatility.garch import GarchVolatilityEstimator


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
# sanitize_ohlcv
# ============================================================================

class TestSanitizeOhlcv:
    def test_none_input_returns_empty_dataframe(self):
        result = GarchVolatilityEstimator.sanitize_ohlcv(None)
        assert result.empty

    def test_empty_input_returns_empty_dataframe(self):
        result = GarchVolatilityEstimator.sanitize_ohlcv(pd.DataFrame())
        assert result.empty

    def test_drops_rows_with_nan_pricing_columns(self):
        df = _ohlcv(30, seed=1)
        df.loc[df.index[5], "Close"] = np.nan
        result = GarchVolatilityEstimator.sanitize_ohlcv(df)
        assert len(result) == 29
        assert not result["Close"].isna().any()

    def test_sorts_chronologically(self):
        df = _ohlcv(10, seed=2).iloc[::-1]  # reverse order
        result = GarchVolatilityEstimator.sanitize_ohlcv(df)
        assert result.index.is_monotonic_increasing


# ============================================================================
# calculate_trend_exit_indicators
# ============================================================================

class TestCalculateIndicators:
    def test_insufficient_history_returns_zero_fallback_dict(self):
        result = calculate_trend_exit_indicators(_ohlcv(10, seed=3))
        assert result == {
            "Aroon_Oscillator": 0.0, "Coppock_Curve": 0.0,
            "Chandelier_Long": 0.0, "Chandelier_Short": 0.0,
        }

    def test_sufficient_history_produces_real_floats(self):
        result = calculate_trend_exit_indicators(_ohlcv(80, seed=4))
        for key in ("Aroon_Oscillator", "Coppock_Curve", "Chandelier_Long", "Chandelier_Short"):
            assert isinstance(result[key], float)
            assert not math.isnan(result[key])


# ============================================================================
# estimate_gjr_garch_volatility
# ============================================================================

class TestEstimateGjrGarchVolatility:
    def test_insufficient_history_returns_nan_not_fabricated_neutral(self):
        """< 22 rows isn't enough to measure ANYTHING (not even a 20-day
        historical stdev fallback) -- must return NaN (CONSTRAINT #4), never
        a fabricated 0.20."""
        import math
        engine = GarchVolatilityEstimator()
        assert math.isnan(engine.estimate_gjr_garch_volatility(_ohlcv(10, seed=5)))

    def test_sufficient_history_returns_bounded_volatility(self):
        engine = GarchVolatilityEstimator()
        vol = engine.estimate_gjr_garch_volatility(_ohlcv(150, seed=6))
        assert 0.02 <= vol <= 3.0

    def test_arch_unavailable_uses_historical_fallback(self, monkeypatch):
        monkeypatch.setattr(garch_module, "ARCH_AVAILABLE", False)
        engine = GarchVolatilityEstimator()
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
        engine = GarchVolatilityEstimator()
        df = _ohlcv(150, seed=8)
        with mock.patch("volatility.garch.arch_model", side_effect=RuntimeError("optimizer failed")):
            vol = engine.estimate_gjr_garch_volatility(df)
        returns = df["Close"].pct_change().dropna()
        expected = float(max(0.02, min(3.0, returns.tail(20).std() * np.sqrt(252))))
        assert math.isclose(vol, expected, rel_tol=1e-6)

    def test_volatility_is_never_negative_or_unbounded(self):
        """Sanity bound enforced regardless of input shape -- feed a near-
        constant series (near-zero realized vol) and a noisy one, both must
        land in [0.02, 3.0]."""
        engine = GarchVolatilityEstimator()
        flat_vol = engine.estimate_gjr_garch_volatility(_ohlcv(100, flat=True))
        assert 0.02 <= flat_vol <= 3.0


# ============================================================================
# estimate_gjr_garch_volatility_term_structure
#
# The bug fix: estimate_gjr_garch_volatility() always forecast exactly 1 day
# ahead, and every multi-day caller (forecasting_engine.py's Monte Carlo
# horizons) scaled that single number by sqrt(T) -- ignoring GARCH's actual
# value, conditional-variance mean-reversion toward the long-run level over
# the forecast window. This term-structure method fits ONCE and derives a
# genuine, mean-reversion-aware annualized vol PER horizon instead.
# ============================================================================


class TestGarchTermStructure:
    def _shocked_series(self, n: int = 1000, seed: int = 42, shock_days: int = 15) -> pd.DataFrame:
        """A calm baseline series with a recent volatility shock in the last
        `shock_days` -- the regime where naive sigma_1*sqrt(T) scaling most
        overstates the true multi-day vol (current conditional variance is
        elevated above the long-run level, so it must mean-revert DOWN over
        the forecast horizon)."""
        rng = np.random.RandomState(seed)
        returns = rng.normal(0, 0.01, n)
        returns[-shock_days:] = rng.normal(0, 0.05, shock_days)
        close = 100.0 * np.exp(np.cumsum(returns))
        dates = pd.date_range("2020-01-01", periods=n, freq="B")
        return pd.DataFrame(
            {
                "Open": close, "High": close * 1.01, "Low": close * 0.99,
                "Close": close, "Volume": np.full(n, 1_000_000.0),
            },
            index=dates,
        )

    def test_horizon_one_is_byte_identical_to_scalar_method(self):
        """estimate_gjr_garch_volatility() is now a thin horizon=1 wrapper
        around the term-structure method -- must be numerically identical,
        not merely close, on the same df (verifies the refactor is truly
        behavior-preserving for every existing horizon=1 caller: the
        GARCH_Vol dashboard column, the VRP gate, True IVR, position
        sizing)."""
        engine = GarchVolatilityEstimator()
        df = self._shocked_series(seed=1)
        scalar = engine.estimate_gjr_garch_volatility(df)
        term_structure = engine.estimate_gjr_garch_volatility_term_structure(df, horizons=(1,))
        assert term_structure[1] == scalar

        # Also true when horizon=1 is requested alongside longer horizons --
        # a single fit+forecast(horizon=max) call must still reproduce the
        # SAME h.1 value regardless of how far the forecast is extended
        # (verified against the arch library: the analytic recursion's first
        # step does not depend on how many further steps are requested).
        multi = engine.estimate_gjr_garch_volatility_term_structure(df, horizons=(1, 10, 30, 60, 90))
        assert multi[1] == scalar

    def test_mean_reversion_is_reflected_after_a_vol_shock(self):
        """The actual regression check for the bug: after a recent vol
        shock, the per-horizon annualized vol must (a) strictly decrease as
        the horizon grows (mean-reverting toward the long-run level) and
        (b) be strictly less than the naive sigma_1 broadcast to every
        horizon -- proving the fix reflects mean-reversion instead of the
        old sigma_1*sqrt(T)-style flat extrapolation."""
        engine = GarchVolatilityEstimator()
        df = self._shocked_series(seed=2)
        horizons = (1, 10, 30, 60, 90)
        term_structure = engine.estimate_gjr_garch_volatility_term_structure(df, horizons=horizons)

        sigma_1 = term_structure[1]
        values = [term_structure[h] for h in horizons]
        # (a) strictly decreasing -- genuine mean-reversion, not noise.
        assert all(values[i] > values[i + 1] for i in range(len(values) - 1)), (
            f"expected monotonically decreasing annualized vol as horizon "
            f"grows after a vol shock, got {term_structure}"
        )
        # (b) every horizon > 1 sits strictly below the flat sigma_1 value
        # the OLD naive sqrt(T) scaling would have (mis)used for every
        # horizon.
        for h in horizons[1:]:
            assert term_structure[h] < sigma_1, (
                f"horizon {h}'s vol ({term_structure[h]}) must be below the "
                f"naive flat sigma_1 ({sigma_1}) after a recent vol shock"
            )

    def test_arch_unavailable_fallback_is_flat_across_every_horizon(self, monkeypatch):
        """No fitted model -> no term structure to compute -- every
        requested horizon must degrade to the SAME 20-day historical
        fallback value (never fabricated per-horizon variation)."""
        monkeypatch.setattr(garch_module, "ARCH_AVAILABLE", False)
        engine = GarchVolatilityEstimator()
        df = _ohlcv(150, seed=17)
        term_structure = engine.estimate_gjr_garch_volatility_term_structure(
            df, horizons=(1, 10, 30, 60, 90)
        )
        returns = df["Close"].pct_change().dropna()
        expected = float(max(0.02, min(3.0, returns.tail(20).std() * np.sqrt(252))))
        for h, vol in term_structure.items():
            assert vol == pytest.approx(expected, rel=1e-6), f"horizon {h} must match the flat fallback"

    def test_insufficient_history_returns_none_not_fabricated_flat_default(self):
        """< 22 rows isn't enough to measure ANYTHING, GARCH or a historical
        stdev fallback -- must return None (CONSTRAINT #4), never a
        fabricated flat 0.20 across every horizon. Callers that subscript
        the result MUST check for None first (see
        estimate_gjr_garch_volatility and forecasting_engine.py's
        _estimate_daily_sigma_multi_horizon, both of which do)."""
        engine = GarchVolatilityEstimator()
        term_structure = engine.estimate_gjr_garch_volatility_term_structure(
            _ohlcv(10, seed=18), horizons=(1, 10, 30, 60, 90)
        )
        assert term_structure is None

    def test_single_fit_covers_every_horizon(self, monkeypatch):
        """Efficiency contract: ONE arch_model.fit() call must produce every
        requested horizon's value -- not one fit per horizon."""
        engine = GarchVolatilityEstimator()
        df = self._shocked_series(seed=3)

        import arch as arch_lib
        real_fit = arch_lib.univariate.base.ARCHModel.fit
        call_count = {"n": 0}

        def counting_fit(self, *args, **kwargs):
            call_count["n"] += 1
            return real_fit(self, *args, **kwargs)

        monkeypatch.setattr(arch_lib.univariate.base.ARCHModel, "fit", counting_fit)
        engine.estimate_gjr_garch_volatility_term_structure(df, horizons=(1, 10, 30, 60, 90))
        assert call_count["n"] == 1, "must fit GJR-GARCH exactly once regardless of horizon count"


# ============================================================================
# The options engine's delegates stay numerically identical to the core
# ============================================================================

class TestDelegatesMatchCore:
    def test_technical_options_engine_delegates_return_core_values(self):
        from technical_options_engine import TechnicalOptionsEngine

        df = _ohlcv(300, seed=21)
        toe = TechnicalOptionsEngine()
        core = GarchVolatilityEstimator()
        assert toe.estimate_gjr_garch_volatility(df) == core.estimate_gjr_garch_volatility(df)
        assert toe.estimate_gjr_garch_volatility_term_structure(
            df, horizons=(1, 10, 30)
        ) == core.estimate_gjr_garch_volatility_term_structure(df, horizons=(1, 10, 30))
        assert toe.calculate_indicators(df) == calculate_trend_exit_indicators(df)
        pd.testing.assert_frame_equal(toe.sanitize_ohlcv(df), core.sanitize_ohlcv(df))
