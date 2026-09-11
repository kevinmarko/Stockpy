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
