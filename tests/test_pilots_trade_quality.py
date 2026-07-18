"""
tests/test_pilots_trade_quality.py
====================================
Tests for the Trade Quality (MFE/MAE + Edge Ratio) attribution feature:

* ``pilots.trade_quality.mfe_mae_scatter`` — pure function, portfolio-wide
  scatter over the latest pipeline snapshot's signals.
* ``pilots.trade_quality.edge_ratio_by_strategy`` — pure function, batch
  recompute over closed trades grouped by strategy.
* ``GET /portfolio/trade-quality`` on ``api/pilots_api.py`` — composes both,
  fail-open read tier (reachable without any command token).

Mirrors the style of ``tests/test_pilots_api.py::TestPortfolioAttribution``
(same module, same auth conventions, same ``mock.patch.object`` idioms for
``HistoricalStore``/``TransactionsStore``/``settings.OUTPUT_DIR``).
"""
from __future__ import annotations

from unittest import mock

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from settings import settings
import api.pilots_api as pilots_api
from pilots import trade_quality

client = TestClient(pilots_api.app)


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


def _bars_frame(start: str, periods: int, high: float, low: float, close: float) -> pd.DataFrame:
    """A minimal OHLCV frame with constant High/Low/Close over `periods` days
    starting at `start` — deliberately constant so MFE/MAE are hand-verifiable."""
    idx = pd.date_range(start, periods=periods, freq="D")
    return pd.DataFrame(
        {
            "Open": [close] * periods,
            "High": [high] * periods,
            "Low": [low] * periods,
            "Close": [close] * periods,
            "Volume": [1_000] * periods,
        },
        index=idx,
    )


def _closed_trades_df(rows: list) -> pd.DataFrame:
    """Build a DataFrame shaped like
    ``transactions_store.TransactionsStore.closed_trades_df()`` — columns:
    trade_id, symbol, side, entry_ts, entry_price, exit_ts, exit_price,
    shares, strategy, notes, conviction."""
    defaults = {
        "trade_id": 0, "side": "long", "exit_price": 0.0, "shares": 1.0,
        "notes": None, "conviction": None,
    }
    full_rows = []
    for i, r in enumerate(rows):
        row = dict(defaults)
        row["trade_id"] = i + 1
        row.update(r)
        full_rows.append(row)
    return pd.DataFrame(full_rows)


class _ClosedStore:
    """Stand-in for ``TransactionsStore(readonly=True)`` exposing only
    ``closed_trades_df()``, matching how ``TestPortfolioAttribution`` mocks
    ``HistoricalStore``."""

    def __init__(self, df: pd.DataFrame):
        self._df = df

    def closed_trades_df(self) -> pd.DataFrame:
        return self._df


class _BarsStore:
    """Stand-in for ``HistoricalStore(readonly=True)`` exposing only
    ``get_bars()``."""

    def __init__(self, bars_by_symbol: dict):
        self._bars = bars_by_symbol

    def get_bars(self, symbol, lookback_days=756, provider=None):
        return self._bars.get(symbol, pd.DataFrame())


# ---------------------------------------------------------------------------
# pilots.trade_quality.mfe_mae_scatter
# ---------------------------------------------------------------------------


class TestMfeMaeScatter:
    def test_empty_signals(self):
        assert trade_quality.mfe_mae_scatter([]) == []

    def test_drops_signal_missing_mfe_or_mae(self):
        """A symbol missing either mfe or mae is dropped entirely — never
        plotted as a fabricated origin point (CONSTRAINT #4)."""
        signals = [
            {"symbol": "AAPL", "mfe": 0.05, "mae": 0.02, "edge_ratio": 2.5,
             "advisory_conviction": 0.7, "action": "BUY"},
            {"symbol": "MSFT", "mae": 0.03},  # missing mfe entirely
            {"symbol": "NVDA", "mfe": 0.04},  # missing mae entirely
            {"symbol": "TSLA", "mfe": 0.1, "mae": 0.0},  # mae=0.0 is a real value, not missing
        ]
        rows = trade_quality.mfe_mae_scatter(signals)
        symbols = {r["symbol"] for r in rows}
        assert symbols == {"AAPL", "TSLA"}

    def test_row_shape_and_none_fallbacks(self):
        signals = [{"symbol": "AAPL", "mfe": 0.05, "mae": 0.02}]
        rows = trade_quality.mfe_mae_scatter(signals)
        assert rows == [{
            "symbol": "AAPL", "mfe": 0.05, "mae": 0.02,
            "edge_ratio": None, "conviction": None, "action": None,
        }]

    def test_action_falls_back_to_advisory_action(self):
        signals = [{"symbol": "AAPL", "mfe": 0.05, "mae": 0.02, "advisory_action": "SELL"}]
        assert trade_quality.mfe_mae_scatter(signals)[0]["action"] == "SELL"

    def test_action_prefers_action_over_advisory_action(self):
        signals = [{"symbol": "AAPL", "mfe": 0.05, "mae": 0.02, "action": "HOLD", "advisory_action": "SELL"}]
        assert trade_quality.mfe_mae_scatter(signals)[0]["action"] == "HOLD"

    def test_non_dict_signal_skipped(self):
        assert trade_quality.mfe_mae_scatter(["not-a-dict", 42, None]) == []

    def test_nan_mfe_treated_as_missing(self):
        signals = [{"symbol": "AAPL", "mfe": float("nan"), "mae": 0.02}]
        assert trade_quality.mfe_mae_scatter(signals) == []


# ---------------------------------------------------------------------------
# pilots.trade_quality.edge_ratio_by_strategy
# ---------------------------------------------------------------------------


class TestEdgeRatioByStrategy:
    def test_no_closed_trades(self):
        result = trade_quality.edge_ratio_by_strategy(pd.DataFrame(), {})
        assert result == {"by_strategy": [], "reason": "no closed trades yet"}

    def test_none_closed_trades(self):
        result = trade_quality.edge_ratio_by_strategy(None, {})
        assert result == {"by_strategy": [], "reason": "no closed trades yet"}

    def test_groups_by_strategy_with_hand_verifiable_numbers(self):
        """Two 'trend' trades + one 'meanrev' trade, plus a fourth trade whose
        symbol has no bars (must be skipped, never fabricated).

        AAA (trend): entry=100, High=112, Low=95
            MFE = (112-100)/100 = 0.12, MAE = (100-95)/100 = 0.05, Edge = 2.4
        BBB (trend): entry=50, High=53, Low=48
            MFE = (53-50)/50 = 0.06, MAE = (50-48)/50 = 0.04, Edge = 1.5
        trend avg: MFE=(0.12+0.06)/2=0.09, MAE=(0.05+0.04)/2=0.045, Edge=(2.4+1.5)/2=1.95

        CCC (meanrev): entry=200, High=220, Low=190
            MFE = (220-200)/200 = 0.10, MAE = (200-190)/200 = 0.05, Edge = 2.0
        meanrev avg: MFE=0.10, MAE=0.05, Edge=2.0 (single trade)

        DDD (trend, no bars available): skipped entirely.
        """
        closed = _closed_trades_df([
            {"symbol": "AAA", "strategy": "trend", "entry_price": 100.0,
             "entry_ts": pd.Timestamp("2026-01-01"), "exit_ts": pd.Timestamp("2026-01-05")},
            {"symbol": "BBB", "strategy": "trend", "entry_price": 50.0,
             "entry_ts": pd.Timestamp("2026-01-01"), "exit_ts": pd.Timestamp("2026-01-05")},
            {"symbol": "CCC", "strategy": "meanrev", "entry_price": 200.0,
             "entry_ts": pd.Timestamp("2026-01-01"), "exit_ts": pd.Timestamp("2026-01-05")},
            {"symbol": "DDD", "strategy": "trend", "entry_price": 10.0,
             "entry_ts": pd.Timestamp("2026-01-01"), "exit_ts": pd.Timestamp("2026-01-05")},
        ])
        bars_by_symbol = {
            "AAA": _bars_frame("2026-01-01", 10, high=112.0, low=95.0, close=100.0),
            "BBB": _bars_frame("2026-01-01", 10, high=53.0, low=48.0, close=50.0),
            "CCC": _bars_frame("2026-01-01", 10, high=220.0, low=190.0, close=200.0),
            # DDD deliberately absent from bars_by_symbol.
        }

        result = trade_quality.edge_ratio_by_strategy(closed, bars_by_symbol)
        assert result["reason"] is None
        by_strategy = {row["strategy"]: row for row in result["by_strategy"]}

        assert set(by_strategy.keys()) == {"trend", "meanrev"}

        trend = by_strategy["trend"]
        assert trend["n_trades"] == 2
        assert trend["avg_mfe"] == pytest.approx(0.09)
        assert trend["avg_mae"] == pytest.approx(0.045)
        assert trend["avg_edge_ratio"] == pytest.approx(1.95)

        meanrev = by_strategy["meanrev"]
        assert meanrev["n_trades"] == 1
        assert meanrev["avg_mfe"] == pytest.approx(0.10)
        assert meanrev["avg_mae"] == pytest.approx(0.05)
        assert meanrev["avg_edge_ratio"] == pytest.approx(2.0)

    def test_untagged_strategy_grouped_together(self):
        closed = _closed_trades_df([
            {"symbol": "AAA", "strategy": None, "entry_price": 100.0,
             "entry_ts": pd.Timestamp("2026-01-01"), "exit_ts": pd.Timestamp("2026-01-05")},
            {"symbol": "BBB", "strategy": "", "entry_price": 50.0,
             "entry_ts": pd.Timestamp("2026-01-01"), "exit_ts": pd.Timestamp("2026-01-05")},
        ])
        bars_by_symbol = {
            "AAA": _bars_frame("2026-01-01", 10, high=112.0, low=95.0, close=100.0),
            "BBB": _bars_frame("2026-01-01", 10, high=53.0, low=48.0, close=50.0),
        }
        result = trade_quality.edge_ratio_by_strategy(closed, bars_by_symbol)
        assert len(result["by_strategy"]) == 1
        assert result["by_strategy"][0]["strategy"] == "(untagged)"
        assert result["by_strategy"][0]["n_trades"] == 2

    def test_all_trades_skipped_no_bars_yields_honest_empty(self):
        closed = _closed_trades_df([
            {"symbol": "ZZZ", "strategy": "trend", "entry_price": 100.0,
             "entry_ts": pd.Timestamp("2026-01-01"), "exit_ts": pd.Timestamp("2026-01-05")},
        ])
        result = trade_quality.edge_ratio_by_strategy(closed, {})
        assert result == {
            "by_strategy": [],
            "reason": "no closed trades with recoverable OHLC history yet",
        }

    def test_empty_bars_for_symbol_skipped(self):
        closed = _closed_trades_df([
            {"symbol": "ZZZ", "strategy": "trend", "entry_price": 100.0,
             "entry_ts": pd.Timestamp("2026-01-01"), "exit_ts": pd.Timestamp("2026-01-05")},
        ])
        result = trade_quality.edge_ratio_by_strategy(closed, {"ZZZ": pd.DataFrame()})
        assert result["by_strategy"] == []
        assert result["reason"] == "no closed trades with recoverable OHLC history yet"

    def test_non_positive_entry_price_skipped(self):
        closed = _closed_trades_df([
            {"symbol": "AAA", "strategy": "trend", "entry_price": 0.0,
             "entry_ts": pd.Timestamp("2026-01-01"), "exit_ts": pd.Timestamp("2026-01-05")},
        ])
        bars_by_symbol = {"AAA": _bars_frame("2026-01-01", 10, high=112.0, low=95.0, close=100.0)}
        result = trade_quality.edge_ratio_by_strategy(closed, bars_by_symbol)
        assert result["by_strategy"] == []
        assert result["reason"] == "no closed trades with recoverable OHLC history yet"

    def test_nat_entry_or_exit_ts_skipped(self):
        closed = _closed_trades_df([
            {"symbol": "AAA", "strategy": "trend", "entry_price": 100.0,
             "entry_ts": pd.NaT, "exit_ts": pd.Timestamp("2026-01-05")},
        ])
        bars_by_symbol = {"AAA": _bars_frame("2026-01-01", 10, high=112.0, low=95.0, close=100.0)}
        result = trade_quality.edge_ratio_by_strategy(closed, bars_by_symbol)
        assert result["by_strategy"] == []
        assert result["reason"] == "no closed trades with recoverable OHLC history yet"


# ---------------------------------------------------------------------------
# GET /portfolio/trade-quality
# ---------------------------------------------------------------------------


class TestPortfolioTradeQualityEndpoint:
    def test_cold_start_no_snapshot_no_trades(self, tmp_path):
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(pilots_api, "TransactionsStore", return_value=_ClosedStore(pd.DataFrame())):
                resp = client.get("/portfolio/trade-quality")
        assert resp.status_code == 200
        body = resp.json()
        assert body["as_of"] is None
        assert body["scatter"] == []
        assert body["edge_ratio_by_strategy"] == {"by_strategy": [], "reason": "no closed trades yet"}

    def test_transactions_store_error_degrades_never_500(self, tmp_path):
        def _boom(*args, **kwargs):
            raise RuntimeError("db unreachable")

        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(pilots_api, "TransactionsStore", side_effect=_boom):
                resp = client.get("/portfolio/trade-quality")
        assert resp.status_code == 200
        assert resp.json()["edge_ratio_by_strategy"] == {"by_strategy": [], "reason": "no closed trades yet"}

    def test_scatter_sourced_from_latest_snapshot(self, tmp_path):
        import json as _json

        snapshot = {
            "timestamp": "2026-07-17T00:00:00+00:00",
            "signals": [
                {"symbol": "AAPL", "mfe": 0.08, "mae": 0.03, "edge_ratio": 2.67,
                 "advisory_conviction": 0.65, "action": "BUY"},
                {"symbol": "MSFT", "mfe": None, "mae": 0.02},  # dropped: missing mfe
            ],
        }
        (tmp_path / "state_snapshot.json").write_text(_json.dumps(snapshot), encoding="utf-8")

        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(pilots_api, "TransactionsStore", return_value=_ClosedStore(pd.DataFrame())):
                resp = client.get("/portfolio/trade-quality")
        assert resp.status_code == 200
        body = resp.json()
        assert body["as_of"] == "2026-07-17T00:00:00+00:00"
        assert body["scatter"] == [{
            "symbol": "AAPL", "mfe": 0.08, "mae": 0.03,
            "edge_ratio": 2.67, "conviction": 0.65, "action": "BUY",
        }]

    def test_edge_ratio_by_strategy_end_to_end(self, tmp_path):
        closed = _closed_trades_df([
            {"symbol": "AAA", "strategy": "trend", "entry_price": 100.0,
             "entry_ts": pd.Timestamp("2026-01-01"), "exit_ts": pd.Timestamp("2026-01-05")},
        ])
        bars_by_symbol = {"AAA": _bars_frame("2026-01-01", 10, high=112.0, low=95.0, close=100.0)}

        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(pilots_api, "TransactionsStore", return_value=_ClosedStore(closed)):
                with mock.patch.object(pilots_api, "HistoricalStore", return_value=_BarsStore(bars_by_symbol)):
                    resp = client.get("/portfolio/trade-quality")
        assert resp.status_code == 200
        by_strategy = resp.json()["edge_ratio_by_strategy"]["by_strategy"]
        assert len(by_strategy) == 1
        assert by_strategy[0]["strategy"] == "trend"
        assert by_strategy[0]["n_trades"] == 1
        assert by_strategy[0]["avg_mfe"] == pytest.approx(0.12)
        assert by_strategy[0]["avg_mae"] == pytest.approx(0.05)
        assert by_strategy[0]["avg_edge_ratio"] == pytest.approx(2.4)

    def test_lookback_days_query_validation(self):
        resp = client.get("/portfolio/trade-quality?lookback_days=5")
        assert resp.status_code == 422
        resp = client.get("/portfolio/trade-quality?lookback_days=5000")
        assert resp.status_code == 422

    def test_reachable_without_any_token_when_unset(self, tmp_path):
        """Fail-open read tier (mirrors every other GET on this API): with
        STATE_API_TOKEN unset, no Authorization header is required."""
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                with mock.patch.object(pilots_api, "TransactionsStore", return_value=_ClosedStore(pd.DataFrame())):
                    resp = client.get("/portfolio/trade-quality")
        assert resp.status_code == 200

    def test_reachable_without_command_token(self, tmp_path):
        """The endpoint is gated ONLY by require_read_token — a configured
        FOLLOW_API_TOKEN (the command tier used by the write endpoints) must
        have no bearing on this GET at all, with or without a header."""
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", "some-command-token"):
                with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                    with mock.patch.object(pilots_api, "TransactionsStore", return_value=_ClosedStore(pd.DataFrame())):
                        resp = client.get("/portfolio/trade-quality")
        assert resp.status_code == 200

    def test_read_token_required_when_configured(self, tmp_path):
        with mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
            with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                with mock.patch.object(pilots_api, "TransactionsStore", return_value=_ClosedStore(pd.DataFrame())):
                    no_auth = client.get("/portfolio/trade-quality")
                    wrong = client.get(
                        "/portfolio/trade-quality",
                        headers={"Authorization": "Bearer WRONG"},
                    )
                    ok = client.get(
                        "/portfolio/trade-quality",
                        headers={"Authorization": "Bearer read-tok"},
                    )
        assert no_auth.status_code == 401
        assert wrong.status_code == 401
        assert ok.status_code == 200
