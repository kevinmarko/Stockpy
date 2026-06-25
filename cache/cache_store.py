"""
cache/cache_store.py
====================
Disk-persisted, SQLite-backed cache for all slow-changing data consumed by the
InvestYo Quant Platform.

Every data category has a natural refresh cadence (INTRADAY to YEARLY).  This
module enforces those cadences so the system never re-fetches data that hasn't
changed, while still picking up fresh values when the TTL elapses.

Public API
----------
Cache                 – core SQLite-backed get / set / invalidate / clear
CacheEntry            – immutable value-object returned by Cache.get()
Cadence               – enum of refresh cadences
CADENCE_TTL           – single dict mapping Cadence → timedelta (tune here)
CADENCE_REGISTRY      – maps logical data-type names to their Cadence
cached                – @cached(namespace, cadence) decorator for fetch funcs
get_default_cache()   – process-wide singleton Cache (path: cache/cache.db)

Architecture notes
------------------
* SQLite WAL mode: concurrent readers never block each other.
* threading.RLock: only one writer at a time.
* Values are JSON-serialised; DataFrames / Series go through a custom
  encoder.  OHLCV history blobs are gzip-compressed JSON for compactness.
* Secrets must NEVER be passed as cache values — callers are responsible.
* `get_history_incremental` fetches only the new bars since the last cached
  date (the "delta") rather than re-downloading full history on every refresh.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# CADENCE DEFINITIONS  (single place to tune all TTLs)
# ─────────────────────────────────────────────────────────────────────────────

class Cadence(Enum):
    """Refresh-cadence category.  TTLs are defined in CADENCE_TTL below."""
    INTRADAY  = "INTRADAY"
    DAILY     = "DAILY"
    WEEKLY    = "WEEKLY"
    MONTHLY   = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    YEARLY    = "YEARLY"


# One place to tune freshness for every cadence.
CADENCE_TTL: dict[Cadence, timedelta] = {
    Cadence.INTRADAY:  timedelta(minutes=5),
    Cadence.DAILY:     timedelta(hours=20),
    Cadence.WEEKLY:    timedelta(days=7),
    Cadence.MONTHLY:   timedelta(days=30),
    Cadence.QUARTERLY: timedelta(days=90),
    Cadence.YEARLY:    timedelta(days=365),
}


# Maps each logical data category to its natural refresh cadence.
# Change a single line here to retune a category's freshness policy.
CADENCE_REGISTRY: dict[str, Cadence] = {
    "quotes":              Cadence.INTRADAY,
    "daily_bars":          Cadence.DAILY,
    "macro_regime_inputs": Cadence.DAILY,
    "analyst_ratings":     Cadence.WEEKLY,
    "earnings_calendar":   Cadence.WEEKLY,
    "fundamentals":        Cadence.QUARTERLY,
    "financials":          Cadence.QUARTERLY,
    "dividends_meta":      Cadence.QUARTERLY,
    "company_profile":     Cadence.YEARLY,
}


# ─────────────────────────────────────────────────────────────────────────────
# CACHE ENTRY  (frozen value-object)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CacheEntry:
    """Immutable snapshot of a single cached item returned by Cache.get()."""

    value: Any
    fetched_at: datetime    # tz-aware UTC timestamp of the original fetch
    expires_at: datetime    # tz-aware UTC timestamp after which the entry is stale
    cadence: Cadence

    @property
    def age_seconds(self) -> float:
        """Elapsed wall-clock seconds since this entry was fetched."""
        return (datetime.now(timezone.utc) - self.fetched_at).total_seconds()

    @property
    def is_fresh(self) -> bool:
        """True when the entry has not yet crossed its expires_at timestamp."""
        return datetime.now(timezone.utc) < self.expires_at


# ─────────────────────────────────────────────────────────────────────────────
# SERIALISATION HELPERS
# ─────────────────────────────────────────────────────────────────────────────

class _Encoder(json.JSONEncoder):
    """JSON encoder that handles pandas DataFrames, Series, and datetimes."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, pd.DataFrame):
            return {"__type__": "DataFrame", "data": obj.to_json(orient="split")}
        if isinstance(obj, pd.Series):
            return {"__type__": "Series", "data": obj.to_json(orient="split")}
        if isinstance(obj, datetime):
            return {"__type__": "datetime", "iso": obj.isoformat()}
        return super().default(obj)


def _object_hook(d: dict) -> Any:
    """Inverse of _Encoder — restores DataFrames, Series, and datetimes."""
    t = d.get("__type__")
    if t == "DataFrame":
        return pd.read_json(io.StringIO(d["data"]), orient="split")
    if t == "Series":
        return pd.read_json(io.StringIO(d["data"]), orient="split", typ="series")
    if t == "datetime":
        return datetime.fromisoformat(d["iso"])
    return d


def _to_json(value: Any) -> str:
    """Serialize *value* to a JSON string."""
    return json.dumps(value, cls=_Encoder)


def _from_json(raw: str) -> Any:
    """Deserialize a JSON string produced by _to_json."""
    return json.loads(raw, object_hook=_object_hook)


def _df_to_blob(df: pd.DataFrame) -> bytes:
    """Serialize a DataFrame to gzip-compressed JSON bytes (no pyarrow dependency)."""
    # Normalise the index to tz-naive strings so the round-trip is deterministic
    # regardless of whether the source (yfinance, FRED) was tz-aware.
    df_copy = df.copy()
    if not df_copy.empty:
        idx = pd.to_datetime(df_copy.index)
        if idx.tz is not None:
            idx = idx.tz_convert(None)
        df_copy.index = idx
    json_str = df_copy.to_json(orient="split", date_format="iso")
    return gzip.compress(json_str.encode("utf-8"))


def _blob_to_df(blob: bytes) -> pd.DataFrame:
    """Deserialize a blob produced by _df_to_blob back to a DataFrame."""
    json_str = gzip.decompress(blob).decode("utf-8")
    df = pd.read_json(io.StringIO(json_str), orient="split")
    # Ensure a proper DatetimeIndex regardless of pandas version inference.
    if not df.empty:
        df.index = pd.to_datetime(df.index)
    return df


def _make_key(*args: Any, **kwargs: Any) -> str:
    """Build a deterministic cache key from function arguments.

    Short keys (≤ 128 chars) are used verbatim; longer ones are SHA-256-hashed
    to keep the SQLite index tight.
    """
    parts = [repr(a) for a in args]
    parts += [f"{k}={repr(v)}" for k, v in sorted(kwargs.items())]
    raw = "|".join(parts)
    return raw if len(raw) <= 128 else hashlib.sha256(raw.encode()).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# CORE CACHE CLASS
# ─────────────────────────────────────────────────────────────────────────────

class Cache:
    """SQLite-backed, thread-safe, disk-persisted key-value cache.

    All values are JSON-serialised via _Encoder / _object_hook.  OHLCV history
    is stored in a separate ``history_cache`` table as gzip-compressed JSON blobs
    to avoid the per-row overhead of individual entries.

    Parameters
    ----------
    db_path : Path
        Filesystem path to the SQLite file.  The parent directory is created
        automatically if it does not exist.

    Thread safety
    -------------
    A per-instance ``threading.RLock`` serialises writes.  SQLite WAL mode
    allows concurrent reads from multiple threads or processes.
    """

    _DDL = """
    CREATE TABLE IF NOT EXISTS cache_entries (
        namespace   TEXT NOT NULL,
        key         TEXT NOT NULL,
        value       TEXT NOT NULL,
        fetched_at  TEXT NOT NULL,
        expires_at  TEXT NOT NULL,
        cadence     TEXT NOT NULL,
        PRIMARY KEY (namespace, key)
    );
    CREATE TABLE IF NOT EXISTS history_cache (
        symbol          TEXT NOT NULL,
        namespace       TEXT NOT NULL,
        cadence         TEXT NOT NULL,
        last_fetched_at TEXT NOT NULL,
        last_bar_date   TEXT,
        data            BLOB NOT NULL,
        PRIMARY KEY (symbol, namespace)
    );
    """

    def __init__(self, db_path: Path) -> None:
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._lock = threading.RLock()
        # isolation_level=None → autocommit; we manage transactions manually.
        self._conn = sqlite3.connect(
            str(db_path),
            check_same_thread=False,
            isolation_level=None,
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(self._DDL)
        logger.debug("Cache initialised at %s", db_path)

    # ------------------------------------------------------------------ #
    # Key-value API                                                        #
    # ------------------------------------------------------------------ #

    def get(self, namespace: str, key: str) -> Optional[CacheEntry]:
        """Return a fresh CacheEntry or None (missing / expired).

        Parameters
        ----------
        namespace : str
            Logical group, e.g. ``"fundamentals"``.
        key : str
            Within-namespace identifier, e.g. a ticker symbol.

        Returns
        -------
        CacheEntry | None
            ``None`` when the entry is absent or its ``expires_at`` is in the past.
        """
        # Python's sqlite3.Connection is not thread-safe even with
        # check_same_thread=False (that flag only suppresses the exception).
        # The lock serialises all access — reads included — to prevent cursor
        # state corruption under concurrent calls.
        with self._lock:
            row = self._conn.execute(
                "SELECT value, fetched_at, expires_at, cadence "
                "FROM cache_entries WHERE namespace=? AND key=?",
                (namespace, key),
            ).fetchone()

        if row is None:
            return None

        value_raw, fetched_at_iso, expires_at_iso, cadence_name = row
        expires_at = datetime.fromisoformat(expires_at_iso)

        if datetime.now(timezone.utc) >= expires_at:
            logger.debug("Cache expired: %s/%s", namespace, key)
            return None

        return CacheEntry(
            value=_from_json(value_raw),
            fetched_at=datetime.fromisoformat(fetched_at_iso),
            expires_at=expires_at,
            cadence=Cadence(cadence_name),
        )

    def set(
        self,
        namespace: str,
        key: str,
        value: Any,
        cadence: Cadence,
        expires_at: Optional[datetime] = None,
    ) -> None:
        """Persist *value* in the cache.

        Parameters
        ----------
        namespace, key : str
            Cache address.
        value : Any
            JSON-serialisable value.  NEVER pass secrets — they will be
            written to disk in plain text.
        cadence : Cadence
            Used to compute ``expires_at`` when not supplied explicitly.
        expires_at : datetime | None
            Override the cadence-derived TTL (e.g. expire on a known
            next-earnings date).  Must be tz-aware UTC when supplied.
        """
        now = datetime.now(timezone.utc)
        if expires_at is None:
            expires_at = now + CADENCE_TTL[cadence]

        value_json = _to_json(value)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    "INSERT OR REPLACE INTO cache_entries "
                    "(namespace, key, value, fetched_at, expires_at, cadence) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        namespace,
                        key,
                        value_json,
                        now.isoformat(),
                        expires_at.isoformat(),
                        cadence.value,
                    ),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

        logger.debug("Cache set: %s/%s (cadence=%s)", namespace, key, cadence.name)

    def invalidate(self, namespace: str, key: str) -> None:
        """Remove a single entry from the cache."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM cache_entries WHERE namespace=? AND key=?",
                (namespace, key),
            )
        logger.debug("Cache invalidated: %s/%s", namespace, key)

    def clear(self, namespace: Optional[str] = None) -> None:
        """Remove all entries, optionally scoped to a single namespace.

        Parameters
        ----------
        namespace : str | None
            When ``None``, clears the entire cache (both tables).
            When provided, clears only entries in that namespace.
        """
        with self._lock:
            if namespace is None:
                self._conn.execute("DELETE FROM cache_entries")
                self._conn.execute("DELETE FROM history_cache")
                logger.info("Cache cleared entirely.")
            else:
                self._conn.execute(
                    "DELETE FROM cache_entries WHERE namespace=?", (namespace,)
                )
                self._conn.execute(
                    "DELETE FROM history_cache WHERE namespace=?", (namespace,)
                )
                logger.info("Cache cleared for namespace=%s", namespace)

    # ------------------------------------------------------------------ #
    # Incremental time-series history                                      #
    # ------------------------------------------------------------------ #

    def get_history_incremental(
        self,
        symbol: str,
        fetch_fn: Callable[..., pd.DataFrame],
        cadence: Cadence = Cadence.DAILY,
        namespace: str = "daily_bars",
    ) -> pd.DataFrame:
        """Load OHLCV history for *symbol*, fetching only the delta on refresh.

        On the first call (cold cache) the full history is downloaded once via
        ``fetch_fn(symbol)`` and persisted.  On subsequent calls only bars
        after the last cached date are requested via
        ``fetch_fn(symbol, start="YYYY-MM-DD")``, merged onto the cached
        series, de-duplicated on the DatetimeIndex, and re-persisted.

        Parameters
        ----------
        symbol : str
            Ticker symbol; used as the primary key in the ``history_cache`` table.
        fetch_fn : Callable
            Signature: ``fetch_fn(symbol: str, start: str | None = None) -> pd.DataFrame``
            where *start* is an ISO date string.  When *start* is ``None`` the
            function should return the full desired history.  If ``fetch_fn``
            does not accept a ``start`` keyword, it is called without it and
            the full history is re-fetched.
        cadence : Cadence
            Controls how often to check for new bars.  Entries younger than
            the cadence TTL are returned as-is (no network call).
        namespace : str
            Cache namespace; defaults to ``"daily_bars"``.

        Returns
        -------
        pd.DataFrame
            Full merged history with a (tz-naive) DatetimeIndex, de-duplicated
            and sorted ascending.
        """
        now = datetime.now(timezone.utc)

        with self._lock:
            row = self._conn.execute(
                "SELECT cadence, last_fetched_at, last_bar_date, data "
                "FROM history_cache WHERE symbol=? AND namespace=?",
                (symbol, namespace),
            ).fetchone()

        if row is not None:
            _, last_fetched_iso, last_bar_date_str, blob = row
            last_fetched_at = datetime.fromisoformat(last_fetched_iso)
            ttl = CADENCE_TTL[cadence]
            cached_df = _blob_to_df(blob)

            if now - last_fetched_at < ttl:
                # Still within the cadence window — no network call needed.
                logger.info(
                    "Cache hit (history): %s/%s — %d bars, last bar %s",
                    namespace, symbol, len(cached_df), last_bar_date_str,
                )
                return cached_df

            # Cadence window elapsed: fetch only the delta.
            if last_bar_date_str:
                start_dt = pd.Timestamp(last_bar_date_str) + pd.Timedelta(days=1)
                start_str = start_dt.strftime("%Y-%m-%d")
                logger.info(
                    "Fetching history delta from %s for %s (namespace=%s)",
                    start_str, symbol, namespace,
                )
                try:
                    delta_df = fetch_fn(symbol, start=start_str)
                except TypeError:
                    # fetch_fn doesn't accept the start kwarg — full re-fetch.
                    logger.debug(
                        "fetch_fn for %s does not accept 'start'; falling back to full fetch",
                        symbol,
                    )
                    delta_df = fetch_fn(symbol)

                if delta_df is not None and not delta_df.empty:
                    delta_df = delta_df.copy()
                    delta_df.index = pd.to_datetime(delta_df.index)
                    # Strip timezone so index types are compatible for concat.
                    if delta_df.index.tz is not None:
                        delta_df.index = delta_df.index.tz_convert(None)
                    merged = pd.concat([cached_df, delta_df])
                    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
                else:
                    merged = cached_df
            else:
                # No last_bar_date recorded: fall back to full re-fetch.
                logger.info("Full history fetch (no last bar date): %s", symbol)
                full = fetch_fn(symbol)
                merged = pd.DataFrame() if full is None else full.copy()
                if not merged.empty:
                    merged.index = pd.to_datetime(merged.index)
                    if merged.index.tz is not None:
                        merged.index = merged.index.tz_convert(None)

            self._persist_history(symbol, namespace, cadence, merged, now)
            return merged

        # Cold cache — full fetch.
        logger.info("Cold cache: full history fetch for %s (namespace=%s)", symbol, namespace)
        df = fetch_fn(symbol)
        if df is None:
            df = pd.DataFrame()
        else:
            df = df.copy()
            if not df.empty:
                df.index = pd.to_datetime(df.index)
                if df.index.tz is not None:
                    df.index = df.index.tz_convert(None)
        self._persist_history(symbol, namespace, cadence, df, now)
        return df

    def _persist_history(
        self,
        symbol: str,
        namespace: str,
        cadence: Cadence,
        df: pd.DataFrame,
        now: datetime,
    ) -> None:
        """Write (or overwrite) a symbol's history blob in ``history_cache``."""
        last_bar_date: Optional[str] = None
        if not df.empty:
            last_bar_date = pd.Timestamp(df.index.max()).strftime("%Y-%m-%d")

        blob = _df_to_blob(df)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    "INSERT OR REPLACE INTO history_cache "
                    "(symbol, namespace, cadence, last_fetched_at, last_bar_date, data) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (symbol, namespace, cadence.value, now.isoformat(), last_bar_date, blob),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

        logger.debug(
            "History persisted: %s/%s — %d bars, last bar %s",
            namespace, symbol, len(df), last_bar_date,
        )

    def close(self) -> None:
        """Release the underlying SQLite connection."""
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# MODULE-LEVEL SINGLETON
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_DB_PATH: Path = Path(__file__).parent / "cache.db"
_default_cache: Optional[Cache] = None
_default_cache_lock: threading.Lock = threading.Lock()


def get_default_cache(db_path: Path = _DEFAULT_DB_PATH) -> Cache:
    """Return (or create) the process-wide singleton Cache instance.

    The first call initialises the SQLite DB at *db_path*.  Subsequent calls
    reuse the same object regardless of *db_path* (the path is only honoured
    on the very first call).  For testing, use monkeypatch / ``_inject_cache``
    to substitute a temp-file instance.
    """
    global _default_cache
    with _default_cache_lock:
        if _default_cache is None:
            _default_cache = Cache(db_path)
    return _default_cache


def _inject_cache(cache: Optional[Cache]) -> None:
    """Replace the module-level singleton (for testing only).

    Pass ``None`` to reset to an uninitialised state so the next call to
    ``get_default_cache()`` creates a fresh instance.
    """
    global _default_cache
    with _default_cache_lock:
        _default_cache = cache


# ─────────────────────────────────────────────────────────────────────────────
# @cached DECORATOR
# ─────────────────────────────────────────────────────────────────────────────

def cached(
    namespace: str,
    cadence: Cadence,
    key_fn: Optional[Callable[..., str]] = None,
) -> Callable:
    """Decorate any fetch function with transparent cache lookup and storage.

    Usage
    -----
    ::

        @cached("fundamentals", Cadence.QUARTERLY)
        def fetch_fundamentals(symbol: str) -> dict:
            ...  # slow network call

        data = fetch_fundamentals("AAPL")          # first call: network
        data = fetch_fundamentals("AAPL")          # second call: cache hit
        data = fetch_fundamentals("AAPL", force=True)  # bypass cache

    Parameters
    ----------
    namespace : str
        Cache namespace (e.g. ``"fundamentals"``).
    cadence : Cadence
        Determines the TTL for newly stored entries.
    key_fn : Callable | None
        Optional ``(*args, **kwargs) -> str`` override for building the
        cache key.  Defaults to a repr-based hash of all arguments.

    Notes
    -----
    * A ``force=True`` keyword argument may be passed to the decorated
      function to bypass the cache and force a fresh fetch.
    * The ``force`` kwarg is consumed by the wrapper and is NOT forwarded
      to the underlying function.
    """
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            force: bool = kwargs.pop("force", False)

            key = key_fn(*args, **kwargs) if key_fn is not None else _make_key(*args, **kwargs)
            store = get_default_cache()

            if not force:
                entry = store.get(namespace, key)
                if entry is not None:
                    logger.info(
                        "Cache hit: %s/%s (age=%.0fs, cadence=%s)",
                        namespace, key, entry.age_seconds, cadence.name,
                    )
                    return entry.value

            if force:
                logger.info("Cache forced refresh: %s/%s", namespace, key)
            else:
                logger.debug("Cache miss: %s/%s (cadence=%s)", namespace, key, cadence.name)

            value = fn(*args, **kwargs)
            store.set(namespace, key, value, cadence)
            return value

        # Expose metadata so tests and Gravity can inspect decoration.
        wrapper._cache_namespace = namespace  # type: ignore[attr-defined]
        wrapper._cache_cadence = cadence      # type: ignore[attr-defined]
        return wrapper

    return decorator
