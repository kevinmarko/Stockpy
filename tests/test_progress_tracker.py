"""
tests/test_progress_tracker.py
==============================
Unit tests for ``reporting/progress.py`` — the file-backed progress contract
that both the pipelines (writer) and the GUI (reader) depend on.

Coverage:
  * happy path — start → advance → finish yields a monotonically-rising percent
    and a terminal 100% snapshot;
  * percent formula — stage slices + within-stage symbol fraction, clamping;
  * edge — symbols_total=0 (no divide-by-zero, within=0);
  * dead-letter — missing/empty/corrupt file → read_progress None; unwritable
    output dir never raises;
  * thread-safety — 8 concurrent advance_symbol() calls, no lost updates.
"""

from __future__ import annotations

import json
import threading

import pytest

from reporting.progress import (
    ProgressReporter,
    ProgressState,
    compute_percent,
    read_progress,
    clear_progress,
    PROGRESS_FILENAME,
)

STAGES = ["data", "macro", "processing", "forecasting", "strategy", "execution"]


# --------------------------------------------------------------------------- #
# Percent formula                                                             #
# --------------------------------------------------------------------------- #
class TestComputePercent:
    def test_stage_slices_are_equal(self):
        # 6 stages → each stage boundary is a multiple of 100/6.
        assert compute_percent(0, 6, 0, 0) == pytest.approx(0.0)
        assert compute_percent(3, 6, 0, 0) == pytest.approx(50.0)
        assert compute_percent(6, 6, 0, 0) == pytest.approx(100.0)

    def test_within_stage_fraction(self):
        # Stage index 3 of 6 with half its symbols done → 50% + half of one slice.
        pct = compute_percent(3, 6, 12, 24)
        assert pct == pytest.approx(100.0 * (3 + 0.5) / 6)  # 58.33

    def test_symbols_total_zero_no_div_by_zero(self):
        assert compute_percent(2, 6, 0, 0) == pytest.approx(100.0 * 2 / 6)

    def test_stage_total_zero_returns_zero(self):
        assert compute_percent(0, 0, 0, 0) == 0.0

    def test_overshoot_is_clamped(self):
        # symbols_done > symbols_total must not push past the stage slice.
        assert compute_percent(5, 6, 99, 10) == pytest.approx(100.0)


# --------------------------------------------------------------------------- #
# Happy path                                                                  #
# --------------------------------------------------------------------------- #
class TestHappyPath:
    def test_start_advance_finish_monotonic(self, tmp_path):
        reporter = ProgressReporter(STAGES, run_id="run-1", output_dir=tmp_path)

        # Initial snapshot written on construction.
        s0 = read_progress(tmp_path)
        assert s0 is not None
        assert s0.state == "running"
        assert s0.run_id == "run-1"
        assert s0.stage_total == 6
        assert s0.percent == pytest.approx(0.0)

        percents = [s0.percent]

        reporter.start_stage("forecasting", symbols_total=4)
        percents.append(read_progress(tmp_path).percent)
        for i in range(4):
            reporter.advance_symbol(f"sym-{i}")
            percents.append(read_progress(tmp_path).percent)

        reporter.finish("succeeded")
        s_final = read_progress(tmp_path)
        percents.append(s_final.percent)

        # Monotonic non-decreasing.
        assert percents == sorted(percents), percents
        # Terminal snapshot pinned to 100% + succeeded.
        assert s_final.state == "succeeded"
        assert s_final.percent == pytest.approx(100.0)
        assert s_final.is_terminal is True

    def test_finish_failed_state(self, tmp_path):
        reporter = ProgressReporter(STAGES, output_dir=tmp_path)
        reporter.start_stage("data")
        reporter.finish("failed")
        s = read_progress(tmp_path)
        assert s.state == "failed"
        assert s.is_terminal is True
        # A failed run is NOT force-pinned to 100%.
        assert s.percent < 100.0

    def test_set_message_does_not_move_bar(self, tmp_path):
        reporter = ProgressReporter(STAGES, output_dir=tmp_path)
        reporter.start_stage("processing", symbols_total=10)
        reporter.advance_symbol()
        before = read_progress(tmp_path).percent
        reporter.set_message("still working…")
        after = read_progress(tmp_path)
        assert after.percent == pytest.approx(before)
        assert after.message == "still working…"

    def test_unknown_stage_keeps_ordinal_but_updates_label(self, tmp_path):
        reporter = ProgressReporter(STAGES, output_dir=tmp_path)
        reporter.start_stage("processing")  # index 2
        reporter.start_stage("totally-unknown")
        s = read_progress(tmp_path)
        assert s.stage == "totally-unknown"
        assert s.stage_index == 2  # unchanged — never rewinds the bar


# --------------------------------------------------------------------------- #
# read_progress + ProgressState                                               #
# --------------------------------------------------------------------------- #
class TestReadProgress:
    def test_missing_file_returns_none(self, tmp_path):
        assert read_progress(tmp_path) is None

    def test_empty_file_returns_none(self, tmp_path):
        (tmp_path / PROGRESS_FILENAME).write_text("   ", encoding="utf-8")
        assert read_progress(tmp_path) is None

    def test_corrupt_json_returns_none(self, tmp_path):
        (tmp_path / PROGRESS_FILENAME).write_text("{not json", encoding="utf-8")
        assert read_progress(tmp_path) is None

    def test_non_dict_json_returns_none(self, tmp_path):
        (tmp_path / PROGRESS_FILENAME).write_text("[1, 2, 3]", encoding="utf-8")
        assert read_progress(tmp_path) is None

    def test_malformed_fields_default_gracefully(self, tmp_path):
        (tmp_path / PROGRESS_FILENAME).write_text(
            json.dumps({"percent": "not-a-number", "stage_total": "x"}),
            encoding="utf-8",
        )
        s = read_progress(tmp_path)
        assert isinstance(s, ProgressState)
        assert s.percent == 0.0
        assert s.stage_total == 0

    def test_age_seconds_positive(self, tmp_path):
        ProgressReporter(STAGES, output_dir=tmp_path)
        s = read_progress(tmp_path)
        assert s.age_seconds() >= 0.0

    def test_clear_progress_removes_file(self, tmp_path):
        ProgressReporter(STAGES, output_dir=tmp_path)
        assert (tmp_path / PROGRESS_FILENAME).exists()
        clear_progress(tmp_path)
        assert not (tmp_path / PROGRESS_FILENAME).exists()
        assert read_progress(tmp_path) is None


# --------------------------------------------------------------------------- #
# Dead-letter: writes never raise                                             #
# --------------------------------------------------------------------------- #
class TestDeadLetter:
    def test_unwritable_output_dir_does_not_raise(self, tmp_path, monkeypatch):
        reporter = ProgressReporter(STAGES, output_dir=tmp_path)

        # Force every subsequent write to fail; the reporter must swallow it.
        def _boom(*_a, **_k):
            raise OSError("disk full")

        monkeypatch.setattr("reporting.progress.os.replace", _boom)
        # Should log-and-continue, never propagate.
        reporter.start_stage("macro")
        reporter.advance_symbol("x")
        reporter.finish("succeeded")

    def test_read_progress_never_raises_on_bad_dir(self, tmp_path):
        # Point at a path whose parent is a file (unreadable as a dir).
        bogus = tmp_path / "afile"
        bogus.write_text("x", encoding="utf-8")
        assert read_progress(bogus) is None


# --------------------------------------------------------------------------- #
# Thread-safety                                                               #
# --------------------------------------------------------------------------- #
class TestThreadSafety:
    def test_concurrent_advance_symbol_no_lost_updates(self, tmp_path):
        n_threads = 8
        reporter = ProgressReporter(STAGES, output_dir=tmp_path)
        reporter.start_stage("forecasting", symbols_total=n_threads)

        barrier = threading.Barrier(n_threads)

        def worker(i: int):
            barrier.wait()  # maximize contention
            reporter.advance_symbol(f"sym-{i}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        s = read_progress(tmp_path)
        assert s is not None
        assert s.symbols_done == n_threads  # exactly one increment per thread
        # Persisted percent is round(_, 2); compare with a matching tolerance.
        assert s.percent == pytest.approx(
            compute_percent(3, 6, n_threads, n_threads), abs=0.01
        )
