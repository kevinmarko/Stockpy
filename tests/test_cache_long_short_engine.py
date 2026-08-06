import pytest
import datetime
import pandas as pd
from unittest.mock import patch, MagicMock
from engine.cache_long_short_engine import CacheLongShortEngine

@patch("engine.cache_long_short_engine.calculate_rolling_beta")
def test_calculate_beta(mock_calc_beta):
    mock_calc_beta.return_value = pd.Series([1.1, 1.2, 1.3])
    beta = CacheLongShortEngine.calculate_beta("AAPL")
    assert beta == 1.3
    assert mock_calc_beta.called

@patch("engine.cache_long_short_engine.HistoricalStore")
@patch("engine.cache_long_short_engine.DataEngine")
@patch("engine.cache_long_short_engine.analyze_pair")
def test_find_correlated_proxy(mock_analyze, mock_de, mock_hist):
    mock_analyze.side_effect = lambda t, c, p: {
        "correlation": 0.85 if c == "XLK" else 0.5,
        "rolling_p": 0.01 if c == "XLK" else 0.1
    }
    
    mock_hist.return_value.get_bars.side_effect = Exception("Trigger fallback")

    proxy, corr = CacheLongShortEngine.find_correlated_proxy("AAPL")
    assert proxy == "XLK"
    assert corr == 0.85
    from unittest.mock import ANY
    mock_analyze.assert_any_call("AAPL", "XLK", ANY)

@patch("engine.cache_long_short_engine.DataEngine")
@patch("engine.cache_long_short_engine.settings")
def test_find_correlated_proxy_no_match(mock_settings, mock_de):
    mock_settings.CACHE_LONG_SHORT_PROXY_CANDIDATES = ["XLK"]
    with patch("engine.cache_long_short_engine.analyze_pair") as mock_analyze:
        mock_analyze.return_value = {"correlation": 0.5} 
        proxy, corr = CacheLongShortEngine.find_correlated_proxy("AAPL")
        assert proxy is None
        assert corr is None

@patch("engine.cache_long_short_engine.CacheLongShortStore")
@patch("engine.cache_long_short_engine.HistoricalStore")
def test_scan_tlh_opportunities(mock_hist, mock_store_cls):
    mock_store = mock_store_cls.return_value
    mock_lot = MagicMock()
    mock_lot.position_id = 1
    mock_lot.cost_basis_per_share = 150.0
    mock_store.get_open_tax_lots.return_value = [mock_lot]
    
    # Needs 'Close' with uppercase C
    mock_hist().get_bars.return_value = pd.DataFrame({
        "Close": [100.0]
    }, index=[datetime.datetime.now()])
    
    CacheLongShortEngine.scan_tlh_opportunities()
    assert mock_store.get_open_tax_lots.called

@patch("engine.cache_long_short_engine.DataEngine")
@patch("engine.cache_long_short_engine.CacheLongShortStore")
@patch("engine.cache_long_short_engine.analyze_pair")
def test_check_correlation_drift(mock_analyze, mock_store_cls, mock_de):
    mock_analyze.return_value = {"correlation": 0.4} 
    CacheLongShortEngine.check_correlation_drift("AAPL", "XLK")
    from unittest.mock import ANY
    mock_analyze.assert_called_once_with("AAPL", "XLK", ANY)
