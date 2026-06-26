"""
Forecast Skill Tracker (Tier 2.2)
===================================
SQLite-backed tracker that records per-model forecast prices, matches them
with actual realized prices once their horizon elapses, and computes
normalized inverse-RMSE weights for ensemble blending.

Design goals
------------
* **Dead-letter resilient** (CONSTRAINT #6): every public method wraps its body
  in try/except so a DB failure never crashes the forecast pipeline.
* **No fabricated data** (CONSTRAINT #4): ``get_skill_weights()`` returns an
  empty dict when there is no history; callers interpret that as "use equal
  weights" rather than receiving fabricated skill estimates.
* **Backward-compatible cold start**: fewer than ``min_obs`` completed rows per
  model → equal weights for all models present in the window. The blending
  formula in ``ForecastingEngine`` degrades smoothly to the prior hardcoded
  static weights when no tracker is wired.

Database table: ``forecast_errors``
-------------------------------------
+----------------+------------+--------------------------------------------------+
| Column         | Type       | Notes                                            |
+----------------+------------+--------------------------------------------------+
| id             | INTEGER PK | Auto-increment.                                  |
| symbol         | TEXT       | Ticker (e.g. "AAPL").                           |
| model_name     | TEXT       | One of: arima, monte_carlo, holt_winters,        |
|                |            | cnn_lstm.                                        |
| horizon_days   | INTEGER    | Forecast horizon (e.g. 10, 30, 60, 90).          |
| forecast_ts    | TEXT       | UTC ISO-8601 when the forecast was made.         |
| forecast_price | REAL       | Predicted terminal price.                        |
| actual_price   | REAL       | NULL until the horizon elapses.                  |
| squared_error  | REAL       | (actual_price - forecast_price)^2; NULL while    |
|                |            | actual_price is still NULL.                      |
| recorded_at    | TEXT       | UTC ISO-8601 when the row was inserted.          |
+----------------+------------+--------------------------------------------------+
"""

import logging
import math
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Canonical model name constants used throughout the codebase.
MODEL_ARIMA = "arima"
MODEL_MONTE_CARLO = "monte_carlo"
MODEL_HOLT_WINTERS = "holt_winters"
MODEL_CNN_LSTM = "cnn_lstm"
ALL_MODEL_NAMES = (MODEL_ARIMA, MODEL_MONTE_CARLO, MODEL_HOLT_WINTERS, MODEL_CNN_LSTM)

# Minimum positive RMSE to prevent division-by-zero when a model is extremely
# accurate over a stretch (a $0.01 RMSE cap avoids assigning infinite weight).
_MIN_RMSE = 0.01


class ForecastTracker:
    """Per-model RMSE-based forecast skill tracker backed by SQLite.

    Typical lifecycle per ``ForecastingEngine.generate_forecast()`` call
    -------------------------------------------------------------------
    1. ``update_actuals(symbol, horizon, current_price, now)`` — fills in
       ``actual_price`` for any past forecasts whose horizon has elapsed.
    2. ``get_skill_weights(symbol, horizon)`` — returns normalized inverse-RMSE
       weights for the models seen in the rolling window.
    3. Blend model outputs using those weights.
    4. ``record_forecasts(symbol, horizon, {model: price, …}, now)`` — stores
       the new forecasts for future validation.

    Parameters
    ----------
    db_path : str
        Path to the SQLite database file (default ``"quant_platform.db"``).
    """

    _TABLE_DDL = """
    CREATE TABLE IF NOT EXISTS forecast_errors (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol         TEXT    NOT NULL,
        model_name     TEXT    NOT NULL,
        horizon_days   INTEGER NOT NULL,
        forecast_ts    TEXT    NOT NULL,
        forecast_price REAL    NOT NULL,
        actual_price   REAL,
        squared_error  REAL,
        recorded_at    TEXT    NOT NULL
    )
    """

    _INDEX_DDL = """
    CREATE INDEX IF NOT EXISTS idx_fe_symbol_model_horizon
        ON forecast_errors (symbol, model_name, horizon_days, forecast_ts)
    """

    def __init__(self, db_path: str = "quant_platform.db") -> None:
        self._db_path = db_path
        self._ensure_table()

    # -------------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")  # concurrent read-write safe
        return conn

    def _ensure_table(self) -> None:
        """Create the forecast_errors table and index if they don't exist."""
        try:
            with self._connect() as conn:
                conn.execute(self._TABLE_DDL)
                conn.execute(self._INDEX_DDL)
                conn.commit()
        except Exception as exc:  # pragma: no cover
            logger.warning("ForecastTracker._ensure_table failed: %s", exc)

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------
    def record_forecasts(
        self,
        symbol: str,
        horizon_days: int,
        model_prices: Dict[str, float],
        forecast_ts: datetime,
    ) -> None:
        """Insert per-model forecast prices for future validation.

        Skips models with a price of 0.0 or below (model did not produce output).
        A per-call try/except ensures a DB failure never aborts the caller.

        Parameters
        ----------
        symbol : str
            Ticker symbol (e.g. ``"AAPL"``).
        horizon_days : int
            Forecast horizon in calendar days (e.g. 30).
        model_prices : dict[str, float]
            Mapping of model name → predicted terminal price.
        forecast_ts : datetime
            UTC timestamp when the forecast was computed.
        """
        try:
            now_iso = datetime.utcnow().isoformat()
            ts_iso = forecast_ts.isoformat() if isinstance(forecast_ts, datetime) else str(forecast_ts)
            rows = [
                (symbol.upper(), name, horizon_days, ts_iso, price, now_iso)
                for name, price in model_prices.items()
                if price and price > 0.0
            ]
            if not rows:
                return
            with self._connect() as conn:
                conn.executemany(
                    """INSERT INTO forecast_errors
                       (symbol, model_name, horizon_days, forecast_ts,
                        forecast_price, recorded_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    rows,
                )
                conn.commit()
        except Exception as exc:
            logger.warning("ForecastTracker.record_forecasts(%s, h=%d) failed: %s", symbol, horizon_days, exc)

    def update_actuals(
        self,
        symbol: str,
        horizon_days: int,
        actual_price: float,
        as_of: datetime,
        tolerance_days: int = 5,
    ) -> int:
        """Match past forecasts with actual realized prices.

        Finds all unactualized rows for ``symbol`` and ``horizon_days`` whose
        ``forecast_ts`` is at least ``horizon_days - tolerance_days`` days before
        ``as_of``, and writes ``actual_price`` + ``squared_error`` into them.

        The tolerance window (+5 days) absorbs weekends, holidays, and the fact
        that runs may be skipped — so a 30-day forecast made on Monday will still
        be actualized if we first run again on the following Thursday.

        Parameters
        ----------
        symbol : str
            Ticker symbol.
        horizon_days : int
            Forecast horizon to actualize.
        actual_price : float
            Current close price (the ground truth for past forecasts).
        as_of : datetime
            The UTC datetime of the current run.
        tolerance_days : int
            Grace window (days) to handle run-skipping and calendar gaps.

        Returns
        -------
        int
            Number of rows updated (0 when nothing was due).
        """
        try:
            # A forecast made on day T is "due" when: now >= T + horizon - tolerance
            # Equivalently: T <= now - horizon + tolerance
            cutoff_dt = as_of - timedelta(days=max(0, horizon_days - tolerance_days))
            cutoff_iso = cutoff_dt.isoformat()

            with self._connect() as conn:
                cursor = conn.execute(
                    """UPDATE forecast_errors
                       SET actual_price  = ?,
                           squared_error = (? - forecast_price) * (? - forecast_price)
                       WHERE symbol       = ?
                         AND horizon_days = ?
                         AND forecast_ts  <= ?
                         AND actual_price IS NULL""",
                    (
                        actual_price, actual_price, actual_price,
                        symbol.upper(), horizon_days, cutoff_iso,
                    ),
                )
                conn.commit()
                return cursor.rowcount
        except Exception as exc:
            logger.warning(
                "ForecastTracker.update_actuals(%s, h=%d) failed: %s", symbol, horizon_days, exc
            )
            return 0

    def get_skill_weights(
        self,
        symbol: str,
        horizon_days: int,
        window_days: int = 60,
        min_obs: int = 30,
    ) -> Dict[str, float]:
        """Return normalized inverse-RMSE weights for ensemble blending.

        Algorithm
        ---------
        1. Query completed (``actual_price IS NOT NULL``) rows in the rolling
           ``window_days`` window.
        2. Compute per-model ``n`` (count) and ``mse`` (mean squared error).
        3. **Cold-start**: if any model has ``n < min_obs``, return equal weights
           for all models seen in the window (symmetric treatment).
        4. **Warm path**: ``weight ∝ 1 / max(RMSE, _MIN_RMSE)`` — inverse-RMSE
           weighting normalized to sum to 1.0.

        Returns an empty dict ``{}`` when no completed rows exist in the window.
        Callers interpret ``{}`` as "use equal weights" or "fall back to hardcoded
        blending" — never fabricate skill from missing data.

        Parameters
        ----------
        symbol : str
            Ticker symbol.
        horizon_days : int
            Forecast horizon to query.
        window_days : int
            Rolling window size in calendar days (default 60).
        min_obs : int
            Minimum completed rows per model before skill weighting activates
            (default 30).

        Returns
        -------
        dict[str, float]
            ``{model_name: normalized_weight}``.  Empty when no history.
        """
        try:
            since_iso = (datetime.utcnow() - timedelta(days=window_days)).isoformat()
            with self._connect() as conn:
                cursor = conn.execute(
                    """SELECT model_name,
                              COUNT(*)           AS n,
                              AVG(squared_error) AS mse
                       FROM forecast_errors
                       WHERE symbol        = ?
                         AND horizon_days  = ?
                         AND actual_price  IS NOT NULL
                         AND forecast_ts   >= ?
                       GROUP BY model_name""",
                    (symbol.upper(), horizon_days, since_iso),
                )
                rows = cursor.fetchall()

            if not rows:
                return {}

            model_stats: Dict[str, tuple] = {
                r[0]: (int(r[1]), float(r[2]) if r[2] is not None else 0.0)
                for r in rows
            }

            # Cold-start: equal weights when any model has fewer than min_obs samples
            if any(n < min_obs for (n, _) in model_stats.values()):
                n_models = len(model_stats)
                return {name: 1.0 / n_models for name in model_stats}

            # Warm path: inverse-RMSE weighting
            inv_rmse: Dict[str, float] = {}
            for name, (_, mse) in model_stats.items():
                rmse = math.sqrt(mse) if mse >= 0 else 0.0
                inv_rmse[name] = 1.0 / max(rmse, _MIN_RMSE)

            total = sum(inv_rmse.values())
            if total <= 0:
                # Degenerate case: all RMSEs clamped to _MIN_RMSE → equal weights
                n_models = len(inv_rmse)
                return {name: 1.0 / n_models for name in inv_rmse}

            return {name: w / total for name, w in inv_rmse.items()}

        except Exception as exc:
            logger.warning(
                "ForecastTracker.get_skill_weights(%s, h=%d) failed: %s", symbol, horizon_days, exc
            )
            return {}

    def pending_count(self, symbol: str, horizon_days: int) -> int:
        """Return the number of un-actualized forecast rows for a symbol+horizon.

        Useful for monitoring how many forecasts are awaiting ground-truth prices.
        Returns 0 on any DB error.
        """
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    """SELECT COUNT(*) FROM forecast_errors
                       WHERE symbol       = ?
                         AND horizon_days = ?
                         AND actual_price IS NULL""",
                    (symbol.upper(), horizon_days),
                )
                row = cursor.fetchone()
                return int(row[0]) if row else 0
        except Exception as exc:
            logger.warning("ForecastTracker.pending_count(%s, h=%d) failed: %s", symbol, horizon_days, exc)
            return 0

    def completed_count(self, symbol: str, horizon_days: int, window_days: int = 60) -> int:
        """Return the number of actualized rows in the rolling window.

        Used by callers to decide whether cold-start equal weighting applies.
        Returns 0 on any DB error.
        """
        try:
            since_iso = (datetime.utcnow() - timedelta(days=window_days)).isoformat()
            with self._connect() as conn:
                cursor = conn.execute(
                    """SELECT COUNT(*) FROM forecast_errors
                       WHERE symbol       = ?
                         AND horizon_days = ?
                         AND actual_price IS NOT NULL
                         AND forecast_ts  >= ?""",
                    (symbol.upper(), horizon_days, since_iso),
                )
                row = cursor.fetchone()
                return int(row[0]) if row else 0
        except Exception as exc:
            logger.warning("ForecastTracker.completed_count(%s) failed: %s", symbol, exc)
            return 0
