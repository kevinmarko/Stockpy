import os
import sys
import io
import datetime
import pytest
import pandas as pd

from universe_engine import (
    get_sp500_constituents,
    get_delisted_tickers,
    get_universe_with_survivorship_warning,
    CACHE_PATH,
    DELISTED_PATH,
    fetch_and_cache_universe,
)

def test_wikipedia_scrape_and_cache():
    """Verify that we can scrape Wikipedia, cache is created, and returns >= 500 tickers."""
    # Force fresh scrape by removing cache
    if os.path.exists(CACHE_PATH):
        try:
            os.remove(CACHE_PATH)
        except OSError:
            pass

    tickers = get_sp500_constituents(datetime.date.today())
    assert len(tickers) >= 500
    assert "AAPL" in tickers
    assert "MSFT" in tickers
    assert os.path.exists(CACHE_PATH), "Cache parquet file should be created"

def test_cache_is_reused():
    """Verify that loading constituents reuse cache if it is fresh."""
    assert os.path.exists(CACHE_PATH)
    initial_mtime = os.path.getmtime(CACHE_PATH)

    # Call get_sp500_constituents again, it should use cache and NOT write a new file
    _ = get_sp500_constituents(datetime.date.today())
    new_mtime = os.path.getmtime(CACHE_PATH)
    assert initial_mtime == new_mtime

def test_bias_report_fields():
    """Verify that the bias report fields are present and correct."""
    constituents, bias_report = get_universe_with_survivorship_warning(datetime.date(2018, 1, 1))
    
    assert isinstance(constituents, list)
    assert len(constituents) > 400
    
    # Check bias report dict keys
    for key in ["n_current", "n_at_date", "n_delisted_in_period", "estimated_bias_pct"]:
        assert key in bias_report
        
    assert bias_report["n_current"] >= 500
    assert bias_report["n_at_date"] > 400
    assert bias_report["estimated_bias_pct"] >= 0.5

def test_point_in_time_no_lookahead():
    """
    Test lookahead/leakage check: Verify that querying for an older date does 
    not include tickers added after that date.
    """
    # Let's find a change in the changes table
    df = pd.read_parquet(CACHE_PATH)
    changes = df[df["type"] == "change"].copy()
    if changes.empty:
        pytest.skip("No historical changes found in Wikipedia cache.")
        
    changes["date_parsed"] = pd.to_datetime(changes["date"]).dt.date
    # Pick a change where a ticker was added
    additions = changes[changes["added_ticker"].notna()].sort_values("date_parsed", ascending=False)
    if additions.empty:
        pytest.skip("No additions found in historical changes.")
        
    target_row = additions.iloc[0]
    change_date = target_row["date_parsed"]
    added_ticker = target_row["added_ticker"]
    
    # Get universe 1 day before the change
    day_before = change_date - datetime.timedelta(days=1)
    universe_before = get_sp500_constituents(day_before)
    
    # Get universe on the change date
    universe_after = get_sp500_constituents(change_date)
    
    # The added ticker should NOT be in universe_before
    assert added_ticker not in universe_before, f"Lookahead bias: {added_ticker} should not be in the universe on {day_before} before it was added on {change_date}"
    # The added ticker SHOULD be in universe_after
    assert added_ticker in universe_after

def test_delisted_tickers_file():
    """Verify that get_delisted_tickers returns the seeded tickers."""
    delisted = get_delisted_tickers()
    assert isinstance(delisted, pd.DataFrame)
    assert len(delisted) >= 30
    assert "LEH" in delisted["ticker"].values
    assert "BSC" in delisted["ticker"].values

def test_backtest_stdout_warning(capsys):
    """Verify that simulation engine prints the survivorship bias warning."""
    from simulation_engine import print_survivorship_warning_for_backtest
    dates = pd.date_range(start='2020-01-01', periods=10, freq='B')
    
    print_survivorship_warning_for_backtest(dates)
    captured = capsys.readouterr()
    
    assert "WARNING — SURVIVORSHIP BIAS" in captured.out
    assert "Free-data backtests systematically overstate returns" in captured.out
    assert "Bias Report Details" in captured.out
