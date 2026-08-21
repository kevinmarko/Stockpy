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

    with patch("data.market_data.get_provider") as mock_get_provider:

        mock_provider = MagicMock()
        mock_provider.get_latest_quote.return_value = mock_quote
        mock_provider.get_intraday_bars.return_value = mock_history
        mock_get_provider.return_value = mock_provider

        result = check_overnight_liquidity("AAPL")

        # Verify the CompositeProvider abstraction was used (not raw yfinance)
        mock_provider.get_intraday_bars.assert_called_once_with(
            "AAPL", lookback_days=10, interval="1d"
        )

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

    with patch("data.market_data.get_provider") as mock_get_provider:

        mock_provider = MagicMock()
        mock_provider.get_latest_quote.return_value = mock_quote
        mock_provider.get_intraday_bars.return_value = mock_history
        mock_get_provider.return_value = mock_provider

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


def test_check_overnight_liquidity_all_nan_volume():
    """An all-NaN Volume column (e.g. a thinly-traded/halted symbol reporting
    no real volume) must degrade adv_10d to None/null, never a NaN that
    leaks as the invalid JSON token `NaN` or renders as literal "nan" text."""
    mock_quote = Quote(
        symbol="HALTED",
        price=10.0,
        bid=9.9,
        ask=10.1,
        timestamp=datetime.now(timezone.utc),
        is_stale=False,
        source="alpaca"
    )

    mock_history = pd.DataFrame({
        "Volume": [math.nan, math.nan, math.nan]
    })

    with patch("data.market_data.get_provider") as mock_get_provider:

        mock_provider = MagicMock()
        mock_provider.get_latest_quote.return_value = mock_quote
        mock_provider.get_intraday_bars.return_value = mock_history
        mock_get_provider.return_value = mock_provider

        result = check_overnight_liquidity("HALTED")

        # Must not crash, and must render N/A rather than literal "nan" text.
        assert "- **ADV (10d)**: N/A" in result
        assert "nan" not in result.lower().split("```json")[0]

        json_block = result.split("```json")[1].split("```")[0]
        # json.loads would raise on a bare `NaN` token if it leaked through
        # (Python's json module accepts it by default, so assert the actual
        # value explicitly rather than relying on a parse failure).
        payload = json.loads(json_block)

        assert payload["approximation"]["adv_10d"] is None
        assert payload["approximation"]["approximate_depth_notional"] is None


def test_check_overnight_liquidity_genuine_zero_values():
    """A genuine halted/zero-volume stock (real 0.0 price and 0.0 ADV) must
    render the real 0.0 values in markdown, not misleadingly show N/A --
    the regression test for the truthy-vs-`is not None` bug."""
    mock_quote = Quote(
        symbol="ZERO",
        price=0.0,
        bid=0.0,
        ask=0.0,
        timestamp=datetime.now(timezone.utc),
        is_stale=False,
        source="alpaca"
    )

    mock_history = pd.DataFrame({
        "Volume": [0, 0, 0]
    })

    with patch("data.market_data.get_provider") as mock_get_provider:

        mock_provider = MagicMock()
        mock_provider.get_latest_quote.return_value = mock_quote
        mock_provider.get_intraday_bars.return_value = mock_history
        mock_get_provider.return_value = mock_provider

        result = check_overnight_liquidity("ZERO")

        # Real zero values must render as real zero values, not "N/A".
        assert "- **Price**: 0.00" in result
        assert "- **Price**: N/A" not in result
        assert "- **ADV (10d)**: 0" in result
        assert "- **ADV (10d)**: N/A" not in result

        json_block = result.split("```json")[1].split("```")[0]
        payload = json.loads(json_block)

        assert payload["quote"]["price"] == 0.0
        assert payload["approximation"]["adv_10d"] == 0.0
        # price is not > 0, so approximate_depth_notional legitimately stays None.
        assert payload["approximation"]["approximate_depth_notional"] is None
