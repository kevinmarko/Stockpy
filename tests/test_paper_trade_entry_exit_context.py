"""tests/test_paper_trade_entry_exit_context.py

Entry/exit decision context for pipeline paper trades
(.claude/paper_trade_entry_exit_context_implementation_plan.md):

* execution/trade_context.py builders (no fabrication, never raise, budgets)
* OrderIntent.decision_context is inert for OrderManager idempotency
* FMPPaperBroker forwards context to apply_fill; no context -> today's call
* PaperAccountStore writes the entry snapshot / real close_reason /
  exit_context_json, and migrates the new column
* RetrospectiveComposer + narrative surface it; nothing changes for
  trades without it

Fully offline: FMP quotes are patched, every store is a tmp_path SQLite file.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
import math
import sqlite3
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

import main_orchestrator
from data.paper_account_store import PaperAccountStore, PaperClosedTrade, PaperEntrySnapshot
from db_config import session_scope
from execution import trade_context as tc
from execution.broker_base import OrderIntent, OrderSide, OrderStatus, OrderType
from execution.fmp_paper_broker import FMPPaperBroker
from execution.kill_switch import GlobalKillSwitch
from execution.order_manager import OrderManager, make_client_order_id

REPO = Path(__file__).resolve().parent.parent
CLOSE_REASON_PATTERN = r"[a-z][a-z0-9_]{0,19}"


@pytest.fixture(autouse=True)
def _no_quote_age_check(monkeypatch):
    monkeypatch.setattr("settings.settings.PAPER_FILL_MAX_QUOTE_AGE_SECONDS", 0.0, raising=False)


def _entry_row(**overrides):
    row = {
        "Symbol": "AAPL", "Action Signal": "STRONG BUY", "Score": 82,
        "Kelly Target": 0.05, "Kelly_Target_Pre_Regime": 0.09,
        "Kelly_Target_Post_Regime": 0.08, "Regime_Multiplier": 0.9,
        "Meta_Label_Composite": 1.0, "Sizing_Binding_Constraint": None,
        "HMM_Regime_State": np.int64(2), "HMM_Risk_On_Probability": np.float64(0.66),
        "Macro Status": "RISK_ON", "DualMomentum_Signal": "SPY",
        "Forecast_30": 110.0, "Forecast_30_Pct": 0.1, "Forecast_30_Is_Fallback": False,
        "GARCH_Vol": 0.2, "Score_Components": {"momentum": 30.0, "value": float("nan")},
        "Actionable Advice Signal": "STRONG BUY: High-conviction entry.",
        "Advisory_Action": "BUY", "Advisory_Conviction": 0.71,
        "Advisory_Rationale": "Trend and value agree.", "Price": 100.0,
    }
    row.update(overrides)
    return pd.Series(row)


def _entry_ctx(row=None, **kw):
    params = dict(sizing_source="kelly", effective_weight=0.05, equity=100_000.0, price=100.0)
    params.update(kw)
    return tc.build_entry_context(_entry_row() if row is None else row, **params)


# =============================================================================
# trade_context builders
# =============================================================================

class TestEntryContext:
    def test_fields_come_from_the_row(self):
        ctx = _entry_ctx()
        assert ctx["kind"] == "entry"
        assert ctx["provenance"] == "signal_driven"
        assert ctx["provenance_tag"] == "main_pipeline:kelly"
        assert ctx["signal_score"] == 82.0
        assert ctx["macro_regime"] == "RISK_ON"
        assert ctx["raw_forecast"] == 110.0
        assert ctx["forecast_model"] is None
        assert ctx["decision_rationale"].startswith("STRONG BUY")
        ind = json.loads(ctx["key_indicators_json"])
        assert ind["context_schema_version"] == tc.CONTEXT_SCHEMA_VERSION
        assert ind["hmm_regime_state"] == 2
        assert ind["hmm_risk_on_probability"] == pytest.approx(0.66)
        assert ind["score_components"] == {"momentum": 30.0, "value": None}
        assert ind["forecast_30_is_fallback"] is False
        assert ind["sizing_source"] == "kelly"

    def test_conviction_column_is_never_the_advisory_engine(self):
        ctx = _entry_ctx()
        assert ctx["conviction"] is None
        ind = json.loads(ctx["key_indicators_json"])
        assert ind["advisory_conviction_same_cycle"] == pytest.approx(0.71)

    def test_advisory_blank_fill_zero_is_not_a_measurement(self):
        ctx = _entry_ctx(_entry_row(Advisory_Action="", Advisory_Conviction=0.0))
        assert json.loads(ctx["key_indicators_json"])["advisory_conviction_same_cycle"] is None

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), None, "abc", True])
    def test_missing_or_bad_numbers_become_none(self, bad):
        ctx = _entry_ctx(_entry_row(Score=bad, Forecast_30=bad, GARCH_Vol=bad))
        assert ctx["signal_score"] is None
        assert ctx["raw_forecast"] is None
        ind = json.loads(ctx["key_indicators_json"])
        assert ind["garch_vol"] is None
        assert ind["context_status"] == "partial"

    @pytest.mark.parametrize("placeholder", [0.0, -5.0])
    def test_non_positive_forecast_price_is_a_placeholder(self, placeholder):
        assert _entry_ctx(_entry_row(Forecast_30=placeholder))["raw_forecast"] is None

    def test_fallback_flag_only_when_a_real_bool(self):
        ind = json.loads(_entry_ctx(_entry_row(Forecast_30_Is_Fallback=float("nan")))["key_indicators_json"])
        assert ind["forecast_30_is_fallback"] is None

    def test_macro_falls_back_to_macro_dto(self):
        class _Macro:
            market_regime = "EXPANSION"
        ctx = _entry_ctx(_entry_row(**{"Macro Status": None}), macro_dto=_Macro())
        assert ctx["macro_regime"] == "EXPANSION"

    def test_probe_source_and_effective_weight(self):
        ctx = _entry_ctx(_entry_row(**{"Kelly Target": 0.0}), sizing_source="probe", effective_weight=0.01)
        assert ctx["provenance_tag"] == "main_pipeline:probe"
        ind = json.loads(ctx["key_indicators_json"])
        assert ind["kelly_target_row"] == 0.0
        assert ind["effective_weight"] == pytest.approx(0.01)

    def test_oversized_payload_is_truncated_and_stays_valid(self):
        huge = {f"k{i}": "x" * 200 for i in range(200)}
        ctx = _entry_ctx(_entry_row(Score_Components=huge))
        text = ctx["key_indicators_json"]
        assert len(text) <= 8000
        ind = json.loads(text)
        assert ind["context_status"] == "truncated"
        assert ind["score_components"] is None
        assert ind["score"] == 82.0

    @pytest.mark.parametrize("row", [None, {}, object(), pd.Series(dtype=object)])
    def test_never_raises_on_garbage_rows(self, row):
        ctx = tc.build_entry_context(row, sizing_source="kelly", effective_weight=None, equity=None, price=None)
        assert ctx["kind"] == "entry"
        json.loads(ctx["key_indicators_json"])

    def test_json_is_strict(self):
        ctx = _entry_ctx(_entry_row(Score_Components={"a": float("inf"), "b": np.nan}))
        json.loads(ctx["key_indicators_json"], parse_constant=lambda c: pytest.fail(f"non-strict JSON {c}"))


class TestExitContext:
    @pytest.mark.parametrize("signal,code", [
        ("SELL", "signal_sell"), ("TRIM", "signal_trim"),
        ("RISK REDUCE", "signal_risk_reduce"), ("AVOID", "signal_avoid"),
        ("risk reduce", "signal_risk_reduce"),
    ])
    def test_close_reason_codes(self, signal, code):
        import re
        assert tc.exit_close_reason(signal) == code
        assert re.fullmatch(CLOSE_REASON_PATTERN, code)

    @pytest.mark.parametrize("signal", ["HOLD", "BUY", "", None, float("nan")])
    def test_non_exit_signals_have_no_code(self, signal):
        assert tc.exit_close_reason(signal) is None

    def test_mapping_matches_orchestrator_exit_signals(self):
        assert set(tc.EXIT_SIGNAL_CLOSE_REASONS) == set(main_orchestrator.EXIT_SIGNALS)

    def test_exit_payload(self):
        ctx = tc.build_exit_context(
            _entry_row(**{"Action Signal": "RISK REDUCE", "Score": 20, "DualMomentum_Signal": "BIL"}),
            signal="RISK REDUCE", held_qty=1.3,
        )
        assert ctx["kind"] == "exit"
        assert ctx["close_reason"] == "signal_risk_reduce"
        payload = json.loads(ctx["exit_context_json"])
        assert payload["exit_signal"] == "RISK REDUCE"
        assert payload["score"] == 20.0
        assert payload["dual_momentum_signal"] == "BIL"
        assert payload["held_qty"] == pytest.approx(1.3)
        assert len(ctx["exit_context_json"]) <= 4000

    def test_exit_never_raises(self):
        ctx = tc.build_exit_context(object(), signal="SELL", held_qty="x")
        assert ctx["close_reason"] == "signal_sell"
        assert json.loads(ctx["exit_context_json"])["held_qty"] is None


class TestApplyFillKwargs:
    def test_none_and_garbage_give_no_kwargs(self):
        for ctx in (None, "x", 5, [], {"kind": "other"}, {}):
            assert tc.apply_fill_kwargs(ctx) == {}

    def test_entry_maps_to_snapshot_kwargs_only(self):
        kwargs = tc.apply_fill_kwargs(_entry_ctx())
        assert set(kwargs) == {
            "provenance", "provenance_tag", "conviction", "macro_regime", "signal_score",
            "raw_forecast", "forecast_model", "key_indicators_json", "decision_rationale",
        }
        assert set(kwargs) <= set(inspect.signature(PaperAccountStore.apply_fill).parameters)

    def test_exit_maps_to_close_kwargs(self):
        kwargs = tc.apply_fill_kwargs(tc.build_exit_context(_entry_row(), signal="SELL", held_qty=1))
        assert set(kwargs) == {"close_reason", "exit_context_json"}
        assert kwargs["close_reason"] == "signal_sell"


# =============================================================================
# OrderIntent / OrderManager idempotency
# =============================================================================

def test_order_intent_field_is_last_and_optional():
    names = [f.name for f in fields(OrderIntent)]
    assert names[-1] == "decision_context"
    assert len(names) == len(set(names))
    intent = OrderIntent(strategy_id="s", symbol="AAPL", side=OrderSide.BUY, qty=1.0)
    assert intent.decision_context is None


def test_idempotency_code_never_reads_decision_context():
    src = (REPO / "execution" / "order_manager.py").read_text()
    assert "decision_context" not in src
    params = inspect.signature(make_client_order_id).parameters
    assert "decision_context" not in params
    for name in ("risk_gate.py", "kill_switch.py", "priority_queue.py"):
        assert "decision_context" not in (REPO / "execution" / name).read_text()


class _RecordingBroker:
    def __init__(self):
        self.submitted = []

    async def submit_order(self, intent):
        from execution.broker_base import OrderResult
        self.submitted.append(intent)
        return OrderResult(client_order_id=intent.client_order_id, broker_order_id="b", status=OrderStatus.FILLED)


def test_order_manager_same_coid_with_or_without_context_and_dedupes(tmp_path):
    ts = datetime(2026, 10, 5, 15, 0, tzinfo=timezone.utc)
    broker = _RecordingBroker()
    om = OrderManager(broker, dry_run=False, kill_switch=GlobalKillSwitch(sentinel_file=tmp_path / "KS"))

    plain = OrderIntent(strategy_id="main_pipeline", symbol="AAPL", side=OrderSide.BUY, qty=12.5)
    with_ctx = OrderIntent(strategy_id="main_pipeline", symbol="AAPL", side=OrderSide.BUY, qty=12.5,
                           decision_context=_entry_ctx())

    asyncio.run(om.submit_order_with_idempotency(plain, timestamp=ts))
    res2 = asyncio.run(om.submit_order_with_idempotency(with_ctx, timestamp=ts))

    assert plain.client_order_id == with_ctx.client_order_id
    assert plain.client_order_id == make_client_order_id("main_pipeline", "AAPL", "buy", 12.5, timestamp=ts)
    assert len(broker.submitted) == 1  # the context-only difference is deduped
    assert res2.broker_order_id is None


# =============================================================================
# FMPPaperBroker -> apply_fill
# =============================================================================

_TODAY_KWARGS = {
    "client_order_id", "symbol", "side", "qty", "fill_price", "commission_and_fees",
    "target_qty", "status", "strategy_id",
}


def _intent(**overrides):
    base = dict(strategy_id="main_pipeline", symbol="AAPL", side=OrderSide.BUY, qty=10.0,
                order_type=OrderType.MARKET, client_order_id="coid_1")
    base.update(overrides)
    return OrderIntent(**base)


def _spy_apply_fill(broker):
    calls = []
    real = broker.store.apply_fill

    def spy(**kwargs):
        calls.append(kwargs)
        return real(**kwargs)

    broker.store.apply_fill = spy
    return calls


def _quote(price=150.0):
    return patch("data.fmp_client.quote", return_value=[{"symbol": "AAPL", "price": price, "marketCap": 2e12}])


def test_broker_without_context_calls_apply_fill_exactly_as_today():
    broker = FMPPaperBroker(db_url="sqlite:///:memory:")
    calls = _spy_apply_fill(broker)
    with _quote():
        result = asyncio.run(broker.submit_order(_intent()))
    assert result.status == OrderStatus.FILLED
    assert len(calls) == 1
    assert set(calls[0]) == _TODAY_KWARGS
    assert calls[0]["strategy_id"] == "main_pipeline"
    assert calls[0]["status"] == OrderStatus.FILLED.value


def test_broker_forwards_entry_and_exit_context(tmp_path):
    broker = FMPPaperBroker(db_url=f"sqlite:///{tmp_path / 'p.db'}")
    calls = _spy_apply_fill(broker)
    with _quote(150.0):
        asyncio.run(broker.submit_order(_intent(decision_context=_entry_ctx())))
    with _quote(140.0):
        asyncio.run(broker.submit_order(_intent(
            side=OrderSide.SELL, client_order_id="coid_2",
            decision_context=tc.build_exit_context(_entry_row(), signal="RISK REDUCE", held_qty=10.0),
        )))
    assert calls[0]["provenance"] == "signal_driven"
    assert "close_reason" not in calls[0]
    assert calls[1]["close_reason"] == "signal_risk_reduce"
    assert set(calls[1]) == _TODAY_KWARGS | {"close_reason", "exit_context_json"}


@pytest.mark.parametrize("ctx", ["garbage", {"kind": "unknown"}, 42])
def test_broker_malformed_context_still_fills_with_todays_kwargs(ctx):
    broker = FMPPaperBroker(db_url="sqlite:///:memory:")
    calls = _spy_apply_fill(broker)
    with _quote():
        result = asyncio.run(broker.submit_order(_intent(decision_context=ctx)))
    assert result.status == OrderStatus.FILLED
    assert set(calls[0]) == _TODAY_KWARGS


def test_broker_context_mapping_crash_still_fills(monkeypatch):
    import execution.fmp_paper_broker as fmp_mod

    def _boom(ctx):
        raise RuntimeError("boom")

    monkeypatch.setattr(fmp_mod, "apply_fill_kwargs", _boom)
    broker = FMPPaperBroker(db_url="sqlite:///:memory:")
    calls = _spy_apply_fill(broker)
    with _quote():
        result = asyncio.run(broker.submit_order(_intent(decision_context=_entry_ctx())))
    assert result.status == OrderStatus.FILLED
    assert set(calls[0]) == _TODAY_KWARGS


# =============================================================================
# PaperAccountStore
# =============================================================================

@pytest.fixture
def store(tmp_path):
    return PaperAccountStore(db_url=f"sqlite:///{tmp_path / 'store.db'}")


def _snapshots(store, strategy_id="main_pipeline"):
    with session_scope(store.Session) as s:
        return [r.to_dict() for r in s.query(PaperEntrySnapshot).filter_by(strategy_id=strategy_id).all()]


def test_store_pipeline_open_without_context_still_writes_no_snapshot(store):
    assert store.apply_fill("c1", "AAPL", "buy", 5.0, 100.0, strategy_id="main_pipeline")
    assert _snapshots(store) == []


def test_store_pipeline_entry_and_exit_round_trip(store):
    kwargs = tc.apply_fill_kwargs(_entry_ctx())
    assert store.apply_fill("c1", "AAPL", "buy", 5.0, 100.0, strategy_id="main_pipeline", **kwargs)
    snaps = _snapshots(store)
    assert len(snaps) == 1
    snap = snaps[0]
    assert snap["provenance"] == "signal_driven"
    assert snap["conviction"] is None
    assert snap["signal_score"] == 82.0
    assert snap["macro_regime"] == "RISK_ON"

    exit_kwargs = tc.apply_fill_kwargs(tc.build_exit_context(_entry_row(), signal="RISK REDUCE", held_qty=5.0))
    assert store.apply_fill("c2", "AAPL", "sell", 5.0, 95.0, strategy_id="main_pipeline", **exit_kwargs)

    trades = store.get_full_closed_trades()
    assert len(trades) == 1
    t = trades[0]
    assert t["close_reason"] == "signal_risk_reduce"
    assert json.loads(t["exit_context_json"])["exit_signal"] == "RISK REDUCE"
    assert t["entry_snapshot_id"] == snap["snapshot_id"]
    linked = store.get_entry_snapshot(snap["snapshot_id"])
    assert linked["trade_id"] == str(t["trade_id"])


def test_store_close_without_reason_is_still_flatten(store):
    store.apply_fill("c1", "AAPL", "buy", 5.0, 100.0, strategy_id="main_pipeline")
    store.apply_fill("c2", "AAPL", "sell", 5.0, 101.0, strategy_id="main_pipeline")
    t = store.get_full_closed_trades()[0]
    assert t["close_reason"] == "flatten"
    assert t["exit_context_json"] is None


@pytest.mark.parametrize("bad", ["DROP TABLE x", "x" * 21, "Signal_Sell", "", "a b", 5, "sell\n"])
def test_store_invalid_close_reason_falls_back_to_flatten(store, bad):
    store.apply_fill("c1", "AAPL", "buy", 5.0, 100.0, strategy_id="main_pipeline")
    store.apply_fill("c2", "AAPL", "sell", 5.0, 101.0, strategy_id="main_pipeline", close_reason=bad)
    assert store.get_full_closed_trades()[0]["close_reason"] == "flatten"


def test_store_short_cover_uses_close_reason(store):
    # buy-to-close-short path (allow_short) honors the same parameter.
    store.apply_fill("c1", "AAPL 2026-12-18 C $100", "sell", 1.0, 5.0, strategy_id="opt", allow_short=True)
    store.apply_fill("c2", "AAPL 2026-12-18 C $100", "buy", 1.0, 4.0, strategy_id="opt",
                     close_reason="signal_trim")
    assert store.get_full_closed_trades()[0]["close_reason"] == "signal_trim"


def _old_schema_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE paper_closed_trades (trade_id INTEGER PRIMARY KEY, strategy_id TEXT, pilot_id TEXT, "
        "experiment_arm TEXT, symbol TEXT NOT NULL, side TEXT NOT NULL, qty REAL NOT NULL, entry_ts DATETIME, "
        "entry_price REAL NOT NULL, exit_ts DATETIME NOT NULL, exit_price REAL NOT NULL, commission REAL, "
        "realized_pnl REAL NOT NULL, realized_pnl_pct REAL, holding_period_days REAL, "
        "close_reason TEXT NOT NULL, leg_group_id TEXT, entry_snapshot_id TEXT, "
        "bridge_status TEXT DEFAULT 'not_attempted', bridged_trade_id INTEGER, bridge_error TEXT, "
        "bridged_at TEXT)"
    )
    conn.execute(
        "INSERT INTO paper_closed_trades (strategy_id, symbol, side, qty, entry_price, exit_ts, exit_price, "
        "commission, realized_pnl, close_reason) VALUES ('main_pipeline','SPY','buy',1,1,'2026-10-01',1,0,0,'flatten')"
    )
    conn.commit()
    conn.close()


def _columns(path: Path) -> set:
    conn = sqlite3.connect(path)
    try:
        return {r[1] for r in conn.execute("PRAGMA table_info(paper_closed_trades)")}
    finally:
        conn.close()


def test_store_migrates_exit_context_column(tmp_path):
    db = tmp_path / "old.db"
    _old_schema_db(db)
    s = PaperAccountStore(db_url=f"sqlite:///{db}")
    assert "exit_context_json" in _columns(db)
    assert s.get_full_closed_trades()[0]["exit_context_json"] is None


def test_database_setup_migrates_exit_context_column(tmp_path):
    from database_setup import migrate_paper_closed_trades_schema

    db = tmp_path / "old2.db"
    _old_schema_db(db)
    conn = sqlite3.connect(db)
    migrate_paper_closed_trades_schema(conn.cursor(), conn)
    conn.close()
    assert "exit_context_json" in _columns(db)


def test_readonly_store_reads_a_db_that_predates_the_column(tmp_path):
    """A readonly store never migrates. Readers (freeze status, Pilots API,
    composer) must still work on a DB no write-mode store has opened since
    the upgrade -- exit_context_json reads as None, no 'no such column'."""
    db = tmp_path / "pre.db"
    _old_schema_db(db)
    ro = PaperAccountStore(db_url=f"sqlite:///{db}", readonly=True)
    assert "exit_context_json" not in _columns(db)

    trades = ro.get_full_closed_trades()
    assert len(trades) == 1
    assert trades[0]["close_reason"] == "flatten"
    assert trades[0]["exit_context_json"] is None
    assert ro.get_bridge_completeness_metrics() is not None
    assert "exit_context_json" not in _columns(db)  # still never written

    rec = _composer(ro, tmp_path).compose_trade_retrospective(trades[0]["trade_id"])
    assert rec is not None
    assert rec["exit_context"] is None
    assert rec["exit_context_status"] == "not_captured"


def test_readonly_store_reads_exit_context_once_migrated(tmp_path):
    db = tmp_path / "post.db"
    w = PaperAccountStore(db_url=f"sqlite:///{db}")
    w.apply_fill("c1", "AAPL", "buy", 5.0, 100.0, strategy_id="main_pipeline")
    w.apply_fill("c2", "AAPL", "sell", 5.0, 95.0, strategy_id="main_pipeline",
                 close_reason="signal_sell", exit_context_json='{"exit_signal": "SELL"}')
    ro = PaperAccountStore(db_url=f"sqlite:///{db}", readonly=True)
    assert ro.get_full_closed_trades()[0]["exit_context_json"] == '{"exit_signal": "SELL"}'


def test_orm_has_exit_context_column():
    assert "exit_context_json" in PaperClosedTrade.__table__.columns


# =============================================================================
# Readers: composer + narrative
# =============================================================================

class _NoBars:
    def get_bars(self, symbol, lookback_days=504, **kwargs):
        return pd.DataFrame()


def _composer(store, tmp_path):
    from evaluation_engine import EvaluationEngine
    from pilots.retrospective_composer import RetrospectiveComposer
    from transactions_store import TransactionsStore

    url = f"sqlite:///{tmp_path / 'tx.db'}"
    return RetrospectiveComposer(
        paper_store=store, transactions_store=TransactionsStore(db_url=url),
        evaluation_engine=EvaluationEngine(), historical_store=_NoBars(), db_url=url,
    )


def test_composer_pipeline_trade_is_captured_with_exit_context(store, tmp_path):
    store.apply_fill("c1", "AAPL", "buy", 5.0, 100.0, strategy_id="main_pipeline",
                     **tc.apply_fill_kwargs(_entry_ctx()))
    store.apply_fill("c2", "AAPL", "sell", 5.0, 95.0, strategy_id="main_pipeline",
                     **tc.apply_fill_kwargs(tc.build_exit_context(_entry_row(), signal="RISK REDUCE", held_qty=5.0)))
    trade_id = store.get_full_closed_trades()[0]["trade_id"]

    rec = _composer(store, tmp_path).compose_trade_retrospective(trade_id)
    assert rec["provenance"] == "signal_driven"
    assert rec["entry_snapshot"]["captured"] is True
    assert rec["entry_snapshot"]["conviction"] is None
    assert rec["close_reason"] == "signal_risk_reduce"
    assert rec["exit_context_status"] == "captured"
    assert rec["exit_context"]["exit_signal"] == "RISK REDUCE"
    assert "Exit trigger: RISK REDUCE signal." in rec["narrative"]


def test_composer_trade_without_context_is_unchanged(store, tmp_path):
    store.apply_fill("c1", "AAPL", "buy", 5.0, 100.0, strategy_id="main_pipeline")
    store.apply_fill("c2", "AAPL", "sell", 5.0, 95.0, strategy_id="main_pipeline")
    trade_id = store.get_full_closed_trades()[0]["trade_id"]

    rec = _composer(store, tmp_path).compose_trade_retrospective(trade_id)
    assert rec["entry_snapshot"]["captured"] is False
    assert rec["exit_context"] is None
    assert rec["exit_context_status"] == "not_captured"
    assert "Exit trigger" not in rec["narrative"]


def test_parse_exit_context_statuses():
    from pilots.retrospective_composer import _parse_exit_context

    assert _parse_exit_context(None) == (None, "not_captured")
    assert _parse_exit_context("  ") == (None, "not_captured")
    assert _parse_exit_context("{bad") == (None, "unparseable")
    assert _parse_exit_context("[1]") == (None, "unparseable")
    assert _parse_exit_context('{"a": 1}') == ({"a": 1}, "captured")


@pytest.mark.parametrize("reason,expected", [
    ("signal_sell", "Exit trigger: SELL signal."),
    ("signal_avoid", "Exit trigger: AVOID signal."),
    ("flatten", None), ("roll", None), (None, None), ("signal_bogus", None),
])
def test_narrative_exit_trigger_only_for_signal_codes(reason, expected):
    from pilots.retrospective_narrative import build_trade_narrative

    base = dict(provenance="signal_driven", side="buy", strategy_id="main_pipeline", entry_price=100.0,
                exit_price=95.0, holding_days=1.0, pnl=-5.0, pnl_pct=-0.05)
    without = build_trade_narrative(**base)
    with_reason = build_trade_narrative(**base, close_reason=reason)
    if expected is None:
        assert with_reason == without
    else:
        assert expected in with_reason
        assert with_reason.replace(f" {expected}", "") == without


# =============================================================================
# End to end: OrderManager + FMPPaperBroker + store + composer
# =============================================================================

def test_end_to_end_pipeline_trade_explains_itself(tmp_path):
    broker = FMPPaperBroker(db_url=f"sqlite:///{tmp_path / 'e2e.db'}")
    om = OrderManager(broker, dry_run=False, kill_switch=GlobalKillSwitch(sentinel_file=tmp_path / "KS"))

    buy = OrderIntent(strategy_id="main_pipeline", symbol="AAPL", side=OrderSide.BUY, qty=5.0,
                      decision_context=_entry_ctx())
    sell = OrderIntent(strategy_id="main_pipeline", symbol="AAPL", side=OrderSide.SELL, qty=5.0,
                       decision_context=tc.build_exit_context(_entry_row(), signal="RISK REDUCE", held_qty=5.0))
    with _quote(150.0):
        assert asyncio.run(om.submit_order_with_idempotency(buy)).status == OrderStatus.FILLED
    with _quote(140.0):
        assert asyncio.run(om.submit_order_with_idempotency(sell)).status == OrderStatus.FILLED

    t = broker.store.get_full_closed_trades()[0]
    assert t["strategy_id"] == "main_pipeline"
    assert t["close_reason"] == "signal_risk_reduce"
    snap = broker.store.get_entry_snapshot(t["entry_snapshot_id"])
    assert snap["client_order_id"] == buy.client_order_id
    assert snap["trade_id"] == str(t["trade_id"])
    assert not math.isnan(json.loads(snap["key_indicators_json"])["score"])

    rec = _composer(broker.store, tmp_path).compose_trade_retrospective(t["trade_id"])
    assert rec["entry_snapshot"]["captured"] is True
    assert rec["exit_context"]["exit_signal"] == "RISK REDUCE"


def test_orchestrator_attaches_context_after_construction():
    """Static guard: the context is assigned after OrderIntent(...) and never
    passed into the constructor, so the trading kwargs stay untouched."""
    tree = ast.parse((REPO / "main_orchestrator.py").read_text())
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_execute_broker_orders")
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "OrderIntent":
            assert "decision_context" not in {k.arg for k in node.keywords}
    assigns = [n for n in ast.walk(fn) if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Attribute) and t.attr == "decision_context" for t in n.targets)]
    assert len(assigns) == 2
