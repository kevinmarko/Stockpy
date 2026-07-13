import math
from unittest import mock

import pytest

from data import edgar_fundamentals

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
                "LongTermDebt": {"units": {"USD": [{"val": 50000.0, "filed": "2020-01-15"}]}}
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

def test_fetch_companyfacts(monkeypatch):
    mock_get = mock.Mock(return_value=b'{"facts": {"us-gaap": {}}}')
    monkeypatch.setattr(edgar_fundamentals, "_http_get", mock_get)
    
    res = edgar_fundamentals.fetch_companyfacts("0000320193")
    assert "facts" in res
