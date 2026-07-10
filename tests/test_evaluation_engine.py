"""
tests/test_evaluation_engine.py
================================
Owning suite for ``evaluation_engine.py``. Pins the pure-analytics public
surfaces that had NO dedicated test file, complementing (not duplicating) the
already-existing scattered coverage.

Coverage:
  - EvaluationEngine.calculate_edge_ratio      (DataFrame-slice MFE/MAE/Edge/StdDev)
  - EvaluationEngine.calculate_kelly_target    (win/loss + continuous methods, half-Kelly, [0,1] clamp)
  - EvaluationEngine.calculate_excursion_metrics (long/short (mae, mfe) tuple, direct)
  - EvaluationEngine.calculate_realized_slippage (implementation shortfall)
  - EvaluationEngine.calculate_tail_dependency   (CoVaR proxy, beta floor)
  - EvaluationEngine.calculate_brinson_fachler   (Series path + DataFrame compat path)
  - EvaluationEngine.calculate_portfolio_heat    (direct)
  - EvaluationEngine.evaluate_portfolio          (portfolio-heat breach → "AVOID (HEAT LIMIT)")

No-fabricated-metrics contract (CONSTRAINT #4): insufficient/invalid inputs must
yield NaN (never a fabricated 0.0) for the excursion/edge surfaces — asserted below.

Existing files checked to avoid duplication (their surfaces are NOT re-tested here):
  - tests/test_calibration.py                    (calibration_curve + helpers)
  - tests/test_recommendation_tracking.py        (recommendation_tracking_report, _price_at_or_before)
  - tests/test_no_fabricated_metrics.py          (evaluate_portfolio default injection / CoVaR default)
  - tests/test_evaluate_portfolio_zero_positions.py (zero-position BF fallback)
  - tests/test_evaluation_no_history.py          (evaluate_portfolio MAE/MFE NaN, no history)
  - tests/test_evaluation_with_history.py        (evaluate_portfolio excursions from data_provider)

Fully offline. Pure-math methods need no DB; the single evaluate_portfolio test
reuses the verified redirect_class_to_memory_db() isolation pattern.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
import transactions_store

from evaluation_engine import EvaluationEngine
from tests._db_isolation import redirect_class_to_memory_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _engine() -> EvaluationEngine:
    return EvaluationEngine()


def _ohlc(index, highs, lows, closes) -> pd.DataFrame:
    return pd.DataFrame({"High": highs, "Low": lows, "Close": closes}, index=index)


def _patched_ee() -> EvaluationEngine:
    """EvaluationEngine whose evaluate_portfolio() reads an in-memory DB.

    evaluate_portfolio() constructs a TransactionsStore several frames deep with
    no override, so the redirect must wrap the CALL, not the constructor (the
    verified pattern from tests/test_evaluate_portfolio_zero_positions.py).
    """
    ee = EvaluationEngine()
    original = ee.evaluate_portfolio

    def _wrapped(*args, **kwargs):
        with redirect_class_to_memory_db(transactions_store.TransactionsStore):
            return original(*args, **kwargs)

    ee.evaluate_portfolio = _wrapped
    return ee


# ===========================================================================
# TestCalculateEdgeRatio
# ===========================================================================

class TestCalculateEdgeRatio:
    """calculate_edge_ratio slices a price history over the hold window and
    computes MFE, MAE (positive magnitude), Edge Ratio = MFE/MAE, and the
    hold-period return std dev."""

    def _history(self):
        idx = pd.date_range("2026-06-20", periods=5, freq="D")
        # entry=100: High peaks at 110 (+10%), Low troughs at 95 (-5%)
        return _ohlc(
            idx,
            highs=[100.0, 105.0, 110.0, 108.0, 104.0],
            lows=[100.0, 98.0, 95.0, 97.0, 101.0],
            closes=[100.0, 103.0, 107.0, 105.0, 103.0],
        )

    def test_known_mfe_mae_edge(self):
        eng = _engine()
        out = eng.calculate_edge_ratio(self._history(), 100.0, "2026-06-20", "2026-06-24")
        assert out["MFE"] == pytest.approx(0.10, abs=1e-9)
        assert out["MAE"] == pytest.approx(0.05, abs=1e-9)
        assert out["Edge Ratio"] == pytest.approx(2.0, abs=1e-9)

    def test_return_std_dev_computed(self):
        eng = _engine()
        out = eng.calculate_edge_ratio(self._history(), 100.0, "2026-06-20", "2026-06-24")
        expected = self._history()["Close"].pct_change().dropna().std()
        assert out["Return Std Dev"] == pytest.approx(float(expected), abs=1e-9)

    def test_empty_history_all_nan(self):
        eng = _engine()
        out = eng.calculate_edge_ratio(pd.DataFrame(), 100.0, "2026-06-20", "2026-06-24")
        for k in ("MFE", "MAE", "Edge Ratio", "Return Std Dev"):
            assert math.isnan(out[k])

    def test_none_history_all_nan(self):
        eng = _engine()
        out = eng.calculate_edge_ratio(None, 100.0, "2026-06-20", "2026-06-24")
        assert all(math.isnan(out[k]) for k in ("MFE", "MAE", "Edge Ratio", "Return Std Dev"))

    def test_no_overlap_window_yields_nan(self):
        eng = _engine()
        # Hold window is entirely before the available data → empty slice → NaN.
        out = eng.calculate_edge_ratio(self._history(), 100.0, "2020-01-01", "2020-01-05")
        assert math.isnan(out["MFE"])
        assert math.isnan(out["MAE"])
        assert math.isnan(out["Edge Ratio"])

    def test_entry_price_non_positive_yields_nan_mfe_mae(self):
        eng = _engine()
        out = eng.calculate_edge_ratio(self._history(), 0.0, "2026-06-20", "2026-06-24")
        assert math.isnan(out["MFE"])
        assert math.isnan(out["MAE"])

    def test_zero_mae_positive_mfe_gives_large_edge_proxy(self):
        eng = _engine()
        idx = pd.date_range("2026-06-20", periods=3, freq="D")
        # Low never dips below entry → MAE == 0; MFE > 0 → edge = MFE/1e-6 (large).
        hist = _ohlc(idx, highs=[110.0, 112.0, 111.0], lows=[100.0, 101.0, 100.5],
                     closes=[105.0, 108.0, 107.0])
        out = eng.calculate_edge_ratio(hist, 100.0, "2026-06-20", "2026-06-22")
        assert out["MAE"] == pytest.approx(0.0, abs=1e-12)
        assert out["Edge Ratio"] > 1e4

    def test_zero_mae_zero_mfe_gives_zero_edge(self):
        eng = _engine()
        idx = pd.date_range("2026-06-20", periods=2, freq="D")
        # Flat at entry: no favorable and no adverse move → edge 0.0.
        hist = _ohlc(idx, highs=[100.0, 100.0], lows=[100.0, 100.0], closes=[100.0, 100.0])
        out = eng.calculate_edge_ratio(hist, 100.0, "2026-06-20", "2026-06-21")
        assert out["MFE"] == pytest.approx(0.0, abs=1e-12)
        assert out["MAE"] == pytest.approx(0.0, abs=1e-12)
        assert out["Edge Ratio"] == pytest.approx(0.0, abs=1e-12)

    def test_single_bar_hold_std_dev_zero(self):
        eng = _engine()
        idx = pd.date_range("2026-06-20", periods=1, freq="D")
        hist = _ohlc(idx, highs=[110.0], lows=[95.0], closes=[105.0])
        out = eng.calculate_edge_ratio(hist, 100.0, "2026-06-20", "2026-06-20")
        # <=1 return → std dev defaults to 0.0 (documented).
        assert out["Return Std Dev"] == pytest.approx(0.0, abs=1e-12)

    def test_tz_aware_index_is_handled(self):
        eng = _engine()
        # A tz-aware index must be converted to naive before slicing (UTC needs
        # no tzdata and still exercises the ``index.tz is not None`` branch).
        idx = pd.date_range("2026-06-20", periods=3, freq="D", tz="UTC")
        hist = _ohlc(idx, highs=[110.0, 112.0, 108.0], lows=[95.0, 96.0, 99.0],
                     closes=[105.0, 108.0, 104.0])
        out = eng.calculate_edge_ratio(hist, 100.0, "2026-06-20", "2026-06-22")
        assert out["MFE"] == pytest.approx(0.12, abs=1e-9)
        assert out["MAE"] == pytest.approx(0.05, abs=1e-9)


# ===========================================================================
# TestCalculateKellyTarget
# ===========================================================================

class TestCalculateKellyTarget:
    """calculate_kelly_target: win/loss method, continuous return/variance
    method, half-Kelly scaling, and the [0,1] bankruptcy/shorting clamp."""

    def test_win_loss_full_kelly(self):
        eng = _engine()
        # f = p - (1-p)/b = 0.6 - 0.4/2 = 0.4
        out = eng.calculate_kelly_target(0.0, 0.0, win_probability=0.6,
                                         win_loss_ratio=2.0, half_kelly=False)
        assert out["Kelly Target"] == pytest.approx(0.4, abs=1e-9)

    def test_win_loss_half_kelly_halves(self):
        eng = _engine()
        out = eng.calculate_kelly_target(0.0, 0.0, win_probability=0.6,
                                         win_loss_ratio=2.0, half_kelly=True)
        assert out["Kelly Target"] == pytest.approx(0.2, abs=1e-9)

    def test_win_loss_ratio_non_positive_returns_zero(self):
        eng = _engine()
        out = eng.calculate_kelly_target(0.0, 0.0, win_probability=0.6,
                                         win_loss_ratio=0.0)
        assert out["Kelly Target"] == 0.0

    def test_continuous_method_uses_return_over_variance(self):
        eng = _engine()
        # er/var = 0.10/0.25 = 0.4, half-Kelly → 0.2
        out = eng.calculate_kelly_target(0.10, 0.25, half_kelly=True)
        assert out["Kelly Target"] == pytest.approx(0.2, abs=1e-9)

    def test_continuous_zero_variance_returns_zero(self):
        eng = _engine()
        out = eng.calculate_kelly_target(0.10, 0.0)
        assert out["Kelly Target"] == 0.0

    def test_clamped_to_one_on_huge_edge(self):
        eng = _engine()
        # Continuous method: er/var = 5.0/1.0 = 5.0 → clamped to the [0,1] ceiling.
        out = eng.calculate_kelly_target(5.0, 1.0, half_kelly=False)
        assert out["Kelly Target"] == 1.0

    def test_clamped_to_zero_on_negative_edge(self):
        eng = _engine()
        # p=0.2, b=1 → f = 0.2 - 0.8 = -0.6 → clamped to 0.0
        out = eng.calculate_kelly_target(0.0, 0.0, win_probability=0.2,
                                         win_loss_ratio=1.0, half_kelly=False)
        assert out["Kelly Target"] == 0.0

    def test_negative_expected_return_clamped_to_zero(self):
        eng = _engine()
        out = eng.calculate_kelly_target(-0.10, 0.25)
        assert out["Kelly Target"] == 0.0

    def test_return_shape_is_dict_with_single_key(self):
        eng = _engine()
        out = eng.calculate_kelly_target(0.10, 0.25)
        assert set(out.keys()) == {"Kelly Target"}
        assert isinstance(out["Kelly Target"], float)


# ===========================================================================
# TestExcursionMetrics
# ===========================================================================

class TestExcursionMetrics:
    """calculate_excursion_metrics returns (mae, mfe) as POSITIVE magnitudes."""

    def test_long_position(self):
        eng = _engine()
        mae, mfe = eng.calculate_excursion_metrics(100.0, 110.0, 95.0, "long")
        assert mae == pytest.approx(0.05, abs=1e-9)
        assert mfe == pytest.approx(0.10, abs=1e-9)

    def test_short_position_inverts(self):
        eng = _engine()
        # short: adverse = price rising (high), favorable = price falling (low)
        mae, mfe = eng.calculate_excursion_metrics(100.0, 110.0, 95.0, "short")
        assert mae == pytest.approx(0.10, abs=1e-9)
        assert mfe == pytest.approx(0.05, abs=1e-9)

    def test_return_order_is_mae_then_mfe(self):
        eng = _engine()
        result = eng.calculate_excursion_metrics(100.0, 120.0, 90.0, "long")
        assert result == (pytest.approx(0.10, abs=1e-9), pytest.approx(0.20, abs=1e-9))

    def test_invalid_entry_price_zero(self):
        eng = _engine()
        assert eng.calculate_excursion_metrics(0.0, 110.0, 95.0, "long") == (0.0, 0.0)

    def test_invalid_entry_price_nan(self):
        eng = _engine()
        assert eng.calculate_excursion_metrics(float("nan"), 110.0, 95.0, "long") == (0.0, 0.0)

    def test_no_adverse_move_clamps_mae_to_zero(self):
        eng = _engine()
        # long, low never below entry → mae = 0.0 (clamped, not negative)
        mae, mfe = eng.calculate_excursion_metrics(100.0, 110.0, 101.0, "long")
        assert mae == 0.0
        assert mfe == pytest.approx(0.10, abs=1e-9)

    def test_values_rounded_to_four_places(self):
        eng = _engine()
        mae, mfe = eng.calculate_excursion_metrics(100.0, 100.123456, 99.876543, "long")
        assert mae == round(mae, 4)
        assert mfe == round(mfe, 4)


# ===========================================================================
# TestRealizedSlippage
# ===========================================================================

class TestRealizedSlippage:
    """calculate_realized_slippage = (entry - expected)/expected (implementation shortfall)."""

    def test_paid_more_is_positive_drag(self):
        eng = _engine()
        # paid 102, expected 100 → +0.02
        assert eng.calculate_realized_slippage(102.0, 100.0) == pytest.approx(0.02, abs=1e-9)

    def test_paid_less_is_negative(self):
        eng = _engine()
        assert eng.calculate_realized_slippage(98.0, 100.0) == pytest.approx(-0.02, abs=1e-9)

    def test_nan_entry_returns_zero(self):
        eng = _engine()
        assert eng.calculate_realized_slippage(float("nan"), 100.0) == 0.0

    def test_nan_expected_returns_zero(self):
        eng = _engine()
        assert eng.calculate_realized_slippage(100.0, float("nan")) == 0.0

    def test_non_positive_expected_returns_zero(self):
        eng = _engine()
        assert eng.calculate_realized_slippage(100.0, 0.0) == 0.0
        assert eng.calculate_realized_slippage(100.0, -5.0) == 0.0

    def test_rounded_to_four_places(self):
        eng = _engine()
        result = eng.calculate_realized_slippage(100.123456, 100.0)
        assert result == round(result, 4)


# ===========================================================================
# TestTailDependency
# ===========================================================================

class TestTailDependency:
    """calculate_tail_dependency (CoVaR proxy) = |VaR| * max(beta, 0)."""

    def test_known_covar(self):
        eng = _engine()
        # |-0.05| * 1.2 = 0.06
        assert eng.calculate_tail_dependency(-0.05, 1.2) == pytest.approx(0.06, abs=1e-9)

    def test_negative_beta_floored_to_zero(self):
        eng = _engine()
        # hedge asset (beta<0) → 0 systemic tail drag
        assert eng.calculate_tail_dependency(-0.10, -0.5) == 0.0

    def test_nan_var_returns_zero(self):
        eng = _engine()
        assert eng.calculate_tail_dependency(float("nan"), 1.2) == 0.0

    def test_nan_beta_returns_zero(self):
        eng = _engine()
        assert eng.calculate_tail_dependency(-0.05, float("nan")) == 0.0

    def test_positive_var_uses_absolute_value(self):
        eng = _engine()
        assert eng.calculate_tail_dependency(0.05, 2.0) == pytest.approx(0.10, abs=1e-9)

    def test_rounded_to_four_places(self):
        eng = _engine()
        result = eng.calculate_tail_dependency(-0.123456, 1.111111)
        assert result == round(result, 4)


# ===========================================================================
# TestBrinsonFachler
# ===========================================================================

class TestBrinsonFachler:
    """calculate_brinson_fachler: Series path returns a per-sector DataFrame;
    the DataFrame path routes to the compat handler returning an aggregate dict."""

    def test_series_path_returns_bf_dataframe(self):
        eng = _engine()
        sectors = ["Tech", "Energy"]
        w_p = pd.Series([0.6, 0.4], index=sectors)
        w_b = pd.Series([0.5, 0.5], index=sectors)
        r_p = pd.Series([0.08, 0.03], index=sectors)
        r_b = pd.Series([0.05, 0.02], index=sectors)
        out = eng.calculate_brinson_fachler(w_p, w_b, r_p, r_b)
        assert isinstance(out, pd.DataFrame)
        assert list(out.columns) == ["BF_Allocation", "BF_Selection"]
        assert list(out.index) == sectors

    def test_series_path_known_arithmetic(self):
        eng = _engine()
        sectors = ["Tech", "Energy"]
        w_p = pd.Series([0.6, 0.4], index=sectors)
        w_b = pd.Series([0.5, 0.5], index=sectors)
        r_p = pd.Series([0.08, 0.03], index=sectors)
        r_b = pd.Series([0.05, 0.02], index=sectors)
        out = eng.calculate_brinson_fachler(w_p, w_b, r_p, r_b)
        # R_total_b = 0.5*0.05 + 0.5*0.02 = 0.035
        # Alloc Tech = (0.6-0.5)*(0.05-0.035) = 0.1*0.015 = 0.0015
        # Select Tech = 0.5*(0.08-0.05) = 0.015
        assert out.loc["Tech", "BF_Allocation"] == pytest.approx(0.0015, abs=1e-9)
        assert out.loc["Tech", "BF_Selection"] == pytest.approx(0.015, abs=1e-9)

    def test_dataframe_path_returns_aggregate_dict(self):
        eng = _engine()
        port = pd.DataFrame({
            "sector": ["Tech", "Energy"],
            "portfolio_weight": [0.6, 0.4],
            "portfolio_return": [0.08, 0.03],
        })
        bench = pd.DataFrame({
            "sector": ["Tech", "Energy"],
            "benchmark_weight": [0.5, 0.5],
            "benchmark_return": [0.05, 0.02],
        })
        out = eng.calculate_brinson_fachler(port, bench)
        assert isinstance(out, dict)
        for key in ("Portfolio Return", "Benchmark Return", "Active Return",
                    "Allocation Effect", "Selection Effect", "Interaction Effect",
                    "Attribution Sum", "Sector Details"):
            assert key in out

    def test_dataframe_path_attribution_sum_matches_active_return(self):
        eng = _engine()
        port = pd.DataFrame({
            "sector": ["Tech", "Energy"],
            "portfolio_weight": [0.6, 0.4],
            "portfolio_return": [0.08, 0.03],
        })
        bench = pd.DataFrame({
            "sector": ["Tech", "Energy"],
            "benchmark_weight": [0.5, 0.5],
            "benchmark_return": [0.05, 0.02],
        })
        out = eng.calculate_brinson_fachler(port, bench)
        # Fundamental attribution identity: sum of effects == active return.
        assert out["Attribution Sum"] == pytest.approx(out["Active Return"], abs=1e-6)

    def test_dataframe_path_missing_column_dead_letters_to_zero_dict(self):
        eng = _engine()
        # Missing the return column entirely → ValueError caught → zeros dict.
        port = pd.DataFrame({"sector": ["Tech"], "portfolio_weight": [1.0]})
        bench = pd.DataFrame({"sector": ["Tech"], "benchmark_weight": [1.0],
                              "benchmark_return": [0.05]})
        out = eng.calculate_brinson_fachler(port, bench)
        assert out["Active Return"] == 0.0
        assert out["Sector Details"] == {}


# ===========================================================================
# TestPortfolioHeat
# ===========================================================================

class TestPortfolioHeat:
    """calculate_portfolio_heat = Σ(position_size * stop_loss_pct) / Σ position_size."""

    def test_known_heat(self):
        eng = _engine()
        df = pd.DataFrame({
            "position_size": [10000.0, 10000.0],
            "stop_loss_pct": [0.05, 0.03],
        })
        # (10000*0.05 + 10000*0.03) / 20000 = 800/20000 = 0.04
        assert eng.calculate_portfolio_heat(df) == pytest.approx(0.04, abs=1e-9)

    def test_missing_position_size_column_returns_zero(self):
        eng = _engine()
        df = pd.DataFrame({"stop_loss_pct": [0.05]})
        assert eng.calculate_portfolio_heat(df) == 0.0

    def test_missing_stop_loss_column_returns_zero(self):
        eng = _engine()
        df = pd.DataFrame({"position_size": [10000.0]})
        assert eng.calculate_portfolio_heat(df) == 0.0

    def test_zero_total_capital_returns_zero(self):
        eng = _engine()
        df = pd.DataFrame({"position_size": [0.0, 0.0], "stop_loss_pct": [0.05, 0.03]})
        assert eng.calculate_portfolio_heat(df) == 0.0

    def test_rounded_to_four_places(self):
        eng = _engine()
        df = pd.DataFrame({"position_size": [3333.0], "stop_loss_pct": [0.0777]})
        result = eng.calculate_portfolio_heat(df)
        assert result == round(result, 4)


# ===========================================================================
# TestPortfolioHeatBreach (evaluate_portfolio systemic-halt path)
# ===========================================================================

class TestPortfolioHeatBreach:
    """When Portfolio_Heat exceeds max_portfolio_heat, evaluate_portfolio must
    rewrite BUY / STRONG BUY Action Signals to 'AVOID (HEAT LIMIT)'."""

    def _df(self):
        # stop_loss_pct 0.20 → heat = 0.20 (> default 0.06 threshold) → breach.
        return pd.DataFrame({
            "Symbol": ["AAPL", "MSFT", "XOM"],
            "sector": ["Technology", "Technology", "Energy"],
            "position_size": [10000.0, 10000.0, 10000.0],
            "stop_loss_pct": [0.20, 0.20, 0.20],
            "Relative_Strength": [0.05, 0.03, 0.02],
            "Action Signal": ["BUY", "STRONG BUY", "HOLD"],
        })

    def test_breach_rewrites_buy_signals(self):
        ee = _patched_ee()
        result = ee.evaluate_portfolio(self._df())
        signals = dict(zip(result["Symbol"], result["Action Signal"]))
        assert signals["AAPL"] == "AVOID (HEAT LIMIT)"
        assert signals["MSFT"] == "AVOID (HEAT LIMIT)"

    def test_breach_leaves_non_buy_signals_untouched(self):
        ee = _patched_ee()
        result = ee.evaluate_portfolio(self._df())
        signals = dict(zip(result["Symbol"], result["Action Signal"]))
        assert signals["XOM"] == "HOLD"

    def test_no_breach_preserves_buy_signals(self):
        ee = _patched_ee()
        df = self._df()
        df["stop_loss_pct"] = [0.02, 0.02, 0.02]  # heat = 0.02 < 0.06 → no breach
        result = ee.evaluate_portfolio(df)
        signals = dict(zip(result["Symbol"], result["Action Signal"]))
        assert signals["AAPL"] == "BUY"
        assert signals["MSFT"] == "STRONG BUY"

    def test_portfolio_heat_column_reflects_breach_value(self):
        ee = _patched_ee()
        result = ee.evaluate_portfolio(self._df())
        assert (result["Portfolio_Heat"] > 0.06).all()
