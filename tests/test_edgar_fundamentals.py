import math
from unittest import mock

import pytest

from data import edgar_fundamentals
from data.yahoo_fundamentals import compute_fundamentals
from tests.test_yahoo_fundamentals import base_kwargs as _yahoo_base_kwargs

@pytest.fixture
def mock_tickers(monkeypatch):
    data = b'{"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp."}}'
    mock_get = mock.Mock(return_value=data)
    monkeypatch.setattr(edgar_fundamentals, "_http_get", mock_get)
    edgar_fundamentals._cik_cache.clear()

def test_get_cik(mock_tickers):
    assert edgar_fundamentals.get_cik("AAPL") == "0000320193"
    assert edgar_fundamentals.get_cik("MSFT") == "0000789019"
    assert edgar_fundamentals.get_cik("UNKNOWN") is None

def test_extract_latest_fact():
    us_gaap = {
        "EarningsPerShareBasic": {
            "units": {
                "USD/shares": [
                    {"val": 1.0, "filed": "2019-10-30"},
                    {"val": 1.5, "filed": "2020-01-30"},
                    {"val": 2.0, "filed": "2020-04-30"}
                ]
            }
        }
    }
    
    assert edgar_fundamentals.extract_latest_fact(us_gaap, "EarningsPerShareBasic", "2019-01-01") is None
    assert edgar_fundamentals.extract_latest_fact(us_gaap, "EarningsPerShareBasic", "2019-11-01") == 1.0
    assert edgar_fundamentals.extract_latest_fact(us_gaap, "EarningsPerShareBasic", "2020-02-01") == 1.5
    assert edgar_fundamentals.extract_latest_fact(us_gaap, "EarningsPerShareBasic", "2020-05-01") == 2.0

def test_compute_pit_ratios():
    facts = {
        "facts": {
            "us-gaap": {
                "EarningsPerShareBasic": {"units": {"USD/shares": [{"val": 5.0, "filed": "2020-01-15"}]}},
                "StockholdersEquity": {"units": {"USD": [{"val": 100000.0, "filed": "2020-01-15"}]}},
                "NetIncomeLoss": {"units": {"USD": [{"val": 15000.0, "filed": "2020-01-15"}]}},
                "Revenues": {"units": {"USD": [{"val": 50000.0, "filed": "2020-01-15"}]}},
                "OperatingIncomeLoss": {"units": {"USD": [{"val": 10000.0, "filed": "2020-01-15"}]}},
                "PaymentsOfDividends": {"units": {"USD": [{"val": 2000.0, "filed": "2020-01-15"}]}},
                "LongTermDebt": {"units": {"USD": [{"val": 50000.0, "filed": "2020-01-15"}]}},
                "AssetsCurrent": {"units": {"USD": [{"val": 30000.0, "filed": "2020-01-15"}]}},
                "LiabilitiesCurrent": {"units": {"USD": [{"val": 20000.0, "filed": "2020-01-15"}]}},
            }
        }
    }
    
    # price = 100.0, shares = 1000.0 -> market_cap = 100,000.0
    out = edgar_fundamentals.compute_pit_ratios(facts, "2020-01-15", 100.0, 1000.0)
    
    assert out["eps"] == 5.0
    assert out["pe_ratio"] == 100.0 / 5.0
    
    # book_value = 100000.0 / 1000 = 100.0
    # pb_ratio = 100.0 / 100.0 = 1.0
    assert out["pb_ratio"] == 1.0
    
    # roe = 15000.0 / 100000.0 = 0.15
    assert out["roe"] == 0.15
    
    # market_cap = 100000.0
    assert out["market_cap"] == 100000.0
    
    # dividend_yield = 2000 / 100000 = 0.02
    assert out["dividend_yield"] == 0.02
    
    # operating_margin = 10000 / 50000 = 0.2
    assert out["operating_margin"] == 0.2
    
    # debt_to_equity = (50000 / 100000) * 100 = 50.0
    assert out["debt_to_equity"] == 50.0

    # current_ratio = 30000 / 20000 = 1.5
    assert out["current_ratio"] == 1.5


def test_compute_pit_ratios_missing_debt_fact_is_nan_not_zero():
    """A company whose LongTermDebt XBRL fact simply wasn't found must report
    debt_to_equity as NaN (undefined), never a fabricated 0.0 that would read
    as "verified zero debt" (CONSTRAINT #4)."""
    facts = {
        "facts": {
            "us-gaap": {
                "StockholdersEquity": {"units": {"USD": [{"val": 100000.0, "filed": "2020-01-15"}]}},
                # No "LongTermDebt" key at all.
            }
        }
    }

    out = edgar_fundamentals.compute_pit_ratios(facts, "2020-01-15", 100.0, 1000.0)

    assert math.isnan(out["debt_to_equity"])


def test_compute_pit_ratios_missing_current_liabilities_is_nan_not_fabricated():
    """No LiabilitiesCurrent fact -> current_ratio stays NaN, never a
    fabricated 0.0 or a divide-by-zero (CONSTRAINT #4)."""
    facts = {
        "facts": {
            "us-gaap": {
                "AssetsCurrent": {"units": {"USD": [{"val": 30000.0, "filed": "2020-01-15"}]}},
            }
        }
    }
    out = edgar_fundamentals.compute_pit_ratios(facts, "2020-01-15", 100.0, 1000.0)
    assert math.isnan(out["current_ratio"])


def test_extract_shares_prefers_dei_falls_back_to_us_gaap():
    facts_dei = {
        "facts": {
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {"shares": [{"val": 5_000_000.0, "filed": "2020-01-15"}]}
                }
            },
            "us-gaap": {
                "CommonStockSharesOutstanding": {
                    "units": {"shares": [{"val": 9_999.0, "filed": "2020-01-15"}]}
                }
            },
        }
    }
    # dei wins when both are present.
    assert edgar_fundamentals.extract_shares(facts_dei, "2020-01-15") == 5_000_000.0

    facts_us_gaap_only = {
        "facts": {
            "us-gaap": {
                "CommonStockSharesOutstanding": {
                    "units": {"shares": [{"val": 9_999.0, "filed": "2020-01-15"}]}
                }
            }
        }
    }
    assert edgar_fundamentals.extract_shares(facts_us_gaap_only, "2020-01-15") == 9_999.0


def test_extract_shares_neither_present_returns_zero_not_fabricated():
    assert edgar_fundamentals.extract_shares({"facts": {}}, "2020-01-15") == 0.0


class TestScaleRuleParityWithYahooFundamentals:
    """data/edgar_fundamentals.py independently reimplements (rather than
    imports) data/yahoo_fundamentals.py's two scale-critical conventions --
    dividendYield as a FRACTION, debtToEquity x100 -- because the two
    modules' input shapes are fundamentally different (raw EDGAR XBRL facts
    vs. yfinance-shaped statement DataFrames), so literal code sharing isn't
    practical. This test pins that the two independent implementations stay
    numerically consistent: if either file's formula ever drifts from the
    other, this breaks loudly instead of silently diverging.

    Uses tests/test_yahoo_fundamentals.py's own base_kwargs() fixture
    (equity=1000, total_debt=1500, price=150, shares=100) so both sides are
    fed genuinely equivalent underlying financials.
    """

    def test_debt_to_equity_matches(self):
        yahoo_out = compute_fundamentals(**_yahoo_base_kwargs())

        facts = {
            "facts": {
                "us-gaap": {
                    "StockholdersEquity": {"units": {"USD": [{"val": 1000.0, "filed": "2025-12-31"}]}},
                    "LongTermDebt": {"units": {"USD": [{"val": 1500.0, "filed": "2025-12-31"}]}},
                }
            }
        }
        edgar_out = edgar_fundamentals.compute_pit_ratios(facts, "2025-12-31", price=150.0, shares=100.0)

        assert yahoo_out["debtToEquity"] == pytest.approx(150.0, abs=1e-6)
        assert edgar_out["debt_to_equity"] == pytest.approx(150.0, abs=1e-6)
        assert edgar_out["debt_to_equity"] == pytest.approx(yahoo_out["debtToEquity"], abs=1e-6)

    def test_dividend_yield_matches(self):
        """base_kwargs() pays $4.00/share/yr at price $150 -> yahoo fraction
        4/150. EDGAR reports the AGGREGATE dollar amount (100 shares *
        $4.00 = $400 total) against market_cap (150*100=15000) -- the same
        ratio via a different but mathematically equivalent path
        (total_dividends/market_cap == per_share_dividends/price)."""
        yahoo_out = compute_fundamentals(**_yahoo_base_kwargs())

        facts = {
            "facts": {
                "us-gaap": {
                    "PaymentsOfDividends": {"units": {"USD": [{"val": 400.0, "filed": "2025-12-31"}]}},
                }
            }
        }
        edgar_out = edgar_fundamentals.compute_pit_ratios(facts, "2025-12-31", price=150.0, shares=100.0)

        assert yahoo_out["dividendYield"] == pytest.approx(4.0 / 150.0, abs=1e-6)
        assert edgar_out["dividend_yield"] == pytest.approx(4.0 / 150.0, abs=1e-6)
        assert edgar_out["dividend_yield"] == pytest.approx(yahoo_out["dividendYield"], abs=1e-6)
        # Guard against the wrong (×100) scaling explicitly, matching
        # TestScaleRules.test_dividend_yield_is_a_fraction's own guard.
        assert edgar_out["dividend_yield"] < 1.0


def test_fetch_companyfacts(monkeypatch):
    mock_get = mock.Mock(return_value=b'{"facts": {"us-gaap": {}}}')
    monkeypatch.setattr(edgar_fundamentals, "_http_get", mock_get)
    
    res = edgar_fundamentals.fetch_companyfacts("0000320193")
    assert "facts" in res
