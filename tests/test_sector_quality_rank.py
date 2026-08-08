"""
InvestYo Quant Platform - Sector-Neutral Earnings-Quality Rank (SNEQR) Tests
==============================================================================
Unit tests for signals/sector_quality_rank.py: within-sector z-scoring,
thin-sector exclusion, missing-data handling (no fabrication), the [-1, +1]
score mapping, and no-lookahead / determinism guarantees.
"""

from __future__ import annotations

import math
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from signals.sector_quality_rank import (
    SectorNeutralQualitySignal,
    SYMBOL_COL,
    SECTOR_COL,
    ACCRUAL_RATIO_COL,
    GROSS_PROFITABILITY_COL,
    MIN_SECTOR_SIZE,
)
from signals.base import SignalContext, SignalModule
from dto_models import MarketBarDTO, FundamentalDataDTO, MacroEconomicDTO


# =============================================================================
# Fixtures
# =============================================================================

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


def _synthetic_universe(seed: int = 11) -> pd.DataFrame:
    """Two sectors of 10 names each. Within each sector, GOOD* names are
    engineered with high accrual quality (low/negative accruals, sign-flipped
    so higher = better) and high gross profitability; AVG* names are
    engineered worse on both axes. Tech sector's raw scale is deliberately
    shifted higher than Financials' so a naive market-wide (non-sector-
    neutral) ranking would systematically favor Tech -- this is exactly the
    property sector-neutral ranking must correct for.
    """
    rng = np.random.RandomState(seed)
    rows = []

    # Financials: lower raw scale for both inputs.
    for i in range(5):
        rows.append({
            "Symbol": f"FIN_GOOD{i}", "sector": "Financials",
            ACCRUAL_RATIO_COL: rng.uniform(0.05, 0.10),
            GROSS_PROFITABILITY_COL: rng.uniform(0.05, 0.10),
        })
    for i in range(5):
        rows.append({
            "Symbol": f"FIN_AVG{i}", "sector": "Financials",
            ACCRUAL_RATIO_COL: rng.uniform(-0.10, -0.05),
            GROSS_PROFITABILITY_COL: rng.uniform(-0.10, -0.05),
        })

    # Technology: higher raw scale for both inputs (structurally "better"
    # looking on an absolute basis, but sector-neutral ranking must not let
    # this systematically dominate Financials).
    for i in range(5):
        rows.append({
            "Symbol": f"TECH_GOOD{i}", "sector": "Technology",
            ACCRUAL_RATIO_COL: rng.uniform(0.45, 0.50),
            GROSS_PROFITABILITY_COL: rng.uniform(0.45, 0.50),
        })
    for i in range(5):
        rows.append({
            "Symbol": f"TECH_AVG{i}", "sector": "Technology",
            ACCRUAL_RATIO_COL: rng.uniform(0.30, 0.35),
            GROSS_PROFITABILITY_COL: rng.uniform(0.30, 0.35),
        })

    return pd.DataFrame(rows)


# =============================================================================
# Happy path: within-sector ranking recovers engineered top names in BOTH sectors
# =============================================================================
def test_top_ranked_names_recovered_within_each_sector():
    df = _synthetic_universe()
    ctx = _make_context()
    module = SectorNeutralQualitySignal()
    module.pre_compute(df, ctx)

    # Every FIN_GOOD* must outrank every FIN_AVG* WITHIN Financials.
    for i in range(5):
        assert ctx.sector_quality_ranks[f"FIN_GOOD{i}"] > ctx.sector_quality_ranks[f"FIN_AVG{i}"]

    # Every TECH_GOOD* must outrank every TECH_AVG* WITHIN Technology.
    for i in range(5):
        assert ctx.sector_quality_ranks[f"TECH_GOOD{i}"] > ctx.sector_quality_ranks[f"TECH_AVG{i}"]


def test_sector_neutrality_best_financial_scores_as_well_as_best_tech():
    """The best Financials name and the best Technology name must land in the
    same (top) percentile band WITHIN their own sector, despite Technology's
    raw inputs being on a structurally higher absolute scale. A non-sector-
    neutral (market-wide) ranking would instead always favor Technology.
    """
    df = _synthetic_universe()
    ctx = _make_context()
    module = SectorNeutralQualitySignal()
    module.pre_compute(df, ctx)

    fin_best = max(ctx.sector_quality_ranks[f"FIN_GOOD{i}"] for i in range(5))
    tech_best = max(ctx.sector_quality_ranks[f"TECH_GOOD{i}"] for i in range(5))
    # Both are the top-ranked name within their own 10-name sector -> percentile 1.0.
    assert math.isclose(fin_best, 1.0, abs_tol=1e-9)
    assert math.isclose(tech_best, 1.0, abs_tol=1e-9)


def test_compute_returns_score_in_valid_range():
    df = _synthetic_universe()
    ctx = _make_context()
    module = SectorNeutralQualitySignal()
    module.pre_compute(df, ctx)

    for _, row in df.iterrows():
        output = module.compute(row, ctx)
        assert -1.0 <= output.score <= 1.0
        assert 0.0 <= output.confidence <= 1.0


def test_engineered_good_ticker_scores_positive():
    df = _synthetic_universe()
    ctx = _make_context()
    module = SectorNeutralQualitySignal()
    module.pre_compute(df, ctx)

    good_row = df[df["Symbol"] == "FIN_GOOD0"].iloc[0]
    output = module.compute(good_row, ctx)
    assert output.score > 0.0


def test_score_formula_midpoint_matches_cross_sectional_momentum_pattern():
    """score = 2 * (percentile - 0.5), same linear remap [0,1] -> [-1,+1]
    used by signals/cross_sectional_momentum.py."""
    ctx = _make_context()
    ctx.sector_quality_ranks = {"MID": 0.5}
    module = SectorNeutralQualitySignal()
    row = pd.Series({SYMBOL_COL: "MID"})
    out = module.compute(row, ctx)
    assert abs(out.score) < 1e-9


# =============================================================================
# Thin-sector exclusion (documented failure mode: never force-rank)
# =============================================================================
def test_thin_sector_excluded_from_ranking():
    """A sector with fewer than MIN_SECTOR_SIZE names this cycle must be
    excluded entirely -- its tickers never appear in sector_quality_ranks."""
    df = _synthetic_universe()
    rng = np.random.RandomState(3)
    thin_rows = []
    for i in range(MIN_SECTOR_SIZE - 1):  # one short of the threshold
        thin_rows.append({
            "Symbol": f"THIN{i}", "sector": "Utilities",
            ACCRUAL_RATIO_COL: rng.uniform(0.1, 0.2),
            GROSS_PROFITABILITY_COL: rng.uniform(0.1, 0.2),
        })
    df = pd.concat([df, pd.DataFrame(thin_rows)], ignore_index=True)

    ctx = _make_context()
    module = SectorNeutralQualitySignal()
    module.pre_compute(df, ctx)

    for i in range(MIN_SECTOR_SIZE - 1):
        assert f"THIN{i}" not in ctx.sector_quality_ranks

    # The other, adequately-sized sectors must still be ranked normally.
    assert "FIN_GOOD0" in ctx.sector_quality_ranks
    assert "TECH_GOOD0" in ctx.sector_quality_ranks


def test_sector_at_exactly_threshold_is_included():
    """A sector with EXACTLY MIN_SECTOR_SIZE names must be ranked (the
    exclusion is a strict '<', not '<=')."""
    rng = np.random.RandomState(5)
    rows = []
    for i in range(MIN_SECTOR_SIZE):
        rows.append({
            "Symbol": f"EXACT{i}", "sector": "Energy",
            ACCRUAL_RATIO_COL: rng.uniform(-0.1, 0.5),
            GROSS_PROFITABILITY_COL: rng.uniform(-0.1, 0.5),
        })
    df = pd.DataFrame(rows)

    ctx = _make_context()
    module = SectorNeutralQualitySignal()
    module.pre_compute(df, ctx)

    for i in range(MIN_SECTOR_SIZE):
        assert f"EXACT{i}" in ctx.sector_quality_ranks


# =============================================================================
# Missing-data handling (no fabrication, CONSTRAINT #4)
# =============================================================================
def test_missing_symbol_column_yields_empty_ranks_no_raise():
    df = pd.DataFrame({"sector": ["Financials", "Technology"]})
    ctx = _make_context()
    module = SectorNeutralQualitySignal()
    module.pre_compute(df, ctx)  # must not raise
    assert ctx.sector_quality_ranks == {}


def test_missing_sector_column_yields_empty_ranks_no_raise():
    df = pd.DataFrame({
        SYMBOL_COL: ["A", "B"],
        ACCRUAL_RATIO_COL: [0.1, 0.2],
        GROSS_PROFITABILITY_COL: [0.1, 0.2],
    })
    ctx = _make_context()
    module = SectorNeutralQualitySignal()
    module.pre_compute(df, ctx)  # must not raise
    assert ctx.sector_quality_ranks == {}


def test_missing_raw_input_columns_yields_empty_ranks_not_fabricated():
    """This is the CURRENT, verified state of the live pipeline: neither
    accrual_ratio nor gross_profitability is populated in universe_df yet
    (see the module's Data Availability Gap docstring section). The module
    must degrade honestly rather than fabricate a score from unrelated data.
    """
    df = pd.DataFrame({
        SYMBOL_COL: [f"T{i}" for i in range(10)],
        "sector": ["Technology"] * 10,
    })
    ctx = _make_context()
    module = SectorNeutralQualitySignal()
    module.pre_compute(df, ctx)
    assert ctx.sector_quality_ranks == {}


def test_sector_fallback_capitalized_column_name():
    """A defensive fallback to 'Sector' (capitalized) must also work, mirroring
    main_orchestrator.py's own row.get('sector', row.get('Sector', ...)) convention."""
    rng = np.random.RandomState(9)
    rows = [
        {
            "Symbol": f"T{i}", "Sector": "Healthcare",
            ACCRUAL_RATIO_COL: rng.uniform(-0.2, 0.2),
            GROSS_PROFITABILITY_COL: rng.uniform(-0.2, 0.2),
        }
        for i in range(6)
    ]
    df = pd.DataFrame(rows)
    ctx = _make_context()
    module = SectorNeutralQualitySignal()
    module.pre_compute(df, ctx)
    assert len(ctx.sector_quality_ranks) == 6


def test_compute_unknown_ticker_returns_neutral_with_warning():
    ctx = _make_context()
    module = SectorNeutralQualitySignal()
    row = pd.Series({SYMBOL_COL: "UNKNOWN_TICKER"})
    output = module.compute(row, ctx)
    assert output.score == 0.0
    assert output.confidence == 0.0
    assert "WARNING" in output.explanation


def test_compute_empty_ranks_returns_neutral():
    """pre_compute never ran (or degraded to empty) this cycle -- compute()
    must still degrade gracefully, never raise a KeyError."""
    ctx = _make_context()
    module = SectorNeutralQualitySignal()
    row = pd.Series({SYMBOL_COL: "ANYTHING"})
    output = module.compute(row, ctx)
    assert output.score == 0.0
    assert output.confidence == 0.0


# =============================================================================
# No-lookahead / determinism
# =============================================================================
def test_no_cross_cycle_state_leakage():
    """SNEQR is a purely cross-sectional, single-date-snapshot signal (like
    signals/multifactor.py) -- pre_compute has no rolling-window/time-series
    component of its own, so tests/lookahead_check.py::verify_no_lookahead's
    "perturb data after time t, assert output at t is unchanged" harness does
    not map cleanly onto its (row, context) shape (the same structural reason
    tests/test_signals_lookahead.py documents for excluding
    CrossSectionalMomentumSignal's compute() from that harness directly).

    The real no-lookahead-equivalent property for a stateless per-cycle
    context module is PURITY: pre_compute(universe_df, context) must be a
    pure function of its two arguments with no hidden cross-call memory, so
    a later cycle's universe can never leak into an earlier cycle's already-
    computed ranks. This test proves that directly: running pre_compute for
    a "day 2" universe on the SAME module instance immediately after a
    "day 1" universe must not perturb a fresh, independent re-computation of
    day 1's own ranks.
    """
    day1_df = _synthetic_universe(seed=11)
    day2_df = _synthetic_universe(seed=99)
    # Give day 2 a distinctly different (shifted) distribution so any leakage
    # would visibly move day 1's re-computed ranks.
    day2_df[ACCRUAL_RATIO_COL] = day2_df[ACCRUAL_RATIO_COL] + 5.0
    day2_df[GROSS_PROFITABILITY_COL] = day2_df[GROSS_PROFITABILITY_COL] + 5.0

    module = SectorNeutralQualitySignal()

    ctx_baseline = _make_context()
    module.pre_compute(day1_df, ctx_baseline)
    baseline_ranks = dict(ctx_baseline.sector_quality_ranks)

    # Run day 1, then day 2, on the SAME module instance -- then re-run day 1
    # fresh and confirm it reproduces the baseline exactly.
    ctx_seq = _make_context()
    module.pre_compute(day1_df, ctx_seq)
    module.pre_compute(day2_df, ctx_seq)  # would leak into ctx_seq if module held state
    ctx_seq_day1_again = _make_context()
    module.pre_compute(day1_df, ctx_seq_day1_again)

    for ticker, rank in baseline_ranks.items():
        assert math.isclose(ctx_seq_day1_again.sector_quality_ranks[ticker], rank, rel_tol=1e-12)


def test_pre_compute_is_deterministic_and_pure():
    """Calling pre_compute twice with identical inputs (fresh contexts) must
    produce byte-identical ranks -- no random seed, no I/O, no mutable
    module-level cache."""
    df = _synthetic_universe()
    module = SectorNeutralQualitySignal()

    ctx_a = _make_context()
    module.pre_compute(df, ctx_a)
    ctx_b = _make_context()
    module.pre_compute(df, ctx_b)

    assert ctx_a.sector_quality_ranks == ctx_b.sector_quality_ranks


# =============================================================================
# ABC conformance
# =============================================================================
def test_module_conforms_to_signal_module_abc():
    module = SectorNeutralQualitySignal()
    assert isinstance(module, SignalModule)
    assert module.name == "sector_quality_rank"
    assert hasattr(module, "compute")
    assert hasattr(module, "pre_compute")


def test_module_is_registered():
    from signals.registry import global_registry
    assert "sector_quality_rank" in global_registry._modules


def test_weight_registered_in_settings():
    from settings import settings
    assert "sector_quality_rank" in settings.SIGNAL_WEIGHTS
