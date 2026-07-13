import pytest
import config
from reporting.sheet_publisher import rec_to_sheet_row
from engine.advisory import Recommendation
from data.robinhood_portfolio import AccountSnapshot, PortfolioPosition
from datetime import datetime

class TestColumnSchemaIntegrity:
    def test_schema_length(self):
        # The schema length is currently exactly 93 columns. 
        # If you genuinely added a new column, update this test deliberately.
        # This prevents accidental schema inflation.
        assert len(config.COLUMN_SCHEMA) == 93

    def test_no_duplicate_keys(self):
        keys = config.get_internal_keys()
        assert len(keys) == len(set(keys)), "COLUMN_SCHEMA contains duplicate keys"

    def test_no_duplicate_headers(self):
        headers = config.get_headers()
        assert len(headers) == len(set(headers)), "COLUMN_SCHEMA contains duplicate headers"

    def test_valid_formats(self):
        valid_formats = {"string", "number", "currency", "currency_large", "percent"}
        for col in config.COLUMN_SCHEMA:
            assert "header" in col
            assert "key" in col
            assert "format" in col
            assert col["format"] in valid_formats, f"Unknown format {col['format']} for key {col['key']}"

    def test_validate_config_runs(self):
        # Exercises the duplicate-key/header guard
        # This was previously only run via `python config.py`
        config.Config.validate_config()


class TestAdvisoryColumnCoverage:
    KNOWN_UNMAPPED_ORCHESTRATOR_ONLY_COLUMNS = frozenset([
        "ARIMA", "Aroon Down", "Aroon Up", "BF_Allocation", "BF_Selection", "Beta",
        "Book Value", "Chandelier Exit", "CoVaR Proxy", "Coppock Curve", 
        "Correlation_Cluster", "DPH", "DPS", "Earnings_Date",
        "Forecast_10", "Forecast_30_Prophet_Lower", "Forecast_30_Prophet_Upper",
        "Forecast_60", "Forecast_90", "Gordon Fair Value", "Graham Num",
        "Institutional Velocity", "Leverage Distress Factor", "LowVol_Z",
        "MACD_Signal", "MAE", "MC_Lower", "MC_Target", "MC_Upper", "MFE",
        "Market Cap", "Momentum_Vol_Scaled", "Multifactor_Composite",
        "News_Sentiment", "Options IV Edge", "P/E", "Portfolio_Heat",
        "Quality Score", "Quality_Z", "ROC_12M", "ROC_6M", "RS-MACD",
        "Realized Slippage", "Realized_Vol_Rank", "SMA_200", "SMA_5",
        "SMA_50", "Size_Z", "Target_Days", "True_IVR", "VRP", "VaR 95",
        "Value_Z", "Volume", "XSec_12_1M", "XSec_Momentum_Rank", "sector", "shortName"
    ])

    def test_rec_to_sheet_row_coverage(self):
        class MockRec:
            symbol = 'AAPL'
            action = 'BUY'
            rationale = 'Good'
            strategy = 'Value'
            buy_range = '1-2'
            sell_range = '3-4'
            forecast = 100.0
            conviction = 0.8
            suggested_position_pct = 0.05
            data_quality = 'FULL'
            key_indicators = {}
            
        rec = MockRec()
        snap = AccountSnapshot(
            positions={'AAPL': PortfolioPosition(
                symbol='AAPL', quantity=10, average_cost=100.0, current_price=150.0, 
                market_value=1500.0, unrealized_pl=500.0, unrealized_pl_pct=0.5, 
                dividends_received=10.0, name='Apple'
            )}, 
            buying_power=100.0, total_equity=1600.0, total_dividends=10.0, 
            fetched_at=datetime.utcnow()
        )
        row = rec_to_sheet_row(rec, snap, 150.0)

        mapped_keys = set(row.keys()).intersection(config.get_internal_keys())
        schema_keys = set(config.get_internal_keys())
        missing_keys = schema_keys - mapped_keys

        # 35 mapped keys currently. If this breaks, it means someone renamed a COLUMN_SCHEMA key
        # without updating rec_to_sheet_row.
        assert len(mapped_keys) == 35, f"Expected 35 mapped advisory columns, found {len(mapped_keys)}"
        
        # Verify the 59 unmapped ones exactly match the known orchestrator-only set
        assert missing_keys == self.KNOWN_UNMAPPED_ORCHESTRATOR_ONLY_COLUMNS
