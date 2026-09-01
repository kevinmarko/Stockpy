from typing import Dict, Any
import pandas as pd

def run_lstm_diagnostic(symbol: str, bars: pd.DataFrame, sector_bars: pd.DataFrame, asvi_sym: pd.Series, asvi_sec: pd.Series) -> Dict[str, Any]:
    from forecasting_engine import ForecastingEngine
    from processing_engine import ProcessingEngine
    
    pe = ProcessingEngine()
    bars = pe._calculate_technical_metrics_for_symbol(bars)
    
    if not sector_bars.empty:
        sector_bars = pe._calculate_technical_metrics_for_symbol(sector_bars)
        
    fe = ForecastingEngine()
    
    return fe.run_lstm_attention_forecast(
        symbol=symbol,
        df_ohlcv=bars,
        df_sector_ohlcv=sector_bars,
        df_asvi_symbol=asvi_sym,
        df_asvi_sector=asvi_sec
    )
