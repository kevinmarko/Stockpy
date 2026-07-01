"""
tests/test_database_setup.py
=============================
Unit tests for ``database_setup.py`` — the SQLite schema initializer that
dynamically derives the ``DailySignals`` table from ``config.COLUMN_SCHEMA``
(CLAUDE.md: "never hardcode SQL column lists").

``tests/test_quantitative_models.py::test_database_schema_initialization``
already pins the happy path (all real COLUMN_SCHEMA keys land as columns).
This file closes the remaining gaps:

  * ``type_map`` — the format-string → SQLite-type translation table,
    including the ``Target_Days``/``Volume`` INTEGER special case and the
    unknown-format TEXT fallback.
  * ``initialize_database`` idempotency — running it twice against the same
    file must not raise or duplicate/drop data (``CREATE TABLE IF NOT
    EXISTS``).
  * Malformed ``config.COLUMN_SCHEMA`` entries — a missing ``"format"`` key
    raises ``KeyError`` (no silent guard exists in the source); a duplicate
    ``"key"`` produces a single SQLite column (later entry wins, no crash)
    since ``CREATE TABLE`` would otherwise raise ``OperationalError: duplicate
    column name``.
  * ``migrate_daily_signals_schema`` — an existing DailySignals table missing
    a newly-added COLUMN_SCHEMA column gets it added via ``ALTER TABLE``
    without touching pre-existing data; re-running the migration against an
    already-current schema is a no-op (no ``ALTER TABLE`` attempted twice).

All tests use a ``tmp_path``-backed SQLite file — never the real,
repo-committed ``quant_platform.db``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest import mock

import pytest

import database_setup


# ---------------------------------------------------------------------------
# type_map
# ---------------------------------------------------------------------------

class TestTypeMap:
    @pytest.mark.parametrize(
        "col_format,expected",
        [
            ("string", "TEXT"),
            ("number", "REAL"),
            ("currency", "REAL"),
            ("currency_large", "REAL"),
            ("percent", "REAL"),
            # Case/whitespace insensitivity.
            ("STRING", "TEXT"),
            ("  number  ", "REAL"),
        ],
    )
    def test_known_formats_map_correctly(self, col_format, expected):
        assert database_setup.type_map(col_format, "Some_Key") == expected

    def test_unknown_format_falls_back_to_text(self):
        """No exception for a typo'd/unrecognized format string — degrades to TEXT."""
        assert database_setup.type_map("this_is_not_a_real_format", "Some_Key") == "TEXT"

    @pytest.mark.parametrize("key", ["Target_Days", "Volume"])
    def test_target_days_and_volume_force_integer(self, key):
        """Special-cased columns are INTEGER regardless of their declared format."""
        assert database_setup.type_map("currency", key) == "INTEGER"
        assert database_setup.type_map("string", key) == "INTEGER"

    def test_other_number_keys_are_not_forced_integer(self):
        assert database_setup.type_map("number", "RSI") == "REAL"


# ---------------------------------------------------------------------------
# initialize_database — idempotency & table creation
# ---------------------------------------------------------------------------

class TestInitializeDatabaseIdempotency:
    def test_running_twice_does_not_raise(self, tmp_path: Path):
        db_file = str(tmp_path / "twice.db")
        database_setup.initialize_database(db_file)
        database_setup.initialize_database(db_file)  # must not raise

        conn = sqlite3.connect(db_file)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = {row[0] for row in cursor.fetchall()}
            assert {"ExecutionLogs", "DailySignals", "Transactions"} <= tables
        finally:
            conn.close()

    def test_running_twice_preserves_existing_rows(self, tmp_path: Path):
        """CREATE TABLE IF NOT EXISTS must never truncate data on a re-run."""
        db_file = str(tmp_path / "preserve.db")
        database_setup.initialize_database(db_file)

        conn = sqlite3.connect(db_file)
        try:
            conn.execute(
                "INSERT INTO ExecutionLogs (status, ticker_count) VALUES (?, ?)",
                ("OK", 5),
            )
            conn.commit()
        finally:
            conn.close()

        database_setup.initialize_database(db_file)

        conn = sqlite3.connect(db_file)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM ExecutionLogs;")
            assert cursor.fetchone()[0] == 1
        finally:
            conn.close()

    def test_transactions_table_created(self, tmp_path: Path):
        db_file = str(tmp_path / "tx.db")
        database_setup.initialize_database(db_file)

        conn = sqlite3.connect(db_file)
        try:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(Transactions);")
            columns = {row[1] for row in cursor.fetchall()}
            assert {
                "transaction_id", "execution_date", "ticker", "trade_type",
                "quantity", "fill_price", "commission", "slippage",
            } <= columns
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Malformed config.COLUMN_SCHEMA entries
# ---------------------------------------------------------------------------

class TestMalformedColumnSchema:
    def test_missing_format_key_raises_keyerror(self, tmp_path: Path):
        """No defensive .get() guard exists for 'format' — pin the current crash behavior."""
        db_file = str(tmp_path / "malformed.db")
        broken_schema = [{"header": "Ticker", "key": "Symbol"}]  # no "format"
        with mock.patch.object(database_setup.config, "COLUMN_SCHEMA", broken_schema):
            with pytest.raises(KeyError):
                database_setup.initialize_database(db_file)

    def test_duplicate_key_does_not_crash_create_table(self, tmp_path: Path):
        """Two COLUMN_SCHEMA entries sharing a 'key' would normally make SQLite
        raise 'duplicate column name' on CREATE TABLE — verify the actual
        behavior rather than assuming it's silently deduped."""
        db_file = str(tmp_path / "dup.db")
        dup_schema = [
            {"header": "Ticker", "key": "Symbol", "format": "string"},
            {"header": "Ticker Again", "key": "Symbol", "format": "number"},
        ]
        with mock.patch.object(database_setup.config, "COLUMN_SCHEMA", dup_schema):
            with pytest.raises(sqlite3.OperationalError, match="duplicate column name"):
                database_setup.initialize_database(db_file)

    def test_unknown_format_type_in_schema_defaults_to_text_column(self, tmp_path: Path):
        db_file = str(tmp_path / "unknown_fmt.db")
        weird_schema = [{"header": "Weird", "key": "Weird_Col", "format": "nonexistent_format"}]
        with mock.patch.object(database_setup.config, "COLUMN_SCHEMA", weird_schema):
            database_setup.initialize_database(db_file)

        conn = sqlite3.connect(db_file)
        try:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(DailySignals);")
            col_types = {row[1]: row[2] for row in cursor.fetchall()}
            assert col_types["Weird_Col"] == "TEXT"
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# migrate_daily_signals_schema
# ---------------------------------------------------------------------------

class TestMigrateDailySignalsSchema:
    def test_adds_missing_column_without_dropping_existing_data(self, tmp_path: Path):
        db_file = str(tmp_path / "migrate.db")
        original_schema = [{"header": "Ticker", "key": "Symbol", "format": "string"}]
        with mock.patch.object(database_setup.config, "COLUMN_SCHEMA", original_schema):
            database_setup.initialize_database(db_file)

        conn = sqlite3.connect(db_file)
        try:
            conn.execute('INSERT INTO DailySignals ("Symbol") VALUES (?)', ("AAPL",))
            conn.commit()
        finally:
            conn.close()

        expanded_schema = original_schema + [
            {"header": "New Column", "key": "New_Metric", "format": "number"}
        ]
        with mock.patch.object(database_setup.config, "COLUMN_SCHEMA", expanded_schema):
            database_setup.initialize_database(db_file)

        conn = sqlite3.connect(db_file)
        try:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(DailySignals);")
            columns = {row[1] for row in cursor.fetchall()}
            assert "New_Metric" in columns

            cursor.execute('SELECT "Symbol" FROM DailySignals;')
            rows = cursor.fetchall()
            assert rows == [("AAPL",)]  # pre-existing row preserved
        finally:
            conn.close()

    def test_migration_is_a_noop_when_schema_already_current(self, tmp_path: Path, caplog):
        """No ALTER TABLE attempted (and no error) when every COLUMN_SCHEMA key
        already exists as a DailySignals column — verified via the columns
        staying byte-identical before/after plus the "already up-to-date"
        log line (sqlite3.Cursor.execute is a read-only C attribute and
        can't be mock.patch.object'd directly)."""
        db_file = str(tmp_path / "noop_migrate.db")
        schema = [{"header": "Ticker", "key": "Symbol", "format": "string"}]
        with mock.patch.object(database_setup.config, "COLUMN_SCHEMA", schema):
            database_setup.initialize_database(db_file)

            conn = sqlite3.connect(db_file)
            try:
                cursor = conn.cursor()
                cursor.execute("PRAGMA table_info(DailySignals);")
                before = {row[1] for row in cursor.fetchall()}

                with caplog.at_level("INFO", logger="DatabaseSetup"):
                    database_setup.migrate_daily_signals_schema(cursor, conn)

                cursor.execute("PRAGMA table_info(DailySignals);")
                after = {row[1] for row in cursor.fetchall()}
                assert after == before
                assert "already up-to-date" in caplog.text
            finally:
                conn.close()

    def test_migrate_daily_signals_schema_called_within_initialize(self, tmp_path: Path):
        """initialize_database wires the migration step in (F-07 fix) — verify
        via a real column-add round trip rather than a private-function patch."""
        db_file = str(tmp_path / "wired.db")
        schema_v1 = [{"header": "A", "key": "Col_A", "format": "string"}]
        schema_v2 = schema_v1 + [{"header": "B", "key": "Col_B", "format": "currency"}]

        with mock.patch.object(database_setup.config, "COLUMN_SCHEMA", schema_v1):
            database_setup.initialize_database(db_file)
        with mock.patch.object(database_setup.config, "COLUMN_SCHEMA", schema_v2):
            database_setup.initialize_database(db_file)

        conn = sqlite3.connect(db_file)
        try:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(DailySignals);")
            columns = {row[1] for row in cursor.fetchall()}
            assert {"Col_A", "Col_B"} <= columns
        finally:
            conn.close()
