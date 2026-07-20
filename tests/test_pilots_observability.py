"""
tests/test_pilots_observability.py
===================================
Unit tests for ``pilots/observability.py`` — the pure, dead-letter-safe reader
that assembles the Mission-Control composite for ``GET /observability/summary``:
portfolio risk metrics, the account equity curve + drawdown, the macro-regime
overlay, portfolio-wide forecast skill, and the risk-gate block log.

All network/engine dependencies are monkeypatched at their SOURCE module
(``data.historical_store.HistoricalStore``, ``forecasting.forecast_tracker
.ForecastTracker``) rather than on ``pilots.observability`` itself, since that
module does lazy (inside-function) imports — mirroring
``pilots/realized.py``'s test convention of patching ``data.robinhood_orders``
directly rather than the pilots-layer module.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest import mock

import pandas as pd
import pytest

from settings import settings
from pilots import observability as obs


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _equity_df(values, start=datetime(2026, 1, 1, tzinfo=timezone.utc)):
    rows = []
    for i, v in enumerate(values):
        ts = start + timedelta(days=i)
        rows.append([_iso(ts), 500.0, float(v), 0.0])
    return pd.DataFrame(
        rows, columns=["fetched_at", "buying_power", "total_equity", "total_dividends"]
    )


# ---------------------------------------------------------------------------
# portfolio_risk_metrics
# ---------------------------------------------------------------------------


class TestPortfolioRiskMetrics:
    def test_cold_start_no_snapshots(self):
        class _Store:
            def account_snapshot_history(self, since=None):
                return pd.DataFrame()

        with mock.patch("data.historical_store.HistoricalStore", return_value=_Store()):
            out = obs.portfolio_risk_metrics()

        assert out["sharpe_ratio"] is None
        assert out["calmar_ratio"] is None
        assert out["max_drawdown"] is None
        assert out["max_drawdown_duration_days"] is None
        assert out["cagr"] is None
        assert out["n_snapshots"] == 0
        assert out["min_snapshots_required"] == 20
        assert out["reason"] and "No account snapshots" in out["reason"]

    def test_insufficient_snapshots_honest_reason(self):
        class _Store:
            def account_snapshot_history(self, since=None):
                return _equity_df([1000 + i for i in range(5)])

        with mock.patch("data.historical_store.HistoricalStore", return_value=_Store()):
            out = obs.portfolio_risk_metrics()

        assert out["n_snapshots"] == 5
        assert out["sharpe_ratio"] is None  # below MIN_SNAPSHOTS_FOR_STATS
        assert out["reason"] and "5 snapshot" in out["reason"]

    def test_warm_path_flat_growth_has_zero_drawdown(self):
        values = [1000.0 * (1.001 ** i) for i in range(25)]

        class _Store:
            def account_snapshot_history(self, since=None):
                return _equity_df(values)

        with mock.patch("data.historical_store.HistoricalStore", return_value=_Store()):
            out = obs.portfolio_risk_metrics()

        assert out["n_snapshots"] == 25
        assert out["reason"] is None
        assert out["sharpe_ratio"] is not None
        # Monotonically increasing equity never dips below its running peak.
        assert out["max_drawdown"] == pytest.approx(0.0, abs=1e-9)
        assert out["max_drawdown_duration_days"] == pytest.approx(0.0)
        assert out["cagr"] is not None

    def test_historical_store_construction_failure_degrades_to_empty(self):
        with mock.patch(
            "data.historical_store.HistoricalStore", side_effect=RuntimeError("db locked")
        ):
            out = obs.portfolio_risk_metrics()
        assert out["sharpe_ratio"] is None
        assert out["n_snapshots"] == 0
        assert out["reason"]


# ---------------------------------------------------------------------------
# portfolio_heat_metric
# ---------------------------------------------------------------------------


class _Pos:
    """Minimal stand-in for data.robinhood_portfolio.PortfolioPosition —
    portfolio_heat_metric only reads .unrealized_pl off each position."""

    def __init__(self, unrealized_pl):
        self.unrealized_pl = unrealized_pl


class _Snapshot:
    """Minimal stand-in for data.historical_store's reconstructed
    AccountSnapshot — portfolio_heat_metric only reads .positions,
    .total_equity, and .fetched_at."""

    def __init__(self, positions, total_equity, fetched_at=None):
        self.positions = positions
        self.total_equity = total_equity
        self.fetched_at = fetched_at


class TestPortfolioHeatMetric:
    def test_cold_start_no_snapshot(self):
        class _Store:
            def latest_account_snapshot(self):
                return None

        with mock.patch("data.historical_store.HistoricalStore", return_value=_Store()):
            out = obs.portfolio_heat_metric()

        assert out["heat_pct"] is None
        assert out["over_limit"] is None
        assert out["n_positions"] == 0
        assert out["max_portfolio_heat"] == pytest.approx(settings.MAX_PORTFOLIO_HEAT)
        assert out["reason"]

    def test_historical_store_construction_failure_degrades_to_empty(self):
        with mock.patch(
            "data.historical_store.HistoricalStore", side_effect=RuntimeError("db locked")
        ):
            out = obs.portfolio_heat_metric()
        assert out["heat_pct"] is None
        assert out["reason"]

    def test_missing_total_equity_is_honest_none_not_fabricated(self):
        snap = _Snapshot(positions={"AAPL": _Pos(-50.0)}, total_equity=None)

        class _Store:
            def latest_account_snapshot(self):
                return snap

        with mock.patch("data.historical_store.HistoricalStore", return_value=_Store()):
            out = obs.portfolio_heat_metric()

        assert out["heat_pct"] is None
        assert out["n_positions"] == 1
        assert "equity" in out["reason"].lower()

    def test_non_positive_total_equity_is_honest_none(self):
        snap = _Snapshot(positions={}, total_equity=0.0)

        class _Store:
            def latest_account_snapshot(self):
                return snap

        with mock.patch("data.historical_store.HistoricalStore", return_value=_Store()):
            out = obs.portfolio_heat_metric()

        assert out["heat_pct"] is None
        assert out["reason"]

    def test_all_profitable_positions_yield_zero_heat(self):
        snap = _Snapshot(
            positions={"AAPL": _Pos(120.0), "MSFT": _Pos(50.0)},
            total_equity=10_000.0,
        )

        class _Store:
            def latest_account_snapshot(self):
                return snap

        with mock.patch("data.historical_store.HistoricalStore", return_value=_Store()):
            out = obs.portfolio_heat_metric()

        assert out["heat_pct"] == pytest.approx(0.0)
        assert out["over_limit"] is False
        assert out["n_positions"] == 2
        assert out["reason"] is None

    def test_heat_matches_risk_gate_formula_exactly(self):
        # Mirrors execution/risk_gate.py::portfolio_heat_check's own formula:
        # sum(abs(unrealized_pl) for adverse positions) / account.equity.
        snap = _Snapshot(
            positions={
                "AAPL": _Pos(-300.0),   # adverse
                "MSFT": _Pos(-200.0),   # adverse
                "NVDA": _Pos(400.0),    # profitable — excluded from the numerator
            },
            total_equity=10_000.0,
        )

        class _Store:
            def latest_account_snapshot(self):
                return snap

        with mock.patch("data.historical_store.HistoricalStore", return_value=_Store()):
            out = obs.portfolio_heat_metric()

        assert out["heat_pct"] == pytest.approx((300.0 + 200.0) / 10_000.0)
        assert out["n_positions"] == 3
        assert out["reason"] is None

    def test_over_limit_flag_true_when_heat_exceeds_configured_ceiling(self):
        snap = _Snapshot(
            positions={"TSLA": _Pos(-900.0)},
            total_equity=10_000.0,  # 9% heat
        )

        class _Store:
            def latest_account_snapshot(self):
                return snap

        with mock.patch("data.historical_store.HistoricalStore", return_value=_Store()):
            with mock.patch.object(settings, "MAX_PORTFOLIO_HEAT", 0.06):
                out = obs.portfolio_heat_metric()

        assert out["heat_pct"] == pytest.approx(0.09)
        assert out["max_portfolio_heat"] == pytest.approx(0.06)
        assert out["over_limit"] is True

    def test_non_finite_unrealized_pl_is_skipped_not_fatal(self):
        snap = _Snapshot(
            positions={"AAPL": _Pos(float("nan")), "MSFT": _Pos(-100.0)},
            total_equity=1_000.0,
        )

        class _Store:
            def latest_account_snapshot(self):
                return snap

        with mock.patch("data.historical_store.HistoricalStore", return_value=_Store()):
            out = obs.portfolio_heat_metric()

        assert out["heat_pct"] == pytest.approx(0.1)  # only the -100.0 counts
        assert out["reason"] is None

    def test_as_of_reflects_snapshot_fetched_at(self):
        ts = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
        snap = _Snapshot(positions={}, total_equity=5_000.0, fetched_at=ts)

        class _Store:
            def latest_account_snapshot(self):
                return snap

        with mock.patch("data.historical_store.HistoricalStore", return_value=_Store()):
            out = obs.portfolio_heat_metric()

        assert out["as_of"] == ts.isoformat()


# ---------------------------------------------------------------------------
# equity_curve_with_drawdown
# ---------------------------------------------------------------------------


class TestEquityCurveWithDrawdown:
    def test_cold_start_empty_points(self):
        class _Store:
            def account_snapshot_history(self, since=None):
                return pd.DataFrame()

        with mock.patch("data.historical_store.HistoricalStore", return_value=_Store()):
            out = obs.equity_curve_with_drawdown("1Y")

        assert out["range"] == "1Y"
        assert out["points"] == []
        assert out["reason"]

    def test_drawdown_computed_against_running_peak(self):
        values = [100, 110, 105, 120, 90, 95]

        class _Store:
            def account_snapshot_history(self, since=None):
                return _equity_df(values)

        with mock.patch("data.historical_store.HistoricalStore", return_value=_Store()):
            out = obs.equity_curve_with_drawdown("2Y")  # wide range: no truncation

        points = out["points"]
        assert len(points) == 6
        assert [p["equity"] for p in points] == [100.0, 110.0, 105.0, 120.0, 90.0, 95.0]
        expected_dd = [0.0, 0.0, (105 - 110) / 110, 0.0, (90 - 120) / 120, (95 - 120) / 120]
        for p, exp in zip(points, expected_dd):
            assert p["drawdown"] == pytest.approx(exp, abs=1e-9)
        assert out["reason"] is None

    def test_range_zoom_truncates_older_points(self):
        values = list(range(100, 130))  # 30 daily points

        class _Store:
            def account_snapshot_history(self, since=None):
                return _equity_df(values)

        with mock.patch("data.historical_store.HistoricalStore", return_value=_Store()):
            full = obs.equity_curve_with_drawdown("2Y")
            zoomed = obs.equity_curve_with_drawdown("1W")

        assert len(zoomed["points"]) < len(full["points"])
        assert len(zoomed["points"]) >= 2

    def test_dedupes_same_day_snapshots_to_last(self):
        ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
        rows = [
            [_iso(ts), 500.0, 100.0, 0.0],
            [_iso(ts + timedelta(hours=6)), 500.0, 105.0, 0.0],  # same calendar day
            [_iso(ts + timedelta(days=1)), 500.0, 110.0, 0.0],
        ]
        df = pd.DataFrame(
            rows, columns=["fetched_at", "buying_power", "total_equity", "total_dividends"]
        )

        class _Store:
            def account_snapshot_history(self, since=None):
                return df

        with mock.patch("data.historical_store.HistoricalStore", return_value=_Store()):
            out = obs.equity_curve_with_drawdown("2Y")

        assert len(out["points"]) == 2  # 2 distinct calendar days
        assert out["points"][0]["equity"] == 105.0  # last snapshot of day 1, not the first

    def test_unreadable_store_degrades_to_empty(self):
        with mock.patch(
            "data.historical_store.HistoricalStore", side_effect=RuntimeError("db locked")
        ):
            out = obs.equity_curve_with_drawdown("1Y")
        assert out["points"] == []
        assert out["reason"]


# ---------------------------------------------------------------------------
# regime_overlay
# ---------------------------------------------------------------------------


class TestRegimeOverlay:
    def test_none_snapshot_is_honest_empty(self):
        out = obs.regime_overlay(None)
        assert out["market_regime"] is None
        assert out["sahm_rule"] is None
        assert out["reason"]

    def test_full_snapshot_maps_every_field(self):
        snap = {
            "timestamp": "2026-07-11T21:05:00+00:00",
            "market_regime": "RISK ON",
            "vix": 14.8,
            "yield_curve": 0.42,
            "sahm_rule": 0.13,
            "high_yield_oas": 3.21,
            "hmm_risk_on_probability": 0.78,
            "kill_switch_active": False,
            "macro_regime_gate_enabled": True,
        }
        out = obs.regime_overlay(snap)
        assert out["as_of"] == "2026-07-11T21:05:00+00:00"
        assert out["market_regime"] == "RISK ON"
        assert out["vix"] == pytest.approx(14.8)
        assert out["sahm_rule"] == pytest.approx(0.13)
        assert out["high_yield_oas"] == pytest.approx(3.21)
        assert out["yield_curve"] == pytest.approx(0.42)
        assert out["hmm_risk_on_probability"] == pytest.approx(0.78)
        assert out["kill_switch_active"] is False
        assert out["macro_regime_gate_enabled"] is True
        assert out["reason"] is None

    def test_missing_hmm_field_is_null_not_fabricated(self):
        snap = {"timestamp": "t", "market_regime": "NEUTRAL"}
        out = obs.regime_overlay(snap)
        assert out["hmm_risk_on_probability"] is None
        assert out["sahm_rule"] is None
        assert out["reason"] is None  # a real (if sparse) snapshot, not cold start

    def test_empty_dict_snapshot_is_treated_as_cold_start(self):
        out = obs.regime_overlay({})
        assert out["reason"]


# ---------------------------------------------------------------------------
# portfolio_forecast_skill
# ---------------------------------------------------------------------------


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
    ``pilots.observability``'s lazy ``from forecasting.forecast_tracker import
    ForecastTracker`` picks up a real tracker pointed at the test's tmp DB."""
    from forecasting.forecast_tracker import ForecastTracker as _Real

    def _factory(*args, **kwargs):
        kwargs["db_path"] = str(db_path)
        return _Real(*args, **kwargs)

    return _factory


class TestPortfolioForecastSkill:
    def test_no_history_is_honest_empty(self):
        with mock.patch(
            "forecasting.forecast_tracker.ForecastTracker",
            side_effect=RuntimeError("unavailable"),
        ):
            out = obs.portfolio_forecast_skill(horizon_days=30)

        assert out["reliability_curve"] == []
        assert out["skill_weights"] == {}
        assert out["pending"] == 0
        assert out["completed"] == 0
        assert out["reason"]

    def test_warm_path_single_model_gets_full_weight(self, tmp_path):
        db_path = tmp_path / "forecasts.db"
        now = datetime.now(timezone.utc)
        rows = []
        for sym in ("AAPL", "MSFT", "NVDA"):
            for j in range(12):
                ts = _iso(now - timedelta(days=5 + j))
                forecast_price = 100.0
                actual_price = 101.0 + (j % 3)
                sq_err = (actual_price - forecast_price) ** 2
                rows.append((sym, "arima", 30, ts, forecast_price, actual_price, sq_err, ts))
        _make_forecast_db(db_path, rows)

        with mock.patch(
            "forecasting.forecast_tracker.ForecastTracker",
            side_effect=_tracker_factory_for(db_path),
        ):
            out = obs.portfolio_forecast_skill(horizon_days=30, window_days=90, min_obs=10)

        assert out["completed"] == 36  # 3 symbols * 12 rows
        assert out["pending"] == 0
        assert out["reason"] is None
        assert out["skill_weights"] == {"arima": pytest.approx(1.0)}

    def test_cold_start_within_window_uses_equal_weights(self, tmp_path):
        """Fewer than min_obs completed rows for one model -> equal weighting
        across all models seen in the window (never a fabricated skill edge)."""
        db_path = tmp_path / "forecasts.db"
        now = datetime.now(timezone.utc)
        rows = []
        # arima: plenty of history.
        for j in range(20):
            ts = _iso(now - timedelta(days=5 + j))
            rows.append(("AAPL", "arima", 30, ts, 100.0, 101.0, 1.0, ts))
        # monte_carlo: too few rows to be confident.
        for j in range(3):
            ts = _iso(now - timedelta(days=5 + j))
            rows.append(("AAPL", "monte_carlo", 30, ts, 100.0, 102.0, 4.0, ts))
        _make_forecast_db(db_path, rows)

        with mock.patch(
            "forecasting.forecast_tracker.ForecastTracker",
            side_effect=_tracker_factory_for(db_path),
        ):
            out = obs.portfolio_forecast_skill(horizon_days=30, window_days=90, min_obs=10)

        assert out["skill_weights"] == {"arima": pytest.approx(0.5), "monte_carlo": pytest.approx(0.5)}

    def test_pending_rows_counted_separately_from_completed(self, tmp_path):
        db_path = tmp_path / "forecasts.db"
        now = datetime.now(timezone.utc)
        ts = _iso(now)
        rows = [
            ("AAPL", "arima", 30, ts, 100.0, None, None, ts),
            ("MSFT", "arima", 30, ts, 100.0, None, None, ts),
        ]
        _make_forecast_db(db_path, rows)

        with mock.patch(
            "forecasting.forecast_tracker.ForecastTracker",
            side_effect=_tracker_factory_for(db_path),
        ):
            out = obs.portfolio_forecast_skill(horizon_days=30)

        assert out["pending"] == 2
        assert out["completed"] == 0
        assert out["skill_weights"] == {}


# ---------------------------------------------------------------------------
# risk_gate_block_log
# ---------------------------------------------------------------------------


class TestRiskGateBlockLog:
    def test_missing_file_is_honest_empty(self, tmp_path):
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            out = obs.risk_gate_block_log()
        assert out["entries"] == []
        assert out["count"] == 0
        assert out["reason"]

    def test_reads_jsonl_newest_first_and_skips_malformed(self, tmp_path):
        log_path = tmp_path / "risk_gate_blocks.jsonl"
        lines = [
            json.dumps({"symbol": "AAPL", "check": "max_position_size", "ts": "2026-07-01"}),
            "{not valid json",
            json.dumps({"symbol": "MSFT", "check": "daily_loss_limit", "ts": "2026-07-02"}),
        ]
        log_path.write_text("\n".join(lines), encoding="utf-8")

        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            out = obs.risk_gate_block_log()

        assert out["count"] == 2  # malformed line skipped
        assert out["entries"][0]["symbol"] == "MSFT"  # newest first
        assert out["entries"][1]["symbol"] == "AAPL"
        assert out["reason"] is None

    def test_respects_n_limit(self, tmp_path):
        log_path = tmp_path / "risk_gate_blocks.jsonl"
        lines = [json.dumps({"i": i}) for i in range(10)]
        log_path.write_text("\n".join(lines), encoding="utf-8")

        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            out = obs.risk_gate_block_log(n=3)

        assert out["count"] == 3
        assert [e["i"] for e in out["entries"]] == [9, 8, 7]


# ---------------------------------------------------------------------------
# observability_summary — composite, independent-degradation contract
# ---------------------------------------------------------------------------


class TestObservabilitySummary:
    def test_all_sections_present(self, tmp_path):
        class _Store:
            def account_snapshot_history(self, since=None):
                return pd.DataFrame()

        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch("data.historical_store.HistoricalStore", return_value=_Store()):
                with mock.patch(
                    "forecasting.forecast_tracker.ForecastTracker",
                    side_effect=RuntimeError("unavailable"),
                ):
                    out = obs.observability_summary()

        assert set(out) == {
            "portfolio_risk", "portfolio_heat", "equity_curve", "regime",
            "forecast_skill", "risk_gate_blocks",
        }

    def test_one_section_failure_never_blocks_the_others(self, tmp_path):
        """A crashing HistoricalStore must not prevent regime/risk-gate-block
        sections (which don't depend on it) from rendering their own data."""
        log_path = tmp_path / "risk_gate_blocks.jsonl"
        log_path.write_text(json.dumps({"symbol": "AAPL"}), encoding="utf-8")
        snapshot = {"timestamp": "t", "market_regime": "RISK ON", "sahm_rule": 0.1}

        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch(
                "data.historical_store.HistoricalStore",
                side_effect=RuntimeError("db locked"),
            ):
                with mock.patch(
                    "forecasting.forecast_tracker.ForecastTracker",
                    side_effect=RuntimeError("unavailable"),
                ):
                    out = obs.observability_summary(snapshot=snapshot)

        # The three DB-dependent sections degrade honestly...
        assert out["portfolio_risk"]["reason"]
        assert out["portfolio_heat"]["heat_pct"] is None
        assert out["portfolio_heat"]["reason"]
        assert out["equity_curve"]["points"] == []
        assert out["forecast_skill"]["reason"]
        # ...while the two sections independent of HistoricalStore/ForecastTracker
        # still work.
        assert out["regime"]["market_regime"] == "RISK ON"
        assert out["regime"]["reason"] is None
        assert out["risk_gate_blocks"]["count"] == 1
