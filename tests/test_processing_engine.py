"""
tests/test_processing_engine.py
================================
Unit, NaN-vs-fabrication (CONSTRAINT #4), and lookahead-bias coverage for
processing_engine.py — the vectorized technical/fundamental calculation core
that every signal module and report ultimately reads from.

This file did not exist prior to this change; processing_engine.py was
previously exercised only incidentally via tests/test_bug_fixes.py's
six-bug regression suite. This file covers the remaining surface:
calculate_graham_number, calculate_gordon_fair_value edge cases beyond the
BUG-3 regression, process_macro_regime, calculate_technical_metrics,
calculate_fundamental_metrics, compile_dashboard, and a lookahead
perturbation proof for calculate_momentum_metrics's shift-based ROC columns.
"""

import logging
import math
import time
from unittest import mock

import numpy as np
import pandas as pd
import pytest
import math

from dto_models import FundamentalDataDTO, MacroEconomicDTO
from processing_engine import ProcessingEngine
from settings import settings

try:
    from tests.lookahead_check import verify_no_lookahead
except ImportError:
    from lookahead_check import verify_no_lookahead


# ============================================================================
# Fixtures / helpers
# ============================================================================

def _ohlcv(n: int, seed: int = 0, start: float = 100.0) -> pd.DataFrame:
    """Deterministic synthetic OHLCV history, n trading days."""
    rng = np.random.RandomState(seed)
    dates = pd.date_range("2020-01-01", periods=n, freq="B")
    close = start + np.cumsum(rng.normal(0, 1.0, n))
    close = np.maximum(close, 1.0)  # keep strictly positive
    high = close + rng.uniform(0, 1.0, n)
    low = close - rng.uniform(0, 1.0, n)
    open_p = close + rng.normal(0, 0.3, n)
    volume = rng.randint(100_000, 1_000_000, n).astype(float)
    return pd.DataFrame(
        {"Open": open_p, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=dates,
    )


def _fund_dto(
    ticker="AAPL",
    pe_ratio=20.0,
    pb_ratio=8.0,
    dividend_yield=0.02,
    book_value=12.0,
    eps_trailing=5.0,
    dividend_growth_rate=0.04,
    payout_ratio=0.2,
    sector="Technology",
    market_cap=2_000_000_000.0,
    price=150.0,
    beta=1.1,
    extra_info=None,
) -> FundamentalDataDTO:
    dto = FundamentalDataDTO(
        ticker=ticker,
        pe_ratio=pe_ratio,
        pb_ratio=pb_ratio,
        dividend_yield=dividend_yield,
        book_value=book_value,
        eps_trailing=eps_trailing,
        dividend_growth_rate=dividend_growth_rate,
        payout_ratio=payout_ratio,
        sector=sector,
        company_name=f"{ticker} Inc.",
        market_cap=market_cap,
        price=price,
        beta=beta,
    )
    dto.raw_info = {
        "regularMarketPrice": price,
        "sector": sector,
        **(extra_info or {}),
    }
    return dto


def _slow_historical_store(sleep_seconds: float = 0.2) -> mock.Mock:
    """A fake HistoricalStore instance whose get_fundamentals() does a REAL
    time.sleep() before returning an empty typed dict -- proof, not
    assumption, that a caller's timeout bound is real (mirrors
    tests/test_data_engine_macro_history.py's _BlackHoleServer-based FRED
    timeout tests: a mock that returns instantly could never expose a
    missing time budget)."""
    def _get_fundamentals(ticker, max_age_days=None, provider=None):
        time.sleep(sleep_seconds)
        return {}

    fake_store = mock.Mock()
    fake_store.get_fundamentals = mock.Mock(side_effect=_get_fundamentals)
    return fake_store


@pytest.fixture
def engine():
    return ProcessingEngine()


# ============================================================================
# calculate_graham_number
# ============================================================================

class TestGrahamNumber:
    def test_normal_case(self, engine):
        result = engine.calculate_graham_number(eps=5.0, book_value=12.0)
        expected = round(math.sqrt(22.5 * 5.0 * 12.0), 2)
        assert result == expected

    @pytest.mark.parametrize("eps,bv", [(0.0, 12.0), (-1.0, 12.0), (5.0, 0.0), (5.0, -2.0)])
    def test_non_positive_inputs_return_nan(self, engine, eps, bv):
        assert math.isnan(engine.calculate_graham_number(eps=eps, book_value=bv))

    def test_none_inputs_return_nan(self, engine):
        assert math.isnan(engine.calculate_graham_number(eps=None, book_value=12.0))
        assert math.isnan(engine.calculate_graham_number(eps=5.0, book_value=None))

    def test_nan_inputs_return_nan(self, engine):
        assert math.isnan(engine.calculate_graham_number(eps=float("nan"), book_value=12.0))
        assert math.isnan(engine.calculate_graham_number(eps=5.0, book_value=float("nan")))

    def test_exception_path_returns_nan_not_raises(self, engine):
        # A non-numeric type triggers the except branch rather than propagating.
        assert math.isnan(engine.calculate_graham_number(eps="not-a-number", book_value=12.0))


# ============================================================================
# calculate_gordon_fair_value — edge cases beyond the BUG-3 regression suite
# ============================================================================

class TestGordonFairValueEdgeCases:
    def test_nan_growth_rate_treated_as_zero(self):
        pe = ProcessingEngine()
        pe.required_return_rate = 0.10
        result = pe.calculate_gordon_fair_value(100.0, 0.05, float("nan"))
        expected = round((100.0 * 0.05 * 1.0) / 0.10, 2)
        assert math.isclose(result, expected, rel_tol=1e-5)

    def test_none_growth_rate_treated_as_zero(self):
        pe = ProcessingEngine()
        pe.required_return_rate = 0.10
        result = pe.calculate_gordon_fair_value(100.0, 0.05, None)
        expected = round((100.0 * 0.05 * 1.0) / 0.10, 2)
        assert math.isclose(result, expected, rel_tol=1e-5)

    def test_growth_exactly_at_cap_boundary(self):
        """g_raw exactly equal to r - 0.01 should NOT be further reduced."""
        pe = ProcessingEngine()
        pe.required_return_rate = 0.10
        g_raw = 0.09  # exactly r - 0.01
        result = pe.calculate_gordon_fair_value(100.0, 0.05, g_raw)
        expected = round((100.0 * 0.05 * 1.09) / (0.10 - 0.09), 2)
        assert math.isclose(result, expected, rel_tol=1e-5)

    def test_exception_path_returns_nan(self):
        pe = ProcessingEngine()
        result = pe.calculate_gordon_fair_value("bad", 0.05, 0.04)
        assert math.isnan(result)


# ============================================================================
# process_macro_regime
# ============================================================================

class TestProcessMacroRegime:
    def test_dto_input_passthrough(self, engine):
        dto = MacroEconomicDTO(
            yield_curve_10y_2y=0.5, high_yield_oas=3.0, inflation_rate=2.0,
            hmm_risk_on_probability=0.8,
        )
        result = engine.process_macro_regime(dto)
        assert result["Regime"] == dto.market_regime
        assert result["HMM_Risk_On_Probability"] == 0.8

    def test_hmm_none_passthrough_not_fabricated(self, engine):
        """When the DTO carries no HMM opinion, the dict must carry None, not
        a fabricated probability (e.g. 0.5)."""
        dto = MacroEconomicDTO(yield_curve_10y_2y=0.5, high_yield_oas=3.0, inflation_rate=2.0)
        result = engine.process_macro_regime(dto)
        assert result["HMM_Risk_On_Probability"] is None

    def test_dict_input_is_converted(self, engine):
        raw = {"T10Y2Y": -0.5, "BAMLH0A0HYM2": 7.0, "CPIAUCSL_YoY": 3.0}
        result = engine.process_macro_regime(raw)
        assert "Regime" in result
        assert isinstance(result["Real_Yield"], float)

    def test_dict_input_missing_vixcls_fails_closed(self, engine):
        """CONSTRAINT #4/#6 regression guard: this defensive dict->DTO branch
        used to silently keep vix_value/sahm_rule_indicator at the DTO class
        defaults (15.0/0.0) and never flag the gap. A dict missing VIXCLS
        (present here) now forces Regime to RECESSION regardless of what the
        T10Y2Y/BAMLH0A0HYM2 values alone would imply."""
        raw = {"T10Y2Y": 1.0, "BAMLH0A0HYM2": 2.0, "CPIAUCSL_YoY": 3.0}  # no VIXCLS
        result = engine.process_macro_regime(raw)
        assert result["Regime"] == "RECESSION"

    def test_dict_input_with_all_keys_present_is_not_forced(self, engine):
        raw = {
            "T10Y2Y": 1.0, "BAMLH0A0HYM2": 2.0, "CPIAUCSL_YoY": 3.0,
            "VIXCLS": 15.0, "SAHMREALTIME": 0.0,
        }
        result = engine.process_macro_regime(raw)
        assert result["Regime"] == "RISK ON"

    def test_malformed_input_degrades_to_neutral_fallback(self, engine):
        """An object missing the expected attributes must hit the except
        branch and return the documented neutral fallback dict, never raise."""
        class _Garbage:
            pass

        result = engine.process_macro_regime(_Garbage())
        assert result["Regime"] == "Neutral"
        assert result["Real_Yield"] == 0.0
        assert result["HMM_Risk_On_Probability"] is None


# ============================================================================
# calculate_technical_metrics
# ============================================================================

class TestCalculateTechnicalMetrics:
    def test_short_history_ticker_is_skipped(self, engine):
        """Tickers with < 30 rows must be excluded from the results dict,
        never produce a half-computed/fabricated row."""
        raw = {"SHORT": _ohlcv(10)}
        result = engine.calculate_technical_metrics(raw)
        assert "SHORT" not in result

    def test_empty_df_ticker_is_skipped(self, engine):
        raw = {"EMPTY": pd.DataFrame()}
        result = engine.calculate_technical_metrics(raw)
        assert "EMPTY" not in result

    def test_missing_spy_does_not_crash(self, engine):
        """SPY absent from raw_tech_data must not raise; relative strength
        falls back to spy_return=0.0 rather than propagating an exception."""
        raw = {"AAPL": _ohlcv(60, seed=1)}
        result = engine.calculate_technical_metrics(raw)
        assert "AAPL" in result
        assert isinstance(result["AAPL"]["RS vs SPY"], float)

    def test_sufficient_history_produces_real_indicators(self, engine):
        raw = {"AAPL": _ohlcv(300, seed=2), "SPY": _ohlcv(300, seed=3, start=400.0)}
        result = engine.calculate_technical_metrics(raw)
        row = result["AAPL"]
        for key in ("RSI", "RSI_2", "MACD_Line", "ATR", "SMA_50", "SMA_200", "Aroon Oscillator"):
            assert key in row
            assert not pd.isna(row[key])

    def test_realized_vol_60d_is_nan_for_short_history(self, engine):
        """Constraint #4: a ticker with >= 30 but < 60 valid daily returns must
        surface NaN (not a fabricated low-vol reading) for Realized_Vol_60D."""
        raw = {"AAPL": _ohlcv(45, seed=4)}
        result = engine.calculate_technical_metrics(raw)
        assert math.isnan(result["AAPL"]["Realized_Vol_60D"])

    def test_one_bad_ticker_does_not_abort_others(self, engine):
        """Per-ticker try/except: a ticker that raises during calculation must
        not prevent other tickers' rows from being produced."""
        good = _ohlcv(120, seed=5)
        bad = _ohlcv(120, seed=6)
        bad.loc[bad.index[-1], "Close"] = np.nan  # still long enough, just dirty tail
        raw = {"GOOD": good, "BAD": bad}
        result = engine.calculate_technical_metrics(raw)
        assert "GOOD" in result

    def test_sortino_near_zero_noise_downside_std_is_nan_not_absurd(self, engine):
        """Regression for the same degenerate-std bug fixed in
        validation/metrics.py::sharpe_ratio (PR #501): a smooth exponential
        price decay produces a daily-return series that's mathematically
        constant, but pandas' two-pass std() accumulates floating-point
        rounding noise over many rows, landing near (not bit-identical to)
        0.0. The old `if downside_std > 0:` check let this slip through and
        computed an absurd, unbounded Sortino instead of the honest NaN
        (CONSTRAINT #4)."""
        n = 300
        r = 0.0001
        dates = pd.date_range("2020-01-01", periods=n, freq="B")
        close = pd.Series([100.0 * (1.0 - r) ** i for i in range(n)], index=dates)
        df = pd.DataFrame(
            {
                "Open": close, "High": close + 0.01, "Low": close - 0.01,
                "Close": close, "Volume": 500_000.0,
            },
            index=dates,
        )
        # Confirm the premise: downside std is nonzero (floating noise), not exactly 0.0.
        downside = df["Close"].pct_change().dropna()
        downside = downside[downside < 0]
        assert downside.std() != 0.0
        assert downside.std() < 1e-12

        result = engine.calculate_technical_metrics({"TICKER": df})
        sortino = result["TICKER"]["Sortino Ratio"]
        assert math.isnan(sortino), f"expected NaN, got an absurd value: {sortino}"


# ============================================================================
# calculate_momentum_metrics — lookahead perturbation proof
# ============================================================================

class TestMomentumMetricsLookahead:
    """The shift(1)/shift(N) ROC columns claim to be lookahead-free. Prove it
    with the same perturb-the-future harness used by
    tests/test_indicators_lookahead.py, rather than just trusting the shift()
    call sites by inspection."""

    def test_roc_12m_no_lookahead(self, engine):
        df = _ohlcv(400, seed=7)

        def calc(data, t):
            sub = data.iloc[: t + 1].copy()
            out = engine.calculate_momentum_metrics(sub)
            return out["ROC_12M"].iloc[-1]

        assert verify_no_lookahead(calc, df, t=350)

    def test_realized_vol_60d_no_lookahead(self, engine):
        df = _ohlcv(400, seed=9)

        def calc(data, t):
            sub = data.iloc[: t + 1].copy()
            out = engine.calculate_momentum_metrics(sub)
            return out["Realized_Vol_60D"].iloc[-1]

        assert verify_no_lookahead(calc, df, t=350)

    def test_momentum_vol_scaled_no_lookahead(self, engine):
        df = _ohlcv(400, seed=10)

        def calc(data, t):
            sub = data.iloc[: t + 1].copy()
            out = engine.calculate_momentum_metrics(sub)
            return out["Momentum_Vol_Scaled"].iloc[-1]

        assert verify_no_lookahead(calc, df, t=350)

    def test_momentum_vol_scaled_is_nan_not_fabricated_when_roc_12m_unavailable(self, engine):
        """Finding 17 regression: the np.where(...) fallback previously
        fabricated 0.0 whenever the vol-scaling inputs weren't ready (e.g.
        ROC_12M still NaN before its 253-day lookback fills, even though the
        realized_vol_60d leg is already valid by then) -- must be NaN, never
        a fabricated "flat momentum" 0.0 (CONSTRAINT #4)."""
        df = _ohlcv(400, seed=11)
        out = engine.calculate_momentum_metrics(df)

        # ROC_12M requires 253 rows of lookback (shift(253)), so rows before
        # index 253 are NaN there while realized_vol_60d (needs only ~61
        # rows) is already valid -- this window is exactly the fallback
        # branch under test.
        window = out.iloc[100:253]
        assert window["ROC_12M"].isna().all()
        assert window["Realized_Vol_60D"].notna().all()
        assert window["Momentum_Vol_Scaled"].isna().all()


# ============================================================================
# calculate_fundamental_metrics
# ============================================================================

class TestCalculateFundamentalMetrics:
    """quant_platform.db is per-machine runtime state (not checked into git),
    so these tests never depend on its on-disk content. Most tests here
    disable the HistoricalStore overlay so results are hermetic and depend
    only on the injected DTO -- the one test that targets the HistoricalStore
    integration mocks HistoricalStore directly rather than reading real data."""

    @pytest.fixture(autouse=True)
    def _disable_historical_store(self, disable_historical_store):
        """Shim wrapping the shared tests/conftest.py fixture in an
        autouse=True local fixture, so every test in this class gets it
        without each one requesting it by name."""
        yield

    def test_basic_happy_path(self, engine):
        dtos = {"AAPL": _fund_dto()}
        result = engine.calculate_fundamental_metrics(dtos)
        assert "AAPL" in result
        row = result["AAPL"]
        assert row["Symbol"] == "AAPL"
        assert row["sector"] == "Technology"

    def test_none_dto_is_skipped(self, engine):
        dtos = {"AAPL": _fund_dto(), "MISSING": None}
        result = engine.calculate_fundamental_metrics(dtos)
        assert "MISSING" not in result
        assert "AAPL" in result

    def test_book_to_market_nan_when_pb_ratio_missing(self, engine):
        """Constraint #4: book_to_market must be NaN (not 0.0) when pb_ratio
        is unavailable -- a 0.0 reading would imply infinite value, not
        'unknown', and would silently corrupt the multifactor composite."""
        dto = _fund_dto(pb_ratio=None)
        result = engine.calculate_fundamental_metrics({"AAPL": dto})
        assert math.isnan(result["AAPL"]["book_to_market"])

    def test_earnings_yield_nan_when_pe_ratio_zero_or_negative(self, engine):
        dto = _fund_dto(pe_ratio=-5.0)
        result = engine.calculate_fundamental_metrics({"AAPL": dto})
        assert math.isnan(result["AAPL"]["earnings_yield"])

    def test_low_vol_score_uses_realized_vol_map(self, engine):
        dto = _fund_dto()
        result = engine.calculate_fundamental_metrics(
            {"AAPL": dto}, realized_vol_60d_map={"AAPL": 0.25}
        )
        assert result["AAPL"]["low_vol_score"] == -0.25

    def test_low_vol_score_nan_when_vol_missing(self, engine):
        """No entry in realized_vol_60d_map -> NaN, never a fabricated 0.0
        'no volatility' reading."""
        dto = _fund_dto()
        result = engine.calculate_fundamental_metrics({"AAPL": dto}, realized_vol_60d_map={})
        assert math.isnan(result["AAPL"]["low_vol_score"])

    def test_quality_factor_score_uses_mean_of_available_metrics(self, engine):
        """Quality = MEAN of available profitability metrics among
        {returnOnEquity, operatingMargins, grossMargins}. Here only roe (0.20)
        and operating margin (0.10) are present -> mean = 0.15 (not the old sum
        0.30). A mean keeps 1/2/3-metric tickers on one z-score scale."""
        dto = _fund_dto(extra_info={"returnOnEquity": 0.20, "operatingMargins": 0.10})
        result = engine.calculate_fundamental_metrics({"AAPL": dto})
        assert math.isclose(result["AAPL"]["quality_factor_score"], 0.15, rel_tol=1e-6)

    def test_quality_factor_score_consumes_gross_margins_alone(self, engine):
        """REUSE proof: grossMargins (emitted by data/yahoo_fundamentals.py) is
        now folded into the quality factor. With ONLY grossMargins present (no
        returnOnEquity, no operatingMargins), the score equals grossMargins
        itself -- a non-NaN value -- instead of the old NaN/leverage fallback."""
        dto = _fund_dto(extra_info={"grossMargins": 0.42})
        result = engine.calculate_fundamental_metrics({"AAPL": dto})
        assert math.isclose(result["AAPL"]["quality_factor_score"], 0.42, rel_tol=1e-6)

    def test_quality_factor_score_falls_back_to_negative_debt_to_equity(self, engine):
        """When ROE/margin are absent, quality proxy = -debt_to_equity."""
        dto = _fund_dto(extra_info={"debtToEquity": 50.0})  # -> 0.5 after /100
        result = engine.calculate_fundamental_metrics({"AAPL": dto})
        assert math.isclose(result["AAPL"]["quality_factor_score"], -0.5, rel_tol=1e-6)

    def test_quality_factor_score_nan_when_no_data_at_all(self, engine):
        dto = _fund_dto()  # no roe/margin/debt info
        result = engine.calculate_fundamental_metrics({"AAPL": dto})
        assert math.isnan(result["AAPL"]["quality_factor_score"])

    def test_log_market_cap_nan_for_zero_market_cap(self, engine):
        dto = _fund_dto(market_cap=0.0)
        result = engine.calculate_fundamental_metrics({"AAPL": dto})
        assert math.isnan(result["AAPL"]["log_market_cap"])

    def test_institutional_velocity_preserves_genuine_zero_change(self, engine):
        """Finding 26 regression: a genuine 0.0 institutional-change reading
        was previously conflated with 'missing' (`if not inst_change or
        inst_change == 0.0`) and silently overwritten by the short-interest
        fallback proxy. Construct short-interest data that WOULD produce a
        non-zero proxy if (wrongly) applied on top of a genuine 0.0 net
        change, and prove the genuine reading survives instead."""
        dto = _fund_dto(extra_info={
            "heldPercentInstitutions": 0.60,
            "netPercentInstitutionsSharesOut": 0.0,  # genuine 0.0 net change
            "sharesShortPriorMonth": 2_000_000.0,
            "sharesShort": 1_000_000.0,
            "sharesOutstanding": 100_000_000.0,
        })
        result = engine.calculate_fundamental_metrics({"AAPL": dto})
        # calculate_institutional_velocity itself treats a 0.0 quarterly
        # change as "no signal" (returns 0.0) -- the point under test is
        # that this 0.0 is the genuinely PRESERVED reading, not the proxy's
        # 0.01 non-zero value ((2,000,000-1,000,000)/100,000,000) that would
        # have overwritten it before the fix.
        assert result["AAPL"]["Institutional Velocity"] == pytest.approx(0.0)

    def test_institutional_velocity_proxy_still_fires_when_genuinely_absent(self, engine):
        """Symmetry check: when the institutional-change field is genuinely
        ABSENT (None), the short-interest fallback proxy must still apply."""
        dto = _fund_dto(extra_info={
            "heldPercentInstitutions": 0.60,
            # no netPercentInsiderShares / netPercentInstitutionsSharesOut key at all
            "sharesShortPriorMonth": 2_000_000.0,
            "sharesShort": 1_000_000.0,
            "sharesOutstanding": 100_000_000.0,
        })
        result = engine.calculate_fundamental_metrics({"AAPL": dto})
        assert result["AAPL"]["Institutional Velocity"] != 0.0

    def test_one_bad_dto_does_not_abort_others(self, engine):
        """A dto that raises mid-calculation must not prevent other tickers
        from producing a row."""
        good = _fund_dto("GOOD")
        bad = _fund_dto("BAD")
        bad.raw_info = "not-a-dict"  # .get() on a str -> AttributeError inside try/except
        result = engine.calculate_fundamental_metrics({"GOOD": good, "BAD": bad})
        assert "GOOD" in result

    def test_historical_store_failure_degrades_gracefully(self, engine):
        """When settings.HISTORICAL_STORE_ENABLED=True but HistoricalStore
        construction raises, calculate_fundamental_metrics must still return
        a complete result via the pre-Phase-3 DTO-only path (dead-letter
        resilience, CONSTRAINT #6)."""
        dto = _fund_dto()
        with mock.patch("settings.settings.HISTORICAL_STORE_ENABLED", True), \
             mock.patch("data.historical_store.HistoricalStore", side_effect=RuntimeError("db down")):
            result = engine.calculate_fundamental_metrics({"AAPL": dto})
        assert "AAPL" in result

    def test_historical_store_budget_bounds_the_loop_and_preserves_all_tickers(
        self, engine
    ):
        """Structural timeout fix: PROCESSING_FUNDAMENTALS_MAX_SECONDS_PER_CYCLE
        bounds the per-ticker HistoricalStore.get_fundamentals() loop. Proven
        with a REAL time.sleep() in the fake (not an instantly-returning
        mock) so this test cannot pass merely because the mock happens to be
        fast -- mirrors tests/test_data_engine_macro_history.py's
        genuinely-bounds-a-real-blocking-call idiom. A tiny budget (0.05s,
        smaller than one 0.2s sleep) must be exhausted well before all 5
        tickers are processed, and CONSTRAINT #6 dead-letter resilience means
        every ticker must still land in the result via the DTO-only
        fallback -- nothing silently dropped just because its live refresh
        was skipped."""
        tickers = [f"T{i}" for i in range(5)]
        dtos = {t: _fund_dto(ticker=t) for t in tickers}
        fake_store = _slow_historical_store(sleep_seconds=0.2)

        with mock.patch("settings.settings.HISTORICAL_STORE_ENABLED", True), \
             mock.patch(
                 "settings.settings.PROCESSING_FUNDAMENTALS_MAX_SECONDS_PER_CYCLE",
                 0.05,
             ), \
             mock.patch(
                 "data.historical_store.HistoricalStore", return_value=fake_store
             ), \
             mock.patch("data.market_data.get_provider", return_value=mock.Mock()):
            started = time.monotonic()
            result = engine.calculate_fundamental_metrics(dtos)
            elapsed = time.monotonic() - started

        # Well under 5 * 0.2s = 1.0s -- proves the loop stopped calling the
        # slow fake long before it would have processed all 5 tickers.
        assert elapsed < 1.0
        assert set(result.keys()) == set(tickers)
        assert fake_store.get_fundamentals.call_count < 5

    def test_historical_store_budget_logs_exactly_one_warning(self, engine, caplog):
        """The deadline is exceeded on every ticker from (at latest) the 2nd
        one onward, but the sticky 'already logged' flag must keep the
        warning to exactly one emission, not one per skipped ticker."""
        tickers = [f"T{i}" for i in range(5)]
        dtos = {t: _fund_dto(ticker=t) for t in tickers}
        fake_store = _slow_historical_store(sleep_seconds=0.2)

        caplog.set_level(logging.WARNING)
        with mock.patch("settings.settings.HISTORICAL_STORE_ENABLED", True), \
             mock.patch(
                 "settings.settings.PROCESSING_FUNDAMENTALS_MAX_SECONDS_PER_CYCLE",
                 0.05,
             ), \
             mock.patch(
                 "data.historical_store.HistoricalStore", return_value=fake_store
             ), \
             mock.patch("data.market_data.get_provider", return_value=mock.Mock()):
            engine.calculate_fundamental_metrics(dtos)

        budget_records = [
            r for r in caplog.records
            if "PROCESSING_FUNDAMENTALS_MAX_SECONDS_PER_CYCLE" in r.message
        ]
        assert len(budget_records) == 1
        assert budget_records[0].levelno == logging.WARNING

    def test_processing_fundamentals_max_seconds_per_cycle_default(self):
        """Pin the documented production default (60.0s) so a future,
        unintentional edit to settings.py can't silently shrink/grow the
        bound without a test noticing -- mirrors
        tests/test_pipeline_runner.py::test_default_timeout_setting_is_generous's
        exact-pin style for the sibling PIPELINE_STEP_TIMEOUT_SECONDS
        setting."""
        assert settings.PROCESSING_FUNDAMENTALS_MAX_SECONDS_PER_CYCLE == 60.0

    def test_historical_store_fast_path_is_unaffected_by_the_new_budget(
        self, engine, caplog
    ):
        """Regression guard: with the default (generous, un-mocked) budget in
        effect and a get_fundamentals() that returns instantly, results must
        come back exactly as before this fix, with zero spurious budget
        warnings logged."""
        dto = _fund_dto()
        fake_store = mock.Mock()
        fake_store.get_fundamentals = mock.Mock(return_value={})

        caplog.set_level(logging.WARNING)
        with mock.patch("settings.settings.HISTORICAL_STORE_ENABLED", True), \
             mock.patch(
                 "data.historical_store.HistoricalStore", return_value=fake_store
             ), \
             mock.patch("data.market_data.get_provider", return_value=mock.Mock()):
            result = engine.calculate_fundamental_metrics({"AAPL": dto})

        assert "AAPL" in result
        assert fake_store.get_fundamentals.call_count == 1
        budget_records = [
            r for r in caplog.records
            if "PROCESSING_FUNDAMENTALS_MAX_SECONDS_PER_CYCLE" in r.message
        ]
        assert not budget_records


# ============================================================================
# compile_dashboard
# ============================================================================

class TestCompileDashboard:
    def test_price_prefers_fundamental_over_technical(self, engine):
        tech = {"AAPL": {"Price_Tech": 99.0}}
        fund = {"AAPL": {"Price_Fund": 150.0}}
        df = engine.compile_dashboard(tech, fund, {"Regime": "RISK ON"})
        row = df[df["Symbol"] == "AAPL"].iloc[0]
        assert row["Price"] == 150.0

    def test_price_falls_back_to_technical_when_fundamental_zero(self, engine):
        tech = {"AAPL": {"Price_Tech": 99.0}}
        fund = {"AAPL": {"Price_Fund": 0.0}}
        df = engine.compile_dashboard(tech, fund, {"Regime": "RISK ON"})
        row = df[df["Symbol"] == "AAPL"].iloc[0]
        assert row["Price"] == 99.0

    def test_hmm_probability_none_becomes_nan_not_fabricated(self, engine):
        df = engine.compile_dashboard(
            {"AAPL": {"Price_Tech": 100.0}}, {}, {"Regime": "RISK ON", "HMM_Risk_On_Probability": None}
        )
        row = df[df["Symbol"] == "AAPL"].iloc[0]
        assert math.isnan(row["HMM_Risk_On_Probability"])

    def test_hmm_probability_passthrough_when_present(self, engine):
        df = engine.compile_dashboard(
            {"AAPL": {"Price_Tech": 100.0}}, {}, {"Regime": "RISK ON", "HMM_Risk_On_Probability": 0.65}
        )
        row = df[df["Symbol"] == "AAPL"].iloc[0]
        assert row["HMM_Risk_On_Probability"] == 0.65

    def test_union_of_tech_and_fund_tickers(self, engine):
        tech = {"TECH_ONLY": {"Price_Tech": 50.0}}
        fund = {"FUND_ONLY": {"Price_Fund": 75.0}}
        df = engine.compile_dashboard(tech, fund, {"Regime": "NEUTRAL"})
        assert set(df["Symbol"]) == {"TECH_ONLY", "FUND_ONLY"}


class TestCalculateRollingBeta:
    """Tests for the module-level calculate_rolling_beta() -- rolling
    Cov(returns, spy_returns)/Var(spy_returns), distinct from the existing
    static point-in-time Beta column."""

    def _mk_price_df(self, closes):
        dates = pd.date_range("2026-01-01", periods=len(closes))
        return pd.DataFrame({"Close": closes}, index=dates)

    def test_known_beta_hand_computed(self):
        """A ticker whose daily returns are a known linear function of SPY's
        (return = 2 * spy_return) should yield a rolling beta of ~2.0 once
        the window fills."""
        from processing_engine import calculate_rolling_beta

        n = 80
        rng = np.random.RandomState(7)
        spy_rets = rng.normal(0.0005, 0.01, n)
        spy_closes = 400.0 * np.cumprod(1.0 + np.concatenate([[0.0], spy_rets]))
        ticker_rets = 2.0 * spy_rets
        ticker_closes = 100.0 * np.cumprod(1.0 + np.concatenate([[0.0], ticker_rets]))

        price_df = self._mk_price_df(ticker_closes)
        spy_df = self._mk_price_df(spy_closes)

        beta = calculate_rolling_beta(price_df, spy_df, window=30)
        assert not beta.empty
        # Last value should be very close to 2.0 (exact linear relationship).
        assert beta.iloc[-1] == pytest.approx(2.0, abs=1e-6)

    def test_insufficient_history_returns_nan_series(self):
        from processing_engine import calculate_rolling_beta

        price_df = self._mk_price_df([100.0 + i for i in range(10)])
        spy_df = self._mk_price_df([400.0 + i for i in range(10)])

        beta = calculate_rolling_beta(price_df, spy_df, window=60)
        # Fewer rows than the window -- every value NaN, never fabricated.
        assert beta.isna().all()

    def test_misaligned_index_restricts_to_overlap(self):
        from processing_engine import calculate_rolling_beta

        dates_a = pd.date_range("2026-01-01", periods=80)
        dates_b = pd.date_range("2026-02-01", periods=80)  # only partial overlap
        price_df = pd.DataFrame({"Close": 100.0 + np.arange(80)}, index=dates_a)
        spy_df = pd.DataFrame({"Close": 400.0 + np.arange(80)}, index=dates_b)

        beta = calculate_rolling_beta(price_df, spy_df, window=20)
        overlap_len = len(dates_a.intersection(dates_b))
        assert len(beta) == overlap_len

    def test_empty_or_missing_close_returns_empty_series(self):
        from processing_engine import calculate_rolling_beta

        empty_df = pd.DataFrame()
        valid_df = self._mk_price_df([100.0 + i for i in range(80)])

        assert calculate_rolling_beta(empty_df, valid_df).empty
        assert calculate_rolling_beta(valid_df, empty_df).empty

        no_close_df = pd.DataFrame({"Open": [1.0, 2.0]}, index=pd.date_range("2026-01-01", periods=2))
        assert calculate_rolling_beta(no_close_df, valid_df).empty
        assert calculate_rolling_beta(valid_df, no_close_df).empty

    def test_zero_variance_spy_window_yields_nan_not_crash(self):
        """A window where SPY is perfectly flat (Var=0) must degrade to NaN
        for that window via pandas' natural division behavior, not crash."""
        from processing_engine import calculate_rolling_beta

        n = 80
        price_df = self._mk_price_df(100.0 + np.random.RandomState(1).normal(0, 1, n).cumsum())
        spy_df = self._mk_price_df([400.0] * n)  # perfectly flat

        beta = calculate_rolling_beta(price_df, spy_df, window=20)
        assert not beta.empty
        # All defined-window values should be NaN or inf (0/0 or x/0), never
        # a fabricated finite number that pretends to be a real beta.
        tail = beta.iloc[20:]
        assert tail.apply(lambda v: pd.isna(v) or np.isinf(v)).all()

    def test_near_constant_spy_window_yields_nan_not_exploded_beta(self):
        """Finding 16 regression: a near-constant (not bit-identical) SPY
        window produces a rolling_var that is near-zero but not exactly 0.0
        due to floating-point noise -- the degenerate-std guard (< 1e-12)
        must catch this and yield NaN, not an exploded beta."""
        from processing_engine import calculate_rolling_beta

        n = 80
        price_df = self._mk_price_df(
            100.0 + np.random.RandomState(1).normal(0, 1, n).cumsum()
        )
        rng = np.random.RandomState(2)
        # SPY closes near-constant with floating-point-scale noise (~1e-10),
        # NOT bit-identical -- an exact `<= 0.0` check would not catch this.
        spy_closes = 400.0 + rng.normal(0, 1e-10, n)
        spy_df = self._mk_price_df(spy_closes)

        beta = calculate_rolling_beta(price_df, spy_df, window=20)
        assert not beta.empty
        tail = beta.iloc[20:]
        # Never a fabricated huge/finite value masquerading as a real beta.
        assert tail.apply(lambda v: pd.isna(v)).all()
