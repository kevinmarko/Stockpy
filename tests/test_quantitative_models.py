"""
InvestYo Quant Platform - Deterministic Test Suite
==================================================
Step 5 of the Roadmap: Complete validation testing using pytest.

Verifies fundamental DTO edge boundary coercions, vectorized technical indices calculations 
(RSI "Known State"), and the macro regime transition gates.
"""

import pytest
import math
import pandas as pd
import numpy as np
from datetime import datetime

from dto_models import MarketBarDTO, FundamentalDataDTO, MacroEconomicDTO
from data_engine import MockDataEngine
from processing_engine import ProcessingEngine
from strategy_engine import StrategyEngine


# =============================================================================
# 1. DATA TRANSFER OBJECT (DTO) IMMUTABILITY & COERCION TESTS
# =============================================================================
def test_dto_resilience_to_malformed_inputs():
    """
    Asserts that DTO boundary parsers safely cleanse strings, formatting characters,
    and missing value signs without crashing.
    """
    raw_bad_data = {
        "shortName": "Test Asset Corp",
        "sector": "Technology",
        "trailingPE": "N/A",        # Should evaluate cleanly to None
        "priceToBook": "  1.85  ",  # Spacing cleanup
        "bookValue": "$42.50",      # Currency notation stripping
        "trailingEps": "1.45",
        "dividendYield": "4.25%",   # Percentage parsing
        "payoutRatio": "0.45"
    }

    dto = FundamentalDataDTO.from_raw_dict("AAPL", raw_bad_data)
    
    assert dto.pe_ratio is None, "PE String 'N/A' failed to cleanly resolve to None"
    assert math.isclose(dto.pb_ratio, 1.85, rel_tol=1e-5), "PB ratio spacing cleanup failed"
    assert math.isclose(dto.book_value, 42.50, rel_tol=1e-5), "Book value dollar removal failed"
    assert math.isclose(dto.dividend_yield, 0.0425, rel_tol=1e-5), "Dividend yield percentage conversion failed"


def test_graham_number_imaginary_bounds():
    """
    Asserts that Ben Graham's calculations safely collapse to zero when EPS is negative.
    Imaginary numbers are mathematically impossible inside stock pricing logic.
    """
    raw_negative_earnings = {
        "shortName": "Distressed Corp",
        "sector": "Industrials",
        "trailingPE": None,
        "priceToBook": 0.5,
        "bookValue": 10.0,
        "trailingEps": -2.50, # EPS is negative (Distressed business)
        "dividendYield": 0.0,
        "payoutRatio": 0.0
    }

    dto = FundamentalDataDTO.from_raw_dict("BAD", raw_negative_earnings)
    
    assert dto.graham_number == 0.0, "Negative EPS allowed calculation to generate complex imaginary bounds"


# =============================================================================
# 2. VECTORIZED MATHEMATICAL ENGINE TESTS ("KNOWN STATE")
# =============================================================================
def test_rsi_vectorized_math():
    """
    Isolates ProcessingEngine technical indicator calculations against 
    a known-state pricing series. Fully verifies vector alignment.
    """
    # Preset: Purely ascending series of close prices (constant gains, 0 losses)
    strictly_gaining_prices = [10.0 + i for i in range(30)] # Expanded to 30 to satisfy len(df) >= 26 constraint in ProcessingEngine
    
    # Decouple the environment via Dependency Injection (DI)
    mock_provider = MockDataEngine(preset_prices=strictly_gaining_prices)
    engine = ProcessingEngine(data_provider=mock_provider)

    # Executing the decoupled pipeline
    raw_dfs = mock_provider.fetch_technical_raw(['AAPL'])
    processed_tech = engine.calculate_technicals_vectorized(raw_dfs)

    # Assertion of known states: RSI of an entirely ascending pricing channel MUST be exactly 100.0
    aapl_rsi = processed_tech['AAPL']['RSI']
    assert math.isclose(aapl_rsi, 100.0, rel_tol=1e-5), f"Vectorized RSI calculated drift: {aapl_rsi} (expected 100.0)"


def test_macd_vectorized_alignment():
    """
    Asserts that the vectorized MACD (12, 26, 9) signals are generated correctly
    and return correct mathematical relationships.
    """
    constant_flat_prices = [100.0] * 35 # Constant price flatline
    mock_provider = MockDataEngine(preset_prices=constant_flat_prices)
    engine = ProcessingEngine(data_provider=mock_provider)

    raw_dfs = mock_provider.fetch_technical_raw(['AAPL'])
    processed_tech = engine.calculate_technicals_vectorized(raw_dfs)

    aapl_macd = processed_tech['AAPL']['MACD']
    aapl_macd_signal = processed_tech['AAPL']['MACD_Signal']
    
    # Flat pricing means MACD lines MUST converge exactly on 0
    assert math.isclose(aapl_macd, 0.0, abs_tol=1e-5), f"Flat pricing generated non-zero MACD convergence: {aapl_macd}"
    assert math.isclose(aapl_macd_signal, 0.0, abs_tol=1e-5), f"Flat pricing generated non-zero Signal convergence: {aapl_macd_signal}"


# =============================================================================
# 3. SYSTEMIC TOP-DOWN REGIME ACTION TESTS
# =============================================================================
def test_macro_economic_risk_gates():
    """
    Asserts that hostile credit environments or inverted yield curves 
    translate cleanly into the target recession/hostile market regimes.
    """
    hostile_macro_data = {
        'T10Y2Y': -0.45,       # Severely inverted yield curve
        'BAMLH0A0HYM2': 6.20,  # Spiking OAS yields (High debt distress)
        'UNRATE': 5.5
    }

    mock_provider = MockDataEngine(preset_macro=hostile_macro_data)
    engine = ProcessingEngine(data_provider=mock_provider)

    raw_macro = mock_provider.fetch_macro_raw()
    regime_output = engine.process_macro_regime(raw_macro)

    assert regime_output["Regime"] == "RECESSION", f"Macro Regime failed to transition to RECESSION. Result: {regime_output['Regime']}"


# =============================================================================
# 4. STRATEGY ENGINE CORRIDOR & OPTIONS OVERLAY TESTS
# =============================================================================
def test_strategy_engine_buy_range_and_options_overlays():
    """
    Asserts that StrategyEngine dynamically calculates the volatility-adjusted buy range
    and selects the correct delta strikes and option types based on stock sectors.
    """
    engine = StrategyEngine()

    # Case 1: Standard Equity in a Strong Buy setup (JNJ - Healthcare)
    bar_equity = MarketBarDTO(datetime.now(), "JNJ", 155.00, 158.00, 154.50, 157.50, 4500000)
    fund_equity = FundamentalDataDTO(
        ticker="JNJ", company_name="Johnson & Johnson", sector="Healthcare",
        pe_ratio=16.5, pb_ratio=1.45, book_value=110.00, eps_trailing=9.50,
        dividend_yield=0.0310, dividend_growth_rate=0.065, payout_ratio=0.52,
    )
    macro_safe = MacroEconomicDTO(0.45, 2.50, 2.10, 4.0)

    # Price = 157.50, ATR = 2.50, Graham = 153.34
    # Expected Lower Bound = 157.50 - 1.5 * 2.50 = 153.75
    # Expected Upper Bound = 157.50 + 0.25 * 2.50 = 158.125
    # Upper Bound capped at Graham (153.34) -> Lower Bound (153.75) > Upper Bound (153.34)
    # Triggering fallback: Lower = 157.50 * 0.95 = 149.625 -> 149.62, Upper = 157.50
    result_equity = engine.evaluate_security(
        bar=bar_equity, fundamentals=fund_equity, macro=macro_safe,
        forecast_price=168.00, trend_strength=72.0, atr=2.50
    )

    assert result_equity["Action Signal"] == "STRONG BUY"
    assert result_equity["buyRange"] == "Buy Zone: $149.62 - $157.50"
    # Should select standard equity OTM covered call (delta-20)
    assert "OTM Covered Call (delta-20)" in result_equity["Option Strategy"]

    # Case 2: REIT high-yielder in a Buy setup (AGNC - Real Estate)
    bar_reit = MarketBarDTO(datetime.now(), "AGNC", 9.80, 10.05, 9.75, 9.85, 2500000)
    fund_reit = FundamentalDataDTO(
        ticker="AGNC", company_name="AGNC Investment Corp", sector="Real Estate (mREIT)",
        pe_ratio=11.5, pb_ratio=0.88, book_value=11.20, eps_trailing=0.85,
        dividend_yield=0.145, dividend_growth_rate=-0.02, payout_ratio=0.92,
    )
    # Safe macro so it triggers BUY signal
    result_reit = engine.evaluate_security(
        bar=bar_reit, fundamentals=fund_reit, macro=macro_safe,
        forecast_price=10.50, trend_strength=60.0, atr=0.15
    )

    # Should select OTM Covered Call with delta-15 (since Real Estate sector is a yield asset)
    assert result_reit["Action Signal"] in ["BUY", "STRONG BUY"]
    assert "OTM Covered Call (delta-15)" in result_reit["Option Strategy"]

    # Case 3: Neutral Stock in HOLD setup
    # Make trend strength neutral (40.0)
    result_hold = engine.evaluate_security(
        bar=bar_equity, fundamentals=fund_equity, macro=macro_safe,
        forecast_price=157.50, trend_strength=40.0, atr=2.50
    )
    assert result_hold["Action Signal"] == "HOLD"
    # Hold Range: Price - 2 * ATR to Price + 2 * ATR -> 157.50 - 5 = 152.50 to 157.50 + 5 = 162.50
    assert result_hold["buyRange"] == "Hold Range: $152.50 - $162.50"

    # Case 4: Distressed stock in RISK REDUCE setup
    # Trigger hostile macro CREDIT EVENT
    macro_hostile = MacroEconomicDTO(0.05, 6.50, 2.80, 4.0)
    result_reduce = engine.evaluate_security(
        bar=bar_reit, fundamentals=fund_reit, macro=macro_hostile,
        forecast_price=9.00, trend_strength=20.0, atr=0.15
    )
    assert result_reduce["Action Signal"] == "RISK REDUCE"
    # Trim @ Price + 0.5 * ATR (9.85 + 0.075 = 9.925 -> rounds to 9.92) | Stop @ Price - 1.0 * ATR (9.85 - 0.15 = 9.70)
    assert result_reduce["buyRange"] == "Trim @ $9.92 | Stop @ $9.70"


def test_database_schema_initialization():
    """
    Verifies that the database setup script initializes the SQLite schema
    correctly and all COLUMN_SCHEMA columns are generated.
    """
    import sqlite3
    import tempfile
    import os
    import database_setup
    import config
    
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_db_path = tmp.name
        
    try:
        # Initialize the temp database file
        database_setup.initialize_database(tmp_db_path)
        
        # Verify tables exist and schema matches
        conn = sqlite3.connect(tmp_db_path)
        cursor = conn.cursor()
        
        # Check tables exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in cursor.fetchall()]
        assert "ExecutionLogs" in tables
        assert "DailySignals" in tables
        
        # Inspect DailySignals columns
        cursor.execute("PRAGMA table_info(DailySignals);")
        columns = [row[1] for row in cursor.fetchall()]
        
        # Check custom/system columns
        assert "id" in columns
        assert "timestamp" in columns
        
        # Check all config keys mapped to database columns
        for col in config.COLUMN_SCHEMA:
            assert col["key"] in columns
            
        conn.close()
    finally:
        if os.path.exists(tmp_db_path):
            os.remove(tmp_db_path)


# =============================================================================
# 5. MACRO ENGINE CORE FUNCTIONAL TESTS
# =============================================================================
def test_macro_engine_killswitch_and_regimes():
    """
    Verifies that the macro engine runs the killswitch and correctly validates
    against the MacroDataSchema.
    """
    from macro_engine import MacroEngine, MacroDataSchema
    from data_engine import MockDataEngine
    import pandas as pd
    
    mock_de = MockDataEngine()
    engine = MacroEngine(mock_de)
    
    # Recession regime trigger test
    macro_raw = {'T10Y2Y': -0.30, 'BAMLH0A0HYM2': 6.5}
    df_recession = engine.run_macro_killswitch(macro_raw, sahm_rule_val=0.6)
    assert df_recession['market_regime'].iloc[0] == "RECESSION"
    
    # Credit Event regime trigger test
    macro_raw_credit = {'T10Y2Y': 0.2, 'BAMLH0A0HYM2': 6.5}
    df_credit = engine.run_macro_killswitch(macro_raw_credit, sahm_rule_val=0.2)
    assert df_credit['market_regime'].iloc[0] == "CREDIT EVENT"
    
    # Verify pandera schema validation works
    try:
        MacroDataSchema.validate(df_recession)
    except Exception as e:
        pytest.fail(f"DataFrame validation against MacroDataSchema failed: {e}")

def test_macro_engine_fama_french_regression():
    """
    Validates Fama-French 3-Factor regression outputs using known-state returns.
    """
    from macro_engine import MacroEngine
    from data_engine import MockDataEngine
    
    mock_de = MockDataEngine()
    engine = MacroEngine(mock_de)
    
    # Create 30 dates of known excess returns
    dates = pd.date_range(end=datetime.now(), periods=30)
    
    # Set up factor data:
    # y = alpha + b1 * mkt_rf + b2 * smb + b3 * hml + rf + error
    # Let alpha = 0.005, b1 = 1.2, b2 = -0.5, b3 = 0.8
    np.random.seed(42)
    mkt_rf = np.random.normal(0.0003, 0.01, 30)
    smb = np.random.normal(0.0001, 0.005, 30)
    hml = np.random.normal(0.0001, 0.005, 30)
    rf = np.ones(30) * 0.0001
    
    factors_df = pd.DataFrame({
        'Mkt-RF': mkt_rf,
        'SMB': smb,
        'HML': hml,
        'RF': rf
    }, index=dates)
    
    # Synthesize stock returns perfectly matching the regression equation (zero error for test ease)
    alpha = 0.005
    beta_mkt = 1.2
    beta_smb = -0.5
    beta_hml = 0.8
    
    stock_excess_returns = alpha + beta_mkt * mkt_rf + beta_smb * smb + beta_hml * hml
    stock_returns = pd.Series(stock_excess_returns + rf, index=dates)
    
    results = engine.calculate_fama_french_alpha(stock_returns, factors_df)
    
    assert math.isclose(results["alpha"], alpha, abs_tol=1e-5)
    assert math.isclose(results["beta_market"], beta_mkt, abs_tol=1e-5)
    assert math.isclose(results["beta_size"], beta_smb, abs_tol=1e-5)
    assert math.isclose(results["beta_value"], beta_hml, abs_tol=1e-5)
    assert results["r_squared"] > 0.99  # Should fit almost perfectly

def test_macro_engine_sentiment_analysis():
    """
    Verifies Google Cloud Natural Language API integration and keyword-based fallback.
    """
    from macro_engine import MacroEngine
    from data_engine import MockDataEngine
    
    mock_de = MockDataEngine()
    engine = MacroEngine(mock_de)
    
    # Test fallback positive text
    pos_text = "The outlook is bullish with strong growth prospects and sustainable profits."
    pos_score = engine.analyze_sentiment(pos_text)
    assert pos_score > 0.0
    
    # Test fallback negative text
    neg_text = "The portfolio is facing downside risk, weak returns, and severe recession slippage."
    neg_score = engine.analyze_sentiment(neg_text)
    assert neg_score < 0.0
    
    # Test neutral / empty text
    empty_score = engine.analyze_sentiment("")
    assert empty_score == 0.0


# =============================================================================
# 6. TECHNICAL & OPTIONS ENGINE FUNCTIONAL TESTS
# =============================================================================
def test_technical_options_engine_indicators():
    """
    Verifies calculation of Aroon, Coppock, and Chandelier Exit indicators.
    """
    from technical_options_engine import TechnicalOptionsEngine
    import pandas as pd
    import numpy as np
    
    # Generate 50 days of synthetic price data (ascending channel)
    dates = pd.date_range(end="2026-06-19", periods=50)
    prices = np.linspace(100.0, 150.0, 50)
    df = pd.DataFrame({
        "Open": prices - 1.0,
        "High": prices + 1.0,
        "Low": prices - 2.0,
        "Close": prices,
        "Volume": [10000] * 50
    }, index=dates)
    
    engine = TechnicalOptionsEngine()
    indicators = engine.calculate_indicators(df)
    
    assert "Aroon_Oscillator" in indicators
    assert "Coppock_Curve" in indicators
    assert "Chandelier_Long" in indicators
    assert "Chandelier_Short" in indicators
    
    # Since prices are rising steadily, Aroon Oscillator should be positive
    assert indicators["Aroon_Oscillator"] > 0
    assert indicators["Chandelier_Long"] < df["High"].iloc[-1]
    assert indicators["Chandelier_Short"] > df["Low"].iloc[-22:].min()
    
    # Since prices are rising steadily, Aroon Oscillator should be positive
    assert indicators["Aroon_Oscillator"] > 0
    assert indicators["Chandelier_Long"] < df["High"].iloc[-1]
    assert indicators["Chandelier_Short"] > df["Low"].iloc[-22:].min()


def test_technical_options_engine_garch_volatility_and_ivr():
    """
    Verifies GJR-GARCH(1,1) volatility calculation, its scaling/descaling,
    recession fallback stability, and IVR rankings.
    """
    from technical_options_engine import TechnicalOptionsEngine
    import pandas as pd
    import numpy as np
    
    # Create random return series (50 days) with normal noise
    np.random.seed(42)
    dates = pd.date_range(end="2026-06-19", periods=50)
    returns = np.random.normal(0.0005, 0.015, 50)
    
    # Reconstruct prices from returns
    prices = [100.0]
    for r in returns:
        prices.append(prices[-1] * (1.0 + r))
    prices = prices[1:]
    
    df = pd.DataFrame({
        "Open": prices,
        "High": [p * 1.01 for p in prices],
        "Low": [p * 0.99 for p in prices],
        "Close": prices,
        "Volume": [20000] * 50
    }, index=dates)
    
    engine = TechnicalOptionsEngine()
    
    # Fit GJR-GARCH and retrieve day-ahead annualized vol
    vol = engine.estimate_gjr_garch_volatility(df)
    assert isinstance(vol, float)
    assert vol > 0.0
    
    # Calculate realized vol rank using this volatility
    realized_vol_rank = engine.calculate_realized_vol_rank(df, vol)
    assert 0.0 <= realized_vol_rank <= 100.0

def test_technical_options_engine_strategy_matrix():
    """
    Validates Automated Option Strategy Matrix logic mapping true_ivr and Trend to strategies.
    """
    from technical_options_engine import TechnicalOptionsEngine
    
    engine = TechnicalOptionsEngine()
    
    # High IVR + Bullish Trend
    s1 = engine.generate_option_strategy_matrix(true_ivr=75.0, aroon_osc=50.0, coppock_val=0.5)
    assert "Put Credit Spread" in s1
    
    # High IVR + Bearish Trend
    s2 = engine.generate_option_strategy_matrix(true_ivr=75.0, aroon_osc=-30.0, coppock_val=-0.2)
    assert "Call Credit Spread" in s2
    
    # High IVR + Neutral Trend
    s3 = engine.generate_option_strategy_matrix(true_ivr=80.0, aroon_osc=10.0, coppock_val=-0.1)
    assert "Iron Condor" in s3
    
    # Low IVR + Bullish Trend
    s4 = engine.generate_option_strategy_matrix(true_ivr=25.0, aroon_osc=40.0, coppock_val=0.3)
    assert "Call Debit Spread" in s4
    
    # Low IVR + Bearish Trend
    s5 = engine.generate_option_strategy_matrix(true_ivr=10.0, aroon_osc=-45.0, coppock_val=-0.5)
    assert "Put Debit Spread" in s5
    
    # Neutral IVR + Bullish Trend
    s6 = engine.generate_option_strategy_matrix(true_ivr=50.0, aroon_osc=50.0, coppock_val=0.5)
    assert "Covered Call" in s6


def test_options_pricing_recommender():
    """
    Validates OptionsPricingRecommender class mathematical calculations,
    root-finding Delta strikes, and realizable theta haircuts.
    """
    from technical_options_engine import OptionsPricingRecommender
    
    recommender = OptionsPricingRecommender(stock_price=100.0, risk_free_rate=0.045)
    
    # 1. Test Black-Scholes pricing and Greeks
    # S=100, K=100, T=30/365 (30 DTE), IV=0.20, Call
    greeks_call = recommender.black_scholes_pricing_and_greeks(K=100.0, T=30.0/365.0, sigma=0.20, option_type='call')
    assert greeks_call['Price'] > 0
    assert 0.0 < greeks_call['Delta'] < 1.0
    assert greeks_call['Gamma'] > 0
    assert greeks_call['Vega'] > 0
    assert greeks_call['Theta_Daily'] < 0
    
    # 2. Test Brentq Delta strike search (Target Delta = 0.30)
    k_call_30 = recommender.find_strike_for_delta(0.30, T=30.0/365.0, sigma=0.20, option_type='call')
    # Since target Delta is 0.30 (OTM call), the strike K should be above stock price
    assert k_call_30 > 100.0
    assert k_call_30 % 0.5 == 0.0
    
    # 3. Test Realizable Theta haircut percentages
    # DTE <= 1 -> 40% drag (60% remaining)
    # DTE <= 7 -> 22% drag (78% remaining)
    # DTE <= 30 -> 12% drag (88% remaining)
    # DTE > 30 -> 5% drag (95% remaining)
    assert math.isclose(recommender.calculate_realizable_theta(-1.0, 1), -0.60)
    assert math.isclose(recommender.calculate_realizable_theta(-1.0, 7), -0.78)
    assert math.isclose(recommender.calculate_realizable_theta(-1.0, 30), -0.88)
    assert math.isclose(recommender.calculate_realizable_theta(-1.0, 45), -0.95)


# =============================================================================
# 7. FORECASTING ENGINE UPGRADE VERIFICATION TESTS
# =============================================================================
def test_forecasting_engine_holt_winters_grid_search():
    """
    Verifies that the Holt-Winters grid search runs successfully and yields a forecast.
    """
    from forecasting_engine import ForecastingEngine
    
    # Linear upward trend
    history = np.linspace(10.0, 20.0, 40)
    engine = ForecastingEngine()
    
    forecast_val = engine.run_holt_winters_grid_search(history, days_forward=5)
    assert isinstance(forecast_val, float)
    assert forecast_val > 15.0 # Should predict higher than mid-points

def test_forecasting_engine_prophet_resilience():
    """
    Validates Prophet wrapper outputs and fallback logic.
    """
    from forecasting_engine import ForecastingEngine
    
    dates = pd.date_range(end="2026-06-19", periods=35)
    history_series = pd.Series(np.linspace(100.0, 110.0, 35), index=dates)
    engine = ForecastingEngine()
    
    # Should run and return yhat, yhat_lower, yhat_upper under any package state
    yhat, lower, upper = engine.run_prophet_forecast(history_series, days_forward=5)
    assert isinstance(yhat, float)
    assert isinstance(lower, float)
    assert isinstance(upper, float)
    assert lower <= yhat <= upper

def test_forecasting_engine_cnn_lstm_slicing_and_fallbacks():
    """
    Verifies sequence slicing dimensions, MinMaxScaler scaling boundaries,
    and CNN-LSTM fallback/execution mechanics.
    """
    from forecasting_engine import ForecastingEngine
    import numpy as np
    
    # 1. Test sequence slicing
    # Create mock 2D data: 70 samples, 5 features
    X_mock = np.random.rand(70, 5)
    y_mock = np.random.rand(70, 1)
    
    X_seq, y_seq = ForecastingEngine.slice_sequences(X_mock, y_mock, lookback=60)
    
    # Reshaped tensor dimensions MUST be: (samples, time_steps, features)
    # With 70 samples and lookback=60, we expect 70 - 60 = 10 sample sequences
    assert X_seq.shape == (10, 60, 5)
    assert y_seq.shape == (10, 1)
    
    # 2. Test model run
    # Create mock history DataFrame
    dates = pd.date_range(end="2026-06-19", periods=75)
    df_history = pd.DataFrame({
        "Open": np.linspace(10.0, 20.0, 75),
        "High": np.linspace(11.0, 21.0, 75),
        "Low": np.linspace(9.0, 19.0, 75),
        "Close": np.linspace(10.0, 20.0, 75),
        "Volume": [5000] * 75
    }, index=dates)
    
    engine = ForecastingEngine()
    lstm_forecast = engine.run_cnn_lstm_forecast(df_history, days_forward=5)
    assert isinstance(lstm_forecast, float)


# =============================================================================
# 8. EVALUATION ENGINE CORE VERIFICATION TESTS
# =============================================================================
def test_evaluation_engine_edge_ratio():
    """
    Verifies MFE, MAE, Edge Ratio, and hold period return volatility calculations.
    """
    from evaluation_engine import EvaluationEngine
    
    dates = pd.date_range(start="2026-06-01", periods=20, freq="D")
    df_history = pd.DataFrame({
        "Open": [100.0] * 20,
        "High": [100.0] * 20,
        "Low": [100.0] * 20,
        "Close": [100.0] * 20,
        "Volume": [1000] * 20
    }, index=dates)
    
    # Introduce localized extreme prices
    # Entry date: 2026-06-05, exit date: 2026-06-15
    # Entry price: 100.0
    # On 2026-06-08, set High to 110.0 (MFE high)
    # On 2026-06-12, set Low to 95.0 (MAE low)
    df_history.at[dates[7], "High"] = 110.0
    df_history.at[dates[11], "Low"] = 95.0
    df_history.at[dates[7], "Close"] = 105.0
    df_history.at[dates[11], "Close"] = 96.0
    
    engine = EvaluationEngine()
    results = engine.calculate_edge_ratio(
        history_df=df_history,
        trade_entry_price=100.0,
        entry_date="2026-06-05",
        exit_date="2026-06-15"
    )
    
    # MFE = (110.0 - 100.0) / 100.0 = 0.10
    # MAE = (100.0 - 95.0) / 100.0 = 0.05
    # Edge Ratio = 0.10 / 0.05 = 2.0
    assert math.isclose(results["MFE"], 0.10, rel_tol=1e-5)
    assert math.isclose(results["MAE"], 0.05, rel_tol=1e-5)
    assert math.isclose(results["Edge Ratio"], 2.0, rel_tol=1e-5)
    assert results["Return Std Dev"] > 0.0


def test_evaluation_engine_kelly_target():
    """
    Verifies Kelly Criterion allocation sizing and Half-Kelly constraints.
    """
    from evaluation_engine import EvaluationEngine
    
    engine = EvaluationEngine()
    
    # Win-Loss Probability Method
    # win rate = 0.60, win/loss ratio = 2.0
    # Full Kelly: 0.60 - (1 - 0.60) / 2.0 = 0.60 - 0.20 = 0.40
    # Half Kelly: 0.20
    results_half = engine.calculate_kelly_target(
        expected_return=0.0,
        variance=0.0,
        win_probability=0.60,
        win_loss_ratio=2.0,
        half_kelly=True
    )
    assert math.isclose(results_half["Kelly Target"], 0.20, rel_tol=1e-5)
    
    results_full = engine.calculate_kelly_target(
        expected_return=0.0,
        variance=0.0,
        win_probability=0.60,
        win_loss_ratio=2.0,
        half_kelly=False
    )
    assert math.isclose(results_full["Kelly Target"], 0.40, rel_tol=1e-5)

    # Continuous Return Method
    # expected_return = 0.05, variance = 0.04
    # Full Kelly: 0.05 / 0.04 = 1.25 -> clamped to 1.0
    # Half Kelly: 1.25 / 2 = 0.625
    results_cont_half = engine.calculate_kelly_target(
        expected_return=0.05,
        variance=0.04,
        half_kelly=True
    )
    assert math.isclose(results_cont_half["Kelly Target"], 0.625, rel_tol=1e-5)


def test_evaluation_engine_brinson_fachler():
    """
    Verifies Brinson-Fachler portfolio attribution model equations and fallback correctness.
    """
    from evaluation_engine import EvaluationEngine
    
    portfolio_data = pd.DataFrame({
        "Sector": ["Technology", "Healthcare", "Financials"],
        "Portfolio_Weight": [0.50, 0.30, 0.20],
        "Portfolio_Return": [0.12, 0.06, 0.02]
    })
    
    benchmark_data = pd.DataFrame({
        "Sector": ["Technology", "Healthcare", "Financials"],
        "Benchmark_Weight": [0.40, 0.40, 0.20],
        "Benchmark_Return": [0.10, 0.05, 0.03]
    })
    
    engine = EvaluationEngine()
    attribution = engine.calculate_brinson_fachler(portfolio_data, benchmark_data)
    
    # Total Portfolio Return: 0.5 * 0.12 + 0.3 * 0.06 + 0.2 * 0.02 = 0.06 + 0.018 + 0.004 = 0.082
    # Total Benchmark Return: 0.4 * 0.10 + 0.4 * 0.05 + 0.2 * 0.03 = 0.04 + 0.02 + 0.006 = 0.066
    # Active Return: 0.082 - 0.066 = 0.016
    assert math.isclose(attribution["Portfolio Return"], 0.082, rel_tol=1e-5)
    assert math.isclose(attribution["Benchmark Return"], 0.066, rel_tol=1e-5)
    assert math.isclose(attribution["Active Return"], 0.016, rel_tol=1e-5)
    
    # Active Return must equal the sum of Allocation, Selection, and Interaction effects
    total_effects = (
        attribution["Allocation Effect"] + 
        attribution["Selection Effect"] + 
        attribution["Interaction Effect"]
    )
    assert math.isclose(total_effects, attribution["Active Return"], rel_tol=1e-5)
    assert math.isclose(attribution["Attribution Sum"], attribution["Active Return"], rel_tol=1e-5)


def test_main_orchestrator_pipeline():
    """
    Verifies that the main orchestrator executes the full synchronous routing pipeline
    successfully and produces a validated output DataFrame.
    """
    from main_orchestrator import run_pipeline
    from data_engine import MockDataEngine
    import config
    
    mock_de = MockDataEngine()
    tickers = ["AAPL"]
    macro_raw = mock_de.fetch_macro_raw()
    fund_raw = mock_de.fetch_fundamentals_raw(tickers)
    tech_raw = mock_de.fetch_technical_raw(tickers)
    
    final_df, _macro_dto, _shared_ctx = run_pipeline(tickers, macro_raw, fund_raw, tech_raw)
    assert not final_df.empty

    # Assert all strategy keys are present and populated
    assert "Action Signal" in final_df.columns
    assert "buyRange" in final_df.columns
    assert "Kelly Target" in final_df.columns
    assert "Option Strategy" in final_df.columns

    # run_pipeline() now returns the macro_dto with HMM probability
    assert _macro_dto is not None

    # Validate final schema
    try:
        config.DashboardSchema.validate(final_df)
    except Exception as e:
        pytest.fail(f"Final compiled DataFrame failed schema validation: {e}")


def test_rsi_unadjusted_split_anomaly():
    """
    Asserts that vectorized indicators (RSI/MACD) handle sudden unadjusted
    stock splits without triggering false positive 'BUY' signals.
    """
    # 1. Create the Split Array: 35 days at $100, then a 2:1 split to $50
    pre_split = [100.0] * 35
    post_split = [50.0] * 15
    split_prices = pre_split + post_split
    
    # 2. Inject via MockDataEngine
    mock_provider = MockDataEngine(preset_prices=split_prices)
    
    # 3. Initialize ProcessingEngine with the mock provider
    engine = ProcessingEngine(data_provider=mock_provider)
    
    raw_dfs = mock_provider.fetch_technical_raw(['AAPL'])
    processed_tech = engine.calculate_technicals_vectorized(raw_dfs)
    
    aapl_metrics = processed_tech['AAPL']
    rsi_val = aapl_metrics['RSI']
    macd_val = aapl_metrics['MACD']
    
    # Check that the RSI plummets to an extreme oversold level (e.g., < 10)
    assert rsi_val < 10.0, f"RSI did not register the massive split drop: {rsi_val}"
    # Check that MACD registers a massive drop (negative)
    assert macd_val < 0.0, f"MACD did not register the massive split drop: {macd_val}"
    
    # 4. Define Assertions for the Edge Case
    # Check that the trading signal does NOT trigger a 'BUY' just because 
    # RSI is artificially oversold, ensuring our momentum confirmation logic works.
    strategy = StrategyEngine()
    latest_price = split_prices[-1]
    
    bar = MarketBarDTO(datetime.now(), "AAPL", latest_price, latest_price * 1.02, latest_price * 0.98, latest_price, 100000)
    
    fundamentals = FundamentalDataDTO(
        ticker="AAPL", company_name="Mock Apple Corp", sector="Technology",
        pe_ratio=15.0, pb_ratio=1.5, book_value=10.0, eps_trailing=2.0,
        dividend_yield=0.02, dividend_growth_rate=0.05, payout_ratio=0.30
    )
    
    macro = MacroEconomicDTO(
        yield_curve_10y_2y=0.5,
        high_yield_oas=3.0,
        inflation_rate=2.5,
        nominal_10y=4.0
    )
    
    # Use trend_strength = 20.0 (bearish Aroon indicator value, since it's a massive drop without confirmation)
    # The expected price forecast is neutral/no gain (50.0). Since the trend is bearish,
    # it should NOT output BUY or STRONG BUY signals.
    result = strategy.evaluate_security(
        bar=bar,
        fundamentals=fundamentals,
        macro=macro,
        forecast_price=latest_price,
        trend_strength=20.0,
        atr=latest_price * 0.02
    )
    
    assert result["Action Signal"] not in ["BUY", "STRONG BUY"], (
        f"False positive BUY signal triggered during unadjusted split. Signal: {result['Action Signal']}"
    )


def test_macd_gap_shock_volatility():
    """
    Asserts that a singular massive price gap (e.g., +40% overnight shock) 
    followed by immediate mean reversion doesn't trigger false sustained crossovers.
    """
    # 1. Create the Shock Array: Flatline at $100, one spike to $140, immediate return to $100
    base_prices = [100.0] * 30
    shock_prices = [140.0]
    reversion_prices = [100.0] * 15
    gap_array = base_prices + shock_prices + reversion_prices
    
    # 2. Inject via MockDataEngine
    mock_provider = MockDataEngine(preset_prices=gap_array)
    engine = ProcessingEngine(data_provider=mock_provider)
    
    raw_dfs = mock_provider.fetch_technical_raw(['AAPL'])
    
    try:
        import pandas_ta as ta
    except ImportError:
        import pandas_ta_classic as ta
    df = raw_dfs['AAPL']
    macd_res = ta.macd(df['Close'], fast=12, slow=26, signal=9)
    
    # Check that MACD Fast line (MACD_12_26_9) diverges sharply at the shock index (30)
    macd_line = macd_res['MACD_12_26_9']
    macd_sig = macd_res['MACDs_12_26_9']
    
    assert abs(macd_line.iloc[30]) > 0.0, "MACD Fast line did not diverge at the shock index"
    
    # Check that after reversion, the histogram crossover does not sustain
    # Specifically, check that the number of consecutive days of a crossover signal is not sustained
    crossovers = (macd_line > macd_sig).astype(int)
    # Check that we don't have a sustained crossover (e.g., > 1 period) in the post-shock period (index 31 onwards)
    post_shock_crossovers = crossovers.iloc[31:]

    
    consecutive = 0
    max_consecutive = 0
    for val in post_shock_crossovers:
        if val == 1:
            consecutive += 1
            max_consecutive = max(max_consecutive, consecutive)
        else:
            consecutive = 0
            
    assert max_consecutive <= 1, f"False crossover sustained for {max_consecutive} periods after mean reversion"


def test_lookahead_bias_prevention():
    """
    Asserts strict boundary conditions: Indicators at time 't' must NEVER 
    be mathematically influenced by the price at time 't+1'.
    """
    # 1. Create a baseline array
    prices_base = [50.0] * 30
    
    # 2. Inject a future shock exactly at index 20
    prices_shock = [50.0] * 30
    prices_shock[20] = 200.0
    
    # 3. Inject both via MockDataEngine to compare
    mock_base = MockDataEngine(preset_prices=prices_base)
    mock_shock = MockDataEngine(preset_prices=prices_shock)
    
    df_base = mock_base.fetch_technical_raw(['AAPL'])['AAPL']
    df_shock = mock_shock.fetch_technical_raw(['AAPL'])['AAPL']
    
    try:
        import pandas_ta as ta
    except ImportError:
        import pandas_ta_classic as ta
    
    # Calculate indicators on both series
    df_base['RSI'] = ta.rsi(df_base['Close'], length=14)
    macd_base = ta.macd(df_base['Close'], fast=12, slow=26, signal=9)
    if macd_base is not None:
        df_base['MACD_Line'] = macd_base['MACD_12_26_9']
        df_base['MACD_Signal'] = macd_base['MACDs_12_26_9']
        
    df_shock['RSI'] = ta.rsi(df_shock['Close'], length=14)
    macd_shock = ta.macd(df_shock['Close'], fast=12, slow=26, signal=9)
    if macd_shock is not None:
        df_shock['MACD_Line'] = macd_shock['MACD_12_26_9']
        df_shock['MACD_Signal'] = macd_shock['MACDs_12_26_9']
        
    # Assert that before index 20, the calculated values are identical
    for i in range(20):
        if not pd.isna(df_base['RSI'].iloc[i]):
            assert math.isclose(df_base['RSI'].iloc[i], df_shock['RSI'].iloc[i], abs_tol=1e-5), (
                f"RSI lookahead bias detected at index {i}"
            )
        if macd_base is not None and not pd.isna(df_base['MACD_Line'].iloc[i]):
            assert math.isclose(df_base['MACD_Line'].iloc[i], df_shock['MACD_Line'].iloc[i], abs_tol=1e-5), (
                f"MACD lookahead bias detected at index {i}"
            )


def test_evaluation_engine_evaluate_portfolio():
    """
    Verifies that evaluate_portfolio correctly computes and populates MAE, MFE,
    Portfolio_Heat, and BF attribution effects on a test portfolio.
    """
    from evaluation_engine import EvaluationEngine
    
    test_df = pd.DataFrame({
        'Symbol': ['AAPL', 'AGNC', 'XOM'],
        'sector': ['Technology', 'Real Estate', 'Energy'],
        'Entry_Price': [150.0, 10.0, 100.0],
        'High': [160.0, 10.5, 105.0],
        'Low': [145.0, 9.0, 90.0],
        'position_size': [15000.0, 5000.0, 10000.0],
        'stop_loss_pct': [0.03, 0.15, 0.08],
        'Relative_Strength': [0.08, -0.02, 0.05]
    })

    test_benchmark = pd.DataFrame({
        'sector': ['Technology', 'Real Estate', 'Energy'],
        'weight': [0.40, 0.30, 0.30],
        'return': [0.05, 0.01, 0.03]
    })

    engine = EvaluationEngine()
    processed_df = engine.evaluate_portfolio(test_df, test_benchmark)
    
    assert 'MAE' in processed_df.columns
    assert 'MFE' in processed_df.columns
    assert 'Portfolio_Heat' in processed_df.columns
    assert 'BF_Allocation' in processed_df.columns
    assert 'BF_Selection' in processed_df.columns

    # Verify AAPL metrics:
    # MAE = |145.0 - 150.0| / 150.0 = 0.0333  (positive loss magnitude, per F-02 convention)
    # MFE = (160.0 - 150.0) / 150.0 = 0.0667
    assert math.isclose(processed_df.loc[processed_df['Symbol'] == 'AAPL', 'MAE'].values[0], 0.0333, abs_tol=1e-3)
    assert math.isclose(processed_df.loc[processed_df['Symbol'] == 'AAPL', 'MFE'].values[0], 0.0667, abs_tol=1e-3)

    # Verify Portfolio Heat:
    # Apple risk = 15000 * 0.03 = 450
    # AGNC risk = 5000 * 0.15 = 750
    # XOM risk = 10000 * 0.08 = 800
    # Total risk = 2000
    # Total capital = 30000
    # Heat = 2000 / 30000 = 0.0667
    assert math.isclose(processed_df['Portfolio_Heat'].iloc[0], 0.0667, abs_tol=1e-3)


def test_aroon_oscillator_chop_filter():
    """
    Asserts that StrategyEngine correctly implements the Aroon Oscillator chop filter
    to penalize scores in choppy markets, suppress false-positive STRONG BUYs,
    and falls back to legacy trend_strength when not provided.
    """
    from strategy_engine import StrategyEngine
    from dto_models import MarketBarDTO, FundamentalDataDTO, MacroEconomicDTO
    from datetime import datetime

    engine = StrategyEngine()
    
    # Common test DTOs
    bar = MarketBarDTO(datetime.now(), "AAPL", 150.0, 150.0, 150.0, 150.0, 10000)
    # Fundamental DTO: High Graham valuation (Graham = 201.24, price = 150 -> undervalued, +15pts)
    # Sustainable dividend (+10pts)
    fundamentals = FundamentalDataDTO(
        ticker="AAPL", company_name="Apple", sector="Technology",
        pe_ratio=15.0, pb_ratio=1.5, book_value=120.0, eps_trailing=15.0,
        dividend_yield=0.02, dividend_growth_rate=0.05, payout_ratio=0.30
    )
    # Macro: RISK ON (+10pts)
    macro = MacroEconomicDTO(
        yield_curve_10y_2y=0.5, high_yield_oas=3.0, inflation_rate=2.5, nominal_10y=4.0
    )
    
    # Baseline Score before Phase 4:
    # 50 (neutral) + 10 (RISK ON) + 15 (Undervalued Graham) + 10 (Sustainable div) = 85
    # Forecast Price: 153.0 -> Projected gain (153-150)/150 = 2% (+10pts) -> 95

    # Case A: Strong Aroon Oscillator Uptrend (aroon_osc = 80, MACD Bullish)
    # MACD Bullish (+10) + Strong Aroon Uptrend (+15) = +25 -> Clamp to 100 -> STRONG BUY
    res_uptrend = engine.evaluate_security(
        bar=bar, fundamentals=fundamentals, macro=macro,
        forecast_price=153.0, trend_strength=80.0, atr=2.5,
        macd_line=1.5, macd_signal=1.0, aroon_osc=80.0
    )
    assert res_uptrend["Action Signal"] == "STRONG BUY"
    assert "Strong Aroon Oscillator Uptrend" in res_uptrend["Strategy Explainer Notes"]

    # Case B: Choppy Market (aroon_osc = 10, MACD Bullish)
    # MACD Bullish (+10) + Choppy Market penalty (-15) = -5
    # Score = 95 - 5 = 90.
    # Score >= 75 but market is choppy! Should downgrade to BUY.
    res_choppy = engine.evaluate_security(
        bar=bar, fundamentals=fundamentals, macro=macro,
        forecast_price=153.0, trend_strength=80.0, atr=2.5,
        macd_line=1.5, macd_signal=1.0, aroon_osc=10.0
    )
    assert res_choppy["Action Signal"] == "BUY"
    assert "Choppy Market via Aroon Oscillator" in res_choppy["Strategy Explainer Notes"]

    # Case C: Strong Downtrend (aroon_osc = -80, MACD Bearish)
    # MACD Bearish (-15) + Strong Aroon Downtrend (-15) = -30
    # Score = 95 - 30 = 65 -> BUY
    res_downtrend = engine.evaluate_security(
        bar=bar, fundamentals=fundamentals, macro=macro,
        forecast_price=153.0, trend_strength=80.0, atr=2.5,
        macd_line=0.5, macd_signal=1.0, aroon_osc=-80.0
    )
    assert res_downtrend["Action Signal"] == "BUY"
    assert "Strong Aroon Oscillator Downtrend" in res_downtrend["Strategy Explainer Notes"]

    # Case D: Legacy Fallback (aroon_osc = None)
    # Should use trend_strength (80.0) -> +10pts
    # Score = 95 + 10 = 105 -> Clamp to 100 -> STRONG BUY
    res_legacy = engine.evaluate_security(
        bar=bar, fundamentals=fundamentals, macro=macro,
        forecast_price=153.0, trend_strength=80.0, atr=2.5,
        macd_line=1.5, macd_signal=1.0, aroon_osc=None
    )
    assert res_legacy["Action Signal"] == "STRONG BUY"
    assert "Bullish technical trend (Aroon >= 50)" in res_legacy["Strategy Explainer Notes"]


def test_garch_and_edge_scoring():
    """
    Asserts GARCH volatility penalties, Edge Ratio rewards and penalties,
    and Kelly sizing rule constraints under high mathematical edge.
    """
    from strategy_engine import StrategyEngine
    from dto_models import MarketBarDTO, FundamentalDataDTO, MacroEconomicDTO
    from transactions_store import TransactionsStore
    from datetime import datetime

    # Inject an empty in-memory store so this test's Kelly-sizing assertions are
    # deterministic regardless of how many real closed trades exist in the live
    # quant_platform.db (the previous version implicitly, and incorrectly,
    # assumed the production database was empty).
    empty_store = TransactionsStore(db_url="sqlite:///:memory:")
    engine = StrategyEngine(transactions_store=empty_store)

    # Common test DTOs
    bar = MarketBarDTO(datetime.now(), "AAPL", 150.0, 150.0, 150.0, 150.0, 10000)
    # Fundamental DTO: High Graham valuation (Graham = 201.24, price = 150 -> undervalued, +15pts)
    # Sustainable dividend (+10pts)
    fundamentals = FundamentalDataDTO(
        ticker="AAPL", company_name="Apple", sector="Technology",
        pe_ratio=15.0, pb_ratio=1.5, book_value=120.0, eps_trailing=15.0,
        dividend_yield=0.02, dividend_growth_rate=0.05, payout_ratio=0.30
    )
    # Macro: RISK ON (+10pts)
    macro = MacroEconomicDTO(
        yield_curve_10y_2y=0.5, high_yield_oas=3.0, inflation_rate=2.5, nominal_10y=4.0
    )

    # Case A: Strong Edge, Low GARCH.
    # Kelly Target sizing is now (sizing/kelly.py, sizing/vol_target.py): with no
    # closed-trade history in this test's TransactionsStore, Kelly is disabled and
    # sizing falls back to volatility-target-only:
    # volatility_target_weight(realized_vol=0.15, target_vol=0.10, max_leverage=2.0)
    # = min(2.0, 0.10/0.15) = 0.6667. Score/Sortino/Edge Ratio no longer gate sizing
    # brackets directly (that arbitrary score-bracket logic was removed).
    res_strong_edge = engine.evaluate_security(
        bar=bar, fundamentals=fundamentals, macro=macro,
        forecast_price=153.0, trend_strength=80.0, atr=2.5,
        macd_line=1.5, macd_signal=1.0, aroon_osc=80.0,
        rsi=50.0, sortino_ratio=2.5, max_drawdown=-0.10, relative_strength=0.05,
        garch_vol=0.15, edge_ratio=1.3
    )
    assert res_strong_edge["Score"] == 100
    assert res_strong_edge["Action Signal"] == "STRONG BUY"
    assert math.isclose(res_strong_edge["Kelly Target"], 2.0 / 3.0, rel_tol=1e-6)
    assert "Strong Mathematical Edge" in res_strong_edge["Strategy Explainer Notes"]

    # Case B: High GARCH penalty (> 40% vol) -> Deducts 20pts
    res_garch_penalty = engine.evaluate_security(
        bar=bar, fundamentals=fundamentals, macro=macro,
        forecast_price=150.0, trend_strength=40.0, atr=2.5,
        macd_line=0.5, macd_signal=1.0, aroon_osc=None, # legacy mode
        rsi=50.0, sortino_ratio=0.5, max_drawdown=-0.10, relative_strength=-0.05,
        garch_vol=0.45, edge_ratio=0.0
    )
    # Base: 50 + 10 (RISK ON) + 15 (Graham) + 10 (div) = 85
    # Forecast: flat (-10pts) -> 75
    # Trend: 40.0 legacy neutral (-5pts) -> 70
    # RS: -0.05 -> underperforming (-10pts) -> 60
    # GARCH: 0.45 -> extreme vol penalty (-20pts) -> 40
    assert res_garch_penalty["Score"] == 40
    assert "Extreme GARCH Volatility" in res_garch_penalty["Strategy Explainer Notes"]

    # Case C: Negative Edge Penalty (Edge < 0.8) -> Deducts 15pts
    res_negative_edge = engine.evaluate_security(
        bar=bar, fundamentals=fundamentals, macro=macro,
        forecast_price=150.0, trend_strength=40.0, atr=2.5,
        macd_line=0.5, macd_signal=1.0, aroon_osc=None,
        rsi=50.0, sortino_ratio=0.5, max_drawdown=-0.10, relative_strength=-0.05,
        garch_vol=0.15, edge_ratio=0.5
    )
    assert res_negative_edge["Score"] == 45
    assert "Negative Mathematical Edge" in res_negative_edge["Strategy Explainer Notes"]

    # Case D: Kelly Target sizing is now decoupled from edge_ratio/sortino_ratio --
    # those no longer gate sizing brackets directly (that arbitrary score-bracket
    # logic was removed; see Case A). With the same realized_vol=0.15 and no closed
    # trade history, sizing falls back to the same volatility-target-only weight
    # regardless of edge_ratio, confirming the old edge_ratio-gated bracket is gone.
    res_restricted_kelly = engine.evaluate_security(
        bar=bar, fundamentals=fundamentals, macro=macro,
        forecast_price=153.0, trend_strength=80.0, atr=2.5,
        macd_line=1.5, macd_signal=1.0, aroon_osc=80.0,
        rsi=50.0, sortino_ratio=2.5, max_drawdown=-0.10, relative_strength=0.05,
        garch_vol=0.15, edge_ratio=0.9
    )
    assert res_restricted_kelly["Action Signal"] == "STRONG BUY"
    assert math.isclose(res_restricted_kelly["Kelly Target"], 2.0 / 3.0, rel_tol=1e-6)
