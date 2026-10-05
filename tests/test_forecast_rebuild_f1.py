"""
tests/test_forecast_rebuild_f1.py
=================================
Forecasting rebuild F1 ("measure what we publish"). See
``.claude/forecasting_rebuild_implementation_plan.md``.

* the one-row-per-(symbol, model, horizon, US/Eastern day) upsert in
  ``ForecastTracker.record_forecasts`` and its additive migration;
* the skill-weight exclusion of measurement-only names, including a
  byte-identity check against a verbatim copy of the pre-F1 formula;
* ``compute_skill_vs_naive`` / ``ForecastTracker.skill_vs_naive`` on a
  synthetic ground truth;
* ``scripts/clean_forecast_ledger.py`` (dry run + ``--apply`` on a temp DB)
  and ``scripts/forecast_skill_report.py``.

Every test writes to its own temp SQLite file; nothing touches the real
ledger.
"""
from __future__ import annotations

import math
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Tuple

import pytest

from forecasting.forecast_tracker import (
    MODEL_ARIMA,
    MODEL_BLEND,
    MODEL_HOLT_WINTERS,
    MODEL_MONTE_CARLO,
    MODEL_NAIVE,
    NON_BLEND_MODEL_NAMES,
    ForecastTracker,
    _MIN_MSE,
    _binomial_two_sided_p,
    compute_skill_vs_naive,
    compute_skill_weights_from_stats,
    eastern_trading_day,
)


def _tracker(tmp_path, name="t.db") -> ForecastTracker:
    return ForecastTracker(db_path=str(tmp_path / name))


def _rows(db: str, sql: str, params=()):
    with sqlite3.connect(db) as conn:
        return conn.execute(sql, params).fetchall()


# ---------------------------------------------------------------------------
# eastern_trading_day
# ---------------------------------------------------------------------------

class TestEasternTradingDay:
    def test_evening_et_cycle_keeps_its_et_date(self):
        # 01:00 UTC on the 28th is 21:00 EDT on the 27th.
        assert eastern_trading_day("2026-09-28T01:00:00+00:00") == "2026-09-27"

    def test_naive_datetime_is_treated_as_utc(self):
        assert eastern_trading_day(datetime(2026, 9, 28, 1, 0)) == "2026-09-27"

    def test_winter_offset_is_five_hours(self):
        # 04:30 UTC in January is 23:30 EST the previous day.
        assert eastern_trading_day("2026-01-15T04:30:00+00:00") == "2026-01-14"
        assert eastern_trading_day("2026-01-15T05:30:00+00:00") == "2026-01-15"

    def test_unparseable_is_none(self):
        assert eastern_trading_day("not a timestamp") is None


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------

class TestRecordForecastsUpsert:
    def test_repeated_same_day_records_keep_one_row_with_latest_values(self, tmp_path):
        t = _tracker(tmp_path)
        base = datetime(2026, 9, 21, 14, 0, tzinfo=timezone.utc)
        for i, price in enumerate([100.0, 101.0, 102.5]):
            t.record_forecasts(
                "AAPL", 30, {MODEL_MONTE_CARLO: price, MODEL_ARIMA: price + 1}, base + timedelta(hours=i),
                model_bounds={MODEL_MONTE_CARLO: (price - 5, price + 5)},
            )
        rows = _rows(
            t._db_path,
            "SELECT model_name, forecast_price, forecast_lower, forecast_upper, forecast_ts, forecast_day "
            "FROM forecast_errors ORDER BY model_name",
        )
        assert len(rows) == 2
        by_model = {r[0]: r for r in rows}
        mc = by_model[MODEL_MONTE_CARLO]
        assert mc[1] == 102.5
        assert (mc[2], mc[3]) == (97.5, 107.5)
        assert mc[4] == (base + timedelta(hours=2)).isoformat()
        assert mc[5] == "2026-09-21"
        assert by_model[MODEL_ARIMA][1] == 103.5
        assert t.pending_count("AAPL", 30) == 2  # one per model

    def test_different_horizon_and_symbol_are_separate_keys(self, tmp_path):
        t = _tracker(tmp_path)
        ts = datetime(2026, 9, 21, 14, 0, tzinfo=timezone.utc)
        t.record_forecasts("AAPL", 30, {MODEL_ARIMA: 1.0}, ts)
        t.record_forecasts("AAPL", 10, {MODEL_ARIMA: 1.0}, ts)
        t.record_forecasts("MSFT", 30, {MODEL_ARIMA: 1.0}, ts)
        assert _rows(t._db_path, "SELECT COUNT(*) FROM forecast_errors")[0][0] == 3

    def test_new_trading_day_adds_a_row(self, tmp_path):
        t = _tracker(tmp_path)
        d1 = datetime(2026, 9, 21, 15, 0, tzinfo=timezone.utc)
        t.record_forecasts("AAPL", 30, {MODEL_ARIMA: 100.0}, d1)
        t.record_forecasts("AAPL", 30, {MODEL_ARIMA: 101.0}, d1 + timedelta(days=1))
        rows = _rows(t._db_path, "SELECT forecast_day, forecast_price FROM forecast_errors ORDER BY id")
        assert rows == [("2026-09-21", 100.0), ("2026-09-22", 101.0)]

    def test_et_day_boundary_not_utc(self, tmp_path):
        """20:00 ET and 23:30 ET on the same ET day are one key even though
        the second is already the next UTC day."""
        t = _tracker(tmp_path)
        t.record_forecasts("AAPL", 30, {MODEL_ARIMA: 100.0}, datetime(2026, 9, 22, 0, 0, tzinfo=timezone.utc))
        t.record_forecasts("AAPL", 30, {MODEL_ARIMA: 99.0}, datetime(2026, 9, 22, 3, 30, tzinfo=timezone.utc))
        rows = _rows(t._db_path, "SELECT forecast_day, forecast_price FROM forecast_errors")
        assert rows == [("2026-09-21", 99.0)]

    def test_matured_row_is_never_overwritten(self, tmp_path):
        t = _tracker(tmp_path)
        ts = datetime(2026, 9, 21, 14, 0, tzinfo=timezone.utc)
        t.record_forecasts("AAPL", 30, {MODEL_ARIMA: 100.0}, ts)
        with sqlite3.connect(t._db_path) as conn:
            conn.execute("UPDATE forecast_errors SET actual_price = 110.0, squared_error = 100.0")
        t.record_forecasts("AAPL", 30, {MODEL_ARIMA: 555.0}, ts + timedelta(hours=1))
        rows = _rows(
            t._db_path,
            "SELECT forecast_price, actual_price, squared_error FROM forecast_errors ORDER BY id",
        )
        assert rows[0] == (100.0, 110.0, 100.0)  # untouched
        assert rows[1] == (555.0, None, None)    # new pending row
        assert len(rows) == 2

    def test_legacy_null_day_rows_are_left_alone(self, tmp_path):
        t = _tracker(tmp_path)
        ts = datetime(2026, 9, 21, 14, 0, tzinfo=timezone.utc)
        with sqlite3.connect(t._db_path) as conn:
            conn.execute(
                "INSERT INTO forecast_errors (symbol, model_name, horizon_days, forecast_ts, "
                "forecast_price, recorded_at) VALUES ('AAPL', 'arima', 30, ?, 42.0, ?)",
                (ts.isoformat(), ts.isoformat()),
            )
        t.record_forecasts("AAPL", 30, {MODEL_ARIMA: 100.0}, ts + timedelta(hours=1))
        rows = _rows(t._db_path, "SELECT forecast_price, forecast_day FROM forecast_errors ORDER BY id")
        assert rows == [(42.0, None), (100.0, "2026-09-21")]

    def test_upserted_row_actualizes_normally(self, tmp_path, monkeypatch):
        from settings import settings as _settings

        monkeypatch.setattr(_settings, "FORECAST_TRACKER_DUE_DATE_LOOKUP_ENABLED", False)
        t = _tracker(tmp_path)
        made = datetime.now(timezone.utc) - timedelta(days=60)
        t.record_forecasts("AAPL", 10, {MODEL_ARIMA: 100.0}, made)
        t.record_forecasts("AAPL", 10, {MODEL_ARIMA: 104.0}, made + timedelta(minutes=5))
        assert t.update_actuals("AAPL", 10, 110.0, datetime.now(timezone.utc)) == 1
        assert _rows(t._db_path, "SELECT forecast_price, squared_error FROM forecast_errors") == [(104.0, 36.0)]
        assert t.completed_count("AAPL", 10, window_days=365) == 1
        assert t.pending_count("AAPL", 10) == 0
        assert "AAPL" in t.get_covered_symbols(window_days=None)

    def test_day_index_exists(self, tmp_path):
        t = _tracker(tmp_path)
        idx = _rows(t._db_path, "SELECT sql FROM sqlite_master WHERE name = 'idx_fe_symbol_model_horizon_day'")
        assert idx and "forecast_day" in idx[0][0] and "WHERE forecast_day IS NOT NULL" in idx[0][0]

    def test_update_uses_the_day_index(self, tmp_path):
        t = _tracker(tmp_path)
        plan = _rows(
            t._db_path,
            "EXPLAIN QUERY PLAN UPDATE forecast_errors SET forecast_price = 1 "
            "WHERE symbol = 'A' AND model_name = 'arima' AND horizon_days = 30 "
            "AND forecast_day = '2026-09-21' AND actual_price IS NULL",
        )
        assert any("idx_fe_symbol_model_horizon_day" in str(r) for r in plan)


class TestMigrationOnPreF1Schema:
    _PRE_F1_DDL = """
    CREATE TABLE forecast_errors (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol         TEXT    NOT NULL,
        model_name     TEXT    NOT NULL,
        horizon_days   INTEGER NOT NULL,
        forecast_ts    TEXT    NOT NULL,
        forecast_price REAL    NOT NULL,
        actual_price   REAL,
        squared_error  REAL,
        recorded_at    TEXT    NOT NULL,
        forecast_lower REAL,
        forecast_upper REAL
    )
    """

    def test_adds_column_and_index_and_keeps_rows(self, tmp_path):
        db = str(tmp_path / "pre_f1.db")
        ts = datetime(2026, 9, 21, 14, 0, tzinfo=timezone.utc)
        with sqlite3.connect(db) as conn:
            conn.execute(self._PRE_F1_DDL)
            conn.execute(
                "INSERT INTO forecast_errors (symbol, model_name, horizon_days, forecast_ts, "
                "forecast_price, recorded_at) VALUES ('AAPL', 'arima', 30, ?, 50.0, ?)",
                (ts.isoformat(), ts.isoformat()),
            )
        t = ForecastTracker(db_path=db)
        cols = {r[1] for r in _rows(db, "PRAGMA table_info(forecast_errors)")}
        assert "forecast_day" in cols
        assert _rows(db, "SELECT forecast_price, forecast_day FROM forecast_errors") == [(50.0, None)]
        assert _rows(db, "SELECT name FROM sqlite_master WHERE name = 'idx_fe_symbol_model_horizon_day'")
        # Upsert on the migrated table: legacy row untouched, new row keyed.
        t.record_forecasts("AAPL", 30, {MODEL_ARIMA: 60.0}, ts)
        t.record_forecasts("AAPL", 30, {MODEL_ARIMA: 61.0}, ts + timedelta(hours=1))
        assert _rows(db, "SELECT forecast_price, forecast_day FROM forecast_errors ORDER BY id") == [
            (50.0, None), (61.0, "2026-09-21"),
        ]
        # A second construction is a no-op.
        ForecastTracker(db_path=db)


# ---------------------------------------------------------------------------
# Skill-weight exclusion: byte-identical to the pre-F1 formula
# ---------------------------------------------------------------------------

def _pre_f1_compute_skill_weights_from_stats(
    model_stats: Dict[str, Tuple[int, float]], min_obs: int
) -> Dict[str, float]:
    """Verbatim copy of compute_skill_weights_from_stats as of origin/main
    before F1 (96f81c44)."""
    if not model_stats:
        return {}
    mature = {name: stats for name, stats in model_stats.items() if stats[0] >= min_obs}
    if not mature:
        n_models = len(model_stats)
        return {name: 1.0 / n_models for name in model_stats}
    inv_mse: Dict[str, float] = {}
    for name, (_, mse) in mature.items():
        mse = max(0.0, mse)
        inv_mse[name] = 1.0 / max(mse, _MIN_MSE)
    total = sum(inv_mse.values())
    if total <= 0:
        n_mature = len(inv_mse)
        return {name: 1.0 / n_mature for name in inv_mse}
    return {name: w / total for name, w in inv_mse.items()}


class TestSkillWeightExclusion:
    _REAL = ("arima", "monte_carlo", "holt_winters", "cnn_lstm", "prophet")

    def test_byte_identical_to_pre_f1_outside_the_leak_regime(self):
        """For every pre-F1-possible ledger state (real models + naive, no
        blend) EXCEPT the leak regime, the weights are exactly (==) what the
        pre-F1 formula returned. The leak regime is: naive mature while no
        real model is."""
        rng = random.Random(7)
        checked = 0
        for _ in range(5000):
            stats = {}
            for name in self._REAL + ("naive",):
                if rng.random() < 0.8:
                    stats[name] = (rng.choice([0, 3, 29, 30, 31, 200]), rng.choice([0.0, 1e-6, 0.5, 3.0, 250.0]))
            mature_real = any(n >= 30 for name, (n, _) in stats.items() if name != "naive")
            naive_mature = stats.get("naive", (0, 0))[0] >= 30
            if naive_mature and not mature_real:
                continue
            assert compute_skill_weights_from_stats(stats, 30) == _pre_f1_compute_skill_weights_from_stats(stats, 30)
            checked += 1
        assert checked > 3000

    def test_blend_rows_never_change_the_weights_of_real_models(self):
        rng = random.Random(11)
        for _ in range(2000):
            stats = {name: (rng.choice([3, 30, 200]), rng.choice([0.5, 3.0, 250.0])) for name in self._REAL}
            stats["naive"] = (rng.choice([3, 30, 200]), 1.0)
            with_blend = dict(stats, blend=(rng.choice([3, 30, 500]), rng.choice([1e-6, 0.01, 999.0])))
            assert compute_skill_weights_from_stats(with_blend, 30) == compute_skill_weights_from_stats(stats, 30)
            assert MODEL_BLEND not in compute_skill_weights_from_stats(with_blend, 30)

    def test_leak_regime_is_the_cold_start_not_naive_only(self):
        stats = {"arima": (5, 4.0), "monte_carlo": (5, 1.0), "naive": (80, 0.01)}
        pre = _pre_f1_compute_skill_weights_from_stats(stats, 30)
        assert pre == {"naive": 1.0}  # the pre-F1 leak
        now = compute_skill_weights_from_stats(stats, 30)
        assert now == {"arima": 1 / 3, "monte_carlo": 1 / 3, "naive": 1 / 3}

    def test_only_measurement_names(self):
        assert compute_skill_weights_from_stats({"blend": (100, 1.0)}, 30) == {}
        assert NON_BLEND_MODEL_NAMES >= {MODEL_NAIVE, MODEL_BLEND}

    def test_get_skill_weights_ignores_blend_rows(self, tmp_path):
        t = _tracker(tmp_path)
        now = datetime.now(timezone.utc)
        with sqlite3.connect(t._db_path) as conn:
            for i in range(40):
                ts = (now - timedelta(days=5 + i)).isoformat()
                for model, sq in ((MODEL_ARIMA, 4.0), (MODEL_HOLT_WINTERS, 1.0), (MODEL_BLEND, 1e-6)):
                    conn.execute(
                        "INSERT INTO forecast_errors (symbol, model_name, horizon_days, forecast_ts, "
                        "forecast_price, actual_price, squared_error, recorded_at) VALUES "
                        "('AAPL', ?, 30, ?, 1, 1, ?, ?)",
                        (model, ts, sq, ts),
                    )
        w = t.get_skill_weights("AAPL", 30, window_days=365, min_obs=30)
        assert set(w) == {MODEL_ARIMA, MODEL_HOLT_WINTERS}
        assert w[MODEL_HOLT_WINTERS] == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# Skill vs naive
# ---------------------------------------------------------------------------

class TestBinomialSignTest:
    @pytest.mark.parametrize("k,n", [(0, 1), (3, 10), (5, 10), (17, 40), (0, 25), (60, 100)])
    def test_exact_fallback_matches_scipy(self, k, n, monkeypatch):
        from scipy.stats import binomtest

        expected = binomtest(k, n, 0.5).pvalue
        import builtins

        real_import = builtins.__import__

        def _no_scipy(name, *args, **kwargs):
            if name.startswith("scipy"):
                raise ImportError("scipy hidden for this test")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _no_scipy)
        assert _binomial_two_sided_p(k, n) == pytest.approx(expected, rel=1e-12)

    def test_zero_trials(self):
        assert _binomial_two_sided_p(0, 0) is None


def _synthetic_pairs(n: int = 400, seed: int = 3):
    """Ground truth: realized log-return r ~ N(0, 0.06). ``good`` sees 80% of
    the move plus small noise, so it beats naive and gets the direction
    right; ``bad`` is pure noise twice the size of the move, so it loses."""
    rng = random.Random(seed)
    pairs = []
    for _ in range(n):
        p0 = rng.uniform(5.0, 200.0)
        r = rng.gauss(0.0, 0.06)
        actual = p0 * math.exp(r)
        good = p0 * math.exp(0.8 * r + rng.gauss(0.0, 0.005))
        bad = p0 * math.exp(rng.gauss(0.0, 0.12))
        pairs.append(("good", good, actual, p0, actual))
        pairs.append(("bad", bad, actual, p0, actual))
        pairs.append((MODEL_NAIVE, p0, actual, p0, actual))
    return pairs


class TestComputeSkillVsNaive:
    def test_synthetic_ground_truth(self):
        res = compute_skill_vs_naive(_synthetic_pairs())
        assert set(res) == {"good", "bad"}  # naive never scored against itself
        good, bad = res["good"], res["bad"]
        assert good["n"] == bad["n"] == 400
        assert good["naive_median_abs_log_error"] == bad["naive_median_abs_log_error"]
        assert good["median_abs_log_error"] < good["naive_median_abs_log_error"]
        assert good["pct_beating_naive"] > 0.9
        assert good["sign_test_p"] < 1e-6
        assert good["direction_hit_rate"] > 0.9
        assert bad["median_abs_log_error"] > bad["naive_median_abs_log_error"]
        assert bad["pct_beating_naive"] < 0.4
        assert bad["sign_test_p"] < 1e-3  # significantly WORSE
        assert 0.4 < bad["direction_hit_rate"] < 0.6  # coin flip
        assert good["wins"] + good["losses"] + good["ties"] == good["n"]

    def test_hand_computed_values(self):
        pairs = [
            ("m", 110.0, 110.0, 100.0, 110.0),  # perfect, beats naive, right direction
            ("m", 90.0, 110.0, 100.0, 110.0),   # wrong direction, loses
            ("m", 100.0, 100.0, 100.0, 100.0),  # tie; no direction (flat)
        ]
        r = compute_skill_vs_naive(pairs)["m"]
        assert r["n"] == 3
        assert (r["wins"], r["losses"], r["ties"]) == (1, 1, 1)
        assert r["pct_beating_naive"] == pytest.approx(1 / 3)
        assert r["direction_n"] == 2
        assert r["direction_hit_rate"] == 0.5
        assert r["median_abs_log_error"] == pytest.approx(0.0)
        assert r["naive_median_abs_log_error"] == pytest.approx(abs(math.log(100 / 110)))
        assert r["sign_test_p"] == pytest.approx(1.0)

    def test_sub_dollar_and_non_positive_prices_excluded(self):
        pairs = [
            ("m", 0.5, 0.6, 0.55, 0.6),   # naive below $1
            ("m", 0.0, 10.0, 10.0, 10.0),  # non-positive forecast
            ("m", 10.0, -1.0, 10.0, 10.0),  # non-positive actual
            ("m", float("nan"), 10.0, 10.0, 10.0),
            ("m", 11.0, 12.0, 10.0, 12.0),  # the only scorable pair
        ]
        assert compute_skill_vs_naive(pairs)["m"]["n"] == 1
        assert compute_skill_vs_naive(pairs, min_price=20.0) == {}

    def test_empty(self):
        assert compute_skill_vs_naive([]) == {}


def _insert(db, symbol, model, h, ts, price, actual, day=None):
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO forecast_errors (symbol, model_name, horizon_days, forecast_ts, forecast_price, "
            "actual_price, squared_error, recorded_at, forecast_day) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (symbol, model, h, ts, price, actual, None if actual is None else (actual - price) ** 2, ts, day),
        )


class TestTrackerSkillVsNaive:
    def test_pairs_by_day_and_counts_legacy_duplicates_once(self, tmp_path):
        t = _tracker(tmp_path)
        db = t._db_path
        day = datetime.now(timezone.utc).replace(hour=15, minute=0, second=0, microsecond=0) - timedelta(days=20)
        # Legacy (NULL forecast_day) hourly duplicates on one ET day: only the
        # LAST (latest ts) row per (symbol, model) counts.
        _insert(db, "AAPL", MODEL_ARIMA, 10, day.isoformat(), 50.0, 110.0)          # stale, bad
        _insert(db, "AAPL", MODEL_ARIMA, 10, (day + timedelta(hours=2)).isoformat(), 109.0, 110.0)
        _insert(db, "AAPL", MODEL_NAIVE, 10, day.isoformat(), 90.0, 110.0)
        _insert(db, "AAPL", MODEL_NAIVE, 10, (day + timedelta(hours=2)).isoformat(), 100.0, 110.0)
        # F1-keyed rows on another day.
        d2 = day + timedelta(days=1)
        _insert(db, "AAPL", MODEL_ARIMA, 10, d2.isoformat(), 125.0, 110.0, eastern_trading_day(d2))
        _insert(db, "AAPL", MODEL_NAIVE, 10, d2.isoformat(), 100.0, 110.0, eastern_trading_day(d2))
        # A model row with no naive partner on its day is not scored.
        d3 = day + timedelta(days=2)
        _insert(db, "AAPL", MODEL_ARIMA, 10, d3.isoformat(), 110.0, 110.0, eastern_trading_day(d3))
        # Pending rows are ignored.
        _insert(db, "AAPL", MODEL_ARIMA, 10, d3.isoformat(), 1.0, None, eastern_trading_day(d3))

        res = t.skill_vs_naive(10, window_days=365)
        arima = res["models"][MODEL_ARIMA]
        assert arima["n"] == 2
        assert arima["wins"] == 1 and arima["losses"] == 1
        assert arima["median_abs_log_error"] == pytest.approx(
            (abs(math.log(109 / 110)) + abs(math.log(125 / 110))) / 2
        )
        assert res["reason"] is None

    def test_no_naive_rows_is_an_honest_empty(self, tmp_path):
        t = _tracker(tmp_path)
        ts = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
        _insert(t._db_path, "AAPL", MODEL_ARIMA, 30, ts, 100.0, 101.0)
        res = t.skill_vs_naive(30, window_days=365)
        assert res["models"] == {}
        assert "naive" in res["reason"]

    def test_readonly_and_missing_table_never_raise(self, tmp_path):
        db = tmp_path / "none.db"
        sqlite3.connect(str(db)).close()
        res = ForecastTracker(db_path=str(db), readonly=True).skill_vs_naive(10, 30)
        assert res["models"] == {} and res["reason"] == "error"


# ---------------------------------------------------------------------------
# scripts/clean_forecast_ledger.py
# ---------------------------------------------------------------------------

def _seed_ledger(db: str) -> None:
    ForecastTracker(db_path=db)
    now = datetime.now(timezone.utc)
    # (a) TEST rows
    for i in range(3):
        # All on one US/Eastern day, so (c) alone would collapse them to 1.
        _insert(db, "TEST", MODEL_ARIMA, 10, f"2026-09-01T14:0{i}:00+00:00", 1.0, None)
    # (b) 2026-08-14 seed-bug cycle for SEED (priced ~$20) and a healthy cycle
    # for PENNY whose 90-day ARIMA/HW trended far below its MC.
    bug = "2026-08-14T14:00:00+00:00"
    for h, mc in ((10, 99.0), (30, 98.0)):
        _insert(db, "SEED", MODEL_MONTE_CARLO, h, bug, mc, None)
        _insert(db, "SEED", MODEL_ARIMA, h, bug, 20.0, None)
        _insert(db, "SEED", MODEL_HOLT_WINTERS, h, bug, 20.5, None)
    for h, a, hw, mc in ((10, 1.48, 1.47, 1.55), (90, 0.23, 0.21, 1.04)):
        _insert(db, "PENNY", MODEL_ARIMA, h, bug, a, None)
        _insert(db, "PENNY", MODEL_HOLT_WINTERS, h, bug, hw, None)
        _insert(db, "PENNY", MODEL_MONTE_CARLO, h, bug, mc, None)
    # (c) intra-day duplicates: 5 completed arima rows on one ET day for KEEP.
    day = (now - timedelta(days=40)).replace(hour=14, minute=0, second=0, microsecond=0)
    for i in range(5):
        _insert(db, "KEEP", MODEL_ARIMA, 10, (day + timedelta(hours=i)).isoformat(), 100.0 + i, 105.0)


class TestCleanForecastLedger:
    def test_dry_run_counts_and_does_not_write(self, tmp_path):
        from scripts import clean_forecast_ledger as clf

        db = str(tmp_path / "ledger.db")
        _seed_ledger(db)
        before = _rows(db, "SELECT COUNT(*) FROM forecast_errors")[0][0]
        conn = clf._connect(db, readonly=True)
        try:
            summary, excluded = clf.analyze(conn, window_days=365, min_obs=3, categories=("a", "b", "c"))
        finally:
            conn.close()
        assert summary["categories"] == ["a", "b", "c"]
        assert summary["a_test_symbol_rows"] == 3
        assert summary["b_mc_seed_rows"] == 2
        assert summary["b_mc_seed_rows_by_symbol"] == {"SEED": 2}  # PENNY's healthy MC kept
        # TEST: 3 rows (one ET day) are excluded by (a), so (c) never sees them.
        # KEEP: 5 rows on one ET day -> 4 duplicates.
        assert summary["c_intraday_duplicate_rows"] == 4
        assert summary["rows_deleted_total"] == 3 + 2 + 4
        assert summary["rows_after_cleanup"] == before - 9
        cs = summary["cold_start"]
        # KEEP/arima@10 had 5 completed rows (mature at min_obs=3) -> 1 after.
        assert cs["keys_falling_below_min_obs"] == 1
        assert cs["symbol_horizon_pairs_dropping_to_cold_start"] == 1
        assert _rows(db, "SELECT COUNT(*) FROM forecast_errors")[0][0] == before

    def test_main_dry_run_prints_and_changes_nothing(self, tmp_path, capsys):
        from scripts import clean_forecast_ledger as clf

        db = str(tmp_path / "ledger.db")
        _seed_ledger(db)
        before = _rows(db, "SELECT COUNT(*) FROM forecast_errors")[0][0]
        assert clf.main(["--db", db, "--window-days", "365", "--min-obs", "3"]) == 0
        out = capsys.readouterr().out
        assert "DRY RUN" in out and "2026-08-14" in out
        assert _rows(db, "SELECT COUNT(*) FROM forecast_errors")[0][0] == before

    def test_apply_with_c_backs_up_then_deletes_in_one_transaction(self, tmp_path):
        from scripts import clean_forecast_ledger as clf

        db = str(tmp_path / "ledger.db")
        _seed_ledger(db)
        before = _rows(db, "SELECT COUNT(*) FROM forecast_errors")[0][0]
        backups = tmp_path / "backups"
        summary = clf.apply_cleanup(
            db, window_days=365, min_obs=3, backup_dir=backups, categories=("a", "b", "c"),
        )
        files = list(backups.iterdir())
        assert len(files) == 1 and Path(summary["backup_path"]) == files[0]
        assert _rows(str(files[0]), "SELECT COUNT(*) FROM forecast_errors")[0][0] == before
        assert summary["rows_remaining"] == before - 9
        assert _rows(db, "SELECT COUNT(*) FROM forecast_errors WHERE symbol = 'TEST'")[0][0] == 0
        # The LAST KEEP row of the day survives, with its day key backfilled.
        keep = _rows(db, "SELECT forecast_price, forecast_day FROM forecast_errors WHERE symbol = 'KEEP'")
        assert len(keep) == 1 and keep[0][0] == 104.0 and keep[0][1] is not None
        assert _rows(db, "SELECT COUNT(*) FROM forecast_errors WHERE forecast_day IS NULL")[0][0] == 0

    def test_apply_refuses_when_backup_fails(self, tmp_path, monkeypatch):
        from scripts import clean_forecast_ledger as clf

        db = str(tmp_path / "ledger.db")
        _seed_ledger(db)
        before = _rows(db, "SELECT COUNT(*) FROM forecast_errors")[0][0]

        def _boom(*_a, **_k):
            raise OSError("disk full")

        monkeypatch.setattr(clf, "make_backup", _boom)
        with pytest.raises(OSError):
            clf.apply_cleanup(db, window_days=365, min_obs=3, backup_dir=tmp_path / "b")
        assert _rows(db, "SELECT COUNT(*) FROM forecast_errors")[0][0] == before

    def test_apply_rolls_back_on_mid_transaction_failure(self, tmp_path, monkeypatch):
        from scripts import clean_forecast_ledger as clf

        db = str(tmp_path / "ledger.db")
        _seed_ledger(db)
        before = _rows(db, "SELECT COUNT(*) FROM forecast_errors")[0][0]
        real_analyze = clf.analyze

        def _lying_analyze(conn, w, m, cats, **kw):
            summary, excluded = real_analyze(conn, w, m, cats, **kw)
            summary["rows_after_cleanup"] = -1  # forces the post-delete check to fail
            return summary, excluded

        monkeypatch.setattr(clf, "analyze", _lying_analyze)
        with pytest.raises(RuntimeError):
            clf.apply_cleanup(db, window_days=365, min_obs=3, backup_dir=tmp_path / "b")
        assert _rows(db, "SELECT COUNT(*) FROM forecast_errors")[0][0] == before

    def test_et_day_cache_matches_full_parse(self):
        from scripts.clean_forecast_ledger import et_day

        for s in ("2026-09-28T01:05:03.123456+00:00", "2026-09-27T23:59:59+00:00",
                  "2026-01-15T04:30:00", "2026-03-08T06:59:00+00:00", "2026-03-08T07:01:00+00:00"):
            assert et_day(s) == eastern_trading_day(s)


class TestCleanForecastLedgerCategories:
    """Category (c) is opt-in: the default is a,b, and every total and the
    cold-start estimate reflect only the selected categories."""

    @staticmethod
    def _analyze(db, categories=None):
        from scripts import clean_forecast_ledger as clf

        conn = clf._connect(db, readonly=True)
        try:
            if categories is None:
                return clf.analyze(conn, window_days=365, min_obs=3)[0]
            return clf.analyze(conn, window_days=365, min_obs=3, categories=categories)[0]
        finally:
            conn.close()

    def test_parse_categories(self):
        from scripts.clean_forecast_ledger import DEFAULT_CATEGORIES, parse_categories

        assert DEFAULT_CATEGORIES == ("a", "b")
        assert parse_categories(None) == ("a", "b")
        assert parse_categories("c, A,b,c") == ("a", "b", "c")
        with pytest.raises(ValueError):
            parse_categories("a,e")
        with pytest.raises(ValueError):
            parse_categories(" , ")

    def test_default_excludes_c(self, tmp_path):
        db = str(tmp_path / "ledger.db")
        _seed_ledger(db)
        before = _rows(db, "SELECT COUNT(*) FROM forecast_errors")[0][0]
        summary = self._analyze(db)
        assert summary["categories"] == ["a", "b"]
        assert summary["c_intraday_duplicate_rows"] is None
        assert summary["rows_deleted_total"] == 3 + 2
        assert summary["rows_after_cleanup"] == before - 5
        cs = summary["cold_start"]
        # Only TEST is removed; KEEP's 5 completed rows all stay mature.
        assert cs["keys_falling_below_min_obs"] == 0
        assert cs["symbol_horizon_pairs_dropping_to_cold_start"] == 0
        assert not any("CATEGORY (c)" in w for w in summary["warnings"])

    def test_c_opt_in_adds_duplicates_and_warning_from_real_numbers(self, tmp_path):
        db = str(tmp_path / "ledger.db")
        _seed_ledger(db)
        summary = self._analyze(db, ("a", "b", "c"))
        assert summary["c_intraday_duplicate_rows"] == 4
        assert summary["rows_deleted_total"] == 3 + 2 + 4
        cs = summary["cold_start"]
        assert cs["symbol_horizon_pairs_dropping_to_cold_start"] == 1
        (warning,) = [w for w in summary["warnings"] if "CATEGORY (c)" in w]
        assert (
            f"{cs['symbol_horizon_pairs_dropping_to_cold_start']:,} of "
            f"{cs['symbol_horizon_pairs_with_mature_model_before']:,} warm" in warning
        )
        assert "F3" in warning

    @pytest.mark.parametrize(
        "cats,deleted",
        [
            (("a",), 3), (("b",), 2), (("a", "b"), 5),
            # c alone: KEEP's 4 duplicates + TEST's 3 same-day rows collapse to 1.
            (("c",), 4 + 2),
            (("a", "c"), 3 + 4), (("a", "b", "c"), 9),
        ],
    )
    def test_totals_per_selection(self, tmp_path, cats, deleted):
        db = str(tmp_path / "ledger.db")
        _seed_ledger(db)
        before = _rows(db, "SELECT COUNT(*) FROM forecast_errors")[0][0]
        summary = self._analyze(db, cats)
        assert summary["rows_deleted_total"] == deleted
        assert summary["rows_after_cleanup"] == before - deleted
        # a/b are always counted for information, whether or not selected.
        assert summary["a_test_symbol_rows"] == 3 and summary["b_mc_seed_rows"] == 2

    def test_c_without_a_counts_test_duplicates(self, tmp_path):
        """With (a) unselected, TEST rows stay and take part in the dedup."""
        db = str(tmp_path / "ledger.db")
        _seed_ledger(db)
        summary = self._analyze(db, ("c",))
        # KEEP: 4 duplicates; TEST: 3 same-ET-day rows collapse to 1; the
        # seed ledger's other symbols have one row per key.
        assert summary["c_intraday_duplicate_rows"] == 4 + 2
        assert summary["a_test_symbol_rows"] == 3  # counted, not deleted

    def test_main_default_prints_c_not_selected_and_no_c_warning(self, tmp_path, capsys):
        from scripts import clean_forecast_ledger as clf

        db = str(tmp_path / "ledger.db")
        _seed_ledger(db)
        assert clf.main(["--db", db, "--window-days", "365", "--min-obs", "3"]) == 0
        out = capsys.readouterr().out
        assert "categories: a,b" in out
        assert "not selected" in out
        assert "CATEGORY (c)" not in out

    def test_main_with_c_prints_warning(self, tmp_path, capsys):
        from scripts import clean_forecast_ledger as clf

        db = str(tmp_path / "ledger.db")
        _seed_ledger(db)
        assert clf.main(["--db", db, "--window-days", "365", "--min-obs", "3", "--categories", "a,b,c"]) == 0
        out = capsys.readouterr().out
        assert out.count("CATEGORY (c) IS A LIVE DECISION CHANGE") == 2  # top and bottom
        assert "1 of 1 warm (symbol, horizon) pairs" in out

    def test_main_rejects_unknown_category(self, tmp_path):
        from scripts import clean_forecast_ledger as clf

        db = str(tmp_path / "ledger.db")
        _seed_ledger(db)
        with pytest.raises(SystemExit):
            clf.main(["--db", db, "--categories", "a,x"])

    def test_default_apply_deletes_only_a_and_b(self, tmp_path):
        from scripts import clean_forecast_ledger as clf

        db = str(tmp_path / "ledger.db")
        _seed_ledger(db)
        before = _rows(db, "SELECT COUNT(*) FROM forecast_errors")[0][0]
        summary = clf.apply_cleanup(db, window_days=365, min_obs=3, backup_dir=tmp_path / "b")
        assert summary["categories"] == ["a", "b"]
        assert summary["rows_remaining"] == before - 5
        assert len(list((tmp_path / "b").iterdir())) == 1
        assert _rows(db, "SELECT COUNT(*) FROM forecast_errors WHERE symbol = 'TEST'")[0][0] == 0
        assert _rows(
            db, "SELECT COUNT(*) FROM forecast_errors WHERE symbol = 'SEED' AND model_name = 'monte_carlo'"
        )[0][0] == 0
        # Duplicates untouched and NOT given a day key (no upsert target created).
        keep = _rows(db, "SELECT forecast_day FROM forecast_errors WHERE symbol = 'KEEP'")
        assert len(keep) == 5 and all(r[0] is None for r in keep)

    def test_apply_via_main_with_c_prints_warning(self, tmp_path, capsys):
        from scripts import clean_forecast_ledger as clf

        db = str(tmp_path / "ledger.db")
        _seed_ledger(db)
        assert clf.main([
            "--db", db, "--window-days", "365", "--min-obs", "3", "--categories", "a,b,c",
            "--apply", "--backup-dir", str(tmp_path / "b"),
        ]) == 0
        out = capsys.readouterr().out
        assert "APPLIED" in out and "CATEGORY (c) IS A LIVE DECISION CHANGE" in out
        assert _rows(db, "SELECT COUNT(*) FROM forecast_errors WHERE symbol = 'KEEP'")[0][0] == 1

    def test_weight_shift_is_reported_without_any_cold_start(self, tmp_path):
        """Deleting a bad completed MC seed row moves the MC weight of a
        symbol that stays warm; the estimate must say so."""
        db = str(tmp_path / "w.db")
        ForecastTracker(db_path=db)
        now = datetime.now(timezone.utc)
        for i in range(5):
            ts = (now - timedelta(days=5 + i)).isoformat()
            _insert(db, "WX", MODEL_ARIMA, 10, ts, 21.0, 20.0)
            _insert(db, "WX", MODEL_MONTE_CARLO, 10, ts, 20.5, 20.0)
        bug = "2026-08-14T14:00:00+00:00"
        _insert(db, "WX", MODEL_ARIMA, 10, bug, 20.0, 20.0)
        _insert(db, "WX", MODEL_MONTE_CARLO, 10, bug, 99.0, 20.0)
        summary = self._analyze(db, ("b",))
        cs = summary["cold_start"]
        assert summary["b_mc_seed_rows"] == 1
        assert cs["symbol_horizon_pairs_dropping_to_cold_start"] == 0
        assert cs["symbol_horizon_pairs_with_weight_change"] == 1
        assert cs["max_model_weight_shift"] > 0.3
        assert any("LIVE SKILL WEIGHTS CHANGE for 1" in w for w in summary["warnings"])

class TestForecastSkillReportScript:
    def test_json_report(self, tmp_path, capsys):
        import json

        from scripts import forecast_skill_report as fsr

        t = _tracker(tmp_path)
        ts = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
        _insert(t._db_path, "AAPL", MODEL_ARIMA, 10, ts, 105.0, 110.0)
        _insert(t._db_path, "AAPL", MODEL_NAIVE, 10, ts, 100.0, 110.0)
        assert fsr.main(["--db", t._db_path, "--horizon", "10", "--window-days", "365", "--json"]) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["window_days"] == 365
        h10 = report["horizons"][0]
        assert h10["horizon_days"] == 10
        assert h10["models"][MODEL_ARIMA]["wins"] == 1

    def test_text_report_handles_empty_horizon(self, tmp_path, capsys):
        from scripts import forecast_skill_report as fsr

        t = _tracker(tmp_path)
        assert fsr.main(["--db", t._db_path, "--horizon", "60", "--window-days", "365"]) == 0
        out = capsys.readouterr().out
        assert "horizon 60" in out and "no scored pairs" in out

    def test_default_window_is_the_live_setting(self, tmp_path, monkeypatch):
        from settings import settings as _settings
        from scripts import forecast_skill_report as fsr

        monkeypatch.setattr(_settings, "FORECAST_SKILL_WINDOW_DAYS", 123)
        t = _tracker(tmp_path)
        assert fsr.build_report([10], db_path=t._db_path)["window_days"] == 123
