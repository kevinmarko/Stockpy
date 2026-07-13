"""Tests for ``ml.feature_engineering.compute_multifactor_zscores`` — the
training-panel Z-score primitive that MUST mirror
``signals/multifactor.py::MultifactorSignal.pre_compute``'s live-inference
formula exactly (a formula drift here is a silent train/serve skew bug).

All offline; no network, no DB.
"""
from __future__ import annotations

import types

import numpy as np
import pandas as pd
import pytest

from ml.feature_engineering import compute_multifactor_zscores
from signals.multifactor import MultifactorSignal

_COLS = ["Value_Z", "Quality_Z", "LowVol_Z", "Size_Z", "Multifactor_Composite"]


def _synthetic_universe(n: int = 20, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    tickers = [f"T{i:02d}" for i in range(n)]
    return pd.DataFrame(
        {
            "book_to_market": rng.normal(0.5, 0.2, n),
            "earnings_yield": rng.normal(0.05, 0.02, n),
            "quality_factor_score": rng.normal(0.1, 0.05, n),
            "low_vol_score": rng.normal(-0.2, 0.05, n),
            "log_market_cap": rng.normal(22.0, 1.5, n),
            "market_cap": rng.uniform(5e8, 5e11, n),
        },
        index=pd.Index(tickers, name="ticker"),
    )


def _run_live_multifactor(universe_df: pd.DataFrame) -> dict:
    """Run the REAL live-pipeline path (MultifactorSignal.pre_compute) over
    the same raw inputs, reshaped to the Symbol/Market-Cap shape it expects,
    for parity comparison against compute_multifactor_zscores.

    ``pre_compute`` only WRITES to ``context.multifactor_scores`` -- it never
    reads ``context.bar``/``fundamentals``/``macro`` -- so a duck-typed
    SimpleNamespace stands in for a real SignalContext without needing to
    satisfy MarketBarDTO/FundamentalDataDTO/MacroEconomicDTO's many required
    constructor args (Python doesn't enforce the type hint at runtime).
    """
    live_df = universe_df.reset_index().rename(columns={"ticker": "Symbol"})
    live_df["Market Cap"] = live_df["market_cap"]
    context = types.SimpleNamespace(multifactor_scores={})
    MultifactorSignal().pre_compute(live_df, context)
    return context.multifactor_scores


class TestComputeMultifactorZscores:
    def test_matches_live_pipeline_formula_exactly(self):
        universe_df = _synthetic_universe()
        result = compute_multifactor_zscores(universe_df)
        live_scores = _run_live_multifactor(universe_df)

        assert set(result.columns) == set(_COLS)
        for ticker in universe_df.index:
            live = live_scores[ticker]
            for col in _COLS:
                got = result.loc[ticker, col]
                want = live[col]
                if pd.isna(want):
                    assert pd.isna(got), f"{ticker}/{col}: expected NaN, got {got}"
                else:
                    assert got == pytest.approx(want, abs=1e-9), (
                        f"{ticker}/{col}: training-panel Z-score {got} != "
                        f"live-pipeline Z-score {want} (formula drift!)"
                    )

    def test_empty_frame_returns_empty_with_correct_columns(self):
        result = compute_multifactor_zscores(pd.DataFrame())
        assert list(result.columns) == _COLS
        assert result.empty

    def test_missing_columns_degrade_to_all_nan_not_fabricated(self):
        # Realistic scenario: price-derived columns present (e.g. from
        # _pit_ticker_row), but the fundamental columns are absent entirely
        # (historical_store=False, or no PIT filing exists yet).
        universe_df = pd.DataFrame(
            {"ROC_12M": [0.1, 0.2, -0.05]},
            index=pd.Index(["AAA", "BBB", "CCC"], name="ticker"),
        )
        result = compute_multifactor_zscores(universe_df)
        assert result.shape == (3, 5)
        assert result.isna().all().all()

    def test_microcap_excluded_gets_all_nan(self):
        universe_df = _synthetic_universe(n=10)
        # Force one ticker below the microcap threshold.
        from settings import settings
        universe_df.loc["T00", "market_cap"] = settings.MULTIFACTOR_MICROCAP_THRESHOLD / 2.0

        result = compute_multifactor_zscores(universe_df)
        assert result.loc["T00"].isna().all()
        # A normal-cap ticker still gets real scores.
        assert result.loc["T01"].notna().any()

    def test_microcap_exclusion_does_not_skew_eligible_populations_stats(self):
        """An excluded microcap must not contribute to the mean/std used to
        Z-score everyone else -- matches the live pipeline's documented
        invariant (CLAUDE.md multifactor.py entry)."""
        universe_df = _synthetic_universe(n=15)
        from settings import settings
        # Push one ticker's book_to_market to an extreme value AND make it a
        # microcap -- if it were NOT excluded from the population, it would
        # blow out the mean/std and change every other ticker's Value_Z.
        universe_df.loc["T00", "book_to_market"] = 500.0
        universe_df.loc["T00", "market_cap"] = settings.MULTIFACTOR_MICROCAP_THRESHOLD / 2.0

        with_extreme_microcap = compute_multifactor_zscores(universe_df)

        universe_df_no_outlier = universe_df.drop(index="T00")
        without_it = compute_multifactor_zscores(universe_df_no_outlier)

        for ticker in universe_df_no_outlier.index:
            pd.testing.assert_series_equal(
                with_extreme_microcap.loc[ticker], without_it.loc[ticker],
                check_names=False, atol=1e-9,
            )

    def test_single_ticker_insufficient_for_zscore_all_nan(self):
        universe_df = _synthetic_universe(n=1)
        result = compute_multifactor_zscores(universe_df)
        # _zscore_winsorize requires >=2 valid observations.
        assert result.isna().all().all()
