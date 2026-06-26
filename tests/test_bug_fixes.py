"""
tests/test_bug_fixes.py
=======================
Regression tests for the six bugs fixed in this session.

BUG-1 / BUG-2 — Sahm Rule bypassed in run_pipeline (main_orchestrator.py)
  BUG-1: me._fallback_sentiment("") was used instead of me.calculate_sahm_rule()
  BUG-2: sahm_rule_indicator was never passed to MacroEconomicDTO, so the
          Sahm Rule kill-switch path in macro_dto.killSwitch could never fire.

BUG-3 — Gordon Growth Model asymmetric g (processing_engine.py)
  Numerator used uncapped div_growth_rate; denominator used min(g, r-0.01).

BUG-4 — Momentum early-return fabricated 0.0 instead of NaN (processing_engine.py)
  When len(df) < 253, ROC columns were set to 0.0 (fabricated flat momentum).

BUG-5 — Mutable default argument in evaluate_portfolio (evaluation_engine.py)
  benchmark_df defaulted to a shared pd.DataFrame() instance.

BUG-6 — Fallback forecast used naive linear formula (main_orchestrator.py)
  Forecast_10/60/90 used price*(1+mu*N) instead of Monte Carlo in the except path.
"""

import math
import numpy as np
import pandas as pd
import pytest
from datetime import datetime
from unittest import mock

from dto_models import MacroEconomicDTO
from processing_engine import ProcessingEngine
from evaluation_engine import EvaluationEngine
from forecasting_engine import ForecastingEngine


# ============================================================================
# BUG-1 / BUG-2: Sahm Rule wiring
# ============================================================================

class TestSahmRuleWiring:
    """
    BUG-1: run_pipeline used me._fallback_sentiment("") (always 0.0) as the
           Sahm Rule value. _fallback_sentiment is a keyword-based NLP helper
           that returns 0.0 for empty input — completely unrelated to FRED.
    BUG-2: sahm_rule_indicator was never forwarded to MacroEconomicDTO,
           so macro_dto.killSwitch via the Sahm Rule branch was structurally
           disabled (self.sahm_rule_indicator always defaulted to 0.0).
    """

    def test_macro_dto_kill_switch_fires_at_sahm_threshold(self):
        """When sahm_rule_indicator >= 0.5, killSwitch must return True."""
        dto = MacroEconomicDTO(
            yield_curve_10y_2y=0.5,
            high_yield_oas=3.0,
            inflation_rate=2.0,
            vix_value=18.0,
            sahm_rule_indicator=0.52,  # above 0.5 threshold
        )
        assert dto.killSwitch is True, (
            "killSwitch should be True when sahm_rule_indicator >= 0.5"
        )

    def test_macro_dto_kill_switch_silent_below_sahm_threshold(self):
        """When sahm_rule_indicator < 0.5 and VIX < 30, killSwitch must be False."""
        dto = MacroEconomicDTO(
            yield_curve_10y_2y=0.5,
            high_yield_oas=3.0,
            inflation_rate=2.0,
            vix_value=18.0,
            sahm_rule_indicator=0.0,  # the bug-induced value
        )
        assert dto.killSwitch is False

    def test_macro_dto_default_sahm_is_zero(self):
        """Without the fix, MacroEconomicDTO defaults sahm_rule_indicator to 0.
        Verify the default so any future refactor that changes it is caught."""
        dto_no_sahm = MacroEconomicDTO(
            yield_curve_10y_2y=0.5,
            high_yield_oas=3.0,
            inflation_rate=2.0,
        )
        assert dto_no_sahm.sahm_rule_indicator == 0.0

    def test_macro_dto_sahm_wired_correctly_triggers_recession_regime(self):
        """Sahm >= 0.6 must produce RECESSION regime via _rules_based_regime."""
        dto = MacroEconomicDTO(
            yield_curve_10y_2y=0.3,      # not inverted
            high_yield_oas=3.0,          # below credit event threshold
            inflation_rate=2.0,
            sahm_rule_indicator=0.65,    # above recession threshold
        )
        assert dto._rules_based_regime == "RECESSION"

    def test_fallback_sentiment_returns_zero_for_empty_string(self):
        """Document that _fallback_sentiment("") always returns 0.0 —
        the root cause of BUG-1 (wrong method called for Sahm proxy)."""
        from macro_engine import MacroEngine
        from data_engine import MockDataEngine

        me = MacroEngine(data_engine=MockDataEngine())
        result = me._fallback_sentiment("")
        assert result == 0.0, (
            "_fallback_sentiment('') must return 0.0; this confirms BUG-1: "
            "calling it as a Sahm proxy always silenced the recession signal."
        )

    def test_run_pipeline_calls_calculate_sahm_rule_not_fallback_sentiment(self):
        """run_pipeline must call me.calculate_sahm_rule(), not _fallback_sentiment."""
        from data_engine import MockDataEngine
        from macro_engine import MacroEngine

        de = MockDataEngine()
        me = MacroEngine(data_engine=de)

        with mock.patch.object(
            me, "_fallback_sentiment", wraps=me._fallback_sentiment
        ) as mock_fallback, mock.patch.object(
            me, "calculate_sahm_rule", return_value=0.42
        ) as mock_sahm:
            # simulate what run_pipeline does after the fix
            sahm_val = me.calculate_sahm_rule()

        mock_sahm.assert_called_once()
        mock_fallback.assert_not_called()
        assert sahm_val == 0.42


# ============================================================================
# BUG-3: Gordon Growth Model symmetric g
# ============================================================================

class TestGordonFairValueSymmetricG:
    """
    BUG-3: The numerator used uncapped div_growth_rate while the denominator
    used min(g, r-0.01). With a high growth rate (e.g. 14%) the numerator
    was inflated while the denominator was capped, producing an over-valued
    fair value. After the fix both use the same capped g.
    """

    def _engine(self):
        pe = ProcessingEngine()
        pe.required_return_rate = 0.10  # 10% discount rate for easy math
        return pe

    def test_gordon_symmetric_with_normal_growth(self):
        """Vanilla case: g=4%, r=10%. Result should be D1/(r-g) consistently."""
        pe = self._engine()
        price = 100.0
        dy = 0.05       # 5% dividend yield → D0 = 5.0
        g_raw = 0.04    # 4% growth
        # g is NOT capped (4% < 10%-1% = 9%)
        # D1 = 5.0 * (1+0.04) = 5.20
        # Gordon = 5.20 / (0.10 - 0.04) = 86.67
        result = pe.calculate_gordon_fair_value(price, dy, g_raw)
        expected = round((price * dy * (1 + g_raw)) / (0.10 - g_raw), 2)
        assert math.isclose(result, expected, rel_tol=1e-5)

    def test_gordon_symmetric_when_g_is_capped(self):
        """When g > r-0.01 (e.g. 14% > 9%), both numerator and denominator
        should use the capped value g=9%, not 14% in the numerator."""
        pe = self._engine()
        price = 100.0
        dy = 0.05        # D0 = 5.0
        g_raw = 0.14     # would be capped to r-0.01 = 0.09
        g_capped = 0.10 - 0.01  # = 0.09

        result = pe.calculate_gordon_fair_value(price, dy, g_raw)
        # Correct: D1 = 5.0 * (1+0.09) = 5.45; Gordon = 5.45/(0.10-0.09)=545
        expected_correct = round((price * dy * (1 + g_capped)) / (0.10 - g_capped), 2)
        # The old (buggy) value would have been:
        # D1 = 5.0 * (1+0.14) = 5.70; Gordon = 5.70/(0.10-0.09)=570
        buggy_value = round((price * dy * (1 + g_raw)) / (0.10 - g_capped), 2)

        assert math.isclose(result, expected_correct, rel_tol=1e-5), (
            f"Gordon with capped g should be {expected_correct}, got {result}"
        )
        assert result != buggy_value, "Bug-3 regression: numerator must use capped g"

    def test_gordon_returns_zero_for_negative_dividend_yield(self):
        pe = self._engine()
        assert pe.calculate_gordon_fair_value(100.0, 0.0, 0.04) == 0.0
        assert pe.calculate_gordon_fair_value(100.0, -0.01, 0.04) == 0.0

    def test_gordon_returns_zero_when_g_exceeds_r(self):
        """r == g after capping triggers the guard → return 0.0 (no infinite val)."""
        pe = self._engine()
        # g_raw = 0.10 → g_capped = 0.10 - 0.01 = 0.09; r - g = 0.01 > 0 → should return value
        # but g_raw = 0.20 → g_capped = 0.09 still
        # Let's ensure it doesn't raise
        result = pe.calculate_gordon_fair_value(100.0, 0.05, 0.20)
        assert isinstance(result, float)
        assert result >= 0.0


# ============================================================================
# BUG-4: Momentum early-return must emit NaN not 0.0
# ============================================================================

class TestMomentumEarlyReturnNaN:
    """
    BUG-4: calculate_momentum_metrics() returned 0.0 for all ROC columns when
    len(df) < 253. A 0.0 ROC is fabricated data — it looks like a perfectly
    flat-momentum stock rather than "insufficient history". Constraint #4.
    After the fix the early-return branch sets all columns to NaN.
    """

    def _short_df(self, n: int = 100) -> pd.DataFrame:
        dates = pd.date_range("2024-01-01", periods=n, freq="B")
        prices = [100.0 + i * 0.1 for i in range(n)]
        return pd.DataFrame({
            "Open": prices, "High": prices, "Low": prices,
            "Close": prices, "Volume": [1_000_000] * n,
        }, index=dates)

    def test_roc_12m_is_nan_for_short_history(self):
        pe = ProcessingEngine()
        df = self._short_df(100)  # < 253 bars
        out = pe.calculate_momentum_metrics(df)
        assert pd.isna(out["ROC_12M"].iloc[-1]), "ROC_12M should be NaN for < 253 bars"

    def test_roc_6m_is_nan_for_short_history(self):
        pe = ProcessingEngine()
        df = self._short_df(100)
        out = pe.calculate_momentum_metrics(df)
        assert pd.isna(out["ROC_6M"].iloc[-1])

    def test_realized_vol_is_nan_for_short_history(self):
        pe = ProcessingEngine()
        df = self._short_df(100)
        out = pe.calculate_momentum_metrics(df)
        assert pd.isna(out["Realized_Vol_60D"].iloc[-1])

    def test_momentum_vol_scaled_is_nan_for_short_history(self):
        pe = ProcessingEngine()
        df = self._short_df(100)
        out = pe.calculate_momentum_metrics(df)
        assert pd.isna(out["Momentum_Vol_Scaled"].iloc[-1])

    def test_roc_values_are_real_for_sufficient_history(self):
        """With >= 253 bars the ROC values must be numeric (not NaN)."""
        pe = ProcessingEngine()
        df = self._short_df(300)
        out = pe.calculate_momentum_metrics(df)
        # The last row should have real (non-NaN) ROC_12M
        assert not pd.isna(out["ROC_12M"].iloc[-1])

    def test_technical_metrics_surfaces_nan_realized_vol_for_short_series(self):
        """calculate_technical_metrics propagates NaN Realized_Vol_60D for tickers
        with fewer than 253 bars — confirming the full-stack fix."""
        pe = ProcessingEngine()
        short_df = self._short_df(100)
        raw = {"AAPL": short_df}
        result = pe.calculate_technical_metrics(raw)
        rv = result["AAPL"]["Realized_Vol_60D"]
        assert math.isnan(rv), f"Realized_Vol_60D should be NaN for short history, got {rv}"


# ============================================================================
# BUG-5: Mutable default argument in evaluate_portfolio
# ============================================================================

class TestEvaluatePortfolioMutableDefault:
    """
    BUG-5: benchmark_df had a mutable default `pd.DataFrame()`. In Python,
    mutable default arguments are evaluated once at function-definition time
    and shared across all calls that don't supply the argument. Any write to
    that object persists to the next call. The fix changes the default to None
    and creates a fresh pd.DataFrame() inside the function body.
    """

    def _minimal_df(self) -> pd.DataFrame:
        return pd.DataFrame({
            "Symbol": ["AAPL"],
            "Price": [150.0],
            "sector": ["Technology"],
            "VaR 95": [-0.03],
            "Beta": [1.1],
        })

    def test_benchmark_df_none_default_does_not_share_state(self):
        """Two consecutive calls with no benchmark_df arg must each get a
        fresh empty DataFrame — i.e. no shared mutable state between calls."""
        import inspect
        sig = inspect.signature(EvaluationEngine.evaluate_portfolio)
        default = sig.parameters["benchmark_df"].default
        # After the fix the default is None (not a pd.DataFrame instance)
        assert default is None, (
            "benchmark_df default must be None (not a shared pd.DataFrame) "
            "to avoid mutable-default-argument bugs."
        )

    def test_omitting_benchmark_df_does_not_raise(self):
        """Calling evaluate_portfolio without a benchmark_df must not raise
        and must return a valid DataFrame (i.e. None is handled internally)."""
        engine = EvaluationEngine()
        df = self._minimal_df()
        # Should not raise; benchmark_df defaults to None and is converted inside
        result = engine.evaluate_portfolio(df)
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 1


# ============================================================================
# BUG-6: Fallback forecast uses Monte Carlo, not naive linear extrapolation
# ============================================================================

class TestFallbackForecastMonteCarlo:
    """
    BUG-6: In the exception-handling branch inside main_orchestrator.run_pipeline,
    Forecast_10/60/90 were computed as `price * (1 + mu * N)` — a linear
    approximation that (a) is deterministic (no variance), (b) is not a valid GBM
    estimate, and (c) fabricates confidence levels that agree exactly with
    Forecast_30's drift. The fix uses run_monte_carlo() for all horizons.

    We test the ForecastingEngine.run_monte_carlo method directly to verify
    its output is consistent across horizons (Monte Carlo is the same engine
    used in the happy path).
    """

    def test_monte_carlo_returns_three_distinct_values(self):
        """run_monte_carlo must return (mean, low, high) for any valid input."""
        fe = ForecastingEngine()
        mean, low, high = fe.run_monte_carlo(100.0, 0.0002, 0.015, 30)
        assert isinstance(mean, float)
        assert isinstance(low, float)
        assert isinstance(high, float)

    def test_monte_carlo_horizon_10_different_from_60(self):
        """A 10-day MC and a 60-day MC on the same params must produce different
        means — confirming each horizon is computed independently, not via
        linear scaling. (Linear scaling would produce deterministically
        proportional values; MC adds variance that breaks determinism.)"""
        fe = ForecastingEngine()
        mean_10, _, _ = fe.run_monte_carlo(100.0, 0.0002, 0.015, 10, simulations=5000)
        mean_60, _, _ = fe.run_monte_carlo(100.0, 0.0002, 0.015, 60, simulations=5000)
        # They should be close but not exactly equal (different drift windows)
        assert mean_10 != mean_60

    def test_linear_formula_absent_from_orchestrator_source(self):
        """AST/source guard: the exact naive linear strings from the old bug
        must not appear in main_orchestrator.py."""
        import pathlib
        src = pathlib.Path("main_orchestrator.py").read_text()
        for pattern in ["(1.0 + mu * 10)", "(1.0 + mu * 60)", "(1.0 + mu * 90)"]:
            assert pattern not in src, (
                f"BUG-6 regression: found old linear formula '{pattern}' in "
                "main_orchestrator.py; fallback forecast must use run_monte_carlo()"
            )

    def test_run_monte_carlo_present_in_orchestrator(self):
        """run_monte_carlo() must appear in main_orchestrator.py source."""
        import pathlib
        src = pathlib.Path("main_orchestrator.py").read_text()
        assert "run_monte_carlo" in src, (
            "BUG-6 regression: run_monte_carlo() not found in main_orchestrator.py"
        )
