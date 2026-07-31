"""
tests/test_fmp_fundamentals.py
================================
Fully-offline unit tests for ``data/fmp_fundamentals.py`` — the pure,
I/O-free FMP-to-yfinance-key mapping engine.

No network, no ``requests``, no ``data.fmp_client``. Every payload is a plain
dict/list built directly in the test body, matching this module's own
zero-mocking contract. The math here is financial and feeds straight into
Gordon Fair Value, the multifactor value z-score, and every downstream sizing
decision, so every expected value is asserted exactly — not just "is finite".

Classes
-------
* ``TestScaleRules``       — debtToEquity x100; dividendYield fraction guard.
* ``TestSignGates``        — trailingPE / priceToBook NaN gates.
* ``TestResponseShapes``   — list-wrapped vs. bare-dict FMP payload parsing.
* ``TestNaNDiscipline``    — CONSTRAINT #4: every-input-None -> every key NaN.
* ``TestContract``         — emitted key set; negative assertions.
* ``TestDividendsSeries``  — the internal ``_dividends_series`` plumbing key.
* ``TestBetaParity``       — compute_beta numeric parity vs. yahoo_fundamentals.
"""

import logging
import math

import numpy as np
import pandas as pd
import pytest

from data.fmp_fundamentals import (
    FMP_FUNDAMENTAL_KEYS,
    compute_beta,
    map_fundamentals,
)
from data.yahoo_fundamentals import FUNDAMENTAL_KEYS
from data.yahoo_fundamentals import compute_fundamentals as yahoo_compute_fundamentals


# --------------------------------------------------------------------------- #
# Fixture helpers — a fully-populated, internally-consistent set of FMP-shaped
# payloads (list-of-one-dict, matching the live-verified response shape).
# --------------------------------------------------------------------------- #
def _quote(price=150.0):
    return [{"symbol": "TEST", "price": price}]


def _profile(price=150.0, market_cap=1_500_000.0, sector="Technology", name="Test Co"):
    return [{
        "symbol": "TEST", "companyName": name, "sector": sector,
        "marketCap": market_cap, "price": price,
    }]


def _key_metrics_ttm(roe=0.20):
    return [{"symbol": "TEST", "returnOnEquityTTM": roe}]


def _ratios_ttm(
    pe=15.0, book_value=10.0, ptb=15.0, div_yield=0.0257, payout=0.30,
    gross_margin=0.45, op_margin=0.25, dte=1.5, current_ratio=1.8,
):
    return [{
        "symbol": "TEST",
        "priceToEarningsRatioTTM": pe,
        "bookValuePerShareTTM": book_value,
        "priceToBookRatioTTM": ptb,
        "dividendYieldTTM": div_yield,
        "dividendPayoutRatioTTM": payout,
        "grossProfitMarginTTM": gross_margin,
        "operatingProfitMarginTTM": op_margin,
        "debtToEquityRatioTTM": dte,
        "currentRatioTTM": current_ratio,
    }]


def _income_statement_ttm(eps_diluted=10.0):
    return [{"symbol": "TEST", "epsDiluted": eps_diluted}]


def _shares_float(outstanding=100_000.0):
    return [{"symbol": "TEST", "outstandingShares": outstanding}]


def full_kwargs(**overrides):
    """A fully-populated, internally-consistent set of map_fundamentals()
    kwargs. Individual tests override just the pieces they exercise."""
    base = dict(
        quote=_quote(),
        profile=_profile(),
        key_metrics_ttm=_key_metrics_ttm(),
        ratios_ttm=_ratios_ttm(),
        income_statement_ttm=_income_statement_ttm(),
        shares_float=_shares_float(),
        dividends=None,
        beta=1.1,
    )
    base.update(overrides)
    return base


# Expected emitted key set, derived programmatically (never hand-copied).
EXPECTED_KEY_SET = (set(FUNDAMENTAL_KEYS) - {"heldPercentInstitutions"}) | {
    "sharesOutstanding", "_source",
}


# --------------------------------------------------------------------------- #
# 1. Scale-critical rules.
# --------------------------------------------------------------------------- #
class TestScaleRules:
    def test_debt_to_equity_is_multiplied_by_100(self):
        """Contract: 1.5 -> 150.0, NOT 1.5. Two downstream consumers /100."""
        out = map_fundamentals("TEST", **full_kwargs(ratios_ttm=_ratios_ttm(dte=1.5)))
        assert out["debtToEquity"] == pytest.approx(150.0)

    def test_debt_to_equity_nan_when_missing(self):
        out = map_fundamentals(
            "TEST", **full_kwargs(ratios_ttm=[{"symbol": "TEST"}])
        )
        assert math.isnan(out["debtToEquity"])

    def test_dividend_yield_fraction_passes_through_unchanged(self):
        out = map_fundamentals(
            "TEST", **full_kwargs(ratios_ttm=_ratios_ttm(div_yield=0.0257))
        )
        assert out["dividendYield"] == pytest.approx(0.0257)

    def test_dividend_yield_over_one_emits_nan_and_logs_error(self, caplog):
        """A value > 1.0 looks like a PERCENT, not a fraction. Must emit NaN
        and log ERROR -- NEVER silently divide by 100 (a wrong guess is a
        100x error into Gordon Fair Value)."""
        with caplog.at_level(logging.ERROR, logger="data.fmp_fundamentals"):
            out = map_fundamentals(
                "TEST", **full_kwargs(ratios_ttm=_ratios_ttm(div_yield=2.57))
            )
        assert math.isnan(out["dividendYield"])
        assert any(
            r.levelno == logging.ERROR and "dividendYieldTTM" in r.message
            for r in caplog.records
        )

    def test_dividend_yield_exactly_one_is_not_flagged(self):
        """Boundary: exactly 1.0 is NOT > 1.0, so it passes through (an
        edge case, not a guessed rule -- the guard is a strict '>')."""
        out = map_fundamentals(
            "TEST", **full_kwargs(ratios_ttm=_ratios_ttm(div_yield=1.0))
        )
        assert out["dividendYield"] == pytest.approx(1.0)

    def test_payout_ratio_over_one_is_not_guarded(self):
        """payoutRatio > 1.0 is a legitimate unsustainable payer -- no guard."""
        out = map_fundamentals(
            "TEST", **full_kwargs(ratios_ttm=_ratios_ttm(payout=1.35))
        )
        assert out["payoutRatio"] == pytest.approx(1.35)

    def test_never_reads_dividend_yield_percentage_key(self):
        """Must not read a 'dividendYieldPercentageTTM'-style key even if
        present alongside the real one."""
        ratios = _ratios_ttm(div_yield=0.0257)
        ratios[0]["dividendYieldPercentageTTM"] = 2.57
        out = map_fundamentals("TEST", **full_kwargs(ratios_ttm=ratios))
        assert out["dividendYield"] == pytest.approx(0.0257)


# --------------------------------------------------------------------------- #
# 2. Sign / positivity gates.
# --------------------------------------------------------------------------- #
class TestSignGates:
    def test_trailing_pe_nan_when_eps_zero(self):
        out = map_fundamentals(
            "TEST",
            **full_kwargs(
                income_statement_ttm=_income_statement_ttm(eps_diluted=0.0),
                ratios_ttm=_ratios_ttm(pe=15.0),
            ),
        )
        assert math.isnan(out["trailingPE"])

    def test_trailing_pe_nan_when_eps_negative_even_with_negative_pe_supplied(self):
        """FMP returns a NEGATIVE PE for loss-makers (unlike Yahoo, which
        emits NaN itself). The consumer must still force NaN via the EPS
        sign, regardless of what number the ratios endpoint sends."""
        out = map_fundamentals(
            "TEST",
            **full_kwargs(
                income_statement_ttm=_income_statement_ttm(eps_diluted=-2.5),
                ratios_ttm=_ratios_ttm(pe=-12.34),
            ),
        )
        assert math.isnan(out["trailingPE"])

    def test_trailing_pe_passes_through_when_eps_positive(self):
        out = map_fundamentals(
            "TEST",
            **full_kwargs(
                income_statement_ttm=_income_statement_ttm(eps_diluted=10.0),
                ratios_ttm=_ratios_ttm(pe=15.0),
            ),
        )
        assert out["trailingPE"] == pytest.approx(15.0)
        assert out["trailingEps"] == pytest.approx(10.0)

    def test_price_to_book_nan_when_book_value_zero(self):
        out = map_fundamentals(
            "TEST",
            **full_kwargs(ratios_ttm=_ratios_ttm(book_value=0.0, ptb=15.0)),
        )
        assert math.isnan(out["priceToBook"])

    def test_price_to_book_nan_when_book_value_negative(self):
        out = map_fundamentals(
            "TEST",
            **full_kwargs(ratios_ttm=_ratios_ttm(book_value=-5.0, ptb=15.0)),
        )
        assert math.isnan(out["priceToBook"])
        # bookValue itself is still emitted as-is (it's a real, if negative,
        # number) -- only the RATIO built from it is gated.
        assert out["bookValue"] == pytest.approx(-5.0)

    def test_price_to_book_passes_through_when_book_value_positive(self):
        out = map_fundamentals(
            "TEST",
            **full_kwargs(ratios_ttm=_ratios_ttm(book_value=10.0, ptb=15.0)),
        )
        assert out["priceToBook"] == pytest.approx(15.0)
        assert out["bookValue"] == pytest.approx(10.0)


# --------------------------------------------------------------------------- #
# 3. Response-shape normalisation: list-wrapped vs. bare-dict.
# --------------------------------------------------------------------------- #
class TestResponseShapes:
    def test_list_wrapped_and_bare_dict_parse_identically(self):
        list_kwargs = full_kwargs()
        bare_kwargs = full_kwargs(
            quote=_quote()[0],
            profile=_profile()[0],
            key_metrics_ttm=_key_metrics_ttm()[0],
            ratios_ttm=_ratios_ttm()[0],
            income_statement_ttm=_income_statement_ttm()[0],
            shares_float=_shares_float()[0],
        )
        out_list = map_fundamentals("TEST", **list_kwargs)
        out_bare = map_fundamentals("TEST", **bare_kwargs)
        assert out_list == out_bare

    def test_empty_list_treated_as_missing(self):
        out = map_fundamentals("TEST", **full_kwargs(profile=[]))
        assert out["sector"] == "N/A"
        assert out["shortName"] == ""
        assert math.isnan(out["marketCap"])

    def test_empty_dict_treated_as_missing(self):
        out = map_fundamentals("TEST", **full_kwargs(ratios_ttm={}))
        assert math.isnan(out["trailingPE"])
        assert math.isnan(out["debtToEquity"])

    def test_none_treated_as_missing(self):
        out = map_fundamentals("TEST", **full_kwargs(key_metrics_ttm=None))
        assert math.isnan(out["returnOnEquity"])


# --------------------------------------------------------------------------- #
# 4. NaN-not-zero discipline (CONSTRAINT #4).
# --------------------------------------------------------------------------- #
class TestNaNDiscipline:
    def test_every_input_none_never_raises_and_degrades_to_nan(self):
        out = map_fundamentals(
            "TEST",
            quote=None,
            profile=None,
            key_metrics_ttm=None,
            ratios_ttm=None,
            income_statement_ttm=None,
            shares_float=None,
            dividends=None,
            beta=None,
        )
        numeric_keys = [k for k in FMP_FUNDAMENTAL_KEYS if k not in ("shortName", "sector")]
        for key in numeric_keys:
            assert math.isnan(out[key]), f"{key} should be NaN, got {out[key]!r}"
        # Strings degrade to their own honest defaults, not NaN -- matches
        # the yahoo_fundamentals convention (sector="N/A", shortName="").
        assert out["shortName"] == ""
        assert out["sector"] == "N/A"
        # _source is always emitted regardless of input completeness.
        assert out["_source"] == "fmp"
        # dividends was None -> no _dividends_series key at all.
        assert "_dividends_series" not in out

    def test_never_raises_on_malformed_payload_shapes(self):
        """Wrong-typed payloads (a string, an int, a list of non-dicts) must
        degrade gracefully, never raise."""
        out = map_fundamentals(
            "TEST",
            quote="not a dict",
            profile=42,
            key_metrics_ttm=[1, 2, 3],
            ratios_ttm=[None],
            income_statement_ttm=[],
            shares_float={"unexpected": "shape"},
            dividends="also not a list",
            beta="not a float either",
        )
        assert math.isnan(out["currentPrice"])
        assert math.isnan(out["beta"])
        assert out["_source"] == "fmp"

    def test_a_single_missing_field_does_not_blank_siblings(self):
        """One missing field degrades independently -- it must not nuke
        every other metric in the same payload."""
        ratios = _ratios_ttm()
        del ratios[0]["bookValuePerShareTTM"]
        out = map_fundamentals("TEST", **full_kwargs(ratios_ttm=ratios))
        assert math.isnan(out["bookValue"])
        # Siblings sourced from the SAME payload are unaffected.
        assert out["dividendYield"] == pytest.approx(0.0257)
        assert out["debtToEquity"] == pytest.approx(150.0)
        assert out["currentRatio"] == pytest.approx(1.8)


# --------------------------------------------------------------------------- #
# 5. Emitted key-set contract + negative assertions.
# --------------------------------------------------------------------------- #
class TestContract:
    def test_emitted_key_set_matches_exactly(self):
        out = map_fundamentals("TEST", **full_kwargs())
        assert set(out.keys()) == EXPECTED_KEY_SET

    def test_emitted_key_set_with_dividends_adds_the_internal_series_key(self):
        out = map_fundamentals(
            "TEST", **full_kwargs(dividends=[{"date": "2026-01-15", "dividend": 0.25}])
        )
        assert set(out.keys()) == EXPECTED_KEY_SET | {"_dividends_series"}

    def test_negative_assertions_forbidden_keys_absent(self):
        """These keys, if emitted, silently corrupt downstream price
        resolution (previousClose/regularMarketPrice) or are simply unused
        (forwardPE) / not on this plan (heldPercentInstitutions) / a
        duplicate of shortName (longName)."""
        out = map_fundamentals("TEST", **full_kwargs())
        assert "previousClose" not in out
        assert "regularMarketPrice" not in out
        assert "forwardPE" not in out
        assert "heldPercentInstitutions" not in out
        assert "longName" not in out

    def test_fmp_fundamental_keys_constant_matches_expected_set(self):
        assert set(FMP_FUNDAMENTAL_KEYS) | {"_source"} == EXPECTED_KEY_SET

    def test_source_is_always_the_literal_fmp(self):
        out = map_fundamentals("TEST", **full_kwargs())
        assert out["_source"] == "fmp"


# --------------------------------------------------------------------------- #
# 6. The internal "_dividends_series" plumbing key.
# --------------------------------------------------------------------------- #
class TestDividendsSeries:
    def test_dividends_series_built_from_date_dividend_rows(self):
        out = map_fundamentals(
            "TEST",
            **full_kwargs(
                dividends=[
                    {"date": "2026-02-09", "dividend": 0.26},
                    {"date": "2025-11-10", "dividend": 0.26},
                    {"date": "2025-08-11", "dividend": 0.25},
                ]
            ),
        )
        series = out["_dividends_series"]
        assert isinstance(series, pd.Series)
        assert len(series) == 3
        # Ascending, tz-naive dates.
        assert list(series.index) == sorted(series.index)
        assert series.loc[pd.Timestamp("2026-02-09")] == pytest.approx(0.26)

    def test_dividends_series_skips_malformed_rows(self):
        out = map_fundamentals(
            "TEST",
            **full_kwargs(
                dividends=[
                    {"date": "2026-02-09", "dividend": 0.26},
                    {"date": None, "dividend": 0.10},        # bad date
                    {"date": "2025-11-10"},                  # missing dividend
                    "not a dict",                             # wrong type
                ]
            ),
        )
        series = out["_dividends_series"]
        assert len(series) == 1

    def test_dividends_empty_list_yields_empty_series(self):
        out = map_fundamentals("TEST", **full_kwargs(dividends=[]))
        assert isinstance(out["_dividends_series"], pd.Series)
        assert out["_dividends_series"].empty

    def test_dividends_none_omits_the_key_entirely(self):
        out = map_fundamentals("TEST", **full_kwargs(dividends=None))
        assert "_dividends_series" not in out


# --------------------------------------------------------------------------- #
# 7. compute_beta numeric parity vs. data/yahoo_fundamentals.py.
# --------------------------------------------------------------------------- #
class TestBetaParity:
    """Deliberate duplication (not a shared refactor) -- this test is the
    proof the duplication is exact."""

    def _synthetic_returns(self, n=80, slope=1.5, seed=7, noise=0.0):
        rng = np.random.default_rng(seed)
        idx = pd.date_range("2024-01-01", periods=n, freq="B")
        m = pd.Series(rng.normal(0.0, 0.01, size=n), index=idx)
        s = slope * m
        if noise:
            s = s + pd.Series(rng.normal(0.0, noise, size=n), index=idx)
        return s, m

    def _yahoo_beta(self, stock_returns, market_returns):
        """Drive yahoo_fundamentals.compute_fundamentals with everything
        else NaN'd out, isolating its beta computation for comparison."""
        res = yahoo_compute_fundamentals(
            "TEST",
            price=float("nan"),
            shares_current=float("nan"),
            shares_diluted=float("nan"),
            income_stmt=pd.DataFrame(),
            income_stmt_quarterly=pd.DataFrame(),
            balance_sheet=pd.DataFrame(),
            cashflow=pd.DataFrame(),
            cashflow_quarterly=pd.DataFrame(),
            dividends=None,
            inst_holders=None,
            stock_returns=stock_returns,
            market_returns=market_returns,
        )
        return res["beta"]

    def test_parity_on_a_clean_slope_fixture(self):
        s, m = self._synthetic_returns(n=80, slope=1.5, seed=7, noise=0.0)
        fmp_beta = compute_beta(s, m)
        yahoo_beta = self._yahoo_beta(s, m)
        assert fmp_beta == pytest.approx(yahoo_beta, abs=1e-9)

    def test_parity_with_noise(self):
        s, m = self._synthetic_returns(n=150, slope=0.8, seed=42, noise=1e-4)
        fmp_beta = compute_beta(s, m)
        yahoo_beta = self._yahoo_beta(s, m)
        assert fmp_beta == pytest.approx(yahoo_beta, abs=1e-9)

    def test_parity_below_min_obs_both_nan(self):
        s, m = self._synthetic_returns(n=59, slope=1.5, seed=1)
        fmp_beta = compute_beta(s, m)
        yahoo_beta = self._yahoo_beta(s, m)
        assert math.isnan(fmp_beta)
        assert math.isnan(yahoo_beta)

    def test_parity_at_exactly_min_obs_both_finite(self):
        s, m = self._synthetic_returns(n=60, slope=1.5, seed=1)
        fmp_beta = compute_beta(s, m)
        yahoo_beta = self._yahoo_beta(s, m)
        assert not math.isnan(fmp_beta)
        assert fmp_beta == pytest.approx(yahoo_beta, abs=1e-9)

    def test_nan_when_series_missing(self):
        assert math.isnan(compute_beta(None, None))
        assert math.isnan(compute_beta(pd.Series(dtype="float64"), pd.Series(dtype="float64")))

    def test_min_obs_is_configurable(self):
        s, m = self._synthetic_returns(n=30, slope=1.5, seed=3)
        assert math.isnan(compute_beta(s, m, min_obs=60))
        assert not math.isnan(compute_beta(s, m, min_obs=20))

    def test_beta_used_end_to_end_via_map_fundamentals(self):
        """map_fundamentals's beta kwarg is passed straight through -- not
        recomputed, not read from profile.beta."""
        out = map_fundamentals("TEST", **full_kwargs(beta=1.234))
        assert out["beta"] == pytest.approx(1.234)
