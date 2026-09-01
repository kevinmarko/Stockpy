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
|                |            | cnn_lstm, prophet, lstm_baseline,                |
|                |            | lstm_attention, bert_lla.                        |
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
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

# Canonical model name constants used throughout the codebase.
MODEL_ARIMA = "arima"
MODEL_MONTE_CARLO = "monte_carlo"
MODEL_HOLT_WINTERS = "holt_winters"
MODEL_CNN_LSTM = "cnn_lstm"
# BERT-LLA ablations (forecasting/bert_lla.py) -- three genuine ablations of
# ONE PyTorch architecture, not three unrelated models: lstm_baseline (no
# attention, no sentiment), lstm_attention (+ LLA attention, no sentiment),
# bert_lla (+ LLA attention + the composite sentiment index). All gated
# behind settings.BERT_LLA_ENABLED (default False) -- these names appear in
# forecast_errors only once an operator opts in.
MODEL_LSTM_BASELINE = "lstm_baseline"
MODEL_LSTM_ATTENTION = "lstm_attention"
MODEL_BERT_LLA = "bert_lla"
ALL_MODEL_NAMES = (
    MODEL_ARIMA, MODEL_MONTE_CARLO, MODEL_HOLT_WINTERS, MODEL_CNN_LSTM,
    MODEL_LSTM_BASELINE, MODEL_LSTM_ATTENTION, MODEL_BERT_LLA,
)

# Sentinel model_name recorded by record_forecasts() when a cycle produced NO
# usable model price for a symbol/horizon -- keeps the symbol visible to any
# tool tracking dataset completeness (e.g. "did this symbol get a forecast
# attempt this cycle") without pretending a real price was predicted.
# update_actuals() deliberately never actualizes these rows (see its own
# comment), which keeps them permanently excluded from get_skill_weights()/
# the pilots/observability.py aggregate siblings (all filter on
# actual_price IS NOT NULL) -- a synthetic placeholder must never be able to
# masquerade as a real, measured model in the skill-weighted blend
# (CONSTRAINT #4).
MODEL_EMPTY = "empty"

# Minimum positive RMSE to prevent division-by-zero when a model is extremely
# accurate over a stretch (a $0.01 RMSE cap avoids assigning infinite weight).
_MIN_RMSE = 0.01


def compute_skill_weights_from_stats(
    model_stats: Dict[str, Tuple[int, float]],
    min_obs: int,
) -> Dict[str, float]:
    """Pure function: normalized inverse-RMSE weights from per-model (n, mse).

    Single source of truth for the cold-start / inverse-RMSE / graduated-
    degrade formula, shared by ForecastTracker.get_skill_weights and
    pilots/observability.py's two bulk-SQL siblings (_portfolio_forecast_stats,
    _forecast_stats_by_symbol) -- eliminating the "three copies must stay in
    sync" risk that let this bug exist in triplicate undetected.

    1. If NO model has n >= min_obs (nobody is mature yet): equal weights
       across EVERY model in model_stats -- the genuine full-cold-start case,
       UNCHANGED from prior behavior.
    2. If ANY model is mature: inverse-RMSE weights computed over the MATURE
       SUBSET ONLY, normalized to sum to 1.0. Immature models are ABSENT
       from the returned dict (not weight 0.0) -- one cold model no longer
       drags N-1 warm models back to uniform.

    Returns {} when model_stats is empty. Never raises -- callers own their
    own try/except (this function is pure math over already-fetched stats).
    """
    if not model_stats:
        return {}

    mature = {name: stats for name, stats in model_stats.items() if stats[0] >= min_obs}

    if not mature:
        n_models = len(model_stats)
        return {name: 1.0 / n_models for name in model_stats}

    inv_rmse: Dict[str, float] = {}
    for name, (_, mse) in mature.items():
        rmse = math.sqrt(mse) if mse >= 0 else 0.0
        inv_rmse[name] = 1.0 / max(rmse, _MIN_RMSE)

    total = sum(inv_rmse.values())
    if total <= 0:
        n_mature = len(inv_rmse)
        return {name: 1.0 / n_mature for name in inv_rmse}

    return {name: w / total for name, w in inv_rmse.items()}


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
    readonly : bool
        When True, the cached connection is opened DATABASE-LEVEL read-only
        (``db_config.sqlite_readonly_uri``, ``?mode=ro``) and ``_ensure_table()``
        is skipped at construction (DDL is itself a write). A readonly instance
        assumes ``forecast_errors`` already exists — true once any write-mode
        tracker has run, which happens before any read-only consumer is
        reachable in practice; if it genuinely doesn't exist yet, reads degrade
        to their normal empty-sentinel behavior (CONSTRAINT #6). A write call
        (``record_forecasts``/``update_actuals``) on a readonly instance is
        rejected at the DB level rather than silently no-op'd (CONSTRAINT #4).
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

    def __init__(self, db_path: Optional[str] = None, *, readonly: bool = False) -> None:
        if db_path is None:
            # This class talks to sqlite directly (sqlite3.connect(), not
            # SQLAlchemy), so it needs a bare filesystem path -- not the
            # sqlite:///<path> URL db_config.resolve_database_url() returns.
            # Only ever resolves to something other than settings.LOCAL_DATA_ROOT
            # / "quant_platform.db" if the operator has explicitly set a custom
            # DATABASE_URL; a non-sqlite (e.g. postgresql://) override falls
            # back to the historical CWD-relative literal, since this class has
            # never supported any backend other than sqlite.
            from db_config import resolve_database_url
            resolved = resolve_database_url()
            if resolved.startswith("sqlite"):
                from sqlalchemy.engine import make_url
                db_path = make_url(resolved).database or "quant_platform.db"
            else:
                db_path = "quant_platform.db"
        self._db_path = db_path
        self._readonly = readonly
        # ONE reused sqlite connection (opened lazily on first data-method use)
        # replaces the previous per-call open+PRAGMA. A per-ticker×per-horizon
        # caller used to open ~12 short-lived connections per ticker per cycle;
        # now they all share this one, cutting connection/PRAGMA overhead.
        #
        # Thread-safety (option a): the tracker is used inside
        # ``ForecastingEngine.generate_forecast`` which runs inside the
        # forecasting ThreadPoolExecutor, and a single tracker/engine instance
        # can be shared across those worker threads. A single sqlite connection
        # is NOT safe across threads by default, so the connection is opened
        # with ``check_same_thread=False`` and EVERY query is serialized by
        # ``self._lock``. sqlite serializes writes internally anyway, so the
        # lock adds no meaningful contention while guaranteeing correctness.
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        if not readonly:
            self._ensure_table()

    # -------------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------------
    def _new_connection(self) -> sqlite3.Connection:
        """Open a fresh sqlite connection with the standard PRAGMAs.

        ``check_same_thread=False`` because the cached connection may be used
        from more than one ThreadPoolExecutor worker; correctness is provided
        by ``self._lock`` guarding every query.
        """
        if self._readonly:
            from db_config import sqlite_readonly_uri
            conn = sqlite3.connect(
                sqlite_readonly_uri(self._db_path), uri=True, check_same_thread=False
            )
            # busy_timeout ONLY — journal_mode=WAL is itself a write and would
            # raise on a non-WAL db read-only (see db_config.create_readonly_db_
            # engine's identical sqlite hook for the full explanation).
            conn.execute("PRAGMA busy_timeout=5000")
            return conn
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")  # concurrent read-write safe
        conn.execute("PRAGMA busy_timeout=5000")  # wait out cross-process locks
        return conn

    def _get_conn(self) -> sqlite3.Connection:
        """Return the cached connection, opening it lazily on first use.

        Callers MUST hold ``self._lock``. Opening lazily (rather than in
        ``__init__``) preserves the dead-letter contract: a ``_db_path`` that
        points at an unwritable location still raises here — inside a method's
        try/except — instead of at construction time.
        """
        if self._conn is None:
            self._conn = self._new_connection()
        return self._conn

    def _safe_rollback(self) -> None:
        """Best-effort rollback of the shared connection after a failed write.

        With a per-call connection the old ``with self._connect()`` context
        manager rolled back on error before discarding the connection; the
        shared connection is long-lived, so a failed write must be rolled back
        explicitly to avoid leaving a dangling transaction. Never raises.
        """
        try:
            if self._conn is not None:
                self._conn.rollback()
        except Exception:
            pass

    def _ensure_table(self) -> None:
        """Create the forecast_errors table and index if they don't exist.

        Uses a short-lived connection (closed immediately) rather than the
        cached one so construction never leaves a live connection pinned to a
        (possibly soon-to-be-swapped) ``_db_path`` — the cached connection is
        opened lazily by the first real data-method call.
        """
        try:
            conn = self._new_connection()
            try:
                conn.execute(self._TABLE_DDL)
                conn.execute(self._INDEX_DDL)
                conn.commit()
            finally:
                conn.close()
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
            now_iso = datetime.now(timezone.utc).isoformat()
            ts_iso = forecast_ts.isoformat() if isinstance(forecast_ts, datetime) else str(forecast_ts)
            rows = [
                (symbol.upper(), name, horizon_days, ts_iso, price, now_iso)
                for name, price in model_prices.items()
                if price and price > 0.0
            ]
            if not rows:
                # forecast_price is NOT NULL, and SQLite silently stores a
                # bound Python float("nan") as NULL (verified -- it still
                # trips the NOT NULL check), so NaN can't be used here. 0.0
                # is safe precisely because every downstream reader of
                # forecast_price in this file (get_forecast_error_summary,
                # calibration/pct-error queries) filters on
                # `actual_price IS NOT NULL`, and update_actuals() below
                # never actualizes a MODEL_EMPTY row -- its forecast_price
                # value is therefore never read by anything (CONSTRAINT #4:
                # no query ever presents it as a measured price).
                rows = [(symbol.upper(), MODEL_EMPTY, horizon_days, ts_iso, 0.0, now_iso)]
            with self._lock:
                conn = self._get_conn()
                conn.executemany(
                    """INSERT INTO forecast_errors
                       (symbol, model_name, horizon_days, forecast_ts,
                        forecast_price, recorded_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    rows,
                )
                conn.commit()
        except Exception as exc:
            self._safe_rollback()
            logger.warning("ForecastTracker.record_forecasts(%s, h=%d) failed: %s", symbol, horizon_days, exc)

    def update_actuals(
        self,
        symbol: str,
        horizon_days: int,
        actual_price: float,
        as_of: datetime,
    ) -> int:
        """Match past forecasts with actual realized prices.

        Finds all unactualized rows for ``symbol`` and ``horizon_days`` whose
        ``forecast_ts`` is at least ``horizon_days`` days before ``as_of`` (i.e.
        the full nominal horizon has genuinely elapsed), and writes
        ``actual_price`` + ``squared_error`` into them.

        No separate lateness tolerance is needed: a forecast that becomes due
        while this cycle is skipped (a weekend, a holiday, a missed run) simply
        stays pending until the next call, at which point ``forecast_ts <=
        cutoff_dt`` is still true and it gets actualized then — the existing
        ``<=`` comparison already absorbs arbitrary lateness for free. (Prior to
        2026-08 this method also accepted a ``tolerance_days`` kwarg that
        *subtracted* days from the cutoff, actualizing forecasts up to
        ``tolerance_days`` early instead of late — e.g. a 30-day forecast was
        scored against a day-25 price. That was a bug, not a grace window; see
        ``docs/known_issues/forecast_tracker_early_actualization.md``.)

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

        Returns
        -------
        int
            Number of rows updated (0 when nothing was due).
        """
        try:
            # A forecast made on day T is "due" when the full horizon has
            # elapsed: now >= T + horizon. Equivalently: T <= now - horizon.
            cutoff_dt = as_of - timedelta(days=max(0, horizon_days))
            cutoff_iso = cutoff_dt.isoformat()

            with self._lock:
                conn = self._get_conn()
                cursor = conn.execute(
                    """UPDATE forecast_errors
                       SET actual_price  = ?,
                           squared_error = (? - forecast_price) * (? - forecast_price)
                       WHERE symbol       = ?
                         AND horizon_days = ?
                         AND forecast_ts  <= ?
                         AND actual_price IS NULL
                         AND model_name   != ?""",
                    (
                        actual_price, actual_price, actual_price,
                        symbol.upper(), horizon_days, cutoff_iso, MODEL_EMPTY,
                    ),
                )
                conn.commit()
                return cursor.rowcount
        except Exception as exc:
            self._safe_rollback()
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

        Algorithm (graduated degrade -- see ``compute_skill_weights_from_stats``)
        ---------------------------------------------------------------------
        1. Query completed (``actual_price IS NOT NULL``) rows in the rolling
           ``window_days`` window.
        2. Compute per-model ``n`` (count) and ``mse`` (mean squared error).
        3. **Full cold-start**: if NO model has ``n >= min_obs``, return equal
           weights for all models seen in the window (symmetric treatment,
           unchanged from prior behavior).
        4. **Graduated degrade**: if ANY model is mature (``n >= min_obs``),
           compute inverse-RMSE weights over the MATURE SUBSET ONLY, normalized
           to sum to 1.0 -- an immature model is simply absent from the
           returned dict rather than dragging every mature model back to
           uniform weighting.

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
            since_iso = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
            with self._lock:
                conn = self._get_conn()
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

            return compute_skill_weights_from_stats(model_stats, min_obs)

        except Exception as exc:
            logger.warning(
                "ForecastTracker.get_skill_weights(%s, h=%d) failed: %s", symbol, horizon_days, exc
            )
            return {}

    def get_error_by_model(
        self,
        symbol: str,
        horizon_days: int,
        window_days: int = 60,
    ) -> "list[Dict[str, object]]":
        """Per-model RMSE and mean absolute error over completed forecasts.

        Unlike ``get_skill_weights`` (which returns a normalized ensemble
        weight), this returns the raw error magnitudes themselves — for
        displaying "how far off was each model, in price terms" rather than
        "how much should each model count." No schema change was needed:
        ``forecast_price``/``actual_price`` are both already stored per row,
        so MAE is computed the same way RMSE always has been (from
        ``squared_error``), just with ``AVG(ABS(...))`` alongside it.

        Same completed-rows-in-``window_days`` query shape as
        ``get_skill_weights``, but with no cold-start/min_obs behavior — a
        raw error figure is meaningful even from a single observation
        (unlike an ensemble weight, which cold-starts to avoid overfitting a
        blend to noise).

        Parameters
        ----------
        symbol : str
            Ticker symbol.
        horizon_days : int
            Forecast horizon to query.
        window_days : int
            Rolling window size in calendar days (default 60).

        Returns
        -------
        list[dict]
            ``[{"model_name": str, "n": int, "rmse": float | None,
            "mae": float | None}, ...]``, sorted by ``rmse`` ascending (most
            accurate model first; a model with no finite rmse sorts last).
            Empty list when no completed rows exist in the window, or on any
            DB error (dead-letter resilient — CONSTRAINT #6, never raises).
        """
        try:
            since_iso = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
            with self._lock:
                conn = self._get_conn()
                cursor = conn.execute(
                    """SELECT model_name,
                              COUNT(*)                                    AS n,
                              AVG(squared_error)                          AS mse,
                              AVG(ABS(actual_price - forecast_price))     AS mae
                       FROM forecast_errors
                       WHERE symbol        = ?
                         AND horizon_days  = ?
                         AND actual_price  IS NOT NULL
                         AND forecast_ts   >= ?
                       GROUP BY model_name""",
                    (symbol.upper(), horizon_days, since_iso),
                )
                rows = cursor.fetchall()

            results = []
            for model_name, n, mse, mae in rows:
                rmse = math.sqrt(mse) if mse is not None and mse >= 0 else None
                results.append(
                    {
                        "model_name": model_name,
                        "n": int(n),
                        "rmse": rmse,
                        "mae": float(mae) if mae is not None else None,
                    }
                )
            # float("inf") sort key (not a (None, real) tuple) so two None-rmse
            # rows never compare None < None, which raises TypeError in Python 3.
            results.sort(key=lambda r: r["rmse"] if r["rmse"] is not None else float("inf"))
            return results

        except Exception as exc:
            logger.warning(
                "ForecastTracker.get_error_by_model(%s, h=%d) failed: %s", symbol, horizon_days, exc
            )
            return []

    def get_covered_symbols(
        self,
        horizon_days: Optional[int] = None,
        window_days: Optional[int] = 7,
    ) -> "list[str]":
        """Return symbols with at least one RECENT forecast recorded.

        "Recent" means ``forecast_ts >= now - window_days`` (default 7
        calendar days) rather than an all-time distinct scan over
        ``forecast_errors``: a symbol forecast once and never again (e.g.
        dropped from the universe months ago) must not read as "covered"
        forever — that would misrepresent the TRUE ACTIVE forecast universe
        to callers such as ``data.portfolio_sync.build_sync_report`` (which
        uses this to set ``SymbolStatus.forecast_available``). 7 days
        tolerates a missed daily cycle / weekend / holiday without
        indefinitely retaining a symbol nothing has forecast in a long time.
        Pass ``window_days=None`` to disable the recency filter and fall back
        to an all-time distinct scan (mainly useful for tests/debugging).

        Never raises (CONSTRAINT #6): any DB error degrades to ``[]``,
        matching every other read method on this class.

        Parameters
        ----------
        horizon_days : int, optional
            Restrict to forecasts recorded at this horizon (e.g. 30).
            ``None`` (default) considers every horizon.
        window_days : int, optional
            Only count a symbol as covered if it has a forecast whose
            ``forecast_ts`` falls within the last ``window_days`` calendar
            days (default 7). ``None`` disables the recency filter entirely.

        Returns
        -------
        list[str]
            Distinct, uppercased ticker symbols. Empty on any DB error or
            when nothing qualifies.
        """
        try:
            clauses = []
            params: list = []
            if horizon_days is not None:
                clauses.append("horizon_days = ?")
                params.append(horizon_days)
            if window_days is not None:
                since_iso = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
                clauses.append("forecast_ts >= ?")
                params.append(since_iso)

            query = "SELECT DISTINCT symbol FROM forecast_errors"
            if clauses:
                query += " WHERE " + " AND ".join(clauses)

            with self._lock:
                conn = self._get_conn()
                cur = conn.execute(query, tuple(params))
                rows = cur.fetchall()
            return [row[0] for row in rows]
        except Exception as exc:
            logger.warning(
                "ForecastTracker.get_covered_symbols(h=%s, window=%s) failed: %s",
                horizon_days, window_days, exc,
            )
            return []

    def pending_count(self, symbol: str, horizon_days: int) -> int:
        """Return the number of un-actualized forecast rows for a symbol+horizon.

        Useful for monitoring how many forecasts are awaiting ground-truth prices.
        Returns 0 on any DB error.
        """
        try:
            with self._lock:
                conn = self._get_conn()
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
            since_iso = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
            with self._lock:
                conn = self._get_conn()
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

    def get_forecast_reliability_curve(
        self,
        symbol: Optional[str] = None,
        horizon_days: Optional[int] = None,
        n_bins: int = 10,
        min_per_bin: int = 3,
    ) -> "pd.DataFrame":
        """Reliability/calibration curve for forecast accuracy.

        Bins COMPLETED forecast_errors rows (``actual_price IS NOT NULL``) by
        the realized percent error ``(actual_price - forecast_price) /
        actual_price`` into ``n_bins`` fixed-width buckets spanning a stable
        ``[-0.5, 0.5]`` range, grouped by ``(model_name, horizon_days)`` --
        producing a per-model/per-horizon calibration diagnostic showing
        systematic over-/under-prediction bias.

        Distinct from ``evaluation_engine.py``'s ``calibration_curve()``
        (conviction-vs-win-rate from closed trades, not forecast accuracy --
        a different data source and a different question entirely).

        Args:
            symbol: Optional ticker filter (case-insensitive). ``None`` means
                all symbols.
            horizon_days: Optional horizon filter. ``None`` means all horizons.
            n_bins: Number of equal-width buckets spanning [-0.5, 0.5].
            min_per_bin: Bins with fewer than this many rows get
                ``mean_pct_error=NaN`` (insufficient sample, never fabricated
                -- CONSTRAINT #4).

        Returns:
            DataFrame with columns ``model_name``, ``horizon_days``,
            ``bin_low``, ``bin_high``, ``bin_center``, ``mean_pct_error``,
            ``count``. Empty DataFrame with the correct schema (zero rows)
            when no completed rows match the filter, or on any DB error
            (dead-letter resilient -- CONSTRAINT #6, never raises).
        """
        columns = ["model_name", "horizon_days", "bin_low", "bin_high", "bin_center", "mean_pct_error", "count"]
        empty_df = pd.DataFrame(columns=columns)

        try:
            query = """
                SELECT model_name, horizon_days, forecast_price, actual_price
                FROM forecast_errors
                WHERE actual_price IS NOT NULL
            """
            params: list = []
            if symbol is not None:
                query += " AND symbol = ?"
                params.append(symbol.upper())
            if horizon_days is not None:
                query += " AND horizon_days = ?"
                params.append(horizon_days)

            with self._lock:
                conn = self._get_conn()
                rows = conn.execute(query, params).fetchall()

            if not rows:
                return empty_df

            df = pd.DataFrame(rows, columns=["model_name", "horizon_days", "forecast_price", "actual_price"])
            df = df[df["actual_price"] != 0]
            if df.empty:
                return empty_df

            df["pct_error"] = (df["actual_price"] - df["forecast_price"]) / df["actual_price"]

            bins = [-0.5 + i * (1.0 / n_bins) for i in range(n_bins + 1)]
            df["_bin"] = pd.cut(df["pct_error"], bins=bins, include_lowest=True)

            records = []
            for (model_name, h_days), group in df.groupby(["model_name", "horizon_days"]):
                for interval in sorted(group["_bin"].dropna().unique()):
                    bucket = group[group["_bin"] == interval]
                    count = len(bucket)
                    bin_low = float(interval.left)
                    bin_high = float(interval.right)
                    bin_center = (bin_low + bin_high) / 2.0
                    mean_pct_error = (
                        float(bucket["pct_error"].mean()) if count >= min_per_bin else float("nan")
                    )
                    records.append({
                        "model_name": model_name,
                        "horizon_days": int(h_days),
                        "bin_low": bin_low,
                        "bin_high": bin_high,
                        "bin_center": bin_center,
                        "mean_pct_error": mean_pct_error,
                        "count": count,
                    })

            if not records:
                return empty_df

            result = pd.DataFrame(records)
            result["count"] = result["count"].astype(int)
            result["horizon_days"] = result["horizon_days"].astype(int)
            return result

        except Exception as exc:
            logger.warning(
                "ForecastTracker.get_forecast_reliability_curve(symbol=%s, horizon=%s) failed: %s",
                symbol, horizon_days, exc,
            )
            return empty_df
