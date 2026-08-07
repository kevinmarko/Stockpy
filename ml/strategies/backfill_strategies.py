import pandas as pd
from typing import List
from .BaseStrategy import BaseStrategy

class TSMOMStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("TSMOM", "Time-Series Momentum")

    def generate_raw_signals(self, start_date: str, end_date: str) -> pd.DataFrame:
        # Stub implementation
        return pd.DataFrame()

    def get_supported_horizons(self) -> List[int]:
        return [10, 30, 60, 90]

class CSMOMStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("CSMOM", "Cross-Sectional Momentum")

    def generate_raw_signals(self, start_date: str, end_date: str) -> pd.DataFrame:
        # Stub implementation
        return pd.DataFrame()

    def get_supported_horizons(self) -> List[int]:
        return [10, 30, 60, 90]

class PairsRadarStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("PAIRS_RADAR", "Pairs Radar")

    def generate_raw_signals(self, start_date: str, end_date: str) -> pd.DataFrame:
        # Stub implementation
        return pd.DataFrame()

    def get_supported_horizons(self) -> List[int]:
        return [10, 30]

class OptionsStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("OPTIONS", "Options")

    def generate_raw_signals(self, start_date: str, end_date: str) -> pd.DataFrame:
        # Stub implementation
        return pd.DataFrame()

    def get_supported_horizons(self) -> List[int]:
        return [14, 30]

class SectorSelectionStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("SECTOR_SELECTION", "Sector Selection")

    def generate_raw_signals(self, start_date: str, end_date: str) -> pd.DataFrame:
        # Stub implementation
        return pd.DataFrame()

    def get_supported_horizons(self) -> List[int]:
        return [30, 90]
