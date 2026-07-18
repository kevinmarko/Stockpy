"""
tests/test_pilots_calibration.py
=================================
Unit tests for ``pilots/calibration.py`` — the pure, dead-letter-safe reader
that assembles the Recommendation Tracking & Calibration analytics for the
Pilots PWA's ``GET /calibration/summary`` + ``GET /calibration/edge-by-strategy``
endpoints: conviction calibration, model-vs-operator recommendation tracking,
per-signal MFE/MAE points, edge-ratio-by-strategy, and the recent-decisions tail.

All engine/DB dependencies are monkeypatched at their SOURCE module
(``transactions_store.TransactionsStore``, ``evaluation_engine.*``,
``data.historical_store.HistoricalStore``, ``gui.decision_log.*``) rather than
on ``pilots.calibration`` itself, since that module does lazy (inside-function)
imports — mirroring ``tests/test_pilots_observability.py``'s convention.
"""

from __future__ import annotations

from unittest import mock

import pandas as pd
import pytest

from pilots import calibration as cal


# ---------------------------------------------------------------------------
# calibration_view
# ---------------------------------------------------------------------------


def _cal_frame(records):
    return pd.DataFrame(
        records,
        columns=[
            "bin_low",
            "bin_high",
            "bin_center",
            "conviction_mean",
            "win_rate",
            "count",
            "perfect_calibration",
        ],
    )


class TestCalibrationView:
    def test_cold_start_empty_schema(self):
        class _Store:
            def closed_trades_df(self):
                return pd.DataFrame()

        with mock.patch("transactions_store.TransactionsStore", return_value=_Store()):
            out = cal.calibration_view()

        assert out["bins"] == []
        assert out["total"] == 0
        assert out["overall_win_rate"] is None
        assert out["calibration_error"] is None
        assert out["n_scored_bins"] == 0
        assert out["reason"]

    def test_happy_path_summary_and_null_under_min(self):
        # Two populated bins (win_rate present) + one under-min bin (win_rate NaN).
        cal_df = _cal_frame(
            [
                {
                    "bin_low": 0.5,
                    "bin_high": 0.6,
                    "bin_center": 0.55,
                    "conviction_mean": 0.55,
                    "win_rate": 0.60,
                    "count": 10,
                    "perfect_calibration": 0.55,
                },
                {
                    "bin_low": 0.7,
                    "bin_high": 0.8,
                    "bin_center": 0.75,
                    "conviction_mean": 0.75,
                    "win_rate": 0.80,
                    "count": 10,
                    "perfect_calibration": 0.75,
                },
                {
                    "bin_low": 0.9,
                    "bin_high": 1.0,
                    "bin_center": 0.95,
                    "conviction_mean": 0.95,
                    "win_rate": float("nan"),  # under min_trades_per_bin
                    "count": 2,
                    "perfect_calibration": 0.95,
                },
            ]
        )

        class _Store:
            def closed_trades_df(self):
                return pd.DataFrame({"conviction": [0.5] * 22})

        with mock.patch("transactions_store.TransactionsStore", return_value=_Store()):
            with mock.patch("evaluation_engine.calibration_curve", return_value=cal_df):
                out = cal.calibration_view()

        assert len(out["bins"]) == 3
        assert out["total"] == 22
        # The under-min bin serializes win_rate as null (never fabricated).
        under_min = out["bins"][2]
        assert under_min["win_rate"] is None
        assert under_min["count"] == 2
        # Count-weighted overall over the two scored bins: (0.6*10 + 0.8*10)/20.
        assert out["overall_win_rate"] == pytest.approx(0.70)
        # calibration_error = mean(|0.60-0.55|, |0.80-0.75|) = 0.05.
        assert out["calibration_error"] == pytest.approx(0.05)
        assert out["n_scored_bins"] == 2
        assert out["reason"] is None

    def test_never_raises_on_store_failure(self):
        with mock.patch(
            "transactions_store.TransactionsStore", side_effect=RuntimeError("db down")
        ):
            out = cal.calibration_view()
        assert out["bins"] == []
        assert out["reason"]


# ---------------------------------------------------------------------------
# recommendation_tracking_view
# ---------------------------------------------------------------------------


class TestRecommendationTrackingView:
    def test_all_empty_no_buy_signals(self):
        empty_report = {
            "rows": [],
            "model_return_30d": float("nan"),
            "operator_return_30d": float("nan"),
            "delta": float("nan"),
            "n_signals": 0,
            "n_acted": 0,
            "n_completed": 0,
            "n_with_exit": 0,
            "horizon_days": 30,
        }
        with mock.patch("transactions_store.TransactionsStore", return_value=object()):
            with mock.patch(
                "evaluation_engine.recommendation_tracking_report",
                return_value=empty_report,
            ):
                out = cal.recommendation_tracking_view(30)

        assert out["n_signals"] == 0
        assert out["model_return"] is None
        assert out["operator_return"] is None
        assert out["delta"] is None
        assert out["rows"] == []
        assert out["reason"]

    def test_happy_path_serializes_and_maps_keys(self):
        report = {
            "rows": [
                {
                    "symbol": "AAPL",
                    "signal_ts": "2026-06-01T00:00:00",
                    "signal_action": "BUY",
                    "conviction": 0.8,
                    "action_taken": "acted",
                    "model_return": 0.05,
                    "actual_return": 0.03,
                    "days_held": 12,
                    "trade_id": 7,
                    "completed": True,
                },
                {
                    "symbol": "MSFT",
                    "signal_ts": "2026-06-02T00:00:00",
                    "signal_action": "STRONG BUY",
                    "conviction": 0.9,
                    "action_taken": "passed",
                    "model_return": float("nan"),  # horizon not elapsed
                    "actual_return": float("nan"),
                    "days_held": None,
                    "trade_id": None,
                    "completed": False,
                },
            ],
            "model_return_30d": 0.05,
            "operator_return_30d": 0.03,
            "delta": -0.02,
            "n_signals": 2,
            "n_acted": 1,
            "n_completed": 1,
            "n_with_exit": 1,
            "horizon_days": 30,
        }
        with mock.patch("transactions_store.TransactionsStore", return_value=object()):
            with mock.patch(
                "evaluation_engine.recommendation_tracking_report",
                return_value=report,
            ):
                out = cal.recommendation_tracking_view(30)

        assert out["n_signals"] == 2
        assert out["model_return"] == pytest.approx(0.05)
        assert out["operator_return"] == pytest.approx(0.03)
        assert out["delta"] == pytest.approx(-0.02)
        assert out["reason"] is None
        assert len(out["rows"]) == 2
        # NaN model/actual returns for the incomplete row serialize to null.
        assert out["rows"][1]["model_return"] is None
        assert out["rows"][1]["actual_return"] is None
        assert out["rows"][1]["trade_id"] is None
        assert out["rows"][0]["trade_id"] == 7
        assert out["rows"][0]["days_held"] == 12

    def test_never_raises_on_report_failure(self):
        with mock.patch("transactions_store.TransactionsStore", return_value=object()):
            with mock.patch(
                "evaluation_engine.recommendation_tracking_report",
                side_effect=RuntimeError("boom"),
            ):
                out = cal.recommendation_tracking_view(45)
        assert out["horizon_days"] == 45
        assert out["n_signals"] == 0
        assert out["reason"]


# ---------------------------------------------------------------------------
# mfe_mae_view (pure snapshot read)
# ---------------------------------------------------------------------------


class TestMfeMaeView:
    def test_none_snapshot(self):
        out = cal.mfe_mae_view(None)
        assert out["points"] == []
        assert out["reason"]

    def test_skips_nan_and_keeps_valid(self):
        snapshot = {
            "signals": [
                {
                    "symbol": "AAPL",
                    "mfe": 0.08,
                    "mae": 0.03,
                    "edge_ratio": 2.67,
                    "advisory_conviction": 0.75,
                    "action": "BUY",
                },
                {
                    "symbol": "NOEXC",  # missing mae -> skipped, never fabricated
                    "mfe": 0.05,
                    "mae": None,
                },
            ]
        }
        out = cal.mfe_mae_view(snapshot)
        assert len(out["points"]) == 1
        pt = out["points"][0]
        assert pt["symbol"] == "AAPL"
        assert pt["mfe"] == pytest.approx(0.08)
        assert pt["conviction"] == pytest.approx(0.75)
        assert pt["action"] == "BUY"
        assert out["reason"] is None

    def test_all_nan_honest_empty(self):
        snapshot = {"signals": [{"symbol": "X", "mfe": None, "mae": None}]}
        out = cal.mfe_mae_view(snapshot)
        assert out["points"] == []
        assert out["reason"]


# ---------------------------------------------------------------------------
# edge_by_strategy_view
# ---------------------------------------------------------------------------


class TestEdgeByStrategyView:
    def test_no_closed_trades(self):
        class _Store:
            def closed_trades_df(self):
                return pd.DataFrame()

        with mock.patch("transactions_store.TransactionsStore", return_value=_Store()):
            out = cal.edge_by_strategy_view()
        assert out["rows"] == []
        assert out["reason"]

    def test_happy_path_groups_by_strategy(self):
        closed = pd.DataFrame(
            {
                "symbol": ["AAPL", "MSFT"],
                "entry_price": [100.0, 200.0],
                "entry_ts": [pd.Timestamp("2026-01-01"), pd.Timestamp("2026-02-01")],
                "exit_ts": [pd.Timestamp("2026-01-10"), pd.Timestamp("2026-02-10")],
                "strategy": ["trend", "trend"],
            }
        )

        class _Store:
            def closed_trades_df(self):
                return closed

        class _HStore:
            def get_bars(self, sym, lookback_days=756):
                idx = pd.date_range("2026-01-01", periods=60, freq="D")
                return pd.DataFrame(
                    {
                        "Open": 100.0,
                        "High": 110.0,
                        "Low": 95.0,
                        "Close": 105.0,
                        "Volume": 1000,
                    },
                    index=idx,
                )

        edge_ret = {"MFE": 0.10, "MAE": 0.05, "Edge Ratio": 2.0, "Return Std Dev": 0.01}

        with mock.patch("transactions_store.TransactionsStore", return_value=_Store()):
            with mock.patch("data.historical_store.HistoricalStore", return_value=_HStore()):
                with mock.patch(
                    "evaluation_engine.EvaluationEngine"
                ) as MockEE:
                    MockEE.return_value.calculate_edge_ratio.return_value = edge_ret
                    out = cal.edge_by_strategy_view()

        assert len(out["rows"]) == 1
        row = out["rows"][0]
        assert row["strategy"] == "trend"
        assert row["n_trades"] == 2
        assert row["mean_edge_ratio"] == pytest.approx(2.0)
        assert row["mean_mfe"] == pytest.approx(0.10)
        assert out["reason"] is None

    def test_never_raises_on_store_failure(self):
        with mock.patch(
            "transactions_store.TransactionsStore", side_effect=RuntimeError("db down")
        ):
            out = cal.edge_by_strategy_view()
        assert out["rows"] == []
        assert out["reason"]


# ---------------------------------------------------------------------------
# recent_decisions_view
# ---------------------------------------------------------------------------


class TestRecentDecisionsView:
    def test_empty_log(self, tmp_path):
        with mock.patch(
            "gui.decision_log.decisions_df", return_value=pd.DataFrame()
        ):
            out = cal.recent_decisions_view(log_path=tmp_path / "missing.jsonl")
        assert out["decisions"] == []
        assert out["reason"]

    def test_newest_first_and_null_trade_id(self, tmp_path):
        df = pd.DataFrame(
            {
                "symbol": ["AAPL", "MSFT"],
                "action_taken": ["acted", "passed"],
                "signal_action": ["BUY", "BUY"],
                "conviction": [0.8, 0.7],
                "notes": ["", ""],
                "timestamp": ["2026-06-01T00:00:00", "2026-06-02T00:00:00"],
                "signal_ts": ["", ""],
                "trade_id": pd.array([7, None], dtype="Int64"),
            }
        )
        with mock.patch("gui.decision_log.decisions_df", return_value=df):
            out = cal.recent_decisions_view(log_path=tmp_path / "log.jsonl")

        assert len(out["decisions"]) == 2
        # Newest-first ordering (MSFT@06-02 before AAPL@06-01).
        assert out["decisions"][0]["symbol"] == "MSFT"
        assert out["decisions"][0]["trade_id"] is None  # NA -> null, never fabricated
        assert out["decisions"][1]["symbol"] == "AAPL"
        assert out["decisions"][1]["trade_id"] == 7


# ---------------------------------------------------------------------------
# calibration_summary composite
# ---------------------------------------------------------------------------


class TestCalibrationSummaryComposite:
    def test_composite_shape_cold_start(self):
        class _Store:
            def closed_trades_df(self):
                return pd.DataFrame()

        with mock.patch("transactions_store.TransactionsStore", return_value=_Store()):
            with mock.patch(
                "evaluation_engine.recommendation_tracking_report",
                return_value={
                    "rows": [],
                    "model_return_30d": float("nan"),
                    "operator_return_30d": float("nan"),
                    "delta": float("nan"),
                    "n_signals": 0,
                    "n_acted": 0,
                    "n_completed": 0,
                    "n_with_exit": 0,
                    "horizon_days": 30,
                },
            ):
                with mock.patch(
                    "gui.decision_log.decisions_df", return_value=pd.DataFrame()
                ):
                    out = cal.calibration_summary(horizon_days=30, snapshot=None)

        assert set(out) == {
            "calibration",
            "recommendation_tracking",
            "mfe_mae",
            "recent_decisions",
        }
        assert out["calibration"]["bins"] == []
        assert out["recommendation_tracking"]["n_signals"] == 0
        assert out["mfe_mae"]["points"] == []
        assert out["recent_decisions"]["decisions"] == []
