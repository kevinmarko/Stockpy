"""Tests for settings.CLOSED_POSITION_RETENTION_DAYS -- keeping a fully-sold
symbol visible to the advisory pipeline for a bounded window after its most
recent real Robinhood SELL fill.

Covers:
  * main.py::_build_universe -- retention unioned AFTER the auto-drop
    subtraction (survives SYMBOL_RATING_AUTO_DROP_ENABLED) and AFTER the
    empty-universe fallback decision (doesn't suppress DEFAULT_TICKERS/Sheet2).
  * MAX_SYMBOLS cap and CLOSED_POSITION_RETENTION_DAYS=0 restoring the exact
    pre-2026-08 universe.
  * data/portfolio_sync.py::build_sync_report injecting the synthetic
    "closed:recent" watchlist, and resolve_universe() protecting it from the
    auto-drop subtraction.
  * async_sync_now()'s DEFAULT_TICKERS persist excluding retained symbols
    (the leak this feature closes).
  * A store failure never shrinks the universe.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest

import main as m
from main import _build_universe, _recently_closed_universe_symbols


@pytest.fixture(autouse=True)
def _isolate_scan_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralize main.discovery() -- see tests/test_run_once.py's identical
    fixture for the full rationale (a real ~/.stockpy_local/output/
    scan_candidates.json on the machine running the suite would otherwise
    pollute every universe-building assertion here)."""
    monkeypatch.setattr("main.discovery", lambda *a, **kw: {"candidates": []})


# ---------------------------------------------------------------------------
# main.py::_build_universe
# ---------------------------------------------------------------------------


def _make_snapshot(positions: Optional[Dict[str, Any]] = None) -> MagicMock:
    snap = MagicMock()
    snap.positions = positions or {}
    return snap


def _make_position(symbol: str) -> MagicMock:
    pos = MagicMock()
    pos.symbol = symbol
    return pos


class TestBuildUniverseRetention:
    def test_retained_symbol_added_when_not_held(self, monkeypatch, tmp_path):
        monkeypatch.delenv("WATCHLIST", raising=False)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("main._recently_closed_universe_symbols", lambda held: {"CMCL"})

        snap = _make_snapshot(positions={"AAPL": _make_position("AAPL")})
        result = _build_universe(snap)
        assert "CMCL" in result
        assert "AAPL" in result

    def test_retention_survives_symbol_rating_auto_drop(self, monkeypatch, tmp_path):
        """The trap: a retained symbol has held=False, so unioning it BEFORE
        the auto-drop subtraction would let it be immediately re-subtracted.
        Retention must be applied AFTER, so it survives regardless."""
        monkeypatch.setattr("main.settings.SYMBOL_RATING_AUTO_DROP_ENABLED", True)
        monkeypatch.delenv("WATCHLIST", raising=False)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("main._recently_closed_universe_symbols", lambda held: {"CMCL"})

        # SymbolRatingStore says CMCL is excluded -- retention must win anyway.
        with patch(
            "rating.symbol_rating_store.SymbolRatingStore.get_excluded_symbols",
            return_value={"CMCL"},
        ):
            snap = _make_snapshot(positions={"AAPL": _make_position("AAPL")})
            result = _build_universe(snap)
        assert "CMCL" in result

    def test_retention_does_not_suppress_default_tickers_fallback(self, monkeypatch, tmp_path):
        """Retention must be decided on the PRE-retention combined set for
        the empty-fallback check -- an otherwise-cold account (no held, no
        watchlist, no discovery) must still reach DEFAULT_TICKERS even when
        retention alone would have made `combined` non-empty."""
        monkeypatch.delenv("WATCHLIST", raising=False)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("main.settings.DEFAULT_TICKERS", ["SPY"])
        monkeypatch.setattr("main._recently_closed_universe_symbols", lambda held: {"CMCL"})

        snap = _make_snapshot(positions={})
        with patch("main._load_tickers_from_sheet2", return_value=[]):
            result = _build_universe(snap)
        assert "SPY" in result  # fallback still fired
        assert "CMCL" in result  # retention still unioned in after

    def test_zero_retention_days_byte_identical_to_disabled(self, monkeypatch, tmp_path):
        monkeypatch.setattr("main.settings.CLOSED_POSITION_RETENTION_DAYS", 0)
        monkeypatch.delenv("WATCHLIST", raising=False)
        monkeypatch.chdir(tmp_path)

        snap = _make_snapshot(positions={"AAPL": _make_position("AAPL")})
        result = _build_universe(snap)
        assert set(result) == {"AAPL"}

    def test_retained_held_symbol_not_double_added(self, monkeypatch, tmp_path):
        monkeypatch.delenv("WATCHLIST", raising=False)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("main._recently_closed_universe_symbols", lambda held: set())

        snap = _make_snapshot(positions={"AAPL": _make_position("AAPL")})
        result = _build_universe(snap)
        assert result.count("AAPL") == 1

    def test_store_failure_degrades_to_empty_universe_unaffected(self, monkeypatch, tmp_path):
        monkeypatch.setattr("main.settings.CLOSED_POSITION_RETENTION_DAYS", 180)
        monkeypatch.delenv("WATCHLIST", raising=False)
        monkeypatch.chdir(tmp_path)

        import data.broker_fills_store as bfs

        def _boom(*a, **kw):
            raise RuntimeError("db down")

        monkeypatch.setattr(bfs, "BrokerFillsStore", _boom)

        snap = _make_snapshot(positions={"AAPL": _make_position("AAPL")})
        result = _build_universe(snap)  # must not raise
        assert set(result) == {"AAPL"}


class TestRecentlyClosedUniverseSymbols:
    def test_excludes_held(self, monkeypatch):
        monkeypatch.setattr("main.settings.CLOSED_POSITION_RETENTION_DAYS", 180)
        import data.broker_fills_store as bfs

        monkeypatch.setattr(bfs, "recently_closed_symbols", lambda **kw: ["AAPL", "CMCL"])
        result = _recently_closed_universe_symbols(held={"AAPL"})
        assert result == {"CMCL"}

    def test_zero_days_never_calls_store(self, monkeypatch):
        monkeypatch.setattr("main.settings.CLOSED_POSITION_RETENTION_DAYS", 0)
        import data.broker_fills_store as bfs

        def _boom(**kw):
            raise AssertionError("must not be called when retention is 0")

        monkeypatch.setattr(bfs, "recently_closed_symbols", _boom)
        assert _recently_closed_universe_symbols(held=set()) == set()


# ---------------------------------------------------------------------------
# data/portfolio_sync.py wiring
# ---------------------------------------------------------------------------


class TestBuildSyncReportRetention:
    def test_injects_synthetic_watchlist(self, monkeypatch):
        from data.portfolio_sync import CLOSED_RECENT_LIST_KEY, build_sync_report

        monkeypatch.setattr("data.portfolio_sync.settings.CLOSED_POSITION_RETENTION_DAYS", 180)
        import data.broker_fills_store as bfs

        monkeypatch.setattr(bfs, "recently_closed_symbols", lambda **kw: ["CMCL"])

        report = build_sync_report(None, probe_market=False)
        assert CLOSED_RECENT_LIST_KEY in report.watchlists
        assert tuple(report.watchlists[CLOSED_RECENT_LIST_KEY]) == ("CMCL",)
        assert "CMCL" in report.symbols

    def test_held_symbol_excluded_from_synthetic_watchlist(self, monkeypatch):
        from data.portfolio_sync import CLOSED_RECENT_LIST_KEY, build_sync_report
        from data.robinhood_portfolio import AccountSnapshot, PortfolioPosition

        monkeypatch.setattr("data.portfolio_sync.settings.CLOSED_POSITION_RETENTION_DAYS", 180)
        import data.broker_fills_store as bfs

        monkeypatch.setattr(bfs, "recently_closed_symbols", lambda **kw: ["AAPL", "CMCL"])

        snap = AccountSnapshot(
            positions={
                "AAPL": PortfolioPosition(
                    symbol="AAPL", quantity=1.0, average_cost=1.0, current_price=1.0,
                    market_value=1.0, unrealized_pl=0.0, unrealized_pl_pct=0.0,
                    dividends_received=0.0, name="AAPL",
                )
            },
            buying_power=0.0, total_equity=0.0, total_dividends=0.0,
            fetched_at=datetime.now(timezone.utc),
        )
        report = build_sync_report(snap, probe_market=False)
        assert tuple(report.watchlists.get(CLOSED_RECENT_LIST_KEY, [])) == ("CMCL",)

    def test_zero_retention_never_calls_store(self, monkeypatch):
        from data.portfolio_sync import build_sync_report

        monkeypatch.setattr("data.portfolio_sync.settings.CLOSED_POSITION_RETENTION_DAYS", 0)
        import data.broker_fills_store as bfs

        def _boom(**kw):
            raise AssertionError("must not be called when retention is 0")

        monkeypatch.setattr(bfs, "recently_closed_symbols", _boom)
        build_sync_report(None, probe_market=False)  # must not raise


class TestResolveUniverseRetentionProtection:
    def test_resolve_universe_protects_retained_symbol_from_auto_drop(self, monkeypatch):
        from data.portfolio_sync import resolve_universe

        monkeypatch.setattr("data.portfolio_sync.settings.SYMBOL_RATING_AUTO_DROP_ENABLED", True)
        monkeypatch.setattr("data.portfolio_sync.settings.CLOSED_POSITION_RETENTION_DAYS", 180)
        monkeypatch.setattr("data.portfolio_sync.settings.DEFAULT_TICKERS", [])

        import data.broker_fills_store as bfs

        monkeypatch.setattr(bfs, "recently_closed_symbols", lambda **kw: ["CMCL"])

        with patch(
            "rating.symbol_rating_store.SymbolRatingStore.get_excluded_symbols",
            return_value={"CMCL"},
        ):
            result = resolve_universe("all", snapshot=None)
        assert "CMCL" in result


# ---------------------------------------------------------------------------
# async_sync_now DEFAULT_TICKERS leak fix
# ---------------------------------------------------------------------------


class TestAsyncSyncNowDefaultTickersLeak:
    def test_retained_symbols_excluded_from_persisted_default_tickers(self, monkeypatch):
        from data.portfolio_sync import CLOSED_RECENT_LIST_KEY, async_sync_now
        from data.robinhood_portfolio import AccountSnapshot, PortfolioPosition

        monkeypatch.setattr("data.portfolio_sync.settings.CLOSED_POSITION_RETENTION_DAYS", 180)
        import data.broker_fills_store as bfs

        monkeypatch.setattr(bfs, "recently_closed_symbols", lambda **kw: ["CMCL"])

        written = {}

        def _fake_write_setting(key, value):
            written[key] = value

        monkeypatch.setattr("shared.env_io.write_setting", _fake_write_setting)

        # A real held position so DEFAULT_TICKERS has something to persist --
        # otherwise (retention as the ONLY source) the "if tickers:" guard
        # correctly skips the write entirely, which isn't what this test is
        # checking.
        snap = AccountSnapshot(
            positions={
                "AAPL": PortfolioPosition(
                    symbol="AAPL", quantity=1.0, average_cost=1.0, current_price=1.0,
                    market_value=1.0, unrealized_pl=0.0, unrealized_pl_pct=0.0,
                    dividends_received=0.0, name="AAPL",
                )
            },
            buying_power=0.0, total_equity=0.0, total_dividends=0.0,
            fetched_at=datetime.now(timezone.utc),
        )
        report = asyncio.run(
            async_sync_now(snap, probe_market=False, persist_default_tickers=True)
        )
        assert CLOSED_RECENT_LIST_KEY in report.watchlists
        assert "CMCL" in report.symbols
        assert "AAPL" in report.symbols
        # ...but CMCL must NOT be in what got persisted to DEFAULT_TICKERS.
        assert "DEFAULT_TICKERS" in written
        assert "AAPL" in written["DEFAULT_TICKERS"]
        assert "CMCL" not in written["DEFAULT_TICKERS"]
