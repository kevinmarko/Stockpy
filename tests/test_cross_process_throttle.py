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

Timing rule for this file: the throttle's contract is about ISSUANCE times --
the ``time.monotonic()`` value ``wait_turn`` persists at the moment it grants a
slot, while still holding the lock. How long the caller then takes to regain
control (scheduler jitter, GIL hand-off, a descheduled child process, a slow CI
runner) is not part of that contract, so no test here asserts on a duration
measured around a ``wait_turn`` call or on a timestamp taken after it returned.
Spacing arithmetic is checked against a fake clock (``fake_clock``: the sleep
the throttle REQUESTS, no wall clock involved); real-clock, thread and
multi-process tests read the issuance stamps back out at the grant point
(``_IssuanceRecorder``). The earlier caller-side measurements were the flake
source: two timing tests in this file failed in CI once and passed on re-run
with no code change.
"""
from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
import threading
import time
from itertools import pairwise
from pathlib import Path

import pytest

import data.cross_process_throttle as throttle_mod
from data.cross_process_throttle import wait_turn

# wait_turn persists each stamp as ``f"{time.monotonic():.6f}"``, so a recorded
# issuance time is quantized to 1us (rounding error <= 0.5us). That quantization
# is the ONLY slack the spacing assertions allow; they otherwise demand the full
# interval, because sleep(interval - elapsed) is anchored on the persisted stamp
# and time.sleep never returns early.
_STAMP_QUANTUM_SECONDS = 1e-6


class _FakeClock:
    """Stand-in for the ``time`` module inside ``data.cross_process_throttle``:
    ``sleep`` records the request and advances ``monotonic`` instead of blocking,
    so spacing arithmetic is exact and independent of machine load."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class _IssuanceRecorder:
    """Proxy for the throttle module's ``os`` that records every stamp at the
    moment ``wait_turn`` persists it. That write happens under the exclusive
    lock, right after the (possible) sleep, so it IS the issuance time -- and
    ``stamps`` is in grant order. Everything else delegates to the real ``os``.
    Self-contained on purpose: the multi-process test embeds its source in each
    child interpreter via ``inspect.getsource``."""

    def __init__(self, real_os):
        self._real_os = real_os
        self.stamps = []

    def pwrite(self, fd, data, offset):
        self.stamps.append(float(data.decode("ascii")))
        return self._real_os.pwrite(fd, data, offset)

    def __getattr__(self, name):
        return getattr(self._real_os, name)


@pytest.fixture
def fake_clock(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(throttle_mod, "time", clock)
    return clock


@pytest.fixture
def issuance(monkeypatch):
    recorder = _IssuanceRecorder(os)
    monkeypatch.setattr(throttle_mod, "os", recorder)
    return recorder


def _assert_spaced(stamps: list[float], interval: float) -> None:
    """Every consecutive pair of issuance stamps is at least ``interval`` apart."""
    gaps = [b - a for a, b in pairwise(stamps)]
    assert all(g >= interval - _STAMP_QUANTUM_SECONDS for g in gaps), (
        f"issuances closer together than the {interval}s interval: {gaps}"
    )


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
    def test_first_call_never_sleeps(self, tmp_path, fake_clock):
        state_path = tmp_path / "x.state"
        wait_turn(state_path, 0.05)
        assert fake_clock.sleeps == [], "a fresh state file must not throttle the first call"
        assert state_path.exists()

    def test_second_rapid_call_sleeps_the_remaining_gap(self, tmp_path, fake_clock):
        state_path = tmp_path / "x.state"
        interval = 0.15
        wait_turn(state_path, interval)
        first_issued = fake_clock.now
        fake_clock.now += 0.04  # the caller comes back 40ms after the first grant
        wait_turn(state_path, interval)
        # Exactly the part of the interval that has not yet elapsed, not a full
        # interval and not zero.
        assert fake_clock.sleeps == [pytest.approx(interval - 0.04, abs=1e-9)]
        second_issued = float(state_path.read_text().strip())
        assert second_issued - first_issued == pytest.approx(
            interval, abs=_STAMP_QUANTUM_SECONDS
        )

    def test_call_after_the_interval_has_passed_does_not_sleep(self, tmp_path, fake_clock):
        state_path = tmp_path / "x.state"
        wait_turn(state_path, 0.15)
        fake_clock.now += 0.2
        wait_turn(state_path, 0.15)
        assert fake_clock.sleeps == []

    def test_second_rapid_call_is_issued_a_full_interval_after_the_first(
        self, tmp_path, issuance
    ):
        """Real ``time.sleep``/``flock``/state file, asserting on the issuance
        stamps the throttle recorded at each grant -- not on how long the
        caller waited, which a descheduled test process stretches or shrinks."""
        state_path = tmp_path / "x.state"
        interval = 0.15
        wait_turn(state_path, interval)
        wait_turn(state_path, interval)
        assert len(issuance.stamps) == 2
        _assert_spaced(issuance.stamps, interval)

    def test_state_file_persists_a_plausible_monotonic_timestamp(self, tmp_path):
        state_path = tmp_path / "x.state"
        before = time.monotonic()
        wait_turn(state_path, 0.01)
        after = time.monotonic()
        raw = state_path.read_text().strip()
        stamp = float(raw)
        assert before <= stamp <= after + 0.01


class TestCorruptStateDegradesGracefully:
    def test_garbage_content_is_treated_as_no_prior_request(self, tmp_path, fake_clock):
        state_path = tmp_path / "x.state"
        state_path.write_text("not-a-float garbage \x00\x01")
        wait_turn(state_path, 0.05)
        assert fake_clock.sleeps == [], "corrupt state must degrade to 'no throttle', never raise"

    def test_empty_file_is_treated_as_no_prior_request(self, tmp_path, fake_clock):
        state_path = tmp_path / "x.state"
        state_path.write_text("")
        wait_turn(state_path, 0.05)
        assert fake_clock.sleeps == []


class TestStaleTimestampFromPriorBootSession:
    """Reproduces a real production incident: a state file written during a
    PRIOR boot session holds a ``time.monotonic()`` value that is larger than
    anything the current boot session's clock has reached yet (monotonic
    clocks reset to near-zero on reboot). Before the fix, this made
    ``elapsed = now - last`` deeply negative, and the resulting
    ``time.sleep(min_interval - elapsed)`` slept for the full (huge)
    magnitude of the gap -- multiple days in the wild -- WHILE HOLDING the
    exclusive cross-process lock, freezing every process on the machine that
    wanted to make an FMP/EDGAR request. See
    docs/known_issues/cross_process_throttle_monotonic_clock_reboot_reset.md."""

    def test_future_timestamp_does_not_hang_and_is_treated_as_no_prior_request(
        self, tmp_path, fake_clock
    ):
        state_path = tmp_path / "x.state"
        # Simulate a value written far into a previous boot session, i.e.
        # comfortably larger than the current session's time.monotonic().
        future_stamp = fake_clock.now + 900_000.0  # +10.4 days
        state_path.write_text(f"{future_stamp:.6f}")

        wait_turn(state_path, 0.05)

        # The fake sleep returns at once, so a regression shows up as a
        # recorded multi-day sleep REQUEST instead of a hung test.
        assert fake_clock.sleeps == [], (
            f"wait_turn asked to sleep {fake_clock.sleeps} against a stale "
            "future-looking timestamp instead of treating it as 'no prior request known'"
        )

    def test_future_timestamp_does_not_deadlock_a_second_waiting_process(self, tmp_path):
        """The load-bearing property: since the lock is held across the
        sleep, a second REAL process racing to acquire it must not be stuck
        behind the first one's (would-be) multi-day sleep either."""
        state_path = tmp_path / "shared.state"
        future_stamp = time.monotonic() + 900_000.0
        state_path.write_text(f"{future_stamp:.6f}")

        repo_root = repr(str(Path(__file__).resolve().parent.parent))
        script = (
            "import sys, time\n"
            f"sys.path.insert(0, {repo_root})\n"
            "from data.cross_process_throttle import wait_turn\n"
            "from pathlib import Path\n"
            f"wait_turn(Path({str(state_path)!r}), 0.05)\n"
            "print('done')\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", script], stdout=subprocess.PIPE, text=True
        )
        out, _ = proc.communicate(timeout=10)
        assert proc.returncode == 0
        assert out.strip() == "done"

    def test_writes_a_fresh_valid_stamp_after_recovering_from_a_stale_one(self, tmp_path):
        state_path = tmp_path / "x.state"
        future_stamp = time.monotonic() + 900_000.0
        state_path.write_text(f"{future_stamp:.6f}")

        before = time.monotonic()
        wait_turn(state_path, 0.01)
        after = time.monotonic()

        stamp = float(state_path.read_text().strip())
        assert before <= stamp <= after + 0.01, (
            "state file must be overwritten with a fresh, current-session "
            "timestamp, not left holding the stale pre-reboot value"
        )


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
    def test_n_threads_against_same_path_are_spaced(self, tmp_path, issuance):
        """Sanity check that wait_turn is itself thread-safe (flock's exclusion
        also applies within one process, on top of the pre-existing
        threading.Lock the callers already have) -- not the load-bearing
        cross-process claim, see TestRealMultiProcessSerialization below.

        Asserts on the stamps recorded at each grant, in grant order. A stamp
        taken by the worker after ``wait_turn`` returned would also absorb the
        GIL hand-off and scheduling delay of that thread, which is not part of
        the spacing guarantee."""
        state_path = tmp_path / "x.state"
        interval = 0.03
        n = 8
        threads = [
            threading.Thread(target=wait_turn, args=(state_path, interval))
            for _ in range(n)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(issuance.stamps) == n
        _assert_spaced(issuance.stamps, interval)


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
        # The child records each issuance stamp at the grant point (inside the
        # throttle, under the lock), not after wait_turn returns: a child that is
        # descheduled between the grant and its next line would otherwise report
        # a late stamp and fake a spacing violation.
        return (
            "import json, os, sys\n"
            f"sys.path.insert(0, {repo_root})\n"
            "import data.cross_process_throttle as throttle\n"
            "from pathlib import Path\n"
            + inspect.getsource(_IssuanceRecorder)
            + "recorder = _IssuanceRecorder(os)\n"
            "throttle.os = recorder\n"
            f"for _ in range({n_calls!r}):\n"
            f"    throttle.wait_turn(Path({state_path!r}), {interval!r})\n"
            f"Path({out_path!r}).write_text(json.dumps(recorder.stamps))\n"
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

        # CLOCK_MONOTONIC is kernel-wide, so the two processes' stamps are on
        # one timeline; sorting them yields the grant order.
        combined = sorted(stamps_a + stamps_b)
        # The COMBINED issuance rate across both processes must respect the
        # interval -- this is the actual claim: two processes do NOT each get
        # their own independent `interval`-spaced budget (which would let
        # `combined` contain near-simultaneous pairs), they share ONE.
        _assert_spaced(combined, interval)
        # And the span of 2*n_calls issuances at one shared `interval`-spaced
        # budget must be at least (2*n_calls-1)*interval -- i.e. genuinely NOT
        # 2x the throughput either process would reach alone.
        total_span = combined[-1] - combined[0]
        assert total_span >= (2 * n_calls - 1) * (interval - _STAMP_QUANTUM_SECONDS), total_span


@pytest.fixture(autouse=True)
def _no_real_leftover_state(tmp_path):
    """Nothing to clean up -- tmp_path is already per-test-isolated -- this
    fixture exists only to document explicitly that these tests never touch
    settings.LOCAL_DATA_ROOT (every test above passes its own tmp_path-derived
    state_path directly to wait_turn, never relying on a module-level default)."""
    yield
