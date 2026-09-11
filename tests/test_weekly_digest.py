import pytest
from unittest.mock import patch, MagicMock

from pilots.weekly_digest import compose_digest
from pilots.digest_models import DigestPayload

@patch("pilots.weekly_digest.load_snapshot")
@patch("pilots.weekly_digest.radar_feed")
@patch("pilots.weekly_digest.SymbolViewStore")
@patch("pilots.weekly_digest.find_underrepresented_sectors")
def test_compose_digest_empty(mock_find_sectors, mock_store, mock_radar, mock_load):
    mock_load.return_value = None
    payload = compose_digest()
    assert isinstance(payload, DigestPayload)
    assert len(payload.items) == 0

@patch("pilots.weekly_digest.load_snapshot")
@patch("pilots.weekly_digest.radar_feed")
@patch("pilots.weekly_digest.SymbolViewStore")
@patch("pilots.weekly_digest.find_underrepresented_sectors")
def test_compose_digest_fallback_ladder(mock_find_sectors, mock_store, mock_radar, mock_load):
    mock_load.return_value = {"dummy": "snapshot"}
    mock_radar.return_value = {
        "items": [
            {"symbol": "AAPL", "sector": "Technology", "reason": "reason 1"}, # Not in viewed -> Personalized
            {"symbol": "MSFT", "sector": "Technology", "reason": "reason 2"}, # In viewed, but sector in gaps -> Sector Gap
            {"symbol": "GOOG", "sector": "Communication", "reason": "reason 3"}, # In viewed, sector not in gaps -> Today's Radar
            {"symbol": "AMZN", "sector": "Consumer", "reason": "reason 4"}, # Not in viewed -> Personalized
            {"symbol": "TSLA", "sector": "Consumer", "reason": "reason 5"}, # Not in viewed -> Personalized
            {"symbol": "META", "sector": "Communication", "reason": "reason 6"}, # Ignored (limit 5)
        ]
    }
    
    mock_store_instance = MagicMock()
    mock_store_instance.get_recently_viewed_symbols.return_value = ["MSFT", "GOOG"]
    mock_store.return_value = mock_store_instance
    
    mock_find_sectors.return_value = ["Technology"]
    
    payload = compose_digest()
    
    assert len(payload.items) == 5
    assert payload.items[0].symbol == "AAPL"
    assert payload.items[0].selection_type == "Personalized"
    
    assert payload.items[1].symbol == "MSFT"
    assert payload.items[1].selection_type == "Sector Gap"
    
    assert payload.items[2].symbol == "GOOG"
    assert payload.items[2].selection_type == "Today's Radar"
    
    assert payload.items[3].symbol == "AMZN"
    assert payload.items[3].selection_type == "Personalized"
    
    assert payload.items[4].symbol == "TSLA"
    assert payload.items[4].selection_type == "Personalized"

@patch("pilots.weekly_digest.load_snapshot")
@patch("pilots.weekly_digest.radar_feed")
@patch("pilots.weekly_digest.SymbolViewStore")
@patch("pilots.weekly_digest.find_underrepresented_sectors")
def test_compose_digest_sparse_data(mock_find_sectors, mock_store, mock_radar, mock_load):
    mock_load.return_value = {"dummy": "snapshot"}
    mock_radar.return_value = {
        "items": [
            {"symbol": "AAPL", "sector": "Technology", "reason": "reason 1"},
            {"symbol": "MSFT", "sector": "Technology", "reason": "reason 2"},
        ]
    }
    
    mock_store_instance = MagicMock()
    mock_store_instance.get_recently_viewed_symbols.return_value = ["AAPL", "MSFT"]
    mock_store.return_value = mock_store_instance
    
    mock_find_sectors.return_value = []
    
    payload = compose_digest()
    
    assert len(payload.items) == 2
    for item in payload.items:
        assert item.selection_type == "Today's Radar"
