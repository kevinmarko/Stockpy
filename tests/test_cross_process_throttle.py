"""
tests/test_cross_process_throttle.py
=====================================
Unit + real-multi-process tests for ``data/cross_process_throttle.py::wait_turn``,
the shared spacing primitive ``data/fmp_client.py``/``data/edgar_fundamentals.py``
now both call in addition to (not instead of) their existing in-process
``threading.Lock``-guarded throttles.

``TestRealMultiProcessSerialization`` is the load-bearing test here: a
thread-based test can only prove serialization WITHIN one process (which the
pre-existing ``threading.Lock`` already guaranteed) -- it cannot prove the
actual property this module exists to add, which is serialization ACROSS
independent OS processes. That test spawns real child ``python -c`` processes
via ``subprocess`` and inspects their real, separately-recorded issuance
timestamps.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from itertools import pairwise
from pathlib import Path

import pytest

from data.cross_process_throttle import wait_turn


class TestNoOpOnNonPositiveInterval:
    def test_zero_interval_touches_nothing(self, tmp_path):
        state_path = tmp_path / "sub" / "does_not_exist" / "x.state"
        wait_turn(state_path, 0.0)
        assert not state_path.parent.exists(), "min_interval<=0 must do zero file I/O"

    def test_negative_interval_touches_nothing(self, tmp_path):
        state_path = tmp_path / "x.state"
        wait_turn(state_path, -1.0)
        assert not state_path.exists()


class TestSpacingArithmetic:
    def test_first_call_never_sleeps(self, tmp_path):
        state_path = tmp_path / "x.state"
        t0 = time.perf_counter()
        wait_turn(state_path, 0.05)
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.05, "a fresh state file must not throttle the first call"
        assert state_path.exists()

    def test_second_rapid_call_sleeps_the_remaining_gap(self, tmp_path):
        state_path = tmp_path / "x.state"
        wait_turn(state_path, 0.15)
        t0 = time.perf_counter()
        wait_turn(state_path, 0.15)
        elapsed = time.perf_counter() - t0
        # 0.7x tolerance for scheduler jitter (matches the tolerance convention
        # already used by tests/test_edgar_fundamentals.py's own throttle test).
        assert elapsed >= 0.15 * 0.7, elapsed

    def test_state_file_persists_a_plausible_monotonic_timestamp(self, tmp_path):
        state_path = tmp_path / "x.state"
        before = time.monotonic()
        wait_turn(state_path, 0.01)
        after = time.monotonic()
        raw = state_path.read_text().strip()
        stamp = float(raw)
        assert before <= stamp <= after + 0.01


class TestCorruptStateDegradesGracefully:
    def test_garbage_content_is_treated_as_no_prior_request(self, tmp_path):
        state_path = tmp_path / "x.state"
        state_path.write_text("not-a-float garbage \x00\x01")
        t0 = time.perf_counter()
        wait_turn(state_path, 0.05)
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.05, "corrupt state must degrade to 'no throttle', never raise"

    def test_empty_file_is_treated_as_no_prior_request(self, tmp_path):
        state_path = tmp_path / "x.state"
        state_path.write_text("")
        t0 = time.perf_counter()
        wait_turn(state_path, 0.05)
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.05


class TestMissingFcntlDegradesToNoop:
    def test_no_fcntl_logs_once_and_never_raises(self, tmp_path, monkeypatch):
        import data.cross_process_throttle as mod

        monkeypatch.setattr(mod, "fcntl", None)
        monkeypatch.setattr(mod, "_no_fcntl_warned", False)
        state_path = tmp_path / "x.state"
        # Must not raise, must not create the file (early return before any I/O).
        wait_turn(state_path, 0.05)
        wait_turn(state_path, 0.05)
        assert not state_path.exists()


class TestUnwritableStateDirDegradesGracefully:
    def test_permission_error_on_mkdir_does_not_raise(self, tmp_path, monkeypatch):
        def _boom(*a, **k):
            raise OSError("permission denied (simulated)")

        monkeypatch.setattr(Path, "mkdir", _boom)
        # Should log a warning and return, never propagate.
        wait_turn(tmp_path / "sub" / "x.state", 0.05)


class TestThreadSerializationWithinOneProcess:
    def test_n_threads_against_same_path_are_spaced(self, tmp_path):
        """Sanity check that wait_turn is itself thread-safe (flock's exclusion
        also applies within one process, on top of the pre-existing
        threading.Lock the callers already have) -- not the load-bearing
        cross-process claim, see TestRealMultiProcessSerialization below."""
        state_path = tmp_path / "x.state"
        interval = 0.03
        issued: list[float] = []
        lock = threading.Lock()

        def worker():
            wait_turn(state_path, interval)
            with lock:
                issued.append(time.monotonic())

        n = 8
        threads = [threading.Thread(target=worker) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(issued) == n
        issued.sort()
        gaps = [b - a for a, b in pairwise(issued)]
        assert all(g >= interval * 0.7 for g in gaps), gaps


class TestRealMultiProcessSerialization:
    """The property thread-based tests cannot prove: two independent OS
    PROCESSES sharing one state file must jointly respect the interval, not
    each get their own separate budget -- which is exactly the production bug
    this module fixes (see the module docstring's
    docs/known_issues/xsec_universe_coverage_concurrency_variance.md
    cross-reference)."""

    @staticmethod
    def _child_script(state_path: str, out_path: str, interval: float, n_calls: int) -> str:
        repo_root = repr(str(Path(__file__).resolve().parent.parent))
        return (
            "import json, time, sys\n"
            f"sys.path.insert(0, {repo_root})\n"
            "from data.cross_process_throttle import wait_turn\n"
            "from pathlib import Path\n"
            "stamps = []\n"
            f"for _ in range({n_calls!r}):\n"
            f"    wait_turn(Path({state_path!r}), {interval!r})\n"
            "    stamps.append(time.monotonic())\n"
            f"Path({out_path!r}).write_text(json.dumps(stamps))\n"
        )

    def test_two_processes_jointly_respect_the_interval(self, tmp_path):
        state_path = tmp_path / "shared.state"
        out_a = tmp_path / "out_a.json"
        out_b = tmp_path / "out_b.json"
        interval = 0.08
        n_calls = 4

        script_a = self._child_script(str(state_path), str(out_a), interval, n_calls)
        script_b = self._child_script(str(state_path), str(out_b), interval, n_calls)

        proc_a = subprocess.Popen([sys.executable, "-c", script_a])
        proc_b = subprocess.Popen([sys.executable, "-c", script_b])
        ret_a = proc_a.wait(timeout=30)
        ret_b = proc_b.wait(timeout=30)
        assert ret_a == 0 and ret_b == 0

        stamps_a = json.loads(out_a.read_text())
        stamps_b = json.loads(out_b.read_text())
        assert len(stamps_a) == n_calls
        assert len(stamps_b) == n_calls

        combined = sorted(stamps_a + stamps_b)
        gaps = [b - a for a, b in pairwise(combined)]
        # The COMBINED issuance rate across both processes must respect the
        # interval -- this is the actual claim: two processes do NOT each get
        # their own independent `interval`-spaced budget (which would let
        # `combined` contain near-simultaneous pairs), they share ONE.
        assert all(g >= interval * 0.7 for g in gaps), (
            f"combined cross-process issuance was not correctly spaced: {gaps}"
        )
        # And the total wall-clock for 2*n_calls combined requests at one
        # shared `interval`-spaced budget must be at least (2*n_calls-1)*interval
        # -- i.e. genuinely NOT 2x the throughput either process would reach alone.
        total_span = combined[-1] - combined[0]
        assert total_span >= (2 * n_calls - 1) * interval * 0.7, total_span


@pytest.fixture(autouse=True)
def _no_real_leftover_state(tmp_path):
    """Nothing to clean up -- tmp_path is already per-test-isolated -- this
    fixture exists only to document explicitly that these tests never touch
    settings.LOCAL_DATA_ROOT (every test above passes its own tmp_path-derived
    state_path directly to wait_turn, never relying on a module-level default)."""
    yield
