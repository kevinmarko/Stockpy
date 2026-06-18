import sys
import os
import pandas as pd
import pandera as pa

# Adjust path to import config
sys.path.append("/Users/kevinlee/Desktop/Stockpy")
import config

def test_market_data_schema():
    print("🧪 Testing MarketDataSchema...")
    
    # 1. Valid data
    valid_df = pd.DataFrame({
        "Open": [100.0, 101.0],
        "High": [105.0, 103.0],
        "Low": [99.0, 100.0],
        "Close": [104.0, 102.0],
        "Volume": [1000.0, 1500.0]
    })
    
    try:
        config.MarketDataSchema.validate(valid_df)
        print("✅ Valid market data passed validation.")
    except Exception as e:
        print(f"❌ Valid market data failed validation: {e}")
        return False

    # 2. Invalid data: High < Low
    invalid_df_high_low = pd.DataFrame({
        "Open": [100.0],
        "High": [95.0],   # High is lower than Low
        "Low": [99.0],
        "Close": [96.0],
        "Volume": [1000.0]
    })
    
    try:
        config.MarketDataSchema.validate(invalid_df_high_low)
        print("❌ Invalid market data (High < Low) passed validation. (This is a bug!)")
        return False
    except pa.errors.SchemaError as e:
        print(f"✅ Invalid market data (High < Low) successfully rejected: {e.reason_code}")

    # 3. Invalid data: Negative Volume
    invalid_df_neg_volume = pd.DataFrame({
        "Open": [100.0],
        "High": [105.0],
        "Low": [99.0],
        "Close": [104.0],
        "Volume": [-100.0]  # Negative volume
    })

    try:
        config.MarketDataSchema.validate(invalid_df_neg_volume)
        print("❌ Invalid market data (Negative Volume) passed validation. (This is a bug!)")
        return False
    except pa.errors.SchemaError as e:
        print(f"✅ Invalid market data (Negative Volume) successfully rejected: {e.reason_code}")
        
    return True

def test_dashboard_schema():
    print("\n🧪 Testing DashboardSchema...")
    
    # 1. Valid data
    valid_df = pd.DataFrame({
        "Symbol": ["AAPL", "MSFT"],
        "Price": [150.0, 300.0],
        "sector": ["Technology", "Technology"],
        "shortName": ["Apple Inc.", "Microsoft Corp."],
        "Market Cap": [2.5e12, 2.2e12],
        "Volume": [50000000.0, 20000000.0],
        "RSI": [55.0, 60.0],
        "Beta": [1.2, 0.9]
    })

    try:
        config.DashboardSchema.validate(valid_df)
        print("✅ Valid dashboard data passed validation.")
    except Exception as e:
        print(f"❌ Valid dashboard data failed validation: {e}")
        return False

    # 2. Invalid data: Symbol too long
    invalid_df_symbol = pd.DataFrame({
        "Symbol": ["VERYLONGSYMBOLNAME"],  # Exceeds max length constraint
        "Price": [150.0],
        "sector": ["Technology"],
        "shortName": ["Apple Inc."],
        "Market Cap": [2.5e12],
        "Volume": [50000000.0],
        "RSI": [55.0],
        "Beta": [1.2]
    })

    try:
        config.DashboardSchema.validate(invalid_df_symbol)
        print("❌ Invalid dashboard data (Symbol too long) passed validation. (This is a bug!)")
        return False
    except pa.errors.SchemaError as e:
        print(f"✅ Invalid dashboard data (Symbol too long) successfully rejected: {e.reason_code}")

    return True

if __name__ == "__main__":
    success = test_market_data_schema() and test_dashboard_schema()
    if success:
        print("\n🎉 All schema tests passed successfully!")
        sys.exit(0)
    else:
        print("\n❌ Schema tests failed.")
        sys.exit(1)
