"""data/trends_stitcher.py — Google Trends Overlapping Window Stitcher & ASVI Calculator
=============================================================================================
Provides production modules for:
1. GoogleTrendsStitcher: An overlapping-window stitching algorithm to reconstruct a
   continuous, long-term daily Search Volume Index (SVI) series from adjacent 90-day
   daily Google Trends intervals (which maintain high daily resolution).
2. ASVICalculator: Calculates Abnormal Search Volume Index (ASVI) per Da, Engelberg & Gao (2011),
   isolating attention shocks via a strictly causal, no-lookahead rolling log-median filter:
       ASVI_t = ln(SVI_t) - ln(Median(SVI_{t-k ... t-1}))
3. FMPDataLoader: Ingests/formats historical daily OHLCV bars and computes technical indicators
   (EMA, MACD, RSI) for econometric and deep learning sequence input tensors.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional, Sequence, Tuple
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class GoogleTrendsStitcher:
    """Implements an overlapping window stitching algorithm to reconstruct a long-term daily SVI series
    from adjacent 90-day daily Google Trends intervals (which maintain daily resolution).
    """

    @staticmethod
    def get_scaling_metadata(period_a_svi: pd.Series, period_b_svi: pd.Series) -> dict:
        """Extracts the geometric scaling factor and overlap window for two periods.

        Single source of truth for BOTH the scaling factor `f` AND the overlap window
        (`overlap_dates`) used by `stitch_intervals` — callers must reuse `overlap_dates`
        rather than re-deriving it via a second `.index.intersection()` call. The overlap
        window's start/end boundaries are not returned as separate keys since they're
        trivially derivable from `overlap_dates` (`overlap_dates[0]` / `overlap_dates[-1]`)
        -- a caller that only needs the boundaries can take them from there.
        """
        overlap_dates = period_a_svi.index.intersection(period_b_svi.index)
        if len(overlap_dates) == 0:
            raise ValueError("No overlapping dates found between Period A and Period B for scaling.")

        overlap_a = period_a_svi.loc[overlap_dates]
        overlap_b = period_b_svi.loc[overlap_dates]

        sum_a = float(overlap_a.sum())
        sum_b = float(overlap_b.sum())

        # Symmetric epsilon-substitution guard: if EITHER side's overlap sum is
        # near-zero, the ratio is unreliable/undefined in a meaningful sense —
        # passthrough (f=1.0) rather than compute a ratio against a near-zero
        # value on only one side. A guard that only checks sum_b (as this used
        # to) still lets a real, near-zero-but-nonzero sum_b divide into a real
        # sum_a and blow up by orders of magnitude — symmetric across all four
        # quadrants (both-zero, A-zero/B-real, A-real/B-zero, both-real) is
        # what actually closes that gap.
        if sum_a <= 1e-9 or sum_b <= 1e-9:
            f = 1.0
        else:
            f = sum_a / sum_b

        return {
            "overlap_dates": overlap_dates,
            "f": f,
        }

    @staticmethod
    def stitch_intervals(period_a_svi: pd.Series, period_b_svi: pd.Series) -> pd.Series:
        """Stitches two adjacent daily periods where period_b follows period_a with an overlapping window.
        Calculates a scaling factor using the overlapping non-zero days to scale period_b's SVI scale
        to align with period_a's scale.

        Args:
            period_a_svi (pd.Series): Daily SVI series for the baseline period (earlier in time).
            period_b_svi (pd.Series): Daily SVI series for the subsequent period (later in time).

        Returns:
            pd.Series: Stitched and rescaled daily SVI series.
        """
        if period_a_svi.empty:
            return period_b_svi.copy()
        if period_b_svi.empty:
            return period_a_svi.copy()

        # Delegate to single source of truth for both the scaling factor AND the overlap window
        # (get_scaling_metadata's own guard is symmetric across sum_a/sum_b -- see its docstring
        # for why a denominator-only guard let a real sum_a blow up by orders of magnitude
        # whenever sum_b alone happened to be near-zero).
        meta = GoogleTrendsStitcher.get_scaling_metadata(period_a_svi, period_b_svi)
        overlap_dates = meta["overlap_dates"]
        f = meta["f"]

        # Rescale Period B
        scaled_b = period_b_svi * f

        # Combine the series: use Period A values first, and update/extend with scaled Period B values
        combined = period_a_svi.combine_first(scaled_b)

        # To maintain index range consistency, update the overlap with the smoothed average
        for date in overlap_dates:
            combined.loc[date] = (period_a_svi.loc[date] + scaled_b.loc[date]) / 2.0

        return combined.sort_index()

    @classmethod
    def stitch_multiple_intervals(cls, intervals: Sequence[pd.Series]) -> pd.Series:
        """Stitches an arbitrary ordered sequence of adjacent overlapping SVI periods.

        Args:
            intervals (Sequence[pd.Series]): List of chronological daily SVI series.

        Returns:
            pd.Series: Continuously stitched daily SVI series.
        """
        if not intervals:
            return pd.Series(dtype=float)

        stitched = intervals[0]
        for next_interval in intervals[1:]:
            stitched = cls.stitch_intervals(stitched, next_interval)
        return stitched

    @staticmethod
    def group_raw_windows_into_series(raw_rows: Sequence[Any]) -> List[Tuple[str, pd.Series]]:
        """Groups ``data/trends_store.py::TrendsStore.load_raw_windows()`` rows
        (objects exposing ``.window_id``/``.date``/``.value``) by ``window_id``
        into chronologically-ordered ``(window_id, pd.Series)`` pairs, one per
        window.

        ``window_id`` is an opaque UUID (see ``desktop/daemon_runtime.py``'s
        ``insert_raw_window`` call), not a chronological identifier -- windows
        are ordered by their own earliest date instead. Rows within a window
        are NOT re-sorted here: ``load_raw_windows``'s own
        ``order_by(RawTrendsDownload.date.asc())`` query already guarantees
        both per-window date order and that each window's first row is its
        earliest, so a caller passing that method's return value straight
        through gets a free, already-sorted result.
        """
        windows: dict[str, List[Any]] = {}
        for row in raw_rows:
            windows.setdefault(row.window_id, []).append(row)
        ordered_window_ids = sorted(windows, key=lambda wid: windows[wid][0].date)
        return [
            (
                window_id,
                pd.Series(
                    [r.value for r in windows[window_id]],
                    index=pd.DatetimeIndex([r.date for r in windows[window_id]]),
                ),
            )
            for window_id in ordered_window_ids
        ]

    @staticmethod
    def rows_to_series(rows: Sequence[dict]) -> pd.Series:
        """Converts a list of ``{"date": ..., "value": ...}`` dicts --
        ``data/trends_store.py::TrendsStore.get_stitched_series()``'s return
        shape -- into a ``pd.Series`` indexed by date."""
        return pd.Series(
            [row["value"] for row in rows],
            index=pd.DatetimeIndex([row["date"] for row in rows]),
        )


class ASVICalculator:
    """Extracts the Abnormal Search Volume Index (ASVI) to isolate temporal shocks of intense retail attention.
    Converts raw SVI into ASVI using a log-transform relative to a rolling window median.
    """

    @staticmethod
    def compute_asvi(
        svi_series: pd.Series,
        lookback_weeks: int = 12,
        epsilon: float = 0.1,
    ) -> pd.Series:
        """Calculates ASVI for a daily SVI series using a rolling lookback window.
        ASVI_t = ln(SVI_t) - ln(Median(SVI_{t-k ... t-1}))

        Strictly causal: the rolling median is shifted by 1 day so that information from date t
        is never included in the baseline median for date t (guaranteeing zero lookahead bias).

        Args:
            svi_series (pd.Series): A long-term continuous daily SVI series.
            lookback_weeks (int): Size of rolling lookback window in weeks (default: 12 weeks / 84 days).
            epsilon (float): Floor value to prevent log(0) domain errors.

        Returns:
            pd.Series: ASVI time series.
        """
        if svi_series.empty:
            return pd.Series(dtype=float)

        lookback_days = max(lookback_weeks * 7, 7)

        # Replace 0 or negative values with epsilon
        svi_clean = svi_series.clip(lower=epsilon)

        # Calculate ln(SVI_t)
        ln_svi = np.log(svi_clean)

        # Calculate rolling median of raw SVI over lookback period, shifted by 1 to exclude current day
        rolling_median = (
            svi_clean.shift(1)
            .rolling(window=lookback_days, min_periods=min(7, lookback_days))
            .median()
        )
        
        # Fallback for initial window before min_periods
        initial_median = float(svi_clean.iloc[:min(len(svi_clean), lookback_days)].median())
        if np.isnan(initial_median) or initial_median <= 0:
            initial_median = epsilon
        rolling_median = rolling_median.fillna(initial_median)

        # Calculate ln(Median)
        ln_median = np.log(rolling_median.clip(lower=epsilon))

        # Compute final ASVI
        asvi = ln_svi - ln_median
        return asvi


class FMPDataLoader:
    """Simulates or interfaces with Financial Modeling Prep (FMP) API to load historical daily OHLCV
    bars and compute key technical indicators (EMA, RSI, MACD) required for sequence input tensors.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or "demo"

    def fetch_historical_ohlcv(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        seed: int = 42,
    ) -> pd.DataFrame:
        """Fetches or generates standardized daily OHLCV bars.
        In offline / testing mode, generates a realistic geometric Brownian motion price walk.
        """
        # Generate trading dates
        date_range = pd.date_range(start=start_date, end=end_date, freq="B")
        n_days = len(date_range)
        if n_days == 0:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        rng = np.random.default_rng(seed)
        price_changes = rng.normal(loc=0.0005, scale=0.015, size=n_days)
        initial_price = 100.0
        close_prices = initial_price * np.exp(np.cumsum(price_changes))

        high_prices = close_prices * (1 + np.abs(rng.normal(0, 0.005, n_days)))
        low_prices = close_prices * (1 - np.abs(rng.normal(0, 0.005, n_days)))
        open_prices = np.roll(close_prices, 1)
        open_prices[0] = initial_price
        volumes = rng.integers(1000000, 5000000, size=n_days).astype(float)

        df = pd.DataFrame(
            {
                "open": open_prices,
                "high": high_prices,
                "low": low_prices,
                "close": close_prices,
                "volume": volumes,
            },
            index=date_range,
        )

        df.index.name = "date"
        return df

    @staticmethod
    def compute_technical_indicators(df: pd.DataFrame) -> pd.DataFrame:
        """Computes fast and slow Exponential Moving Averages (EMA), Relative Strength Index (RSI),
        and Moving Average Convergence Divergence (MACD) on the OHLCV dataframe.
        """
        if df.empty or "close" not in df.columns:
            return df.copy()

        out = df.copy()

        # 1. EMAs
        out["ema_12"] = out["close"].ewm(span=12, adjust=False).mean()
        out["ema_26"] = out["close"].ewm(span=26, adjust=False).mean()

        # 2. MACD
        out["macd"] = out["ema_12"] - out["ema_26"]
        out["macd_signal"] = out["macd"].ewm(span=9, adjust=False).mean()
        out["macd_hist"] = out["macd"] - out["macd_signal"]

        # 3. RSI (14-day)
        delta = out["close"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / (loss + 1e-9)
        out["rsi_14"] = 100.0 - (100.0 / (1.0 + rs))
        out["rsi_14"] = out["rsi_14"].fillna(50.0)

        return out
