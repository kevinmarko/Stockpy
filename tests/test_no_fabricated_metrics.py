"""
tests/test_no_fabricated_metrics.py
====================================
CONSTRAINT #4 sweep: "no fabricated metrics — insufficient/missing data must
yield NaN/None/neutral, never a fabricated default."

This is a broad, parametrized sweep (not exhaustive per-function depth)
across the modules a coverage survey identified as genuine gaps: most of
`research_engine.AdvancedResearchEngine`'s valuation/income methods (zero
prior insufficient-input tests), `dto_models.BaseDTO`/`FundamentalDataDTO`'s
default-value contract, the `compute()` NaN-input path for
`signals/timeseries_momentum.py` and `signals/rsi2_mean_reversion.py`,
`signals/pairs_trading.generate_pairs_signals`'s warm-up-period behavior, and
`evaluation_engine.evaluate_portfolio`'s remaining un-pinned default-injection
branches.

Already well-covered modules are deliberately excluded here (not duplicated):
`research_engine.compute_correlation_clusters`/`fetch_returns_for_clustering`
(tests/test_correlation_clusters.py), `signals/regime_multiplier.py`
(tests/test_regime_multiplier.py), `signals/lgbm_ranker.py`
(tests/test_lgbm_ranker_signal.py), `evaluate_portfolio`'s MAE/MFE/Edge Ratio
NaN-with-no-history (tests/test_evaluation_no_history.py) and BF_*-zero-on-
zero-positions (tests/test_evaluate_portfolio_zero_positions.py).

Every assertion below was verified against the actual production source
during planning, not assumed from docstrings alone — several of these
functions return a *documented, intentional* sentinel (e.g. `0.5` neutral
leverage score, `99.0` "infinite payback horizon") rather than NaN; the test
docstrings call out which is which so a future reader doesn't mistake a
deliberate sentinel for a bug.
"""

from __future__ import annotations

import math
from unittest import mock

import numpy as np
import pandas as pd
import pytest
import transactions_store

from dto_models import FundamentalDataDTO, MacroEconomicDTO, MarketBarDTO, BaseDTO
from evaluation_engine import EvaluationEngine
from research_engine import AdvancedResearchEngine
from signals.base import SignalContext
from signals.pairs_trading import generate_pairs_signals
from signals.rsi2_mean_reversion import RSI2MeanReversionSignal
from signals.timeseries_momentum import TimeSeriesMomentumSignal
from tests._db_isolation import redirect_class_to_memory_db


# ============================================================================
# Shared helpers
# ============================================================================

def _patched_ee() -> EvaluationEngine:
    """EvaluationEngine backed by an in-memory DB so no real trades are read.

    CORRECTED (found during PR review, verified by direct execution):
    EvaluationEngine.__init__ never constructs a TransactionsStore -- only
    evaluate_portfolio() does, internally, with no override parameter. An
    earlier version of this helper patched TransactionsStore.__init__ only
    for the duration of EvaluationEngine() construction and restored it in a
    finally block immediately afterward -- by the time a test called
    ee.evaluate_portfolio(...), the patch was already gone, so every test
    using that pattern silently read the real, git-committed on-disk
    quant_platform.db instead of an in-memory one (a read, not a write, so
    `git status` never caught it). Wrapping evaluate_portfolio() itself,
    rather than the constructor, keeps the redirect active for exactly the
    call that needs it, regardless of how many times evaluate_portfolio()
    is invoked on the returned engine.
    """
    ee = EvaluationEngine()
    original_evaluate_portfolio = ee.evaluate_portfolio

    def _wrapped_evaluate_portfolio(*args, **kwargs):
        with redirect_class_to_memory_db(transactions_store.TransactionsStore):
            return original_evaluate_portfolio(*args, **kwargs)

    ee.evaluate_portfolio = _wrapped_evaluate_portfolio
    return ee


def _signal_context() -> SignalContext:
    bar = MarketBarDTO(
        date=pd.Timestamp("2026-06-24"), ticker="AAPL", open_price=150.0,
        high_price=155.0, low_price=149.0, close_price=154.0, volume=1_000_000,
    )
    fund = FundamentalDataDTO(
        ticker="AAPL", pe_ratio=15.0, pb_ratio=2.0, dividend_yield=0.01,
        book_value=50.0, eps_trailing=10.0, dividend_growth_rate=0.05,
        payout_ratio=0.3, sector="Technology", company_name="Apple Inc",
    )
    macro = MacroEconomicDTO(
        yield_curve_10y_2y=0.5, high_yield_oas=3.5, inflation_rate=2.0,
        nominal_10y=4.0, vix_value=15.0,
    )
    return SignalContext(bar=bar, fundamentals=fund, macro=macro)


# ============================================================================
# research_engine.AdvancedResearchEngine
# ============================================================================

@pytest.fixture
def research_engine():
    return AdvancedResearchEngine(risk_free_rate=0.0425, real_yield=0.0215)


class TestSectorAdjustedValuation:
    """calculate_sector_adjusted_valuation reverse-engineers missing EPS/BV
    from PE/PB; the documented contract is 'only return 0 if reverse-
    engineering completely failed' -- but REIT/BDC branches never hit that
    guard at all (they floor inputs at 0.01 instead)."""

    def test_standard_sector_all_zero_inputs_returns_documented_zero_sentinel(self, research_engine):
        result = research_engine.calculate_sector_adjusted_valuation(
            sector="Technology", pe=0.0, pb=0.0, book_value=0.0, eps=0.0, price=0.0,
        )
        assert result == 0.0

    def test_reit_sector_all_zero_inputs_never_returns_zero(self, research_engine):
        """REIT/BDC branches floor eps/book_value at 0.01 via max(0.01, ...)
        before the sqrt -- they structurally cannot hit the standard-sector
        0.0 guard, even on completely garbage input."""
        result = research_engine.calculate_sector_adjusted_valuation(
            sector="REIT", pe=0.0, pb=0.0, book_value=0.0, eps=0.0, price=0.0,
        )
        assert result > 0.0

    @pytest.mark.parametrize("eps,book_value", [(-5.0, 10.0), (5.0, -10.0), (-5.0, -10.0)])
    def test_standard_sector_negative_inputs_return_zero(self, research_engine, eps, book_value):
        """price=0.0 disables the reverse-engineering guard (it requires
        price>0), so negative eps/book_value cannot be rescued from PE/PB and
        must fall through to the documented 0.0 'completely failed' sentinel.
        (With price>0, a negative eps/book_value WOULD be reverse-engineered
        away via price/pe or price/pb -- that is covered by a separate test.)"""
        result = research_engine.calculate_sector_adjusted_valuation(
            sector="Industrials", pe=15.0, pb=2.0, book_value=book_value, eps=eps, price=0.0,
        )
        assert result == 0.0

    def test_negative_inputs_with_price_are_reverse_engineered_away(self, research_engine):
        """Contrast case: when price>0, calculate_sector_adjusted_valuation's
        eps<=0 guard also catches negative eps (not just zero) and rescues it
        via price/pe -- so a negative eps does NOT guarantee a 0.0 result."""
        result = research_engine.calculate_sector_adjusted_valuation(
            sector="Industrials", pe=15.0, pb=2.0, book_value=10.0, eps=-5.0, price=100.0,
        )
        assert result > 0.0

    def test_price_omitted_silently_disables_reverse_engineering(self, research_engine):
        """price defaults to 0.0; the reverse-engineering guard requires
        price>0, so omitting it leaves eps/book_value at their raw (possibly
        non-positive) input -- a silent no-op worth pinning explicitly."""
        result = research_engine.calculate_sector_adjusted_valuation(
            sector="Technology", pe=15.0, pb=2.0, book_value=0.0, eps=0.0,
        )
        assert result == 0.0


class TestRealYieldDrag:
    def test_whole_percent_real_yield_is_auto_scaled(self):
        """real_yield is constructor state (not a call param); values > 0.2
        are treated as whole-percent format (2.15 means 2.15%, i.e. 0.0215)
        and auto-divided by 100 before use."""
        engine_whole_pct = AdvancedResearchEngine(real_yield=2.15)
        engine_fraction = AdvancedResearchEngine(real_yield=0.0215)
        result_whole = engine_whole_pct.calculate_real_yield_drag(100.0)
        result_fraction = engine_fraction.calculate_real_yield_drag(100.0)
        assert math.isclose(result_whole, result_fraction, rel_tol=1e-6)

    def test_zero_fair_value_floors_at_zero_not_negative(self):
        engine = AdvancedResearchEngine(real_yield=0.03)
        result = engine.calculate_real_yield_drag(0.0)
        assert result == 0.0


class TestDividendPremiumSpread:
    """No NaN guard exists in this function at all -- NaN must propagate
    through arithmetic (honest 'undefined'), never silently become a
    fabricated real number."""

    def test_nan_dividend_yield_propagates_as_nan(self, research_engine):
        result = research_engine.calculate_dividend_premium_spread(float("nan"))
        assert math.isnan(result)

    def test_zero_dividend_yield_is_a_real_negative_spread_not_special_cased(self, research_engine):
        result = research_engine.calculate_dividend_premium_spread(0.0)
        assert result == 0.0 - research_engine.risk_free_rate


class TestInstitutionalVelocity:
    """Explicit, documented neutral 0.0 (not a crash, not a fabricated
    nonzero directional reading) for missing/NaN/unparseable inputs."""

    @pytest.mark.parametrize("inst_own,quarterly_change", [
        (None, None),
        (float("nan"), 0.01),
        (0.5, float("nan")),
    ])
    def test_missing_or_nan_inputs_yield_neutral_zero(self, research_engine, inst_own, quarterly_change):
        result = research_engine.calculate_institutional_velocity(inst_own, quarterly_change)
        assert result == 0.0

    def test_unparseable_string_input_yields_neutral_zero_not_raise(self, research_engine):
        result = research_engine.calculate_institutional_velocity("N/A%", "garbage")
        assert result == 0.0


class TestDividendPaybackHorizon:
    """Explicit 99.0 'infinite horizon' sentinel -- a documented contract,
    distinct from a fabricated finite-looking number."""

    @pytest.mark.parametrize("price,annual_div", [(100.0, 0.0), (0.0, 5.0)])
    def test_zero_div_or_zero_price_returns_infinite_horizon_sentinel(self, research_engine, price, annual_div):
        result = research_engine.calculate_dividend_payback_horizon(price, annual_div, 0.05)
        assert result == 99.0

    def test_extreme_growth_rate_is_clamped_to_documented_ceiling(self, research_engine):
        """dgr_5y is clamped to +/-20% internally -- an input far outside
        that range must produce the SAME result as the clamp boundary, not
        a runaway extrapolation."""
        result_extreme = research_engine.calculate_dividend_payback_horizon(100.0, 5.0, 5.0)
        result_at_clamp = research_engine.calculate_dividend_payback_horizon(100.0, 5.0, 0.20)
        assert math.isclose(result_extreme, result_at_clamp, rel_tol=1e-9)

    def test_deep_negative_growth_hits_forty_year_cap(self, research_engine):
        result = research_engine.calculate_dividend_payback_horizon(10_000.0, 1.0, -5.0)
        assert result == 40.0


class TestLeverageDistressFactor:
    """Source comment explicitly documents a prior bug fix: '0 debt returned
    a perfect 1.0, ruining the metric's reliability' -- the current contract
    is an explicit neutral 0.5, neither the old fabricated 1.0 nor a
    fabricated 0.0."""

    @pytest.mark.parametrize("debt_to_equity", [None, float("nan"), 0.0, 0.0009])
    def test_missing_or_near_zero_debt_returns_neutral_half(self, research_engine, debt_to_equity):
        result = research_engine.calculate_leverage_distress_factor("Technology", debt_to_equity)
        assert result == 0.5

    def test_reit_over_leverage_limit_clamps_to_zero_not_negative(self, research_engine):
        result = research_engine.calculate_leverage_distress_factor("REIT", 10.0)
        assert result == 0.0


class TestRelativeStrengthMomentumSlope:
    """None/insufficient-length series degrade to a documented 0.0 -- the
    test docstring flags this as borderline w.r.t. CONSTRAINT #4's spirit
    (0.0 is indistinguishable from 'no momentum' vs 'no data'); pinning
    current behavior, not asserting it's ideal."""

    def test_none_series_returns_documented_zero(self, research_engine):
        result = research_engine.calculate_relative_strength_momentum_slope(None, pd.Series([1.0, 2.0]))
        assert result == 0.0

    def test_series_below_thirty_day_minimum_returns_zero(self, research_engine):
        short = pd.Series(np.linspace(100, 110, 29))  # one below the 30-day floor
        spy = pd.Series(np.linspace(400, 410, 29))
        result = research_engine.calculate_relative_strength_momentum_slope(short, spy)
        assert result == 0.0

    def test_mismatched_length_series_returns_zero(self, research_engine):
        asset = pd.Series(np.linspace(100, 110, 50))
        spy = pd.Series(np.linspace(400, 410, 20))
        result = research_engine.calculate_relative_strength_momentum_slope(asset, spy)
        assert result == 0.0


class TestOptionsVolatilityEdge:
    """price<=0 is explicitly guarded -> 0.0; historical_vol/atr have NO
    guard at all -- a NaN there must propagate honestly through the
    subtraction, never silently become a fabricated edge reading."""

    def test_non_positive_price_returns_zero(self, research_engine):
        assert research_engine.calculate_options_volatility_edge(0.15, 2.0, 0.0) == 0.0
        assert research_engine.calculate_options_volatility_edge(0.15, 2.0, -10.0) == 0.0

    def test_nan_historical_vol_propagates_not_fabricated(self, research_engine):
        result = research_engine.calculate_options_volatility_edge(float("nan"), 2.0, 100.0)
        assert math.isnan(result)

    def test_nan_atr_propagates_not_fabricated(self, research_engine):
        result = research_engine.calculate_options_volatility_edge(0.15, float("nan"), 100.0)
        assert math.isnan(result)


# ============================================================================
# dto_models.BaseDTO
# ============================================================================

class TestBaseDtoToFloat:
    def test_none_honors_caller_supplied_default_not_hardcoded_zero(self):
        assert BaseDTO._to_float(None, default=0.0) == 0.0
        assert BaseDTO._to_float(None, default=None) is None
        assert BaseDTO._to_float(None, default=99.0) == 99.0

    @pytest.mark.parametrize("raw,expected", [
        ("$1,234.56", 1234.56),
        ("12.5%", 0.125),
        ("", None),
        ("garbage", None),
    ])
    def test_string_cleansing_matrix(self, raw, expected):
        result = BaseDTO._to_float(raw, default=None)
        if expected is None:
            assert result is None
        else:
            assert math.isclose(result, expected, rel_tol=1e-9)

    def test_na_substring_case_insensitive_returns_default(self):
        assert BaseDTO._to_float("n/a", default=-1.0) == -1.0

    def test_bool_is_treated_as_int_not_excluded(self):
        """A real gotcha: isinstance(value, (int, float)) does not exclude
        bool (bool is an int subclass in Python), so True/False pass through
        as 1.0/0.0 rather than hitting the string-cleansing path. Contrast
        with normalize_yfinance_dividend_yield elsewhere in this file, which
        explicitly guards against bool -- this method does not."""
        assert BaseDTO._to_float(True, default=99.0) == 1.0
        assert BaseDTO._to_float(False, default=99.0) == 0.0


class TestBaseDtoToInt:
    """_to_int is NOT symmetric with _to_float: it has no explicit 'N/A'
    substring guard, relying solely on the bare except ValueError."""

    def test_none_returns_default(self):
        assert BaseDTO._to_int(None, default=7) == 7

    def test_na_string_falls_through_to_except_branch(self):
        # int(float("N/A")) raises ValueError, caught by the bare except.
        assert BaseDTO._to_int("N/A", default=7) == 7

    def test_numeric_string_parses_correctly(self):
        assert BaseDTO._to_int("1,234", default=0) == 1234


class TestFundamentalDataDtoDefaults:
    """The three-way default asymmetry is the load-bearing contract here:
    pe_ratio/pb_ratio stay None on missing/unparseable input (Optional[float]
    in the signature); every OTHER numeric field silently defaults to 0.0;
    beta defaults to 1.0. This is intentional, documented behavior -- the
    test exists so a future 'consistency cleanup' can't silently change it
    without this test failing loudly."""

    def test_pe_pb_stay_none_other_fields_default_per_documented_contract(self):
        dto = FundamentalDataDTO(
            ticker="ZZZ", pe_ratio=None, pb_ratio=None,
            dividend_yield=None, book_value=None, eps_trailing=None,
            dividend_growth_rate=None, payout_ratio=None,
            sector="Technology", company_name="Zzz Corp",
            market_cap=None, price=None, beta=None,
        )
        assert dto.pe_ratio is None
        assert dto.pb_ratio is None
        assert dto.book_value == 0.0
        assert dto.eps_trailing == 0.0
        assert dto.dividend_yield == 0.0
        assert dto.dividend_growth_rate == 0.0
        assert dto.payout_ratio == 0.0
        assert dto.market_cap == 0.0
        assert dto.price == 0.0
        assert dto.beta == 1.0  # distinct third default value

    def test_unparseable_pe_ratio_string_also_stays_none(self):
        """_to_float(value, None) returns the caller-supplied default (None)
        on a parse failure too, not just on a literal None input."""
        dto = FundamentalDataDTO(
            ticker="ZZZ", pe_ratio="not-a-number", pb_ratio="also-garbage",
            dividend_yield=0.02, book_value=10.0, eps_trailing=1.0,
            dividend_growth_rate=0.02, payout_ratio=0.3,
            sector="Technology", company_name="Zzz Corp",
        )
        assert dto.pe_ratio is None
        assert dto.pb_ratio is None

    def test_graham_number_unaffected_by_pe_ratio_being_none(self):
        """graham_number depends only on eps_trailing/book_value -- a None
        pe_ratio must not propagate into a broken/fabricated Graham number."""
        dto = FundamentalDataDTO(
            ticker="ZZZ", pe_ratio=None, pb_ratio=None,
            dividend_yield=0.02, book_value=50.0, eps_trailing=5.0,
            dividend_growth_rate=0.02, payout_ratio=0.3,
            sector="Technology", company_name="Zzz Corp",
        )
        assert dto.graham_number == pytest.approx(math.sqrt(22.5 * 5.0 * 50.0))

    def test_from_raw_dict_with_empty_info_preserves_pe_ratio_none(self):
        """End-to-end through the actual production parsing path (not just
        direct construction) -- info.get('trailingPE') on an empty dict
        returns None, which must survive all the way to dto.pe_ratio."""
        dto = FundamentalDataDTO.from_raw_dict("ZZZ", info={})
        assert dto.pe_ratio is None
        assert dto.pb_ratio is None


# ============================================================================
# signals/timeseries_momentum.py
# ============================================================================

class TestTimeSeriesMomentumMissingInputs:
    """compute() returns a neutral score=0.0/confidence=0.0 with a WARNING
    explanation when ROC_12M/GARCH_Vol is NaN or vol<=0 -- never a fabricated
    directional score. tests/test_ts_momentum.py only exercises full-pipeline
    happy paths; this closes the direct NaN-input gap."""

    @pytest.mark.parametrize("roc_12m,garch_vol", [
        (np.nan, 0.10),
        (0.05, np.nan),
        (0.05, 0.0),
        (0.05, -0.10),
    ])
    def test_missing_or_invalid_inputs_yield_neutral_output(self, roc_12m, garch_vol):
        sig = TimeSeriesMomentumSignal()
        row = pd.Series({"ROC_12M": roc_12m, "GARCH_Vol": garch_vol})
        out = sig.compute(row, _signal_context())
        assert out.score == 0.0
        assert out.confidence == 0.0
        assert "WARNING" in out.explanation


# ============================================================================
# signals/rsi2_mean_reversion.py
# ============================================================================

class TestRsi2MeanReversionMissingInputs:
    """The NaN-guard branch is structurally identical to the already-tested
    0.0-score paths (downtrend, not-oversold) in tests/test_rsi2.py, but no
    existing test passes pd.isna inputs directly -- close that residual gap."""

    def test_nan_row_yields_neutral_zero_score(self):
        sig = RSI2MeanReversionSignal()
        row = pd.Series({"Close": np.nan, "RSI_2": 5.0, "SMA_5": 100.0, "SMA_200": 90.0})
        out = sig.compute(row, _signal_context())
        assert out.score == 0.0
        assert out.confidence == 0.0


# ============================================================================
# signals/pairs_trading.py
# ============================================================================

class TestGeneratePairsSignalsWarmup:
    """generate_pairs_signals is a plain function (not a registered
    SignalModule), but the same CONSTRAINT #4 spirit applies: NaN z_score/
    rolling_p during the rolling-window warm-up period must yield an
    explicit flat (0.0) position, never a fabricated directional bet."""

    def _cointegrated_pair(self, n: int, seed: int = 0):
        rng = np.random.RandomState(seed)
        dates = pd.date_range("2023-01-01", periods=n, freq="B")
        x = 100.0 + np.cumsum(rng.normal(0, 1.0, n))
        y = 2.0 * x + 5.0 + rng.normal(0, 0.5, n)  # cointegrated with x
        return pd.Series(y, index=dates), pd.Series(x, index=dates)

    def test_short_series_entire_warmup_period_stays_flat(self):
        """10 days is far below the rolling-window requirements -- every
        position in the warm-up period must be exactly 0.0, never a
        spurious entry from undefined statistics."""
        y, x = self._cointegrated_pair(10, seed=1)
        signals_df = generate_pairs_signals(y, x)
        assert (signals_df["position"] == 0.0).all()

    def test_flat_degenerate_price_series_uses_half_life_fallback_and_does_not_crash(self):
        """A perfectly flat (zero-variance) spread makes compute_half_life
        numerically degenerate (inf/nan/<=0); the function must engage its
        documented hl=20.0 fallback and still return a well-formed,
        non-crashing DataFrame rather than propagating a NaN/inf window size."""
        dates = pd.date_range("2023-01-01", periods=150, freq="B")
        y = pd.Series(np.full(150, 100.0), index=dates)
        x = pd.Series(np.full(150, 50.0), index=dates)
        signals_df = generate_pairs_signals(y, x)
        assert len(signals_df) == 150
        assert not signals_df["position"].isna().any()


# ============================================================================
# evaluation_engine.evaluate_portfolio — remaining un-pinned default branches
# ============================================================================

class TestEvaluatePortfolioDefaultInjection:
    def _minimal_df(self, **overrides) -> pd.DataFrame:
        base = {
            "Symbol": ["AAPL"],
            "sector": ["Technology"],
            "position_size": [15000.0],
            "stop_loss_pct": [0.05],
            "Relative_Strength": [0.05],
        }
        base.update(overrides)
        return pd.DataFrame(base)

    def test_covar_proxy_defaults_to_zero_when_var_and_beta_columns_absent(self):
        """Neither 'VaR 95'/'VaR_95' nor 'Beta' present -- CoVaR Proxy must
        be the documented unconditional 0.0, not NaN, not a crash."""
        ee = _patched_ee()
        df = self._minimal_df()
        result = ee.evaluate_portfolio(df)
        assert (result["CoVaR Proxy"] == 0.0).all()

    def test_missing_sector_column_entirely_defaults_bf_to_zero(self):
        """Distinct from the zero-position-size case already covered
        elsewhere: here position_size IS nonzero, but 'sector' is absent
        entirely, hitting the else-branch fallback."""
        ee = _patched_ee()
        df = self._minimal_df().drop(columns=["sector"])
        bench = pd.DataFrame({"sector": ["Technology"], "weight": [1.0], "return": [0.02]})
        result = ee.evaluate_portfolio(df, bench)
        assert (result["BF_Allocation"] == 0.0).all()
        assert (result["BF_Selection"] == 0.0).all()

    def test_none_benchmark_df_defaults_bf_to_zero(self):
        """benchmark_df=None triggers the function's own default-arg
        substitution to an empty DataFrame, which is .empty -- same
        else-branch fallback as a missing sector column."""
        ee = _patched_ee()
        df = self._minimal_df()
        result = ee.evaluate_portfolio(df, benchmark_df=None)
        assert (result["BF_Allocation"] == 0.0).all()
        assert (result["BF_Selection"] == 0.0).all()

    def test_missing_position_size_and_stop_loss_and_var_injects_documented_defaults(self):
        """Missing position_size/stop_loss_pct/VaR 95 simultaneously triggers
        silent numeric defaults (10000.0 / 0.05) for portfolio-heat sizing
        purposes. This IS an intentional, pre-existing sizing fallback (not a
        bug this sweep should flag) -- the test pins current behavior so a
        future refactor can't silently change the sizing assumption."""
        ee = _patched_ee()
        df = pd.DataFrame({"Symbol": ["AAPL"], "sector": ["Technology"], "Relative_Strength": [0.05]})
        result = ee.evaluate_portfolio(df)
        assert (result["position_size"] == 10000.0).all()
        assert (result["stop_loss_pct"] == 0.05).all()
        assert "Portfolio_Heat" in result.columns
        assert not result["Portfolio_Heat"].isna().any()

    def test_realized_slippage_is_nan_not_zero_with_no_trade_history(self):
        """Distinct from research_engine.calculate_realized_slippage's OWN
        0.0-on-bad-input contract (a different function entirely) --
        evaluate_portfolio's per-row 'Realized Slippage' column stays NaN
        (never fabricated 0.0) when there is no matching trade history."""
        ee = _patched_ee()
        df = self._minimal_df()
        result = ee.evaluate_portfolio(df)
        assert math.isnan(result["Realized Slippage"].iloc[0])
