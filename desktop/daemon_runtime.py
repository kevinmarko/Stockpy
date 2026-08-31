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
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Optional

import main_orchestrator
import runtime_flags
from settings import settings, validate_interval_seconds
from data_engine import DataEngine, MockDataEngine
from reporting.progress import read_progress
from engine.advisory_agent import is_automatic_run_gated

logger = logging.getLogger("OrchestratorDaemon")

#: Sentinel for "maybe_refresh_settings() has never checked the store yet" --
#: see OrchestratorDaemon.__init__'s _last_seen_store_stat for why this must
#: be distinct from `None`.
_STORE_UNCHECKED = object()


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

        # Sentinel distinct from both `None` (file confirmed absent on the
        # last check) and any real `(mtime_ns, size)` tuple, so the very
        # first maybe_refresh_settings() call always treats the store as
        # "possibly changed" -- whether it turns out to exist or not -- and
        # never has to special-case "no prior check happened yet" against
        # "the file wasn't there last time either" (see that method).
        self._last_seen_store_stat: Any = _STORE_UNCHECKED

        # Bounded in-process (timestamp, equity) sample buffer for
        # maybe_update_circuit_breaker()'s loss-velocity brake. Sized for a
        # ~60-minute rolling window assuming a ~60s daemon tick cadence (the
        # smallest practically-useful ORCHESTRATOR_INTERVAL_SECONDS an
        # operator would run this feature at) -- comfortably covers
        # settings.CIRCUIT_BREAKER_LOSS_VELOCITY_WINDOW_MINS's default 30m
        # even with some margin. A slower configured cadence just means the
        # buffer's oldest sample spans MORE than 60 minutes, which only
        # makes the computed rate a smoother, longer-horizon average -- never
        # a correctness problem. Never persisted; intentionally lost on
        # restart (an intraday-only metric with no cross-restart meaning).
        self._circuit_breaker_equity_history: "deque[tuple[float, float]]" = deque(maxlen=60)

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
        in-flight run to finish, up to ``timeout`` seconds TOTAL. Idempotent.

        The timer-thread join is budgeted WITHIN ``timeout`` (capped at 5.0s
        of it) rather than added as a separate hardcoded 5.0s on top --
        earlier this method joined for a flat 5.0s and then started a FRESH
        ``timeout``-long deadline for the in-flight-run poll, so a caller
        passing ``timeout=10.0`` could actually wait up to 15.0s: the exact
        "emergent, unreconciled sum" defect that ``settings.
        DAEMON_SHUTDOWN_TIMEOUT_SECONDS``'s single-published-budget design
        exists to eliminate. With this fix, ``shutdown(timeout=T)`` returns
        within ``T`` of being called, full stop -- callers (see
        ``desktop/orchestrator_daemon.py``'s ``_teardown()``) can size their
        OWN remaining budget for this call without double-counting a join
        this method already accounts for internally.
        """
        _entry = time.monotonic()
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
            join_timeout = max(0.0, min(5.0, timeout - (time.monotonic() - _entry)))
            thread.join(timeout=join_timeout)

        deadline = _entry + timeout
        while self.is_running and time.monotonic() < deadline:
            # Clamp each sleep slice to whatever's actually left on the
            # deadline -- a fixed 0.1s sleep can itself overshoot `timeout`
            # (e.g. timeout=0.01: the while-check passes once, then a flat
            # 0.1s sleep blows the 10ms budget by 10x), which would make
            # shutdown(timeout=T) not actually honor T for small T.
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))

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
        # Self-gated, read-only (see its own docstring) -- called here too so
        # an on-demand-only deployment (settings.ORCHESTRATOR_INTERVAL_SECONDS
        # <= 0, where _timer_loop parks on an untimed wait and never gets a
        # periodic chance to check) still detects a stall the moment anyone
        # triggers or polls a run.
        self.maybe_alert_on_pipeline_stall()
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
        # Only the automatic interval timer honors the cross-cycle
        # data-freshness gate (DATA_FRESHNESS_TTL_SECONDS). Every other trigger
        # -- a manual "Run Pipeline", an on-demand API call, a dry-run -- forces
        # a real refresh so the operator's explicit action is never silently
        # skipped as "data still fresh".
        force = reason != "interval"
        try:
            asyncio.run(
                main_orchestrator._main_body(
                    self._dry_run,
                    strict=self._strict,
                    engines=self._engines,
                    data_engine=self._data_engine,
                    mode=mode,
                    force=force,
                )
            )
            state = RunState.SUCCEEDED
            error = None
            # Note: 0DTE exit-lifecycle management (manage_0dte_exits) is NOT
            # re-run here. _timer_loop already calls it on every interval wake
            # (more frequently than once per full pipeline cycle, and without
            # waiting on cycle success) -- see _timer_loop below. A second call
            # here would just double-fire it once per completed cycle.
            #
            # 0DTE is also the ONLY piece of main.py's automated options
            # lifecycle with any daemon-path equivalent at all -- exit
            # management (execution.options_paper_executor.OptionsPaperExecutor
            # .execute_auto_exits), new-position strategy auto-execution
            # (.execute_strategy_directives), and delta hedging
            # (main._run_automated_delta_hedge_cycle) are called ONLY from
            # main.py's _run_cycle() and have NO equivalent call anywhere in
            # main_orchestrator.py or this file. If ORCHESTRATOR_DAEMON_ENABLED
            # is ever flipped to True, those three automated behaviors would
            # silently stop running. This is a disclosed, deferred gap, not an
            # oversight left uncommented -- see
            # docs/known_issues/options_lifecycle_daemon_gate_gap_2026_08_22.md
            # for the full write-up and why a real fix needs a shared,
            # importable-from-both module plus a cadence + macro_dto-threading
            # design decision, not a quick import from main.py (main.py's
            # module-top venv-reexec guard makes importing it from here unsafe).
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

        # Best-effort persist to the durable pipeline_runs table (desktop/
        # run_history_store.py) so the Pipeline Dashboard's run-history table
        # survives a daemon restart instead of being capped at the in-memory
        # ring below. Lazy import (matches HistoricalStore's convention
        # elsewhere in this codebase -- avoids a DB import at module load
        # time). A DB hiccup here must never crash the daemon or affect this
        # run's already-decided SUCCEEDED/FAILED state -- only the durable
        # table lags, exactly like the progress_snapshot capture above.
        try:
            from desktop.run_history_store import RunHistoryStore

            RunHistoryStore().record_run(record)
        except Exception as exc:  # pragma: no cover - defensive only
            logger.warning(
                "Run %s: failed to persist run history to DB (%s); "
                "the run's %s state is unaffected -- only the durable "
                "history table lags.", run_id, exc, state.value,
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

    # ------------------------------------------------------------------
    # Cross-process settings hot-reload
    # ------------------------------------------------------------------

    def maybe_refresh_settings(
        self, *, path: Optional[Any] = None
    ) -> Optional[runtime_flags.ApplyReport]:
        """Re-apply ``output/runtime_flags.json`` onto this process's live
        ``settings`` singleton if the store has changed since the last check.

        The honest scope of what this buys: a settings-store write served by
        THIS SAME process (e.g. ``PILOTS_API_ENABLED=True`` hosting
        ``api/pilots_api.py`` inside the daemon) already applies immediately
        via ``runtime_flags_writer.write_override()``'s own in-process
        re-apply — this method exists for the write served by a DIFFERENT
        process (the far more common topology, where ``pilots_api.py`` runs
        standalone). Called periodically, on a poll interval, by the
        standalone entrypoint (``desktop/orchestrator_daemon.py``), AND from
        ``_timer_loop`` below on every wake -- both call sites gate the call
        on ``settings.RUNTIME_FLAGS_REFRESH_ENABLED`` themselves rather than
        this method checking it internally, so a caller that forgets the
        gate would silently poll/apply the store regardless of the flag;
        this method itself stays free of any opinion about polling cadence
        or whether cross-process refresh is wanted at all.

        Deferred (returns ``None``, no-op) while a pipeline cycle is in
        flight — a value changing mid-cycle must not partially apply and
        leave the cycle reading a mix of old and new settings; the next poll
        tick picks it up once idle. This is a best-effort deferral, not a
        hard guarantee (a cycle could start in the narrow window between the
        ``is_running`` check and the apply below) — acceptable, since the
        thing being guarded against is a long JSON-file read racing a run's
        *start*, not a run reading a genuinely torn value.

        One ``os.stat()`` per call; the ``(mtime_ns, size)`` pair is compared
        against the last-seen value under ``self._lock`` so two overlapping
        calls (there is only ever one caller today, but this method makes no
        assumption about that) can never both decide the file "changed" and
        both pay the cost of re-validating and re-applying the same content.
        A change triggers exactly one ``runtime_flags.apply_overrides()``
        call, done OUTSIDE the lock (file I/O and pydantic validation have no
        business holding a lock this class's run-triggering methods also
        need).

        ``ON_CHANGE_HOOKS``: a bare ``setattr`` is not enough for
        ``ORCHESTRATOR_INTERVAL_SECONDS`` specifically — ``_timer_loop``
        reads ``self._interval_seconds``, captured at thread-start time, and
        never re-reads ``settings`` on its own — so when that key is among
        the ones ``apply_overrides()`` actually applied, this method also
        calls ``self.set_interval()`` with the new value so the running
        timer thread picks up the change instead of the write silently
        becoming a permanent no-op until the daemon restarts.

        Never raises (CONSTRAINT #6) — a stat failure, a corrupt store, or a
        hook error all degrade to "try again next tick," logged, never
        propagated into the caller's polling loop.
        """
        try:
            if self.is_running:
                return None

            resolved = runtime_flags.store_path(path)
            try:
                stat_result = resolved.stat()
                current = (stat_result.st_mtime_ns, stat_result.st_size)
            except FileNotFoundError:
                current = None

            with self._lock:
                if current == self._last_seen_store_stat:
                    return None
                self._last_seen_store_stat = current

            if current is None:
                # The store doesn't exist (never did, or was removed) --
                # nothing to apply. Still worth recording the transition
                # above so a file that later appears is correctly detected
                # as "changed" on some future tick.
                return None

            report = runtime_flags.apply_overrides(settings, path=path)

            if "ORCHESTRATOR_INTERVAL_SECONDS" in report.applied:
                new_interval = report.applied["ORCHESTRATOR_INTERVAL_SECONDS"]
                try:
                    self.set_interval(new_interval)
                except ValueError as exc:
                    # Genuinely reachable, not a defensive-only guard: the
                    # field itself carries no @field_validator (it's a plain
                    # `int` -- see settings.py), so apply_overrides() accepts
                    # any integer, while set_interval() enforces the
                    # stricter "0 or [60, 86400]" business rule via
                    # validate_interval_seconds(). The same gap exists on
                    # the established write path for this field
                    # (api/pilots_api.py's set_automation_interval writes to
                    # .env unconditionally and only degrades its OWN
                    # `applies` to "next_daemon_restart" when the live-apply
                    # rejects the value) -- this hook matches that existing,
                    # honest behavior rather than inventing a new one: the
                    # setting is durably applied, the running timer keeps
                    # its old cadence, and both facts are logged rather than
                    # one silently winning.
                    logger.warning(
                        "maybe_refresh_settings: ORCHESTRATOR_INTERVAL_SECONDS "
                        "changed to %r but the live timer rejected it (%s); "
                        "the setting is applied, the running timer is not.",
                        new_interval, exc,
                    )

            return report
        except Exception as exc:  # noqa: BLE001 - CONSTRAINT #6, never break the poller
            logger.warning(
                "maybe_refresh_settings: unexpected failure (%s); will retry "
                "next tick.", type(exc).__name__, exc_info=True,
            )
            return None

    # ------------------------------------------------------------------
    # Live circuit-breaker updater (volatility-jump + VPIN + loss-velocity;
    # OFI deliberately unwired -- see maybe_update_circuit_breaker's own
    # docstring for the full, honest scope)
    # ------------------------------------------------------------------

    def maybe_update_circuit_breaker(self) -> None:
        """Live circuit-breaker updater: volatility-jump + VPIN + loss-velocity.

        HONEST SCOPE — read this before assuming full automatic coverage:
        this wires THREE of the Dynamic Circuit Breaker's four sub-checks
        (``execution.dynamic_circuit_breaker.DynamicCircuitBreaker``) into a
        periodic live data feed:

        1. **Volatility jump** (``check_volatility_jump`` / the
           ``volatility_zscore`` input) — daily-bar baseline vs. a reactive
           hourly window, as before.
        2. **VPIN** (Volume-Synchronized Probability of Toxicity, the
           ``vpin`` input) — a coarse, BAR-LEVEL Bulk Volume Classification
           approximation (``pilots.options_vpin.calculate_vpin``) computed
           against the SAME reactive hourly-bar window fetched for #1 (no
           second network fetch). This is intentionally NOT tick-resolution
           toxicity — with only ~a few dozen hourly bars available, the
           number of volume buckets is sized down from the module's 50-bucket
           tick-stream default to fit the actual row count. It is a genuine,
           non-fabricated signal, just a coarser one than the literature's
           tick-level formulation.
        3. **Loss velocity** (``check_loss_velocity_brake`` / the
           ``loss_velocity_per_min``/``account_equity`` inputs) — sampled
           from ``data.paper_account_store.PaperAccountStore(readonly=True)
           .get_account().equity`` into a small in-process rolling buffer
           (``self._circuit_breaker_equity_history``) on this instance; the
           rate is computed against the OLDEST buffered sample once at least
           two samples span >= 60 seconds. Each sample is a REAL read (not a
           cached/stale value) that resolves live prices for every open
           paper position, so this ties a real external quote-API cost to
           whatever daemon tick cadence is configured — worth knowing before
           turning ``CIRCUIT_BREAKER_ENABLED`` on with a tight interval.

        **OFI (Order Flow Imbalance) is deliberately NOT wired and stays
        MANUAL-ONLY.** No configured market-data provider (Alpaca/FMP/
        yfinance) populates bid/ask SIZE anywhere in this codebase's
        ``Quote`` type — there is no real order-flow-imbalance signal to
        compute from here, full stop; this is a genuine data-availability
        gap, not an oversight. Because ``check_flash_crash_shield`` requires
        BOTH ``ofi`` and ``vpin`` to be non-``None`` before it evaluates
        anything, the compound flash-crash shield still can never trigger
        automatically even though VPIN itself is now real and persisted —
        VPIN's persisted value retains standalone diagnostic/observability
        worth on its own. An operator (or an external watchdog) can still
        trip either the flash-crash shield or any other state directly via
        ``python -m execution.kill_switch --activate-soft-halt`` /
        ``--activate``.

        Gated on ``settings.CIRCUIT_BREAKER_ENABLED`` (default ``False`` —
        today's exact, inert, behavior; a no-op when disabled). When
        enabled: fetches recent daily bars for
        ``settings.CIRCUIT_BREAKER_REFERENCE_SYMBOL`` to build a rolling
        20-trading-day annualized realized-vol baseline (plus that series'
        own std), fetches a short recent hourly window as the reactive
        "current" input (reused for VPIN, see #2 above), computes the
        5m-EWMA-style volatility Z-score via ``check_volatility_jump``, and
        persists the combined result via ``update_metrics(volatility_zscore=
        ..., vpin=..., loss_velocity_per_min=..., account_equity=...,
        persist=True)`` — the same persistence path
        ``dynamic_circuit_breaker_check``'s file-sentinel fallback
        (``execution/risk_gate.py``) reads via ``load_metrics()``.

        Never raises (CONSTRAINT #6): any data-fetch or computation failure
        in the volatility-jump path degrades this whole tick to a logged
        WARNING (skipped, previous state untouched). The VPIN and
        loss-velocity sub-steps are each wrapped in their OWN try/except so
        a failure in either (e.g. VPIN's bar-level BVC computation, or a
        paper-account-store read) degrades that one input to ``None``
        without preventing the other two inputs (including the
        already-working volatility Z-score) from still being computed and
        persisted this tick. Called from ``_timer_loop`` on every wake,
        mirroring ``maybe_refresh_settings``'s own defensive pattern.
        """
        if not settings.CIRCUIT_BREAKER_ENABLED:
            return
        try:
            from data.market_data import get_provider
            from execution.dynamic_circuit_breaker import DynamicCircuitBreaker

            symbol = settings.CIRCUIT_BREAKER_REFERENCE_SYMBOL
            provider = get_provider()

            daily_bars = provider.get_intraday_bars(symbol, lookback_days=90, interval="1d")
            if daily_bars is None or daily_bars.empty or "Close" not in daily_bars.columns:
                logger.warning(
                    "maybe_update_circuit_breaker: no usable daily bars for %s; skipping tick.",
                    symbol,
                )
                return

            daily_returns = daily_bars["Close"].pct_change().dropna()
            if len(daily_returns) < 21:
                logger.warning(
                    "maybe_update_circuit_breaker: insufficient daily-return history for "
                    "%s (%d rows, need >= 21 for a 20d rolling-vol baseline); skipping tick.",
                    symbol, len(daily_returns),
                )
                return

            rolling_vol = daily_returns.rolling(window=20).std().dropna() * (252.0 ** 0.5)
            if rolling_vol.empty:
                logger.warning(
                    "maybe_update_circuit_breaker: rolling 20d vol series empty for %s; "
                    "skipping tick.",
                    symbol,
                )
                return
            baseline_20d_vol = float(rolling_vol.iloc[-1])
            baseline_vol_std = float(rolling_vol.std()) if len(rolling_vol) > 1 else None

            reactive_bars = provider.get_intraday_bars(symbol, lookback_days=2, interval="1h")
            if reactive_bars is None or reactive_bars.empty or "Close" not in reactive_bars.columns:
                logger.warning(
                    "maybe_update_circuit_breaker: no usable hourly bars for %s; skipping tick.",
                    symbol,
                )
                return

            cb = DynamicCircuitBreaker()
            _triggered, z_score, _reason = cb.check_volatility_jump(
                intraday_returns_or_prices=reactive_bars["Close"],
                baseline_20d_vol=baseline_20d_vol,
                baseline_vol_std=baseline_vol_std,
                is_prices=True,
            )

            # --- VPIN: coarse bar-level BVC approximation ------------------
            # Reuses the SAME reactive_bars hourly window fetched above for
            # the vol-jump detector -- no second network fetch. Isolated in
            # its own try/except (CONSTRAINT #6): a VPIN failure must never
            # prevent the already-computed volatility Z-score (or the
            # loss-velocity sub-step below) from still being persisted.
            vpin_value: Optional[float] = None
            try:
                import pandas as pd

                from pilots.options_vpin import calculate_vpin

                # DEFAULT_NUM_BUCKETS (50) assumes a real tick/trade stream;
                # a ~2-day hourly window is only ~13-14 rows, so bucket count
                # is sized down to the actual row count instead (floored at
                # 2 so the rolling-VPIN window is never degenerate).
                vpin_num_buckets = max(2, min(10, len(reactive_bars) // 2))
                # _normalize_trades_df matches an EXACT lowercase column set
                # ("price"/"volume"/"time" among its aliases) -- the raw
                # OHLCV bars use capitalized "Close"/"Volume" and a
                # DatetimeIndex, neither of which match those aliases
                # as-is, so an explicit rename (not a bare pass-through) is
                # required here.
                vpin_trades_df = pd.DataFrame(
                    {
                        "price": reactive_bars["Close"].to_numpy(dtype=float),
                        "volume": reactive_bars["Volume"].to_numpy(dtype=float),
                        "time": reactive_bars.index.astype(str),
                    }
                )
                vpin_result = calculate_vpin(
                    vpin_trades_df, num_buckets=vpin_num_buckets, symbol=symbol
                )
                vpin_value = float(vpin_result.vpin)
            except Exception as vpin_exc:  # noqa: BLE001 - CONSTRAINT #6, isolate from vol-jump
                logger.warning(
                    "maybe_update_circuit_breaker: VPIN computation failed (%s); "
                    "leaving vpin=None this tick.", type(vpin_exc).__name__, exc_info=True,
                )

            # --- Loss velocity: live PaperAccountStore equity sampling -----
            # Isolated in its own try/except (CONSTRAINT #6) for the same
            # reason as VPIN above -- a paper-account-store read failure
            # must not take down the vol-jump/VPIN inputs already computed.
            loss_velocity_per_min: Optional[float] = None
            account_equity_for_update: Optional[float] = None
            try:
                from data.paper_account_store import PaperAccountStore

                equity_now = float(PaperAccountStore(readonly=True).get_account().equity)
                now_ts = time.time()
                self._circuit_breaker_equity_history.append((now_ts, equity_now))

                if len(self._circuit_breaker_equity_history) >= 2:
                    earliest_ts, earliest_equity = self._circuit_breaker_equity_history[0]
                    elapsed_seconds = now_ts - earliest_ts
                    # Require >= 60s of real elapsed time so the rate isn't
                    # dominated by noise from two near-simultaneous samples.
                    if elapsed_seconds >= 60.0:
                        loss_velocity_per_min = (
                            (equity_now - earliest_equity) / (elapsed_seconds / 60.0)
                        )
                        account_equity_for_update = equity_now
            except Exception as lv_exc:  # noqa: BLE001 - CONSTRAINT #6, isolate from vol-jump/VPIN
                logger.warning(
                    "maybe_update_circuit_breaker: loss-velocity sampling failed (%s); "
                    "leaving loss_velocity_per_min=None this tick.",
                    type(lv_exc).__name__, exc_info=True,
                )

            # --- OFI: deliberately NOT computed -----------------------------
            # No configured market-data provider (Alpaca/FMP/yfinance)
            # populates bid/ask SIZE anywhere in this codebase's Quote type,
            # so there is no real order-flow-imbalance signal available to
            # compute here -- this is a genuine data-availability gap, not
            # an oversight. check_flash_crash_shield requires BOTH ofi and
            # vpin to be non-None before it evaluates anything, so the
            # compound flash-crash shield still cannot trigger automatically
            # even with vpin now real and persisted below; VPIN's persisted
            # value retains standalone diagnostic worth on its own.
            cb.update_metrics(
                volatility_zscore=z_score,
                vpin=vpin_value,
                loss_velocity_per_min=loss_velocity_per_min,
                account_equity=account_equity_for_update,
                persist=True,
            )
            logger.debug(
                "maybe_update_circuit_breaker: %s volatility Z-score=%.2f vpin=%s "
                "loss_velocity_per_min=%s -> state=%s",
                symbol, z_score, vpin_value, loss_velocity_per_min, cb.current_state.value,
            )
        except Exception as exc:  # noqa: BLE001 - CONSTRAINT #6, never break the timer loop
            logger.warning(
                "maybe_update_circuit_breaker: unexpected failure (%s); will retry "
                "next tick.", type(exc).__name__, exc_info=True,
            )

    def maybe_alert_on_pipeline_stall(self) -> None:
        """Read-only stall watchdog for a wedged pipeline cycle.

        2026-08 fix: a real incident showed a cycle can wedge in a single
        stage (an unbounded synchronous call blocking a background thread
        forever -- see docs/known_issues/data_pipeline_fred_unbounded_timeout_stall.md)
        with NOTHING surfacing that fact. ``_run_one_cycle`` runs on its own
        thread, separate from this one, so the daemon's Control/Pilots APIs
        stay fully responsive throughout a wedge -- which is exactly why it
        went unnoticed for 2.5 days: nothing else looked broken.

        Deliberately alert-only, gated on ``settings.PIPELINE_STALL_ALERT_ENABLED``
        (default True): this never cancels the wedged cycle or restarts this
        process. Forcibly killing a mid-flight cycle risks corrupting partial
        state, and this process also hosts the Control/Pilots APIs the webapp
        depends on -- turning every future stall into a guaranteed outage
        would trade one problem for a worse one. ``observability.alerts.send_alert``'s
        own ``dedup_key``/``settings.ALERT_DEDUP_WINDOW_SECONDS`` mechanism
        means a persisting stall re-fires as a periodic reminder rather than
        going silent forever after the first alert.

        Called unconditionally from both ``_timer_loop`` per-wake spots
        (self-gates internally, matching ``maybe_update_circuit_breaker``'s
        own contract) AND from ``trigger_run`` -- ``settings.ORCHESTRATOR_INTERVAL_SECONDS``
        defaults to 0 (on-demand only), where ``_timer_loop`` parks on an
        untimed wait and would otherwise never get a periodic chance to check.
        """
        if not settings.PIPELINE_STALL_ALERT_ENABLED:
            return
        try:
            state = read_progress()
            if state is None or state.state != "running":
                return
            age = state.age_seconds()
            if age < settings.PIPELINE_STALL_ALERT_SECONDS:
                return
            from observability.alerts import send_alert
            send_alert(
                "WARNING",
                f"Pipeline cycle {state.run_id!r} has been stuck in stage "
                f"'{state.stage}' ({state.symbols_done}/{state.symbols_total} "
                f"symbols) for {age:.0f}s with no progress update -- it may be "
                "wedged on an unbounded blocking call. See "
                "docs/known_issues/data_pipeline_fred_unbounded_timeout_stall.md.",
                dedup_key="pipeline_stall",
            )
        except Exception:  # noqa: BLE001 - CONSTRAINT #6, this check must never break the caller
            logger.warning("maybe_alert_on_pipeline_stall: unexpected failure", exc_info=True)


    def maybe_refresh_google_trends(self) -> None:
        """Periodic refresh of Google Trends data.
        
        Gated by settings.GOOGLE_TRENDS_ENABLED. Tracks last run time internally
        and throttles based on settings.GOOGLE_TRENDS_REFRESH_INTERVAL_HOURS.
        Never raises.
        """
        if not getattr(settings, "GOOGLE_TRENDS_ENABLED", False):
            return
            
        now = time.monotonic()
        refresh_hours = getattr(settings, "GOOGLE_TRENDS_REFRESH_INTERVAL_HOURS", 24.0)
        
        # Internal throttle
        if getattr(self, "_last_google_trends_refresh", 0.0) > 0.0:
            if (now - self._last_google_trends_refresh) < (refresh_hours * 3600):
                return
                
        try:
            from data.google_trends_client import fetch_overlapping_windows
            from data.trends_stitcher import GoogleTrendsStitcher
            from data.trends_store import TrendsStore
            import uuid
            
            store = TrendsStore()
            symbols = list(getattr(settings, "DEFAULT_TICKERS", []) or [])
            if not symbols:
                return
            
            end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            # Pull 1 year of data
            start_date = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%d")

            for sym in symbols:
                # fetch overlapping windows
                series_list = fetch_overlapping_windows(sym, start_date, end_date)
                if not series_list:
                    continue
                    
                window_ids = [str(uuid.uuid4()) for _ in series_list]
                
                # store raw
                for i, series in enumerate(series_list):
                    raw_data = [{"date": d.date(), "value": v} for d, v in series.items()]
                    store.insert_raw_window(sym, window_ids[i], raw_data, datetime.now(timezone.utc))
                    
                # stitch and store
                stitched = GoogleTrendsStitcher.stitch_multiple_intervals(series_list)
                if not stitched.empty:
                    stitched_data = [{"date": d.date(), "value": v} for d, v in stitched.items()]
                    store.save_stitched_series(sym, stitched_data, datetime.now(timezone.utc))
                    
            self._last_google_trends_refresh = time.monotonic()
        except Exception as exc:
            logger.warning("maybe_refresh_google_trends: unexpected failure: %s", exc)


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
            # Gated on the SAME flag desktop/orchestrator_daemon.py's
            # standalone refresher thread checks before it is even spawned
            # (settings.RUNTIME_FLAGS_REFRESH_ENABLED) -- maybe_refresh_settings()
            # itself has no opinion on whether cross-process refresh is
            # wanted at all (see its own docstring), so an unconditional
            # call here would silently keep polling/applying
            # output/runtime_flags.json on every timer wake even when an
            # operator has explicitly set the flag to False to opt out.
            if settings.RUNTIME_FLAGS_REFRESH_ENABLED:
                self.maybe_refresh_settings()
            # maybe_update_circuit_breaker() gates on
            # settings.CIRCUIT_BREAKER_ENABLED internally (unlike
            # maybe_refresh_settings, which relies on its callers to gate) --
            # see its own docstring. Called unconditionally here so it is a
            # true no-op, not merely "never invoked," when the flag is off.
            self.maybe_update_circuit_breaker()
            self.maybe_refresh_google_trends()
            # Same "called unconditionally, self-gates internally" contract --
            # see maybe_alert_on_pipeline_stall's own docstring.
            self.maybe_alert_on_pipeline_stall()
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
            if settings.RUNTIME_FLAGS_REFRESH_ENABLED:
                self.maybe_refresh_settings()
            self.maybe_update_circuit_breaker()
            self.maybe_refresh_google_trends()
            self.maybe_alert_on_pipeline_stall()
            # ALREADY_RUNNING (previous interval cycle still in flight) is
            # expected and fine -- just proceed to the next wait.
            if is_automatic_run_gated(
                datetime.now(timezone.utc), extended_hours_only=settings.ORCHESTRATOR_EXTENDED_HOURS_ONLY
            ):
                logger.debug("Market-hours gate: skipping interval cycle (outside 4am-8pm ET weekday window).")
                continue
            # Periodically evaluate and manage 0DTE exits (F5) during market hours.
            # This is the ONLY automated-options-lifecycle behavior wired into
            # the daemon path -- exit management, strategy auto-execution, and
            # delta hedging (main.py's OPTIONS_AUTO_EXIT_ENABLED /
            # PAPER_OPTIONS_AUTO_EXECUTE_ENABLED / OPTIONS_DELTA_HEDGE_ENABLED)
            # have no daemon-path equivalent at all -- see
            # docs/known_issues/options_lifecycle_daemon_gate_gap_2026_08_22.md.
            if getattr(settings, "OPTIONS_0DTE_ENABLED", False):
                try:
                    from pilots.zero_dte_engine import manage_0dte_exits
                    manage_0dte_exits()
                except Exception as exc:  # noqa: BLE001 - defensive only (CONSTRAINT #6)
                    logger.debug("0DTE daemon periodic exit evaluation skipped: %s", exc)

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
