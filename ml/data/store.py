"""
ml.data.store — Point-in-Time Feature Store
============================================
Caches daily cross-sectional feature matrices (as_of_date → DataFrame) so that
monthly model retraining can pull an expanding window without recomputing
everything from scratch.

The store uses Parquet files under ml/data/cache/ for efficient I/O. Each date
is a separate file: ``cache/features_<YYYYMMDD>.parquet``. The cache is append-
only; it is never modified retroactively (PIT guarantee).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger("ML.Data.Store")

_CACHE_DIR = Path(__file__).parent / "cache"


class PITFeatureStore:
    """Point-in-Time feature cache for incremental model retraining.

    Usage
    -----
    >>> store = PITFeatureStore()
    >>> store.write(as_of_date, feature_df)
    >>> panel = store.read_range("2020-01-01", "2024-12-31")
    """

    def __init__(self, cache_dir: Optional[Path] = None) -> None:
        self._dir = Path(cache_dir) if cache_dir else _CACHE_DIR
        self._dir.mkdir(parents=True, exist_ok=True)

    def write(self, as_of_date: pd.Timestamp, features: pd.DataFrame) -> Path:
        """Write a daily feature snapshot to the cache.

        Safe to call repeatedly for the same date: overwrites the existing file
        (idempotent, but see the PIT note — never alter a *past* date's cache).

        Parameters
        ----------
        as_of_date :
            The calendar date this snapshot represents.
        features :
            DataFrame indexed by ticker, columns = feature columns.

        Returns
        -------
        Path to the written Parquet file.
        """
        stamp = pd.Timestamp(as_of_date).strftime("%Y%m%d")
        path = self._dir / f"features_{stamp}.parquet"
        features.to_parquet(path, engine="pyarrow")
        logger.debug("PITFeatureStore: wrote %s (%d tickers)", stamp, len(features))
        return path

    def read_range(
        self,
        start: str,
        end: str,
    ) -> pd.DataFrame:
        """Load an expanding-window panel from the cache.

        Parameters
        ----------
        start, end :
            ISO date strings (e.g., ``"2020-01-01"``).

        Returns
        -------
        pd.DataFrame with (date, ticker) MultiIndex, or empty if no data found.
        """
        t_start = pd.Timestamp(start)
        t_end = pd.Timestamp(end)

        frames: list[pd.DataFrame] = []
        for parquet in sorted(self._dir.glob("features_*.parquet")):
            date_str = parquet.stem.split("_", 1)[1]
            try:
                dt = pd.Timestamp(date_str)
            except Exception:
                continue
            if t_start <= dt <= t_end:
                try:
                    df = pd.read_parquet(parquet, engine="pyarrow")
                    df["__date__"] = dt
                    frames.append(df)
                except Exception as exc:
                    logger.warning("PITFeatureStore: failed to read %s: %s", parquet, exc)

        if not frames:
            return pd.DataFrame()

        panel = pd.concat(frames)
        panel = panel.reset_index().set_index(["__date__", panel.index.name or "ticker"])
        panel.index.names = ["date", "ticker"]
        return panel

    def available_dates(self) -> list[pd.Timestamp]:
        """List all dates present in the cache, sorted ascending."""
        dates = []
        for p in sorted(self._dir.glob("features_*.parquet")):
            try:
                dates.append(pd.Timestamp(p.stem.split("_", 1)[1]))
            except Exception:
                continue
        return dates
