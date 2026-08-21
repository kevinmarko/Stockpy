import pytest
import json
import math
import pandas as pd
from unittest.mock import patch, MagicMock
from investyo_mcp_server import check_overnight_liquidity
from data.market_data import Quote
from datetime import datetime, timezone

def test_check_overnight_liquidity_valid_data():
    """Test that check_overnight_liquidity returns correct approximation format."""
    mock_quote = Quote(
        symbol="AAPL",
        price=150.0,
        bid=149.9,
        ask=150.1,
        timestamp=datetime.now(timezone.utc),
        is_stale=False,
        source="alpaca"
    )

    mock_history = pd.DataFrame({
        "Volume": [1000000, 1200000, 1100000]
    })

    with patch("data.market_data.get_provider") as mock_get_provider, \
         patch("yfinance.Ticker") as mock_ticker:
        
        mock_provider = MagicMock()
        mock_provider.get_latest_quote.return_value = mock_quote
        mock_get_provider.return_value = mock_provider
        
        mock_ticker_instance = MagicMock()
        mock_ticker_instance.history.return_value = mock_history
        mock_ticker.return_value = mock_ticker_instance
        
        result = check_overnight_liquidity("AAPL")
        
        # Verify markdown content
        assert "# Overnight Liquidity Approximation — AAPL" in result
        assert "Data source is an approximation based on Top-of-Book spread and Average Daily Volume. No claims of real Level-2 data exist." in result
        assert "- **Price**: 150.00" in result
        
        # Verify JSON content
        json_block = result.split("```json")[1].split("```")[0]
        payload = json.loads(json_block)
        
        assert payload["symbol"] == "AAPL"
        assert payload["quote"]["price"] == 150.0
        assert payload["quote"]["spread"] == pytest.approx(0.2)
        assert payload["approximation"]["adv_10d"] == 1100000.0
        from settings import settings
        assert payload["approximation"]["approximate_depth_notional"] == pytest.approx(150.0 * 1100000.0 * settings.OVERNIGHT_LIQUIDITY_DEPTH_HEURISTIC)
        assert "No claims of real Level-2 data exist" in payload["approximation"]["disclaimer"]


def test_check_overnight_liquidity_missing_data():
    """Test that check_overnight_liquidity handles missing data gracefully."""
    mock_quote = Quote(
        symbol="UNKNOWN",
        price=math.nan,
        bid=math.nan,
        ask=math.nan,
        timestamp=datetime.now(timezone.utc),
        is_stale=True,
        source="yfinance"
    )

    mock_history = pd.DataFrame()  # Empty dataframe

    with patch("data.market_data.get_provider") as mock_get_provider, \
         patch("yfinance.Ticker") as mock_ticker:
        
        mock_provider = MagicMock()
        mock_provider.get_latest_quote.return_value = mock_quote
        mock_get_provider.return_value = mock_provider
        
        mock_ticker_instance = MagicMock()
        mock_ticker_instance.history.return_value = mock_history
        mock_ticker.return_value = mock_ticker_instance
        
        result = check_overnight_liquidity("UNKNOWN")
        
        assert "- **Price**: N/A" in result
        assert "- **Spread (bps)**: N/A" in result
        assert "- **ADV (10d)**: N/A" in result
        
        json_block = result.split("```json")[1].split("```")[0]
        payload = json.loads(json_block)
        
        assert payload["quote"]["price"] is None
        assert payload["quote"]["spread"] is None
        assert payload["approximation"]["adv_10d"] is None
        assert payload["approximation"]["approximate_depth_notional"] is None
