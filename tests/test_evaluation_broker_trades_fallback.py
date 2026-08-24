"""Tests for evaluation_engine.py's opt-in settings.EVAL_BROKER_TRADES_ENABLED
fallback: evaluate_portfolio() falling back to broker-reconstructed closed
trades (data/broker_fills_store.py) for MAE/MFE/'Edge Ratio' on a symbol with
NO internal transactions_store trade history.

Covers:
  * Flag off (default) -- byte-identical NaN behavior to today, matching
    tests/test_evaluation_no_history.py's existing assertions.
  * Flag on + a broker-only symbol -- real MAE/MFE/Edge Ratio, computed from
    the broker trade's entry/exit against real hold-period OHLC.
  * Internal history always wins when both an internal AND a broker trade
    exist for the same symbol.
  * transactions_store's `trades` table is never written by any of this.
  * A broker-fills-store failure degrades to NaN, never raises.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

import transactions_store
from data.broker_fills_store import BrokerFillsStore
from data.robinhood_orders import OrderFill
from evaluation_engine import EvaluationEngine


def _empty_transactions_store(monkeypatch):
    """Point TransactionsStore at a fresh in-memory DB for this test,
    mirroring tests/test_evaluation_no_history.py's exact pattern."""
    store = transactions_store.TransactionsStore(db_url="sqlite:///:memory:")

    def mock_init(self, db_url=None, *, readonly=False, **kwargs):
        self.engine = store.engine
        self.Session = store.Session

    monkeypatch.setattr(transactions_store.TransactionsStore, "__init__", mock_init)
    return store


def _hold_period_history():
    date_range = pd.date_range(start="2026-06-20", end="2026-06-24", freq="D")
    hist = pd.DataFrame({
        "High": [100.0, 105.0, 110.0, 108.0, 104.0],
        "Low": [100.0, 98.0, 95.0, 97.0, 101.0],
        "Close": [100.0, 103.0, 107.0, 105.0, 103.0],
    }, index=date_range)
    hist.index = hist.index.tz_localize(None)
    return hist


def _base_test_df(symbol="AAPL"):
    return pd.DataFrame({
        "Symbol": [symbol],
        "sector": ["Technology"],
        "position_size": [5000.0],
        "stop_loss_pct": [0.05],
        "Relative_Strength": [0.0],
    })


def _benchmark_df():
    return pd.DataFrame({"sector": ["Technology"], "weight": [1.0], "return": [0.02]})


class TestFlagOffByteIdentical:
    def test_broker_trade_exists_but_flag_off_still_yields_nan(self, monkeypatch, tmp_path):
        """A real broker fill exists, but with the flag at its default
        (False), the result must be identical to having no broker data at
        all -- today's exact behavior is preserved."""
        monkeypatch.setattr("settings.settings.EVAL_BROKER_TRADES_ENABLED", False)
        _empty_transactions_store(monkeypatch)

        import data.broker_fills_store as bfs

        db_url = f"sqlite:///{tmp_path}/off.db"
        monkeypatch.setattr(bfs, "resolve_database_url", lambda: db_url)
        store = BrokerFillsStore(db_url=db_url)
        store.record_fills([
            OrderFill("AAPL", "buy", 50, 100.0, datetime(2026, 6, 20, 9, 30, tzinfo=timezone.utc), "a1"),
            OrderFill("AAPL", "sell", 50, 107.0, datetime(2026, 6, 24, 9, 30, tzinfo=timezone.utc), "a2"),
        ])

        ee = EvaluationEngine()
        processed = ee.evaluate_portfolio(
            _base_test_df(), _benchmark_df(), data_provider={"AAPL": _hold_period_history()}
        )
        assert np.isnan(processed.iloc[0]["MAE"])
        assert np.isnan(processed.iloc[0]["MFE"])
        assert np.isnan(processed.iloc[0]["Edge Ratio"])


class TestFlagOnBrokerFallback:
    def test_broker_only_symbol_yields_real_mae_mfe(self, monkeypatch, tmp_path):
        monkeypatch.setattr("settings.settings.EVAL_BROKER_TRADES_ENABLED", True)
        _empty_transactions_store(monkeypatch)  # no internal history at all

        import data.broker_fills_store as bfs

        db_url = f"sqlite:///{tmp_path}/on.db"
        monkeypatch.setattr(bfs, "resolve_database_url", lambda: db_url)
        store = BrokerFillsStore(db_url=db_url)
        store.record_fills([
            OrderFill("AAPL", "buy", 50, 100.0, datetime(2026, 6, 20, 9, 30, tzinfo=timezone.utc), "a1"),
            OrderFill("AAPL", "sell", 50, 107.0, datetime(2026, 6, 24, 9, 30, tzinfo=timezone.utc), "a2"),
        ])

        ee = EvaluationEngine()
        processed = ee.evaluate_portfolio(
            _base_test_df(), _benchmark_df(), data_provider={"AAPL": _hold_period_history()}
        )
        # Same hold-period OHLC as test_evaluation_with_history.py's fixture:
        # MAE = (100-95)/100 = 0.05, MFE = (110-100)/100 = 0.10, Edge Ratio = 2.0.
        assert math.isclose(processed.iloc[0]["MAE"], 0.05, abs_tol=1e-3)
        assert math.isclose(processed.iloc[0]["MFE"], 0.10, abs_tol=1e-3)
        assert math.isclose(processed.iloc[0]["Edge Ratio"], 2.0, abs_tol=1e-3)

    def test_internal_history_always_wins_over_broker(self, monkeypatch, tmp_path):
        """When BOTH an internal trade and a broker trade exist for the same
        symbol, the internal one must be used -- broker data is a fallback
        for symbols with NO internal history, never an override."""
        monkeypatch.setattr("settings.settings.EVAL_BROKER_TRADES_ENABLED", True)
        internal_store = _empty_transactions_store(monkeypatch)
        internal_store.record_trade(
            symbol="AAPL", side="long",
            entry_ts=datetime(2026, 6, 20, 9, 30, 0), entry_price=100.0, shares=50.0,
        )

        import data.broker_fills_store as bfs

        db_url = f"sqlite:///{tmp_path}/both.db"
        monkeypatch.setattr(bfs, "resolve_database_url", lambda: db_url)
        broker_store = BrokerFillsStore(db_url=db_url)
        # A wildly different broker trade -- if this were used instead, the
        # MAE/MFE below would be very different (entry 50.0, not 100.0).
        broker_store.record_fills([
            OrderFill("AAPL", "buy", 10, 50.0, datetime(2026, 6, 20, 9, 30, tzinfo=timezone.utc), "b1"),
            OrderFill("AAPL", "sell", 10, 55.0, datetime(2026, 6, 24, 9, 30, tzinfo=timezone.utc), "b2"),
        ])

        ee = EvaluationEngine()
        processed = ee.evaluate_portfolio(
            _base_test_df(), _benchmark_df(), data_provider={"AAPL": _hold_period_history()}
        )
        # Matches test_evaluation_with_history.py's internal-only result
        # exactly -- proof the broker trade was never consulted.
        assert math.isclose(processed.iloc[0]["MAE"], 0.05, abs_tol=1e-3)
        assert math.isclose(processed.iloc[0]["MFE"], 0.10, abs_tol=1e-3)

    def test_broker_store_failure_degrades_to_nan_never_raises(self, monkeypatch):
        monkeypatch.setattr("settings.settings.EVAL_BROKER_TRADES_ENABLED", True)
        _empty_transactions_store(monkeypatch)

        import data.broker_fills_store as bfs

        def _boom(*a, **kw):
            raise RuntimeError("db unreachable")

        monkeypatch.setattr(bfs, "BrokerFillsStore", _boom)

        ee = EvaluationEngine()
        processed = ee.evaluate_portfolio(_base_test_df(), _benchmark_df())  # must not raise
        assert np.isnan(processed.iloc[0]["MAE"])

    def test_never_writes_transactions_store_trades_table(self, monkeypatch, tmp_path):
        """The whole point of Decision A: broker fallback data must never
        land in the `trades` table, since that's what feeds live position
        sizing (sizing/kelly.py's aggregate path)."""
        monkeypatch.setattr("settings.settings.EVAL_BROKER_TRADES_ENABLED", True)
        internal_store = _empty_transactions_store(monkeypatch)

        import data.broker_fills_store as bfs

        db_url = f"sqlite:///{tmp_path}/isolation.db"
        monkeypatch.setattr(bfs, "resolve_database_url", lambda: db_url)
        broker_store = BrokerFillsStore(db_url=db_url)
        broker_store.record_fills([
            OrderFill("AAPL", "buy", 50, 100.0, datetime(2026, 6, 20, 9, 30, tzinfo=timezone.utc), "a1"),
            OrderFill("AAPL", "sell", 50, 107.0, datetime(2026, 6, 24, 9, 30, tzinfo=timezone.utc), "a2"),
        ])

        ee = EvaluationEngine()
        ee.evaluate_portfolio(
            _base_test_df(), _benchmark_df(), data_provider={"AAPL": _hold_period_history()}
        )

        assert internal_store.closed_trades_df().empty  # unchanged -- still zero rows
        assert internal_store.open_trades_df().empty
