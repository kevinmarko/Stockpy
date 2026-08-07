from abc import ABC, abstractmethod
import pandas as pd
from typing import Dict, Any, List

class BaseStrategy(ABC):
    """Abstract Base Class for all trainable/backtestable strategies."""
    
    def __init__(self, strategy_id: str, name: str):
        self.strategy_id = strategy_id
        self.name = name

    @abstractmethod
    def generate_raw_signals(self, start_date: str, end_date: str) -> pd.DataFrame:
        """
        Must return DataFrame with standard schema:
        ['timestamp', 'ticker', 'raw_signal', 'volatility', 'rsi', 'macd', 'volume_ratio']
        where raw_signal is in {-1, 0, 1}.
        """
        pass

    @abstractmethod
    def get_supported_horizons(self) -> List[int]:
        """Returns target horizon days, e.g., [10, 30, 60, 90]."""
        pass
