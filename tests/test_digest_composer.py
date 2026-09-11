"""Tests for the weekly digest composer."""

from unittest import mock

import pytest

from pilots.weekly_digest import compose_weekly_digest


@pytest.fixture
def mock_snapshot():
    return {"timestamp": "2026-09-11T00:00:00Z", "signals": [], "tickers": []}


@pytest.fixture
def mock_radar_feed():
    return {
        "items": [
            {"symbol": "AAPL", "sector": "Technology", "reason": "Apple reason"},
            {"symbol": "MSFT", "sector": "Technology", "reason": "Microsoft reason"},
            {"symbol": "XOM", "sector": "Energy", "reason": "Exxon reason"},
            {"symbol": "JNJ", "sector": "Healthcare", "reason": "JNJ reason"},
            {"symbol": "JPM", "sector": "Financials", "reason": "JPM reason"},
            {"symbol": "TSLA", "sector": "Consumer Cyclical", "reason": "Tesla reason"},
            {"symbol": "V", "sector": "Financials", "reason": "Visa reason"},
        ]
    }


@mock.patch("pilots.weekly_digest.load_snapshot")
@mock.patch("pilots.weekly_digest.radar_feed")
@mock.patch("pilots.weekly_digest.SymbolViewStore")
@mock.patch("pilots.weekly_digest.find_underrepresented_sectors")
def test_compose_weekly_digest_all_rungs(
    mock_find_sectors, mock_view_store, mock_radar, mock_load, mock_snapshot, mock_radar_feed
):
    mock_load.return_value = mock_snapshot
    mock_radar.return_value = mock_radar_feed
    
    # AAPL and MSFT are viewed, others are not
    instance = mock_view_store.return_value
    instance.get_recently_viewed_symbols.return_value = ["AAPL", "MSFT"]
    
    # Energy is underrepresented
    mock_find_sectors.return_value = ["Energy"]
    
    digest = compose_weekly_digest(limit=5)
    
    assert len(digest) == 5
    
    # Unviewed symbols go first (Personalized)
    # The unviewed symbols in radar are: XOM, JNJ, JPM, TSLA, V
    # XOM, JNJ, JPM, TSLA, V will all be Personalized.
    assert digest[0] == {"symbol": "XOM", "reason": "Personalized: Exxon reason", "type": "Personalized"}
    assert digest[1] == {"symbol": "JNJ", "reason": "Personalized: JNJ reason", "type": "Personalized"}
    assert digest[2] == {"symbol": "JPM", "reason": "Personalized: JPM reason", "type": "Personalized"}
    assert digest[3] == {"symbol": "TSLA", "reason": "Personalized: Tesla reason", "type": "Personalized"}
    assert digest[4] == {"symbol": "V", "reason": "Personalized: Visa reason", "type": "Personalized"}


@mock.patch("pilots.weekly_digest.load_snapshot")
@mock.patch("pilots.weekly_digest.radar_feed")
@mock.patch("pilots.weekly_digest.SymbolViewStore")
@mock.patch("pilots.weekly_digest.find_underrepresented_sectors")
def test_compose_weekly_digest_fallback_to_sector_gap_and_radar(
    mock_find_sectors, mock_view_store, mock_radar, mock_load, mock_snapshot, mock_radar_feed
):
    mock_load.return_value = mock_snapshot
    mock_radar.return_value = mock_radar_feed
    
    # ALL symbols in the radar feed are viewed, so "Personalized" will be empty.
    instance = mock_view_store.return_value
    instance.get_recently_viewed_symbols.return_value = [
        "AAPL", "MSFT", "XOM", "JNJ", "JPM", "TSLA", "V"
    ]
    
    # Energy and Healthcare are underrepresented.
    # From radar: XOM is Energy, JNJ is Healthcare.
    mock_find_sectors.return_value = ["Energy", "Healthcare"]
    
    digest = compose_weekly_digest(limit=5)
    
    assert len(digest) == 5
    
    # Rung 1 (Personalized): None, since all are viewed.
    # Rung 2 (Sector Gap): XOM, JNJ
    assert digest[0] == {"symbol": "XOM", "reason": "Sector Gap: Exxon reason", "type": "Sector Gap"}
    assert digest[1] == {"symbol": "JNJ", "reason": "Sector Gap: JNJ reason", "type": "Sector Gap"}
    
    # Rung 3 (Today's Radar): AAPL, MSFT, JPM
    assert digest[2] == {"symbol": "AAPL", "reason": "Today's Radar: Apple reason", "type": "Today's Radar"}
    assert digest[3] == {"symbol": "MSFT", "reason": "Today's Radar: Microsoft reason", "type": "Today's Radar"}
    assert digest[4] == {"symbol": "JPM", "reason": "Today's Radar: JPM reason", "type": "Today's Radar"}


@mock.patch("pilots.weekly_digest.load_snapshot")
@mock.patch("pilots.weekly_digest.radar_feed")
@mock.patch("pilots.weekly_digest.SymbolViewStore")
@mock.patch("pilots.weekly_digest.find_underrepresented_sectors")
def test_compose_weekly_digest_empty_radar(
    mock_find_sectors, mock_view_store, mock_radar, mock_load, mock_snapshot
):
    mock_load.return_value = mock_snapshot
    mock_radar.return_value = {"items": []}
    
    instance = mock_view_store.return_value
    instance.get_recently_viewed_symbols.return_value = []
    mock_find_sectors.return_value = []
    
    digest = compose_weekly_digest()
    assert digest == []


@mock.patch("pilots.weekly_digest.load_snapshot")
@mock.patch("pilots.weekly_digest.radar_feed")
@mock.patch("pilots.weekly_digest.SymbolViewStore")
@mock.patch("pilots.weekly_digest.find_underrepresented_sectors")
def test_compose_weekly_digest_less_than_limit(
    mock_find_sectors, mock_view_store, mock_radar, mock_load, mock_snapshot
):
    mock_load.return_value = mock_snapshot
    mock_radar.return_value = {
        "items": [
            {"symbol": "AAPL", "sector": "Technology", "reason": "Apple reason"},
            {"symbol": "MSFT", "sector": "Technology", "reason": "Microsoft reason"},
        ]
    }
    
    instance = mock_view_store.return_value
    instance.get_recently_viewed_symbols.return_value = ["AAPL"]
    mock_find_sectors.return_value = ["Technology"]
    
    digest = compose_weekly_digest(limit=5)
    
    assert len(digest) == 2
    
    # MSFT is unviewed (Personalized)
    assert digest[0] == {"symbol": "MSFT", "reason": "Personalized: Microsoft reason", "type": "Personalized"}
    
    # AAPL is viewed, but in Technology (Sector Gap)
    assert digest[1] == {"symbol": "AAPL", "reason": "Sector Gap: Apple reason", "type": "Sector Gap"}
