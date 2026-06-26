"""
tests/test_recommendation_tracking.py — Tier 4.1 recommendation tracking tests.

Verifies that ``evaluation_engine.recommendation_tracking_report()`` correctly
joins the 1.3 decision log with historical bar prices to produce model vs.
operator return comparisons.

All network I/O (HistoricalStore, TransactionsStore) is monkeypatched so the
suite is fully offline.

Test classes
------------
TestEmptyLog            — missing / empty decision log → empty result, no crash
TestNoBuySignals        — log with only HOLD/SELL → n_signals=0, no crash
TestModelReturn         — BUY + mock bars → correct model_return computed
TestActualReturn        — acted + trade_id + mock store → correct actual_return
TestPassedSignal        — "passed" entry → n_acted=0, actual_return=NaN
TestHorizonNotElapsed   — recent signal → completed=False, model_exit=NaN
TestConvictionWeighting — two signals with different convictions → weighted avg
TestDelta               — both returns present → delta = operator − model
TestDeadLetterResilience — HistoricalStore failure → graceful degradation
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from evaluation_engine import recommendation_tracking_report
from gui.decision_log import DecisionEntry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso(d: date, hour: int = 12) -> str:
    return datetime(d.year, d.month, d.day, hour, 0, 0).isoformat()


def _make_entry(
    symbol: str = "AAPL",
    action_taken: str = "passed",
    signal_action: str = "BUY",
    conviction: float = 0.75,
    signal_ts: Optional[str] = None,
    trade_id: Optional[int] = None,
    days_ago: int = 40,
) -> DecisionEntry:
    """Build a synthetic DecisionEntry positioned *days_ago* days before today."""
    sig_date = date.today() - timedelta(days=days_ago)
    return DecisionEntry(
        symbol=symbol,
        action_taken=action_taken,
        signal_action=signal_action,
        conviction=conviction,
        notes="",
        timestamp=_iso(sig_date),
        signal_ts=signal_ts or _iso(sig_date),
        trade_id=trade_id,
    )


def _make_bars(
    symbol: str,
    *,
    start_days_ago: int = 60,
    end_days_ago: int = 0,
    price_at_start: float = 100.0,
    price_at_end: float = 110.0,
) -> pd.DataFrame:
    """Build a synthetic OHLCV DataFrame covering the requested window."""
    today = date.today()
    start = today - timedelta(days=start_days_ago)
    end = today - timedelta(days=end_days_ago)
    dates = pd.date_range(start=start, end=end, freq="B")
    n = len(dates)
    prices = [
        price_at_start + (price_at_end - price_at_start) * i / max(n - 1, 1)
        for i in range(n)
    ]
    close = pd.Series(prices, index=dates)
    return pd.DataFrame(
        {
            "Open": close * 0.99,
            "High": close * 1.01,
            "Low": close * 0.98,
            "Close": close,
            "Volume": 1_000_000,
        },
        index=dates,
    )


class _FakeStore:
    """Minimal TransactionsStore stub."""

    def __init__(self, trades: Optional[List[dict]] = None) -> None:
        self._trades = trades or []

    def get_trade_history(self, symbol: str) -> pd.DataFrame:
        rows = [t for t in self._trades if t.get("symbol", "").upper() == symbol.upper()]
        if not rows:
            return pd.DataFrame(
                columns=["trade_id", "symbol", "side", "entry_ts",
                         "entry_price", "exit_ts", "exit_price", "shares"]
            )
        return pd.DataFrame(rows)


class _FakeHistoricalStore:
    """Minimal HistoricalStore stub backed by a symbol→DataFrame map."""

    def __init__(self, bars: Dict[str, pd.DataFrame]) -> None:
        self._bars = bars

    def get_bars(self, symbol: str, lookback_days: int = 504) -> pd.DataFrame:
        return self._bars.get(symbol.upper(), pd.DataFrame())


# ---------------------------------------------------------------------------
# TestEmptyLog
# ---------------------------------------------------------------------------

class TestEmptyLog:
    def test_missing_log_returns_empty_result(self, tmp_path: Path) -> None:
        result = recommendation_tracking_report(log_path=tmp_path / "missing.jsonl")
        assert result["n_signals"] == 0
        assert result["rows"] == []
        assert math.isnan(result["model_return_30d"])
        assert math.isnan(result["operator_return_30d"])
        assert math.isnan(result["delta"])

    def test_empty_log_file_returns_empty_result(self, tmp_path: Path) -> None:
        p = tmp_path / "log.jsonl"
        p.write_text("")
        result = recommendation_tracking_report(log_path=p)
        assert result["n_signals"] == 0

    def test_corrupt_log_is_tolerated(self, tmp_path: Path) -> None:
        p = tmp_path / "log.jsonl"
        p.write_text("{bad json}\n")
        result = recommendation_tracking_report(log_path=p)
        assert result["n_signals"] == 0

    def test_horizon_days_preserved_in_result(self, tmp_path: Path) -> None:
        result = recommendation_tracking_report(
            log_path=tmp_path / "x.jsonl", horizon_days=45
        )
        assert result["horizon_days"] == 45


# ---------------------------------------------------------------------------
# TestNoBuySignals
# ---------------------------------------------------------------------------

class TestNoBuySignals:
    def _write_entries(self, entries: List[DecisionEntry], path: Path) -> None:
        import json
        from dataclasses import asdict

        with open(path, "w") as fh:
            for e in entries:
                fh.write(json.dumps(asdict(e)) + "\n")

    def test_hold_signal_not_counted(self, tmp_path: Path) -> None:
        e = _make_entry(signal_action="HOLD")
        self._write_entries([e], tmp_path / "log.jsonl")
        result = recommendation_tracking_report(log_path=tmp_path / "log.jsonl")
        assert result["n_signals"] == 0

    def test_sell_signal_not_counted(self, tmp_path: Path) -> None:
        e = _make_entry(signal_action="SELL")
        self._write_entries([e], tmp_path / "log.jsonl")
        result = recommendation_tracking_report(log_path=tmp_path / "log.jsonl")
        assert result["n_signals"] == 0

    def test_strong_buy_is_counted(self, tmp_path: Path) -> None:
        e = _make_entry(signal_action="STRONG BUY", days_ago=40)
        self._write_entries([e], tmp_path / "log.jsonl")
        result = recommendation_tracking_report(log_path=tmp_path / "log.jsonl")
        assert result["n_signals"] == 1


# ---------------------------------------------------------------------------
# TestModelReturn
# ---------------------------------------------------------------------------

class TestModelReturn:
    def _write_one(self, path: Path, entry: DecisionEntry) -> None:
        import json
        from dataclasses import asdict

        with open(path, "w") as fh:
            fh.write(json.dumps(asdict(entry)) + "\n")

    def test_model_return_computed_from_bars(self, tmp_path: Path) -> None:
        # Signal 40 days ago → horizon 30d → completed
        entry = _make_entry(signal_action="BUY", days_ago=40, conviction=1.0)
        self._write_one(tmp_path / "log.jsonl", entry)

        # Bars: price starts at 100, rises linearly to 110 over 60 days
        bars = _make_bars("AAPL", start_days_ago=60, price_at_start=100.0, price_at_end=110.0)
        hs = _FakeHistoricalStore({"AAPL": bars})

        result = recommendation_tracking_report(
            log_path=tmp_path / "log.jsonl",
            horizon_days=30,
            historical_store=hs,
            _today=date.today(),
        )

        assert result["n_signals"] == 1
        assert result["n_completed"] == 1
        assert not math.isnan(result["model_return_30d"])
        # Model return should be positive (price went up)
        assert result["model_return_30d"] > 0

    def test_model_return_is_nan_when_no_bars(self, tmp_path: Path) -> None:
        entry = _make_entry(signal_action="BUY", days_ago=40, conviction=1.0)
        self._write_one(tmp_path / "log.jsonl", entry)
        hs = _FakeHistoricalStore({})  # no bars for AAPL

        result = recommendation_tracking_report(
            log_path=tmp_path / "log.jsonl",
            horizon_days=30,
            historical_store=hs,
        )
        assert result["n_completed"] == 1
        assert math.isnan(result["model_return_30d"])

    def test_model_return_single_signal_equals_price_change(self, tmp_path: Path) -> None:
        today = date.today()
        signal_date = today - timedelta(days=40)
        exit_date = signal_date + timedelta(days=30)

        # Build exact bars so we know the expected return
        dates = pd.date_range(start=today - timedelta(days=60), end=today, freq="B")
        closes = pd.Series(
            [100.0 if d.date() <= signal_date else 108.0 for d in dates],
            index=dates,
        )
        bars = pd.DataFrame({"Open": closes, "High": closes, "Low": closes,
                              "Close": closes, "Volume": 1e6}, index=dates)
        hs = _FakeHistoricalStore({"AAPL": bars})

        entry = _make_entry(signal_action="BUY", days_ago=40, conviction=1.0)
        self._write_one(tmp_path / "log.jsonl", entry)

        result = recommendation_tracking_report(
            log_path=tmp_path / "log.jsonl",
            horizon_days=30,
            historical_store=hs,
        )
        # With entry=100 and exit=108, model_return should be 0.08
        assert abs(result["model_return_30d"] - 0.08) < 0.01


# ---------------------------------------------------------------------------
# TestActualReturn
# ---------------------------------------------------------------------------

class TestActualReturn:
    def _write_one(self, path: Path, entry: DecisionEntry) -> None:
        import json
        from dataclasses import asdict

        with open(path, "w") as fh:
            fh.write(json.dumps(asdict(entry)) + "\n")

    def test_actual_return_computed_from_trade(self, tmp_path: Path) -> None:
        trade_id = 42
        today = date.today()
        entry_ts = (today - timedelta(days=35)).isoformat() + "T10:00:00"
        exit_ts = (today - timedelta(days=5)).isoformat() + "T10:00:00"

        entry = _make_entry(
            signal_action="BUY", action_taken="acted",
            days_ago=40, trade_id=trade_id, conviction=0.8,
        )
        self._write_one(tmp_path / "log.jsonl", entry)

        trade = {
            "trade_id": trade_id, "symbol": "AAPL", "side": "long",
            "entry_ts": entry_ts, "entry_price": 100.0,
            "exit_ts": exit_ts, "exit_price": 112.0, "shares": 10,
        }
        store = _FakeStore([trade])
        hs = _FakeHistoricalStore({"AAPL": _make_bars("AAPL")})

        result = recommendation_tracking_report(
            log_path=tmp_path / "log.jsonl",
            transactions_store=store,
            horizon_days=30,
            historical_store=hs,
        )

        assert result["n_acted"] == 1
        assert result["n_with_exit"] == 1
        assert not math.isnan(result["operator_return_30d"])
        # (112 - 100) / 100 = 0.12
        assert abs(result["operator_return_30d"] - 0.12) < 1e-6

    def test_open_trade_uses_latest_bar_close(self, tmp_path: Path) -> None:
        trade_id = 7
        today = date.today()
        entry_ts = (today - timedelta(days=10)).isoformat() + "T10:00:00"

        entry = _make_entry(
            signal_action="BUY", action_taken="acted",
            days_ago=40, trade_id=trade_id, conviction=1.0,
        )
        self._write_one(tmp_path / "log.jsonl", entry)

        trade = {
            "trade_id": trade_id, "symbol": "AAPL", "side": "long",
            "entry_ts": entry_ts, "entry_price": 100.0,
            "exit_ts": None, "exit_price": None, "shares": 5,
        }
        store = _FakeStore([trade])
        bars = _make_bars("AAPL", price_at_end=115.0)
        hs = _FakeHistoricalStore({"AAPL": bars})

        result = recommendation_tracking_report(
            log_path=tmp_path / "log.jsonl",
            transactions_store=store,
            horizon_days=30,
            historical_store=hs,
        )

        assert result["n_with_exit"] == 1
        assert result["operator_return_30d"] > 0  # 115 > 100

    def test_no_matching_trade_id_leaves_actual_return_nan(self, tmp_path: Path) -> None:
        entry = _make_entry(
            signal_action="BUY", action_taken="acted", trade_id=999, days_ago=40,
        )
        self._write_one(tmp_path / "log.jsonl", entry)

        store = _FakeStore([])  # empty — trade_id 999 not found
        hs = _FakeHistoricalStore({"AAPL": _make_bars("AAPL")})

        result = recommendation_tracking_report(
            log_path=tmp_path / "log.jsonl",
            transactions_store=store,
            historical_store=hs,
        )

        assert result["n_with_exit"] == 0
        assert math.isnan(result["operator_return_30d"])


# ---------------------------------------------------------------------------
# TestPassedSignal
# ---------------------------------------------------------------------------

class TestPassedSignal:
    def _write_one(self, path: Path, entry: DecisionEntry) -> None:
        import json
        from dataclasses import asdict

        with open(path, "w") as fh:
            fh.write(json.dumps(asdict(entry)) + "\n")

    def test_passed_signal_counted_in_n_signals_not_n_acted(self, tmp_path: Path) -> None:
        entry = _make_entry(signal_action="BUY", action_taken="passed", days_ago=40)
        self._write_one(tmp_path / "log.jsonl", entry)

        result = recommendation_tracking_report(
            log_path=tmp_path / "log.jsonl",
            historical_store=_FakeHistoricalStore({"AAPL": _make_bars("AAPL")}),
        )
        assert result["n_signals"] == 1
        assert result["n_acted"] == 0
        assert result["n_with_exit"] == 0
        assert math.isnan(result["operator_return_30d"])

    def test_passed_signal_included_in_model_return(self, tmp_path: Path) -> None:
        entry = _make_entry(signal_action="BUY", action_taken="passed",
                            days_ago=40, conviction=1.0)
        self._write_one(tmp_path / "log.jsonl", entry)

        result = recommendation_tracking_report(
            log_path=tmp_path / "log.jsonl",
            historical_store=_FakeHistoricalStore(
                {"AAPL": _make_bars("AAPL", price_at_start=100, price_at_end=105)}
            ),
        )
        # "passed" signals count for the model return (what WOULD have happened)
        assert not math.isnan(result["model_return_30d"])
        assert result["model_return_30d"] > 0


# ---------------------------------------------------------------------------
# TestHorizonNotElapsed
# ---------------------------------------------------------------------------

class TestHorizonNotElapsed:
    def _write_one(self, path: Path, entry: DecisionEntry) -> None:
        import json
        from dataclasses import asdict

        with open(path, "w") as fh:
            fh.write(json.dumps(asdict(entry)) + "\n")

    def test_recent_signal_not_completed(self, tmp_path: Path) -> None:
        # Signal only 5 days ago, horizon=30 → not completed yet
        entry = _make_entry(signal_action="BUY", days_ago=5, conviction=1.0)
        self._write_one(tmp_path / "log.jsonl", entry)

        result = recommendation_tracking_report(
            log_path=tmp_path / "log.jsonl",
            horizon_days=30,
            historical_store=_FakeHistoricalStore({"AAPL": _make_bars("AAPL")}),
        )
        assert result["n_signals"] == 1
        assert result["n_completed"] == 0
        # model_return_30d is NaN because no completed signals
        assert math.isnan(result["model_return_30d"])

    def test_completed_flag_in_rows(self, tmp_path: Path) -> None:
        recent = _make_entry(signal_action="BUY", days_ago=5)
        old = _make_entry(signal_action="BUY", days_ago=45)

        import json
        from dataclasses import asdict

        with open(tmp_path / "log.jsonl", "w") as fh:
            for e in [recent, old]:
                fh.write(json.dumps(asdict(e)) + "\n")

        result = recommendation_tracking_report(
            log_path=tmp_path / "log.jsonl",
            horizon_days=30,
            historical_store=_FakeHistoricalStore({"AAPL": _make_bars("AAPL")}),
        )
        assert result["n_signals"] == 2
        completions = [r["completed"] for r in result["rows"]]
        assert False in completions  # recent one not completed
        assert True in completions   # old one completed


# ---------------------------------------------------------------------------
# TestConvictionWeighting
# ---------------------------------------------------------------------------

class TestConvictionWeighting:
    def test_high_conviction_weighs_more_in_model_return(self, tmp_path: Path) -> None:
        import json
        from dataclasses import asdict

        # Two signals 40 days ago: one with high conviction/high return, one low/low
        today = date.today()
        sig_date = today - timedelta(days=40)

        # Bars for AAPL: rises strongly +20%
        dates_aapl = pd.date_range(today - timedelta(days=60), today, freq="B")
        close_aapl = pd.Series(
            [100.0 + 20.0 * i / max(len(dates_aapl) - 1, 1) for i in range(len(dates_aapl))],
            index=dates_aapl,
        )
        bars_aapl = pd.DataFrame({"Open": close_aapl, "High": close_aapl,
                                   "Low": close_aapl, "Close": close_aapl,
                                   "Volume": 1e6}, index=dates_aapl)

        # Bars for MSFT: falls -10%
        dates_msft = pd.date_range(today - timedelta(days=60), today, freq="B")
        close_msft = pd.Series(
            [200.0 - 20.0 * i / max(len(dates_msft) - 1, 1) for i in range(len(dates_msft))],
            index=dates_msft,
        )
        bars_msft = pd.DataFrame({"Open": close_msft, "High": close_msft,
                                   "Low": close_msft, "Close": close_msft,
                                   "Volume": 1e6}, index=dates_msft)

        hs = _FakeHistoricalStore({"AAPL": bars_aapl, "MSFT": bars_msft})

        e1 = DecisionEntry(
            symbol="AAPL", action_taken="passed", signal_action="BUY",
            conviction=0.9,  # high conviction on the winner
            notes="", timestamp=_iso(sig_date), signal_ts=_iso(sig_date),
        )
        e2 = DecisionEntry(
            symbol="MSFT", action_taken="passed", signal_action="BUY",
            conviction=0.1,  # low conviction on the loser
            notes="", timestamp=_iso(sig_date), signal_ts=_iso(sig_date),
        )

        with open(tmp_path / "log.jsonl", "w") as fh:
            for e in [e1, e2]:
                fh.write(json.dumps(asdict(e)) + "\n")

        result = recommendation_tracking_report(
            log_path=tmp_path / "log.jsonl",
            horizon_days=30,
            historical_store=hs,
        )
        assert result["n_signals"] == 2
        assert result["n_completed"] == 2
        # Weighted return should be positive overall (high-conviction winner dominates)
        assert not math.isnan(result["model_return_30d"])
        assert result["model_return_30d"] > 0


# ---------------------------------------------------------------------------
# TestDelta
# ---------------------------------------------------------------------------

class TestDelta:
    def test_delta_equals_operator_minus_model(self, tmp_path: Path) -> None:
        import json
        from dataclasses import asdict

        today = date.today()
        sig_date = today - timedelta(days=40)
        trade_id = 5

        # Bars: entry=100, 30d-exit=110, latest=120
        dates = pd.date_range(today - timedelta(days=60), today, freq="B")
        closes = pd.Series([100.0 + i * 0.5 for i in range(len(dates))], index=dates)
        bars = pd.DataFrame({"Open": closes, "High": closes, "Low": closes,
                              "Close": closes, "Volume": 1e6}, index=dates)
        hs = _FakeHistoricalStore({"AAPL": bars})

        entry = DecisionEntry(
            symbol="AAPL", action_taken="acted", signal_action="BUY",
            conviction=1.0, notes="",
            timestamp=_iso(sig_date), signal_ts=_iso(sig_date),
            trade_id=trade_id,
        )
        with open(tmp_path / "log.jsonl", "w") as fh:
            fh.write(json.dumps(asdict(entry)) + "\n")

        exit_ts = (today - timedelta(days=5)).isoformat() + "T00:00:00"
        store = _FakeStore([{
            "trade_id": trade_id, "symbol": "AAPL", "side": "long",
            "entry_ts": sig_date.isoformat() + "T12:00:00",
            "entry_price": 100.0,
            "exit_ts": exit_ts, "exit_price": 125.0, "shares": 1,
        }])

        result = recommendation_tracking_report(
            log_path=tmp_path / "log.jsonl",
            transactions_store=store,
            horizon_days=30,
            historical_store=hs,
        )

        assert not math.isnan(result["delta"])
        expected_delta = result["operator_return_30d"] - result["model_return_30d"]
        assert abs(result["delta"] - expected_delta) < 1e-10

    def test_delta_nan_when_only_model_return_available(self, tmp_path: Path) -> None:
        # BUY signal logged as "passed" → no actual return → delta NaN
        import json
        from dataclasses import asdict

        entry = _make_entry(signal_action="BUY", action_taken="passed", days_ago=40)
        with open(tmp_path / "log.jsonl", "w") as fh:
            fh.write(json.dumps(asdict(entry)) + "\n")

        result = recommendation_tracking_report(
            log_path=tmp_path / "log.jsonl",
            historical_store=_FakeHistoricalStore(
                {"AAPL": _make_bars("AAPL", price_at_start=100, price_at_end=110)}
            ),
        )
        assert not math.isnan(result["model_return_30d"])
        assert math.isnan(result["operator_return_30d"])
        assert math.isnan(result["delta"])


# ---------------------------------------------------------------------------
# TestDeadLetterResilience
# ---------------------------------------------------------------------------

class TestDeadLetterResilience:
    def _write_one(self, path: Path, entry: DecisionEntry) -> None:
        import json
        from dataclasses import asdict

        with open(path, "w") as fh:
            fh.write(json.dumps(asdict(entry)) + "\n")

    def test_historical_store_failure_degrades_gracefully(
        self, tmp_path: Path
    ) -> None:
        entry = _make_entry(signal_action="BUY", days_ago=40)
        self._write_one(tmp_path / "log.jsonl", entry)

        class BrokenStore:
            def get_bars(self, *args, **kwargs):
                raise RuntimeError("DB is broken")

        # Should not raise; model prices will be NaN
        result = recommendation_tracking_report(
            log_path=tmp_path / "log.jsonl",
            historical_store=BrokenStore(),
        )
        assert result["n_signals"] == 1
        assert math.isnan(result["model_return_30d"])

    def test_transactions_store_failure_degrades_gracefully(
        self, tmp_path: Path
    ) -> None:
        entry = _make_entry(
            signal_action="BUY", action_taken="acted", trade_id=1, days_ago=40
        )
        self._write_one(tmp_path / "log.jsonl", entry)

        class BrokenTxStore:
            def get_trade_history(self, *args, **kwargs):
                raise RuntimeError("DB is broken")

        result = recommendation_tracking_report(
            log_path=tmp_path / "log.jsonl",
            transactions_store=BrokenTxStore(),
            historical_store=_FakeHistoricalStore({}),
        )
        assert result["n_signals"] == 1
        assert result["n_with_exit"] == 0
        assert math.isnan(result["operator_return_30d"])

    def test_no_crash_when_all_entries_have_parse_errors(
        self, tmp_path: Path
    ) -> None:
        p = tmp_path / "log.jsonl"
        p.write_text('{"symbol": null, "action_taken": null}\n')
        # Should not raise (the bad entry is skipped)
        result = recommendation_tracking_report(log_path=p)
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# TestModuleSurface
# ---------------------------------------------------------------------------

class TestModuleSurface:
    def test_function_importable(self) -> None:
        from evaluation_engine import recommendation_tracking_report  # noqa: F401

    def test_empty_sentinel_structure(self) -> None:
        from evaluation_engine import _TRACKING_EMPTY, _DEFAULT_DECISION_LOG_PATH
        for key in ("rows", "model_return_30d", "operator_return_30d",
                    "delta", "n_signals", "n_acted", "n_completed",
                    "n_with_exit", "horizon_days"):
            assert key in _TRACKING_EMPTY, f"Missing key: {key}"

    def test_default_log_path_type(self) -> None:
        from evaluation_engine import _DEFAULT_DECISION_LOG_PATH
        assert isinstance(_DEFAULT_DECISION_LOG_PATH, Path)

    def test_price_at_or_before_helper_nan_on_empty(self) -> None:
        from evaluation_engine import _price_at_or_before
        assert math.isnan(_price_at_or_before(pd.DataFrame(), datetime.now()))

    def test_price_at_or_before_returns_correct_close(self) -> None:
        from evaluation_engine import _price_at_or_before
        # Business days Mon 2024-01-01 → Fri 2024-01-05
        dates = pd.date_range("2024-01-01", periods=5, freq="B")
        closes = pd.Series([10.0, 11.0, 12.0, 13.0, 14.0], index=dates)
        bars = pd.DataFrame({"Close": closes}, index=dates)
        # Saturday 2024-01-06 falls after Friday's bar; should return 14.0
        target = datetime(2024, 1, 6, 15, 0, 0)
        result = _price_at_or_before(bars, target)
        assert result == 14.0
