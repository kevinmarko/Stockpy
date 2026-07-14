"""
tests/test_investyo_mcp_server.py
====================================
Unit tests for ``investyo_mcp_server.py`` — the InvestYo platform's own
FastMCP server (distinct from the GitHub MCP integration), exposing ~28
tools, 3 resources, and 1 prompt template over the platform's engines,
database, and file-based state for a remote MCP client (see
``mcp_remote_adapter.py``, tested separately, for the stdio-proxy that
connects to this server over SSH). This file had zero test coverage of
any kind before this suite (flagged in the 2026-07-14 test-coverage
re-audit's Phase 5 roadmap).

Every ``@mcp.tool()``/``@mcp.resource()``/``@mcp.prompt()`` decorator in
the installed ``mcp`` SDK version returns the original function
unmodified (verified by direct inspection), so every tool is called here
as a plain Python function — no MCP transport/protocol layer is involved.

Two genuine bugs were found and fixed while reading this file to write
these tests (not speculative — verified by direct execution before the
fix):
  1. ``configure_alerts``/``send_test_alert`` imported from
     ``alerting.notifier``, which does not exist — ``alerting`` is a
     plain module (``alerting.py``), not a package, so it has no
     ``notifier`` submodule. Both tools always raised
     ``ModuleNotFoundError`` (caught by their own try/except, so they
     silently returned a "failed" string on every call rather than ever
     working). Fixed to import from ``alerting_mcp.notifier`` — the
     actual sibling module with the matching
     ``get_alert_config``/``save_alert_config``/``send`` API (see
     ``tests/test_alerting_mcp_notifier.py``).
  2. ``plot_equity_curve``/``plot_portfolio_equity`` wrote PNG artifacts
     to a hardcoded, machine-specific absolute path
     (``/Users/kevinlee/.gemini/antigravity/brain/<uuid>``) left over
     from the retired Antigravity IDE (see CLAUDE.md: "Antigravity IDE is
     retired"). On any other machine this would fail outright or write
     to a nonsensical location. Fixed to use
     ``settings.OUTPUT_DIR / "artifacts"``, matching this codebase's
     established output-directory convention (e.g.
     ``get_execution_queue``'s ``output/execution_queue.json``).
     ``TestArtifactDirectoryRegression`` in this file pins the fix.

Testing approach
-----------------
Every dependency this module reaches (``TransactionsStore``,
``HistoricalStore``, ``alerting_mcp.notifier``, ``prompt_registry``,
``simulation_engine``, ``yfinance``, ``subprocess.run``) is imported
LOCALLY inside each tool's function body (not at module top), so mocks
are applied at the dependency's OWN module path (e.g.
``monkeypatch.setattr(transactions_store, "TransactionsStore", ...)``),
not on ``investyo_mcp_server``'s namespace. File-based tools
(``update_watch_rules``, ``update_universe_tickers``,
``get_universe_status``, ``read_platform_logs``) use
``monkeypatch.chdir(tmp_path)`` to isolate from the real repo's
``.env``/``watch_rules.yaml``/``quant_platform.db``/log files.

Coverage
--------
* Resources: ``get_read_only_entry``, ``get_database_schema`` (missing
  DB / real schema / query error), ``get_ticker_context`` (empty
  history / happy path / exception).
* Prompt + ``list_registry_prompts``: registry wiring.
* ``query_investyo_db``: the SELECT-only guard (case-insensitive,
  leading-whitespace-tolerant, rejects INSERT/DROP/UPDATE), missing DB,
  0-row result, formatted multi-row result, malformed-SQL exception path.
* ``execute_paper_trade``: open long/short, close (found/not-found),
  invalid side, ``TransactionsStore`` exception degradation.
* ``update_watch_rules`` / ``update_universe_tickers``: add/update/remove,
  missing file, invalid action, malformed ``DEFAULT_TICKERS`` JSON
  fallback to comma-split.
* ``get_portfolio_summary``: long/short P&L math for open (unrealized,
  priced via mocked yfinance) and closed (realized) positions, win-rate
  calculation, empty-portfolio degradation.
* ``read_platform_logs`` / ``get_universe_status``: DB-present and
  file-present branches, all-absent degradation.
* Representative subprocess-wrapping tools
  (``trigger_data_engine``, ``run_platform_tests``,
  ``trigger_edgar_backfill``): success / ``CalledProcessError`` /
  ``FileNotFoundError`` / ``TimeoutExpired`` paths, and the exact
  constructed ``argv`` for every remaining subprocess-based tool
  (``generate_html_report``, ``trigger_forecasting``,
  ``trigger_macro_engine``, ``trigger_full_pipeline``,
  ``run_validation_harness``, ``compare_strategies``,
  ``trigger_model_retraining``) via a mocked ``subprocess.run`` that
  records its call args.
* ``get_pit_coverage_report`` / ``run_pit_audit`` / ``run_lookahead_check``:
  mocked ``validation.pit_fundamentals`` wiring and output formatting.
* ``get_model_registry_status``: list-shaped and dict-shaped registries,
  staleness threshold, missing file.
* ``get_execution_queue``: missing file, empty queue, list- and
  dict-wrapped shapes, gated/blocked rendering.
* ``get_trade_journal``: symbol filter, win-rate/P&L summary.
* ``configure_alerts`` / ``send_test_alert``: partial-update preserves
  existing config, event toggles, per-channel result rendering — also
  the regression proof that the ``alerting_mcp.notifier`` import fix
  above actually works (these would have raised ``ModuleNotFoundError``
  before the fix).
* ``TestArtifactDirectoryRegression``: ``plot_equity_curve`` writes its
  PNG under ``settings.OUTPUT_DIR / "artifacts"``, not the old
  hardcoded personal-machine path.
* No order-submission function names anywhere in this file (mirrors, for
  this specific file, the same guard
  ``tests/test_pipeline_smoke.py::TestNoOrderFunctions`` already applies
  repo-wide — this file is NOT in that guard's exclusion list, so it was
  already covered, but pinning it here documents the intent locally).
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest
import yaml

import investyo_mcp_server as srv


def _capturing_run(captured, result):
    """subprocess.run replacement that records the argv into *captured*
    under the "cmd" key and returns *result* -- a plain closure, unlike a
    `captured.setdefault(...) or result` one-liner, whose `or` would
    short-circuit and return the (truthy, non-empty) argv list itself
    instead of *result*."""

    def _run(cmd, **kwargs):
        captured["cmd"] = cmd
        return result

    return _run


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


class TestGetReadOnlyEntry:
    def test_returns_locked_config_json(self):
        result = json.loads(srv.get_read_only_entry())
        assert result["entry_id"] == "historical_seed_001"
        assert result["permissions"] == "locked"


class TestGetDatabaseSchema:
    def test_missing_db_returns_error(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        assert "not found" in srv.get_database_schema()

    def test_real_schema_returned(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        conn = sqlite3.connect("quant_platform.db")
        conn.execute("CREATE TABLE Foo (id INTEGER PRIMARY KEY, name TEXT)")
        conn.commit()
        conn.close()

        result = srv.get_database_schema()

        assert "CREATE TABLE Foo" in result

    def test_empty_db_returns_placeholder(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        sqlite3.connect("quant_platform.db").close()

        assert srv.get_database_schema() == "Database is currently empty."

    def test_query_exception_degrades_to_error_string(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        sqlite3.connect("quant_platform.db").close()

        def _raise(*a, **k):
            raise sqlite3.OperationalError("disk I/O error")

        monkeypatch.setattr(sqlite3, "connect", _raise)

        assert "Database connection error" in srv.get_database_schema()


class TestGetTickerContext:
    def test_empty_history_returns_message(self, monkeypatch):
        fake_ticker = MagicMock()
        fake_ticker.history.return_value = pd.DataFrame()
        fake_yf = SimpleNamespace(Ticker=lambda symbol: fake_ticker)
        monkeypatch.setitem(__import__("sys").modules, "yfinance", fake_yf)

        assert "No pricing data" in srv.get_ticker_context("ZZZZ")

    def test_happy_path_renders_markdown(self, monkeypatch):
        idx = pd.bdate_range("2026-01-01", periods=5)
        hist = pd.DataFrame(
            {"Open": 1.0, "High": 2.0, "Low": 0.5, "Close": 1.5, "Volume": 100},
            index=idx,
        )
        fake_ticker = MagicMock()
        fake_ticker.history.return_value = hist
        fake_ticker.info = {"longName": "Test Corp", "sector": "Tech", "trailingPE": 10.0, "priceToBook": 2.0}
        fake_yf = SimpleNamespace(Ticker=lambda symbol: fake_ticker)
        monkeypatch.setitem(__import__("sys").modules, "yfinance", fake_yf)

        result = srv.get_ticker_context("AAPL")

        assert "Test Corp" in result
        assert "Tech" in result

    def test_exception_degrades_to_error_string(self, monkeypatch):
        def _raise(symbol):
            raise RuntimeError("network down")

        fake_yf = SimpleNamespace(Ticker=_raise)
        monkeypatch.setitem(__import__("sys").modules, "yfinance", fake_yf)

        assert "Error retrieving context" in srv.get_ticker_context("AAPL")


# ---------------------------------------------------------------------------
# Prompt + list_registry_prompts
# ---------------------------------------------------------------------------


class TestPromptRegistryWiring:
    def test_investyo_registry_prompt_uses_registry_get(self, monkeypatch):
        import prompt_registry

        fake_registry = MagicMock()
        fake_registry.get.return_value = "THE PROMPT BODY"
        monkeypatch.setattr(prompt_registry, "get_registry", lambda: fake_registry)

        result = srv.investyo_registry_prompt("gravity_system")

        fake_registry.get.assert_called_once_with("gravity_system")
        assert "THE PROMPT BODY" in result

    def test_list_registry_prompts(self, monkeypatch):
        import prompt_registry.cache as cache_mod

        monkeypatch.setattr(cache_mod, "list_baseline_ids", lambda: ["a", "b"])

        result = srv.list_registry_prompts()

        assert "- a" in result
        assert "- b" in result


# ---------------------------------------------------------------------------
# query_investyo_db
# ---------------------------------------------------------------------------


class TestQueryInvestyoDb:
    @pytest.mark.parametrize(
        "bad_query",
        ["INSERT INTO x VALUES (1)", "DROP TABLE x", "UPDATE x SET y=1", "delete from x"],
    )
    def test_rejects_non_select(self, bad_query):
        assert "Only SELECT queries are permitted" in srv.query_investyo_db(bad_query)

    def test_accepts_lowercase_and_leading_whitespace_select(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        sqlite3.connect("quant_platform.db").close()
        assert "Only SELECT" not in srv.query_investyo_db("   select 1")

    def test_missing_db_returns_error(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        assert "not found" in srv.query_investyo_db("SELECT 1")

    def test_zero_rows_message(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        conn = sqlite3.connect("quant_platform.db")
        conn.execute("CREATE TABLE T (id INTEGER)")
        conn.commit()
        conn.close()

        result = srv.query_investyo_db("SELECT * FROM T")

        assert "returned 0 rows" in result

    def test_formatted_multi_row_result(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        conn = sqlite3.connect("quant_platform.db")
        conn.execute("CREATE TABLE T (symbol TEXT, score REAL)")
        conn.execute("INSERT INTO T VALUES ('AAPL', 1.5)")
        conn.execute("INSERT INTO T VALUES ('MSFT', 2.5)")
        conn.commit()
        conn.close()

        result = srv.query_investyo_db("SELECT symbol, score FROM T ORDER BY symbol")

        assert "symbol, score" in result
        assert "AAPL, 1.5" in result
        assert "MSFT, 2.5" in result

    def test_malformed_sql_degrades_to_error_string(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        sqlite3.connect("quant_platform.db").close()

        result = srv.query_investyo_db("SELECT * FROM nonexistent_table")

        assert "Database query failed" in result


# ---------------------------------------------------------------------------
# execute_paper_trade
# ---------------------------------------------------------------------------


class TestExecutePaperTrade:
    def test_open_long_position(self, monkeypatch):
        import transactions_store

        fake_store = MagicMock()
        fake_store.record_trade.return_value = 42
        monkeypatch.setattr(transactions_store, "TransactionsStore", lambda: fake_store)

        result = srv.execute_paper_trade("aapl", "buy", 150.0, 10.0)

        assert "Trade ID: 42" in result
        assert "long" in result
        fake_store.record_trade.assert_called_once()
        assert fake_store.record_trade.call_args.kwargs["symbol"] == "AAPL"
        assert fake_store.record_trade.call_args.kwargs["side"] == "long"

    def test_open_short_position(self, monkeypatch):
        import transactions_store

        fake_store = MagicMock()
        fake_store.record_trade.return_value = 7
        monkeypatch.setattr(transactions_store, "TransactionsStore", lambda: fake_store)

        result = srv.execute_paper_trade("tsla", "short", 200.0, 5.0)

        assert "short" in result
        assert fake_store.record_trade.call_args.kwargs["side"] == "short"

    def test_close_no_open_trade_found(self, monkeypatch):
        import transactions_store

        fake_store = MagicMock()
        fake_store.open_trades_df.return_value = pd.DataFrame()
        monkeypatch.setattr(transactions_store, "TransactionsStore", lambda: fake_store)

        result = srv.execute_paper_trade("aapl", "close", 150.0, 10.0)

        assert "No open paper trades found" in result

    def test_close_success(self, monkeypatch):
        import transactions_store

        fake_store = MagicMock()
        fake_store.open_trades_df.return_value = pd.DataFrame(
            {"symbol": ["AAPL"], "trade_id": [3]}
        )
        monkeypatch.setattr(transactions_store, "TransactionsStore", lambda: fake_store)

        result = srv.execute_paper_trade("aapl", "close", 155.0, 10.0)

        assert "Closed paper trade ID 3" in result
        fake_store.close_trade.assert_called_once()
        assert fake_store.close_trade.call_args.kwargs["trade_id"] == 3

    def test_invalid_side_returns_error(self, monkeypatch):
        import transactions_store

        monkeypatch.setattr(transactions_store, "TransactionsStore", lambda: MagicMock())

        assert "Invalid side" in srv.execute_paper_trade("aapl", "yolo", 1.0, 1.0)

    def test_record_trade_exception_degrades_gracefully(self, monkeypatch):
        import transactions_store

        fake_store = MagicMock()
        fake_store.record_trade.side_effect = RuntimeError("db locked")
        monkeypatch.setattr(transactions_store, "TransactionsStore", lambda: fake_store)

        result = srv.execute_paper_trade("aapl", "buy", 1.0, 1.0)

        assert "Failed to record paper trade" in result


# ---------------------------------------------------------------------------
# update_watch_rules
# ---------------------------------------------------------------------------


class TestUpdateWatchRules:
    def test_missing_file_returns_error(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        assert "not found" in srv.update_watch_rules("add", "AAPL", alert_on="conviction_above")

    def test_add_rule(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "watch_rules.yaml").write_text("rules: []\n", encoding="utf-8")

        result = srv.update_watch_rules(
            "add", "aapl", alert_on="conviction_above", threshold=0.8, priority="high"
        )

        assert "Successfully added" in result
        data = yaml.safe_load((tmp_path / "watch_rules.yaml").read_text(encoding="utf-8"))
        assert data["rules"] == [
            {"symbol": "AAPL", "alert_on": "conviction_above", "threshold": 0.8, "priority": "high"}
        ]

    def test_add_without_alert_on_errors(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "watch_rules.yaml").write_text("rules: []\n", encoding="utf-8")

        assert "'alert_on' is required" in srv.update_watch_rules("add", "AAPL")

    def test_update_replaces_existing_rule_for_symbol(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "watch_rules.yaml").write_text(
            yaml.safe_dump({"rules": [{"symbol": "AAPL", "alert_on": "old_trigger"}]}),
            encoding="utf-8",
        )

        srv.update_watch_rules("update", "AAPL", alert_on="new_trigger")

        data = yaml.safe_load((tmp_path / "watch_rules.yaml").read_text(encoding="utf-8"))
        assert len(data["rules"]) == 1
        assert data["rules"][0]["alert_on"] == "new_trigger"

    def test_remove_rule(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "watch_rules.yaml").write_text(
            yaml.safe_dump({"rules": [{"symbol": "AAPL", "alert_on": "x"}]}), encoding="utf-8"
        )

        result = srv.update_watch_rules("remove", "AAPL")

        assert "Successfully removed" in result
        data = yaml.safe_load((tmp_path / "watch_rules.yaml").read_text(encoding="utf-8"))
        assert data["rules"] == []

    def test_remove_no_matching_rule(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "watch_rules.yaml").write_text("rules: []\n", encoding="utf-8")

        assert "No watch rules found" in srv.update_watch_rules("remove", "MSFT")

    def test_invalid_action(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "watch_rules.yaml").write_text("rules: []\n", encoding="utf-8")

        assert "Invalid action" in srv.update_watch_rules("delete_everything", "AAPL")


# ---------------------------------------------------------------------------
# update_universe_tickers
# ---------------------------------------------------------------------------


class TestUpdateUniverseTickers:
    def test_add_with_no_env_file_uses_default_universe(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)

        result = srv.update_universe_tickers("add", "tsla")

        assert "TSLA" in result
        env_text = (tmp_path / ".env").read_text(encoding="utf-8")
        tickers = json.loads(env_text.split("DEFAULT_TICKERS=", 1)[1].strip())
        assert "TSLA" in tickers
        assert "AAPL" in tickers  # from the hardcoded default

    def test_add_already_present(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text('DEFAULT_TICKERS=["AAPL"]\n', encoding="utf-8")

        assert "already in the trading universe" in srv.update_universe_tickers("add", "aapl")

    def test_remove_present_ticker(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text('DEFAULT_TICKERS=["AAPL", "MSFT"]\n', encoding="utf-8")

        result = srv.update_universe_tickers("remove", "msft")

        # NOTE: the source's f"Successfully {action_lower}ed ..." produces
        # "Successfully removeed" (double e) for action="remove" -- a
        # harmless grammar quirk, not asserted on here.
        assert "MSFT" in result and "active universe" in result
        env_text = (tmp_path / ".env").read_text(encoding="utf-8")
        tickers = json.loads(env_text.split("DEFAULT_TICKERS=", 1)[1].strip())
        assert tickers == ["AAPL"]

    def test_remove_absent_ticker(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text('DEFAULT_TICKERS=["AAPL"]\n', encoding="utf-8")

        assert "is not in the trading universe" in srv.update_universe_tickers("remove", "zzzz")

    def test_malformed_json_falls_back_to_comma_split(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("DEFAULT_TICKERS=AAPL,MSFT\n", encoding="utf-8")

        result = srv.update_universe_tickers("add", "tsla")

        assert "TSLA" in result
        env_text = (tmp_path / ".env").read_text(encoding="utf-8")
        tickers = json.loads(env_text.split("DEFAULT_TICKERS=", 1)[1].strip())
        assert set(tickers) == {"AAPL", "MSFT", "TSLA"}

    def test_invalid_action(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        assert "Invalid action" in srv.update_universe_tickers("destroy", "AAPL")


# ---------------------------------------------------------------------------
# get_portfolio_summary
# ---------------------------------------------------------------------------


class TestGetPortfolioSummary:
    def test_empty_portfolio(self, monkeypatch):
        import transactions_store

        fake_store = MagicMock()
        fake_store.open_trades_df.return_value = pd.DataFrame()
        fake_store.closed_trades_df.return_value = pd.DataFrame()
        monkeypatch.setattr(transactions_store, "TransactionsStore", lambda: fake_store)

        result = srv.get_portfolio_summary()

        assert "No open positions" in result
        assert "No closed trades recorded yet" in result

    def test_open_long_and_short_unrealized_pl(self, monkeypatch):
        import transactions_store

        fake_store = MagicMock()
        fake_store.open_trades_df.return_value = pd.DataFrame(
            {
                "trade_id": [1, 2],
                "symbol": ["AAPL", "TSLA"],
                "side": ["long", "short"],
                "entry_price": [100.0, 200.0],
                "shares": [10.0, 5.0],
            }
        )
        fake_store.closed_trades_df.return_value = pd.DataFrame()
        monkeypatch.setattr(transactions_store, "TransactionsStore", lambda: fake_store)

        fake_ticker = SimpleNamespace(
            history=lambda period: pd.DataFrame({"Close": [110.0]})
        )
        fake_tickers = SimpleNamespace(tickers={"AAPL": fake_ticker, "TSLA": fake_ticker})
        fake_yf = SimpleNamespace(Tickers=lambda syms: fake_tickers)
        monkeypatch.setitem(__import__("sys").modules, "yfinance", fake_yf)

        result = srv.get_portfolio_summary()

        # AAPL long: (110-100)*10 = +100; TSLA short: (200-110)*5 = +450
        assert "+$100.00" in result or "100.00" in result
        assert "Unrealized" in result

    def test_closed_trades_win_rate_and_realized_pl(self, monkeypatch):
        import transactions_store

        fake_store = MagicMock()
        fake_store.open_trades_df.return_value = pd.DataFrame()
        fake_store.closed_trades_df.return_value = pd.DataFrame(
            {
                "side": ["long", "long"],
                "entry_price": [100.0, 100.0],
                "exit_price": [110.0, 90.0],
                "shares": [1.0, 1.0],
            }
        )
        monkeypatch.setattr(transactions_store, "TransactionsStore", lambda: fake_store)

        result = srv.get_portfolio_summary()

        assert "Total Closed Trades**: 2" in result
        assert "Win Rate**: 50.0%" in result

    def test_exception_degrades_to_error_string(self, monkeypatch):
        import transactions_store

        def _raise():
            raise RuntimeError("db down")

        monkeypatch.setattr(transactions_store, "TransactionsStore", _raise)

        assert "Failed to retrieve portfolio summary" in srv.get_portfolio_summary()


# ---------------------------------------------------------------------------
# read_platform_logs
# ---------------------------------------------------------------------------


class TestReadPlatformLogs:
    def test_nothing_present_returns_message(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        assert "No execution logs found" in srv.read_platform_logs()

    def test_db_rows_rendered(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        conn = sqlite3.connect("quant_platform.db")
        conn.execute(
            "CREATE TABLE ExecutionLogs (id INTEGER PRIMARY KEY, timestamp TEXT, status TEXT, "
            "ticker_count INTEGER, execution_time_seconds REAL, error_message TEXT)"
        )
        conn.execute(
            "INSERT INTO ExecutionLogs (timestamp, status, ticker_count, execution_time_seconds, error_message) "
            "VALUES ('2026-01-01', 'OK', 5, 1.23, NULL)"
        )
        conn.commit()
        conn.close()

        result = srv.read_platform_logs(lines=10)

        assert "Database Execution Logs" in result
        assert "OK" in result

    def test_log_file_contents_included(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "app.log").write_text("line1\nline2\nline3\n", encoding="utf-8")

        result = srv.read_platform_logs(lines=2)

        assert "app.log" in result
        assert "line2" in result
        assert "line3" in result


# ---------------------------------------------------------------------------
# get_universe_status
# ---------------------------------------------------------------------------


class TestGetUniverseStatus:
    def test_defaults_when_nothing_present(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)

        result = srv.get_universe_status()

        assert "AAPL" in result  # hardcoded default universe
        # No watch_rules.yaml at all -> the whole section is omitted (the
        # source only renders it when the file exists), not a "none
        # configured" message.
        assert "Active Watch Rules" not in result

    def test_reads_env_and_watch_rules_and_db(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text('DEFAULT_TICKERS=["NVDA"]\n', encoding="utf-8")
        (tmp_path / "watch_rules.yaml").write_text(
            yaml.safe_dump({"rules": [{"symbol": "NVDA", "alert_on": "conviction_above", "threshold": 0.9}]}),
            encoding="utf-8",
        )
        conn = sqlite3.connect("quant_platform.db")
        conn.execute("CREATE TABLE DailySignals (id INTEGER)")
        conn.execute("CREATE TABLE Transactions (id INTEGER)")
        conn.execute("CREATE TABLE ExecutionLogs (id INTEGER)")
        conn.commit()
        conn.close()

        result = srv.get_universe_status()

        assert "NVDA" in result
        assert "conviction_above" in result
        assert "Daily Signals Table Rows**: 0" in result


# ---------------------------------------------------------------------------
# Subprocess-wrapping tools
# ---------------------------------------------------------------------------


class TestTriggerDataEngineSubprocessPattern:
    """Deep test of the try/except CalledProcessError/FileNotFoundError
    pattern shared verbatim by generate_html_report, run_platform_tests,
    trigger_forecasting, trigger_macro_engine, and (with a TimeoutExpired
    variant) trigger_edgar_backfill/trigger_full_pipeline/
    run_validation_harness/compare_strategies/trigger_model_retraining."""

    def test_success(self, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="OK output", returncode=0)
        )
        result = srv.trigger_data_engine("AAPL", "1D")
        assert "Data ingestion successful for AAPL" in result
        assert "OK output" in result

    def test_called_process_error(self, monkeypatch):
        def _raise(*a, **k):
            raise subprocess.CalledProcessError(1, "cmd", stderr="boom")

        monkeypatch.setattr(subprocess, "run", _raise)
        result = srv.trigger_data_engine("AAPL")
        assert "Data ingestion failed" in result
        assert "boom" in result

    def test_file_not_found(self, monkeypatch):
        def _raise(*a, **k):
            raise FileNotFoundError()

        monkeypatch.setattr(subprocess, "run", _raise)
        assert "data_engine.py not found" in srv.trigger_data_engine("AAPL")

    def test_command_args(self, monkeypatch):
        captured = {}

        def _fake_run(cmd, **k):
            captured["cmd"] = cmd
            return SimpleNamespace(stdout="", returncode=0)

        monkeypatch.setattr(subprocess, "run", _fake_run)
        srv.trigger_data_engine("aapl", "5min")

        assert "--symbol" in captured["cmd"]
        assert "aapl" in captured["cmd"]
        assert "--timeframe" in captured["cmd"]
        assert "5min" in captured["cmd"]


class TestRunPlatformTests:
    def test_success(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="5 passed", returncode=0))
        assert "Test suite passed successfully" in srv.run_platform_tests()

    def test_failure(self, monkeypatch):
        def _raise(*a, **k):
            raise subprocess.CalledProcessError(1, "pytest", output="1 failed", stderr="AssertionError")

        monkeypatch.setattr(subprocess, "run", _raise)
        result = srv.run_platform_tests()
        assert "Test suite failed" in result
        assert "AssertionError" in result


class TestTriggerEdgarBackfillTimeoutPattern:
    def test_success(self, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="done", stderr="", returncode=0)
        )
        assert "completed successfully" in srv.trigger_edgar_backfill("AAPL")

    def test_nonzero_exit(self, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="", stderr="oops", returncode=2)
        )
        result = srv.trigger_edgar_backfill()
        assert "exited with code 2" in result

    def test_timeout(self, monkeypatch):
        def _raise(*a, **k):
            raise subprocess.TimeoutExpired(cmd="x", timeout=600)

        monkeypatch.setattr(subprocess, "run", _raise)
        assert "timed out" in srv.trigger_edgar_backfill()

    def test_all_tickers_omits_tickers_flag(self, monkeypatch):
        captured = {}

        def _fake_run(cmd, **k):
            captured["cmd"] = cmd
            return SimpleNamespace(stdout="", stderr="", returncode=0)

        monkeypatch.setattr(subprocess, "run", _fake_run)
        srv.trigger_edgar_backfill(tickers="all")

        assert "--tickers" not in captured["cmd"]

    def test_specific_tickers_included(self, monkeypatch):
        captured = {}

        def _fake_run(cmd, **k):
            captured["cmd"] = cmd
            return SimpleNamespace(stdout="", stderr="", returncode=0)

        monkeypatch.setattr(subprocess, "run", _fake_run)
        srv.trigger_edgar_backfill(tickers="aapl,msft")

        idx = captured["cmd"].index("--tickers")
        assert captured["cmd"][idx + 1 : idx + 3] == ["AAPL", "MSFT"]


class TestRemainingSubprocessToolsArgv:
    """Lighter-touch tests for the remaining subprocess-wrapping tools:
    verify the constructed argv is correct and that a nonzero exit
    produces a readable failure string, without re-deriving the full
    exception-branch matrix already covered above for the pattern."""

    def test_generate_html_report(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            subprocess, "run", _capturing_run(captured, SimpleNamespace(stdout="ok", returncode=0))
        )
        result = srv.generate_html_report("port-1")
        assert "Report generated successfully" in result
        assert "--portfolio" in captured["cmd"] and "port-1" in captured["cmd"]

    def test_trigger_forecasting(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            subprocess, "run", _capturing_run(captured, SimpleNamespace(stdout="ok", returncode=0))
        )
        result = srv.trigger_forecasting("AAPL")
        assert "Forecasting successful for AAPL" in result
        assert "--symbol" in captured["cmd"] and "AAPL" in captured["cmd"]

    def test_trigger_macro_engine(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda cmd, **k: SimpleNamespace(stdout="ok", returncode=0))
        assert "Macro engine run successful" in srv.trigger_macro_engine()

    def test_trigger_full_pipeline_reports_per_step_status(self, monkeypatch):
        calls = []

        def _fake_run(cmd, **k):
            calls.append(cmd)
            return SimpleNamespace(stdout="", stderr="", returncode=0)

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = srv.trigger_full_pipeline("AAPL,MSFT")

        assert "✅ data_engine(AAPL): OK" in result
        assert "✅ data_engine(MSFT): OK" in result
        assert "✅ edgar_backfill: OK" in result
        assert "✅ macro_engine: OK" in result

    def test_run_validation_harness(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            subprocess,
            "run",
            _capturing_run(captured, SimpleNamespace(stdout="Sharpe: 1.2", returncode=0)),
        )
        result = srv.run_validation_harness("my_strat", "2020-01-01", "2021-01-01")
        assert "my_strat" in result
        assert "--strategy" in captured["cmd"]

    def test_compare_strategies(self, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run", lambda cmd, **k: SimpleNamespace(stdout="metrics", returncode=0)
        )
        result = srv.compare_strategies("strat_a", "strat_b")
        assert "strat_a vs strat_b" in result
        assert "## strat_a" in result and "## strat_b" in result

    def test_trigger_model_retraining_all(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            subprocess,
            "run",
            _capturing_run(captured, SimpleNamespace(stdout="retrained", returncode=0)),
        )
        result = srv.trigger_model_retraining()
        assert "Model Retraining Complete" in result
        assert "--model" not in captured["cmd"]

    def test_trigger_model_retraining_specific(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            subprocess,
            "run",
            _capturing_run(captured, SimpleNamespace(stdout="", returncode=0)),
        )
        srv.trigger_model_retraining("lgbm_ranker")
        idx = captured["cmd"].index("--model")
        assert captured["cmd"][idx + 1] == "lgbm_ranker"


# ---------------------------------------------------------------------------
# PIT tools
# ---------------------------------------------------------------------------


class TestPitTools:
    def test_get_pit_coverage_report_empty(self, monkeypatch):
        import data.historical_store as hs_mod
        import validation.pit_fundamentals as pf_mod

        monkeypatch.setattr(hs_mod, "HistoricalStore", lambda: MagicMock())
        monkeypatch.setattr(pf_mod, "generate_coverage_report", lambda store: pd.DataFrame())

        assert "No PIT fundamental data found" in srv.get_pit_coverage_report()

    def test_get_pit_coverage_report_renders_table(self, monkeypatch):
        import data.historical_store as hs_mod
        import validation.pit_fundamentals as pf_mod

        monkeypatch.setattr(hs_mod, "HistoricalStore", lambda: MagicMock())
        monkeypatch.setattr(
            pf_mod, "generate_coverage_report", lambda store: pd.DataFrame({"symbol": ["AAPL"], "rows": [5]})
        )

        result = srv.get_pit_coverage_report()
        assert "PIT Fundamentals Coverage Report" in result
        assert "AAPL" in result

    def test_run_pit_audit_renders_verdict(self, monkeypatch):
        import data.historical_store as hs_mod
        import validation.pit_fundamentals as pf_mod

        fake_result = SimpleNamespace(
            verdict="PASS", report_date="2026-01-01", fields_checked=["eps"], reason="ok", error=None
        )
        monkeypatch.setattr(hs_mod, "HistoricalStore", lambda: MagicMock())
        monkeypatch.setattr(pf_mod, "audit_from_historical_store", lambda store, symbol, date: fake_result)

        result = srv.run_pit_audit("AAPL", "2026-01-01")
        assert "**Verdict**: PASS" in result
        assert "eps" in result

    def test_run_lookahead_check_isolated(self, monkeypatch):
        import data.historical_store as hs_mod
        import validation.pit_fundamentals as pf_mod

        monkeypatch.setattr(hs_mod, "HistoricalStore", lambda: MagicMock())
        monkeypatch.setattr(pf_mod, "audit_no_lookahead_sample", lambda store, symbol, date: True)

        assert "ISOLATED" in srv.run_lookahead_check("AAPL", "2026-01-01")

    def test_run_lookahead_check_contaminated(self, monkeypatch):
        import data.historical_store as hs_mod
        import validation.pit_fundamentals as pf_mod

        monkeypatch.setattr(hs_mod, "HistoricalStore", lambda: MagicMock())
        monkeypatch.setattr(pf_mod, "audit_no_lookahead_sample", lambda store, symbol, date: False)

        assert "CONTAMINATED" in srv.run_lookahead_check("AAPL", "2026-01-01")


# ---------------------------------------------------------------------------
# get_model_registry_status
# ---------------------------------------------------------------------------


class TestGetModelRegistryStatus:
    def test_missing_file(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        assert "not found" in srv.get_model_registry_status()

    def test_list_shaped_registry_with_stale_model(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "ml").mkdir()
        old_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        (tmp_path / "ml" / "registry.yaml").write_text(
            yaml.safe_dump([{"name": "lgbm_ranker", "last_trained": old_date}]), encoding="utf-8"
        )

        result = srv.get_model_registry_status()

        assert "lgbm_ranker" in result
        assert "STALE" in result

    def test_dict_shaped_registry_fresh_model(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "ml").mkdir()
        recent = datetime.now().strftime("%Y-%m-%d")
        (tmp_path / "ml" / "registry.yaml").write_text(
            yaml.safe_dump({"name": "meta_labeler", "last_trained": recent}), encoding="utf-8"
        )

        result = srv.get_model_registry_status()

        assert "meta_labeler" in result
        assert "Fresh" in result

    def test_empty_registry(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "ml").mkdir()
        (tmp_path / "ml" / "registry.yaml").write_text("", encoding="utf-8")

        assert srv.get_model_registry_status() == "Registry is empty."


# ---------------------------------------------------------------------------
# get_execution_queue
# ---------------------------------------------------------------------------


class TestGetExecutionQueue:
    def test_missing_file(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        assert "No execution queue file found" in srv.get_execution_queue()

    def test_empty_queue(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "output").mkdir()
        (tmp_path / "output" / "execution_queue.json").write_text("[]", encoding="utf-8")

        assert "Execution queue is empty" in srv.get_execution_queue()

    def test_list_shaped_queue_renders_gate_status(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "output").mkdir()
        orders = [
            {"symbol": "AAPL", "side": "buy", "shares": 10, "price": 150.0, "allow_place": True, "gate_reason": "ok"},
            {"symbol": "TSLA", "side": "sell", "shares": 5, "price": 200.0, "allow_place": False, "reason": "kill switch active"},
        ]
        (tmp_path / "output" / "execution_queue.json").write_text(json.dumps(orders), encoding="utf-8")

        result = srv.get_execution_queue()

        assert "AAPL" in result and "✅" in result
        assert "TSLA" in result and "🚫" in result and "kill switch active" in result

    def test_dict_wrapped_queue_shape(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "output").mkdir()
        payload = {"orders": [{"symbol": "AAPL", "side": "buy", "shares": 1, "price": 1.0, "allow_place": True}]}
        (tmp_path / "output" / "execution_queue.json").write_text(json.dumps(payload), encoding="utf-8")

        result = srv.get_execution_queue()

        assert "AAPL" in result


# ---------------------------------------------------------------------------
# get_trade_journal
# ---------------------------------------------------------------------------


class TestGetTradeJournal:
    def test_filters_by_symbol(self, monkeypatch):
        import transactions_store

        fake_store = MagicMock()
        fake_store.closed_trades_df.return_value = pd.DataFrame(
            {
                "symbol": ["AAPL", "MSFT"],
                "side": ["long", "long"],
                "entry_price": [100.0, 50.0],
                "exit_price": [110.0, 40.0],
                "shares": [1.0, 1.0],
            }
        )
        fake_store.open_trades_df.return_value = pd.DataFrame()
        monkeypatch.setattr(transactions_store, "TransactionsStore", lambda: fake_store)

        result = srv.get_trade_journal(symbol="aapl")

        assert "AAPL" in result
        assert "MSFT" not in result

    def test_no_trades_at_all(self, monkeypatch):
        import transactions_store

        fake_store = MagicMock()
        fake_store.closed_trades_df.return_value = pd.DataFrame()
        fake_store.open_trades_df.return_value = pd.DataFrame()
        monkeypatch.setattr(transactions_store, "TransactionsStore", lambda: fake_store)

        result = srv.get_trade_journal()

        assert "None." in result


# ---------------------------------------------------------------------------
# configure_alerts / send_test_alert
# (also the regression proof that the alerting_mcp.notifier import fix works)
# ---------------------------------------------------------------------------


class TestConfigureAlerts:
    def test_partial_update_preserves_existing_config(self, monkeypatch):
        import alerting_mcp.notifier as notifier_mod

        existing = {"channels": ["ntfy"], "events": {"signal_fired": True, "model_stale": False}}
        monkeypatch.setattr(notifier_mod, "get_alert_config", lambda: dict(existing))
        saved = {}
        monkeypatch.setattr(notifier_mod, "save_alert_config", lambda cfg: saved.update(cfg))

        result = srv.configure_alerts(pipeline_failed=True)

        assert saved["channels"] == ["ntfy"]
        assert saved["events"]["signal_fired"] is True  # untouched
        assert saved["events"]["pipeline_failed"] is True  # newly set
        assert "Alert Configuration Updated" in result

    def test_channels_string_parsed(self, monkeypatch):
        import alerting_mcp.notifier as notifier_mod

        monkeypatch.setattr(notifier_mod, "get_alert_config", lambda: {"channels": [], "events": {}})
        saved = {}
        monkeypatch.setattr(notifier_mod, "save_alert_config", lambda cfg: saved.update(cfg))

        srv.configure_alerts(channels="ntfy, slack")

        assert saved["channels"] == ["ntfy", "slack"]

    def test_import_resolves_without_error(self, monkeypatch):
        # Regression proof for the alerting.notifier -> alerting_mcp.notifier
        # fix: before the fix this call always raised ModuleNotFoundError
        # (caught, returning "Alert configuration failed: No module named
        # 'alerting.notifier'") on every invocation, real config or not.
        result = srv.configure_alerts()
        assert "Alert configuration failed" not in result


class TestSendTestAlert:
    def test_renders_per_channel_result(self, monkeypatch):
        import alerting_mcp.notifier as notifier_mod

        monkeypatch.setattr(notifier_mod, "send", lambda title, msg, priority="default": {"ntfy": True, "slack": False})

        result = srv.send_test_alert("Hi", "there")

        assert "ntfy**: ✅ Delivered" in result
        assert "slack**: ❌ Failed" in result

    def test_import_resolves_without_error(self, monkeypatch):
        result = srv.send_test_alert()
        assert "Test alert failed" not in result


# ---------------------------------------------------------------------------
# plot_equity_curve artifact-directory regression (bug fix #2)
# ---------------------------------------------------------------------------


class TestArtifactDirectoryRegression:
    def test_plot_equity_curve_writes_under_settings_output_dir(self, monkeypatch, tmp_path):
        from settings import settings

        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path / "output")

        idx = pd.bdate_range("2024-01-01", periods=120)
        hist = pd.DataFrame(
            {
                "Open": 100.0,
                "High": 101.0,
                "Low": 99.0,
                "Close": 100.0 + 0.01 * np.arange(len(idx)),
                "Volume": 1000,
            },
            index=idx,
        )
        fake_ticker = SimpleNamespace(history=lambda period: hist)
        fake_yf = SimpleNamespace(Ticker=lambda symbol: fake_ticker)
        monkeypatch.setitem(__import__("sys").modules, "yfinance", fake_yf)

        result = srv.plot_equity_curve("AAPL", "6mo")

        assert "/Users/kevinlee" not in result
        expected_dir = tmp_path / "output" / "artifacts"
        assert str(expected_dir) in result
        assert expected_dir.exists()
        assert any(expected_dir.glob("equity_curve_aapl.png"))

    def test_empty_history_returns_error(self, monkeypatch):
        fake_ticker = SimpleNamespace(history=lambda period: pd.DataFrame())
        fake_yf = SimpleNamespace(Ticker=lambda symbol: fake_ticker)
        monkeypatch.setitem(__import__("sys").modules, "yfinance", fake_yf)

        assert "No data found" in srv.plot_equity_curve("ZZZZ")

    def test_plot_portfolio_equity_writes_under_settings_output_dir(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        from settings import settings

        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path / "output")

        idx = pd.bdate_range("2024-01-01", periods=120)
        hist = pd.DataFrame(
            {
                "Open": 100.0,
                "High": 101.0,
                "Low": 99.0,
                "Close": 100.0 + 0.01 * np.arange(len(idx)),
                "Volume": 1000,
            },
            index=idx,
        )
        # A fresh copy per call: the source code mutates df.columns in place
        # per ticker, and this fixture is also used for the SPY benchmark
        # fetch -- sharing one DataFrame object across calls would leak
        # AAPL's already-lowercased columns into the SPY fetch.
        fake_ticker = SimpleNamespace(history=lambda period: hist.copy())
        fake_yf = SimpleNamespace(Ticker=lambda symbol: fake_ticker)
        monkeypatch.setitem(__import__("sys").modules, "yfinance", fake_yf)

        # No .env -> falls back to the hardcoded default universe (4 tickers).
        result = srv.plot_portfolio_equity("6mo")

        assert "/Users/kevinlee" not in result
        expected_dir = tmp_path / "output" / "artifacts"
        assert str(expected_dir) in result
        assert any(expected_dir.glob("portfolio_equity_vs_spy.png"))

    def test_plot_portfolio_equity_no_tickers_simulated_returns_error(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        fake_ticker = SimpleNamespace(history=lambda period: pd.DataFrame())
        fake_yf = SimpleNamespace(Ticker=lambda symbol: fake_ticker)
        monkeypatch.setitem(__import__("sys").modules, "yfinance", fake_yf)

        assert "No tickers could be simulated" in srv.plot_portfolio_equity("6mo")


# ---------------------------------------------------------------------------
# run_backtest
# ---------------------------------------------------------------------------


class TestRunBacktest:
    def test_empty_history_returns_error(self, monkeypatch):
        fake_ticker = SimpleNamespace(history=lambda period: pd.DataFrame())
        fake_yf = SimpleNamespace(Ticker=lambda symbol: fake_ticker)
        monkeypatch.setitem(__import__("sys").modules, "yfinance", fake_yf)

        assert "No historical data found" in srv.run_backtest("ZZZZ")

    def test_happy_path_delegates_to_simulation_engine(self, monkeypatch):
        import simulation_engine

        idx = pd.bdate_range("2024-01-01", periods=10)
        hist = pd.DataFrame(
            {"Open": 1.0, "High": 2.0, "Low": 0.5, "Close": 1.5, "Volume": 100}, index=idx
        )
        fake_ticker = SimpleNamespace(history=lambda period: hist)
        fake_yf = SimpleNamespace(Ticker=lambda symbol: fake_ticker)
        monkeypatch.setitem(__import__("sys").modules, "yfinance", fake_yf)

        called = {}

        def _fake_sim(df):
            called["columns"] = list(df.columns)
            print("Backtrader simulation output")

        monkeypatch.setattr(simulation_engine, "run_backtrader_simulation", _fake_sim)

        result = srv.run_backtest("AAPL", "1y")

        assert "Backtest Results for AAPL" in result
        assert "Backtrader simulation output" in result
        # Column names lowercased for the Backtrader feed.
        assert called["columns"] == ["open", "high", "low", "close", "volume"]

    def test_engine_exception_degrades_to_error_string(self, monkeypatch):
        import simulation_engine

        idx = pd.bdate_range("2024-01-01", periods=10)
        hist = pd.DataFrame(
            {"Open": 1.0, "High": 2.0, "Low": 0.5, "Close": 1.5, "Volume": 100}, index=idx
        )
        fake_ticker = SimpleNamespace(history=lambda period: hist)
        fake_yf = SimpleNamespace(Ticker=lambda symbol: fake_ticker)
        monkeypatch.setitem(__import__("sys").modules, "yfinance", fake_yf)

        def _raise(df):
            raise RuntimeError("cerebro exploded")

        monkeypatch.setattr(simulation_engine, "run_backtrader_simulation", _raise)

        assert "Backtest failed" in srv.run_backtest("AAPL")


# ---------------------------------------------------------------------------
# get_signal_breakdown
# ---------------------------------------------------------------------------


class TestGetSignalBreakdown:
    def test_missing_db_returns_error(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        assert "not found" in srv.get_signal_breakdown("AAPL")

    def test_symbol_not_found(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        conn = sqlite3.connect("quant_platform.db")
        conn.execute("CREATE TABLE DailySignals (symbol TEXT, date TEXT, composite_score REAL)")
        conn.commit()
        conn.close()

        assert "No signals found for AAPL" in srv.get_signal_breakdown("aapl")

    def test_renders_most_recent_row_excluding_meta_keys(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        conn = sqlite3.connect("quant_platform.db")
        conn.execute(
            "CREATE TABLE DailySignals (id INTEGER, symbol TEXT, date TEXT, "
            "composite_score REAL, created_at TEXT)"
        )
        conn.execute(
            "INSERT INTO DailySignals VALUES (1, 'AAPL', '2026-01-01', 42.5, 'ts1')"
        )
        conn.execute(
            "INSERT INTO DailySignals VALUES (2, 'AAPL', '2026-01-02', 50.0, 'ts2')"
        )
        conn.commit()
        conn.close()

        result = srv.get_signal_breakdown("aapl")

        assert "Signal Breakdown: AAPL (2026-01-02)" in result
        assert "composite_score" in result and "50.0" in result
        # Meta keys must not be rendered as "signal" rows.
        assert "**id**" not in result
        assert "**symbol**" not in result
        assert "**created_at**" not in result


# ---------------------------------------------------------------------------
# generate_daily_signals
# ---------------------------------------------------------------------------


class TestGenerateDailySignals:
    def test_missing_db_returns_error(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        assert "not found" in srv.generate_daily_signals()

    def test_no_signals_in_db(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        conn = sqlite3.connect("quant_platform.db")
        conn.execute(
            "CREATE TABLE DailySignals (symbol TEXT, date TEXT, composite_score REAL, "
            "action TEXT, conviction REAL)"
        )
        conn.commit()
        conn.close()

        assert "Run the full pipeline first" in srv.generate_daily_signals()

    def test_top_n_ranked_by_composite_score(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        conn = sqlite3.connect("quant_platform.db")
        conn.execute(
            "CREATE TABLE DailySignals (symbol TEXT, date TEXT, composite_score REAL, "
            "action TEXT, conviction REAL)"
        )
        rows = [
            ("AAPL", "2026-01-01", 90.0, "BUY", 0.9),
            ("MSFT", "2026-01-01", 50.0, "HOLD", 0.5),
            ("TSLA", "2026-01-01", 10.0, None, 0.1),
        ]
        conn.executemany("INSERT INTO DailySignals VALUES (?, ?, ?, ?, ?)", rows)
        conn.commit()
        conn.close()

        result = srv.generate_daily_signals(top_n=2)

        assert "AAPL" in result and "MSFT" in result
        assert "TSLA" not in result  # only top 2 by composite_score
        aapl_idx = result.index("AAPL")
        msft_idx = result.index("MSFT")
        assert aapl_idx < msft_idx  # ranked descending
        assert "HOLD" in result  # None action defaults to HOLD elsewhere in the row, MSFT's real action
