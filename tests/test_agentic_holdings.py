"""Tests for the Agentic-account holdings snapshot and its daemon consumers.

Contracts K1 + K2 of ``.claude/agentic_exits_speed_partial_fills_implementation_plan.md``:

* ``execution/agentic_holdings.py`` -- the ``save`` CLI (writer) and
  ``load_agentic_positions`` (reader): stale, unreadable, empty book, missing
  cost/price skipped, never raises.
* ``pipeline/advisory_inputs.py::build_universe_detailed`` -- Agentic symbols
  join the universe with held protection; ``held`` stays main-account only.
* ``pipeline/production_steps.py::AdvisoryOverlayStep`` -- an Agentic-only
  position is evaluated as held (a loss + bearish forecast -> SELL).
* ``execution/queue_builder.py`` / ``execution/compose.py`` -- an Agentic-only
  SELL is queued with ``exit_kind`` and ``holding_source``; the main account
  wins for a name held in both; BUYs are unaffected.

Fully offline. Every test that touches the holdings file points
``settings.OUTPUT_DIR`` (or ``output_dir``) at its own ``tmp_path`` so nothing
leaks into the session-wide test OUTPUT_DIR other tests read.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from data.robinhood_portfolio import PortfolioPosition
from execution import agentic_holdings as ah
from execution import queue_builder as qb

NOW = datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# MCP payload builders (real response shapes, made-up numbers)
# ---------------------------------------------------------------------------

def positions_payload(*rows):
    """rows are (symbol, qty, sellable, avg_cost-or-None)."""
    out = []
    for sym, qty, sell, cost in rows:
        row = {"symbol": sym, "quantity": str(qty), "shares_available_for_sells": str(sell)}
        if cost is not None:
            row["average_buy_price"] = str(cost)
        out.append(row)
    return {"data": {"positions": out}}


def quotes_payload(**prices):
    """prices: SYM=(last, bid) -- either may be None."""
    rows = []
    for sym, (last, bid) in prices.items():
        q = {"symbol": sym, "state": "active", "has_traded": True,
             "venue_last_trade_time": "2026-11-14T14:59:00Z"}
        if last is not None:
            q["last_trade_price"] = str(last)
        if bid is not None:
            q["bid_price"] = str(bid)
        rows.append({"quote": q})
    return {"data": {"results": rows}}


def _write_doc(tmp_path, positions: Dict[str, Dict[str, Any]], *, fetched_at: Optional[datetime] = None,
               source: str = "risk"):
    path = tmp_path / "agentic" / "agentic_holdings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "fetched_at": (fetched_at or NOW).isoformat(),
        "source": source,
        "positions": positions,
    }))
    return path


def _row(qty=3.0, cost=50.0, price=40.0, sellable=None):
    return {"quantity": qty, "sellable": qty if sellable is None else sellable,
            "average_cost": cost, "current_price": price}


def _run_cli(tmp_path, positions=None, quotes=None, *, raw_positions: Optional[str] = None,
             raw_quotes: Optional[str] = None, source="risk", capsys=None):
    args = ["save", "--source", source, "--output-dir", str(tmp_path)]
    pf = tmp_path / "positions.json"
    pf.write_text(raw_positions if raw_positions is not None else json.dumps(positions))
    args += ["--positions", str(pf)]
    if quotes is not None or raw_quotes is not None:
        qf = tmp_path / "quotes.json"
        qf.write_text(raw_quotes if raw_quotes is not None else json.dumps(quotes))
        args += ["--quotes", str(qf)]
    rc = ah.main(args)
    out = json.loads(capsys.readouterr().out) if capsys is not None else None
    return rc, out


# ===========================================================================
# K1 writer
# ===========================================================================

class TestWriterCli:
    def test_writes_positions_with_quote_prices(self, tmp_path, capsys):
        rc, out = _run_cli(
            tmp_path,
            positions_payload(("F", 3, 3, 12.5), ("AAPL", 1, 1, 200)),
            quotes_payload(F=(11.0, 10.9), AAPL=(None, 190.0)),
            capsys=capsys,
        )
        assert rc == 0
        doc = json.loads((tmp_path / "agentic" / "agentic_holdings.json").read_text())
        assert doc["source"] == "risk"
        assert datetime.fromisoformat(doc["fetched_at"]).tzinfo is not None
        assert doc["positions"]["F"] == {"quantity": 3.0, "sellable": 3.0,
                                         "average_cost": 12.5, "current_price": 11.0}
        # No last trade -> the bid.
        assert doc["positions"]["AAPL"]["current_price"] == 190.0
        assert out["n_positions"] == 2 and out["missing_price"] == [] and out["missing_cost"] == []

    def test_missing_quote_and_cost_are_null_not_guessed(self, tmp_path, capsys):
        rc, out = _run_cli(tmp_path, positions_payload(("F", 3, 3, None), ("GM", 2, 2, 30)),
                           quotes_payload(F=(11.0, None)), source="review", capsys=capsys)
        assert rc == 0
        doc = json.loads((tmp_path / "agentic" / "agentic_holdings.json").read_text())
        assert doc["source"] == "review"
        assert doc["positions"]["F"]["average_cost"] is None
        assert doc["positions"]["GM"]["current_price"] is None
        assert out["missing_price"] == ["GM"] and out["missing_cost"] == ["F"]

    def test_no_quotes_flag_writes_null_prices(self, tmp_path, capsys):
        rc, _ = _run_cli(tmp_path, positions_payload(("F", 3, 3, 12.5)), capsys=capsys)
        assert rc == 0
        doc = json.loads((tmp_path / "agentic" / "agentic_holdings.json").read_text())
        assert doc["positions"]["F"]["current_price"] is None

    def test_empty_book_is_a_real_reading(self, tmp_path, capsys):
        rc, out = _run_cli(tmp_path, {"data": {"positions": []}}, capsys=capsys)
        assert rc == 0
        doc = json.loads((tmp_path / "agentic" / "agentic_holdings.json").read_text())
        assert doc["positions"] == {}
        assert out["n_positions"] == 0

    @pytest.mark.parametrize("raw", [
        "not json{",
        json.dumps({"data": {"error": "unauthorized"}}),
        json.dumps(None),
        json.dumps({"data": {"positions": [{"quantity": "3"}]}}),  # row without symbol
    ])
    def test_unreadable_positions_write_nothing_and_exit_2(self, tmp_path, capsys, raw):
        rc, out = _run_cli(tmp_path, raw_positions=raw, capsys=capsys)
        assert rc == 2
        assert "error" in out
        assert not (tmp_path / "agentic" / "agentic_holdings.json").exists()

    def test_unreadable_positions_keep_the_previous_file(self, tmp_path, capsys):
        prev = _write_doc(tmp_path, {"F": _row()})
        before = prev.read_text()
        rc, _ = _run_cli(tmp_path, raw_positions="garbage", capsys=capsys)
        assert rc == 2
        assert prev.read_text() == before

    def test_unreadable_quotes_file_writes_nothing(self, tmp_path, capsys):
        rc, out = _run_cli(tmp_path, positions_payload(("F", 3, 3, 12.5)), raw_quotes="{bad",
                           capsys=capsys)
        assert rc == 2 and "quotes unreadable" in out["error"]
        assert not (tmp_path / "agentic" / "agentic_holdings.json").exists()

    def test_paginated_positions_are_merged(self, tmp_path, capsys):
        pages = [positions_payload(("F", 3, 3, 12.5)), positions_payload(("GM", 2, 2, 30))]
        rc, out = _run_cli(tmp_path, pages, capsys=capsys)
        assert rc == 0 and out["n_positions"] == 2

    def test_bad_source_is_rejected(self, tmp_path):
        pf = tmp_path / "p.json"
        pf.write_text(json.dumps(positions_payload(("F", 3, 3, 12.5))))
        with pytest.raises(SystemExit):
            ah.main(["save", "--positions", str(pf), "--source", "nope", "--output-dir", str(tmp_path)])

    def test_round_trip_through_the_reader(self, tmp_path, capsys):
        _run_cli(tmp_path, positions_payload(("F", 4, 4, 12.5)), quotes_payload(F=(10.0, 9.9)),
                 capsys=capsys)
        got = ah.load_agentic_positions(tmp_path)
        assert set(got) == {"F"}
        assert got["F"].quantity == 4.0 and got["F"].current_price == 10.0


# ===========================================================================
# K1 reader
# ===========================================================================

class TestReader:
    def test_reads_portfolio_positions(self, tmp_path):
        _write_doc(tmp_path, {"f": _row(qty=4, cost=50.0, price=40.0)})
        got = ah.load_agentic_positions(tmp_path, now=NOW)
        pos = got["F"]
        assert isinstance(pos, PortfolioPosition)
        assert pos.symbol == "F" and pos.quantity == 4.0
        assert pos.average_cost == 50.0 and pos.current_price == 40.0
        assert pos.market_value == pytest.approx(160.0)
        assert pos.unrealized_pl == pytest.approx(-40.0)
        assert pos.unrealized_pl_pct == pytest.approx(-20.0)
        assert pos.dividends_received == 0.0

    def test_missing_file_is_empty_and_logged_at_info(self, tmp_path, caplog):
        # A missing file is the normal state until the agents first write one,
        # so it is an INFO line, not a WARNING on every daemon cycle.
        with caplog.at_level(logging.INFO, logger="execution.agentic_holdings"):
            assert ah.load_agentic_positions(tmp_path, now=NOW) == {}
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len([r for r in caplog.records if r.levelno == logging.INFO]) == 1

    def test_stale_file_is_empty(self, tmp_path, caplog):
        _write_doc(tmp_path, {"F": _row()}, fetched_at=NOW - timedelta(hours=ah.MAX_AGE_HOURS + 1))
        with caplog.at_level(logging.WARNING, logger="execution.agentic_holdings"):
            assert ah.load_agentic_positions(tmp_path, now=NOW) == {}
        assert any("stale" in r.getMessage() for r in caplog.records)

    def test_within_max_age_is_read(self, tmp_path):
        _write_doc(tmp_path, {"F": _row()}, fetched_at=NOW - timedelta(hours=ah.MAX_AGE_HOURS - 1))
        assert set(ah.load_agentic_positions(tmp_path, now=NOW)) == {"F"}

    def test_max_age_hours_override(self, tmp_path):
        _write_doc(tmp_path, {"F": _row()}, fetched_at=NOW - timedelta(hours=2))
        assert ah.load_agentic_positions(tmp_path, now=NOW, max_age_hours=1) == {}

    def test_future_fetched_at_is_unreadable(self, tmp_path):
        _write_doc(tmp_path, {"F": _row()}, fetched_at=NOW + timedelta(hours=1))
        assert ah.load_agentic_positions(tmp_path, now=NOW) == {}

    @pytest.mark.parametrize("raw", [
        "{not json",
        "[]",
        json.dumps({"positions": {"F": _row()}}),  # no fetched_at
        json.dumps({"fetched_at": "yesterday", "positions": {}}),
        json.dumps({"fetched_at": NOW.isoformat(), "positions": ["F"]}),
    ])
    def test_unparseable_file_is_empty_and_never_raises(self, tmp_path, raw, caplog):
        path = tmp_path / "agentic" / "agentic_holdings.json"
        path.parent.mkdir(parents=True)
        path.write_text(raw)
        with caplog.at_level(logging.WARNING, logger="execution.agentic_holdings"):
            assert ah.load_agentic_positions(tmp_path, now=NOW) == {}
        assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1

    def test_empty_book_reads_as_empty_without_error(self, tmp_path):
        _write_doc(tmp_path, {})
        assert ah.load_agentic_positions(tmp_path, now=NOW) == {}

    def test_positions_missing_cost_or_price_or_qty_are_skipped(self, tmp_path, caplog):
        _write_doc(tmp_path, {
            "OK": _row(),
            "NOCOST": _row(cost=None),
            "NOPRICE": _row(price=None),
            "ZEROQTY": _row(qty=0),
            "NEGQTY": _row(qty=-1),
            "NANPRICE": {"quantity": 1, "average_cost": 5.0, "current_price": "nan"},
            "JUNK": "not a dict",
        })
        with caplog.at_level(logging.WARNING, logger="execution.agentic_holdings"):
            got = ah.load_agentic_positions(tmp_path, now=NOW)
        assert set(got) == {"OK"}
        msg = " ".join(r.getMessage() for r in caplog.records)
        assert "NOCOST" in msg and "NOPRICE" in msg and "ZEROQTY" in msg

    def test_default_output_dir_is_settings_output_dir(self, tmp_path, monkeypatch):
        from settings import settings
        monkeypatch.setattr(settings, "OUTPUT_DIR", str(tmp_path), raising=False)
        _write_doc(tmp_path, {"F": _row()}, fetched_at=datetime.now(timezone.utc))
        assert set(ah.load_agentic_positions()) == {"F"}

    def test_unreadable_path_never_raises(self, tmp_path):
        # The holdings path is a directory, not a file.
        (tmp_path / "agentic" / "agentic_holdings.json").mkdir(parents=True)
        assert ah.load_agentic_positions(tmp_path, now=NOW) == {}


# ===========================================================================
# K2 universe
# ===========================================================================

def _snapshot(positions: Optional[Dict[str, Any]] = None) -> MagicMock:
    snap = MagicMock()
    snap.positions = positions or {}
    return snap


class TestUniverse:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch, tmp_path):
        from settings import settings
        monkeypatch.setattr(settings, "OUTPUT_DIR", str(tmp_path), raising=False)
        monkeypatch.setattr("pipeline.advisory_inputs.discovery", lambda *a, **kw: {"candidates": []})
        monkeypatch.setattr("pipeline.advisory_inputs.recently_closed_universe_symbols", lambda held: set())
        monkeypatch.setattr("pipeline.advisory_inputs._open_pipeline_paper_symbols", lambda: set())
        monkeypatch.setattr("pipeline.advisory_inputs.load_watchlist", lambda *a: ["MSFT"])
        monkeypatch.setattr(settings, "DEFAULT_TICKERS", ["SPY"], raising=False)
        self.tmp = tmp_path

    def test_agentic_symbols_join_the_universe_and_held_stays_main_only(self):
        from pipeline.advisory_inputs import build_universe_detailed
        _write_doc(self.tmp, {"F": _row(), "AAPL": _row()}, fetched_at=datetime.now(timezone.utc))
        build = build_universe_detailed(_snapshot({"AAPL": object()}))
        assert {"F", "AAPL", "MSFT"} <= set(build.symbols)
        assert build.held == frozenset({"AAPL"})
        assert build.agentic_held == frozenset({"F", "AAPL"})

    def test_no_file_leaves_the_universe_unchanged(self):
        from pipeline.advisory_inputs import build_universe_detailed
        build = build_universe_detailed(_snapshot({"AAPL": object()}))
        assert build.symbols == ["AAPL", "MSFT"]
        assert build.agentic_held == frozenset()

    def test_agentic_symbols_are_protected_from_rating_auto_drop(self, monkeypatch):
        from pipeline.advisory_inputs import build_universe_detailed
        from settings import settings
        monkeypatch.setattr(settings, "SYMBOL_RATING_AUTO_DROP_ENABLED", True, raising=False)

        class _Store:
            def __init__(self, *a, **kw):
                pass

            def get_excluded_symbols(self, **kw):
                return {"F", "MSFT"}

        monkeypatch.setattr("rating.symbol_rating_store.SymbolRatingStore", _Store)
        _write_doc(self.tmp, {"F": _row()}, fetched_at=datetime.now(timezone.utc))
        build = build_universe_detailed(_snapshot({}))
        assert "F" in build.symbols
        assert "MSFT" not in build.symbols

    def test_agentic_holdings_suppress_the_default_tickers_fallback(self, monkeypatch):
        from pipeline.advisory_inputs import build_universe_detailed
        monkeypatch.setattr("pipeline.advisory_inputs.load_watchlist", lambda *a: [])
        _write_doc(self.tmp, {"F": _row()}, fetched_at=datetime.now(timezone.utc))
        build = build_universe_detailed(_snapshot({}))
        assert build.symbols == ["F"]
        assert build.default_tickers_is_fallback is False

    def test_reader_failure_never_breaks_the_universe(self, monkeypatch):
        from pipeline.advisory_inputs import build_universe_detailed

        def _boom(*a, **kw):
            raise RuntimeError("boom")

        monkeypatch.setattr("execution.agentic_holdings.load_agentic_positions", _boom)
        build = build_universe_detailed(_snapshot({"AAPL": object()}))
        assert build.symbols == ["AAPL", "MSFT"] and build.agentic_held == frozenset()


# ===========================================================================
# K2 AdvisoryOverlayStep
# ===========================================================================

def _overlay_ctx(symbols: List[str], main_positions: Dict[str, Any]):
    import pandas as pd

    from pipeline.context import RunContext

    ctx = RunContext(
        force_account=False,
        started_at=datetime.now(timezone.utc),
        watchlist_file="watchlist.txt",
        fetch_account_snapshot_fn=lambda **_kw: None,
        build_universe_fn=lambda snapshot: [],
        build_macro_dto_fn=lambda: None,
        get_provider_fn=lambda: None,
        fetch_bars_fn=lambda symbols, market: {},
        build_context_extras_fn=lambda symbols, bars, macro_dto: {},
        advisory_evaluate_fn=lambda **_kw: None,
    )
    ctx.symbols = list(symbols)
    snap = MagicMock()
    snap.positions = main_positions
    snap.total_equity = 100_000.0
    snap.buying_power = 10_000.0
    ctx.snapshot = snap
    ctx.macro_dto = None
    ctx.dashboard_df = pd.DataFrame({"Symbol": list(symbols)})
    return ctx


def _main_pos(sym: str) -> PortfolioPosition:
    return PortfolioPosition(symbol=sym, quantity=100.0, average_cost=10.0, current_price=11.0,
                             market_value=1100.0, unrealized_pl=100.0, unrealized_pl_pct=10.0,
                             dividends_received=0.0, name=sym)


class TestAdvisoryOverlay:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch, tmp_path):
        from settings import settings
        monkeypatch.setattr(settings, "OUTPUT_DIR", str(tmp_path), raising=False)
        monkeypatch.setattr(settings, "ADVISORY_MAX_CONCURRENCY", 1, raising=False)
        monkeypatch.setattr("pipeline.advisory_inputs.fetch_bars_for_universe", lambda symbols, market: {})
        monkeypatch.setattr("pipeline.advisory_inputs.build_context_extras",
                            lambda symbols, bars, macro, market: {})
        self.tmp = tmp_path

    def test_position_passed_is_main_then_agentic(self, monkeypatch):
        from engine.advisory import Recommendation
        from pipeline.production_steps import AdvisoryOverlayStep

        seen: Dict[str, Any] = {}

        def _evaluate(**kw):
            seen[kw["symbol"]] = kw["position"]
            return Recommendation(symbol=kw["symbol"], action="HOLD", strategy="t", conviction=0.5,
                                  rationale="r", suggested_position_pct=0.0, forecast=None,
                                  key_indicators={}, data_quality="OK")

        monkeypatch.setattr("engine.advisory.evaluate", _evaluate)
        monkeypatch.setattr("data.market_data.get_provider", lambda: object())
        _write_doc(self.tmp, {"F": _row(qty=3), "AAPL": _row(qty=1)}, fetched_at=datetime.now(timezone.utc))
        main_aapl = _main_pos("AAPL")
        ctx = _overlay_ctx(["AAPL", "F", "MSFT"], {"AAPL": main_aapl})

        AdvisoryOverlayStep().run(ctx)

        assert ctx.context_extras.get("advisory_overlay_ok") is True
        assert seen["AAPL"] is main_aapl                # main wins when held in both
        assert seen["F"].quantity == 3.0                # Agentic-only -> held
        assert seen["MSFT"] is None                     # held nowhere
        assert set(ctx.context_extras["agentic_positions"]) == {"F", "AAPL"}

    def test_no_file_passes_main_positions_only(self, monkeypatch):
        from engine.advisory import Recommendation
        from pipeline.production_steps import AdvisoryOverlayStep

        seen: Dict[str, Any] = {}

        def _evaluate(**kw):
            seen[kw["symbol"]] = kw["position"]
            return Recommendation(symbol=kw["symbol"], action="HOLD", strategy="t", conviction=0.5,
                                  rationale="r", suggested_position_pct=0.0, forecast=None,
                                  key_indicators={}, data_quality="OK")

        monkeypatch.setattr("engine.advisory.evaluate", _evaluate)
        monkeypatch.setattr("data.market_data.get_provider", lambda: object())
        ctx = _overlay_ctx(["F"], {})
        AdvisoryOverlayStep().run(ctx)
        assert seen == {"F": None}
        assert ctx.context_extras["agentic_positions"] == {}

    def test_agentic_only_loser_with_bearish_forecast_is_a_sell(self, monkeypatch):
        """End to end through the REAL engine.advisory.evaluate (heavy engines
        mocked, as tests/test_advisory.py does): an Agentic-only position below
        cost with a bearish forecast is Case A -> SELL."""
        import unittest.mock as mock

        import engine.advisory as adv
        from pipeline.production_steps import AdvisoryOverlayStep
        from tests.test_advisory import _MOCK_TECH, _make_bars, _make_market_provider
        from transactions_store import TransactionsStore

        market = _make_market_provider(price=100.0, bars=_make_bars(252, 100.0))
        monkeypatch.setattr("data.market_data.get_provider", lambda: market)
        ts = TransactionsStore(db_url="sqlite:///:memory:")
        real_evaluate = adv.evaluate
        monkeypatch.setattr("engine.advisory.evaluate",
                            lambda **kw: real_evaluate(transactions_store=ts, **kw))
        _write_doc(self.tmp, {"TEST": _row(qty=50, cost=120.0, price=100.0)},
                   fetched_at=datetime.now(timezone.utc))
        ctx = _overlay_ctx(["TEST"], {})

        with mock.patch("engine.advisory.ProcessingEngine") as MockPE, \
             mock.patch("engine.advisory.ForecastingEngine") as MockFE, \
             mock.patch("engine.advisory.GarchVolatilityEstimator") as MockTOE, \
             mock.patch("engine.advisory.StrategyEngine") as MockSE:
            MockPE.return_value.calculate_technical_metrics.return_value = {"TEST": {**_MOCK_TECH, "RSI": 30.0}}
            MockFE.return_value.generate_forecast.return_value = {"Forecast_30": 94.0}
            MockTOE.return_value.estimate_gjr_garch_volatility.return_value = 0.25
            MockTOE.return_value.estimate_gjr_garch_volatility_term_structure.return_value = {
                h: 0.25 for h in (1, 10, 30, 60, 90)}
            MockSE.return_value.evaluate_security.return_value = {
                "Action Signal": "HOLD", "Score": 40, "Kelly Target": 0.02}
            AdvisoryOverlayStep().run(ctx)

        recs = {r.symbol: r for r in ctx.recommendations}
        assert recs["TEST"].action == "SELL"
        assert recs["TEST"].suggested_exit_pct == adv.CONFIG["exit_fraction_strong_sell"]

        # Without the holdings file the same symbol is not held -> no SELL exit.
        (self.tmp / "agentic" / "agentic_holdings.json").unlink()
        ctx2 = _overlay_ctx(["TEST"], {})
        with mock.patch("engine.advisory.ProcessingEngine") as MockPE, \
             mock.patch("engine.advisory.ForecastingEngine") as MockFE, \
             mock.patch("engine.advisory.GarchVolatilityEstimator") as MockTOE, \
             mock.patch("engine.advisory.StrategyEngine") as MockSE:
            MockPE.return_value.calculate_technical_metrics.return_value = {"TEST": {**_MOCK_TECH, "RSI": 30.0}}
            MockFE.return_value.generate_forecast.return_value = {"Forecast_30": 94.0}
            MockTOE.return_value.estimate_gjr_garch_volatility.return_value = 0.25
            MockTOE.return_value.estimate_gjr_garch_volatility_term_structure.return_value = {
                h: 0.25 for h in (1, 10, 30, 60, 90)}
            MockSE.return_value.evaluate_security.return_value = {
                "Action Signal": "HOLD", "Score": 40, "Kelly Target": 0.02}
            AdvisoryOverlayStep().run(ctx2)
        assert ctx2.recommendations[0].action != "SELL"


# ===========================================================================
# K2 queue
# ===========================================================================

@dataclass
class _Rec:
    symbol: str
    action: str
    conviction: float
    suggested_position_pct: float = 0.0
    strategy: str = "advisory"
    rationale: str = "because"
    suggested_exit_pct: Optional[float] = None


@dataclass
class _Snap:
    positions: Dict[str, Any]
    total_equity: float = 10_000.0
    buying_power: float = 3_000.0


@dataclass
class _RR:
    snapshot: _Snap
    recommendations: List[_Rec]


_RTH = datetime(2026, 6, 30, 17, 0, tzinfo=timezone.utc)


def _agentic(sym="F", qty=4.0, cost=12.0, price=10.0) -> PortfolioPosition:
    return PortfolioPosition(symbol=sym, quantity=qty, average_cost=cost, current_price=price,
                             market_value=qty * price, unrealized_pl=qty * (price - cost),
                             unrealized_pl_pct=(price - cost) / cost * 100.0,
                             dividends_received=0.0, name="")


@pytest.fixture
def _queue_env(monkeypatch):
    monkeypatch.setattr(qb, "_max_notional", lambda: 5000.0)
    monkeypatch.setattr(qb, "_limit_buffer_bps", lambda: 0)


class TestQueue:
    def test_agentic_only_sell_is_queued_with_exit_kind_and_source(self, _queue_env):
        rr = _RR(_Snap({}), [_Rec("F", "SELL", 0.9)])
        payload = qb.build_execution_queue(rr, mode="review", now=_RTH,
                                           agentic_positions={"F": _agentic()})
        (intent,) = payload["intents"]
        assert intent["symbol"] == "F" and intent["side"] == "sell"
        assert intent["qty"] == 4.0
        assert intent["target_notional"] == 40.0
        assert intent["exit_kind"] == "full"
        assert intent["holding_source"] == "agentic"

    def test_agentic_only_fraction_exit(self, _queue_env):
        rr = _RR(_Snap({}), [_Rec("F", "SELL", 0.9, suggested_exit_pct=0.5)])
        (intent,) = qb.build_execution_queue(rr, mode="review", now=_RTH,
                                             agentic_positions={"F": _agentic()})["intents"]
        assert intent["exit_kind"] == "fraction" and intent["exit_fraction"] == 0.5
        assert intent["target_notional"] == 20.0
        assert intent["holding_source"] == "agentic"

    def test_run_result_attribute_is_honoured(self, _queue_env):
        rr = _RR(_Snap({}), [_Rec("F", "SELL", 0.9)])
        rr.agentic_positions = {"F": _agentic()}
        (intent,) = qb.build_execution_queue(rr, mode="review", now=_RTH)["intents"]
        assert intent["holding_source"] == "agentic"

    def test_without_agentic_positions_the_sell_is_dropped(self, _queue_env):
        rr = _RR(_Snap({}), [_Rec("F", "SELL", 0.9)])
        assert qb.build_execution_queue(rr, mode="review", now=_RTH)["intents"] == []
        assert qb.build_execution_queue(rr, mode="review", now=_RTH,
                                        agentic_positions={})["intents"] == []

    def test_main_wins_when_held_in_both_and_omits_the_key(self, _queue_env):
        main = _agentic("F", qty=100.0, cost=12.0, price=10.0)
        rr = _RR(_Snap({"F": main}), [_Rec("F", "SELL", 0.9)])
        with_agentic = qb.build_execution_queue(rr, mode="review", now=_RTH,
                                                agentic_positions={"F": _agentic(qty=4.0)})
        without = qb.build_execution_queue(rr, mode="review", now=_RTH)
        (intent,) = with_agentic["intents"]
        assert intent["qty"] == 100.0
        assert "holding_source" not in intent
        assert json.dumps(with_agentic, sort_keys=True) == json.dumps(without, sort_keys=True)

    def test_buy_is_unaffected_by_agentic_holdings(self, _queue_env):
        rr = _RR(_Snap({}), [_Rec("F", "BUY", 0.9, suggested_position_pct=0.05)])
        with_agentic = qb.build_execution_queue(rr, mode="review", now=_RTH,
                                                agentic_positions={"F": _agentic()})
        without = qb.build_execution_queue(rr, mode="review", now=_RTH)
        assert json.dumps(with_agentic, sort_keys=True) == json.dumps(without, sort_keys=True)
        (intent,) = with_agentic["intents"]
        assert intent["target_notional"] == 500.0  # main total_equity * pct
        assert "holding_source" not in intent


class TestCompose:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch, tmp_path):
        from settings import settings
        monkeypatch.setattr(settings, "OUTPUT_DIR", str(tmp_path), raising=False)
        monkeypatch.setattr(settings, "ROBINHOOD_EXECUTION_MODE", "review", raising=False)
        monkeypatch.setattr(settings, "ROBINHOOD_MAX_NOTIONAL_PER_ORDER", 0.0, raising=False)
        self.tmp = tmp_path

    def _write_sell_source(self, out_dir, now):
        from execution.compose import write_source
        write_source("advisory", [{"symbol": "F", "action": "SELL", "conviction": 0.95,
                                   "suggested_position_pct": 0.0, "strategy": "advisory",
                                   "rationale": "exit"}], output_dir=out_dir, now=now)

    def test_compose_reads_the_holdings_file_by_default(self):
        from execution.compose import compose_and_emit
        now = datetime.now(timezone.utc)
        out_dir = self.tmp / "queue"
        self._write_sell_source(out_dir, now)
        _write_doc(self.tmp, {"F": _row(qty=4, cost=12.0, price=10.0)}, fetched_at=now)
        path = compose_and_emit(_Snap({}, total_equity=100_000.0), output_dir=out_dir, now=now)
        assert path is not None
        (intent,) = json.loads(path.read_text())["intents"]
        assert intent["symbol"] == "F" and intent["holding_source"] == "agentic"
        assert intent["exit_kind"] == "full" and intent["qty"] == 4.0

    def test_compose_with_explicit_empty_map_skips_the_lookup(self):
        from execution.compose import compose_and_emit
        now = datetime.now(timezone.utc)
        out_dir = self.tmp / "queue"
        self._write_sell_source(out_dir, now)
        _write_doc(self.tmp, {"F": _row()}, fetched_at=now)
        path = compose_and_emit(_Snap({}, total_equity=100_000.0), output_dir=out_dir, now=now,
                                agentic_positions={})
        assert json.loads(path.read_text())["intents"] == []

    def test_compose_uses_a_passed_map(self):
        from execution.compose import compose_and_emit
        now = datetime.now(timezone.utc)
        out_dir = self.tmp / "queue"
        self._write_sell_source(out_dir, now)
        path = compose_and_emit(_Snap({}, total_equity=100_000.0), output_dir=out_dir, now=now,
                                agentic_positions={"F": _agentic()})
        (intent,) = json.loads(path.read_text())["intents"]
        assert intent["holding_source"] == "agentic"


class TestAgenticSellPassesQueueGate:
    """An Agentic-only SELL must be ACTIONABLE, not merely present: the queue's
    risk gate sees only the main account, which doesn't hold the symbol."""

    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch, tmp_path):
        from settings import settings
        monkeypatch.setattr(settings, "OUTPUT_DIR", str(tmp_path), raising=False)
        monkeypatch.setattr(settings, "ROBINHOOD_EXECUTION_MODE", "review", raising=False)
        monkeypatch.setattr(settings, "ROBINHOOD_MAX_NOTIONAL_PER_ORDER", 50.0, raising=False)
        self.tmp = tmp_path

    def test_agentic_only_sell_is_gate_allowed(self):
        from execution.compose import compose_and_emit, write_source
        now = datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)  # Wed 10:00 ET, inside RTH
        out_dir = self.tmp / "queue"
        write_source("advisory", [{"symbol": "F", "action": "SELL", "conviction": 0.95,
                                   "suggested_position_pct": 0.0, "strategy": "advisory",
                                   "rationale": "exit"}], output_dir=out_dir, now=now)
        path = compose_and_emit(_Snap({}, total_equity=100_000.0), output_dir=out_dir, now=now,
                                agentic_positions={"F": _agentic()})
        (intent,) = json.loads(path.read_text())["intents"]
        assert intent["holding_source"] == "agentic"
        assert intent["gate_allowed"] is True, intent.get("gate_reasons")
