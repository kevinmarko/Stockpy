import pytest
from unittest import mock

from main import _read_macro_snapshot_hint

def test_read_macro_snapshot_hint_missing_file(monkeypatch):
    """Test that default values are returned when the file does not exist."""
    mock_settings = mock.MagicMock()
    mock_path = mock.MagicMock()
    mock_path.exists.return_value = False
    mock_settings.OUTPUT_DIR.__truediv__.return_value = mock_path

    monkeypatch.setattr("main.settings", mock_settings)

    result = _read_macro_snapshot_hint()

    assert result == {"vix": None, "market_regime": None}
    mock_path.exists.assert_called_once()

def test_read_macro_snapshot_hint_happy_path(monkeypatch):
    """Test that valid JSON data with vix and market_regime is successfully parsed."""
    mock_settings = mock.MagicMock()
    mock_path = mock.MagicMock()
    mock_path.exists.return_value = True

    # Valid JSON payload
    valid_payload = '{"vix": 15.5, "market_regime": "bull"}'
    mock_path.read_text.return_value = valid_payload
    mock_settings.OUTPUT_DIR.__truediv__.return_value = mock_path

    monkeypatch.setattr("main.settings", mock_settings)

    result = _read_macro_snapshot_hint()

    assert result == {"vix": 15.5, "market_regime": "bull"}
    mock_path.exists.assert_called_once()
    mock_path.read_text.assert_called_once_with(encoding="utf-8")

def test_read_macro_snapshot_hint_invalid_data(monkeypatch):
    """Test edge cases with invalid or missing data such as 0, None or missing fields."""
    mock_settings = mock.MagicMock()
    mock_path = mock.MagicMock()
    mock_path.exists.return_value = True
    mock_settings.OUTPUT_DIR.__truediv__.return_value = mock_path

    monkeypatch.setattr("main.settings", mock_settings)

    # Test case 1: VIX is 0, market_regime is None
    mock_path.read_text.return_value = '{"vix": 0, "market_regime": null}'
    assert _read_macro_snapshot_hint() == {"vix": None, "market_regime": None}

    # Test case 2: Missing fields
    mock_path.read_text.return_value = '{"other_key": "value"}'
    assert _read_macro_snapshot_hint() == {"vix": None, "market_regime": None}

    # Test case 3: VIX is 0.0, market_regime is empty string
    mock_path.read_text.return_value = '{"vix": 0.0, "market_regime": ""}'
    assert _read_macro_snapshot_hint() == {"vix": None, "market_regime": None}

def test_read_macro_snapshot_hint_file_io_exception(monkeypatch):
    """Test that the function handles file I/O exceptions by returning default values."""
    mock_settings = mock.MagicMock()
    mock_path = mock.MagicMock()
    mock_path.exists.return_value = True
    # Simulate a PermissionError when reading the file
    mock_path.read_text.side_effect = PermissionError("Permission denied")
    mock_settings.OUTPUT_DIR.__truediv__.return_value = mock_path

    monkeypatch.setattr("main.settings", mock_settings)

    result = _read_macro_snapshot_hint()

    assert result == {"vix": None, "market_regime": None}
    mock_path.exists.assert_called_once()
    mock_path.read_text.assert_called_once_with(encoding="utf-8")
