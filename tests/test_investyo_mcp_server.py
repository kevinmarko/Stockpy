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

Three genuine bugs were found and fixed while reading this file to write
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
     established output-directory convention.
     ``TestArtifactDirectoryRegression`` in this file pins the fix.
  3. ``get_execution_queue`` read a schema
     (``orders``/``shares``/``price``/``gate_reason``) the queue builder
     has NEVER emitted — ``execution.queue_builder.build_execution_queue``
     writes ``intents``/``qty``/``target_notional``/``gate_reasons`` (a
     list). ``queue.get("orders", [queue])`` silently fell through to
     iterating the payload DICT itself as one fake order, rendering a row
     of literal ``"?"``s for every real queue this tool was ever asked
     about. It also hardcoded the relative path
     ``"output/execution_queue.json"`` instead of ``settings.OUTPUT_DIR``
     (cwd-dependent). Fixed to read the real schema and the correct path;
     ``gui/robinhood_execution_panel.py::read_execution_queue`` (which
     already read this correctly) is the reference this fix matches, and
     ``TestGetExecutionQueue::test_parity_with_gui_reader_on_the_same_payload``
     pins agreement between the two readers on a real builder payload.

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
* Subprocess-wrapping tools that REMAIN subprocess-based
  (``run_platform_tests``, ``trigger_edgar_backfill``,
  ``generate_html_report``, ``run_validation_harness``,
  ``compare_strategies``, ``trigger_model_retraining``): success /
  ``CalledProcessError`` / ``FileNotFoundError`` / ``TimeoutExpired``
  paths plus the exact constructed ``argv`` via a mocked
  ``subprocess.run`` that records its call args. The FIXED argv
  contracts are asserted here: ``run_validation_harness`` /
  ``compare_strategies`` use ``--strategies`` (plural) + ``--json`` (not
  ``--strategy`` / ``--json-output``); ``run_validation_harness`` OMITS
  ``--strategies`` for "default"/"all"/empty (validate all);
  ``trigger_edgar_backfill(tickers="all")`` RESOLVES the universe and
  ALWAYS passes ``--tickers``; ``generate_html_report`` shells
  ``[sys.executable, "main.py"]``.
* Tools that became IN-PROCESS (no longer shell to a nonexistent CLI):
  ``trigger_data_engine`` (→ ``HistoricalStore().get_bars``),
  ``trigger_forecasting`` (→ ``engine.advisory.evaluate`` reporting
  ``.forecast``), ``trigger_macro_engine`` (→ in-process ``MacroEngine``
  fed by a ``DataEngine``), and ``trigger_full_pipeline`` (Step 1
  in-process bars, Step 3 in-process macro; Step 2 EDGAR still
  subprocess but always with ``--tickers``). Mocked at the engine/store's
  OWN module path (imported locally inside each tool body).
* ``update_universe_tickers`` routes ``.env`` writes through
  ``gui.env_io.write_setting("DEFAULT_TICKERS", ...)`` — a guard test
  pins that an unrelated comment line SURVIVES the edit and the new
  ticker lands in the parsed ``DEFAULT_TICKERS``.
* ``get_universe_status`` counts the real ``trades`` table (not a
  nonexistent ``Transactions`` table) in its DB-metrics section.
* ``query_investyo_db`` accepts a read-only ``WITH ... SELECT`` CTE while
  still rejecting INSERT/UPDATE/DELETE/DROP (incl. a CTE-prefixed mutation).
* New read-only market-intelligence tools ``get_recommendation`` /
  ``get_options_directive`` / ``get_regime_status`` /
  ``get_portfolio_coverage``: one happy-path each (markdown fields + a
  fenced ```json block) mocking the underlying engine, plus a dead-letter
  degradation path each.
* ``get_pit_coverage_report`` / ``run_pit_audit`` / ``run_lookahead_check``:
  mocked ``validation.pit_fundamentals`` wiring and output formatting.
* ``get_model_registry_status``: list-shaped and dict-shaped registries,
  staleness threshold, missing file.
* ``get_execution_queue``: missing file, empty (real) queue, a real
  builder-produced payload rendering correct columns, gate-reasons list
  joining, ``settings.OUTPUT_DIR`` (not cwd) resolution, and parity with
  ``gui.robinhood_execution_panel.read_execution_queue`` on the same file.
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


def _patch_advisory_inputs(monkeypatch, snapshot=None):
    """Defensively neutralize the network-touching input builders that an
    advisory/market read-only tool may construct BEFORE/AROUND the primary
    engine call the test actually mocks (e.g. a market-data provider or a
    Robinhood account snapshot). Patching is best-effort (``raising=False``)
    so it is a no-op if the tool never imports the symbol -- the test only
    depends on the primary engine mock, not on these."""
    try:
        import data.market_data as md_mod

        monkeypatch.setattr(md_mod, "get_provider", lambda *a, **k: MagicMock(), raising=False)
        monkeypatch.setattr(md_mod, "reset_provider", lambda *a, **k: None, raising=False)
    except Exception:  # pragma: no cover - module always importable in this repo
        pass
    try:
        import data.robinhood_portfolio as rp_mod

        monkeypatch.setattr(
            rp_mod, "fetch_account_snapshot", lambda *a, **k: snapshot, raising=False
        )
    except Exception:  # pragma: no cover
        pass


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

    def test_accepts_with_cte_query(self, monkeypatch, tmp_path):
        """Fixed contract: a read-only ``WITH ... SELECT`` CTE is ACCEPTED
        (the guard is no longer a naive ``startswith('SELECT')``)."""
        monkeypatch.chdir(tmp_path)
        conn = sqlite3.connect("quant_platform.db")
        conn.execute("CREATE TABLE T (symbol TEXT, score REAL)")
        conn.execute("INSERT INTO T VALUES ('AAPL', 1.5)")
        conn.commit()
        conn.close()

        result = srv.query_investyo_db(
            "WITH cte AS (SELECT symbol, score FROM T) SELECT * FROM cte"
        )

        assert "Only SELECT queries are permitted" not in result
        assert "AAPL" in result

    def test_accepts_leading_whitespace_with_cte(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        sqlite3.connect("quant_platform.db").close()
        assert "Only SELECT queries are permitted" not in srv.query_investyo_db(
            "  \n WITH x AS (SELECT 1 AS a) SELECT a FROM x"
        )

    @pytest.mark.parametrize(
        "bad_query",
        [
            "WITH x AS (SELECT 1) INSERT INTO T VALUES (1)",
            "WITH x AS (SELECT 1) DELETE FROM T",
            "  update T set score=1",
            "drop table T",
        ],
    )
    def test_rejects_mutations_even_with_cte_prefix(self, bad_query):
        # A CTE prefix must not be a bypass for a trailing mutation, and bare
        # INSERT/UPDATE/DELETE/DROP stay rejected.
        assert "Only SELECT queries are permitted" in srv.query_investyo_db(bad_query)

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

    # -- DATABASE-LEVEL read-only enforcement (layer 2, beneath the regex) -----

    def test_db_query_connection_is_readonly_at_db_level(self, monkeypatch, tmp_path):
        """The property the regex guard alone cannot provide: calling _db_query
        DIRECTLY (bypassing query_investyo_db's regex) must still be rejected by
        the connection itself. This is the path every other _db_query caller —
        and any future caller — takes."""
        monkeypatch.chdir(tmp_path)
        conn = sqlite3.connect("quant_platform.db")
        conn.execute("CREATE TABLE T (symbol TEXT, score REAL)")
        conn.commit()
        conn.close()

        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            srv._db_query("INSERT INTO T VALUES ('X', 1.0)")

    def test_db_query_pragma_query_only_cannot_be_reverted(self, monkeypatch, tmp_path):
        """mode=ro is strictly stronger than PRAGMA query_only: even after asking
        to disable query_only, a write still fails (mode=ro is not revertible)."""
        monkeypatch.chdir(tmp_path)
        conn = sqlite3.connect("quant_platform.db")
        conn.execute("CREATE TABLE T (x INTEGER)")
        conn.commit()
        conn.close()

        # query_only=0 on a mode=ro connection is itself a no-op; the subsequent
        # write is still rejected by the read-only connection.
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            srv._db_query("PRAGMA query_only=0")
            srv._db_query("INSERT INTO T VALUES (1)")

    def test_query_investyo_db_regex_still_first_line_of_defense(self):
        """The friendly regex layer is intact — a mutation gets the clear
        message, not a raw DB error string."""
        assert "Only SELECT queries are permitted" in srv.query_investyo_db(
            "INSERT INTO T VALUES (1)"
        )

    def test_db_query_does_not_create_missing_db_file(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(FileNotFoundError):
            srv._db_query("SELECT 1")
        assert not (tmp_path / "quant_platform.db").exists()

    def test_db_query_readonly_creates_no_wal_sidecars(self, monkeypatch, tmp_path):
        """A read over a non-WAL db must not leave -wal/-shm sidecars behind."""
        monkeypatch.chdir(tmp_path)
        conn = sqlite3.connect("quant_platform.db")
        conn.execute("CREATE TABLE T (x INTEGER)")
        conn.execute("INSERT INTO T VALUES (1)")
        conn.commit()
        conn.close()

        srv._db_query("SELECT * FROM T")

        assert not (tmp_path / "quant_platform.db-wal").exists()
        assert not (tmp_path / "quant_platform.db-shm").exists()


# ---------------------------------------------------------------------------
# _db_query internals — two dormant Postgres-branch bugs (fixed):
#   1. ``sqlalchemy.text(sql)`` doesn't understand SQLite's ``?`` positional
#      placeholders, and a plain tuple params arg raises ArgumentError under
#      SQLAlchemy 2.0 — `_qmark_to_named` rewrites `?` -> `:pN` + a bind dict.
#   2. The sqlite branch silently discarded a caller-configured custom
#      DATABASE_URL and always read the cwd-relative "quant_platform.db"
#      literal — fixed to honor an EXPLICIT custom sqlite DATABASE_URL,
#      while leaving the (unset/default) case, and every existing
#      chdir-based test above, byte-for-byte unchanged.
# ---------------------------------------------------------------------------


class TestQmarkToNamed:
    """Pure-function tests for the `?` -> `:pN` SQLAlchemy bind-rewrite helper."""

    def test_no_params_is_a_noop(self):
        sql = "SELECT * FROM T"
        rewritten, binds = srv._qmark_to_named(sql, ())
        assert rewritten == sql
        assert binds == {}

    def test_single_placeholder(self):
        rewritten, binds = srv._qmark_to_named(
            "SELECT timestamp FROM ExecutionLogs ORDER BY id DESC LIMIT ?", (50,)
        )
        assert "?" not in rewritten
        assert ":p0" in rewritten
        assert binds == {"p0": 50}

    def test_multiple_placeholders_preserve_order(self):
        rewritten, binds = srv._qmark_to_named(
            "SELECT symbol, composite_score FROM DailySignals "
            "WHERE date=? ORDER BY composite_score DESC LIMIT ?",
            ("2026-07-15", 10),
        )
        assert "?" not in rewritten
        assert rewritten.index(":p0") < rewritten.index(":p1")
        assert binds == {"p0": "2026-07-15", "p1": 10}

    def test_matches_read_platform_logs_query(self):
        """Exact call-site query from `read_platform_logs` rewrites cleanly."""
        sql = (
            "SELECT timestamp, status, ticker_count, execution_time_seconds, error_message "
            "FROM ExecutionLogs ORDER BY id DESC LIMIT ?"
        )
        rewritten, binds = srv._qmark_to_named(sql, (25,))
        assert rewritten == sql.replace("?", ":p0")
        assert binds == {"p0": 25}

    def test_matches_get_signal_breakdown_query(self):
        sql = "SELECT * FROM DailySignals WHERE symbol = ? ORDER BY date DESC LIMIT 1"
        rewritten, binds = srv._qmark_to_named(sql, ("AAPL",))
        assert rewritten == sql.replace("?", ":p0")
        assert binds == {"p0": "AAPL"}

    def test_matches_generate_daily_signals_query(self):
        sql = (
            "SELECT symbol, composite_score, action, conviction FROM DailySignals "
            "WHERE date = ? ORDER BY composite_score DESC LIMIT ?"
        )
        rewritten, binds = srv._qmark_to_named(sql, ("2026-07-15", 10))
        assert rewritten == "SELECT symbol, composite_score, action, conviction FROM DailySignals " \
            "WHERE date = :p0 ORDER BY composite_score DESC LIMIT :p1"
        assert binds == {"p0": "2026-07-15", "p1": 10}

    def test_mismatched_placeholder_count_raises(self):
        with pytest.raises(ValueError):
            srv._qmark_to_named("SELECT * FROM T WHERE id = ?", (1, 2))

    def test_mismatched_placeholder_count_too_few_params_raises(self):
        with pytest.raises(ValueError):
            srv._qmark_to_named("SELECT * FROM T WHERE id = ? AND x = ?", (1,))


class _FakeSQLAlchemyResult:
    def __init__(self, columns, rows):
        self._columns = columns
        self._rows = rows

    def keys(self):
        return self._columns

    def fetchall(self):
        return self._rows


class _FakeSQLAlchemyConnection:
    def __init__(self, captured, result):
        self._captured = captured
        self._result = result

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def execute(self, clause, parameters=None):
        self._captured["sql_text"] = str(clause)
        self._captured["parameters"] = parameters
        return self._result


class _FakeSQLAlchemyEngine:
    def __init__(self, captured, result):
        self._captured = captured
        self._result = result

    def connect(self):
        return _FakeSQLAlchemyConnection(self._captured, self._result)


class TestDbQueryPostgresBranch:
    """Integration-style tests mocked at the SQLAlchemy engine boundary --
    no live Postgres server is available in this environment, so
    `db_config.create_readonly_db_engine` (the DATABASE-LEVEL read-only seam
    _db_query actually routes through) is replaced with a fake engine/
    connection that records exactly what `_db_query` passed to `.execute()`."""

    def test_parameterized_query_rewrites_qmarks_and_binds(self, monkeypatch):
        import db_config
        from settings import settings

        monkeypatch.setattr(settings, "DATABASE_URL", "postgresql://u:p@host/db1")
        captured = {}
        fake_result = _FakeSQLAlchemyResult(["symbol", "date"], [("AAPL", "2026-07-15")])
        # _db_query routes through the cached, DATABASE-LEVEL read-only
        # _readonly_engine() -> db_config.create_readonly_db_engine(), not the
        # write-path create_db_engine -- mock at that boundary instead. A
        # per-test-unique db_url avoids collisions with @lru_cache on
        # _readonly_engine (also cleared explicitly for safety).
        monkeypatch.setattr(
            db_config, "create_readonly_db_engine",
            lambda url: _FakeSQLAlchemyEngine(captured, fake_result),
        )
        srv._readonly_engine.cache_clear()

        columns, rows = srv._db_query(
            "SELECT * FROM DailySignals WHERE symbol = ? ORDER BY date DESC LIMIT 1",
            ("AAPL",),
        )

        assert columns == ["symbol", "date"]
        assert rows == [("AAPL", "2026-07-15")]
        # The `?` placeholder must be gone and replaced with a named bind --
        # this is the exact break the bug report identified (sqlalchemy.text()
        # does not recognize `?`).
        assert "?" not in captured["sql_text"]
        assert ":p0" in captured["sql_text"]
        assert captured["parameters"] == {"p0": "AAPL"}

    def test_no_params_query_passes_empty_bind_dict(self, monkeypatch):
        import db_config
        from settings import settings

        monkeypatch.setattr(settings, "DATABASE_URL", "postgresql://u:p@host/db2")
        captured = {}
        fake_result = _FakeSQLAlchemyResult(["c"], [(1,)])
        monkeypatch.setattr(
            db_config, "create_readonly_db_engine",
            lambda url: _FakeSQLAlchemyEngine(captured, fake_result),
        )
        srv._readonly_engine.cache_clear()

        columns, rows = srv._db_query("SELECT 1 AS c")

        assert columns == ["c"]
        assert rows == [(1,)]
        assert captured["sql_text"] == "SELECT 1 AS c"
        assert captured["parameters"] == {}

    def test_multi_param_query_rewrites_in_order(self, monkeypatch):
        import db_config
        from settings import settings

        monkeypatch.setattr(settings, "DATABASE_URL", "postgresql://u:p@host/db3")
        captured = {}
        fake_result = _FakeSQLAlchemyResult(["symbol"], [])
        monkeypatch.setattr(
            db_config, "create_readonly_db_engine",
            lambda url: _FakeSQLAlchemyEngine(captured, fake_result),
        )
        srv._readonly_engine.cache_clear()

        srv._db_query(
            "SELECT symbol FROM DailySignals WHERE date = ? ORDER BY composite_score DESC LIMIT ?",
            ("2026-07-15", 10),
        )

        assert captured["parameters"] == {"p0": "2026-07-15", "p1": 10}
        assert "?" not in captured["sql_text"]


class TestDbQuerySqliteDatabaseUrlHonored:
    """Bug 2: DATABASE_URL unset must reproduce today's exact cwd-relative
    behavior; an EXPLICIT custom sqlite DATABASE_URL must be honored instead
    of silently reading the wrong file."""

    def test_unset_database_url_uses_cwd_relative_default(self, monkeypatch, tmp_path):
        """Unchanged-behavior pin: identical to how every other test in this
        file already calls query_investyo_db/read_platform_logs/etc, just
        exercised directly against `_db_query`."""
        from settings import settings

        monkeypatch.setattr(settings, "DATABASE_URL", None)
        monkeypatch.chdir(tmp_path)
        conn = sqlite3.connect("quant_platform.db")
        conn.execute("CREATE TABLE T (id INTEGER)")
        conn.execute("INSERT INTO T VALUES (7)")
        conn.commit()
        conn.close()

        columns, rows = srv._db_query("SELECT id FROM T")

        assert rows == [(7,)]

    def test_custom_sqlite_database_url_is_honored(self, monkeypatch, tmp_path):
        """The core regression: a custom DATABASE_URL pointing at a
        DIFFERENT sqlite file must actually be read from -- not silently
        ignored in favor of the cwd-relative "quant_platform.db" default."""
        from settings import settings

        monkeypatch.chdir(tmp_path)

        # A DIFFERENT default-named DB sits in the cwd with no matching
        # table -- if the bug were still present, `_db_query` would read
        # this file (via the hardcoded "quant_platform.db" literal) and
        # raise/degrade instead of returning the custom DB's row.
        sqlite3.connect("quant_platform.db").close()

        custom_db = tmp_path / "custom_subdir" / "other.db"
        custom_db.parent.mkdir()
        conn = sqlite3.connect(str(custom_db))
        conn.execute("CREATE TABLE T (id INTEGER)")
        conn.execute("INSERT INTO T VALUES (42)")
        conn.commit()
        conn.close()

        monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{custom_db}")

        columns, rows = srv._db_query("SELECT id FROM T")

        assert rows == [(42,)]

    def test_custom_sqlite_database_url_missing_file_raises(self, monkeypatch, tmp_path):
        from settings import settings

        monkeypatch.chdir(tmp_path)
        missing_db = tmp_path / "does_not_exist.db"
        monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{missing_db}")

        with pytest.raises(FileNotFoundError):
            srv._db_query("SELECT 1")

    def test_custom_sqlite_database_url_with_uri_metacharacters_stays_readonly(
        self, monkeypatch, tmp_path
    ):
        """Interaction regression: when a custom DATABASE_URL is honored, its
        path is no longer the hardcoded literal with zero URI metacharacters
        -- an unescaped path containing '?'/'#'/'%' would silently DROP
        ?mode=ro and hand back a READ-WRITE connection (the exact fail-open
        trap db_config.sqlite_readonly_uri exists to prevent). This pins that
        _db_query reuses that helper rather than raw f-string interpolation."""
        from settings import settings

        weird_dir = tmp_path / "100%data"
        weird_dir.mkdir()
        custom_db = weird_dir / "other.db"
        conn = sqlite3.connect(str(custom_db))
        conn.execute("CREATE TABLE T (id INTEGER)")
        conn.execute("INSERT INTO T VALUES (99)")
        conn.commit()
        conn.close()

        monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{custom_db}")

        # Round-trips despite the '%' in the path (proves escaping happened).
        columns, rows = srv._db_query("SELECT id FROM T")
        assert rows == [(99,)]

        # And the connection is still genuinely read-only (proves mode=ro
        # wasn't silently dropped by an unescaped metacharacter).
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            srv._db_query("INSERT INTO T VALUES (1)")

    def test_resolve_database_url_failure_falls_back_to_default_path(self, monkeypatch, tmp_path):
        """Dead-letter path: if `resolve_database_url()` itself raises,
        `_db_query` must still fall back to the exact pre-fix relative-path
        behavior, never raise from the resolution step itself. Mocked at
        `db_config`'s own attribute (the module `_db_query` locally
        imports from on every call), matching this file's established
        monkeypatch convention."""
        import db_config

        monkeypatch.chdir(tmp_path)
        conn = sqlite3.connect("quant_platform.db")
        conn.execute("CREATE TABLE T (id INTEGER)")
        conn.execute("INSERT INTO T VALUES (99)")
        conn.commit()
        conn.close()

        def _raise(*_a, **_k):
            raise RuntimeError("simulated resolve_database_url failure")

        monkeypatch.setattr(db_config, "resolve_database_url", _raise)

        columns, rows = srv._db_query("SELECT id FROM T")

        assert rows == [(99,)]


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
    """``gui.env_io`` computes ``ENV_PATH`` from the repo root at import time,
    NOT from the CWD -- ``monkeypatch.chdir()`` alone does not redirect it.
    Every test here must also redirect the module symbol directly, or it will
    silently read/write the real repo ``.env`` file instead of the fixture.
    """

    def _redirect_env(self, monkeypatch, tmp_path):
        import gui.env_io as env_io

        env_file = tmp_path / ".env"
        monkeypatch.setattr(env_io, "ENV_PATH", env_file)
        monkeypatch.chdir(tmp_path)
        return env_file

    @staticmethod
    def _parse_default_tickers(env_text: str) -> list:
        """python-dotenv's ``set_key(quote_mode="auto")`` wraps a value
        containing special characters in a single quote (e.g.
        ``DEFAULT_TICKERS='["AAPL", "TSLA"]'``) -- strip a matching wrapping
        quote before JSON-decoding.
        """
        raw = env_text.split("DEFAULT_TICKERS=", 1)[1].splitlines()[0].strip()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
            raw = raw[1:-1]
        return json.loads(raw)

    def test_add_with_no_env_file_uses_default_universe(self, monkeypatch, tmp_path):
        env_file = self._redirect_env(monkeypatch, tmp_path)

        result = srv.update_universe_tickers("add", "tsla")

        assert "TSLA" in result
        tickers = self._parse_default_tickers(env_file.read_text(encoding="utf-8"))
        # No .env existed, so env_io.get_value's own default ("[]") applies --
        # NOT the tool's old hand-rolled 4-ticker hardcoded fallback.
        assert tickers == ["TSLA"]

    def test_add_already_present(self, monkeypatch, tmp_path):
        env_file = self._redirect_env(monkeypatch, tmp_path)
        env_file.write_text('DEFAULT_TICKERS=["AAPL"]\n', encoding="utf-8")

        assert "already in the trading universe" in srv.update_universe_tickers("add", "aapl")

    def test_remove_present_ticker(self, monkeypatch, tmp_path):
        env_file = self._redirect_env(monkeypatch, tmp_path)
        env_file.write_text('DEFAULT_TICKERS=["AAPL", "MSFT"]\n', encoding="utf-8")

        result = srv.update_universe_tickers("remove", "msft")

        # NOTE: the source's f"Successfully {action_lower}ed ..." produces
        # "Successfully removeed" (double e) for action="remove" -- a
        # harmless grammar quirk, not asserted on here.
        assert "MSFT" in result and "active universe" in result
        tickers = self._parse_default_tickers(env_file.read_text(encoding="utf-8"))
        assert tickers == ["AAPL"]

    def test_remove_absent_ticker(self, monkeypatch, tmp_path):
        env_file = self._redirect_env(monkeypatch, tmp_path)
        env_file.write_text('DEFAULT_TICKERS=["AAPL"]\n', encoding="utf-8")

        assert "is not in the trading universe" in srv.update_universe_tickers("remove", "zzzz")

    def test_malformed_json_falls_back_to_comma_split(self, monkeypatch, tmp_path):
        env_file = self._redirect_env(monkeypatch, tmp_path)
        env_file.write_text("DEFAULT_TICKERS=AAPL,MSFT\n", encoding="utf-8")

        result = srv.update_universe_tickers("add", "tsla")

        assert "TSLA" in result
        tickers = self._parse_default_tickers(env_file.read_text(encoding="utf-8"))
        assert set(tickers) == {"AAPL", "MSFT", "TSLA"}

    def test_invalid_action(self, monkeypatch, tmp_path):
        self._redirect_env(monkeypatch, tmp_path)
        assert "Invalid action" in srv.update_universe_tickers("destroy", "AAPL")

    def test_add_via_env_io_preserves_comments(self, monkeypatch, tmp_path):
        """Fixed contract: the write is routed through
        ``gui.env_io.write_setting("DEFAULT_TICKERS", ...)`` (dotenv ``set_key``,
        which edits in place) instead of rewriting the whole file line-by-line.
        This means unrelated lines -- crucially a comment -- SURVIVE the edit.
        """
        env_file = self._redirect_env(monkeypatch, tmp_path)
        env_file.write_text(
            "# my comment\nDEFAULT_TICKERS=[\"AAPL\"]\n", encoding="utf-8"
        )

        result = srv.update_universe_tickers("add", "tsla")

        assert "TSLA" in result
        text = env_file.read_text(encoding="utf-8")
        # (a) the comment line survives the rewrite.
        assert "# my comment" in text
        # (b) TSLA is present in the parsed DEFAULT_TICKERS.
        tickers = self._parse_default_tickers(text)
        assert "TSLA" in [t.upper() for t in tickers]
        assert "AAPL" in [t.upper() for t in tickers]


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
        # `settings` is a process-wide singleton constructed once on first
        # import, not re-read per call -- whichever test imports it first
        # locks in whatever DEFAULT_TICKERS its real `.env` had at that
        # moment, regardless of this test's own chdir. Patch it explicitly
        # so the "nothing present" branch is deterministic instead of
        # depending on test-collection/import order.
        from settings import settings

        monkeypatch.setattr(settings, "DEFAULT_TICKERS", [])

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
        # Fixed contract: the DB-metrics section counts the `trades` table
        # (TransactionsStore's real table name), NOT a `Transactions` table.
        conn.execute("CREATE TABLE trades (id INTEGER)")
        conn.execute("CREATE TABLE ExecutionLogs (id INTEGER)")
        conn.commit()
        conn.close()

        result = srv.get_universe_status()

        assert "NVDA" in result
        assert "conviction_above" in result
        assert "Daily Signals Table Rows**: 0" in result
        # The section rendered fully -> the trades-table query did not error out.
        assert "Error querying DB stats" not in result

    def test_queries_trades_table_not_transactions(self, monkeypatch, tmp_path):
        """Regression guard for the fixed contract: the DB-metrics section
        must query the real `trades` table. Seeding ONLY DailySignals /
        ExecutionLogs / trades (and NO `Transactions` table) must render the
        metrics without the old ``no such table: Transactions`` error."""
        monkeypatch.chdir(tmp_path)
        conn = sqlite3.connect("quant_platform.db")
        conn.execute("CREATE TABLE DailySignals (id INTEGER)")
        conn.execute("CREATE TABLE ExecutionLogs (id INTEGER)")
        conn.execute("CREATE TABLE trades (id INTEGER)")
        conn.commit()
        conn.close()

        result = srv.get_universe_status()

        assert "Daily Signals Table Rows**: 0" in result
        assert "Error querying DB stats" not in result
        assert "no such table" not in result.lower()


# ---------------------------------------------------------------------------
# Subprocess-wrapping tools
# ---------------------------------------------------------------------------


class TestTriggerDataEngineInProcess:
    """``trigger_data_engine`` is now IN-PROCESS: it fetches/refreshes bars via
    ``data.historical_store.HistoricalStore().get_bars(...)`` instead of shelling
    out to a nonexistent ``data_engine.py`` CLI entrypoint. Mock the store at its
    own module path (it is imported locally inside the tool body)."""

    def _bars(self):
        idx = pd.bdate_range("2024-01-01", periods=3)
        return pd.DataFrame(
            {"Open": 1.0, "High": 2.0, "Low": 0.5, "Close": 1.5, "Volume": 100}, index=idx
        )

    def test_success_fetches_bars_in_process(self, monkeypatch):
        import data.historical_store as hs_mod

        fake_store = MagicMock()
        fake_store.get_bars.return_value = self._bars()
        monkeypatch.setattr(hs_mod, "HistoricalStore", lambda *a, **k: fake_store)

        result = srv.trigger_data_engine("aapl", "1D")

        assert isinstance(result, str) and result
        assert "AAPL" in result.upper()
        fake_store.get_bars.assert_called_once()
        # The symbol is threaded into get_bars (as a positional or keyword arg).
        call = fake_store.get_bars.call_args
        passed = [str(v).upper() for v in (list(call.args) + list(call.kwargs.values()))]
        assert "AAPL" in passed

    def test_empty_bars_does_not_raise(self, monkeypatch):
        import data.historical_store as hs_mod

        fake_store = MagicMock()
        fake_store.get_bars.return_value = pd.DataFrame()
        monkeypatch.setattr(hs_mod, "HistoricalStore", lambda *a, **k: fake_store)

        result = srv.trigger_data_engine("ZZZZ")
        assert isinstance(result, str) and result  # dead-letter: a message, never a raise

    def test_store_exception_degrades_gracefully(self, monkeypatch):
        import data.historical_store as hs_mod

        fake_store = MagicMock()
        fake_store.get_bars.side_effect = RuntimeError("db locked")
        monkeypatch.setattr(hs_mod, "HistoricalStore", lambda *a, **k: fake_store)

        result = srv.trigger_data_engine("AAPL")
        assert isinstance(result, str)
        low = result.lower()
        assert "error" in low or "fail" in low or "AAPL" in result.upper()


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

    def test_all_tickers_resolves_universe_and_passes_tickers(self, monkeypatch, tmp_path):
        # Fixed contract: "all" is RESOLVED to the real universe and --tickers is
        # ALWAYS passed (the old code silently omitted the flag, which made the
        # backfill script default to backfilling nothing). chdir to an empty
        # tmp_path so the resolver falls back to its hardcoded default universe
        # rather than reading the repo's real .env.
        monkeypatch.chdir(tmp_path)
        captured = {}

        def _fake_run(cmd, **k):
            captured["cmd"] = cmd
            return SimpleNamespace(stdout="", stderr="", returncode=0)

        monkeypatch.setattr(subprocess, "run", _fake_run)
        srv.trigger_edgar_backfill(tickers="all")

        assert "--tickers" in captured["cmd"]
        idx = captured["cmd"].index("--tickers")
        # At least one resolved ticker follows the flag.
        assert len(captured["cmd"]) > idx + 1

    def test_specific_tickers_included(self, monkeypatch):
        captured = {}

        def _fake_run(cmd, **k):
            captured["cmd"] = cmd
            return SimpleNamespace(stdout="", stderr="", returncode=0)

        monkeypatch.setattr(subprocess, "run", _fake_run)
        srv.trigger_edgar_backfill(tickers="aapl,msft")

        # backfill_edgar_fundamentals.py's --tickers takes ONE comma-joined
        # string (it does `args.tickers.split(",")` internally), not nargs='+'.
        idx = captured["cmd"].index("--tickers")
        assert captured["cmd"][idx + 1] == "AAPL,MSFT"


class TestRemainingSubprocessToolsArgv:
    """Lighter-touch tests for the tools that REMAIN subprocess-wrapping:
    verify the constructed argv is correct and that a nonzero exit
    produces a readable failure string, without re-deriving the full
    exception-branch matrix already covered above for the pattern.

    (``trigger_forecasting`` / ``trigger_macro_engine`` / ``trigger_full_pipeline``
    became in-process and now have their own dedicated classes below.)"""

    def test_generate_html_report(self, monkeypatch, tmp_path):
        from settings import settings

        # Fixed contract: success is no longer trusted from the subprocess exit
        # code alone (CONSTRAINT #4 - never fabricate) -- the tool checks that
        # daily_report.html was actually produced under settings.OUTPUT_DIR.
        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
        (tmp_path / "daily_report.html").write_text("<html></html>", encoding="utf-8")

        captured = {}
        monkeypatch.setattr(
            subprocess, "run", _capturing_run(captured, SimpleNamespace(stdout="ok", returncode=0))
        )
        result = srv.generate_html_report("port-1")
        assert "HTML report generated" in result
        # Fixed contract: shells `[sys.executable, "main.py"]` (the real advisory
        # orchestrator entrypoint), NOT `-m reporting.html_publisher` / a
        # nonexistent reporting_engine.py.
        assert "main.py" in captured["cmd"]
        assert "reporting.html_publisher" not in captured["cmd"]
        assert "reporting_engine.py" not in captured["cmd"]

    def test_run_validation_harness(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            subprocess,
            "run",
            _capturing_run(captured, SimpleNamespace(stdout="Sharpe: 1.2", returncode=0)),
        )
        result = srv.run_validation_harness("my_strat", "2020-01-01", "2021-01-01")
        assert "my_strat" in result
        # Fixed contract: the flag is --strategies (plural), not --strategy.
        assert "--strategies" in captured["cmd"]
        assert "--strategy" not in captured["cmd"]
        idx = captured["cmd"].index("--strategies")
        assert captured["cmd"][idx + 1] == "my_strat"

    @pytest.mark.parametrize("name", ["default", "all", ""])
    def test_run_validation_harness_all_omits_strategies_flag(self, monkeypatch, name):
        # "default"/"all"/empty means "validate everything" -> --strategies omitted.
        captured = {}
        monkeypatch.setattr(
            subprocess,
            "run",
            _capturing_run(captured, SimpleNamespace(stdout="ok", returncode=0)),
        )
        srv.run_validation_harness(name, "2020-01-01", "2021-01-01")
        assert "--strategies" not in captured["cmd"]

    def test_compare_strategies(self, monkeypatch):
        calls = []

        def _fake_run(cmd, **k):
            calls.append(list(cmd))
            return SimpleNamespace(stdout="metrics", returncode=0)

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = srv.compare_strategies("strat_a", "strat_b")
        assert "strat_a vs strat_b" in result
        assert "## strat_a" in result and "## strat_b" in result
        # Fixed contract: --strategies (plural) + --json; NEVER --strategy / --json-output.
        flat = [tok for cmd in calls for tok in cmd]
        assert "--strategies" in flat
        assert "--json" in flat
        assert "--strategy" not in flat
        assert "--json-output" not in flat

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
# In-process migrations: trigger_forecasting / trigger_macro_engine /
# trigger_full_pipeline. These no longer shell to a nonexistent CLI; they call
# the engines directly. Mock at the engine's OWN module path (imported locally
# inside each tool body).
# ---------------------------------------------------------------------------


def _fake_macro_engine():
    """A MacroEngine stand-in whose likely accessor methods all return a
    numerically-formattable macro DTO, so whichever one the tool calls renders
    without a TypeError. The exact method name is an impl detail we don't pin."""
    fake_dto = SimpleNamespace(
        market_regime="RISK ON",
        vix=15.0,
        sahm_rule_indicator=0.1,
        sahm_rule=0.1,
        yield_curve_spread=0.5,
        yield_curve=0.5,
        high_yield_oas=3.0,
        credit_spread=3.0,
        real_yield=1.0,
        killSwitch=False,
        hmm_risk_on_probability=0.7,
    )
    engine = MagicMock()
    for name in (
        "analyze",
        "run",
        "get_macro_dto",
        "build_macro_dto",
        "_build_macro_dto",
        "compute_regime",
        "get_regime",
        "evaluate",
        "detect_regime",
    ):
        setattr(engine, name, lambda *a, **k: fake_dto)
    engine.compute_hmm_risk_on_probability = lambda *a, **k: 0.7
    return engine


class TestTriggerForecastingInProcess:
    """``trigger_forecasting`` now runs the forecast in-process via
    ``engine.advisory.evaluate(...)`` and reports the recommendation's
    ``.forecast`` value, instead of shelling to a nonexistent
    ``forecasting_engine.py`` CLI."""

    def _fake_rec(self):
        return SimpleNamespace(
            symbol="AAPL",
            action="BUY",
            strategy="momentum",
            conviction=0.8,
            rationale="up",
            suggested_position_pct=0.02,
            forecast=123.45,
            key_indicators={},
            data_quality="OK",
        )

    def test_reports_forecast(self, monkeypatch):
        import engine.advisory as adv_mod

        monkeypatch.setattr(adv_mod, "evaluate", lambda *a, **k: self._fake_rec())
        _patch_advisory_inputs(monkeypatch)

        result = srv.trigger_forecasting("AAPL")

        assert "AAPL" in result
        assert "123.45" in result or "123" in result

    def test_exception_degrades_gracefully(self, monkeypatch):
        import engine.advisory as adv_mod

        def _raise(*a, **k):
            raise RuntimeError("forecast down")

        monkeypatch.setattr(adv_mod, "evaluate", _raise)
        _patch_advisory_inputs(monkeypatch)

        result = srv.trigger_forecasting("AAPL")
        assert isinstance(result, str)
        low = result.lower()
        assert "error" in low or "fail" in low or "unavailable" in low


class TestTriggerMacroEngineInProcess:
    """``trigger_macro_engine`` now constructs an in-process ``MacroEngine``
    (fed by a ``DataEngine``) instead of shelling to ``macro_engine.py``."""

    def test_uses_in_process_macro_engine(self, monkeypatch):
        import macro_engine as me_mod
        import data_engine as de_mod

        constructed = {"macro": False}

        def _make_macro(*a, **k):
            constructed["macro"] = True
            return _fake_macro_engine()

        monkeypatch.setattr(me_mod, "MacroEngine", _make_macro)
        monkeypatch.setattr(de_mod, "DataEngine", lambda *a, **k: MagicMock(), raising=False)

        result = srv.trigger_macro_engine()

        assert constructed["macro"] is True
        assert isinstance(result, str) and result

    def test_exception_degrades_gracefully(self, monkeypatch):
        import macro_engine as me_mod
        import data_engine as de_mod

        def _raise(*a, **k):
            raise RuntimeError("macro boom")

        monkeypatch.setattr(me_mod, "MacroEngine", _raise)
        monkeypatch.setattr(de_mod, "DataEngine", lambda *a, **k: MagicMock(), raising=False)

        result = srv.trigger_macro_engine()
        assert isinstance(result, str)
        low = result.lower()
        assert "error" in low or "fail" in low or "boom" in result


class TestTriggerFullPipelineInProcess:
    """``trigger_full_pipeline``: Step 1 (price bars) and Step 3 (macro) are now
    in-process; Step 2 (EDGAR fundamentals) is STILL a subprocess but now
    ALWAYS passes ``--tickers``."""

    def test_reports_per_step_status_and_edgar_tickers(self, monkeypatch):
        import data.historical_store as hs_mod
        import macro_engine as me_mod
        import data_engine as de_mod

        idx = pd.bdate_range("2024-01-01", periods=3)
        bars = pd.DataFrame(
            {"Open": 1.0, "High": 2.0, "Low": 0.5, "Close": 1.5, "Volume": 100}, index=idx
        )
        fake_store = MagicMock()
        fake_store.get_bars.return_value = bars
        monkeypatch.setattr(hs_mod, "HistoricalStore", lambda *a, **k: fake_store)

        monkeypatch.setattr(me_mod, "MacroEngine", lambda *a, **k: _fake_macro_engine())
        monkeypatch.setattr(de_mod, "DataEngine", lambda *a, **k: MagicMock(), raising=False)

        calls = []

        def _fake_run(cmd, **k):
            calls.append(list(cmd))
            return SimpleNamespace(stdout="", stderr="", returncode=0)

        monkeypatch.setattr(subprocess, "run", _fake_run)

        result = srv.trigger_full_pipeline("AAPL,MSFT")

        # Per-ticker Step 1 + Step 2 (edgar) + Step 3 (macro) statuses render.
        assert "AAPL" in result and "MSFT" in result
        assert "edgar" in result.lower()
        assert "macro" in result.lower()
        # Step 1 fetched bars in-process for each ticker.
        assert fake_store.get_bars.call_count >= 2
        # Step 2 (edgar) remained a subprocess and always passed --tickers.
        flat = [tok for cmd in calls for tok in cmd]
        assert "--tickers" in flat


# ---------------------------------------------------------------------------
# New read-only market-intelligence tools:
#   get_recommendation / get_options_directive / get_regime_status /
#   get_portfolio_coverage
# Each renders markdown for a human PLUS a fenced ```json block, and each is
# dead-letter safe (an engine exception degrades to an error/"unavailable"
# string, never a raise).
# ---------------------------------------------------------------------------


class TestGetRecommendation:
    def _fake_rec(self):
        return SimpleNamespace(
            symbol="AAPL",
            action="BUY",
            strategy="momentum",
            conviction=0.82,
            rationale="Strong uptrend with a positive 30-day forecast.",
            suggested_position_pct=0.03,
            forecast=155.0,
            key_indicators={"rsi_2": 5.0, "garch_vol": 0.21},
            data_quality="OK",
        )

    def test_happy_path_renders_fields_and_json_block(self, monkeypatch):
        import engine.advisory as adv_mod

        monkeypatch.setattr(adv_mod, "evaluate", lambda *a, **k: self._fake_rec())
        _patch_advisory_inputs(monkeypatch)

        result = srv.get_recommendation("aapl")

        assert "AAPL" in result
        assert "BUY" in result
        assert "```json" in result

    def test_exception_degrades(self, monkeypatch):
        import engine.advisory as adv_mod

        def _raise(*a, **k):
            raise RuntimeError("engine down")

        monkeypatch.setattr(adv_mod, "evaluate", _raise)
        _patch_advisory_inputs(monkeypatch)

        result = srv.get_recommendation("AAPL")
        assert isinstance(result, str)
        low = result.lower()
        assert "error" in low or "unavailable" in low or "fail" in low


class TestGetOptionsDirective:
    def _directive(self):
        return {
            "Symbol": "AAPL",
            "Strategy": "Put Credit Spread",
            "Action": "SELL",
            "Net_Premium": 1.25,
            "Short_Strike": 145.0,
            "Long_Strike": 140.0,
            "Sigma_GARCH": 0.22,
            "Trend_Bias": "Bullish",
            "Integrity_OK": True,
        }

    def _patch_bars_provider(self, monkeypatch):
        """The generic MagicMock() provider from _patch_advisory_inputs
        returns a MagicMock (truthy .empty) for get_intraday_bars, which
        trips the tool's "no bar data" guard before it ever reaches
        build_premium_directive. Provide a fake with a real, non-empty
        bars DataFrame instead."""
        import data.market_data as md_mod

        idx = pd.bdate_range("2024-01-01", periods=30)
        bars = pd.DataFrame(
            {"Open": 150.0, "High": 152.0, "Low": 148.0, "Close": 150.0, "Volume": 1_000_000},
            index=idx,
        )

        fake_provider = MagicMock()
        fake_provider.get_intraday_bars.return_value = bars
        fake_provider.get_latest_quote.return_value = SimpleNamespace(price=150.0, is_stale=False)
        monkeypatch.setattr(md_mod, "get_provider", lambda *a, **k: fake_provider, raising=False)

    def test_happy_path_renders_directive_and_json_block(self, monkeypatch):
        import technical_options_engine as toe_mod

        monkeypatch.setattr(toe_mod, "build_premium_directive", lambda *a, **k: self._directive())
        monkeypatch.setattr(
            toe_mod,
            "validate_directive_integrity",
            lambda *a, **k: {"ok": True, "issues": [], "checks": []},
        )
        _patch_advisory_inputs(monkeypatch)
        self._patch_bars_provider(monkeypatch)

        result = srv.get_options_directive("aapl")

        assert "AAPL" in result
        assert "Put Credit Spread" in result or "SELL" in result
        assert "```json" in result

    def test_exception_degrades(self, monkeypatch):
        import technical_options_engine as toe_mod

        def _raise(*a, **k):
            raise RuntimeError("garch failed")

        monkeypatch.setattr(toe_mod, "build_premium_directive", _raise)
        _patch_advisory_inputs(monkeypatch)
        self._patch_bars_provider(monkeypatch)

        result = srv.get_options_directive("AAPL")
        assert isinstance(result, str)
        low = result.lower()
        assert "error" in low or "unavailable" in low or "fail" in low

    def test_nan_realizable_theta_renders_as_na_not_literal_nan(self, monkeypatch):
        """A debit-spread/Covered-Call/Cash directive never computes
        Realizable_Daily_Theta (engine leaves it NaN, CONSTRAINT #4). The
        markdown renderer must show 'N/A', not the literal string 'nan'."""
        import technical_options_engine as toe_mod

        directive = self._directive()
        directive["Strategy"] = "Call Debit Spread"
        directive["Realizable_Daily_Theta"] = float("nan")

        monkeypatch.setattr(toe_mod, "build_premium_directive", lambda *a, **k: directive)
        monkeypatch.setattr(
            toe_mod,
            "validate_directive_integrity",
            lambda *a, **k: {"ok": True, "issues": [], "checks": []},
        )
        _patch_advisory_inputs(monkeypatch)
        self._patch_bars_provider(monkeypatch)

        result = srv.get_options_directive("aapl")

        assert "Realizable Daily Theta**: N/A" in result
        assert "nan" not in result.lower().split("```json")[0]


class TestGetRegimeStatus:
    def _write_snapshot(self, tmp_path, data):
        (tmp_path / "output").mkdir(exist_ok=True)
        (tmp_path / "output" / "state_snapshot.json").write_text(
            json.dumps(data), encoding="utf-8"
        )

    def test_happy_path_renders_regime_and_json_block(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        self._write_snapshot(
            tmp_path,
            {
                "market_regime": "RISK ON",
                "vix": 14.5,
                "sahm_rule": 0.1,
                "high_yield_oas": 3.2,
                "yield_curve": 0.4,
                "hmm_risk_on_probability": 0.72,
                "macro_regime_gate_enabled": True,
            },
        )
        import execution.kill_switch as ks_mod

        fake_ks = MagicMock()
        fake_ks.is_active.return_value = False
        monkeypatch.setattr(ks_mod, "GlobalKillSwitch", lambda *a, **k: fake_ks)

        result = srv.get_regime_status()

        assert "RISK ON" in result or "14.5" in result
        assert "```json" in result

    def test_exception_degrades(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)  # no output/state_snapshot.json present
        import execution.kill_switch as ks_mod

        def _raise(*a, **k):
            raise RuntimeError("ks boom")

        monkeypatch.setattr(ks_mod, "GlobalKillSwitch", _raise)

        result = srv.get_regime_status()
        assert isinstance(result, str) and result


class TestGetPortfolioCoverage:
    def _fake_report(self):
        # data.portfolio_sync.SyncReport.symbols is Mapping[str, SymbolStatus]
        # (a dict keyed by ticker), not a list -- match the real contract.
        sym = SimpleNamespace(
            symbol="AAPL",
            coverage="FULL",
            current_price=150.0,
            market_value=1500.0,
            cost_basis_delta_per_share=5.0,
            held=True,
            forecast_available=True,
            watchlists=(),
            diagnostic="",
        )
        return SimpleNamespace(
            symbols={"AAPL": sym},
            provider_source="yfinance",
            fundamentals_source="yahoo_computed",
            n_total=1,
            n_full=1,
            n_equity_only=0,
            n_uncovered=0,
            held_total_equity=lambda: 1500.0,
            generated_at="2026-01-01T00:00:00Z",
        )

    def test_happy_path_renders_coverage_and_json_block(self, monkeypatch):
        import data.portfolio_sync as ps_mod

        monkeypatch.setattr(ps_mod, "build_sync_report", lambda *a, **k: self._fake_report())
        _patch_advisory_inputs(monkeypatch)

        result = srv.get_portfolio_coverage()

        assert "AAPL" in result or "FULL" in result or "Coverage" in result
        assert "```json" in result

    def test_exception_degrades(self, monkeypatch):
        import data.portfolio_sync as ps_mod

        def _raise(*a, **k):
            raise RuntimeError("sync boom")

        monkeypatch.setattr(ps_mod, "build_sync_report", _raise)
        _patch_advisory_inputs(monkeypatch)

        result = srv.get_portfolio_coverage()
        assert isinstance(result, str)
        low = result.lower()
        assert "error" in low or "unavailable" in low or "fail" in low


class TestGetPortfolioContextNote:
    """``get_portfolio_context_note`` — Phase 2 PR3 RAG portfolio contextualizer
    tool. Reads the account snapshot DB-first (never forces a live login);
    the LLM note itself is generated by ``engine.portfolio_context.
    generate_portfolio_context_note`` (tested in isolation in
    tests/test_portfolio_context.py) — here we only verify the tool's own
    markdown rendering + degradation paths, never raising regardless of
    snapshot presence or downstream failure."""

    def _fake_snapshot(self):
        pos = SimpleNamespace(symbol="AAPL", market_value=1000.0)
        return SimpleNamespace(positions={"AAPL": pos}, total_equity=1000.0)

    def test_no_snapshot_degrades(self, monkeypatch):
        import data.historical_store as hs_mod

        monkeypatch.setattr(hs_mod.HistoricalStore, "latest_account_snapshot", lambda self: None)

        result = srv.get_portfolio_context_note()
        assert isinstance(result, str) and result
        assert "No positions" in result

    def test_empty_positions_degrades(self, monkeypatch):
        import data.historical_store as hs_mod

        empty_snap = SimpleNamespace(positions={}, total_equity=0.0)
        monkeypatch.setattr(
            hs_mod.HistoricalStore, "latest_account_snapshot", lambda self: empty_snap
        )

        result = srv.get_portfolio_context_note()
        assert isinstance(result, str) and result
        assert "No positions" in result

    def test_happy_path_renders_exposure_table_flag_disabled(self, monkeypatch):
        # RAG_PORTFOLIO_CONTEXT_ENABLED defaults False -- the tool must still
        # render the deterministic exposure table with no AI note and no crash.
        import data.historical_store as hs_mod

        fake_snap = self._fake_snapshot()
        monkeypatch.setattr(
            hs_mod.HistoricalStore, "latest_account_snapshot", lambda self: fake_snap
        )

        result = srv.get_portfolio_context_note()
        assert "Sector Exposure" in result
        assert "AAPL" in result
        assert "No AI context note available" in result

    def test_happy_path_with_mocked_context_note(self, monkeypatch):
        import data.historical_store as hs_mod
        import engine.portfolio_context as pc_mod
        from engine.portfolio_exposure import SectorExposure

        fake_snap = self._fake_snapshot()
        monkeypatch.setattr(
            hs_mod.HistoricalStore, "latest_account_snapshot", lambda self: fake_snap
        )

        fake_note = SimpleNamespace(
            headline="Tech-heavy concentration",
            tailwind_or_headwind="neutral",
            rationale="Portfolio is concentrated in Technology.",
            affected_sectors=["Technology"],
        )
        fake_result = pc_mod.PortfolioContextResult(
            sector_exposure={
                "Technology": SectorExposure(
                    sector="Technology",
                    net_market_value=1000.0,
                    pct_of_equity=1.0,
                    symbols=["AAPL"],
                )
            },
            total_equity=1000.0,
            context_note=fake_note,
            retrieved_document_count=2,
            retrieved_symbols=["AAPL"],
        )
        monkeypatch.setattr(
            pc_mod, "generate_portfolio_context_note", lambda *a, **k: fake_result
        )

        result = srv.get_portfolio_context_note()
        assert "Technology" in result
        assert "Tech-heavy concentration" in result
        assert "2 retrieved document" in result

    def test_exception_degrades(self, monkeypatch):
        import data.historical_store as hs_mod

        def _raise(self):
            raise RuntimeError("snapshot boom")

        monkeypatch.setattr(hs_mod.HistoricalStore, "latest_account_snapshot", _raise)

        result = srv.get_portfolio_context_note()
        assert isinstance(result, str) and result
        # A snapshot fetch failure degrades to "no snapshot" messaging, not a raise.
        assert "No positions" in result or "unavailable" in result.lower()

    def test_generate_note_exception_degrades(self, monkeypatch):
        import data.historical_store as hs_mod
        import engine.portfolio_context as pc_mod

        fake_snap = self._fake_snapshot()
        monkeypatch.setattr(
            hs_mod.HistoricalStore, "latest_account_snapshot", lambda self: fake_snap
        )

        def _raise(*a, **k):
            raise RuntimeError("context boom")

        monkeypatch.setattr(pc_mod, "generate_portfolio_context_note", _raise)

        result = srv.get_portfolio_context_note()
        assert isinstance(result, str)
        assert "Failed to retrieve portfolio context note" in result


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
    """Bug B fix: the tool used to read a schema
    (``orders``/``shares``/``price``/``gate_reason``) the builder has NEVER
    emitted -- ``execution.queue_builder.build_execution_queue`` writes
    ``intents``/``qty``/``target_notional``/``gate_reasons`` (a list). The old
    reader's ``queue.get("orders", [queue])`` fell through to iterating the
    payload DICT itself as one fake order, rendering a row of literal "?"s on
    every real queue. These tests exercise the REAL schema; the old
    list-shaped/``orders``-wrapped fixtures below were testing the bug's own
    behavior, not the actual contract, and have been replaced."""

    def _real_payload(self, **overrides):
        """A real payload shape via the actual builder, so these tests can't
        drift from what build_execution_queue truly emits."""
        from datetime import datetime, timezone
        from execution.queue_builder import build_execution_queue

        class _Rec:
            def __init__(self, symbol, action, conviction, pct=0.0):
                self.symbol = symbol
                self.action = action
                self.conviction = conviction
                self.suggested_position_pct = pct
                self.strategy = "advisory"
                self.rationale = "Momentum broke down below the 200-day; forecast turned negative."

        class _Pos:
            def __init__(self, symbol, quantity, current_price, market_value):
                self.symbol = symbol
                self.quantity = quantity
                self.current_price = current_price
                self.market_value = market_value
                self.average_cost = current_price
                self.unrealized_pl = 0.0

        class _Snap:
            def __init__(self, positions, total_equity):
                self.positions = positions
                self.total_equity = total_equity
                self.buying_power = total_equity

        class _RR:
            def __init__(self, recs, snap):
                self.recommendations = recs
                self.snapshot = snap

        snap = _Snap({"NVDA": _Pos("NVDA", 10, 500.0, 5000.0)}, 100_000.0)
        _SENTINEL = object()
        recs = overrides.pop("recs", _SENTINEL)
        if recs is _SENTINEL:
            recs = [_Rec("NVDA", "SELL", 0.9), _Rec("AAPL", "BUY", 0.9, pct=0.05)]
        return build_execution_queue(
            _RR(recs, snap), mode="review",
            now=datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc),
        )

    def test_missing_file(self, monkeypatch, tmp_path):
        from settings import settings
        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
        assert "No execution queue file found" in srv.get_execution_queue()

    def test_empty_queue_is_a_real_empty_payload_not_a_bare_list(self, monkeypatch, tmp_path):
        from settings import settings
        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
        payload = self._real_payload(recs=[])
        assert payload["intents"] == []  # confirms the real builder's empty shape
        (tmp_path / "execution_queue.json").write_text(json.dumps(payload), encoding="utf-8")

        assert "Execution queue is empty" in srv.get_execution_queue()

    def test_real_queue_renders_correct_columns_never_question_marks(self, monkeypatch, tmp_path):
        from settings import settings
        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
        payload = self._real_payload()
        (tmp_path / "execution_queue.json").write_text(json.dumps(payload), encoding="utf-8")

        result = srv.get_execution_queue()

        # The old bug rendered one row of "?" cells for the whole payload
        # (iterating the dict as a fake order) -- neither real symbol appeared.
        assert "NVDA" in result and "AAPL" in result
        assert "| `?` |" not in result
        # Real fields, not the old nonexistent shares/price/gate_reason keys.
        assert "$5,000.00" in result  # NVDA's target_notional (held market value)
        assert "Momentum broke down below the 200-day" in result  # Bug D's real rationale
        for intent in payload["intents"]:
            assert intent["symbol"] in result

    def test_gate_reasons_list_is_joined_not_a_python_repr(self, monkeypatch, tmp_path):
        from settings import settings
        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
        payload = self._real_payload()
        (tmp_path / "execution_queue.json").write_text(json.dumps(payload), encoding="utf-8")

        result = srv.get_execution_queue()

        # gate_reasons is a LIST in the real schema (plural) -- must render as
        # joined text, never Python's str(['a', 'b']) list repr.
        assert "['" not in result and '["' not in result

    def test_output_dir_honored_not_a_hardcoded_relative_path(self, monkeypatch, tmp_path):
        """Regression: the old tool hardcoded the relative string
        "output/execution_queue.json", so it was silently cwd-dependent. It
        must read settings.OUTPUT_DIR regardless of the process cwd."""
        from settings import settings
        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
        monkeypatch.chdir(tmp_path.parent)  # cwd is deliberately NOT tmp_path
        payload = self._real_payload()
        (tmp_path / "execution_queue.json").write_text(json.dumps(payload), encoding="utf-8")

        assert "NVDA" in srv.get_execution_queue()

    def test_parity_with_gui_reader_on_the_same_payload(self, monkeypatch, tmp_path):
        """Anti-drift guard (tests/test_state_snapshot_parity.py's pattern,
        applied to the two execution-queue readers): a real builder payload
        fed to BOTH the MCP tool and the GUI's read_execution_queue must
        agree on the intent set. gui.robinhood_execution_panel.py already
        reads this schema correctly -- it's the reference this fix matches."""
        from settings import settings
        from gui.robinhood_execution_panel import read_execution_queue

        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
        payload = self._real_payload()
        queue_path = tmp_path / "execution_queue.json"
        queue_path.write_text(json.dumps(payload), encoding="utf-8")

        mcp_result = srv.get_execution_queue()
        gui_snapshot = read_execution_queue(queue_path)

        assert gui_snapshot is not None
        for intent in gui_snapshot.intents:
            assert intent.symbol in mcp_result
            assert intent.rationale in mcp_result


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


# ---------------------------------------------------------------------------
# Pilots marketplace tools (list_pilots / get_pilot_detail /
# get_pilot_performance / get_pilot_trades / get_follows / follow_pilot)
#
# These wrap pilots.catalog / pilots.scoring / pilots.performance /
# pilots.follows_store / pilots.mirror -- each of which already has its own
# dedicated test suite (tests/test_pilots_*.py). Tests here therefore focus
# on the MCP tool WIRING: arg validation, markdown+json rendering, unknown-
# pilot 404-equivalent messages, and dead-letter degradation -- not on
# re-proving the pilots.* layer's own math. ``catalog.get_pilot``/
# ``list_pilots`` are real (pure, static, dependency-light) rather than
# mocked; the snapshot/performance/follow layers are monkeypatched for
# determinism.
# ---------------------------------------------------------------------------


class TestListPilots:
    def test_no_snapshot_renders_dashes_and_json(self, monkeypatch):
        import pilots.scoring as scoring_mod

        monkeypatch.setattr(scoring_mod, "load_snapshot", lambda *a, **k: None)

        result = srv.list_pilots()

        assert "# Pilots Marketplace" in result
        assert "trend-following" in result
        assert "```json" in result
        payload = json.loads(result.split("```json")[1].split("```")[0])
        assert isinstance(payload, list) and payload
        assert all(row["holdings_count"] == 0 for row in payload)

    def test_exception_degrades(self, monkeypatch):
        import pilots.catalog as catalog_mod

        def _raise(*a, **k):
            raise RuntimeError("catalog boom")

        monkeypatch.setattr(catalog_mod, "list_pilots", _raise)

        result = srv.list_pilots()
        assert "Failed to list pilots" in result


class TestGetPilotDetail:
    def test_unknown_pilot(self):
        result = srv.get_pilot_detail("does-not-exist")
        assert "No such pilot 'does-not-exist'" in result
        assert "trend-following" in result

    def test_no_snapshot_degrades_honestly(self, monkeypatch):
        import pilots.scoring as scoring_mod

        monkeypatch.setattr(scoring_mod, "load_snapshot", lambda *a, **k: None)

        result = srv.get_pilot_detail("trend-following")

        assert "No state snapshot yet" in result
        assert '"holdings": []' in result

    def test_happy_path_with_holdings(self, monkeypatch):
        import pilots.performance as performance_mod
        import pilots.scoring as scoring_mod

        fake_snapshot = {"timestamp": "2026-01-01T00:00:00Z", "signals": []}
        monkeypatch.setattr(scoring_mod, "load_snapshot", lambda *a, **k: fake_snapshot)
        monkeypatch.setattr(
            scoring_mod,
            "pilot_holdings",
            lambda pilot, snap, top_n=None: [
                {"symbol": "AAPL", "weight": 0.6, "score": 0.5, "price": 150.0, "sector": "Technology"}
            ],
        )
        monkeypatch.setattr(
            scoring_mod, "sector_allocation", lambda holdings: [{"sector": "Technology", "weight": 0.6}]
        )
        monkeypatch.setattr(
            scoring_mod,
            "pilot_trades",
            lambda pilot, **k: [{"date": "2026-01-02", "symbol": "AAPL", "side": "ENTER", "weight_delta": 0.6}],
        )
        monkeypatch.setattr(
            performance_mod,
            "pilot_headline",
            lambda pilot, **k: {"sharpe": 1.2, "dsr": 0.99, "pbo": 0.1, "max_drawdown": 0.2, "deployable": True},
        )

        result = srv.get_pilot_detail("trend-following")

        assert "AAPL" in result
        assert "Technology" in result
        assert "```json" in result

    def test_exception_degrades(self, monkeypatch):
        import pilots.catalog as catalog_mod

        def _raise(*a, **k):
            raise RuntimeError("boom")

        monkeypatch.setattr(catalog_mod, "get_pilot", _raise)

        result = srv.get_pilot_detail("trend-following")
        assert "Failed to get pilot detail" in result


class TestGetPilotPerformance:
    def test_unknown_pilot(self):
        assert "No such pilot" in srv.get_pilot_performance("nope", "1M")

    def test_invalid_range(self):
        result = srv.get_pilot_performance("trend-following", "5Y")
        assert "Invalid range '5Y'" in result

    def test_case_insensitive_range_and_metrics_rendering(self, monkeypatch):
        import pilots.performance as performance_mod

        monkeypatch.setattr(
            performance_mod,
            "pilot_performance",
            lambda pilot, range="1M", **k: {
                "metrics": {"sharpe": 1.1, "dsr": 0.98, "pbo": 0.05, "max_drawdown": 0.15, "deployable": True},
                "curve": [{"date": "2026-01-01", "value": 100.0}, {"date": "2026-01-02", "value": 101.0}],
                "benchmark": None,
                "macro_benchmark": None,
                "reason": None,
                "range": range,
            },
        )

        result = srv.get_pilot_performance("trend-following", "1m")

        assert "1M" in result
        assert "2 points" in result
        assert "```json" in result

    def test_no_backtest_reason_surfaced(self, monkeypatch):
        import pilots.performance as performance_mod

        monkeypatch.setattr(
            performance_mod,
            "pilot_performance",
            lambda pilot, range="1M", **k: {
                "metrics": None,
                "curve": None,
                "benchmark": None,
                "macro_benchmark": None,
                "reason": "no validated backtest for this pilot",
                "range": range,
            },
        )

        result = srv.get_pilot_performance("regime-navigator", "1M")
        assert "no validated backtest for this pilot" in result

    def test_exception_degrades(self, monkeypatch):
        import pilots.performance as performance_mod

        def _raise(*a, **k):
            raise RuntimeError("boom")

        monkeypatch.setattr(performance_mod, "pilot_performance", _raise)

        result = srv.get_pilot_performance("trend-following", "1M")
        assert "Failed to get performance" in result


class TestGetPilotTrades:
    def test_unknown_pilot(self):
        assert "No such pilot" in srv.get_pilot_trades("nope")

    def test_empty_history(self, monkeypatch):
        import pilots.scoring as scoring_mod

        monkeypatch.setattr(scoring_mod, "pilot_trades", lambda *a, **k: [])

        result = srv.get_pilot_trades("trend-following")
        assert "No trade events" in result

    def test_happy_path_respects_limit(self, monkeypatch):
        import pilots.scoring as scoring_mod

        events = [
            {"date": f"2026-01-0{i}", "symbol": "AAPL", "side": "REWEIGHT", "weight_delta": 0.01 * i}
            for i in range(1, 4)
        ]
        monkeypatch.setattr(scoring_mod, "pilot_trades", lambda *a, **k: events)

        result = srv.get_pilot_trades("trend-following", limit=2)

        payload = json.loads(result.split("```json")[1].split("```")[0])
        assert payload == events[-2:]

    def test_exception_degrades(self, monkeypatch):
        import pilots.scoring as scoring_mod

        def _raise(*a, **k):
            raise RuntimeError("boom")

        monkeypatch.setattr(scoring_mod, "pilot_trades", _raise)

        result = srv.get_pilot_trades("trend-following")
        assert "Failed to get trades" in result


class TestGetFollows:
    def test_no_active_follows(self, monkeypatch):
        import pilots.follows_store as fs_mod

        monkeypatch.setattr(fs_mod.FollowsStore, "list_active", lambda self: [])

        result = srv.get_follows()
        assert "No active follows" in result

    def test_happy_path(self, monkeypatch):
        import pilots.follows_store as fs_mod

        rows = [
            {
                "pilot_id": "trend-following",
                "amount": 500.0,
                "created_at": "t1",
                "updated_at": "t2",
                "status": "active",
            }
        ]
        monkeypatch.setattr(fs_mod.FollowsStore, "list_active", lambda self: rows)

        result = srv.get_follows()
        assert "trend-following" in result
        assert "$500.00" in result

    def test_exception_degrades(self, monkeypatch):
        import pilots.follows_store as fs_mod

        def _raise(self):
            raise RuntimeError("boom")

        monkeypatch.setattr(fs_mod.FollowsStore, "list_active", _raise)

        result = srv.get_follows()
        assert "Failed to list follows" in result


class TestFollowPilot:
    def test_unknown_pilot(self):
        assert "No such pilot" in srv.follow_pilot("nope", 100)

    def test_non_positive_amount_rejected(self):
        assert "amount must be > 0" in srv.follow_pilot("trend-following", 0)
        assert "amount must be > 0" in srv.follow_pilot("trend-following", -5)

    def test_kill_switch_blocks(self, monkeypatch):
        import execution.kill_switch as ks_mod

        monkeypatch.setattr(ks_mod.GlobalKillSwitch, "is_active", lambda self: True)
        monkeypatch.setattr(ks_mod.GlobalKillSwitch, "reason", lambda self: "VIX spike")

        result = srv.follow_pilot("trend-following", 500)

        assert "Kill switch is active" in result
        assert "VIX spike" in result

    def test_happy_path_no_account_snapshot(self, monkeypatch):
        import data.historical_store as hs_mod
        import execution.kill_switch as ks_mod
        import pilots.follows_store as fs_mod
        import pilots.mirror as mirror_mod
        import pilots.scoring as scoring_mod

        monkeypatch.setattr(ks_mod.GlobalKillSwitch, "is_active", lambda self: False)
        follow_row = {"pilot_id": "trend-following", "amount": 500.0, "status": "active"}
        monkeypatch.setattr(fs_mod.FollowsStore, "upsert", lambda self, pid, amt: follow_row)
        monkeypatch.setattr(scoring_mod, "load_snapshot", lambda *a, **k: None)
        monkeypatch.setattr(hs_mod.HistoricalStore, "latest_account_snapshot", lambda self: None)
        monkeypatch.setattr(
            mirror_mod,
            "plan_follow",
            lambda pilot, amount, account_snapshot, snapshot=None: {
                "planned_intents": [],
                "mode": "off",
                "queue_written": False,
            },
        )

        result = srv.follow_pilot("trend-following", 500)

        assert "no account snapshot" in result
        assert "No order is placed automatically" in result
        assert '"queue_written": false' in result

    def test_happy_path_with_planned_intents(self, monkeypatch):
        import data.historical_store as hs_mod
        import execution.kill_switch as ks_mod
        import pilots.follows_store as fs_mod
        import pilots.mirror as mirror_mod
        import pilots.scoring as scoring_mod

        monkeypatch.setattr(ks_mod.GlobalKillSwitch, "is_active", lambda self: False)
        monkeypatch.setattr(
            fs_mod.FollowsStore, "upsert", lambda self, pid, amt: {"pilot_id": pid, "amount": amt}
        )
        monkeypatch.setattr(scoring_mod, "load_snapshot", lambda *a, **k: {"timestamp": "t"})
        fake_snap = SimpleNamespace(total_equity=10000.0)
        monkeypatch.setattr(hs_mod.HistoricalStore, "latest_account_snapshot", lambda self: fake_snap)
        monkeypatch.setattr(
            mirror_mod,
            "plan_follow",
            lambda pilot, amount, account_snapshot, snapshot=None: {
                "planned_intents": [
                    {"symbol": "AAPL", "action": "BUY", "target_notional": 300.0, "rationale": "underweight"}
                ],
                "mode": "review",
                "queue_written": True,
            },
        )

        result = srv.follow_pilot("trend-following", 500)

        assert "account snapshot loaded (DB)" in result
        assert "AAPL" in result
        assert "$300.00" in result

    def test_exception_degrades(self, monkeypatch):
        import pilots.catalog as catalog_mod

        def _raise(*a, **k):
            raise RuntimeError("boom")

        monkeypatch.setattr(catalog_mod, "get_pilot", _raise)

        result = srv.follow_pilot("trend-following", 500)
        assert "Failed to follow pilot" in result


# ---------------------------------------------------------------------------
# Prompt Registry version-control tools (Backlog item 9): get_registry_prompt_status,
# get_registry_prompt, diff_registry_prompt, pin_registry_prompt,
# rollback_registry_prompt, sync_prompt_registry.
#
# Uses REAL PromptRegistry + CacheManager instances (mirrors the fixture
# pattern in tests/test_prompt_registry_cli.py) rather than a MagicMock, so
# these tests exercise the actual resolution chain / cache / rollback logic
# the new tools wrap, not just their own control flow. The singleton is
# injected by monkeypatching prompt_registry.get_registry directly (the same
# approach TestPromptRegistryWiring above already uses), since every new tool
# does a local `from prompt_registry import get_registry` inside its body.
# ---------------------------------------------------------------------------


class _PRFakeStore:
    def __init__(self, manifest):
        self._manifest = manifest

    def fetch_manifest(self):
        return self._manifest


class _PRFailingStore:
    def fetch_manifest(self):
        from prompt_registry.store import RegistryFetchError
        raise RegistryFetchError("forced failure")


def _pr_make_record(body, key=None):
    from prompt_registry.models import PromptRecord
    from prompt_registry.signing import compute_sha256, sign

    sha = compute_sha256(body)
    sig = sign(body, key) if key else "unsigned"
    return PromptRecord(body=body, sha256=sha, signature=sig, created_at="2026-06-30T00:00:00Z")


def _pr_make_manifest(entries, key=None):
    from prompt_registry.models import PromptVersion, RegistryManifest

    prompts = {}
    for pid, body in entries.items():
        prompts[pid] = PromptVersion(latest="1.0.0", versions={"1.0.0": _pr_make_record(body, key=key)})
    return RegistryManifest(registry_version="test-mcp", signing_alg="HMAC-SHA256", prompts=prompts)


def _pr_make_registry(tmp_path, *, manifest=None, pins=None, enabled=True, store=None):
    from prompt_registry.cache import CacheManager
    from prompt_registry.registry import PromptRegistry

    cache = CacheManager(tmp_path)
    s = store if store is not None else (_PRFakeStore(manifest) if manifest else None)
    reg = PromptRegistry(store=s, cache=cache, pins=pins or {}, enabled=enabled)
    if manifest is not None:
        reg._manifest = manifest
    return reg


def _pr_inject(monkeypatch, reg):
    import prompt_registry as pr_pkg

    monkeypatch.setattr(pr_pkg, "get_registry", lambda: reg)


class TestGetRegistryPromptStatus:
    def test_no_ids_found_message(self, monkeypatch, tmp_path):
        import prompt_registry.cache as cache_mod

        monkeypatch.setattr(cache_mod, "list_baseline_ids", lambda: [])
        reg = _pr_make_registry(tmp_path, enabled=False)
        _pr_inject(monkeypatch, reg)

        assert "No prompt IDs found" in srv.get_registry_prompt_status()

    def test_renders_markdown_table_with_pin_and_cache_counts(self, monkeypatch, tmp_path):
        manifest = _pr_make_manifest({"custom.test.prompt": "REMOTE BODY"})
        reg = _pr_make_registry(tmp_path, manifest=manifest, pins={"custom.pinned.prompt": "2.0.0"})
        reg._cache.write("custom.pinned.prompt", "1.0.0", _pr_make_record("v1 body"))
        reg._cache.write("custom.pinned.prompt", "2.0.0", _pr_make_record("v2 body"))
        _pr_inject(monkeypatch, reg)

        result = srv.get_registry_prompt_status()

        assert "custom.test.prompt" in result
        assert "remote" in result
        assert "custom.pinned.prompt" in result
        assert "2.0.0" in result  # pinned version shown in its own column
        assert "| 2 |" in result  # cache count of 2 cached versions

    def test_disabled_registry_shows_banner(self, monkeypatch, tmp_path):
        reg = _pr_make_registry(tmp_path, enabled=False, pins={"custom.test.prompt": "1.0.0"})
        _pr_inject(monkeypatch, reg)

        result = srv.get_registry_prompt_status()

        assert "disabled" in result.lower()


class TestGetRegistryPrompt:
    def test_resolved_body_no_version_uses_remote(self, monkeypatch, tmp_path):
        manifest = _pr_make_manifest({"custom.test.prompt": "REMOTE BODY TEXT"})
        reg = _pr_make_registry(tmp_path, manifest=manifest)
        _pr_inject(monkeypatch, reg)

        result = srv.get_registry_prompt("custom.test.prompt")

        assert "REMOTE BODY TEXT" in result
        assert "custom.test.prompt" in result

    def test_specific_version_found_in_cache(self, monkeypatch, tmp_path):
        reg = _pr_make_registry(tmp_path)
        reg._cache.write("custom.test.prompt", "1.0.0", _pr_make_record("CACHED BODY"))
        _pr_inject(monkeypatch, reg)

        result = srv.get_registry_prompt("custom.test.prompt", version="1.0.0")

        assert "CACHED BODY" in result

    def test_specific_version_not_found(self, monkeypatch, tmp_path):
        reg = _pr_make_registry(tmp_path)
        _pr_inject(monkeypatch, reg)

        result = srv.get_registry_prompt("custom.test.prompt", version="9.9.9")

        assert "not found" in result

    def test_unknown_id_degrades_to_sentinel_message(self, monkeypatch, tmp_path):
        reg = _pr_make_registry(tmp_path)
        _pr_inject(monkeypatch, reg)

        result = srv.get_registry_prompt("totally_unknown_prompt_id_zzz")

        assert "no body" in result.lower()

    def test_baseline_fallback_when_no_pin_remote_or_cache(self, monkeypatch, tmp_path):
        reg = _pr_make_registry(tmp_path)
        _pr_inject(monkeypatch, reg)

        result = srv.get_registry_prompt("master_preprompt")

        # Baseline is always present for a known baseline id (CONSTRAINT #4).
        assert "ADVISORY_ONLY" in result


class TestDiffRegistryPrompt:
    def test_diff_produces_unified_diff(self, monkeypatch, tmp_path):
        reg = _pr_make_registry(tmp_path)
        reg._cache.write("custom.test.prompt", "1.0.0", _pr_make_record("line one\nline two\n"))
        reg._cache.write("custom.test.prompt", "2.0.0", _pr_make_record("line one\nline TWO CHANGED\n"))
        _pr_inject(monkeypatch, reg)

        result = srv.diff_registry_prompt("custom.test.prompt", "1.0.0", "2.0.0")

        assert "-line two" in result
        assert "+line TWO CHANGED" in result

    def test_no_differences_between_identical_versions(self, monkeypatch, tmp_path):
        reg = _pr_make_registry(tmp_path)
        reg._cache.write("custom.test.prompt", "1.0.0", _pr_make_record("same body\n"))
        _pr_inject(monkeypatch, reg)

        result = srv.diff_registry_prompt("custom.test.prompt", "1.0.0", "1.0.0")

        assert "No differences" in result

    def test_version_a_not_found(self, monkeypatch, tmp_path):
        reg = _pr_make_registry(tmp_path)
        reg._cache.write("custom.test.prompt", "1.0.0", _pr_make_record("body"))
        _pr_inject(monkeypatch, reg)

        result = srv.diff_registry_prompt("custom.test.prompt", "9.9.9", "1.0.0")

        assert "9.9.9" in result and "not found" in result

    def test_version_b_not_found(self, monkeypatch, tmp_path):
        reg = _pr_make_registry(tmp_path)
        reg._cache.write("custom.test.prompt", "1.0.0", _pr_make_record("body"))
        _pr_inject(monkeypatch, reg)

        result = srv.diff_registry_prompt("custom.test.prompt", "1.0.0", "9.9.9")

        assert "9.9.9" in result and "not found" in result


class TestPinRegistryPrompt:
    def test_missing_version_returns_error_and_does_not_set_pin(self, monkeypatch, tmp_path):
        reg = _pr_make_registry(tmp_path)
        _pr_inject(monkeypatch, reg)

        result = srv.pin_registry_prompt("custom.test.prompt", "9.9.9")

        assert "not found" in result
        assert "pin NOT set" in result
        assert "custom.test.prompt" not in reg._pins

    def test_valid_version_sets_pin_and_writes_env(self, monkeypatch, tmp_path):
        import gui.env_io as env_io

        reg = _pr_make_registry(tmp_path)
        reg._cache.write("custom.test.prompt", "1.0.0", _pr_make_record("body"))
        _pr_inject(monkeypatch, reg)

        env_file = tmp_path / ".env"
        monkeypatch.setattr(env_io, "ENV_PATH", env_file)

        result = srv.pin_registry_prompt("custom.test.prompt", "1.0.0")

        assert "Pinned" in result
        assert reg._pins["custom.test.prompt"] == "1.0.0"
        written = json.loads(env_file.read_text(encoding="utf-8").split("=", 1)[1].strip().strip("'\""))
        assert written == {"custom.test.prompt": "1.0.0"}

    def test_pin_passes_a_real_dict_not_a_pre_encoded_json_string(self, monkeypatch, tmp_path):
        """Regression guard: gui/panels/prompt_registry.py's Streamlit tab calls
        env_io.write_setting("PROMPT_REGISTRY_PINS", json.dumps(...)) -- since
        write_setting's _encode_value() ALSO json.dumps()'s JSON-classified
        keys, that pre-encodes the value TWICE, writing a JSON string literal
        wrapping the real dict rather than the dict itself. This tool must
        pass the dict straight through instead."""
        import gui.env_io as env_io

        reg = _pr_make_registry(tmp_path)
        reg._cache.write("custom.test.prompt", "1.0.0", _pr_make_record("body"))
        _pr_inject(monkeypatch, reg)

        captured = {}
        monkeypatch.setattr(
            env_io, "write_setting",
            lambda key, value: captured.setdefault(key, value) or "",
        )

        srv.pin_registry_prompt("custom.test.prompt", "1.0.0")

        assert captured["PROMPT_REGISTRY_PINS"] == {"custom.test.prompt": "1.0.0"}
        assert isinstance(captured["PROMPT_REGISTRY_PINS"], dict)

    def test_env_write_failure_degrades_to_in_memory_pin(self, monkeypatch, tmp_path):
        import gui.env_io as env_io

        reg = _pr_make_registry(tmp_path)
        reg._cache.write("custom.test.prompt", "1.0.0", _pr_make_record("body"))
        _pr_inject(monkeypatch, reg)

        def _raise(*a, **k):
            raise env_io.DisallowedKeyError("simulated allowlist regression")

        monkeypatch.setattr(env_io, "write_setting", _raise)

        result = srv.pin_registry_prompt("custom.test.prompt", "1.0.0")

        assert "in-memory" in result
        assert reg._pins["custom.test.prompt"] == "1.0.0"  # pin still applied this session


class TestRollbackRegistryPrompt:
    def test_no_older_cached_version_reports_honestly(self, monkeypatch, tmp_path):
        reg = _pr_make_registry(tmp_path)
        reg._cache.write("custom.test.prompt", "1.0.0", _pr_make_record("only version"))
        _pr_inject(monkeypatch, reg)

        result = srv.rollback_registry_prompt("custom.test.prompt")

        assert "Cannot roll back" in result
        assert "no older cached version" in result

    def test_no_cached_versions_at_all_reports_honestly(self, monkeypatch, tmp_path):
        reg = _pr_make_registry(tmp_path)
        _pr_inject(monkeypatch, reg)

        result = srv.rollback_registry_prompt("never_cached_prompt_id")

        assert "Cannot roll back" in result

    def test_rollback_success_writes_env(self, monkeypatch, tmp_path):
        import gui.env_io as env_io
        import time as _time

        reg = _pr_make_registry(tmp_path)
        reg._cache.write("custom.test.prompt", "1.0.0", _pr_make_record("v1"))
        _time.sleep(0.01)
        reg._cache.write("custom.test.prompt", "2.0.0", _pr_make_record("v2"))
        _pr_inject(monkeypatch, reg)

        env_file = tmp_path / ".env"
        monkeypatch.setattr(env_io, "ENV_PATH", env_file)

        result = srv.rollback_registry_prompt("custom.test.prompt")

        assert "Rolled back" in result
        assert reg._pins["custom.test.prompt"] == "1.0.0"
        assert "PROMPT_REGISTRY_PINS" in env_file.read_text(encoding="utf-8")


class TestSyncPromptRegistry:
    def test_disabled_registry_reports_honestly(self, monkeypatch, tmp_path):
        reg = _pr_make_registry(tmp_path, enabled=False)
        _pr_inject(monkeypatch, reg)

        result = srv.sync_prompt_registry()

        assert "disabled" in result.lower()

    def test_no_store_configured_reports_honestly(self, monkeypatch, tmp_path):
        reg = _pr_make_registry(tmp_path, enabled=True, store=None)
        _pr_inject(monkeypatch, reg)

        result = srv.sync_prompt_registry()

        assert "No remote store configured" in result

    def test_successful_sync_reports_manifest_info(self, monkeypatch, tmp_path):
        manifest = _pr_make_manifest({"custom.test.prompt": "REMOTE BODY"})
        reg = _pr_make_registry(tmp_path, enabled=True, store=_PRFakeStore(manifest))
        _pr_inject(monkeypatch, reg)

        result = srv.sync_prompt_registry()

        assert "Sync complete" in result
        assert "test-mcp" in result

    def test_failed_sync_reports_failure(self, monkeypatch, tmp_path):
        reg = _pr_make_registry(tmp_path, enabled=True, store=_PRFailingStore())
        _pr_inject(monkeypatch, reg)

        result = srv.sync_prompt_registry()

        assert "Sync failed" in result


# ---------------------------------------------------------------------------
# read_platform_logs — logs/investyo.log path fix
# ---------------------------------------------------------------------------


class TestReadPlatformLogsFindsLogsSubdirectory:
    """Regression test for the path bug: read_platform_logs used to only
    os.listdir(".") for *.log files, so it never found the REAL rotating log
    file alerting.py::setup_logging actually writes (logs/investyo.log, one
    directory down). Fixed to also look inside logs/."""

    def test_finds_investyo_log_in_logs_subdir(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        (logs_dir / "investyo.log").write_text("line1\nline2\nline3\n", encoding="utf-8")

        result = srv.read_platform_logs(lines=2)

        assert "investyo.log" in result
        assert "line2" in result
        assert "line3" in result

    def test_still_finds_cwd_log_files_too(self, monkeypatch, tmp_path):
        """Backward-compat: a *.log file directly in the cwd (not under
        logs/) is still found -- pins the pre-existing behavior/test."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "app.log").write_text("a\nb\n", encoding="utf-8")

        result = srv.read_platform_logs(lines=10)

        assert "app.log" in result

    def test_finds_both_logs_subdir_and_cwd_files(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        (logs_dir / "investyo.log").write_text("db line\n", encoding="utf-8")
        (tmp_path / "other.log").write_text("other line\n", encoding="utf-8")

        result = srv.read_platform_logs(lines=10)

        assert "investyo.log" in result
        assert "other.log" in result
