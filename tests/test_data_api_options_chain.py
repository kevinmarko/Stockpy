import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from api.data_api import app
from data.market_data import CompositeOptionsProvider, MarketDataError, Quote, get_options_provider
from settings import settings
import pandas as pd
import numpy as np

client = TestClient(app)

@pytest.fixture
def mock_dependencies():
    # NOTE: `app.dependency_overrides[require_token] = lambda: True` used to be
    # set here at MODULE IMPORT TIME with no teardown. `app` is the shared
    # FastAPI singleton every test_data_api*.py file imports from
    # `api.data_api` -- an override with no cleanup stays live for every other
    # test collected into the same pytest-xdist worker for the rest of the
    # process, which silently made every `..._401_with_wrong_token`-style test
    # elsewhere in the suite pass a real token check for free (CI caught this:
    # `assert 200 == 401` across test_data_api.py/test_data_api_ai.py/etc. once
    # the full suite ran this file alongside them). Patch the real
    # `settings.STATE_API_TOKEN` singleton instead -- the same pattern every
    # other test_data_api*.py file uses -- and present a real matching Bearer
    # token, scoped to just this fixture's `with` block.
    with patch("data.market_data.get_provider") as mock_get_provider, \
         patch("data.market_data.get_options_provider") as mock_get_options, \
         patch.object(settings, "STATE_API_TOKEN", "test-token"), \
         patch.object(settings, "RISK_FREE_RATE", 0.05):

        mock_provider = MagicMock()
        mock_quote = Quote(
            symbol="AAPL",
            price=150.0,
            bid=149.9,
            ask=150.1,
            timestamp=pd.Timestamp.now(tz="UTC"),
            is_stale=False,
            source="fmp"
        )
        mock_provider.get_latest_quote.return_value = mock_quote
        mock_get_provider.return_value = mock_provider

        mock_options = MagicMock()
        mock_get_options.return_value = mock_options

        yield mock_provider, mock_options

def test_options_chain_chance_of_profit_reference_case(mock_dependencies):
    """
    Test the 'Chance of Profit' calculation against a known reference case to ensure statistical validity.
    
    Reference Case (Call Option):
    S = 150.0
    K = 150.0
    T = 30 days (30 / 365) = 0.08219
    r = 0.05
    sigma (IV) = 0.25
    
    From Black-Scholes:
    d1 = (ln(150/150) + (0.05 + 0.5 * 0.25^2) * 0.08219) / (0.25 * sqrt(0.08219))
       = (0 + 0.08125 * 0.08219) / (0.25 * 0.28669) = 0.006678 / 0.07167 = 0.09317
    d2 = 0.09317 - 0.07167 = 0.0215
    
    Price = 150 * N(d1) - 150 * exp(-rT) * N(d2)
          = 150 * 0.5371 - 150 * 0.9959 * 0.5085 = 80.565 - 75.96 = 4.605
          
    Break-Even = K + Price = 150 + 4.605 = 154.605
    
    Chance of Profit = N(d2_be)
    d2_be = (ln(150 / 154.605) + (0.05 - 0.5 * 0.25^2) * 0.08219) / (0.25 * sqrt(0.08219))
          = (-0.03025 + 0.01875 * 0.08219) / 0.07167
          = (-0.03025 + 0.00154) / 0.07167 = -0.4005
          
    N(-0.4005) is approx 0.3444 (34.44%)
    """
    mock_provider, mock_options = mock_dependencies
    
    # Expiration is in 30 days
    today = pd.Timestamp.now().date()
    exp_date = (today + pd.Timedelta(days=30)).strftime("%Y-%m-%d")
    
    # Mock yfinance-like chain response
    mock_chain = MagicMock()
    mock_chain.calls = pd.DataFrame([{
        "contractSymbol": "AAPL230120C00150000",
        "strike": 150.0,
        "lastPrice": 4.60,
        "bid": 4.50,
        "ask": 4.70,
        "volume": 100,
        "openInterest": 500,
        "impliedVolatility": 0.25,
        "inTheMoney": False
    }])
    mock_chain.puts = pd.DataFrame()
    mock_options.fetch_options_chain.return_value = mock_chain
    
    response = client.get(
        f"/data/options/chain/AAPL?expiration={exp_date}", 
        headers={"Authorization": "Bearer test-token"}
    )
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    data = response.json()
    
    assert len(data["calls"]) == 1
    call = data["calls"][0]
    
    # Check that price is correctly passed to the chance of profit calculation
    chance_of_profit = call["greeks"]["chanceOfProfit"]
    
    # Approx 34.4% chance of profit
    assert abs(chance_of_profit - 0.3444) < 0.005, f"Expected ~0.3444, got {chance_of_profit}"
