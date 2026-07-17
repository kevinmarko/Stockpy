"""desktop/daemon_runtime.py
============================
Signal-agnostic run engine for the persistent orchestrator daemon.

Background
----------
``main_orchestrator.py`` traditionally ran as a fresh subprocess per cycle,
re-importing and re-constructing every heavy engine (ARIMA, HMM, GJR-GARCH,
etc.) every single time. Two prerequisite refactors on this branch made the
engines reusable across cycles:

* ``main_orchestrator.PipelineFatalError`` — raised (never ``sys.exit(1)``)
  on a fatal per-cycle failure, so a long-lived caller can catch it with a
  plain ``except Exception`` and keep running.
* ``main_orchestrator.EngineContext`` — a bag of pre-built engine instances,
  and ``main_orchestrator._main_body(..., engines=..., data_engine=...)``
  which runs ONE FULL CYCLE reusing whatever engines/data_engine are handed
  to it.

This module is the class that actually keeps those warm instances alive and
runs cycles against them: ``OrchestratorDaemon``. It owns:

* a thread-safe run state machine (single-flight — only one cycle in flight
  at a time),
* a background worker thread per triggered run,
* an optional interval timer thread that triggers a run on a cadence,
* a bounded, introspectable run history.

What it deliberately does NOT own: any `signal`/SIGTERM/process-lifecycle
handling, `os.fork`, or subprocess supervision. That is the separate concern
of the standalone entrypoint that wraps this class — this module must stay a
plain, importable, testable class with no OS-signal awareness at all.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import main_orchestrator
from settings import settings, validate_interval_seconds
from data_engine import DataEngine, MockDataEngine
from reporting.progress import read_progress

logger = logging.getLogger("OrchestratorDaemon")


class RunState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    state: RunState
    started_at: datetime            # UTC-aware
    finished_at: Optional[datetime]  # None while RUNNING
    duration_seconds: Optional[float]
    error: Optional[str]            # str(exception) on FAILED, else None
    reason: str                     # "manual" | "interval"
    # Which pipeline sub-run this cycle executed: "full" (whole cycle, the
    # default and every pre-existing caller), "data" (data-fetch stages only),
    # or "metrics" (data-fetch + indicator/forecast/signal precompute, no broker
    # execution / state-snapshot). Additive with a default so existing
    # RunRecord(...) constructions (e.g. tests/test_control_api.py) stay valid.
    mode: str = "full"
    # Progress instrumentation (reporting/progress.py) -- a plain-dict snapshot
    # of the pipeline's live 0-100% progress telemetry (output/progress.json)
    # taken at the moment this record is written (i.e. cycle completion; see
    # _run_one_cycle below). None when unavailable (no progress.json yet, or
    # the read itself failed) -- CONSTRAINT #4, never a fabricated snapshot.
    # This dict's "run_id" key is overwritten with THIS RunRecord's own run_id
    # by _run_one_cycle before the record is built -- main_orchestrator's
    # internally-constructed ProgressReporter has no notion of the daemon's
    # run_id, so progress.json on disk always carries "run_id": null; without
    # the override, RunRecord.progress["run_id"] would silently disagree with
    # RunRecord.run_id even though both describe the same cycle. Safe to
    # overwrite: the daemon is single-flight (one cycle at a time, lock-
    # enforced by trigger_run()), so the progress.json read here is always
    # this cycle's own terminal snapshot, never a stale one from a prior run.
    progress: Optional[dict] = None


class TriggerOutcome(str, Enum):
    ACCEPTED = "accepted"
    ALREADY_RUNNING = "already_running"


@dataclass(frozen=True)
class TriggerResult:
    outcome: TriggerOutcome
    run_id: str   # the NEW run's id if ACCEPTED; the EXISTING in-flight run's id if ALREADY_RUNNING


class OrchestratorDaemon:
    """Signal-agnostic core run engine.

    Thread-safety: a single ``threading.Lock`` (``self._lock``) guards
    ``self._current_run_id``, ``self._run_history`` (and its insertion-order
    list), the derived "is a run in flight" state, and (as of the live
    interval setter) ``self._interval_seconds``/``self._timer_thread`` too.
    Every read or mutation of those fields takes the lock; the single-flight
    check-and-claim in ``trigger_run`` happens atomically inside one lock
    acquisition so two near-simultaneous callers can never both observe
    ``_current_run_id is None`` and both proceed to ACCEPTED.

    The timer loop additionally uses TWO ``threading.Event``s (not a
    ``Condition`` -- zero precedent for that primitive in this codebase):
    ``self._stop_event`` (set once, at shutdown, never cleared again) and
    ``self._wake_event`` (cleared and set repeatedly across the timer
    thread's lifetime -- set by ``set_interval()`` to wake a sleeping/parked
    loop immediately so a cadence change takes effect without waiting out
    the old interval, and by ``shutdown()`` so a PARKED loop, which is
    blocked on ``self._wake_event.wait()`` with no timeout when
    ``interval_seconds <= 0``, actually wakes -- ``_stop_event`` alone would
    never reach it). See ``_timer_loop`` for the exact clear-before-read
    ordering this depends on.
    """

    def __init__(self, *, interval_seconds: int = 0, strict: bool = False,
                 dry_run: bool = False, run_history_size: int = 10) -> None:
        self._interval_seconds = interval_seconds
        self._strict = strict
        self._dry_run = dry_run
        self._run_history_size = run_history_size

        self._lock = threading.Lock()
        self._current_run_id: Optional[str] = None
        self._run_history: dict[str, RunRecord] = {}
        self._run_order: list[str] = []  # oldest-first insertion order, for eviction

        self._engines: Optional[main_orchestrator.EngineContext] = None
        self._data_engine: Optional[Any] = None
        self._started = False
        self._started_at: Optional[datetime] = None

        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._timer_thread: Optional[threading.Thread] = None
        self._worker_threads: dict[str, threading.Thread] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Build the warm DataEngine + EngineContext once, then start the
        interval timer thread (if configured). Idempotent."""
        if self._started:
            logger.warning("OrchestratorDaemon.start() called twice; ignoring second call.")
            return

        self._data_engine = self._build_data_engine()
        self._engines = main_orchestrator.EngineContext.build(data_engine=self._data_engine)
        self._started = True
        self._started_at = datetime.now(timezone.utc)
        logger.info(
            "OrchestratorDaemon started: engines warm, data_engine=%s, interval_seconds=%s",
            type(self._data_engine).__name__, self._interval_seconds,
        )

        if self._interval_seconds > 0:
            self._stop_event.clear()
            self._wake_event.clear()
            thread = self._new_timer_thread()
            with self._lock:
                self._timer_thread = thread
            thread.start()

    def shutdown(self, *, timeout: float = 10.0) -> None:
        """Stop the timer thread and wait (without forcibly killing) for any
        in-flight run to finish, up to ``timeout`` seconds. Idempotent."""
        self._stop_event.set()  # wakes a WAITING (interval > 0) timer loop immediately
        self._wake_event.set()  # ALSO required: a PARKED (interval <= 0) loop is
        # blocked on _wake_event.wait() with no timeout -- _stop_event alone
        # would never reach it.

        # Read + clear the thread reference under the lock, but join() OUTSIDE
        # it: _timer_loop may call self.trigger_run(), which itself acquires
        # self._lock -- holding the lock across join() here would deadlock
        # against a timer thread that's mid-trigger_run() when shutdown() is
        # called.
        with self._lock:
            thread = self._timer_thread
            self._timer_thread = None
        if thread is not None:
            thread.join(timeout=5.0)

        deadline = time.monotonic() + timeout
        while self.is_running and time.monotonic() < deadline:
            time.sleep(0.1)

        if self.is_running:
            logger.warning(
                "OrchestratorDaemon.shutdown(): timeout=%.1fs elapsed while a run "
                "was still in flight; returning without forcibly killing it.",
                timeout,
            )
        else:
            logger.info("OrchestratorDaemon shutdown complete.")

    # ------------------------------------------------------------------
    # Warm DataEngine construction — mirrors _main_body's own choice
    # ------------------------------------------------------------------

    def _build_data_engine(self) -> Any:
        """Construct a DataEngine/MockDataEngine exactly the way
        ``main_orchestrator._main_body`` would have, so ``start()`` produces
        the identical choice, just once instead of every cycle."""
        creds_exist = os.path.exists("credentials.json")
        if creds_exist:
            try:
                settings.ensure_fred_configured()
                return DataEngine(settings.FRED_API_KEY)
            except Exception as exc:
                logger.warning(
                    "FRED configuration check failed (%s); falling back to "
                    "deterministic MockDataEngine.", exc,
                )
                return MockDataEngine()
        else:
            logger.warning("credentials.json not found. Operating with deterministic MockDataEngine.")
            return MockDataEngine()

    # ------------------------------------------------------------------
    # Triggering runs
    # ------------------------------------------------------------------

    def trigger_run(self, *, reason: str = "manual", mode: str = "full") -> TriggerResult:
        """Non-blocking, single-flight run trigger.

        ``mode`` selects which pipeline sub-run to execute: "full" (default,
        unchanged whole cycle), "data" (data-fetch stages only), or "metrics"
        (data-fetch + indicator/forecast/signal precompute). It is threaded
        through to ``main_orchestrator._main_body(..., mode=mode)`` and recorded
        on the ``RunRecord``.
        """
        with self._lock:
            if self._current_run_id is not None:
                return TriggerResult(
                    outcome=TriggerOutcome.ALREADY_RUNNING,
                    run_id=self._current_run_id,
                )
            run_id = str(uuid.uuid4())
            self._current_run_id = run_id
            # Insert a RUNNING placeholder immediately (same lock acquisition
            # that claims the single-flight slot) so get_run(run_id) can find
            # this run the instant it's accepted -- a caller polling right
            # after trigger_run() returns must never see a false "unknown
            # run_id" for a run that is legitimately in flight. _run_one_cycle
            # overwrites this record in place (same run_id, no second append)
            # once the cycle finishes.
            self._run_history[run_id] = RunRecord(
                run_id=run_id, state=RunState.RUNNING, mode=mode,
                started_at=datetime.now(timezone.utc), finished_at=None,
                duration_seconds=None, error=None, reason=reason,
            )
            self._run_order.append(run_id)
            while len(self._run_order) > self._run_history_size:
                oldest = self._run_order.pop(0)
                self._run_history.pop(oldest, None)

        thread = threading.Thread(
            target=self._run_one_cycle, args=(run_id, reason, mode),
            name=f"OrchestratorDaemon-run-{run_id[:8]}", daemon=True,
        )
        self._worker_threads[run_id] = thread
        thread.start()
        return TriggerResult(outcome=TriggerOutcome.ACCEPTED, run_id=run_id)

    def _run_one_cycle(self, run_id: str, reason: str, mode: str = "full") -> None:
        started_at = datetime.now(timezone.utc)
        state: RunState
        error: Optional[str]
        try:
            asyncio.run(
                main_orchestrator._main_body(
                    self._dry_run,
                    strict=self._strict,
                    engines=self._engines,
                    data_engine=self._data_engine,
                    mode=mode,
                )
            )
            state = RunState.SUCCEEDED
            error = None
        except main_orchestrator.PipelineFatalError as exc:
            state = RunState.FAILED
            error = str(exc)
            logger.error("Run %s FAILED (PipelineFatalError): %s", run_id, exc)
        except Exception as exc:  # belt-and-suspenders: an unexpected bug must
            # never kill the daemon or leave it stuck "running" forever --
            # this is the core daemon-survives-a-crash property this whole
            # redesign exists for.
            state = RunState.FAILED
            error = f"unexpected: {exc}"
            logger.critical(
                "Run %s FAILED (unexpected exception): %s", run_id, exc, exc_info=True,
            )

        finished_at = datetime.now(timezone.utc)
        duration_seconds = (finished_at - started_at).total_seconds()

        # Snapshot the pipeline's final progress state (reporting/progress.py)
        # at cycle-completion time. read_progress() never raises (dead-letter
        # by its own contract), but the dataclass-to-dict conversion + ISO
        # serialization below is wrapped defensively anyway so a snapshotting
        # bug can NEVER affect whether this run is recorded as
        # SUCCEEDED/FAILED (CONSTRAINT #6) -- a periodic mid-run stamp was
        # explicitly called out as a "bonus, not required" by the progress
        # instrumentation task; this end-of-cycle snapshot satisfies the
        # baseline requirement.
        progress_snapshot: Optional[dict] = None
        try:
            _state = read_progress()
            if _state is not None:
                progress_snapshot = {
                    # Overwritten with the daemon's own run_id -- see the
                    # RunRecord.progress field comment above for why.
                    "run_id": run_id,
                    "state": _state.state,
                    "stage": _state.stage,
                    "stage_index": _state.stage_index,
                    "stage_total": _state.stage_total,
                    "symbols_done": _state.symbols_done,
                    "symbols_total": _state.symbols_total,
                    "percent": _state.percent,
                    "message": _state.message,
                    "started_at": _state.started_at.isoformat(),
                    "updated_at": _state.updated_at.isoformat(),
                }
        except Exception as _progress_exc:  # pragma: no cover - defensive only
            logger.debug(
                "Run %s: could not snapshot progress.json (%s); "
                "RunRecord.progress will be None.", run_id, _progress_exc,
            )
            progress_snapshot = None

        record = RunRecord(
            run_id=run_id,
            state=state,
            mode=mode,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=duration_seconds,
            error=error,
            reason=reason,
            progress=progress_snapshot,
        )

        with self._lock:
            # Overwrite the RUNNING placeholder inserted by trigger_run() in
            # place -- run_id is already in _run_order from that call, so no
            # second append/eviction pass is needed here.
            self._run_history[run_id] = record
            self._current_run_id = None

        self._worker_threads.pop(run_id, None)

    # ------------------------------------------------------------------
    # Interval timer
    # ------------------------------------------------------------------

    def _new_timer_thread(self) -> threading.Thread:
        return threading.Thread(
            target=self._timer_loop, name="OrchestratorDaemon-timer", daemon=True,
        )

    def set_interval(self, interval_seconds: int) -> None:
        """Change the daemon's internal timer cadence LIVE, without a
        restart. Raises ``ValueError`` (via ``settings.validate_interval_seconds``)
        on an invalid value -- callers translate that into their own error
        response (e.g. HTTP 422); no daemon state is mutated on a rejected
        value.

        ``start()`` only creates the timer thread when ``interval_seconds >
        0`` at startup, so a daemon started at 0 (on-demand only) has no
        thread to wake -- this method creates one on demand if none exists
        yet, for either a zero or nonzero target value, so a later
        ``set_interval`` call always has a thread to signal.

        Thread creation happens under ``self._lock`` (so two concurrent
        ``set_interval`` calls can never both create a thread), but
        ``thread.start()`` itself happens OUTSIDE the lock, mirroring
        ``trigger_run``'s own worker-thread pattern.
        """
        interval_seconds = validate_interval_seconds(interval_seconds)
        thread_to_start: Optional[threading.Thread] = None
        with self._lock:
            self._interval_seconds = interval_seconds
            if self._timer_thread is None:
                self._stop_event.clear()
                thread_to_start = self._new_timer_thread()
                self._timer_thread = thread_to_start
        if thread_to_start is not None:
            thread_to_start.start()
        # Wake a loop that's already parked/waiting on the OLD interval so
        # the new cadence takes effect immediately rather than after the old
        # interval elapses. A no-op if the thread was just created above
        # (its first action is to clear this event and re-read the interval
        # anyway).
        self._wake_event.set()
        logger.info("OrchestratorDaemon interval changed to %s seconds.", interval_seconds)

    def _timer_loop(self) -> None:
        while not self._stop_event.is_set():
            # Clear BEFORE reading the interval. If set_interval() fires
            # between this clear and the read below, we read its NEW value
            # AND observe the event already set -> one harmless spurious
            # loop iteration, never a lost wake. Clearing AFTER the read
            # would instead risk dropping that wake and sleeping out the
            # OLD interval -- that ordering bug is exactly what this
            # comment exists to prevent from being "cleaned up" later.
            self._wake_event.clear()
            with self._lock:
                interval = self._interval_seconds
            if self._stop_event.is_set():
                break
            if interval <= 0:
                self._wake_event.wait()  # park; _stop_event.wait(0) would spin a core
                continue
            if self._wake_event.wait(timeout=interval):
                continue  # interval changed OR shutting down -- re-check at the top
            if self._stop_event.is_set():
                break
            # ALREADY_RUNNING (previous interval cycle still in flight) is
            # expected and fine -- just proceed to the next wait.
            self.trigger_run(reason="interval")

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def status(self) -> dict:
        with self._lock:
            current_run_id = self._current_run_id
            last_run = self._run_history[self._run_order[-1]] if self._run_order else None
            interval_seconds = self._interval_seconds
            # Bounded run history, most-recent-first (matches the frozen
            # GET /status contract). _run_order is oldest->newest (append), so
            # reverse it. Records are snapshotted under the lock; the caller
            # (api/control_api.py) serializes each RunRecord.
            run_history = [self._run_history[rid] for rid in reversed(self._run_order)]
        return {
            "is_running": current_run_id is not None,
            "current_run_id": current_run_id,
            "interval_seconds": interval_seconds,
            "last_run": last_run,
            "run_history": run_history,
            "engines_warm": self._engines is not None,
            "started_at": self._started_at,
        }

    def get_run(self, run_id: str) -> Optional[RunRecord]:
        with self._lock:
            return self._run_history.get(run_id)

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._current_run_id is not None

    @property
    def last_result(self) -> Optional[RunRecord]:
        with self._lock:
            if not self._run_order:
                return None
            return self._run_history[self._run_order[-1]]
