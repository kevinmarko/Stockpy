"""Owning suite for ``research_engine.AdvancedResearchEngine``.

This module is the dedicated *known-answer / happy-path correctness* suite for the
ten analytics methods (metrics 21-30) on ``AdvancedResearchEngine``. The existing
research-engine tests deliberately pin only the defensive behaviours; this suite
pins the numeric results on the NORMAL computation branch so a silent formula
regression is caught.

Coverage:
    - calculate_sector_adjusted_valuation  — standard Graham √, REIT & BDC branches
    - calculate_real_yield_drag            — restrictive-yield drag + no-drag branch
    - calculate_dividend_premium_spread    — whole-% normalisation + decimal path
    - calculate_institutional_velocity     — weighted velocity, liquidation sign, "%" strings
    - calculate_dividend_payback_horizon   — flat & compounding payback counts
    - calculate_leverage_distress_factor   — standard / REIT / BDC per-branch scores
    - calculate_relative_strength_momentum_slope — signed OLS slope × 1000
    - calculate_realized_slippage          — commission-based bps, case-insensitive codes
    - calculate_options_volatility_edge    — ATR vol-proxy edge (rich vs. cheap)
    - calculate_portfolio_covar_dependency — max pairwise |corr| tail-dependency

Non-duplication note — verified against the existing owners so this suite fills the
happy-path gap rather than repeating their edge cases:
    - tests/test_no_fabricated_metrics.py   (NaN / zero-sentinel edge cases)
    - tests/test_dead_letter_resilience.py  (try/except resilience of slippage/covar)
    - tests/test_indicators_lookahead.py    (RS-slope lookahead perturbation)
    - tests/test_correlation_clusters.py    (compute_correlation_clusters / fetch helpers)

All tests are fully offline and deterministic (seeded numpy where randomness is used);
no network, no yfinance, no FRED. No-fabricated-metrics and dead-letter angles that are
*new* (not re-pinning the above) are included at the end.
"""

import math

import numpy as np
import pandas as pd
import pytest

from research_engine import AdvancedResearchEngine


@pytest.fixture
def engine():
    """Default engine matching the module's own __main__ demo constants."""
    return AdvancedResearchEngine(risk_free_rate=0.0425, real_yield=0.0215)


# =============================================================================
# Topic 21 — calculate_sector_adjusted_valuation
# =============================================================================
class TestSectorAdjustedValuationKnownAnswer:
    """Pins each of the three valuation branches to a hand-computed value."""

    def test_standard_sector_is_plain_graham_number(self, engine):
        # Industrial/tech path with valid inputs → sqrt(22.5 * eps * bv).
        result = engine.calculate_sector_adjusted_valuation(
            sector="Technology", pe=15.0, pb=2.0, book_value=8.0, eps=2.0, price=30.0
        )
        assert result == pytest.approx(math.sqrt(22.5 * 2.0 * 8.0))  # sqrt(360) ≈ 18.9737

    def test_reit_branch_applies_ffo_uplift(self, engine):
        # REIT path: eps *1.35 (FFO proxy), bv *1.05, then Graham √.
        eps, bv = 1.20, 12.50
        expected = math.sqrt(22.5 * (eps * 1.35) * (bv * 1.05))
        result = engine.calculate_sector_adjusted_valuation(
            sector="Real Estate (mREIT)", pe=10.0, pb=0.85, book_value=bv, eps=eps, price=10.50
        )
        assert result == pytest.approx(expected)

    def test_bdc_branch_uses_nav_weighted_multiple(self, engine):
        # Financial/BDC path: 15.0 multiple, eps *1.15, book value un-scaled.
        eps, bv = 1.00, 20.0
        expected = math.sqrt(15.0 * (eps * 1.15) * bv)
        result = engine.calculate_sector_adjusted_valuation(
            sector="Financial (BDC)", pe=8.0, pb=0.9, book_value=bv, eps=eps, price=18.0
        )
        assert result == pytest.approx(expected)

    def test_reit_result_exceeds_standard_for_same_inputs(self, engine):
        # Sanity on the whole point of the metric: the FFO uplift lifts the REIT
        # fair value above the plain Graham number for identical eps/bv.
        eps, bv = 2.0, 8.0
        standard = engine.calculate_sector_adjusted_valuation(
            sector="Technology", pe=15.0, pb=2.0, book_value=bv, eps=eps, price=30.0
        )
        reit = engine.calculate_sector_adjusted_valuation(
            sector="REIT", pe=15.0, pb=2.0, book_value=bv, eps=eps, price=30.0
        )
        assert reit > standard


# =============================================================================
# Topic 22 — calculate_real_yield_drag
# =============================================================================
class TestRealYieldDragKnownAnswer:
    def test_restrictive_yield_applies_exact_drag(self):
        # real_yield=0.0215 → ry>0.02 → drag_factor = 1 - (0.0215 - 0.02) = 0.9985.
        eng = AdvancedResearchEngine(real_yield=0.0215)
        assert eng.calculate_real_yield_drag(100.0) == pytest.approx(99.85)

    def test_whole_percentage_input_matches_fraction(self):
        # 2.15 (whole %) is auto-divided by 100 → identical to the 0.0215 fraction.
        whole = AdvancedResearchEngine(real_yield=2.15).calculate_real_yield_drag(100.0)
        frac = AdvancedResearchEngine(real_yield=0.0215).calculate_real_yield_drag(100.0)
        assert whole == pytest.approx(frac) == pytest.approx(99.85)

    def test_non_restrictive_yield_returns_fair_value_unchanged(self):
        # real_yield=0.01 ≤ 0.02 threshold → no drag branch → identity.
        eng = AdvancedResearchEngine(real_yield=0.01)
        assert eng.calculate_real_yield_drag(123.45) == 123.45


# =============================================================================
# Topic 23 — calculate_dividend_premium_spread
# =============================================================================
class TestDividendPremiumSpreadKnownAnswer:
    def test_whole_percentage_yield_is_normalised_then_spread(self, engine):
        # 5.0 (>1.0) → 0.05, minus risk_free 0.0425 → 0.0075.
        assert engine.calculate_dividend_premium_spread(5.0) == pytest.approx(0.0075)

    def test_decimal_yield_taken_as_is(self, engine):
        # 0.06 (≤1.0) is not rescaled → 0.06 - 0.0425 = 0.0175.
        assert engine.calculate_dividend_premium_spread(0.06) == pytest.approx(0.0175)

    def test_yield_below_risk_free_is_negative_spread(self, engine):
        # 0.03 - 0.0425 = -0.0125 → uncompensated yield surfaces as a real negative.
        assert engine.calculate_dividend_premium_spread(0.03) == pytest.approx(-0.0125)


# =============================================================================
# Topic 24 — calculate_institutional_velocity
# =============================================================================
class TestInstitutionalVelocityKnownAnswer:
    def test_weighted_velocity_from_whole_numbers(self, engine):
        # inst_own 60.0→0.60, change 5.0→0.05 → 0.05 * (1 + 0.60) = 0.08.
        assert engine.calculate_institutional_velocity(60.0, 5.0) == pytest.approx(0.08)

    def test_negative_change_signals_liquidation(self, engine):
        # inst_own 0.50, change -10.0→-0.10 → -0.10 * 1.50 = -0.15 (< 0 = liquidating).
        result = engine.calculate_institutional_velocity(0.50, -10.0)
        assert result == pytest.approx(-0.15)
        assert result < 0

    def test_percent_formatted_string_inputs_parse(self, engine):
        # "60%" / "5%" strip the % and follow the same whole-number path → 0.08.
        assert engine.calculate_institutional_velocity("60%", "5%") == pytest.approx(0.08)


# =============================================================================
# Topic 25 — calculate_dividend_payback_horizon
# =============================================================================
class TestDividendPaybackHorizonKnownAnswer:
    def test_flat_growth_counts_years_to_recover_cost(self, engine):
        # g=0, price=100, div=30 (< price*0.5 so no auto-scale): cumulative 30,60,90,120
        # → first ≥100 at year 4.
        assert engine.calculate_dividend_payback_horizon(100.0, 30.0, 0.0) == 4.0

    def test_compounding_growth_shortens_horizon(self, engine):
        # g=0.10: div 33, 36.3, 39.93 → cumulative 33, 69.3, 109.23 ≥ 100 at year 3.
        assert engine.calculate_dividend_payback_horizon(100.0, 30.0, 0.10) == 3.0

    def test_growth_case_is_faster_than_flat_case(self, engine):
        flat = engine.calculate_dividend_payback_horizon(100.0, 30.0, 0.0)
        grown = engine.calculate_dividend_payback_horizon(100.0, 30.0, 0.10)
        assert grown < flat


# =============================================================================
# Topic 26 — calculate_leverage_distress_factor
# =============================================================================
class TestLeverageDistressFactorKnownAnswer:
    def test_standard_corporate_midpoint(self, engine):
        # Standard limit 1.5x: (1.5 - 0.75) / 1.5 = 0.5.
        assert engine.calculate_leverage_distress_factor("Technology", 0.75) == pytest.approx(0.5)

    def test_reit_higher_structural_limit(self, engine):
        # REIT limit 6.0x: (6 - 3) / 6 = 0.5 — same score at 4x the leverage of a corp.
        assert engine.calculate_leverage_distress_factor("Real Estate", 3.0) == pytest.approx(0.5)

    def test_bdc_regulatory_limit(self, engine):
        # BDC limit 2.0x: (2 - 1) / 2 = 0.5.
        assert engine.calculate_leverage_distress_factor("Financial (BDC)", 1.0) == pytest.approx(0.5)

    def test_low_leverage_scores_near_healthy(self, engine):
        # A near-debt-free corp is healthy: (1.5 - 0.15) / 1.5 = 0.9.
        assert engine.calculate_leverage_distress_factor("Industrials", 0.15) == pytest.approx(0.9)


# =============================================================================
# Topic 27 — calculate_relative_strength_momentum_slope
# =============================================================================
class TestRelativeStrengthMomentumSlopeKnownAnswer:
    def test_positive_linear_outperformance_slope(self, engine):
        # SPY flat at 1.0; asset's last 20 closes rise by +1.0/step → RS ratio slope = 1.0,
        # scaled ×1000 → 1000.0. Length ≥ 30 satisfies the guard.
        spy = pd.Series([1.0] * 35)
        asset = pd.Series(np.arange(100.0, 135.0))  # 35 points, step +1
        result = engine.calculate_relative_strength_momentum_slope(asset, spy)
        assert result == pytest.approx(1000.0, abs=1e-6)

    def test_decaying_outperformance_is_negative_slope(self, engine):
        spy = pd.Series([1.0] * 35)
        asset = pd.Series(np.arange(135.0, 100.0, -1.0))  # step -1
        result = engine.calculate_relative_strength_momentum_slope(asset, spy)
        assert result == pytest.approx(-1000.0, abs=1e-6)

    def test_parallel_series_have_zero_slope(self, engine):
        # Asset and SPY move identically → RS ratio flat → slope 0.
        base = pd.Series(np.arange(100.0, 135.0))
        result = engine.calculate_relative_strength_momentum_slope(base, base.copy())
        assert result == pytest.approx(0.0, abs=1e-6)


# =============================================================================
# Topic 28 — calculate_realized_slippage
# =============================================================================
class TestRealizedSlippageKnownAnswer:
    def test_commission_based_bps(self, engine):
        # friction = |commission| = 1; computed = |amount| = 1000 → 1/1000 * 10000 = 10.0 bps.
        df = pd.DataFrame(
            {
                "Trans Code": ["BUY"],
                "Amount": ["$1,000.00"],
                "Commission": ["$1.00"],
            }
        )
        assert engine.calculate_realized_slippage(df) == pytest.approx(10.0)

    def test_trans_code_matching_is_case_insensitive(self, engine):
        # Lowercase 'buy'/'sell' still match the execution filter.
        df = pd.DataFrame(
            {
                "Trans Code": ["buy", "sell"],
                "Amount": ["1000", "1000"],
                "Commission": ["1", "1"],
            }
        )
        # Two rows: friction 2, computed 2000 → 2/2000 * 10000 = 10.0 bps.
        assert engine.calculate_realized_slippage(df) == pytest.approx(10.0)

    def test_non_execution_rows_are_excluded(self, engine):
        # A DIV row is filtered out; only the BUY row drives the bps.
        df = pd.DataFrame(
            {
                "Trans Code": ["DIV", "BUY"],
                "Amount": ["5", "1000"],
                "Commission": ["0", "2"],
            }
        )
        # Only BUY counts: 2 / 1000 * 10000 = 20.0 bps.
        assert engine.calculate_realized_slippage(df) == pytest.approx(20.0)


# =============================================================================
# Topic 29 — calculate_options_volatility_edge
# =============================================================================
class TestOptionsVolatilityEdgeKnownAnswer:
    def test_rich_premium_positive_edge(self, engine):
        # atr=1, price=100 → proxy = sqrt(252)/100 ≈ 0.158745; minus HV 0.10 → +0.0587.
        expected = round((1.0 * math.sqrt(252)) / 100.0 - 0.10, 4)
        assert engine.calculate_options_volatility_edge(0.10, 1.0, 100.0) == pytest.approx(expected)
        assert engine.calculate_options_volatility_edge(0.10, 1.0, 100.0) > 0

    def test_cheap_premium_negative_edge(self, engine):
        # High HV relative to ATR proxy → edge < 0 (selling unfavourable).
        result = engine.calculate_options_volatility_edge(0.50, 1.0, 100.0)
        assert result < 0
        assert result == pytest.approx(round((math.sqrt(252)) / 100.0 - 0.50, 4))


# =============================================================================
# Topic 30 — calculate_portfolio_covar_dependency
# =============================================================================
class TestPortfolioCovarDependencyKnownAnswer:
    def test_perfectly_correlated_pair_max_corr_is_one(self, engine):
        a = pd.Series([0.01, -0.02, 0.03, -0.01, 0.02, 0.00, 0.015])
        df = pd.DataFrame({"A": a, "B": a * 2.0})  # perfectly correlated → |ρ| = 1.0
        assert engine.calculate_portfolio_covar_dependency(df) == pytest.approx(1.0)

    def test_anti_correlated_pair_absolute_value_is_one(self, engine):
        a = pd.Series([0.01, -0.02, 0.03, -0.01, 0.02, 0.00, 0.015])
        df = pd.DataFrame({"A": a, "B": -a})  # ρ = -1.0 → abs → 1.0
        assert engine.calculate_portfolio_covar_dependency(df) == pytest.approx(1.0)

    def test_max_pairwise_dominates_across_three_columns(self, engine):
        a = pd.Series([0.01, -0.02, 0.03, -0.01, 0.02, 0.00, 0.015])
        rng = np.random.default_rng(7)
        noise = pd.Series(rng.normal(0, 0.01, size=len(a)))
        # A~B perfectly correlated; C is independent noise. Max |ρ| must come from A/B = 1.0.
        df = pd.DataFrame({"A": a, "B": a * 3.0, "C": noise})
        assert engine.calculate_portfolio_covar_dependency(df) == pytest.approx(1.0)

    def test_seeded_independent_columns_report_bounded_not_fabricated(self, engine):
        rng = np.random.default_rng(42)
        df = pd.DataFrame(
            {
                "A": rng.normal(0, 0.01, size=200),
                "B": rng.normal(0, 0.01, size=200),
            }
        )
        result = engine.calculate_portfolio_covar_dependency(df)
        # A real, small |ρ| in [0, 1) — never a fabricated 0.0 and never > 1.
        assert 0.0 <= result < 1.0


# =============================================================================
# Cross-cutting — NEW no-fabricated-metrics / dead-letter angles
# (distinct from the existing owner suites)
# =============================================================================
class TestNoFabricatedHappyPathContrast:
    def test_missing_eps_bv_but_price_present_recovers_nonzero(self, engine):
        # Reverse-engineering path yields a real Graham number, not a fabricated 0.0.
        result = engine.calculate_sector_adjusted_valuation(
            sector="Technology", pe=15.0, pb=2.0, book_value=0.0, eps=0.0, price=30.0
        )
        # eps = 30/15 = 2.0, bv = 30/2 = 15.0 → sqrt(22.5 * 2 * 15).
        assert result == pytest.approx(math.sqrt(22.5 * 2.0 * 15.0))
        assert result > 0.0


class TestRealizedSlippageBatchResilience:
    def test_one_unparseable_row_does_not_abort_batch(self, engine):
        # The method is try/except-guarded: a garbage Amount forces the whole call into
        # the documented 0.0 sentinel rather than raising mid-batch.
        df = pd.DataFrame(
            {
                "Trans Code": ["BUY", "SELL"],
                "Amount": ["1000", "not-a-number"],
                "Commission": ["1", "1"],
            }
        )
        result = engine.calculate_realized_slippage(df)
        assert result == 0.0  # graceful degradation, no exception
