"""
tests/test_yahoo_fundamentals.py
================================
Fully-offline unit tests for ``data/yahoo_fundamentals.py`` — the pure,
I/O-free fundamental-metrics computation engine.

The math here is financial and every expected value is HAND-COMPUTED in the
test body (a wrong scale factor silently corrupts position sizing and the
multifactor signal, so we pin the exact numbers, not just "is finite").

No network, no ``yfinance``.  We build synthetic yfinance-shaped statement
frames directly: **rows = line-item labels (index)**, **columns = period dates
in DESCENDING order (newest first)**.

Classes
-------
* ``TestScaleRules``      — the two scale-critical rules + fraction ratios.
* ``TestValuationMath``   — bookValue/priceToBook/EPS/PE/marketCap/currentRatio.
* ``TestPayoutSign``      — the mandatory ``abs()`` on negative Cash Dividends Paid.
* ``TestBeta``            — Cov/Var beta with a known slope + <60-obs NaN guard.
* ``TestNaNDiscipline``   — CONSTRAINT #4: NaN-not-zero, independent degradation.
* ``TestAliasResolver``   — ``_row_latest`` / ``_ttm`` / alias-table fallback.
* ``TestContract``        — emitted keys ⊆ FUNDAMENTAL_KEYS; no leaked keys.
"""

import math

import numpy as np
import pandas as pd
import pytest

from data.yahoo_fundamentals import (
    EQUITY,
    FUNDAMENTAL_KEYS,
    _row_latest,
    _ttm,
    compute_fundamentals,
)


# --------------------------------------------------------------------------- #
# Synthetic-frame helpers.
# --------------------------------------------------------------------------- #
# yfinance convention: index = line-item labels, columns = period dates newest
# first (descending).
_A_DATES = pd.to_datetime(["2025-12-31", "2024-12-31"])          # annual
_Q_DATES = pd.to_datetime(
    ["2025-12-31", "2025-09-30", "2025-06-30", "2025-03-31"]     # quarterly
)


def stmt(rows: dict, dates=_Q_DATES) -> pd.DataFrame:
    """Build a statement frame.

    ``rows`` maps a line-item label -> list of values aligned to ``dates``
    (newest column first).  Returns a DataFrame indexed by the labels with the
    dates as columns.
    """
    cols = {}
    labels = list(rows.keys())
    for i, d in enumerate(dates):
        cols[d] = [rows[label][i] for label in labels]
    return pd.DataFrame(cols, index=labels)


def base_kwargs(**overrides):
    """Reasonable defaults for a fully-populated compute_fundamentals() call.

    Individual tests override just the pieces they exercise.  Numbers are chosen
    so the canonical hand-computed outputs hold:
      equity=1000, shares=100 -> bookValue=10; price=150 -> priceToBook=15
      total_debt=1500 -> debtToEquity=150.0
      net income TTM=200 -> returnOnEquity=0.20
      diluted EPS 0.5*4 -> trailingEps=2.0 -> trailingPE=75.0
    """
    balance_sheet = stmt(
        {
            "Stockholders Equity": [1000.0, 900.0],
            "Total Debt": [1500.0, 1400.0],
            "Current Assets": [800.0, 700.0],
            "Current Liabilities": [400.0, 350.0],
        },
        dates=_A_DATES,
    )
    income_stmt_quarterly = stmt(
        {
            "Net Income": [50.0, 50.0, 50.0, 50.0],
            "Total Revenue": [250.0, 250.0, 250.0, 250.0],
            "Operating Income": [30.0, 30.0, 30.0, 30.0],
            "Gross Profit": [100.0, 100.0, 100.0, 100.0],
            "Diluted EPS": [0.5, 0.5, 0.5, 0.5],
        }
    )
    income_stmt = stmt(
        {
            "Net Income": [200.0, 180.0],
            "Total Revenue": [1000.0, 800.0],
        },
        dates=_A_DATES,
    )
    cashflow_quarterly = stmt(
        {"Cash Dividends Paid": [-20.0, -20.0, -20.0, -20.0]}
    )
    cashflow = stmt(
        {"Cash Dividends Paid": [-80.0, -70.0]}, dates=_A_DATES
    )
    dividends = pd.Series(
        [1.0, 1.0, 1.0, 1.0],
        index=pd.to_datetime(
            ["2025-01-15", "2025-04-15", "2025-07-15", "2025-10-15"]
        ),
    )
    kwargs = dict(
        ticker="TEST",
        price=150.0,
        shares_current=100.0,
        shares_diluted=100.0,
        income_stmt=income_stmt,
        income_stmt_quarterly=income_stmt_quarterly,
        balance_sheet=balance_sheet,
        cashflow=cashflow,
        cashflow_quarterly=cashflow_quarterly,
        dividends=dividends,
        inst_holders=None,
        stock_returns=None,
        market_returns=None,
        sector="Technology",
        company_name="Test Co",
    )
    kwargs.update(overrides)
    return kwargs


# --------------------------------------------------------------------------- #
# 1. Scale-critical rules (HIGHEST PRIORITY).
# --------------------------------------------------------------------------- #
class TestScaleRules:
    def test_dividend_yield_is_a_fraction(self):
        """4.00/yr dividends at price 150 -> 0.0267 FRACTION (not 2.67, not 2.67e-4)."""
        res = compute_fundamentals(**base_kwargs())
        assert res["dividendYield"] == pytest.approx(4.0 / 150.0, abs=1e-4)
        assert res["dividendYield"] == pytest.approx(0.026667, abs=1e-4)
        # Guard against the two wrong scalings explicitly.
        assert not res["dividendYield"] == pytest.approx(2.6667, abs=1e-2)
        assert res["dividendYield"] < 1.0

    def test_debt_to_equity_times_100(self):
        """Total Debt 1500 / Equity 1000 -> 150.0 (x100), NOT 1.5."""
        res = compute_fundamentals(**base_kwargs())
        assert res["debtToEquity"] == pytest.approx(150.0, abs=1e-6)
        assert res["debtToEquity"] != pytest.approx(1.5, abs=1e-6)

    def test_return_on_equity_is_a_fraction(self):
        """TTM Net Income 200 / Equity 1000 -> 0.20 (not 20)."""
        res = compute_fundamentals(**base_kwargs())
        assert res["returnOnEquity"] == pytest.approx(0.20, abs=1e-9)

    def test_operating_margin_is_a_fraction(self):
        """Operating Income TTM 120 / Revenue TTM 1000 -> 0.12."""
        res = compute_fundamentals(**base_kwargs())
        assert res["operatingMargins"] == pytest.approx(0.12, abs=1e-9)
        assert res["operatingMargins"] < 1.0

    def test_gross_margin_is_a_fraction(self):
        """Gross Profit TTM 400 / Revenue TTM 1000 -> 0.40."""
        res = compute_fundamentals(**base_kwargs())
        assert res["grossMargins"] == pytest.approx(0.40, abs=1e-9)
        assert res["grossMargins"] < 1.0


# --------------------------------------------------------------------------- #
# 2. Core valuation math (hand-computed).
# --------------------------------------------------------------------------- #
class TestValuationMath:
    def test_book_value(self):
        """equity 1000 / shares 100 -> 10.0."""
        res = compute_fundamentals(**base_kwargs())
        assert res["bookValue"] == pytest.approx(10.0, abs=1e-9)

    def test_price_to_book(self):
        """price 150 / bookValue 10 -> 15.0."""
        res = compute_fundamentals(**base_kwargs())
        assert res["priceToBook"] == pytest.approx(15.0, abs=1e-9)

    def test_trailing_eps_from_four_quarters(self):
        """Diluted EPS 0.5 x 4 -> 2.0."""
        res = compute_fundamentals(**base_kwargs())
        assert res["trailingEps"] == pytest.approx(2.0, abs=1e-9)

    def test_trailing_pe(self):
        """price 150 / eps 2.0 -> 75.0."""
        res = compute_fundamentals(**base_kwargs())
        assert res["trailingPE"] == pytest.approx(75.0, abs=1e-9)

    def test_trailing_pe_nan_when_eps_negative(self):
        """Negative EPS quarters -> trailingPE is NaN (mirrors Yahoo)."""
        neg_q = stmt(
            {
                "Net Income": [-50.0, -50.0, -50.0, -50.0],
                "Total Revenue": [250.0, 250.0, 250.0, 250.0],
                "Diluted EPS": [-0.5, -0.5, -0.5, -0.5],
            }
        )
        res = compute_fundamentals(**base_kwargs(income_stmt_quarterly=neg_q))
        assert math.isnan(res["trailingPE"])
        # trailingEps itself is a real (negative) number, not NaN.
        assert res["trailingEps"] == pytest.approx(-2.0, abs=1e-9)

    def test_market_cap(self):
        """price 150 x shares 100 -> 15000."""
        res = compute_fundamentals(**base_kwargs())
        assert res["marketCap"] == pytest.approx(15000.0, abs=1e-6)

    def test_current_ratio(self):
        """Current Assets 800 / Current Liabilities 400 -> 2.0."""
        res = compute_fundamentals(**base_kwargs())
        assert res["currentRatio"] == pytest.approx(2.0, abs=1e-9)


# --------------------------------------------------------------------------- #
# 3. payoutRatio sign trap — the mandatory abs().
# --------------------------------------------------------------------------- #
class TestPayoutSign:
    def test_payout_ratio_is_positive_from_negative_cash_outflow(self):
        """Cash Dividends Paid is a NEGATIVE outflow (-80); NI TTM 200 -> +0.40."""
        res = compute_fundamentals(**base_kwargs())
        assert res["payoutRatio"] == pytest.approx(0.40, abs=1e-9)
        assert res["payoutRatio"] > 0.0

    def test_payout_ratio_positive_even_with_annual_fallback(self):
        """Empty quarterly cashflow -> annual -80 fallback still yields +0.40."""
        res = compute_fundamentals(
            **base_kwargs(cashflow_quarterly=pd.DataFrame())
        )
        assert res["payoutRatio"] == pytest.approx(0.40, abs=1e-9)
        assert res["payoutRatio"] > 0.0


# --------------------------------------------------------------------------- #
# 4. beta = Cov(stock, mkt) / Var(mkt).
# --------------------------------------------------------------------------- #
class TestBeta:
    def _returns(self, n=80, slope=1.5, seed=7, noise=0.0):
        rng = np.random.default_rng(seed)
        idx = pd.date_range("2024-01-01", periods=n, freq="B")
        m = pd.Series(rng.normal(0.0, 0.01, size=n), index=idx)
        s = slope * m
        if noise:
            s = s + pd.Series(rng.normal(0.0, noise, size=n), index=idx)
        return s, m

    def test_beta_recovers_known_slope(self):
        """stock = 1.5 * market (>=60 obs) -> beta == 1.5."""
        s, m = self._returns(n=80, slope=1.5)
        res = compute_fundamentals(
            **base_kwargs(stock_returns=s, market_returns=m)
        )
        assert res["beta"] == pytest.approx(1.5, abs=1e-6)

    def test_beta_recovers_slope_with_small_noise(self):
        """A small idiosyncratic noise term keeps beta near the 1.5 slope."""
        s, m = self._returns(n=120, slope=1.5, noise=1e-5)
        res = compute_fundamentals(
            **base_kwargs(stock_returns=s, market_returns=m)
        )
        assert res["beta"] == pytest.approx(1.5, abs=0.05)

    def test_beta_nan_when_fewer_than_60_overlaps(self):
        """<60 overlapping observations -> beta is NaN (never fabricated)."""
        s, m = self._returns(n=59, slope=1.5)
        res = compute_fundamentals(
            **base_kwargs(stock_returns=s, market_returns=m)
        )
        assert math.isnan(res["beta"])

    def test_beta_nan_when_returns_missing(self):
        res = compute_fundamentals(
            **base_kwargs(stock_returns=None, market_returns=None)
        )
        assert math.isnan(res["beta"])


# --------------------------------------------------------------------------- #
# 5. NaN discipline (CONSTRAINT #4).
# --------------------------------------------------------------------------- #
class TestNaNDiscipline:
    _STATEMENT_METRICS = [
        "trailingEps",
        "trailingPE",
        "bookValue",
        "priceToBook",
        "dividendYield",
        "payoutRatio",
        "beta",
        "returnOnEquity",
        "debtToEquity",
        "grossMargins",
        "operatingMargins",
        "currentRatio",
        "heldPercentInstitutions",
    ]

    def test_empty_frames_etf_style_does_not_raise_and_nans_out(self):
        """All-empty statements (an ETF): no raise, price/marketCap set, rest NaN."""
        empty = pd.DataFrame()
        res = compute_fundamentals(
            ticker="SPY",
            price=150.0,
            shares_current=100.0,
            shares_diluted=100.0,
            income_stmt=empty,
            income_stmt_quarterly=empty,
            balance_sheet=empty,
            cashflow=empty,
            cashflow_quarterly=empty,
            dividends=None,
            inst_holders=None,
            stock_returns=None,
            market_returns=None,
        )
        assert isinstance(res, dict)
        # The two market-data-only fields still compute.
        assert res["currentPrice"] == pytest.approx(150.0, abs=1e-9)
        assert res["marketCap"] == pytest.approx(15000.0, abs=1e-6)
        # Every statement-derived metric is NaN — NEVER 0.0.
        for key in self._STATEMENT_METRICS:
            assert math.isnan(res[key]), f"{key} should be NaN for an empty-statement ETF"
            assert res[key] != 0.0

    def test_metrics_degrade_independently(self):
        """Missing equity row NaNs the equity-derived metrics but leaves margins."""
        # Balance sheet has debt / current items but NO equity row.
        bs_no_equity = stmt(
            {
                "Total Debt": [1500.0, 1400.0],
                "Current Assets": [800.0, 700.0],
                "Current Liabilities": [400.0, 350.0],
            },
            dates=_A_DATES,
        )
        res = compute_fundamentals(**base_kwargs(balance_sheet=bs_no_equity))
        # Equity-derived metrics NaN out.
        for key in ("bookValue", "priceToBook", "returnOnEquity", "debtToEquity"):
            assert math.isnan(res[key]), f"{key} should be NaN without an equity row"
        # Revenue/margin metrics still compute from the income statement.
        assert res["grossMargins"] == pytest.approx(0.40, abs=1e-9)
        assert res["operatingMargins"] == pytest.approx(0.12, abs=1e-9)
        assert res["currentRatio"] == pytest.approx(2.0, abs=1e-9)

    def test_dividend_yield_nan_when_no_dividends(self):
        res = compute_fundamentals(**base_kwargs(dividends=None))
        assert math.isnan(res["dividendYield"])
        assert res["dividendYield"] != 0.0


# --------------------------------------------------------------------------- #
# 6. Alias resolver unit tests (_row_latest, _ttm, alias fallback).
# --------------------------------------------------------------------------- #
class TestAliasResolver:
    def test_row_latest_case_and_whitespace_insensitive(self):
        df = stmt(
            {"stockholders   EQUITY": [1000.0, 900.0]}, dates=_A_DATES
        )
        assert _row_latest(df, ["Stockholders Equity"]) == pytest.approx(1000.0)

    def test_row_latest_returns_newest_non_nan(self):
        # Newest column is NaN; should return the older, real value.
        df = stmt(
            {"Stockholders Equity": [float("nan"), 500.0]}, dates=_A_DATES
        )
        assert _row_latest(df, ["Stockholders Equity"]) == pytest.approx(500.0)

    def test_row_latest_missing_label_is_nan(self):
        df = stmt({"Total Revenue": [1000.0, 900.0]}, dates=_A_DATES)
        assert math.isnan(_row_latest(df, ["Stockholders Equity"]))

    def test_row_latest_all_nan_row_is_nan(self):
        df = stmt(
            {"Stockholders Equity": [float("nan"), float("nan")]}, dates=_A_DATES
        )
        assert math.isnan(_row_latest(df, ["Stockholders Equity"]))

    def test_row_latest_none_df_is_nan(self):
        assert math.isnan(_row_latest(None, ["Stockholders Equity"]))

    def test_ttm_sums_trailing_four_quarters(self):
        q = stmt({"Net Income": [50.0, 50.0, 50.0, 50.0]})
        assert _ttm(q, ["Net Income"], None) == pytest.approx(200.0, abs=1e-9)

    def test_ttm_falls_back_to_latest_annual_when_quarterly_empty(self):
        annual = stmt({"Net Income": [180.0, 160.0]}, dates=_A_DATES)
        assert _ttm(pd.DataFrame(), ["Net Income"], annual) == pytest.approx(180.0)

    def test_ttm_falls_back_to_annual_when_insufficient_quarters(self):
        # Only 3 real quarters -> < 4 -> annual fallback.
        q = stmt(
            {"Net Income": [50.0, 50.0, 50.0]},
            dates=pd.to_datetime(["2025-12-31", "2025-09-30", "2025-06-30"]),
        )
        annual = stmt({"Net Income": [180.0, 160.0]}, dates=_A_DATES)
        assert _ttm(q, ["Net Income"], annual) == pytest.approx(180.0)

    def test_alias_fallback_uses_second_alias(self):
        """Equity present under the 2nd EQUITY alias still resolves."""
        second_alias = EQUITY[1]  # "Total Equity Gross Minority Interest"
        assert second_alias != EQUITY[0]
        df = stmt({second_alias: [1234.0, 1000.0]}, dates=_A_DATES)
        assert _row_latest(df, EQUITY) == pytest.approx(1234.0)

    def test_alias_fallback_end_to_end_book_value(self):
        """bookValue computes when equity lives under a non-primary alias."""
        bs = stmt(
            {EQUITY[1]: [1000.0, 900.0]}, dates=_A_DATES
        )
        res = compute_fundamentals(**base_kwargs(balance_sheet=bs))
        assert res["bookValue"] == pytest.approx(10.0, abs=1e-9)


# --------------------------------------------------------------------------- #
# 7. Output contract.
# --------------------------------------------------------------------------- #
class TestContract:
    def test_keys_are_subset_of_fundamental_keys(self):
        res = compute_fundamentals(**base_kwargs())
        assert set(res).issubset(set(FUNDAMENTAL_KEYS))

    def test_leaked_short_interest_key_not_emitted(self):
        res = compute_fundamentals(**base_kwargs())
        assert "netPercentInstitutionsSharesOut" not in res

    def test_straight_through_fields_present(self):
        res = compute_fundamentals(**base_kwargs(sector="Energy", company_name="Acme"))
        assert res["sector"] == "Energy"
        assert res["shortName"] == "Acme"
        assert res["currentPrice"] == pytest.approx(150.0, abs=1e-9)


# --------------------------------------------------------------------------- #
# 8. currentRatio -> FundamentalDataDTO.current_ratio wiring.
# --------------------------------------------------------------------------- #
class TestCurrentRatioDTOWiring:
    def test_current_ratio_carried_into_dto(self):
        from dto_models import FundamentalDataDTO

        dto = FundamentalDataDTO.from_raw_dict("XYZ", {"currentRatio": 1.8})
        assert dto.current_ratio == pytest.approx(1.8, abs=1e-9)

    def test_missing_current_ratio_is_nan_not_zero(self):
        from dto_models import FundamentalDataDTO

        dto = FundamentalDataDTO.from_raw_dict("XYZ", {})
        assert math.isnan(dto.current_ratio)

    def test_directly_constructed_dto_has_current_ratio(self):
        """A DTO built via __init__ (not from_raw_dict) must still expose the attr."""
        from dto_models import FundamentalDataDTO

        dto = FundamentalDataDTO(
            ticker="XYZ",
            company_name="X",
            sector="Tech",
            pe_ratio=10.0,
            pb_ratio=1.0,
            book_value=5.0,
            eps_trailing=1.0,
            dividend_yield=0.0,
            dividend_growth_rate=0.02,
            payout_ratio=0.0,
            market_cap=1.0,
            price=10.0,
            beta=1.0,
        )
        assert math.isnan(dto.current_ratio)
