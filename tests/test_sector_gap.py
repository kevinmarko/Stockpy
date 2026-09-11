import pytest
from unittest.mock import patch, MagicMock

from pilots.sector_gap import find_underrepresented_sectors
from execution.broker_base import PositionSnapshot

def test_find_underrepresented_sectors_missing_completely():
    with patch("pilots.sector_gap.PaperAccountStore") as mock_paper, \
         patch("pilots.sector_gap.HistoricalStore") as mock_hist, \
         patch("pilots.sector_gap.resolve_universe") as mock_resolve:
         
        # Mock universe
        mock_resolve.return_value = ["AAPL", "MSFT", "XOM"]
        
        # Mock holdings (AAPL and MSFT, meaning Tech is represented but Energy is missing)
        pos1 = PositionSnapshot(symbol="AAPL", qty=10, avg_entry_price=100, market_value=1000, unrealized_pl=0)
        pos2 = PositionSnapshot(symbol="MSFT", qty=10, avg_entry_price=100, market_value=1000, unrealized_pl=0)
        mock_paper.return_value.get_open_positions.return_value = [pos1, pos2]
        
        # Mock sectors
        def mock_get_fundamentals(symbol, max_age_days):
            sectors = {
                "AAPL": {"sector": "Technology"},
                "MSFT": {"sector": "Technology"},
                "XOM": {"sector": "Energy"}
            }
            return sectors.get(symbol, {})
            
        mock_hist.return_value.get_fundamentals_raw.side_effect = mock_get_fundamentals
        
        gaps = find_underrepresented_sectors()
        assert "Energy" in gaps
        assert "Technology" not in gaps

def test_find_underrepresented_sectors_proportionally():
    with patch("pilots.sector_gap.PaperAccountStore") as mock_paper, \
         patch("pilots.sector_gap.HistoricalStore") as mock_hist, \
         patch("pilots.sector_gap.resolve_universe") as mock_resolve:
         
        # Mock universe (2 Tech, 2 Energy)
        mock_resolve.return_value = ["AAPL", "MSFT", "XOM", "CVX"]
        
        # Mock holdings (2 Tech, 1 Energy -> Energy is underrepresented proportionally)
        pos1 = PositionSnapshot(symbol="AAPL", qty=10, avg_entry_price=100, market_value=1000, unrealized_pl=0)
        pos2 = PositionSnapshot(symbol="MSFT", qty=10, avg_entry_price=100, market_value=1000, unrealized_pl=0)
        pos3 = PositionSnapshot(symbol="XOM", qty=10, avg_entry_price=100, market_value=1000, unrealized_pl=0)
        mock_paper.return_value.get_open_positions.return_value = [pos1, pos2, pos3]
        
        def mock_get_fundamentals(symbol, max_age_days):
            sectors = {
                "AAPL": {"sector": "Technology"},
                "MSFT": {"sector": "Technology"},
                "XOM": {"sector": "Energy"},
                "CVX": {"sector": "Energy"}
            }
            return sectors.get(symbol, {})
            
        mock_hist.return_value.get_fundamentals_raw.side_effect = mock_get_fundamentals
        
        gaps = find_underrepresented_sectors()
        assert "Energy" in gaps
        assert "Technology" not in gaps

def test_graceful_degradation_on_failure():
    with patch("pilots.sector_gap.resolve_universe") as mock_resolve:
        mock_resolve.side_effect = Exception("DB Connection Failed")
        
        # Should catch exception and return empty list
        gaps = find_underrepresented_sectors()
        assert gaps == []

def test_empty_universe():
    with patch("pilots.sector_gap.PaperAccountStore") as mock_paper, \
         patch("pilots.sector_gap.HistoricalStore") as mock_hist, \
         patch("pilots.sector_gap.resolve_universe") as mock_resolve:

        mock_resolve.return_value = []
        gaps = find_underrepresented_sectors()
        assert gaps == []


def test_holding_universe_overlap_deduplicates_lookups():
    """A symbol present in both holdings and the tracked universe must only
    be looked up once -- not once per set it appears in."""
    with patch("pilots.sector_gap.PaperAccountStore") as mock_paper, \
         patch("pilots.sector_gap.HistoricalStore") as mock_hist, \
         patch("pilots.sector_gap.resolve_universe") as mock_resolve:

        mock_resolve.return_value = ["AAPL", "MSFT", "XOM"]

        pos1 = PositionSnapshot(symbol="AAPL", qty=10, avg_entry_price=100, market_value=1000, unrealized_pl=0)
        mock_paper.return_value.get_open_positions.return_value = [pos1]

        call_counts = {}

        def mock_get_fundamentals(symbol, max_age_days):
            call_counts[symbol] = call_counts.get(symbol, 0) + 1
            sectors = {
                "AAPL": {"sector": "Technology"},
                "MSFT": {"sector": "Technology"},
                "XOM": {"sector": "Energy"},
            }
            return sectors.get(symbol, {})

        mock_hist.return_value.get_fundamentals_raw.side_effect = mock_get_fundamentals

        gaps = find_underrepresented_sectors()

        # AAPL is in both holdings and universe -- must be looked up once.
        assert call_counts["AAPL"] == 1
        assert call_counts["MSFT"] == 1
        assert call_counts["XOM"] == 1
        assert "Energy" in gaps


def test_sector_lookup_budget_exhausted_degrades_gracefully():
    """Once the wall-clock lookup budget is exhausted, remaining symbols
    are honestly excluded (never fabricated) rather than hanging the
    caller -- mirrors processing_engine.py's established
    PROCESSING_FUNDAMENTALS_MAX_SECONDS_PER_CYCLE convention for this same
    bug class (an unbounded per-ticker live-fetch loop)."""
    with patch("pilots.sector_gap.PaperAccountStore") as mock_paper, \
         patch("pilots.sector_gap.HistoricalStore") as mock_hist, \
         patch("pilots.sector_gap.resolve_universe") as mock_resolve, \
         patch("pilots.sector_gap.time") as mock_time:

        mock_resolve.return_value = ["AAPL", "MSFT", "XOM"]
        mock_paper.return_value.get_open_positions.return_value = []

        def mock_get_fundamentals(symbol, max_age_days):
            return {"sector": "Technology"}

        mock_hist.return_value.get_fundamentals_raw.side_effect = mock_get_fundamentals

        # First call establishes the deadline; every subsequent
        # monotonic() read reports the deadline has already passed, so the
        # loop should bail out before resolving any symbol's sector.
        mock_time.monotonic.side_effect = [0.0] + [1_000_000.0] * 10

        gaps = find_underrepresented_sectors()

        # With every sector lookup skipped, there is no universe sector
        # data at all -- an honest empty result, never a hang or a crash.
        assert gaps == []
