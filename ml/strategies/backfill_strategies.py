import pandas as pd
from typing import List
from .BaseStrategy import BaseStrategy

class TSMOMStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("TSMOM", "Time-Series Momentum")

    def generate_raw_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        import numpy as np
        df = df.copy()
        if "Return_252d" in df.columns:
            df["raw_signal"] = np.sign(df["Return_252d"]).replace(0, 1).fillna(0)
        else:
            df["raw_signal"] = 0
        return df

    def get_supported_horizons(self) -> List[int]:
        return [10, 30, 60, 90]

class CSMOMStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("CSMOM", "Cross-Sectional Momentum")

    def generate_raw_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        import numpy as np
        df = df.copy()
        if "Return_252d" in df.columns:
            ranks = df.groupby(level="Date")["Return_252d"].rank(pct=True)
            df["raw_signal"] = np.where(ranks > 0.5, 1, -1)
            df.loc[ranks.isna(), "raw_signal"] = 0
        else:
            df["raw_signal"] = 0
        return df

    def get_supported_horizons(self) -> List[int]:
        return [10, 30, 60, 90]

class PairsRadarStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("PAIRS_RADAR", "Pairs Radar")

    def generate_raw_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        import numpy as np
        df = df.copy()
        if "Return" in df.columns:
            mean = df.groupby(level="Ticker")["Return"].transform(lambda x: x.rolling(20).mean())
            std = df.groupby(level="Ticker")["Return"].transform(lambda x: x.rolling(20).std())
            z_score = (df["Return"] - mean) / std
            df["raw_signal"] = np.where(z_score > 2, -1, np.where(z_score < -2, 1, 0))
        else:
            df["raw_signal"] = 0
        return df

    def get_supported_horizons(self) -> List[int]:
        return [10, 30]

class OptionsStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("OPTIONS", "Options")

    def generate_raw_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        import numpy as np
        df = df.copy()
        if "Vol_20" in df.columns:
            # Proxies short vol regime logic
            df["raw_signal"] = np.where(df["Vol_20"] > 0.15, 1, -1)
        else:
            df["raw_signal"] = 0
        return df

    def get_supported_horizons(self) -> List[int]:
        return [14, 30]

class SectorSelectionStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("SECTOR_SELECTION", "Sector Selection")

    def generate_raw_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        import numpy as np
        df = df.copy()
        if "Return_252d" in df.columns and "RSI_14" in df.columns:
            score = df["Return_252d"] * (df["RSI_14"] - 50)
            ranks = score.groupby(level="Date").rank(pct=True)
            df["raw_signal"] = np.where(ranks > 0.8, 1, np.where(ranks < 0.2, -1, 0))
            df.loc[ranks.isna(), "raw_signal"] = 0
        else:
            df["raw_signal"] = 0
        return df

    def get_supported_horizons(self) -> List[int]:
        return [30, 90]
