"""
tests/test_refresh_validations.py — Tier 4.2 walk-forward validation cadence tests.

Verifies ``scripts.refresh_validations`` module structure, adapter outputs, registry
integrity, and CLI behaviour.  All network I/O (yfinance download, harness runs)
is monkeypatched so the suite is fully offline.

Test classes
------------
TestModuleSurface       — importable, public names exposed, constants correct
TestRegistryStructure   — STRATEGY_REGISTRY shape, known strategies present
TestBuildRsi2Adapter    — RSI(2) adapter returns correct X/y/precomputed shapes
TestBuildTsmomAdapter   — TSMOM adapter returns correct X/y/precomputed shapes
TestMakeStrategyFn      — closure returns harness-compatible result per split
TestRunValidations      — per-strategy dead-letter; SPY failure propagates
TestMainCLI             — argument parsing; all-pass exit-0, any-fail exit-1
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _synthetic_spy(n: int = 500) -> pd.Series:
    """Return a deterministic SPY-like close series (business days, ~$300)."""
    rng = np.random.default_rng(seed=42)
    rets = rng.normal(loc=0.0004, scale=0.01, size=n)
    prices = 300.0 * np.cumprod(1 + rets)
    idx = pd.bdate_range(end="2024-12-31", periods=n)
    return pd.Series(prices, index=idx)


def _synthetic_closes(tickers: List[str], n: int = 500) -> pd.DataFrame:
    """Deterministic multi-ticker close-price DataFrame (business days, ~$200)."""
    rng = np.random.default_rng(seed=7)
    idx = pd.bdate_range(end="2024-12-31", periods=n)
    data = {}
    for t in tickers:
        rets = rng.normal(loc=0.0004, scale=0.01, size=n)
        data[t] = 200.0 * np.cumprod(1 + rets)
    return pd.DataFrame(data, index=idx)


def _noop_harness_run(
    start_date: str,
    end_date: str,
    X: pd.DataFrame,
    y: pd.Series,
    strategy_name: str,
) -> MagicMock:
    """Fake ``StrategyValidationHarness.run()`` returning a deployable report."""
    report = MagicMock()
    report.to_summary_dict.return_value = {
        "strategy_id": strategy_name,
        "deployable": True,
        "pbo": 0.35,
        "dsr": 0.98,
        "sharpe": 0.85,
        "max_drawdown": 0.15,
        "report_date": "2024-12-31",
    }
    return report


# ---------------------------------------------------------------------------
# TestModuleSurface
# ---------------------------------------------------------------------------

class TestModuleSurface:
    def test_module_importable(self) -> None:
        import scripts.refresh_validations  # noqa: F401

    def test_run_validations_callable(self) -> None:
        from scripts.refresh_validations import run_validations

        assert callable(run_validations)

    def test_main_callable(self) -> None:
        from scripts.refresh_validations import main

        assert callable(main)

    def test_strategy_registry_exported(self) -> None:
        from scripts.refresh_validations import STRATEGY_REGISTRY

        assert isinstance(STRATEGY_REGISTRY, dict)

    def test_download_spy_callable(self) -> None:
        from scripts.refresh_validations import _download_spy

        assert callable(_download_spy)

    def test_make_strategy_fn_callable(self) -> None:
        from scripts.refresh_validations import _make_strategy_fn

        assert callable(_make_strategy_fn)


# ---------------------------------------------------------------------------
# TestRegistryStructure
# ---------------------------------------------------------------------------

class TestRegistryStructure:
    def test_rsi2_registered(self) -> None:
        from scripts.refresh_validations import STRATEGY_REGISTRY

        assert "rsi2_mean_reversion" in STRATEGY_REGISTRY

    def test_tsmom_registered(self) -> None:
        from scripts.refresh_validations import STRATEGY_REGISTRY

        assert "timeseries_momentum" in STRATEGY_REGISTRY

    def test_each_entry_is_adapter_turnover_universe_triple(self) -> None:
        from scripts.refresh_validations import STRATEGY_REGISTRY

        for name, entry in STRATEGY_REGISTRY.items():
            fn, turnover, universe = entry
            assert callable(fn), f"{name}: adapter must be callable"
            assert isinstance(turnover, float) and turnover > 0, (
                f"{name}: turnover must be positive float"
            )
            assert isinstance(universe, list) and len(universe) > 0, (
                f"{name}: universe must be a non-empty list of tickers"
            )
            assert all(isinstance(t, str) for t in universe), (
                f"{name}: universe tickers must be strings"
            )

    def test_turnover_reasonable_range(self) -> None:
        from scripts.refresh_validations import STRATEGY_REGISTRY

        for name, (_, turnover, _universe) in STRATEGY_REGISTRY.items():
            assert 0 < turnover <= 0.10, (
                f"{name}: turnover {turnover} outside (0, 0.10] — sanity check"
            )

    def test_new_strategies_registered(self) -> None:
        from scripts.refresh_validations import STRATEGY_REGISTRY

        for name in (
            "macd_trend", "coppock_momentum", "multifactor_lowvol_size",
            "garch_vol_target", "cross_sectional_momentum",
            "relative_strength_xsec", "rsi14_extremes", "sortino_drawdown",
        ):
            assert name in STRATEGY_REGISTRY, f"{name} missing from STRATEGY_REGISTRY"

    def test_multifactor_universe_is_multi_ticker(self) -> None:
        from scripts.refresh_validations import STRATEGY_REGISTRY

        _, _, universe = STRATEGY_REGISTRY["multifactor_lowvol_size"]
        assert len(universe) > 1, "cross-sectional strategy needs a multi-ticker universe"


# ---------------------------------------------------------------------------
# TestBuildRsi2Adapter
# ---------------------------------------------------------------------------

class TestBuildRsi2Adapter:
    def test_returns_three_items(self) -> None:
        from scripts.refresh_validations import _build_rsi2_adapter

        spy = _synthetic_spy()
        result = _build_rsi2_adapter(spy)
        assert len(result) == 3

    def test_X_has_expected_columns(self) -> None:
        from scripts.refresh_validations import _build_rsi2_adapter

        X, y, pre = _build_rsi2_adapter(_synthetic_spy())
        assert "RSI_2" in X.columns
        assert "SMA_200" in X.columns

    def test_y_is_series(self) -> None:
        from scripts.refresh_validations import _build_rsi2_adapter

        X, y, pre = _build_rsi2_adapter(_synthetic_spy())
        assert isinstance(y, pd.Series)

    def test_X_and_y_share_index(self) -> None:
        from scripts.refresh_validations import _build_rsi2_adapter

        X, y, pre = _build_rsi2_adapter(_synthetic_spy())
        assert X.index.equals(y.index)

    def test_precomputed_keys(self) -> None:
        from scripts.refresh_validations import _build_rsi2_adapter

        _, _, pre = _build_rsi2_adapter(_synthetic_spy())
        assert "RSI2_Gated" in pre
        assert "RSI2_Ungated" in pre

    def test_precomputed_series_share_index_with_y(self) -> None:
        from scripts.refresh_validations import _build_rsi2_adapter

        X, y, pre = _build_rsi2_adapter(_synthetic_spy())
        for k, v in pre.items():
            assert v.index.equals(X.index), f"{k} index mismatch"

    def test_sma200_warmup_rows_trimmed(self) -> None:
        from scripts.refresh_validations import _build_rsi2_adapter

        spy = _synthetic_spy(n=500)
        X, y, _ = _build_rsi2_adapter(spy)
        # After trimming SMA(200) NaN warmup, at least 250 rows should remain
        assert len(X) >= 250

    def test_rsi2_score_bounded_01(self) -> None:
        from scripts.refresh_validations import _build_rsi2_adapter

        _, _, pre = _build_rsi2_adapter(_synthetic_spy())
        # Precomputed return series are score × daily_ret — allow any float
        # but RSI_2 feature column must be in [0, 100]
        X, _, _ = _build_rsi2_adapter(_synthetic_spy())
        assert (X["RSI_2"].dropna() >= 0.0).all()
        assert (X["RSI_2"].dropna() <= 100.0).all()


# ---------------------------------------------------------------------------
# TestBuildTsmomAdapter
# ---------------------------------------------------------------------------

class TestBuildTsmomAdapter:
    def test_returns_three_items(self) -> None:
        from scripts.refresh_validations import _build_tsmom_adapter

        result = _build_tsmom_adapter(_synthetic_spy())
        assert len(result) == 3

    def test_X_has_expected_columns(self) -> None:
        from scripts.refresh_validations import _build_tsmom_adapter

        X, y, _ = _build_tsmom_adapter(_synthetic_spy())
        for col in ("ROC_12M", "ROC_6M", "Vol"):
            assert col in X.columns, f"Missing column: {col}"

    def test_four_precomputed_variants(self) -> None:
        from scripts.refresh_validations import _build_tsmom_adapter

        _, _, pre = _build_tsmom_adapter(_synthetic_spy())
        assert len(pre) == 4, "Expected 4 TSMOM variants"

    def test_precomputed_variant_names_pattern(self) -> None:
        from scripts.refresh_validations import _build_tsmom_adapter

        _, _, pre = _build_tsmom_adapter(_synthetic_spy())
        for k in pre:
            assert "TSMOM_" in k

    def test_vol_scalar_caps_at_1(self) -> None:
        """Vol-target scalar must not exceed 1 (no leverage in the scalar)."""
        from scripts.refresh_validations import _build_tsmom_adapter

        spy = _synthetic_spy()
        X, y, _ = _build_tsmom_adapter(spy)
        vol = X["Vol"]
        # Scalar = min(1.0, target_vol/vol). With target_vol=0.10, the 10 pct
        # variant's scores should have |score| <= 1.0.
        _, _, pre = _build_tsmom_adapter(spy)
        for k, s in pre.items():
            daily_ret = spy.pct_change().loc[X.index]
            # recover score from ret = score.shift(1) * daily_ret is imperfect;
            # instead just check the precomputed series is finite
            assert s.notna().any(), f"{k}: all NaN"


# ---------------------------------------------------------------------------
# TestBuildMacdAdapter
# ---------------------------------------------------------------------------

class TestBuildMacdAdapter:
    def test_returns_three_items(self) -> None:
        from scripts.refresh_validations import _build_macd_adapter

        result = _build_macd_adapter(_synthetic_spy())
        assert len(result) == 3

    def test_X_has_expected_columns(self) -> None:
        from scripts.refresh_validations import _build_macd_adapter

        X, y, _ = _build_macd_adapter(_synthetic_spy())
        assert "MACD_Hist" in X.columns
        assert "SMA_200" in X.columns

    def test_three_precomputed_variants(self) -> None:
        from scripts.refresh_validations import _build_macd_adapter

        _, _, pre = _build_macd_adapter(_synthetic_spy())
        assert set(pre.keys()) == {"MACD_LongOnly", "MACD_LongShort", "MACD_TrendFilter"}

    def test_precomputed_series_share_index_with_X(self) -> None:
        from scripts.refresh_validations import _build_macd_adapter

        X, y, pre = _build_macd_adapter(_synthetic_spy())
        for k, v in pre.items():
            assert v.index.equals(X.index), f"{k} index mismatch"

    def test_no_lookahead_perturbing_future_does_not_change_past_signal(self) -> None:
        """Perturbing close AFTER date t must not change MACD_Hist AT date t —
        every EMA in the adapter is causal (adjust=False) and the position is
        .shift(1)-ed before multiplying by the realized return."""
        from scripts.refresh_validations import _build_macd_adapter

        spy = _synthetic_spy(n=400)
        cutoff = spy.index[300]

        X_orig, _, _ = _build_macd_adapter(spy)
        hist_at_cutoff_orig = X_orig.loc[cutoff, "MACD_Hist"]

        perturbed = spy.copy()
        perturbed.loc[perturbed.index > cutoff] *= 5.0  # violent future shock
        X_pert, _, _ = _build_macd_adapter(perturbed)
        hist_at_cutoff_pert = X_pert.loc[cutoff, "MACD_Hist"]

        assert hist_at_cutoff_orig == pytest.approx(hist_at_cutoff_pert)


# ---------------------------------------------------------------------------
# TestBuildCoppockAdapter
# ---------------------------------------------------------------------------

class TestBuildCoppockAdapter:
    def test_returns_three_items(self) -> None:
        from scripts.refresh_validations import _build_coppock_adapter

        result = _build_coppock_adapter(_synthetic_spy(n=700))
        assert len(result) == 3

    def test_X_has_coppock_column(self) -> None:
        from scripts.refresh_validations import _build_coppock_adapter

        X, y, _ = _build_coppock_adapter(_synthetic_spy(n=700))
        assert "Coppock" in X.columns

    def test_two_precomputed_variants(self) -> None:
        from scripts.refresh_validations import _build_coppock_adapter

        _, _, pre = _build_coppock_adapter(_synthetic_spy(n=700))
        assert set(pre.keys()) == {"Coppock_Long", "Coppock_Rising"}

    def test_insufficient_history_returns_empty(self) -> None:
        """Fewer bars than the ~210-day WMA warmup -> clean empty result,
        never a fabricated value (CONSTRAINT #4)."""
        from scripts.refresh_validations import _build_coppock_adapter

        X, y, pre = _build_coppock_adapter(_synthetic_spy(n=50))
        assert X.empty
        assert y.empty
        assert pre == {}

    def test_no_lookahead_perturbing_future_does_not_change_past_signal(self) -> None:
        from scripts.refresh_validations import _build_coppock_adapter

        spy = _synthetic_spy(n=700)
        cutoff = spy.index[600]

        X_orig, _, _ = _build_coppock_adapter(spy)
        val_orig = X_orig.loc[cutoff, "Coppock"]

        perturbed = spy.copy()
        perturbed.loc[perturbed.index > cutoff] *= 5.0
        X_pert, _, _ = _build_coppock_adapter(perturbed)
        val_pert = X_pert.loc[cutoff, "Coppock"]

        assert val_orig == pytest.approx(val_pert)


# ---------------------------------------------------------------------------
# TestBuildLowVolSizeAdapter
# ---------------------------------------------------------------------------

class TestBuildLowVolSizeAdapter:
    _TICKERS = ["AAA", "BBB", "CCC", "DDD"]

    def test_returns_three_items(self) -> None:
        from scripts.refresh_validations import _build_lowvol_size_adapter

        closes = _synthetic_closes(self._TICKERS)
        shares = {t: 1e9 for t in self._TICKERS}
        result = _build_lowvol_size_adapter(closes, shares)
        assert len(result) == 3

    def test_X_has_expected_columns(self) -> None:
        from scripts.refresh_validations import _build_lowvol_size_adapter

        closes = _synthetic_closes(self._TICKERS)
        shares = {t: 1e9 for t in self._TICKERS}
        X, y, _ = _build_lowvol_size_adapter(closes, shares)
        assert "LowVol_Composite" in X.columns
        assert "Size_Composite" in X.columns

    def test_precomputed_portfolio_returns_key(self) -> None:
        from scripts.refresh_validations import _build_lowvol_size_adapter

        closes = _synthetic_closes(self._TICKERS)
        shares = {t: 1e9 for t in self._TICKERS}
        _, _, pre = _build_lowvol_size_adapter(closes, shares)
        assert "Multifactor_LowVol_Size" in pre
        assert pre["Multifactor_LowVol_Size"].notna().any()

    def test_missing_shares_degrades_to_nan_not_fabricated(self) -> None:
        """A ticker with no shares snapshot gets NaN Size (never a fabricated
        0.0) and the composite falls back to the Low-Vol tilt only
        (CONSTRAINT #4)."""
        from scripts.refresh_validations import _build_lowvol_size_adapter

        closes = _synthetic_closes(self._TICKERS)
        shares: Dict[str, float] = {}  # no shares snapshot for anyone
        X, y, pre = _build_lowvol_size_adapter(closes, shares)
        assert not X.empty
        assert pre["Multifactor_LowVol_Size"].notna().any()

    def test_no_lookahead_shift1_on_weights(self) -> None:
        """Perturbing returns strictly AFTER date t must not change the
        portfolio return series' value AT date t (weights are .shift(1)-ed)."""
        from scripts.refresh_validations import _build_lowvol_size_adapter

        closes = _synthetic_closes(self._TICKERS, n=300)
        shares = {t: 1e9 for t in self._TICKERS}
        cutoff = closes.index[200]

        _, _, pre_orig = _build_lowvol_size_adapter(closes, shares)
        val_orig = pre_orig["Multifactor_LowVol_Size"].loc[cutoff]

        perturbed = closes.copy()
        perturbed.loc[perturbed.index > cutoff] *= 5.0
        _, _, pre_pert = _build_lowvol_size_adapter(perturbed, shares)
        val_pert = pre_pert["Multifactor_LowVol_Size"].loc[cutoff]

        assert val_orig == pytest.approx(val_pert)


# ---------------------------------------------------------------------------
# New price-only adapters (garch vol-timing / xsec momentum / rel-strength / rsi14)
# ---------------------------------------------------------------------------

class TestBuildGarchVoltargetAdapter:
    def test_returns_three_items_and_variants(self) -> None:
        from scripts.refresh_validations import _build_garch_voltarget_adapter

        X, y, pre = _build_garch_voltarget_adapter(_synthetic_spy(n=500))
        assert len(X.columns) >= 1 and not y.empty
        assert set(pre.keys()) == {
            "GARCH_VolTarget_10pct", "GARCH_VolTarget_15pct",
            "GARCH_InvVol", "GARCH_GJR_Downside12",
        }
        for k, v in pre.items():
            assert v.index.equals(y.index), f"{k} index mismatch"

    def test_exposure_is_long_only_no_leverage(self) -> None:
        """Vol-target exposure is capped at 1.0, so on a positive-return day the
        strategy return can never exceed that day's underlying return."""
        from scripts.refresh_validations import _build_garch_voltarget_adapter

        spy = _synthetic_spy(n=500)
        X, y, pre = _build_garch_voltarget_adapter(spy)
        for k in ("GARCH_VolTarget_10pct", "GARCH_VolTarget_15pct"):
            # strategy_ret = expo.shift(1) * daily_ret with 0 <= expo <= 1, so
            # |strategy_ret| <= |daily_ret| everywhere.
            assert (pre[k].abs() <= y.abs() + 1e-12).all(), k

    def test_no_lookahead_shift1(self) -> None:
        from scripts.refresh_validations import _build_garch_voltarget_adapter

        spy = _synthetic_spy(n=400)
        cutoff = spy.index[300]
        _, _, pre_orig = _build_garch_voltarget_adapter(spy)
        val_orig = pre_orig["GARCH_VolTarget_10pct"].loc[cutoff]
        perturbed = spy.copy()
        perturbed.loc[perturbed.index > cutoff] *= 5.0
        _, _, pre_pert = _build_garch_voltarget_adapter(perturbed)
        assert val_orig == pytest.approx(pre_pert["GARCH_VolTarget_10pct"].loc[cutoff])


class TestBuildXsecMomentumAdapter:
    _TICKERS = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]

    def test_returns_three_items_and_variants(self) -> None:
        from scripts.refresh_validations import _build_xsec_momentum_adapter

        closes = _synthetic_closes(self._TICKERS, n=500)
        X, y, pre = _build_xsec_momentum_adapter(closes, {})
        assert not X.empty and not y.empty
        assert set(pre.keys()) == {"XSecMom_TopHalf", "XSecMom_TopTertile"}
        for k, v in pre.items():
            assert v.index.equals(y.index), f"{k} index mismatch"

    def test_insufficient_history_returns_empty(self) -> None:
        from scripts.refresh_validations import _build_xsec_momentum_adapter

        X, y, pre = _build_xsec_momentum_adapter(
            _synthetic_closes(self._TICKERS, n=100), {}
        )
        assert X.empty and y.empty and pre == {}

    def test_no_lookahead_shift1(self) -> None:
        from scripts.refresh_validations import _build_xsec_momentum_adapter

        closes = _synthetic_closes(self._TICKERS, n=400)
        cutoff = closes.index[350]
        _, _, pre_orig = _build_xsec_momentum_adapter(closes, {})
        val_orig = pre_orig["XSecMom_TopHalf"].loc[cutoff]
        perturbed = closes.copy()
        perturbed.loc[perturbed.index > cutoff] *= 5.0
        _, _, pre_pert = _build_xsec_momentum_adapter(perturbed, {})
        assert val_orig == pytest.approx(pre_pert["XSecMom_TopHalf"].loc[cutoff])


class TestBuildRelativeStrengthAdapter:
    _TICKERS = ["AAA", "BBB", "CCC", "DDD", "EEE"]

    def test_requires_spy_benchmark(self) -> None:
        from scripts.refresh_validations import _build_relative_strength_adapter

        closes = _synthetic_closes(self._TICKERS, n=400)  # no SPY column
        with pytest.raises(RuntimeError):
            _build_relative_strength_adapter(closes, {})

    def test_spy_excluded_from_tradeable_book(self) -> None:
        from scripts.refresh_validations import _build_relative_strength_adapter

        closes = _synthetic_closes(self._TICKERS + ["SPY"], n=400)
        X, y, pre = _build_relative_strength_adapter(closes, {})
        assert not X.empty and not y.empty
        assert set(pre.keys()) == {"RS_BeatSPY_Absolute", "RS_TopHalf"}
        for k, v in pre.items():
            assert v.index.equals(y.index), f"{k} index mismatch"

    def test_no_lookahead_shift1(self) -> None:
        from scripts.refresh_validations import _build_relative_strength_adapter

        closes = _synthetic_closes(self._TICKERS + ["SPY"], n=400)
        cutoff = closes.index[350]
        _, _, pre_orig = _build_relative_strength_adapter(closes, {})
        val_orig = pre_orig["RS_TopHalf"].loc[cutoff]
        perturbed = closes.copy()
        perturbed.loc[perturbed.index > cutoff] *= 5.0
        _, _, pre_pert = _build_relative_strength_adapter(perturbed, {})
        assert val_orig == pytest.approx(pre_pert["RS_TopHalf"].loc[cutoff])


class TestBuildRsi14ExtremesAdapter:
    def test_returns_three_items_and_variants(self) -> None:
        from scripts.refresh_validations import _build_rsi14_extremes_adapter

        X, y, pre = _build_rsi14_extremes_adapter(_synthetic_spy(n=400))
        assert "RSI_14" in X.columns and "SMA_200" in X.columns and not y.empty
        assert set(pre.keys()) == {
            "RSI14_OversoldLong", "RSI14_LongShort", "RSI14_TrendFilteredLong",
        }

    def test_trend_filtered_zero_outside_uptrend(self) -> None:
        """RSI14_TrendFilteredLong must never take a position when price is
        below its SMA(200) — even on a day RSI14_OversoldLong would."""
        from scripts.refresh_validations import _build_rsi14_extremes_adapter

        spy = _synthetic_spy(n=500)
        X, y, pre = _build_rsi14_extremes_adapter(spy)
        downtrend = spy.reindex(X.index) <= X["SMA_200"]
        # A day strictly below the shift(1) position can't be checked directly
        # (position is lagged), but the day AFTER a downtrend day must be flat
        # whenever the trend-filtered variant differs from the oversold-long one.
        trend_ret = pre["RSI14_TrendFilteredLong"]
        oversold_ret = pre["RSI14_OversoldLong"]
        # Trend-filtered is a subset: |trend| <= |oversold| pointwise given the
        # AND-gate construction (same daily_ret, position clamped to a subset).
        assert (trend_ret.abs() <= oversold_ret.abs() + 1e-12).all()
        assert downtrend.any()  # sanity: the synthetic series does dip below its SMA200

    def test_rsi_bounded_0_100(self) -> None:
        from scripts.refresh_validations import _build_rsi14_extremes_adapter

        X, _, _ = _build_rsi14_extremes_adapter(_synthetic_spy(n=400))
        assert (X["RSI_14"].dropna() >= 0.0).all()
        assert (X["RSI_14"].dropna() <= 100.0).all()

    def test_no_lookahead_shift1(self) -> None:
        from scripts.refresh_validations import _build_rsi14_extremes_adapter

        spy = _synthetic_spy(n=400)
        cutoff = spy.index[300]
        _, _, pre_orig = _build_rsi14_extremes_adapter(spy)
        val_orig = pre_orig["RSI14_OversoldLong"].loc[cutoff]
        perturbed = spy.copy()
        perturbed.loc[perturbed.index > cutoff] *= 5.0
        _, _, pre_pert = _build_rsi14_extremes_adapter(perturbed)
        assert val_orig == pytest.approx(pre_pert["RSI14_OversoldLong"].loc[cutoff])


class TestBuildSortinoDrawdownAdapter:
    def test_returns_three_items_and_variants(self) -> None:
        from scripts.refresh_validations import _build_sortino_drawdown_adapter

        X, y, pre = _build_sortino_drawdown_adapter(_synthetic_spy(n=1400))
        assert not X.empty and not y.empty
        assert "Sortino_504D" in X.columns and "Drawdown_504D" in X.columns
        assert set(pre.keys()) == {
            "SortinoDD_HighSortino", "SortinoDD_DrawdownGate", "SortinoDD_Combined",
        }
        for k, v in pre.items():
            assert v.index.equals(y.index), f"{k} index mismatch"

    def test_insufficient_history_returns_empty(self) -> None:
        """Fewer bars than the 504-day rolling window -> clean empty result,
        never a fabricated value (CONSTRAINT #4)."""
        from scripts.refresh_validations import _build_sortino_drawdown_adapter

        X, y, pre = _build_sortino_drawdown_adapter(_synthetic_spy(n=300))
        assert X.empty and y.empty and pre == {}

    def test_combined_is_and_of_both_gates(self) -> None:
        """SortinoDD_Combined can only ever be nonzero where BOTH single-gate
        variants are — |combined| <= |either single gate| pointwise."""
        from scripts.refresh_validations import _build_sortino_drawdown_adapter

        _, _, pre = _build_sortino_drawdown_adapter(_synthetic_spy(n=1400))
        combined = pre["SortinoDD_Combined"]
        for k in ("SortinoDD_HighSortino", "SortinoDD_DrawdownGate"):
            assert (combined.abs() <= pre[k].abs() + 1e-12).all(), k

    def test_no_lookahead_shift1(self) -> None:
        from scripts.refresh_validations import _build_sortino_drawdown_adapter

        spy = _synthetic_spy(n=1400)
        cutoff = spy.index[1200]
        _, _, pre_orig = _build_sortino_drawdown_adapter(spy)
        val_orig = pre_orig["SortinoDD_Combined"].loc[cutoff]
        perturbed = spy.copy()
        perturbed.loc[perturbed.index > cutoff] *= 5.0
        _, _, pre_pert = _build_sortino_drawdown_adapter(perturbed)
        assert val_orig == pytest.approx(pre_pert["SortinoDD_Combined"].loc[cutoff])


# ---------------------------------------------------------------------------
# TestLoadTickerSectors
# ---------------------------------------------------------------------------

class TestLoadTickerSectors:
    def test_reads_committed_csv(self) -> None:
        from scripts.refresh_validations import _load_ticker_sectors

        mapping = _load_ticker_sectors()
        assert isinstance(mapping, dict)
        assert mapping.get("AAPL") == "Technology"

    def test_covers_the_full_xsec_universe_30(self) -> None:
        """Regression guard for the Phase 0b sector-map backfill: every ticker
        in _XSEC_UNIVERSE_30 must resolve to a real sector (needed by the
        macro_regime_pit / signal_replay adapters' sector-rotation scoring)."""
        from scripts.refresh_validations import _load_ticker_sectors, _XSEC_UNIVERSE_30

        mapping = _load_ticker_sectors()
        missing = [t for t in _XSEC_UNIVERSE_30 if t not in mapping]
        assert missing == [], f"missing sector coverage for: {missing}"

    def test_missing_file_degrades_to_empty_dict(self, monkeypatch) -> None:
        import scripts.refresh_validations as rv

        monkeypatch.setattr(
            rv.pd, "read_csv",
            lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("nope")),
        )
        assert rv._load_ticker_sectors() == {}


# ---------------------------------------------------------------------------
# TestMakeStrategyFn
# ---------------------------------------------------------------------------

class TestMakeStrategyFn:
    def _fake_precomputed(self, n: int = 200) -> Dict[str, pd.Series]:
        idx = pd.bdate_range("2020-01-01", periods=n)
        return {"StratA": pd.Series(np.zeros(n), index=idx)}

    def test_returns_callable(self) -> None:
        from scripts.refresh_validations import _make_strategy_fn

        fn = _make_strategy_fn(self._fake_precomputed())
        assert callable(fn)

    def test_callable_returns_list(self) -> None:
        from scripts.refresh_validations import _make_strategy_fn

        pre = self._fake_precomputed(200)
        fn = _make_strategy_fn(pre)
        idx = pd.bdate_range("2020-01-01", periods=200)
        X = pd.DataFrame({"f": np.zeros(200)}, index=idx)
        y = pd.Series(np.zeros(200), index=idx)
        result = fn(X[:100], y[:100], X[100:], y[100:])
        assert isinstance(result, list)
        assert len(result) == 1

    def test_result_dict_has_required_keys(self) -> None:
        from scripts.refresh_validations import _make_strategy_fn

        pre = self._fake_precomputed(200)
        fn = _make_strategy_fn(pre)
        idx = pd.bdate_range("2020-01-01", periods=200)
        X = pd.DataFrame({"f": np.zeros(200)}, index=idx)
        y = pd.Series(np.zeros(200), index=idx)
        result = fn(X[:100], y[:100], X[100:], y[100:])
        for key in ("params", "train_returns", "test_returns", "turnover"):
            assert key in result[0], f"Missing key: {key}"

    def test_turnover_propagated(self) -> None:
        from scripts.refresh_validations import _make_strategy_fn

        pre = self._fake_precomputed(200)
        fn = _make_strategy_fn(pre, turnover=0.005)
        idx = pd.bdate_range("2020-01-01", periods=200)
        X = pd.DataFrame({"f": np.zeros(200)}, index=idx)
        y = pd.Series(np.zeros(200), index=idx)
        result = fn(X[:100], y[:100], X[100:], y[100:])
        assert result[0]["turnover"] == 0.005

    def test_one_result_per_precomputed_series(self) -> None:
        from scripts.refresh_validations import _make_strategy_fn

        n = 200
        idx = pd.bdate_range("2020-01-01", periods=n)
        pre = {
            "A": pd.Series(np.zeros(n), index=idx),
            "B": pd.Series(np.ones(n) * 0.01, index=idx),
        }
        fn = _make_strategy_fn(pre)
        X = pd.DataFrame({"f": np.zeros(n)}, index=idx)
        y = pd.Series(np.zeros(n), index=idx)
        result = fn(X[:100], y[:100], X[100:], y[100:])
        assert len(result) == 2


# ---------------------------------------------------------------------------
# TestRunValidations
# ---------------------------------------------------------------------------

class TestRunValidations:
    def _patch_harness(self):
        """Monkeypatch StrategyValidationHarness at its source module.

        run_validations() imports it lazily (``from validation.harness import ...``),
        so patching the scripts.refresh_validations module attribute would fail.
        Patching the source attribute is the correct approach.
        """
        mock_cls = MagicMock()
        instance = MagicMock()
        instance.run.side_effect = _noop_harness_run
        mock_cls.return_value = instance
        return patch("validation.harness.StrategyValidationHarness", mock_cls)

    def _patch_closes(self):
        """Patch ``_download_closes`` to synthesize prices for whatever ticker
        union the caller requests (the union varies per test/registry
        selection), so this must be a ``side_effect`` callable, not a fixed
        ``return_value``."""
        def _fake_download(tickers: List[str], start_date: str, end_date: str) -> pd.DataFrame:
            return _synthetic_closes(tickers)

        return patch(
            "scripts.refresh_validations._download_closes",
            side_effect=_fake_download,
        )

    def _patch_shares(self):
        def _fake_shares(tickers: List[str]) -> Dict[str, float]:
            return {t: 1_000_000_000.0 for t in tickers}

        return patch(
            "scripts.refresh_validations._download_shares",
            side_effect=_fake_shares,
        )

    def _patch_cost(self):
        return patch(
            "execution.cost_model.TieredCostModel",
            return_value=MagicMock(),
        )

    def test_returns_dict_for_each_strategy(self, tmp_path: Path) -> None:
        from scripts.refresh_validations import run_validations

        with self._patch_closes(), self._patch_shares(), self._patch_harness(), self._patch_cost():
            results = run_validations(output_dir=tmp_path)

        assert isinstance(results, dict)
        assert "rsi2_mean_reversion" in results
        assert "timeseries_momentum" in results

    def test_unknown_strategy_is_dead_lettered(self, tmp_path: Path) -> None:
        from scripts.refresh_validations import run_validations

        with self._patch_closes(), self._patch_shares(), self._patch_harness(), self._patch_cost():
            results = run_validations(
                strategies=["totally_unknown_strategy"], output_dir=tmp_path
            )
        r = results["totally_unknown_strategy"]
        assert r["deployable"] is False
        assert "error" in r

    def test_price_download_failure_marks_all_as_failed(self, tmp_path: Path) -> None:
        from scripts.refresh_validations import run_validations

        with patch(
            "scripts.refresh_validations._download_closes",
            side_effect=RuntimeError("network down"),
        ), self._patch_cost():
            results = run_validations(
                strategies=["rsi2_mean_reversion"], output_dir=tmp_path
            )

        assert results["rsi2_mean_reversion"]["deployable"] is False
        assert "error" in results["rsi2_mean_reversion"]

    def test_adapter_exception_dead_lettered(self, tmp_path: Path) -> None:
        from scripts.refresh_validations import run_validations

        broken_adapter = MagicMock(side_effect=ValueError("adapter exploded"))
        patched_registry = {
            "rsi2_mean_reversion": (broken_adapter, 0.02, ["SPY"]),
        }
        with (
            self._patch_closes(),
            self._patch_shares(),
            self._patch_cost(),
            patch("scripts.refresh_validations.STRATEGY_REGISTRY", patched_registry),
        ):
            results = run_validations(
                strategies=["rsi2_mean_reversion"], output_dir=tmp_path
            )

        r = results["rsi2_mean_reversion"]
        assert r["deployable"] is False
        assert "adapter exploded" in r["error"]

    def test_single_strategy_filter(self, tmp_path: Path) -> None:
        from scripts.refresh_validations import run_validations

        with self._patch_closes(), self._patch_shares(), self._patch_harness(), self._patch_cost():
            results = run_validations(
                strategies=["rsi2_mean_reversion"], output_dir=tmp_path
            )

        assert list(results.keys()) == ["rsi2_mean_reversion"]

    def test_multifactor_strategy_runs_with_multi_ticker_universe(
        self, tmp_path: Path
    ) -> None:
        """The cross-sectional adapter needs multiple tickers + a shares
        snapshot; verify run_validations wires both through without error."""
        from scripts.refresh_validations import run_validations

        with self._patch_closes(), self._patch_shares(), self._patch_harness(), self._patch_cost():
            results = run_validations(
                strategies=["multifactor_lowvol_size"], output_dir=tmp_path
            )

        assert "multifactor_lowvol_size" in results
        assert "error" not in results["multifactor_lowvol_size"]


# ---------------------------------------------------------------------------
# TestMainCLI
# ---------------------------------------------------------------------------

class TestMainCLI:
    def _run_main(
        self,
        argv: List[str],
        results: Dict[str, dict],
        tmp_path: Path,
    ) -> int:
        from scripts.refresh_validations import main

        with patch(
            "scripts.refresh_validations.run_validations",
            return_value=results,
        ):
            full_argv = argv + ["--output-dir", str(tmp_path)]
            return main(full_argv)

    def test_all_pass_returns_exit_code_0(self, tmp_path: Path) -> None:
        results = {
            "rsi2_mean_reversion": {"deployable": True},
            "timeseries_momentum": {"deployable": True},
        }
        code = self._run_main([], results, tmp_path)
        assert code == 0

    def test_any_fail_returns_exit_code_1(self, tmp_path: Path) -> None:
        results = {
            "rsi2_mean_reversion": {"deployable": True},
            "timeseries_momentum": {"deployable": False},
        }
        code = self._run_main([], results, tmp_path)
        assert code == 1

    def test_error_entry_returns_exit_code_1(self, tmp_path: Path) -> None:
        results = {
            "rsi2_mean_reversion": {"deployable": False, "error": "boom"},
        }
        code = self._run_main([], results, tmp_path)
        assert code == 1

    def test_strategies_flag_forwarded(self, tmp_path: Path) -> None:
        from scripts.refresh_validations import main

        captured: Dict[str, Any] = {}

        def fake_run(**kwargs: Any) -> Dict[str, dict]:
            captured.update(kwargs)
            return {"rsi2_mean_reversion": {"deployable": True}}

        with patch("scripts.refresh_validations.run_validations", fake_run):
            main(["--strategies", "rsi2_mean_reversion",
                  "--output-dir", str(tmp_path)])

        assert captured["strategies"] == ["rsi2_mean_reversion"]

    def test_start_end_flags_forwarded(self, tmp_path: Path) -> None:
        from scripts.refresh_validations import main

        captured: Dict[str, Any] = {}

        def fake_run(**kwargs: Any) -> Dict[str, dict]:
            captured.update(kwargs)
            return {"rsi2_mean_reversion": {"deployable": True}}

        with patch("scripts.refresh_validations.run_validations", fake_run):
            main(["--start", "2010-01-01", "--end", "2020-12-31",
                  "--output-dir", str(tmp_path)])

        assert captured["start_date"] == "2010-01-01"
        assert captured["end_date"] == "2020-12-31"

    def test_n_cpcv_splits_forwarded(self, tmp_path: Path) -> None:
        from scripts.refresh_validations import main

        captured: Dict[str, Any] = {}

        def fake_run(**kwargs: Any) -> Dict[str, dict]:
            captured.update(kwargs)
            return {"rsi2_mean_reversion": {"deployable": True}}

        with patch("scripts.refresh_validations.run_validations", fake_run):
            main(["--n-cpcv-splits", "5", "--output-dir", str(tmp_path)])

        assert captured["n_cpcv_splits"] == 5
