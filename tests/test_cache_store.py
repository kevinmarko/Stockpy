"""
tests/test_cache_store.py
=========================
Tests for cache/cache_store.py.

Coverage
--------
- CacheEntry: age_seconds, is_fresh
- Cache.get / set / invalidate / clear (key-value)
- TTL expiry and force-refresh
- Custom expires_at override
- Serialisation round-trips: dict, DataFrame, Series
- Database survives process restart (re-open same SQLite file)
- @cached decorator: second call is a cache hit; force=True bypasses cache;
  different args → different keys; expired entry triggers refresh
- get_history_incremental: cold cache (full fetch), warm cache (no fetch),
  expired cache (delta fetch with logged "Fetching history delta from" message),
  delta merges and de-duplicates correctly, empty delta handled gracefully
- CADENCE_REGISTRY: all required keys present; CADENCE_TTL covers every Cadence
- Thread safety: concurrent readers don't corrupt state
"""

from __future__ import annotations

import io
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import patch

import pandas as pd
import pytest

import cache.cache_store as cs
from cache.cache_store import (
    CADENCE_REGISTRY,
    CADENCE_TTL,
    Cache,
    CacheEntry,
    Cadence,
    _blob_to_df,
    _df_to_blob,
    _from_json,
    _make_key,
    _to_json,
    cached,
    get_default_cache,
)


# ─────────────────────────────────────────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_cache(tmp_path: Path) -> Cache:
    """Isolated Cache backed by a temp SQLite file."""
    c = Cache(tmp_path / "test.db")
    yield c
    c.close()


@pytest.fixture(autouse=True)
def isolate_singleton(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect the module-level singleton to a per-test temp file.

    This prevents decorator tests from bleeding into each other or touching
    the production cache/cache.db.
    """
    test_cache = Cache(tmp_path / "singleton.db")
    monkeypatch.setattr(cs, "_default_cache", test_cache)
    yield
    test_cache.close()
    # Reset so the next test starts fresh.
    monkeypatch.setattr(cs, "_default_cache", None)


# ─────────────────────────────────────────────────────────────────────────────
# CacheEntry
# ─────────────────────────────────────────────────────────────────────────────

class TestCacheEntry:
    def test_age_seconds_approximately_correct(self) -> None:
        now = datetime.now(timezone.utc)
        entry = CacheEntry(
            value=42,
            fetched_at=now - timedelta(seconds=30),
            expires_at=now + timedelta(hours=1),
            cadence=Cadence.DAILY,
        )
        assert 29 <= entry.age_seconds <= 33  # allow 3 s clock slop

    def test_is_fresh_before_expiry(self) -> None:
        now = datetime.now(timezone.utc)
        fresh = CacheEntry(
            value="data",
            fetched_at=now - timedelta(seconds=1),
            expires_at=now + timedelta(hours=1),
            cadence=Cadence.DAILY,
        )
        assert fresh.is_fresh is True

    def test_is_stale_after_expiry(self) -> None:
        now = datetime.now(timezone.utc)
        stale = CacheEntry(
            value="data",
            fetched_at=now - timedelta(hours=2),
            expires_at=now - timedelta(hours=1),
            cadence=Cadence.DAILY,
        )
        assert stale.is_fresh is False


# ─────────────────────────────────────────────────────────────────────────────
# Cache key-value API
# ─────────────────────────────────────────────────────────────────────────────

class TestCacheKeyValue:
    def test_set_and_get_returns_same_value(self, tmp_cache: Cache) -> None:
        tmp_cache.set("ns", "key1", {"x": 1, "y": 2.5}, Cadence.DAILY)
        entry = tmp_cache.get("ns", "key1")
        assert entry is not None
        assert entry.value == {"x": 1, "y": 2.5}
        assert entry.cadence == Cadence.DAILY
        assert entry.is_fresh

    def test_get_missing_key_returns_none(self, tmp_cache: Cache) -> None:
        assert tmp_cache.get("ns", "nonexistent") is None

    def test_expired_entry_returns_none(self, tmp_cache: Cache) -> None:
        # Write with an explicit past expires_at to simulate immediate expiry.
        past = datetime.now(timezone.utc) - timedelta(seconds=1)
        tmp_cache.set("ns", "old", "value", Cadence.DAILY, expires_at=past)
        assert tmp_cache.get("ns", "old") is None

    def test_explicit_expires_at_overrides_cadence_ttl(self, tmp_cache: Cache) -> None:
        far_future = datetime.now(timezone.utc) + timedelta(days=1000)
        tmp_cache.set("ns", "key_ff", "hello", Cadence.INTRADAY, expires_at=far_future)
        entry = tmp_cache.get("ns", "key_ff")
        assert entry is not None
        # INTRADAY TTL is 5 minutes, but far_future means it's still fresh.
        assert entry.is_fresh
        assert abs((entry.expires_at - far_future).total_seconds()) < 1

    def test_invalidate_removes_entry(self, tmp_cache: Cache) -> None:
        tmp_cache.set("ns", "k", "v", Cadence.DAILY)
        assert tmp_cache.get("ns", "k") is not None
        tmp_cache.invalidate("ns", "k")
        assert tmp_cache.get("ns", "k") is None

    def test_invalidate_nonexistent_is_silent(self, tmp_cache: Cache) -> None:
        tmp_cache.invalidate("ns", "ghost")  # must not raise

    def test_clear_namespace_removes_only_that_namespace(self, tmp_cache: Cache) -> None:
        tmp_cache.set("a", "k1", 1, Cadence.DAILY)
        tmp_cache.set("b", "k2", 2, Cadence.DAILY)
        tmp_cache.clear("a")
        assert tmp_cache.get("a", "k1") is None
        assert tmp_cache.get("b", "k2") is not None

    def test_clear_all_removes_everything(self, tmp_cache: Cache) -> None:
        tmp_cache.set("x", "k", 1, Cadence.DAILY)
        tmp_cache.set("y", "k", 2, Cadence.DAILY)
        tmp_cache.clear()
        assert tmp_cache.get("x", "k") is None
        assert tmp_cache.get("y", "k") is None

    def test_set_overwrites_existing_value(self, tmp_cache: Cache) -> None:
        tmp_cache.set("ns", "k", "first", Cadence.DAILY)
        tmp_cache.set("ns", "k", "second", Cadence.DAILY)
        entry = tmp_cache.get("ns", "k")
        assert entry is not None
        assert entry.value == "second"

    def test_survives_process_restart(self, tmp_path: Path) -> None:
        """Data persists in the SQLite file across Cache instances."""
        db_path = tmp_path / "persist.db"
        c1 = Cache(db_path)
        c1.set("persist_ns", "ticker", {"price": 42.0}, Cadence.QUARTERLY)
        c1.close()

        c2 = Cache(db_path)
        entry = c2.get("persist_ns", "ticker")
        c2.close()

        assert entry is not None
        assert entry.value == {"price": 42.0}

    def test_fetched_at_is_utc_aware(self, tmp_cache: Cache) -> None:
        tmp_cache.set("ns", "k", 1, Cadence.DAILY)
        entry = tmp_cache.get("ns", "k")
        assert entry is not None
        assert entry.fetched_at.tzinfo is not None

    def test_expires_at_honours_cadence_ttl(self, tmp_cache: Cache) -> None:
        before = datetime.now(timezone.utc)
        tmp_cache.set("ns", "k", 1, Cadence.QUARTERLY)
        entry = tmp_cache.get("ns", "k")
        after = datetime.now(timezone.utc)
        assert entry is not None
        expected_min = before + CADENCE_TTL[Cadence.QUARTERLY]
        expected_max = after + CADENCE_TTL[Cadence.QUARTERLY]
        assert expected_min <= entry.expires_at <= expected_max


# ─────────────────────────────────────────────────────────────────────────────
# Serialisation round-trips
# ─────────────────────────────────────────────────────────────────────────────

class TestSerialisation:
    def test_dict_round_trip(self, tmp_cache: Cache) -> None:
        original = {"symbol": "AAPL", "pe": 25.3, "sector": "Technology"}
        tmp_cache.set("ns", "k", original, Cadence.DAILY)
        entry = tmp_cache.get("ns", "k")
        assert entry is not None
        assert entry.value == original

    def test_list_round_trip(self, tmp_cache: Cache) -> None:
        original = [1, 2.5, "three", None]
        tmp_cache.set("ns", "k", original, Cadence.DAILY)
        entry = tmp_cache.get("ns", "k")
        assert entry is not None
        assert entry.value == original

    def test_scalar_int_round_trip(self, tmp_cache: Cache) -> None:
        tmp_cache.set("ns", "k", 99, Cadence.DAILY)
        entry = tmp_cache.get("ns", "k")
        assert entry is not None
        assert entry.value == 99

    def test_dataframe_round_trip_via_json(self) -> None:
        dates = pd.date_range("2024-01-01", periods=3, freq="B")
        df = pd.DataFrame({"Close": [100.0, 101.0, 99.5], "Volume": [1000, 1200, 800]}, index=dates)
        blob = _df_to_blob(df)
        recovered = _blob_to_df(blob)
        assert len(recovered) == 3
        assert list(recovered.columns) == list(df.columns)
        pd.testing.assert_series_equal(
            recovered["Close"].reset_index(drop=True),
            df["Close"].reset_index(drop=True),
            check_names=False,
        )

    def test_dataframe_tz_aware_index_normalised_to_naive(self) -> None:
        dates = pd.date_range("2024-01-01", periods=5, freq="B", tz="UTC")
        df = pd.DataFrame({"Close": range(5)}, index=dates)
        blob = _df_to_blob(df)
        recovered = _blob_to_df(blob)
        assert recovered.index.tz is None  # tz-naive after round-trip

    def test_series_in_json_round_trip(self) -> None:
        s = pd.Series({"a": 1.0, "b": 2.0})
        raw = _to_json(s)
        restored = _from_json(raw)
        assert isinstance(restored, pd.Series)
        assert float(restored["a"]) == 1.0

    def test_make_key_short_is_verbatim(self) -> None:
        k = _make_key("AAPL")
        assert k == "'AAPL'"

    def test_make_key_long_is_hashed(self) -> None:
        long_args = ["x" * 200]
        k = _make_key(*long_args)
        assert len(k) == 64  # SHA-256 hex length

    def test_make_key_deterministic(self) -> None:
        assert _make_key("AAPL", 42) == _make_key("AAPL", 42)

    def test_make_key_differs_on_different_args(self) -> None:
        assert _make_key("AAPL") != _make_key("MSFT")


# ─────────────────────────────────────────────────────────────────────────────
# @cached decorator
# ─────────────────────────────────────────────────────────────────────────────

class TestCachedDecorator:
    def test_second_call_is_cache_hit_network_called_once(self) -> None:
        call_count = {"n": 0}

        @cached("fundamentals", Cadence.QUARTERLY)
        def fetch_fund(symbol: str) -> dict:
            call_count["n"] += 1
            return {"pe_ratio": 25.0, "symbol": symbol}

        r1 = fetch_fund("AAPL")
        r2 = fetch_fund("AAPL")

        assert call_count["n"] == 1, "network should be called only once"
        assert r1 == r2
        assert r1["symbol"] == "AAPL"

    def test_second_call_logs_cache_hit(self, caplog: pytest.LogCaptureFixture) -> None:
        @cached("fundamentals", Cadence.QUARTERLY)
        def fetch_fund2(symbol: str) -> dict:
            return {"pe": 10.0}

        fetch_fund2("AAPL")
        with caplog.at_level("INFO", logger="cache.cache_store"):
            fetch_fund2("AAPL")
        assert any("Cache hit" in r.message for r in caplog.records), (
            "second call should log a cache hit"
        )

    def test_force_true_bypasses_cache(self) -> None:
        call_count = {"n": 0}

        @cached("fundamentals", Cadence.QUARTERLY)
        def fetch_fund3(symbol: str) -> dict:
            call_count["n"] += 1
            return {"n": call_count["n"]}

        r1 = fetch_fund3("AAPL")
        r2 = fetch_fund3("AAPL", force=True)

        assert call_count["n"] == 2, "force=True must trigger a fresh fetch"
        assert r1["n"] == 1
        assert r2["n"] == 2

    def test_different_args_produce_different_cache_entries(self) -> None:
        call_count = {"n": 0}

        @cached("fundamentals", Cadence.QUARTERLY)
        def fetch_fund4(symbol: str) -> dict:
            call_count["n"] += 1
            return {"symbol": symbol}

        fetch_fund4("AAPL")
        fetch_fund4("MSFT")

        assert call_count["n"] == 2, "different args must be cached separately"

    def test_expired_entry_triggers_network_call(self) -> None:
        call_count = {"n": 0}

        @cached("fundamentals", Cadence.QUARTERLY)
        def fetch_fund5(symbol: str) -> dict:
            call_count["n"] += 1
            return {"n": call_count["n"]}

        fetch_fund5("AAPL")  # populate cache
        assert call_count["n"] == 1

        # Expire the entry by writing a past expires_at directly.
        store = get_default_cache()
        key = _make_key("AAPL")
        past = datetime.now(timezone.utc) - timedelta(seconds=1)
        store.set("fundamentals", key, {"n": 0}, Cadence.QUARTERLY, expires_at=past)

        fetch_fund5("AAPL")  # must re-fetch
        assert call_count["n"] == 2

    def test_decorator_exposes_metadata_attributes(self) -> None:
        @cached("company_profile", Cadence.YEARLY)
        def fetch_profile(symbol: str) -> dict:
            return {}

        assert fetch_profile._cache_namespace == "company_profile"
        assert fetch_profile._cache_cadence == Cadence.YEARLY


# ─────────────────────────────────────────────────────────────────────────────
# get_history_incremental
# ─────────────────────────────────────────────────────────────────────────────

class TestHistoryIncremental:
    """Uses DAILY cadence (20-hour TTL) but manipulates last_fetched_at directly
    in SQLite to simulate expiry without sleeping."""

    def _make_initial_df(self) -> pd.DataFrame:
        dates = pd.bdate_range("2024-01-01", "2024-01-05")  # 5 bars Mon-Fri
        return pd.DataFrame({"Close": [100.0, 101.0, 102.0, 103.0, 104.0]}, index=dates)

    def _make_delta_df(self) -> pd.DataFrame:
        dates = pd.bdate_range("2024-01-08", "2024-01-10")  # 3 bars next week
        return pd.DataFrame({"Close": [105.0, 106.0, 107.0]}, index=dates)

    def test_cold_cache_full_fetch_called(self, tmp_cache: Cache) -> None:
        initial = self._make_initial_df()
        call_log: list[dict] = []

        def fetch_fn(symbol: str, start: Optional[str] = None) -> pd.DataFrame:
            call_log.append({"symbol": symbol, "start": start})
            return initial

        result = tmp_cache.get_history_incremental(
            "AAPL", fetch_fn, cadence=Cadence.DAILY, namespace="test_hist"
        )
        assert len(call_log) == 1
        assert call_log[0]["start"] is None
        assert len(result) == 5

    def test_warm_cache_within_ttl_no_network_call(self, tmp_cache: Cache) -> None:
        initial = self._make_initial_df()
        call_log: list[dict] = []

        def fetch_fn(symbol: str, start: Optional[str] = None) -> pd.DataFrame:
            call_log.append({"symbol": symbol, "start": start})
            return initial

        # Populate cache.
        tmp_cache.get_history_incremental(
            "AAPL", fetch_fn, cadence=Cadence.DAILY, namespace="test_warm"
        )
        assert len(call_log) == 1

        # Second call within TTL: must return cached data without a network call.
        result = tmp_cache.get_history_incremental(
            "AAPL", fetch_fn, cadence=Cadence.DAILY, namespace="test_warm"
        )
        assert len(call_log) == 1, "no second network call within TTL"
        assert len(result) == 5

    def test_expired_cache_triggers_delta_fetch_with_log(
        self, tmp_cache: Cache, caplog: pytest.LogCaptureFixture
    ) -> None:
        initial = self._make_initial_df()
        delta = self._make_delta_df()
        call_log: list[dict] = []

        def fetch_fn(symbol: str, start: Optional[str] = None) -> pd.DataFrame:
            call_log.append({"symbol": symbol, "start": start})
            return initial if start is None else delta

        # Populate.
        tmp_cache.get_history_incremental(
            "AAPL", fetch_fn, cadence=Cadence.DAILY, namespace="test_delta"
        )
        assert len(call_log) == 1

        # Simulate TTL expiry by pushing last_fetched_at 25 hours into the past.
        expired_ts = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        tmp_cache._conn.execute(
            "UPDATE history_cache SET last_fetched_at=? WHERE symbol=? AND namespace=?",
            (expired_ts, "AAPL", "test_delta"),
        )

        with caplog.at_level("INFO", logger="cache.cache_store"):
            result = tmp_cache.get_history_incremental(
                "AAPL", fetch_fn, cadence=Cadence.DAILY, namespace="test_delta"
            )

        # Delta fetch should have been triggered.
        assert len(call_log) == 2
        # The start arg should be the day after the last cached bar (2024-01-05 → 2024-01-06).
        assert call_log[1]["start"] == "2024-01-06"
        # Log should contain the "delta from" message.
        delta_logs = [r.message for r in caplog.records if "delta from" in r.message.lower()]
        assert delta_logs, "expected 'Fetching history delta from' log message"

    def test_delta_merged_and_deduped(self, tmp_cache: Cache) -> None:
        initial = self._make_initial_df()
        delta = self._make_delta_df()
        call_log: list[dict] = []

        def fetch_fn(symbol: str, start: Optional[str] = None) -> pd.DataFrame:
            call_log.append(start)
            return initial if start is None else delta

        tmp_cache.get_history_incremental(
            "AAPL", fetch_fn, cadence=Cadence.DAILY, namespace="test_merge"
        )
        # Expire the cache.
        expired_ts = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        tmp_cache._conn.execute(
            "UPDATE history_cache SET last_fetched_at=? WHERE symbol=? AND namespace=?",
            (expired_ts, "AAPL", "test_merge"),
        )

        result = tmp_cache.get_history_incremental(
            "AAPL", fetch_fn, cadence=Cadence.DAILY, namespace="test_merge"
        )
        # 5 initial bars + 3 delta bars = 8 unique bars.
        assert len(result) == 8
        assert list(result.index) == sorted(result.index)

    def test_empty_delta_returns_cached_data(self, tmp_cache: Cache) -> None:
        initial = self._make_initial_df()
        call_count = {"n": 0}

        def fetch_fn(symbol: str, start: Optional[str] = None) -> pd.DataFrame:
            call_count["n"] += 1
            return initial if start is None else pd.DataFrame()

        tmp_cache.get_history_incremental(
            "AAPL", fetch_fn, cadence=Cadence.DAILY, namespace="test_empty_delta"
        )
        expired_ts = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        tmp_cache._conn.execute(
            "UPDATE history_cache SET last_fetched_at=? WHERE symbol=? AND namespace=?",
            (expired_ts, "AAPL", "test_empty_delta"),
        )

        result = tmp_cache.get_history_incremental(
            "AAPL", fetch_fn, cadence=Cadence.DAILY, namespace="test_empty_delta"
        )
        assert len(result) == 5  # same 5 bars, no new data
        assert call_count["n"] == 2

    def test_fetch_fn_without_start_kwarg_falls_back_gracefully(
        self, tmp_cache: Cache
    ) -> None:
        """If fetch_fn doesn't accept start, TypeError is caught and full fetch happens."""
        initial = self._make_initial_df()
        call_count = {"n": 0}

        def fetch_fn_no_start(symbol: str) -> pd.DataFrame:  # no start kwarg
            call_count["n"] += 1
            return initial

        tmp_cache.get_history_incremental(
            "AAPL", fetch_fn_no_start, cadence=Cadence.DAILY, namespace="test_no_start"
        )
        expired_ts = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        tmp_cache._conn.execute(
            "UPDATE history_cache SET last_fetched_at=? WHERE symbol=? AND namespace=?",
            (expired_ts, "AAPL", "test_no_start"),
        )

        result = tmp_cache.get_history_incremental(
            "AAPL", fetch_fn_no_start, cadence=Cadence.DAILY, namespace="test_no_start"
        )
        assert call_count["n"] == 2  # second call happened (fallback full fetch)
        assert len(result) == 5

    def test_history_survives_restart(self, tmp_path: Path) -> None:
        """Incremental history persists across Cache instances."""
        db = tmp_path / "hist.db"
        initial = self._make_initial_df()

        c1 = Cache(db)
        c1.get_history_incremental(
            "AAPL", lambda sym, start=None: initial, cadence=Cadence.DAILY, namespace="ns"
        )
        c1.close()

        call_count = {"n": 0}

        def fetch2(sym: str, start: Optional[str] = None) -> pd.DataFrame:
            call_count["n"] += 1
            return pd.DataFrame()

        c2 = Cache(db)
        result = c2.get_history_incremental("AAPL", fetch2, cadence=Cadence.DAILY, namespace="ns")
        c2.close()

        assert call_count["n"] == 0, "within TTL on second open: no network call"
        assert len(result) == 5

    def test_clear_history_namespace_removes_history(self, tmp_cache: Cache) -> None:
        initial = self._make_initial_df()
        tmp_cache.get_history_incremental(
            "AAPL", lambda s, start=None: initial, cadence=Cadence.DAILY, namespace="hist_ns"
        )
        tmp_cache.clear("hist_ns")

        call_count = {"n": 0}

        def fresh_fetch(sym: str, start: Optional[str] = None) -> pd.DataFrame:
            call_count["n"] += 1
            return initial

        tmp_cache.get_history_incremental(
            "AAPL", fresh_fetch, cadence=Cadence.DAILY, namespace="hist_ns"
        )
        assert call_count["n"] == 1, "after clear, cache is cold again"


# ─────────────────────────────────────────────────────────────────────────────
# Cadence registry & TTL completeness
# ─────────────────────────────────────────────────────────────────────────────

class TestCadenceRegistry:
    def test_cadence_ttl_covers_all_cadences(self) -> None:
        for cadence in Cadence:
            assert cadence in CADENCE_TTL, f"CADENCE_TTL missing {cadence}"
            assert CADENCE_TTL[cadence].total_seconds() > 0

    def test_cadence_registry_has_required_keys(self) -> None:
        required = {
            "quotes",
            "daily_bars",
            "fundamentals",
            "financials",
            "dividends_meta",
            "analyst_ratings",
            "earnings_calendar",
            "company_profile",
            "macro_regime_inputs",
        }
        missing = required - set(CADENCE_REGISTRY)
        assert not missing, f"CADENCE_REGISTRY missing keys: {missing}"

    def test_registry_values_are_cadence_instances(self) -> None:
        for k, v in CADENCE_REGISTRY.items():
            assert isinstance(v, Cadence), f"CADENCE_REGISTRY[{k!r}] is not a Cadence"

    def test_ttl_ordering_makes_sense(self) -> None:
        """Coarser cadences must have longer TTLs."""
        assert CADENCE_TTL[Cadence.INTRADAY] < CADENCE_TTL[Cadence.DAILY]
        assert CADENCE_TTL[Cadence.DAILY] < CADENCE_TTL[Cadence.WEEKLY]
        assert CADENCE_TTL[Cadence.WEEKLY] < CADENCE_TTL[Cadence.MONTHLY]
        assert CADENCE_TTL[Cadence.MONTHLY] < CADENCE_TTL[Cadence.QUARTERLY]
        assert CADENCE_TTL[Cadence.QUARTERLY] < CADENCE_TTL[Cadence.YEARLY]


# ─────────────────────────────────────────────────────────────────────────────
# Thread safety (light smoke test)
# ─────────────────────────────────────────────────────────────────────────────

class TestThreadSafety:
    def test_concurrent_reads_do_not_corrupt(self, tmp_cache: Cache) -> None:
        """Many threads reading simultaneously must all get the same value."""
        tmp_cache.set("ns", "shared", {"ok": True}, Cadence.DAILY)

        results: list[Optional[CacheEntry]] = []
        lock = threading.Lock()

        def reader() -> None:
            entry = tmp_cache.get("ns", "shared")
            with lock:
                results.append(entry)

        threads = [threading.Thread(target=reader) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(e is not None and e.value == {"ok": True} for e in results), (
            "all 20 concurrent reads should return the correct value"
        )

    def test_concurrent_writes_do_not_corrupt(self, tmp_cache: Cache) -> None:
        """Many threads writing different keys must all succeed without error."""
        errors: list[Exception] = []

        def writer(i: int) -> None:
            try:
                tmp_cache.set("ns", f"key_{i}", i, Cadence.DAILY)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"concurrent writes raised: {errors}"
        for i in range(20):
            entry = tmp_cache.get("ns", f"key_{i}")
            assert entry is not None and entry.value == i
