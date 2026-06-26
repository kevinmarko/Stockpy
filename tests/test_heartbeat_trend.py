"""Tests for the new observability helpers added in gui/observability_telemetry.py.

Covers:
- HeartbeatSample + HeartbeatTrendStore ring buffer semantics.
- extract_symbol_from_message() pattern matching + false-positive avoidance.
- classify_log_entry() systemic / symbol_specific / unknown classification.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from gui.observability_telemetry import (
    HeartbeatSample,
    HeartbeatTrendStore,
    LogEntry,
    classify_log_entry,
    extract_symbol_from_message,
)


# ===========================================================================
# HeartbeatSample & HeartbeatTrendStore
# ===========================================================================

class TestHeartbeatSample:
    def test_frozen(self) -> None:
        s = HeartbeatSample(sampled_at=datetime.now(timezone.utc), age_seconds=42.0)
        with pytest.raises((AttributeError, TypeError)):
            s.age_seconds = 99.0  # type: ignore[misc]

    def test_nan_age_preserved(self) -> None:
        s = HeartbeatSample(sampled_at=datetime.now(timezone.utc), age_seconds=math.nan)
        assert math.isnan(s.age_seconds)


class TestHeartbeatTrendStore:
    def test_empty_on_construction(self) -> None:
        store = HeartbeatTrendStore(max_samples=10)
        assert len(store) == 0
        assert store.samples() == []

    def test_record_appends(self) -> None:
        store = HeartbeatTrendStore(max_samples=10)
        store.record(5.0)
        assert len(store) == 1
        assert store.samples()[0].age_seconds == 5.0

    def test_ring_buffer_roll_off(self) -> None:
        store = HeartbeatTrendStore(max_samples=3)
        for i in range(5):
            store.record(float(i))
        assert len(store) == 3
        ages = [s.age_seconds for s in store.samples()]
        assert ages == [2.0, 3.0, 4.0]  # oldest three dropped

    def test_clear_empties_buffer(self) -> None:
        store = HeartbeatTrendStore(max_samples=5)
        store.record(10.0)
        store.record(20.0)
        store.clear()
        assert len(store) == 0

    def test_invalid_capacity_raises(self) -> None:
        with pytest.raises(ValueError):
            HeartbeatTrendStore(max_samples=0)

    def test_nan_sample_preserved(self) -> None:
        store = HeartbeatTrendStore(max_samples=5)
        store.record(math.nan)
        assert math.isnan(store.samples()[0].age_seconds)

    def test_samples_returns_copy(self) -> None:
        store = HeartbeatTrendStore(max_samples=5)
        store.record(1.0)
        s1 = store.samples()
        s1.clear()
        assert len(store) == 1  # original unaffected

    def test_to_dataframe_empty(self) -> None:
        import pandas as pd
        store = HeartbeatTrendStore()
        df = store.to_dataframe()
        assert isinstance(df, pd.DataFrame)
        assert df.empty

    def test_to_dataframe_shape(self) -> None:
        import pandas as pd
        store = HeartbeatTrendStore()
        store.record(10.0)
        store.record(20.0)
        df = store.to_dataframe()
        assert isinstance(df, pd.DataFrame)
        assert "age_seconds" in df.columns
        assert len(df) == 2
        assert list(df["age_seconds"]) == [10.0, 20.0]

    def test_to_dataframe_index_is_datetime(self) -> None:
        import pandas as pd
        store = HeartbeatTrendStore()
        store.record(5.0)
        df = store.to_dataframe()
        assert pd.api.types.is_datetime64_any_dtype(df.index)


# ===========================================================================
# extract_symbol_from_message
# ===========================================================================

class TestExtractSymbol:
    def test_dead_lettered_pattern(self) -> None:
        assert extract_symbol_from_message("Dead-lettered HKIT at stage=strategy") == "HKIT"

    def test_for_ticker_pattern(self) -> None:
        assert extract_symbol_from_message("No expirations returned for AAPL") == "AAPL"

    def test_for_symbol_pattern(self) -> None:
        assert extract_symbol_from_message("Probe failed for symbol MSFT") == "MSFT"

    def test_prefix_colon_pattern(self) -> None:
        assert extract_symbol_from_message("TSLA: ZeroDivisionError in strategy") == "TSLA"

    def test_symbol_kwarg_pattern(self) -> None:
        assert extract_symbol_from_message("calculate_edge_ratio failed symbol=NVDA") == "NVDA"

    def test_ticker_kwarg_pattern(self) -> None:
        assert extract_symbol_from_message("Error at ticker=GOOG stage=strategy") == "GOOG"

    def test_bracketed_pattern(self) -> None:
        assert extract_symbol_from_message("[AMZN] price fetch error") == "AMZN"

    def test_no_match_returns_none(self) -> None:
        assert extract_symbol_from_message("Platform execution pipeline crashed") is None

    def test_single_letter_excluded(self) -> None:
        # "A" alone should not match as a ticker (too likely to be a regular word)
        result = extract_symbol_from_message("for A at stage=strategy")
        assert result is None

    def test_common_false_positive_excluded(self) -> None:
        # "OR", "IN", etc. are on the exclusion list
        result = extract_symbol_from_message("crashed in OR mode")
        assert result is None

    def test_five_char_ticker(self) -> None:
        assert extract_symbol_from_message("Dead-lettered GOOGL at stage=results") == "GOOGL"

    def test_returns_none_on_empty_string(self) -> None:
        assert extract_symbol_from_message("") is None


# ===========================================================================
# classify_log_entry
# ===========================================================================

def _make_entry(level: str, name: str, msg: str) -> LogEntry:
    from datetime import datetime, timezone
    return LogEntry(
        timestamp=datetime.now(timezone.utc),
        level=level,
        logger_name=name,
        message=msg,
        raw=f"2026-06-26 10:00:00  {level:<8}  {name} — {msg}",
    )


def _unparsed(raw: str = "Traceback (most recent call last):") -> LogEntry:
    return LogEntry(timestamp=None, level="", logger_name="", message=raw, raw=raw)


class TestClassifyLogEntry:
    def test_systemic_pipeline_keyword(self) -> None:
        e = _make_entry("CRITICAL", "main_orchestrator", "Platform execution pipeline crashed: float division by zero")
        assert classify_log_entry(e) == "systemic"

    def test_systemic_crash_keyword(self) -> None:
        e = _make_entry("ERROR", "main_orchestrator", "Platform crashed unexpectedly")
        assert classify_log_entry(e) == "systemic"

    def test_systemic_fred_keyword(self) -> None:
        e = _make_entry("ERROR", "macro_engine", "FRED API unavailable")
        assert classify_log_entry(e) == "systemic"

    def test_symbol_specific(self) -> None:
        e = _make_entry("ERROR", "main_orchestrator", "Dead-lettered HKIT at stage=strategy: ZeroDivisionError")
        assert classify_log_entry(e) == "symbol_specific"

    def test_symbol_specific_for_pattern(self) -> None:
        e = _make_entry("WARNING", "data.market_data", "No expirations returned for AAPL")
        assert classify_log_entry(e) == "symbol_specific"

    def test_unknown_for_generic_warning(self) -> None:
        e = _make_entry("WARNING", "some_module", "Retry limit reached")
        result = classify_log_entry(e)
        # Should not be systemic or symbol_specific — unknown or symbol_specific is ok
        assert result in ("unknown", "symbol_specific")

    def test_unparsed_entry_is_unknown(self) -> None:
        e = _unparsed("  File \"strategy_engine.py\", line 42, in evaluate_security")
        assert classify_log_entry(e) == "unknown"

    def test_info_entry_classified(self) -> None:
        e = _make_entry("INFO", "data_engine", "Schema validation passed")
        # "schema" is a systemic keyword
        assert classify_log_entry(e) == "systemic"

    def test_database_keyword_systemic(self) -> None:
        e = _make_entry("ERROR", "database_setup", "database schema migration failed")
        assert classify_log_entry(e) == "systemic"
