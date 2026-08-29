"""
tests/test_observability_skill_decay.py
========================================
Focused unit tests for the ``decay_pct`` / ``decay_reason`` forecast-skill-decay
signal wired into ``pilots.observability.forecast_skill_by_symbol_summary`` —
closing the confirmed gap where ``investyo_mcp_server.py::get_model_drift_report``
read a ``decay_pct`` field that was never actually computed anywhere.

Covers, per the task brief:
  (a) recent forecast error worse than the older baseline -> positive decay_pct
      (skill degrading).
  (b) recent forecast error better than the older baseline -> negative
      decay_pct (skill improving).
  (c) insufficient completed forecasts in one sub-window (recent or baseline)
      -> decay_pct is None with an honest decay_reason, never a fabricated
      number (CONSTRAINT #4).

Also exercises the pure ``_skill_from_pooled_stats`` helper directly, and a
dead-letter (CONSTRAINT #6) check that a DB failure degrades every requested
symbol to an honest ``None`` rather than raising.

Same DB-mocking convention as ``tests/test_pilots_observability.py``: a real
``forecast_errors`` SQLite table is built at a tmp path and
``forecasting.forecast_tracker.ForecastTracker`` is patched to a factory bound
to that path — small self-contained duplication here (rather than importing
private test helpers cross-module) per this codebase's own stated convention
(see ``pilots/observability.py``'s module docstring on ``_RANGE_DAYS``/
``load_block_log``).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

from pilots import observability as obs


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _make_forecast_db(path, rows):
    """Create a ``forecast_errors`` table at ``path`` with the given rows.

    ``rows``: list of (symbol, model_name, horizon_days, forecast_ts_iso,
    forecast_price, actual_price, squared_error, recorded_at_iso).
    """
    conn = sqlite3.connect(str(path))
    conn.execute(
        """CREATE TABLE forecast_errors (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol         TEXT    NOT NULL,
            model_name     TEXT    NOT NULL,
            horizon_days   INTEGER NOT NULL,
            forecast_ts    TEXT    NOT NULL,
            forecast_price REAL    NOT NULL,
            actual_price   REAL,
            squared_error  REAL,
            recorded_at    TEXT    NOT NULL
        )"""
    )
    conn.executemany(
        """INSERT INTO forecast_errors
           (symbol, model_name, horizon_days, forecast_ts, forecast_price,
            actual_price, squared_error, recorded_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    conn.close()


def _tracker_factory_for(db_path):
    """Build a ``ForecastTracker`` factory bound to a fixed db_path — patched
    onto ``forecasting.forecast_tracker.ForecastTracker`` so
    ``pilots.observability``'s lazy imports pick up a real tracker pointed at
    the test's tmp DB. Mirrors ``tests/test_pilots_observability.py``'s
    identical helper."""
    from forecasting.forecast_tracker import ForecastTracker as _Real

    def _factory(*args, **kwargs):
        kwargs["db_path"] = str(db_path)
        return _Real(*args, **kwargs)

    return _factory


def _snapshot_with_symbols(symbols):
    return {"signals": [{"symbol": s} for s in symbols]}


# ---------------------------------------------------------------------------
# _skill_from_pooled_stats — pure function
# ---------------------------------------------------------------------------


class TestSkillFromPooledStats:
    def test_below_min_obs_is_none(self):
        assert obs._skill_from_pooled_stats(n=5, mse=1.0, min_obs=10) is None

    def test_missing_mse_is_none(self):
        assert obs._skill_from_pooled_stats(n=50, mse=None, min_obs=10) is None

    def test_negative_mse_is_none(self):
        assert obs._skill_from_pooled_stats(n=50, mse=-1.0, min_obs=10) is None

    def test_valid_stats_return_inverse_rmse(self):
        # mse=4.0 -> rmse=2.0 -> skill = 1/2.0 = 0.5
        assert obs._skill_from_pooled_stats(n=50, mse=4.0, min_obs=10) == pytest.approx(0.5)

    def test_tiny_mse_is_floored_by_min_rmse(self):
        from forecasting.forecast_tracker import _MIN_RMSE

        # An essentially-zero MSE must not blow up to a huge/infinite skill —
        # the same _MIN_RMSE floor compute_skill_weights_from_stats uses.
        skill = obs._skill_from_pooled_stats(n=50, mse=1e-12, min_obs=10)
        assert skill == pytest.approx(1.0 / _MIN_RMSE)


# ---------------------------------------------------------------------------
# _forecast_decay_stats_by_symbol — bulk SQL aggregate
# ---------------------------------------------------------------------------


class TestForecastDecayStatsBySymbol:
    def test_recent_error_worse_than_baseline_is_positive_decay(self, tmp_path):
        """(a) Recent forecasts clearly less accurate than the older baseline
        -> positive decay_pct (skill degrading)."""
        db_path = tmp_path / "forecasts.db"
        now = datetime.now(timezone.utc)
        rows = []
        # Baseline half (50-64 days ago, inside the 90d window's older half):
        # small squared_error -> good (high) skill.
        for j in range(15):
            ts = _iso(now - timedelta(days=50 + j))
            rows.append(("AAPL", "arima", 30, ts, 100.0, 101.0, 1.0, ts))  # rmse=1.0
        # Recent half (5-19 days ago): large squared_error -> poor (low) skill.
        for j in range(15):
            ts = _iso(now - timedelta(days=5 + j))
            rows.append(("AAPL", "arima", 30, ts, 100.0, 105.0, 25.0, ts))  # rmse=5.0
        _make_forecast_db(db_path, rows)

        out = obs._forecast_decay_stats_by_symbol(
            str(db_path), ["AAPL"], horizon_days=30, window_days=90, min_obs=10
        )

        decay = out["AAPL"]["decay_pct"]
        assert decay is not None
        assert decay > 0
        # baseline_skill=1.0, recent_skill=0.2 -> (1.0-0.2)/1.0*100 = 80.0
        assert decay == pytest.approx(80.0, abs=0.5)
        assert out["AAPL"]["decay_reason"] is None

    def test_recent_error_better_than_baseline_is_negative_decay(self, tmp_path):
        """(b) Recent forecasts clearly MORE accurate than the older baseline
        -> negative decay_pct (skill improving)."""
        db_path = tmp_path / "forecasts.db"
        now = datetime.now(timezone.utc)
        rows = []
        # Baseline half: large squared_error -> poor (low) skill.
        for j in range(15):
            ts = _iso(now - timedelta(days=50 + j))
            rows.append(("AAPL", "arima", 30, ts, 100.0, 105.0, 25.0, ts))  # rmse=5.0
        # Recent half: small squared_error -> good (high) skill.
        for j in range(15):
            ts = _iso(now - timedelta(days=5 + j))
            rows.append(("AAPL", "arima", 30, ts, 100.0, 101.0, 1.0, ts))  # rmse=1.0
        _make_forecast_db(db_path, rows)

        out = obs._forecast_decay_stats_by_symbol(
            str(db_path), ["AAPL"], horizon_days=30, window_days=90, min_obs=10
        )

        decay = out["AAPL"]["decay_pct"]
        assert decay is not None
        assert decay < 0
        # baseline_skill=0.2, recent_skill=1.0 -> (0.2-1.0)/0.2*100 = -400.0
        assert decay == pytest.approx(-400.0, abs=1.0)
        assert out["AAPL"]["decay_reason"] is None

    def test_insufficient_baseline_history_is_none_with_honest_reason(self, tmp_path):
        """(c) Plenty of recent history but too few baseline observations to
        clear min_obs -> decay_pct is None with an honest reason, never a
        fabricated number."""
        db_path = tmp_path / "forecasts.db"
        now = datetime.now(timezone.utc)
        rows = []
        # Recent half: well above min_obs=10.
        for j in range(15):
            ts = _iso(now - timedelta(days=5 + j))
            rows.append(("AAPL", "arima", 30, ts, 100.0, 101.0, 1.0, ts))
        # Baseline half: only 3 rows -- below min_obs=10.
        for j in range(3):
            ts = _iso(now - timedelta(days=50 + j))
            rows.append(("AAPL", "arima", 30, ts, 100.0, 101.0, 1.0, ts))
        _make_forecast_db(db_path, rows)

        out = obs._forecast_decay_stats_by_symbol(
            str(db_path), ["AAPL"], horizon_days=30, window_days=90, min_obs=10
        )

        assert out["AAPL"]["decay_pct"] is None
        assert isinstance(out["AAPL"]["decay_reason"], str) and out["AAPL"]["decay_reason"]

    def test_insufficient_recent_history_is_none_with_honest_reason(self, tmp_path):
        """(c) Mirror case: plenty of baseline history but too few recent
        observations -> still an honest None, not a fabricated number."""
        db_path = tmp_path / "forecasts.db"
        now = datetime.now(timezone.utc)
        rows = []
        for j in range(15):
            ts = _iso(now - timedelta(days=50 + j))
            rows.append(("AAPL", "arima", 30, ts, 100.0, 101.0, 1.0, ts))
        for j in range(3):  # recent: below min_obs=10
            ts = _iso(now - timedelta(days=5 + j))
            rows.append(("AAPL", "arima", 30, ts, 100.0, 101.0, 1.0, ts))
        _make_forecast_db(db_path, rows)

        out = obs._forecast_decay_stats_by_symbol(
            str(db_path), ["AAPL"], horizon_days=30, window_days=90, min_obs=10
        )

        assert out["AAPL"]["decay_pct"] is None
        assert isinstance(out["AAPL"]["decay_reason"], str) and out["AAPL"]["decay_reason"]

    def test_no_rows_at_all_is_none_with_honest_reason(self, tmp_path):
        db_path = tmp_path / "forecasts.db"
        _make_forecast_db(db_path, [])

        out = obs._forecast_decay_stats_by_symbol(
            str(db_path), ["AAPL", "MSFT"], horizon_days=30, window_days=90, min_obs=10
        )

        assert out["AAPL"]["decay_pct"] is None
        assert out["MSFT"]["decay_pct"] is None

    def test_empty_symbol_list_returns_empty_dict(self, tmp_path):
        db_path = tmp_path / "forecasts.db"
        _make_forecast_db(db_path, [])
        assert obs._forecast_decay_stats_by_symbol(str(db_path), [], 30, 90, 10) == {}

    def test_db_failure_dead_letters_to_honest_none_for_every_symbol(self):
        """CONSTRAINT #6: a missing/unreadable DB file must never raise -- it
        degrades every requested symbol to an honest None, not a crash."""
        out = obs._forecast_decay_stats_by_symbol(
            "/nonexistent/path/does-not-exist.db",
            ["AAPL", "MSFT"],
            horizon_days=30,
            window_days=90,
            min_obs=10,
        )
        assert out["AAPL"]["decay_pct"] is None
        assert out["MSFT"]["decay_pct"] is None
        assert out["AAPL"]["decay_reason"]
        assert out["MSFT"]["decay_reason"]

    def test_decay_pooled_across_models_not_per_model(self, tmp_path):
        """decay_pct is a single symbol-level number pooled across ALL models
        in each sub-window -- not split per model like skill_weights."""
        db_path = tmp_path / "forecasts.db"
        now = datetime.now(timezone.utc)
        rows = []
        # Baseline: two models, pooled rmse should reflect both.
        for j in range(8):
            ts = _iso(now - timedelta(days=50 + j))
            rows.append(("AAPL", "arima", 30, ts, 100.0, 101.0, 1.0, ts))
            rows.append(("AAPL", "monte_carlo", 30, ts, 100.0, 101.0, 1.0, ts))
        # Recent: two models, pooled worse.
        for j in range(8):
            ts = _iso(now - timedelta(days=5 + j))
            rows.append(("AAPL", "arima", 30, ts, 100.0, 105.0, 25.0, ts))
            rows.append(("AAPL", "monte_carlo", 30, ts, 100.0, 105.0, 25.0, ts))
        _make_forecast_db(db_path, rows)

        out = obs._forecast_decay_stats_by_symbol(
            str(db_path), ["AAPL"], horizon_days=30, window_days=90, min_obs=10
        )
        # 16 pooled rows per half (>= min_obs=10) -> a valid comparison exists.
        assert out["AAPL"]["decay_pct"] == pytest.approx(80.0, abs=0.5)


# ---------------------------------------------------------------------------
# forecast_skill_by_symbol_summary — decay_pct wired into the public rows
# ---------------------------------------------------------------------------


class TestForecastSkillBySymbolSummaryDecayIntegration:
    def test_row_carries_decay_pct_and_reason(self, tmp_path):
        db_path = tmp_path / "forecasts.db"
        now = datetime.now(timezone.utc)
        rows = []
        for j in range(15):
            ts = _iso(now - timedelta(days=50 + j))
            rows.append(("AAPL", "arima", 30, ts, 100.0, 101.0, 1.0, ts))
        for j in range(15):
            ts = _iso(now - timedelta(days=5 + j))
            rows.append(("AAPL", "arima", 30, ts, 100.0, 105.0, 25.0, ts))
        _make_forecast_db(db_path, rows)

        with mock.patch(
            "forecasting.forecast_tracker.ForecastTracker",
            side_effect=_tracker_factory_for(db_path),
        ):
            out = obs.forecast_skill_by_symbol_summary(
                snapshot=_snapshot_with_symbols(["AAPL"]),
                horizon_days=30,
                window_days=90,
                min_obs=10,
            )

        row = out["rows"][0]
        assert row["symbol"] == "AAPL"
        assert row["decay_pct"] == pytest.approx(80.0, abs=0.5)
        assert row["decay_reason"] is None

    def test_row_decay_is_none_with_reason_when_history_too_thin(self, tmp_path):
        db_path = tmp_path / "forecasts.db"
        now = datetime.now(timezone.utc)
        rows = []
        # Only 5 completed rows total -- enough to clear the outer
        # any_history gate but nowhere near min_obs=10 per sub-window.
        for j in range(5):
            ts = _iso(now - timedelta(days=5 + j))
            rows.append(("AAPL", "arima", 30, ts, 100.0, 101.0, 1.0, ts))
        _make_forecast_db(db_path, rows)

        with mock.patch(
            "forecasting.forecast_tracker.ForecastTracker",
            side_effect=_tracker_factory_for(db_path),
        ):
            out = obs.forecast_skill_by_symbol_summary(
                snapshot=_snapshot_with_symbols(["AAPL"]),
                horizon_days=30,
                window_days=90,
                min_obs=10,
            )

        row = out["rows"][0]
        assert row["decay_pct"] is None
        assert isinstance(row["decay_reason"], str) and row["decay_reason"]

    def test_symbol_with_zero_history_gets_none_decay_not_fabricated(self, tmp_path):
        """A symbol present in the requested universe but with real history
        for ANOTHER symbol only -- must still resolve to an honest
        decay_pct: None row, never a KeyError or a fabricated 0.0."""
        db_path = tmp_path / "forecasts.db"
        now = datetime.now(timezone.utc)
        rows = []
        for j in range(15):
            ts = _iso(now - timedelta(days=50 + j))
            rows.append(("MSFT", "arima", 30, ts, 100.0, 101.0, 1.0, ts))
        for j in range(15):
            ts = _iso(now - timedelta(days=5 + j))
            rows.append(("MSFT", "arima", 30, ts, 100.0, 105.0, 25.0, ts))
        _make_forecast_db(db_path, rows)

        with mock.patch(
            "forecasting.forecast_tracker.ForecastTracker",
            side_effect=_tracker_factory_for(db_path),
        ):
            out = obs.forecast_skill_by_symbol_summary(
                snapshot=_snapshot_with_symbols(["AAPL", "MSFT"]),
                horizon_days=30,
                window_days=90,
                min_obs=10,
            )

        by_symbol = {r["symbol"]: r for r in out["rows"]}
        assert by_symbol["AAPL"]["decay_pct"] is None
        assert by_symbol["AAPL"]["decay_reason"]
        assert by_symbol["MSFT"]["decay_pct"] == pytest.approx(80.0, abs=0.5)
