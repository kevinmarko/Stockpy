"""tests/test_observability_telemetry.py — Health-tab helpers coverage.

Exercises ``gui/observability_telemetry.py`` without Streamlit. Three groups
mirror the module's three surfaces:

1.  ``collect_system_telemetry`` + ``format_bytes`` — shape and degraded path.
2.  ``LatencySampleStore`` + ``summarise_latency`` — record, roll-off, summary.
3.  ``parse_log_lines`` + ``filter_log_entries`` + ``tally_levels`` — formatter
    round-trip + level ordinal filter + traceback-continuation preservation.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gui import observability_telemetry as ot


# ===========================================================================
# 1. System telemetry
# ===========================================================================

class TestSystemTelemetry:
    def test_happy_shape(self) -> None:
        """With psutil installed, telemetry has finite host metrics."""
        t = ot.collect_system_telemetry()
        if not t.psutil_available:
            pytest.skip("psutil not installed in this environment")
        assert 0.0 <= t.cpu_percent <= 100.0 + 1e-6
        assert 0.0 <= t.memory_percent <= 100.0
        assert 0.0 <= t.disk_percent <= 100.0
        assert t.memory_total_bytes > 0
        assert t.disk_total_bytes > 0
        assert t.process_rss_bytes > 0
        assert t.sampled_at.tzinfo is not None  # UTC-aware

    def test_psutil_missing_returns_nan(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Module-level ImportError fallback path produces NaN-shaped output."""
        # Force the lazy import inside collect_system_telemetry to fail.
        import builtins
        real_import = builtins.__import__

        def fake_import(name: str, *a, **kw):
            if name == "psutil":
                raise ImportError("simulated missing psutil")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        t = ot.collect_system_telemetry()
        assert t.psutil_available is False
        assert math.isnan(t.cpu_percent)
        assert math.isnan(t.memory_percent)
        assert math.isnan(t.disk_percent)
        assert t.memory_total_bytes == -1
        assert t.process_rss_bytes == -1

    @pytest.mark.parametrize("n,expected_unit", [
        (0, "B"),
        (1023, "B"),
        (1024, "KiB"),
        (1024 ** 2, "MiB"),
        (1024 ** 3, "GiB"),
    ])
    def test_format_bytes_units(self, n: int, expected_unit: str) -> None:
        assert expected_unit in ot.format_bytes(n)

    def test_format_bytes_negative_dash(self) -> None:
        assert ot.format_bytes(-1) == "—"


# ===========================================================================
# 2. Latency store
# ===========================================================================

class TestLatencySampleStore:
    def test_record_and_compute_latency(self) -> None:
        store = ot.LatencySampleStore(max_samples=5)
        q_ts = datetime(2026, 6, 26, 12, 0, 0, tzinfo=timezone.utc)
        ing = datetime(2026, 6, 26, 12, 0, 5, tzinfo=timezone.utc)
        sample = store.record("AAPL", "alpaca", q_ts, ingested_at=ing)
        assert sample.symbol == "AAPL"
        assert sample.latency_seconds == pytest.approx(5.0)
        assert len(store) == 1

    def test_naive_timestamps_promoted_to_utc(self) -> None:
        store = ot.LatencySampleStore()
        q_naive = datetime(2026, 6, 26, 12, 0, 0)  # no tzinfo
        sample = store.record("AAPL", "yfinance", q_naive)
        assert sample.quote_timestamp.tzinfo is not None
        assert sample.ingested_at.tzinfo is not None

    def test_max_samples_roll_off(self) -> None:
        store = ot.LatencySampleStore(max_samples=3)
        base = datetime(2026, 6, 26, tzinfo=timezone.utc)
        for i in range(5):
            store.record(f"S{i}", "yfinance",
                         base + timedelta(seconds=i),
                         ingested_at=base + timedelta(seconds=i + 1))
        samples = store.samples()
        assert len(samples) == 3
        # Oldest entries should have been evicted; symbols are S2, S3, S4.
        assert [s.symbol for s in samples] == ["S2", "S3", "S4"]

    def test_clear_empties(self) -> None:
        store = ot.LatencySampleStore()
        store.record("AAPL", "alpaca",
                     datetime.now(timezone.utc))
        assert len(store) == 1
        store.clear()
        assert len(store) == 0

    def test_invalid_capacity(self) -> None:
        with pytest.raises(ValueError):
            ot.LatencySampleStore(max_samples=0)

    def test_summarise_latency_empty(self) -> None:
        s = ot.summarise_latency([])
        assert s["count"] == 0
        assert math.isnan(s["p50"])
        assert s["worst_symbol"] is None

    def test_summarise_latency_picks_worst(self) -> None:
        store = ot.LatencySampleStore()
        base = datetime(2026, 6, 26, tzinfo=timezone.utc)
        # AAPL: latencies 1, 1, 1; MSFT: 60, 70, 80 — MSFT should be worst.
        for lat in (1, 1, 1):
            store.record("AAPL", "alpaca", base,
                         ingested_at=base + timedelta(seconds=lat))
        for lat in (60, 70, 80):
            store.record("MSFT", "alpaca", base,
                         ingested_at=base + timedelta(seconds=lat))
        summary = ot.summarise_latency(store.samples())
        assert summary["count"] == 6
        assert summary["worst_symbol"] == "MSFT"
        assert summary["worst_p95"] >= 60.0


# ===========================================================================
# 3. Log parsing / filtering
# ===========================================================================

_FORMAT_LINE = (
    "{ts}  {lvl:<8}  {name} — {msg}"
)


def _make_log(ts: str, lvl: str, name: str, msg: str) -> str:
    return _FORMAT_LINE.format(ts=ts, lvl=lvl, name=name, msg=msg)


class TestParseLogLines:
    def test_round_trip_canonical_line(self) -> None:
        line = _make_log("2026-06-26 08:40:28,615", "INFO",
                         "engine.advisory", "Pipeline complete")
        [entry] = ot.parse_log_lines([line])
        assert entry.parsed is True
        assert entry.level == "INFO"
        assert entry.logger_name == "engine.advisory"
        assert entry.message == "Pipeline complete"
        assert entry.timestamp is not None
        assert entry.timestamp.year == 2026

    def test_all_levels_parse(self) -> None:
        lines = [
            _make_log("2026-06-26 08:40:28,615", lvl,
                     "x.y", f"line {lvl}")
            for lvl in ot.VALID_LEVELS
        ]
        entries = ot.parse_log_lines(lines)
        assert [e.level for e in entries] == list(ot.VALID_LEVELS)
        assert all(e.parsed for e in entries)

    def test_traceback_continuation_kept_unparsed(self) -> None:
        lines = [
            _make_log("2026-06-26 08:40:28,615", "ERROR",
                     "main", "boom"),
            "Traceback (most recent call last):",
            '  File "x.py", line 1, in <module>',
        ]
        entries = ot.parse_log_lines(lines)
        assert len(entries) == 3
        assert entries[0].parsed is True
        assert entries[1].parsed is False
        assert entries[2].parsed is False
        # Raw text preserved verbatim.
        assert entries[1].raw == "Traceback (most recent call last):"

    def test_blank_lines_skipped(self) -> None:
        entries = ot.parse_log_lines(["", "\n", ""])
        assert entries == []


class TestFilterLogEntries:
    def test_filter_threshold(self) -> None:
        lines = [
            _make_log("2026-06-26 08:40:28,615", "INFO", "x", "i1"),
            _make_log("2026-06-26 08:40:29,615", "WARNING", "x", "w1"),
            _make_log("2026-06-26 08:40:30,615", "ERROR", "x", "e1"),
            _make_log("2026-06-26 08:40:31,615", "CRITICAL", "x", "c1"),
        ]
        entries = ot.parse_log_lines(lines)
        kept = ot.filter_log_entries(entries, min_level="WARNING")
        kept_msgs = [e.message for e in kept]
        assert "i1" not in kept_msgs
        assert {"w1", "e1", "c1"} <= set(kept_msgs)

    def test_filter_substring_case_insensitive(self) -> None:
        lines = [
            _make_log("2026-06-26 08:40:28,615", "ERROR", "broker", "AAPL fill"),
            _make_log("2026-06-26 08:40:29,615", "ERROR", "broker", "MSFT fill"),
        ]
        entries = ot.parse_log_lines(lines)
        kept = ot.filter_log_entries(entries, min_level="INFO", contains="aapl")
        assert len(kept) == 1
        assert "AAPL" in kept[0].message

    def test_unparsed_lines_kept_when_filtering(self) -> None:
        """Tracebacks must survive a min_level filter so context isn't lost."""
        lines = [
            _make_log("2026-06-26 08:40:30,615", "ERROR", "x", "boom"),
            "  File 'x.py', line 1, in <module>",
        ]
        entries = ot.parse_log_lines(lines)
        kept = ot.filter_log_entries(entries, min_level="CRITICAL")
        # CRITICAL drops the ERROR, but the traceback continuation stays.
        assert any(not e.parsed for e in kept)

    def test_invalid_min_level_rejected(self) -> None:
        with pytest.raises(ValueError):
            ot.filter_log_entries([], min_level="VERBOSE")  # type: ignore[arg-type]


class TestTallyAndIO:
    def test_tally_counts_levels(self) -> None:
        lines = [
            _make_log("2026-06-26 08:40:28,615", "INFO", "x", "i"),
            _make_log("2026-06-26 08:40:28,615", "INFO", "x", "i"),
            _make_log("2026-06-26 08:40:28,615", "ERROR", "x", "e"),
            "  unparsed continuation",
        ]
        entries = ot.parse_log_lines(lines)
        tally = ot.tally_levels(entries)
        assert tally["INFO"] == 2
        assert tally["ERROR"] == 1
        assert tally["UNPARSED"] == 1

    def test_read_log_tail_missing(self, tmp_path: Path) -> None:
        assert ot.read_log_tail(tmp_path / "nope.log") == []

    def test_read_log_tail_returns_last_n(self, tmp_path: Path) -> None:
        path = tmp_path / "x.log"
        path.write_text("\n".join(f"line {i}" for i in range(10)) + "\n")
        tail = ot.read_log_tail(path, max_lines=3)
        assert [line.rstrip() for line in tail] == ["line 7", "line 8", "line 9"]
