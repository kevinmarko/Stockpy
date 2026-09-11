from unittest.mock import MagicMock, patch

from pilots.sector_gap import find_underrepresented_sectors


def test_find_underrepresented_sectors():
    with patch("pilots.sector_gap.resolve_universe") as mock_resolve:
        mock_resolve.return_value = ["AAPL", "MSFT", "JNJ", "XOM"]
        
        with patch("pilots.sector_gap.PaperAccountStore") as mock_paper:
            mock_store = MagicMock()
            
            # PositionSnapshot mock
            mock_pos1 = MagicMock()
            mock_pos1.symbol = "AAPL"
            mock_store.get_open_positions.return_value = [mock_pos1]
            mock_paper.return_value = mock_store
            
            with patch("pilots.sector_gap.HistoricalStore") as mock_hist:
                mock_hist_store = MagicMock()
                
                def mock_get_fundamentals_raw(sym, max_age_days):
                    sectors = {
                        "AAPL": {"sector": "Technology"},
                        "MSFT": {"sector": "Technology"},
                        "JNJ": {"sector": "Healthcare"},
                        "XOM": {"sector": "Energy"}
                    }
                    return sectors.get(sym, {})
                
                mock_hist_store.get_fundamentals_raw.side_effect = mock_get_fundamentals_raw
                mock_hist.return_value = mock_hist_store
                
                result = find_underrepresented_sectors()
                
                assert "Healthcare" in result
                assert "Energy" in result
                assert "Technology" not in result
                
                assert sorted(result) == ["Energy", "Healthcare"]

def test_find_underrepresented_sectors_error_handling():
    with patch("pilots.sector_gap.resolve_universe") as mock_resolve:
        mock_resolve.side_effect = Exception("Network error")
        result = find_underrepresented_sectors()
        assert result == []
