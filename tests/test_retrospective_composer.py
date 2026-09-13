"""Tests for ``pilots/retrospective_composer.py`` — the per-trade retrospective
composer backing the Retrospective Learning Loop (Trade Journal).

All heavy engine/DB dependencies are monkeypatched at their SOURCE module
(``data.historical_store.HistoricalStore``, ``evaluation_engine
.EvaluationEngine``, ``data.trade_decision_snapshot_store
.resolve_database_url``) rather than on ``pilots.retrospective_composer``
itself, since that module does lazy (inside-function) imports — mirroring
``tests/test_pilots_calibration.py``'s convention exactly.

conftest.py's ``_isolate_trade_decision_snapshot_db_in_tests`` autouse
fixture already points the default ``TradeDecisionSnapshotStore`` resolver at
``sqlite:///:memory:`` for every test. Tests that need to actually see a
snapshot row round-trip (decision-state tests below) re-patch
``data.trade_decision_snapshot_store.resolve_database_url`` to a real
``tmp_path``-backed file URL instead, and seed it via a write-mode
``TradeDecisionSnapshotStore`` constructed against that same URL BEFORE
calling the composer (which always constructs its own readonly instance with
no explicit URL, so both instances must resolve to the identical file).
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest import mock

import pandas as pd
import pytest

from pilots import retrospective_composer as rc
from data.trade_decision_snapshot_store import TradeDecisionSnapshotStore


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


def _bars(start="2026-01-01", periods=60, high=110.0, low=95.0, close=105.0, open_=100.0):
    idx = pd.date_range(start, periods=periods, freq="D")
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": 1_000},
        index=idx,
    )


def _closed_trade(**overrides):
    trade = {
        "trade_id": 1,
        "strategy_id": "earnings-crush",
        "pilot_id": "earnings-crush",
        "experiment_arm": None,
        "symbol": "AAPL",
        "side": "BUY",
        "qty": 10.0,
        "entry_ts": "2026-01-05T14:30:00+00:00",
        "entry_price": 100.0,
        "exit_ts": "2026-01-10T14:30:00+00:00",
        "exit_price": 108.0,
        "commission": 1.0,
        "realized_pnl": 79.0,
        "realized_pnl_pct": 0.079,
        "holding_period_days": 5.0,
        "close_reason": "manual_close",
        "leg_group_id": None,
    }
    trade.update(overrides)
    return trade


class _FakeHStore:
    """Stand-in for ``data.historical_store.HistoricalStore`` -- constructed
    with zero args (matching the composer's own ``HistoricalStore()`` call),
    ``get_bars`` is swappable per test."""

    def __init__(self, bars=None, raise_on_fetch=False, call_log=None):
        self._bars = bars if bars is not None else pd.DataFrame()
        self._raise = raise_on_fetch
        self._call_log = call_log if call_log is not None else []

    def get_bars(self, symbol, lookback_days=756):
        self._call_log.append(symbol)
        if self._raise:
            raise RuntimeError("simulated fetch failure")
        return self._bars


def _snapshot_db_url(tmp_path, name="snap.db"):
    return f"sqlite:///{tmp_path / name}"


def _seed_snapshot(db_url, **kwargs):
    store = TradeDecisionSnapshotStore(db_url=db_url)
    store.record_snapshot(**kwargs)


def _point_snapshot_resolver_at(monkeypatch, db_url):
    """Repoint the composer's (readonly, no-explicit-url) TradeDecisionSnapshotStore
    construction at a real tmp_path-backed file DB instead of the autouse
    :memory: fixture, so a row seeded via a write-mode store constructed
    against the SAME db_url is actually visible."""
    import data.trade_decision_snapshot_store as tdss

    monkeypatch.setattr(tdss, "resolve_database_url", lambda: db_url)


# ---------------------------------------------------------------------------
# 1. Byte-for-byte parity against a direct engine call (the most important test)
# ---------------------------------------------------------------------------


class TestEvaluationParity:
    def test_mfe_mae_edge_ratio_exactly_match_direct_engine_call(self):
        trade = _closed_trade()
        bars = _bars()

        with mock.patch("data.historical_store.HistoricalStore", return_value=_FakeHStore(bars=bars)):
            out = rc.compose_trade_retrospective(trade)

        assert out["evaluation"]["available"] is True

        # Independently call the REAL engine with the SAME inputs the
        # composer used internally, and require EXACT (not approximate)
        # equality -- this is the audit protocol's own byte-for-byte check.
        from evaluation_engine import EvaluationEngine

        entry_dt = datetime.fromisoformat(trade["entry_ts"])
        exit_dt = datetime.fromisoformat(trade["exit_ts"])
        direct = EvaluationEngine().calculate_edge_ratio(bars, trade["entry_price"], entry_dt, exit_dt)

        assert out["evaluation"]["mfe"] == direct["MFE"]
        assert out["evaluation"]["mae"] == direct["MAE"]
        assert out["evaluation"]["edge_ratio"] == direct["Edge Ratio"]
        assert out["evaluation"]["reason"] is None


# ---------------------------------------------------------------------------
# 2. Evaluation unavailable -- never a fabricated number
# ---------------------------------------------------------------------------


class TestEvaluationUnavailable:
    def test_empty_bars_reports_unavailable_with_reason(self):
        trade = _closed_trade()
        with mock.patch("data.historical_store.HistoricalStore", return_value=_FakeHStore(bars=pd.DataFrame())):
            out = rc.compose_trade_retrospective(trade)

        assert out["evaluation"]["available"] is False
        assert out["evaluation"]["mfe"] is None
        assert out["evaluation"]["mae"] is None
        assert out["evaluation"]["edge_ratio"] is None
        assert isinstance(out["evaluation"]["reason"], str) and out["evaluation"]["reason"]

    def test_bars_fetch_raises_reports_unavailable_with_reason(self):
        trade = _closed_trade()
        with mock.patch(
            "data.historical_store.HistoricalStore", return_value=_FakeHStore(raise_on_fetch=True)
        ):
            out = rc.compose_trade_retrospective(trade)

        assert out["evaluation"]["available"] is False
        assert out["evaluation"]["mfe"] is None
        assert isinstance(out["evaluation"]["reason"], str) and out["evaluation"]["reason"]

    def test_historical_store_construction_failure_degrades_honestly(self):
        trade = _closed_trade()
        with mock.patch(
            "data.historical_store.HistoricalStore", side_effect=RuntimeError("db unavailable")
        ):
            out = rc.compose_trade_retrospective(trade)

        assert out["evaluation"]["available"] is False
        assert out["evaluation"]["reason"]
        # Base "what happened" fields are unaffected by the evaluation-side failure.
        assert out["symbol"] == "AAPL"
        assert out["realized_pnl"] == pytest.approx(79.0)


# ---------------------------------------------------------------------------
# 3. entry_ts is None -> decision unknown AND evaluation unavailable
# ---------------------------------------------------------------------------


class TestEntryTsNone:
    def test_none_entry_ts_forces_unknown_decision_and_unavailable_evaluation(self):
        trade = _closed_trade(entry_ts=None)
        with mock.patch("data.historical_store.HistoricalStore", return_value=_FakeHStore(bars=_bars())):
            out = rc.compose_trade_retrospective(trade)

        assert out["decision"]["state"] == "unknown"
        assert out["decision"]["provenance"] is None
        assert out["evaluation"]["available"] is False
        assert out["evaluation"]["reason"] == "entry_ts unknown for this trade"
        # entry_ts is passed through verbatim (None stays None).
        assert out["entry_ts"] is None


# ---------------------------------------------------------------------------
# 4/5/6. decision.state derivation -- manual / signal_driven / unknown
# ---------------------------------------------------------------------------


class TestDecisionState:
    def test_state_manual_when_snapshot_provenance_is_manual(self, tmp_path, monkeypatch):
        db_url = _snapshot_db_url(tmp_path)
        _point_snapshot_resolver_at(monkeypatch, db_url)

        entry_ts = datetime(2026, 1, 5, 14, 30, 0, tzinfo=timezone.utc)
        trade = _closed_trade(strategy_id="untagged", entry_ts=entry_ts.isoformat())
        _seed_snapshot(
            db_url,
            symbol="AAPL",
            strategy_id="untagged",
            entry_ts=entry_ts,
            provenance="manual",
        )

        with mock.patch("data.historical_store.HistoricalStore", return_value=_FakeHStore(bars=_bars())):
            out = rc.compose_trade_retrospective(trade)

        assert out["decision"]["state"] == "manual"
        assert out["decision"]["provenance"] == "manual"

    def test_state_signal_driven_with_conviction_and_factors(self, tmp_path, monkeypatch):
        db_url = _snapshot_db_url(tmp_path)
        _point_snapshot_resolver_at(monkeypatch, db_url)

        entry_ts = datetime(2026, 1, 5, 14, 30, 0, tzinfo=timezone.utc)
        trade = _closed_trade(strategy_id="earnings-crush", entry_ts=entry_ts.isoformat())
        _seed_snapshot(
            db_url,
            symbol="AAPL",
            strategy_id="earnings-crush",
            entry_ts=entry_ts,
            provenance="automated:options_auto_scan",
            conviction=0.7,
            factors={"ivr": 60.0},
        )

        with mock.patch("data.historical_store.HistoricalStore", return_value=_FakeHStore(bars=_bars())):
            out = rc.compose_trade_retrospective(trade)

        assert out["decision"]["state"] == "signal_driven"
        assert out["decision"]["provenance"] == "automated:options_auto_scan"
        assert out["decision"]["conviction"] == pytest.approx(0.7)
        assert out["decision"]["factors"] == {"ivr": 60.0}

    def test_state_unknown_when_no_snapshot_row_exists(self, tmp_path, monkeypatch):
        db_url = _snapshot_db_url(tmp_path)
        _point_snapshot_resolver_at(monkeypatch, db_url)
        # Seed the table (write-mode construction) but record NOTHING for
        # this trade's key -- a real, empty-of-this-key table, not a missing one.
        TradeDecisionSnapshotStore(db_url=db_url)

        entry_ts = datetime(2026, 1, 5, 14, 30, 0, tzinfo=timezone.utc)
        trade = _closed_trade(strategy_id="earnings-crush", entry_ts=entry_ts.isoformat())

        with mock.patch("data.historical_store.HistoricalStore", return_value=_FakeHStore(bars=_bars())):
            out = rc.compose_trade_retrospective(trade)

        assert out["decision"]["state"] == "unknown"
        assert out["decision"]["provenance"] is None
        assert out["decision"]["conviction"] is None

    def test_unrecognized_provenance_fails_closed_to_unknown_but_keeps_raw_string(self, tmp_path, monkeypatch):
        """A real snapshot row with a provenance value that is neither
        'manual' nor 'automated:*' must still fail closed to state='unknown'
        (never guess) -- but the RAW provenance string is still surfaced so
        an operator/caller can see what it actually was."""
        db_url = _snapshot_db_url(tmp_path)
        _point_snapshot_resolver_at(monkeypatch, db_url)

        entry_ts = datetime(2026, 1, 5, 14, 30, 0, tzinfo=timezone.utc)
        trade = _closed_trade(strategy_id="weird-strategy", entry_ts=entry_ts.isoformat())
        _seed_snapshot(
            db_url,
            symbol="AAPL",
            strategy_id="weird-strategy",
            entry_ts=entry_ts,
            provenance="something_else_entirely",
        )

        with mock.patch("data.historical_store.HistoricalStore", return_value=_FakeHStore(bars=_bars())):
            out = rc.compose_trade_retrospective(trade)

        assert out["decision"]["state"] == "unknown"
        assert out["decision"]["provenance"] == "something_else_entirely"


# ---------------------------------------------------------------------------
# 7. THE critical anti-shortcut test
# ---------------------------------------------------------------------------


class TestAntiShortcut:
    def test_manual_trade_strategy_id_without_snapshot_is_still_unknown(self, tmp_path, monkeypatch):
        """A trade with strategy_id == 'Manual Trade' (this codebase's
        existing, pre-existing convention string for manually-placed orders)
        but with NO captured decision snapshot must report state='unknown',
        NOT 'manual'.

        This is the exact fabrication shortcut CLAUDE.md's Retrospective
        Learning Loop framing forbids: inferring decision provenance from
        strategy_id instead of from an actually-captured record, even when
        that inference would usually be numerically correct. If this test
        ever starts asserting state == "manual" here, the composer has
        regressed into presenting an inference as a record.
        """
        db_url = _snapshot_db_url(tmp_path)
        _point_snapshot_resolver_at(monkeypatch, db_url)
        # Real table exists (write-mode construction), but genuinely has no
        # row for this trade's (symbol, strategy_id, entry_ts) key.
        TradeDecisionSnapshotStore(db_url=db_url)

        entry_ts = datetime(2026, 1, 5, 14, 30, 0, tzinfo=timezone.utc)
        trade = _closed_trade(strategy_id="Manual Trade", entry_ts=entry_ts.isoformat())

        with mock.patch("data.historical_store.HistoricalStore", return_value=_FakeHStore(bars=_bars())):
            out = rc.compose_trade_retrospective(trade)

        assert out["decision"]["state"] == "unknown"
        assert out["decision"]["state"] != "manual"
        assert out["decision"]["provenance"] is None


# ---------------------------------------------------------------------------
# 8. Batch efficiency + parity with N single calls
# ---------------------------------------------------------------------------


class TestBatchComposition:
    def test_batch_matches_n_single_calls_and_fetches_bars_once_per_symbol(self, tmp_path, monkeypatch):
        db_url = _snapshot_db_url(tmp_path)
        _point_snapshot_resolver_at(monkeypatch, db_url)

        # All three entry times are deliberately before the shared default
        # exit_ts ("2026-01-10T14:30:00+00:00", from _closed_trade()) so every
        # trade's hold period is well-formed (entry < exit) for the evaluation
        # section, even though this test only asserts on decision.state.
        entry_ts_1 = datetime(2026, 1, 5, 14, 30, 0, tzinfo=timezone.utc)
        entry_ts_2 = datetime(2026, 1, 6, 9, 0, 0, tzinfo=timezone.utc)
        entry_ts_3 = datetime(2026, 1, 7, 9, 0, 0, tzinfo=timezone.utc)

        trades = [
            _closed_trade(trade_id=1, symbol="AAPL", strategy_id="earnings-crush", entry_ts=entry_ts_1.isoformat()),
            # Same symbol as trade 1, different entry -- bars must be
            # fetched only ONCE for AAPL across the whole batch.
            _closed_trade(trade_id=2, symbol="AAPL", strategy_id="earnings-crush", entry_ts=entry_ts_2.isoformat()),
            _closed_trade(trade_id=3, symbol="MSFT", strategy_id="untagged", entry_ts=entry_ts_3.isoformat()),
        ]
        _seed_snapshot(
            db_url, symbol="AAPL", strategy_id="earnings-crush", entry_ts=entry_ts_1,
            provenance="automated:options_auto_scan", conviction=0.6,
        )

        call_log: list = []
        with mock.patch(
            "data.historical_store.HistoricalStore",
            return_value=_FakeHStore(bars=_bars(), call_log=call_log),
        ):
            batch_out = rc.compose_trade_retrospectives(trades)

        assert len(batch_out) == 3
        # AAPL fetched exactly once, MSFT fetched exactly once -- two total
        # calls for three trades (not three).
        assert sorted(call_log) == ["AAPL", "MSFT"]
        assert len(call_log) == 2

        # Batch result must exactly equal calling the single-trade function
        # N times (same inputs, fresh mock so the fetch actually happens
        # again per single call -- this only checks OUTPUT parity, not call
        # count, for the single-call path).
        call_log_single: list = []
        with mock.patch(
            "data.historical_store.HistoricalStore",
            return_value=_FakeHStore(bars=_bars(), call_log=call_log_single),
        ):
            single_out = [rc.compose_trade_retrospective(t) for t in trades]

        assert batch_out == single_out

        # Sanity on the actual content for trade 1 (has a real snapshot).
        assert batch_out[0]["decision"]["state"] == "signal_driven"
        assert batch_out[0]["decision"]["conviction"] == pytest.approx(0.6)
        # trade 2 shares AAPL/earnings-crush but a DIFFERENT entry_ts -> no snapshot.
        assert batch_out[1]["decision"]["state"] == "unknown"
        # trade 3 has no snapshot at all.
        assert batch_out[2]["decision"]["state"] == "unknown"

    def test_empty_batch_returns_empty_list(self):
        assert rc.compose_trade_retrospectives([]) == []


# ---------------------------------------------------------------------------
# 9. Never raises on malformed/incomplete trade input
# ---------------------------------------------------------------------------


class TestNeverRaises:
    @pytest.mark.parametrize(
        "malformed_trade",
        [
            {},
            {"symbol": "AAPL"},
            {"symbol": 123, "qty": "not-a-number", "entry_price": object()},
            {"entry_ts": "not-a-real-timestamp", "exit_ts": "also-not-real"},
            None,
            "just a string, not a dict",
            42,
        ],
    )
    def test_malformed_trade_never_raises_and_degrades_honestly(self, malformed_trade):
        # No HistoricalStore mock at all -- if the composer ever attempted a
        # real fetch here it would be a bug in itself (these trades have no
        # usable entry_ts), so leaving it unmocked also proves that path is
        # never reached for a malformed/incomplete trade.
        out = rc.compose_trade_retrospective(malformed_trade)

        assert isinstance(out, dict)
        assert out["decision"]["state"] == "unknown"
        assert out["evaluation"]["available"] is False
        assert isinstance(out["evaluation"]["reason"], str) and out["evaluation"]["reason"]

    def test_malformed_trade_in_a_batch_never_aborts_the_whole_batch(self):
        trades = [_closed_trade(trade_id=1), {}, _closed_trade(trade_id=2, symbol="MSFT")]
        with mock.patch("data.historical_store.HistoricalStore", return_value=_FakeHStore(bars=_bars())):
            out = rc.compose_trade_retrospectives(trades)

        assert len(out) == 3
        assert out[0]["evaluation"]["available"] is True
        assert out[1]["decision"]["state"] == "unknown"
        assert out[2]["evaluation"]["available"] is True
