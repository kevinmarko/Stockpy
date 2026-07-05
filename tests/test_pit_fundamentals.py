"""
tests/test_pit_fundamentals.py
===============================
Unit tests for ``validation/pit_fundamentals.py`` — the point-in-time (PIT)
fundamentals audit that checks whether fundamentals used in a historical
decision were genuinely public knowledge at that decision date.

Coverage:
  - A report_date on/before the decision date -> PASS.
  - A report_date after the decision date -> FAIL (look-ahead detected).
  - No usable date field at all -> UNVERIFIABLE (fail-closed, never a
    silent pass).
  - Dead-letter resilience: an exception during evaluation still returns a
    PITAuditResult, never raises (CONSTRAINT #6).
  - epoch-seconds (yfinance mostRecentQuarter/lastFiscalYearEnd) and ISO
    string report dates are both parsed correctly.
  - HistoricalStore integration: report_date column round-trip and the
    audit_from_historical_store() convenience wrapper.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone

import pandas as pd
import pytest

from validation.pit_fundamentals import (
    PITAuditResult,
    REPORT_DATE_KEYS,
    audit_fundamentals_snapshot,
    audit_from_historical_store,
    format_pit_audit_summary,
)


def _epoch(d: date) -> float:
    """Convert a date to UTC midnight epoch seconds (yfinance convention)."""
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()


# ─────────────────────────────────────────────────────────────────────────────
# Core verdict logic
# ─────────────────────────────────────────────────────────────────────────────

class TestPassVerdict:
    def test_report_date_before_decision_date_passes(self):
        payload = {"mostRecentQuarter": _epoch(date(2024, 1, 1))}
        result = audit_fundamentals_snapshot("AAPL", "2024-02-01", payload)
        assert result.verdict == "PASS"
        assert result.passed is True
        assert result.report_date == "2024-01-01"
        assert result.report_date_source_key == "mostRecentQuarter"

    def test_report_date_equal_to_decision_date_passes(self):
        """The boundary case: report_date == decision_date is knowable
        (public as of that date), not look-ahead."""
        payload = {"mostRecentQuarter": _epoch(date(2024, 2, 1))}
        result = audit_fundamentals_snapshot("AAPL", "2024-02-01", payload)
        assert result.verdict == "PASS"

    def test_iso_string_report_date_field_passes(self):
        """Platform-added report_date column (persisted by HistoricalStore)
        is an ISO date string, not an epoch timestamp."""
        payload = {"report_date": "2024-01-15"}
        result = audit_fundamentals_snapshot("MSFT", "2024-02-01", payload)
        assert result.verdict == "PASS"
        assert result.report_date_source_key == "report_date"


class TestFailVerdict:
    def test_report_date_after_decision_date_fails(self):
        """The core look-ahead case: fundamentals reflect a quarter reported
        AFTER the decision date -> the strategy could not have known this."""
        payload = {"mostRecentQuarter": _epoch(date(2024, 3, 1))}
        result = audit_fundamentals_snapshot("AAPL", "2024-02-01", payload)
        assert result.verdict == "FAIL"
        assert result.passed is False
        assert "look-ahead" in result.reason.lower() or "after" in result.reason.lower()

    def test_last_fiscal_year_end_after_decision_fails(self):
        payload = {"lastFiscalYearEnd": _epoch(date(2024, 12, 31))}
        result = audit_fundamentals_snapshot("XOM", "2024-06-01", payload)
        assert result.verdict == "FAIL"


class TestUnverifiableVerdict:
    def test_no_date_field_at_all_is_unverifiable_not_passed(self):
        """Missing date info must FAIL CLOSED as unverifiable, never silently
        pass — this is the crux of CONSTRAINT #4's spirit applied here."""
        payload = {"trailingPE": 25.0, "priceToBook": 5.0}
        result = audit_fundamentals_snapshot("GOOG", "2024-02-01", payload)
        assert result.verdict == "UNVERIFIABLE"
        assert result.passed is False
        assert result.report_date is None

    def test_empty_payload_is_unverifiable(self):
        result = audit_fundamentals_snapshot("TSLA", "2024-02-01", {})
        assert result.verdict == "UNVERIFIABLE"
        assert result.passed is False

    def test_none_payload_is_unverifiable(self):
        result = audit_fundamentals_snapshot("TSLA", "2024-02-01", None)
        assert result.verdict == "UNVERIFIABLE"
        assert result.passed is False

    def test_unverifiable_never_silently_treated_as_pass(self):
        """Regression guard: passed must be strictly False for UNVERIFIABLE,
        distinguishing it from an accidental default-True verdict."""
        result = audit_fundamentals_snapshot("NVDA", "2024-02-01", {"foo": "bar"})
        assert result.verdict != "PASS"
        assert result.passed is False


class TestDeadLetterResilience:
    def test_unparsable_decision_date_returns_result_not_raise(self):
        """An exception during date coercion must degrade to a FAILED
        result, never propagate (CONSTRAINT #6)."""
        result = audit_fundamentals_snapshot(
            "AAPL", "not-a-real-date-at-all", {"mostRecentQuarter": _epoch(date(2024, 1, 1))}
        )
        assert isinstance(result, PITAuditResult)
        assert result.passed is False
        assert result.error is not None

    def test_malformed_report_date_value_does_not_raise(self):
        """A malformed value under a known key should be skipped (falls
        through to UNVERIFIABLE), not raise."""
        payload = {"mostRecentQuarter": "not-a-number-or-date"}
        result = audit_fundamentals_snapshot("AAPL", "2024-02-01", payload)
        assert isinstance(result, PITAuditResult)
        assert result.verdict in ("UNVERIFIABLE", "FAIL")

    def test_exception_inside_extraction_is_caught(self, monkeypatch):
        """Force an exception deep inside the audit path and confirm the
        function still returns a well-formed PITAuditResult rather than
        raising up to the caller."""
        import validation.pit_fundamentals as pf

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated failure")

        monkeypatch.setattr(pf, "_extract_report_date", _boom)
        result = pf.audit_fundamentals_snapshot(
            "AAPL", "2024-02-01", {"mostRecentQuarter": 123456}
        )
        assert isinstance(result, PITAuditResult)
        assert result.passed is False
        assert result.error is not None


class TestDecisionDateTypes:
    """audit_fundamentals_snapshot accepts str / date / datetime / Timestamp."""

    def test_accepts_date_object(self):
        payload = {"mostRecentQuarter": _epoch(date(2024, 1, 1))}
        result = audit_fundamentals_snapshot("AAPL", date(2024, 2, 1), payload)
        assert result.verdict == "PASS"

    def test_accepts_datetime_object(self):
        payload = {"mostRecentQuarter": _epoch(date(2024, 1, 1))}
        result = audit_fundamentals_snapshot("AAPL", datetime(2024, 2, 1, 9, 30), payload)
        assert result.verdict == "PASS"

    def test_accepts_pandas_timestamp(self):
        payload = {"mostRecentQuarter": _epoch(date(2024, 1, 1))}
        result = audit_fundamentals_snapshot("AAPL", pd.Timestamp("2024-02-01"), payload)
        assert result.verdict == "PASS"


class TestFieldsChecked:
    def test_fields_checked_carried_through(self):
        payload = {"mostRecentQuarter": _epoch(date(2024, 1, 1))}
        result = audit_fundamentals_snapshot(
            "AAPL", "2024-02-01", payload, fields_checked=["pe_ratio", "eps"]
        )
        assert result.fields_checked == ["pe_ratio", "eps"]

    def test_fields_checked_defaults_to_empty_list(self):
        result = audit_fundamentals_snapshot("AAPL", "2024-02-01", {})
        assert result.fields_checked == []


class TestFormatSummary:
    def test_format_summary_handles_empty(self):
        out = format_pit_audit_summary([])
        assert "NO RESULTS" in out

    def test_format_summary_counts_verdicts(self):
        results = [
            audit_fundamentals_snapshot("AAPL", "2024-02-01", {"mostRecentQuarter": _epoch(date(2024, 1, 1))}),
            audit_fundamentals_snapshot("MSFT", "2024-02-01", {"mostRecentQuarter": _epoch(date(2024, 3, 1))}),
            audit_fundamentals_snapshot("GOOG", "2024-02-01", {}),
        ]
        out = format_pit_audit_summary(results)
        assert "1 PASS" in out
        assert "1 FAIL" in out
        assert "1 UNVERIFIABLE" in out


# ─────────────────────────────────────────────────────────────────────────────
# HistoricalStore integration
# ─────────────────────────────────────────────────────────────────────────────

class TestHistoricalStoreIntegration:
    """Verifies the additive report_date column round-trips through
    HistoricalStore and that audit_from_historical_store() consumes it
    correctly. All tests use a temp on-disk SQLite DB (tmp_path)."""

    def _make_store(self, tmp_path):
        from data.historical_store import HistoricalStore
        return HistoricalStore(db_path=str(tmp_path / "test_pit.db"))

    def test_report_date_column_exists_on_fresh_db(self, tmp_path):
        store = self._make_store(tmp_path)
        import sqlite3
        conn = sqlite3.connect(str(tmp_path / "test_pit.db"))
        cols = {row[1] for row in conn.execute("PRAGMA table_info(fundamentals_history)").fetchall()}
        conn.close()
        assert "report_date" in cols

    def test_migration_adds_column_to_legacy_db(self, tmp_path):
        """Simulate a pre-existing DB created before report_date existed by
        building the table without it, then constructing HistoricalStore
        against the same path and confirming the column appears."""
        import sqlite3
        from data.historical_store import HistoricalStore

        db_path = str(tmp_path / "legacy.db")
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE fundamentals_history (
                symbol TEXT NOT NULL,
                as_of TEXT NOT NULL,
                pe_ratio REAL, pb_ratio REAL, roe REAL, dividend_yield REAL,
                market_cap REAL, eps REAL, operating_margin REAL,
                debt_to_equity REAL, raw_json TEXT,
                source TEXT NOT NULL, fetched_at TEXT NOT NULL,
                PRIMARY KEY (symbol, as_of)
            )
            """
        )
        conn.commit()
        conn.close()

        # Constructing HistoricalStore against this path should migrate it.
        store = HistoricalStore(db_path=db_path)

        conn2 = sqlite3.connect(db_path)
        cols = {row[1] for row in conn2.execute("PRAGMA table_info(fundamentals_history)").fetchall()}
        conn2.close()
        assert "report_date" in cols

    def test_upsert_persists_report_date_from_raw_payload(self, tmp_path):
        store = self._make_store(tmp_path)
        raw = {"trailingPE": 20.0, "mostRecentQuarter": _epoch(date(2024, 1, 15))}
        typed = {"pe_ratio": 20.0}
        store._upsert_fundamentals("AAPL", typed, raw, source="test")

        stored = store._read_fundamentals_report_date("AAPL")
        assert stored == "2024-01-15"

    def test_upsert_with_no_date_field_stores_none(self, tmp_path):
        store = self._make_store(tmp_path)
        raw = {"trailingPE": 20.0}
        typed = {"pe_ratio": 20.0}
        store._upsert_fundamentals("MSFT", typed, raw, source="test")

        stored = store._read_fundamentals_report_date("MSFT")
        assert stored is None

    def test_audit_from_historical_store_pass(self, tmp_path):
        store = self._make_store(tmp_path)
        raw = {"mostRecentQuarter": _epoch(date(2024, 1, 1))}
        store._upsert_fundamentals("AAPL", {"pe_ratio": 20.0}, raw, source="test")

        result = audit_from_historical_store(store, "AAPL", "2024-02-01")
        assert result.verdict == "PASS"

    def test_audit_from_historical_store_fail(self, tmp_path):
        store = self._make_store(tmp_path)
        raw = {"mostRecentQuarter": _epoch(date(2024, 6, 1))}
        store._upsert_fundamentals("AAPL", {"pe_ratio": 20.0}, raw, source="test")

        result = audit_from_historical_store(store, "AAPL", "2024-02-01")
        assert result.verdict == "FAIL"

    def test_audit_from_historical_store_missing_row_is_unverifiable(self, tmp_path):
        store = self._make_store(tmp_path)
        result = audit_from_historical_store(store, "ZZZZ", "2024-02-01")
        assert result.verdict == "UNVERIFIABLE"
        assert result.passed is False

    def test_audit_from_historical_store_dead_letter_on_db_error(self, tmp_path, monkeypatch):
        """If the DB read itself raises, the wrapper must still return a
        PITAuditResult rather than propagate (CONSTRAINT #6)."""
        store = self._make_store(tmp_path)

        def _boom(symbol):
            raise RuntimeError("simulated DB failure")

        monkeypatch.setattr(store, "_read_fundamentals_row", _boom)
        result = audit_from_historical_store(store, "AAPL", "2024-02-01")
        assert isinstance(result, PITAuditResult)
        assert result.passed is False
        assert result.error is not None

    def test_no_fabricated_report_date_missing_field(self, tmp_path):
        """CONSTRAINT #4 spirit: a payload without a date field must never
        result in a fabricated report_date — it stays None/UNVERIFIABLE."""
        store = self._make_store(tmp_path)
        raw = {"trailingPE": 15.0, "priceToBook": 3.0}
        store._upsert_fundamentals("IBM", {"pe_ratio": 15.0}, raw, source="test")

        result = audit_from_historical_store(store, "IBM", "2024-02-01")
        assert result.verdict == "UNVERIFIABLE"
        assert result.report_date is None
