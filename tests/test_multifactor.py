"""
InvestYo Quant Platform - Multifactor Signal Tests
=====================================================
Unit tests for signals/multifactor.py: cross-sectional z-scoring,
winsorization, microcap exclusion, and the [-1, +1] composite score mapping.
"""

import math
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from signals.multifactor import MultifactorSignal, _zscore_winsorize, WINSOR_LIMIT
from signals.base import SignalContext
from dto_models import MarketBarDTO, FundamentalDataDTO, MacroEconomicDTO


def _make_context() -> SignalContext:
    bar = MarketBarDTO(datetime.now(), "TEST", 100.0, 100.0, 100.0, 100.0, 1000)
    fund = FundamentalDataDTO(
        ticker="TEST", pe_ratio=None, pb_ratio=None, dividend_yield=0.0,
        book_value=0.0, eps_trailing=0.0, dividend_growth_rate=0.0,
        payout_ratio=0.0, sector="Unknown", company_name="Unknown",
    )
    macro = MacroEconomicDTO(
        yield_curve_10y_2y=0.5, high_yield_oas=2.0, inflation_rate=0.03,
        vix_value=15.0,
    )
    return SignalContext(bar=bar, fundamentals=fund, macro=macro)


def _synthetic_universe(n_good: int = 10, n_rest: int = 40, seed: int = 7) -> pd.DataFrame:
    """50-stock universe: n_good tickers engineered as high-value, high-quality,
    low-vol, small-size; n_rest tickers are randomized 'average' names with
    factor exposures drawn from a non-overlapping (expensive/low-quality/
    high-vol/large-cap) regime, so the engineered names should dominate the
    top quintile of the composite.
    """
    rng = np.random.RandomState(seed)
    rows = []

    for i in range(n_good):
        rows.append({
            "Symbol": f"GOOD{i}",
            "Market Cap": rng.uniform(1e9, 5e9),       # mid-cap, not microcap
            "book_to_market": rng.uniform(1.5, 2.5),    # cheap (high B/M)
            "earnings_yield": rng.uniform(0.10, 0.18),  # cheap (high E/Y)
            "quality_factor_score": rng.uniform(0.25, 0.40),  # high ROE+margin
            "low_vol_score": rng.uniform(-0.10, -0.05),  # low realized vol
        })

    for i in range(n_rest):
        rows.append({
            "Symbol": f"AVG{i}",
            "Market Cap": rng.uniform(5e10, 5e11),       # large-cap (negative size exposure)
            "book_to_market": rng.uniform(0.05, 0.30),   # expensive (low B/M)
            "earnings_yield": rng.uniform(0.01, 0.04),   # expensive (low E/Y)
            "quality_factor_score": rng.uniform(-0.15, 0.05),  # low quality
            "low_vol_score": rng.uniform(-0.80, -0.40),  # high realized vol
        })

    df = pd.DataFrame(rows)
    df["log_market_cap"] = np.log(df["Market Cap"])
    return df


# =============================================================================
# Happy path: engineered top-quintile recovery
# =============================================================================
def test_top_quintile_contains_engineered_value_quality_lowvol_names():
    df = _synthetic_universe(n_good=10, n_rest=40)
    ctx = _make_context()
    module = MultifactorSignal()
    module.pre_compute(df, ctx)

    composite_by_ticker = {
        t: entry["Multifactor_Composite"] for t, entry in ctx.multifactor_scores.items()
    }
    ranked = sorted(composite_by_ticker.items(), key=lambda kv: kv[1], reverse=True)
    top_quintile = {t for t, _ in ranked[:10]}  # top 10 of 50 = top quintile

    good_tickers = {f"GOOD{i}" for i in range(10)}
    overlap = good_tickers & top_quintile
    assert len(overlap) >= 8, (
        f"Expected most engineered high-value/quality/low-vol names in the top "
        f"quintile, got overlap={overlap}"
    )


def test_compute_returns_score_in_valid_range():
    df = _synthetic_universe()
    ctx = _make_context()
    module = MultifactorSignal()
    module.pre_compute(df, ctx)

    for _, row in df.iterrows():
        output = module.compute(row, ctx)
        assert -1.0 <= output.score <= 1.0
        assert 0.0 <= output.confidence <= 1.0


def test_engineered_good_ticker_scores_positive():
    df = _synthetic_universe()
    ctx = _make_context()
    module = MultifactorSignal()
    module.pre_compute(df, ctx)

    good_row = df[df["Symbol"] == "GOOD0"].iloc[0]
    output = module.compute(good_row, ctx)
    assert output.score > 0.0


# =============================================================================
# Winsorization
# =============================================================================
def test_zscore_winsorize_clips_extreme_outlier():
    """An extreme outlier must not produce a z-score beyond +/-WINSOR_LIMIT.
    Uses a larger sample (20 normal values + 1 outlier) so the outlier's own
    contribution to the std doesn't fully absorb its deviation -- with only a
    handful of points a single outlier inflates its own std enough to stay
    under 3 sigma, which would make this test vacuous."""
    rng = np.random.RandomState(3)
    normal_values = list(rng.normal(loc=1.0, scale=0.05, size=20))
    values = pd.Series(normal_values + [1000.0])  # last value is a wild outlier
    z = _zscore_winsorize(values)

    assert z.iloc[-1] == WINSOR_LIMIT  # clipped exactly at the winsor limit
    # The other members should not be crushed to near-zero by the outlier's
    # effect on the mean/std -- they remain within the winsor band too.
    assert (z.iloc[:-1].abs() <= WINSOR_LIMIT).all()


def test_winsorization_outlier_does_not_dominate_composite():
    df = _synthetic_universe(n_good=10, n_rest=39)
    # Inject one extreme outlier in book_to_market for an otherwise-average name.
    outlier_row = {
        "Symbol": "OUTLIER",
        "Market Cap": 1e11,
        "book_to_market": 999999.0,  # absurd outlier
        "earnings_yield": 0.02,
        "quality_factor_score": -0.05,
        "low_vol_score": -0.50,
    }
    outlier_row["log_market_cap"] = np.log(outlier_row["Market Cap"])
    df = pd.concat([df, pd.DataFrame([outlier_row])], ignore_index=True)

    ctx = _make_context()
    module = MultifactorSignal()
    module.pre_compute(df, ctx)

    outlier_entry = ctx.multifactor_scores["OUTLIER"]
    # Value_Z averages b2m_z (clipped at +3) and earnings_yield_z (not extreme),
    # so it must stay bounded -- the raw 999999 must never leak through unclipped.
    assert outlier_entry["Value_Z"] <= WINSOR_LIMIT + 1e-9
    assert not math.isnan(outlier_entry["Multifactor_Composite"])
    assert abs(outlier_entry["Multifactor_Composite"]) <= WINSOR_LIMIT + 1e-9


# =============================================================================
# Microcap exclusion
# =============================================================================
def test_microcap_excluded_from_population_and_gets_neutral_score():
    df = _synthetic_universe(n_good=10, n_rest=39)
    microcap_row = {
        "Symbol": "MICRO",
        "Market Cap": 50_000_000.0,  # below default $300M threshold
        "book_to_market": 5.0,        # would otherwise look like a great value name
        "earnings_yield": 0.20,
        "quality_factor_score": 0.50,
        "low_vol_score": -0.02,
    }
    microcap_row["log_market_cap"] = np.log(microcap_row["Market Cap"])
    df = pd.concat([df, pd.DataFrame([microcap_row])], ignore_index=True)

    ctx = _make_context()
    module = MultifactorSignal()
    module.pre_compute(df, ctx)

    micro_entry = ctx.multifactor_scores["MICRO"]
    assert micro_entry["excluded_microcap"] is True
    assert math.isnan(micro_entry["Multifactor_Composite"])

    micro_output = module.compute(df[df["Symbol"] == "MICRO"].iloc[0], ctx)
    assert micro_output.score == 0.0
    assert micro_output.confidence == 0.0


def test_microcap_does_not_skew_cross_sectional_zscores():
    """A microcap with an extreme (fabricated-looking) value exposure must not
    shift the mean/std used to z-score the eligible (non-microcap) population."""
    df_without_micro = _synthetic_universe(n_good=10, n_rest=39)
    ctx_without = _make_context()
    MultifactorSignal().pre_compute(df_without_micro, ctx_without)

    df_with_micro = df_without_micro.copy()
    extreme_micro = {
        "Symbol": "MICRO2",
        "Market Cap": 10_000_000.0,
        "book_to_market": 50.0,
        "earnings_yield": 0.99,
        "quality_factor_score": 5.0,
        "low_vol_score": 0.50,
    }
    extreme_micro["log_market_cap"] = np.log(extreme_micro["Market Cap"])
    df_with_micro = pd.concat([df_with_micro, pd.DataFrame([extreme_micro])], ignore_index=True)
    ctx_with = _make_context()
    MultifactorSignal().pre_compute(df_with_micro, ctx_with)

    # GOOD0's composite must be unaffected by the microcap's extreme values.
    good0_without = ctx_without.multifactor_scores["GOOD0"]["Multifactor_Composite"]
    good0_with = ctx_with.multifactor_scores["GOOD0"]["Multifactor_Composite"]
    assert math.isclose(good0_without, good0_with, rel_tol=1e-9)


# =============================================================================
# Missing-data handling (no fabrication)
# =============================================================================
def test_missing_raw_columns_yields_empty_scores_not_fabricated():
    df = pd.DataFrame({"Symbol": ["A", "B"], "Market Cap": [1e9, 2e9]})  # no factor inputs
    ctx = _make_context()
    module = MultifactorSignal()
    module.pre_compute(df, ctx)
    assert ctx.multifactor_scores == {}


def test_compute_unknown_ticker_returns_neutral_with_warning():
    ctx = _make_context()
    module = MultifactorSignal()
    row = pd.Series({"Symbol": "UNKNOWN_TICKER"})
    output = module.compute(row, ctx)
    assert output.score == 0.0
    assert "WARNING" in output.explanation


# =============================================================================
# ABC conformance
# =============================================================================
def test_module_conforms_to_signal_module_abc():
    from signals.base import SignalModule
    module = MultifactorSignal()
    assert isinstance(module, SignalModule)
    assert module.name == "multifactor"
    assert hasattr(module, "compute")
    assert hasattr(module, "pre_compute")


def test_module_is_registered():
    from signals.registry import global_registry
    assert "multifactor" in global_registry._modules
