"""
tests/test_production_steps_sector_selection.py
=================================================
Unit tests for pipeline/production_steps.py::_apply_sector_selection -- the
orchestration glue that calls sector_selection_engine.run_sector_selection()
for the current tracked universe.

Before this function existed, nothing in either orchestrator ever called
run_sector_selection -- the webapp's Sector Selection screen permanently
rendered its "nothing computed yet" empty state regardless of
SECTOR_SELECTION_ENABLED. These tests lock in the wiring: the settings gate,
the daily-freshness de-dup (so `main.py --interval N` doesn't insert
duplicate sector_correlations rows every cycle), and CONSTRAINT #6
(never raises).

Deliberately targets the module-level `_apply_sector_selection` function
directly rather than going through StrategyEvalStep.run() (which imports
main_orchestrator and its full heavy engine chain at call time) -- this
keeps the test suite importable/runnable without pulling in yfinance/
fredapi/statsmodels/sentence-transformers/etc.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from pipeline.production_steps import _apply_sector_selection


def _df(symbols):
    return pd.DataFrame({"Symbol": symbols})


def _store(latest_by_symbol):
    """MagicMock SectorCorrelationStore whose get_latest(sym) returns
    latest_by_symbol.get(sym, [])."""
    store = MagicMock()
    store.get_latest.side_effect = lambda sym: latest_by_symbol.get(sym, [])
    return store


class TestGating:
    def test_disabled_never_constructs_store_or_calls_engine(self):
        with patch("settings.settings.SECTOR_SELECTION_ENABLED", False), \
             patch("sector_selection_engine._build_correlation_store") as mock_build, \
             patch("sector_selection_engine.run_sector_selection") as mock_run:
            _apply_sector_selection(_df(["AAPL"]))
        mock_build.assert_not_called()
        mock_run.assert_not_called()

    def test_empty_dashboard_is_no_op(self):
        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True), \
             patch("sector_selection_engine.run_sector_selection") as mock_run:
            _apply_sector_selection(_df([]))
        mock_run.assert_not_called()

    def test_none_dashboard_is_no_op(self):
        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True), \
             patch("sector_selection_engine.run_sector_selection") as mock_run:
            _apply_sector_selection(None)
        mock_run.assert_not_called()

    def test_missing_symbol_column_degrades_no_crash(self):
        df = pd.DataFrame({"sector": ["Technology"]})
        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True), \
             patch("sector_selection_engine.run_sector_selection") as mock_run:
            _apply_sector_selection(df)  # must not raise
        mock_run.assert_not_called()


class TestDailyFreshnessGate:
    def test_all_symbols_stale_are_all_passed_through(self):
        store = _store({})  # nothing computed yet for anyone
        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True), \
             patch("sector_selection_engine._build_correlation_store", return_value=store), \
             patch("data.historical_store.HistoricalStore.resolve_trading_day", return_value="2026-07-31"), \
             patch("sector_selection_engine.run_sector_selection") as mock_run:
            _apply_sector_selection(_df(["AAPL", "MSFT"]))

        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        assert sorted(args[0]) == ["AAPL", "MSFT"]
        assert kwargs["correlation_store"] is store

    def test_symbol_already_fresh_today_is_skipped(self):
        store = _store({
            "AAPL": [{"as_of": "2026-07-31"}],  # already computed today
            "MSFT": [{"as_of": "2026-07-30"}],  # stale (yesterday)
        })
        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True), \
             patch("sector_selection_engine._build_correlation_store", return_value=store), \
             patch("data.historical_store.HistoricalStore.resolve_trading_day", return_value="2026-07-31"), \
             patch("sector_selection_engine.run_sector_selection") as mock_run:
            _apply_sector_selection(_df(["AAPL", "MSFT"]))

        mock_run.assert_called_once()
        args, _ = mock_run.call_args
        assert args[0] == ["MSFT"]

    def test_all_symbols_fresh_skips_engine_call_entirely(self):
        store = _store({
            "AAPL": [{"as_of": "2026-07-31"}],
            "MSFT": [{"as_of": "2026-07-31"}],
        })
        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True), \
             patch("sector_selection_engine._build_correlation_store", return_value=store), \
             patch("data.historical_store.HistoricalStore.resolve_trading_day", return_value="2026-07-31"), \
             patch("sector_selection_engine.run_sector_selection") as mock_run:
            _apply_sector_selection(_df(["AAPL", "MSFT"]))

        mock_run.assert_not_called()

    def test_duplicate_and_lowercase_symbols_deduplicated_and_uppercased(self):
        store = _store({})
        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True), \
             patch("sector_selection_engine._build_correlation_store", return_value=store), \
             patch("data.historical_store.HistoricalStore.resolve_trading_day", return_value="2026-07-31"), \
             patch("sector_selection_engine.run_sector_selection") as mock_run:
            _apply_sector_selection(_df(["aapl", "AAPL", "msft"]))

        args, _ = mock_run.call_args
        assert sorted(args[0]) == ["AAPL", "MSFT"]


class TestResilience:
    def test_engine_exception_is_swallowed_never_raises(self):
        store = _store({})
        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True), \
             patch("sector_selection_engine._build_correlation_store", return_value=store), \
             patch("data.historical_store.HistoricalStore.resolve_trading_day", return_value="2026-07-31"), \
             patch("sector_selection_engine.run_sector_selection", side_effect=RuntimeError("boom")):
            _apply_sector_selection(_df(["AAPL"]))  # must not raise

    def test_store_construction_failure_is_swallowed_never_raises(self):
        with patch("settings.settings.SECTOR_SELECTION_ENABLED", True), \
             patch("sector_selection_engine._build_correlation_store", side_effect=RuntimeError("db down")):
            _apply_sector_selection(_df(["AAPL"]))  # must not raise
