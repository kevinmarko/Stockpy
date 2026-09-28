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
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import main_orchestrator
import runtime_flags
from settings import settings, validate_interval_seconds
import data_engine
from data_engine import DataEngine, MockDataEngine
from reporting.atomic_write import atomic_write_json
from reporting.progress import read_progress
from engine.advisory_agent import is_automatic_run_gated

logger = logging.getLogger("OrchestratorDaemon")

#: Sentinel for "maybe_refresh_settings() has never checked the store yet" --
#: see OrchestratorDaemon.__init__'s _last_seen_store_stat for why this must
#: be distinct from `None`.
_STORE_UNCHECKED = object()

#: How long ``_timer_loop`` parks between wake-ups when the daemon is
#: running in strictly on-demand mode (``settings.ORCHESTRATOR_INTERVAL_SECONDS
#: <= 0``). Previously an UNBOUNDED ``threading.Event.wait()`` -- woken only
#: by ``set_interval()``/``shutdown()`` -- which meant every self-gated
#: periodic check at the top of the loop (``maybe_refresh_google_trends``,
#: ``maybe_dispatch_weekly_digest``, ...)
#: got at most one chance to run (the loop's first iteration, before the
#: first park) and then never again for the rest of the process's life
#: unless something else happened to call ``set_interval()``. A bounded park
#: gives those checks a periodic chance regardless -- cheap and safe because
#: every one of them already self-gates on its own settings flag internally,
#: so waking hourly to no-op through several disabled checks costs nothing.
#: See ``maybe_dispatch_weekly_digest``'s own docstring for the concrete
#: case this constant was introduced to fix.
_PARKED_TIMER_POLL_SECONDS = 3600.0

#: Filename (under settings.OUTPUT_DIR) for the weekly digest's durable
#: last-dispatch state. See ``_weekly_digest_state_path`` /
#: ``maybe_dispatch_weekly_digest`` below.
_WEEKLY_DIGEST_STATE_FILENAME = "weekly_digest_state.json"


def _weekly_digest_state_path() -> Path:
    """Path to the weekly digest's durable dispatch-state file.

    Lives under ``settings.OUTPUT_DIR`` (``LOCAL_DATA_ROOT``-relative, shared
    across every git worktree/checkout on this machine) so a daemon restart
    can see whether this week's digest was already dispatched, instead of
    relying solely on an in-process attribute that resets to unset on every
    fresh ``OrchestratorDaemon()`` construction.
    """
    return Path(settings.OUTPUT_DIR) / _WEEKLY_DIGEST_STATE_FILENAME


def _weekly_digest_snapshot_path() -> str:
    """Resolve ``state_snapshot.json`` the SAME way
    ``api/pilots_api.py::_snapshot_path()`` does: ``settings.OUTPUT_DIR``
    (``LOCAL_DATA_ROOT``-relative, e.g. ``~/.stockpy_local/output`` by
    default -- deliberately OUTSIDE every git worktree/checkout).

    CONFIRMED BUG this closes: ``maybe_dispatch_weekly_digest`` previously
    called ``compose_digest()`` with NO argument, which falls back to
    ``pilots.scoring.load_snapshot``'s own hardcoded, CWD-relative default
    (``"output/state_snapshot.json"``) -- a path the daemon's working
    directory (the repo root) essentially never matches in a real
    deployment. The automatic, daemon-driven digest dispatch was therefore
    silently and permanently degrading to "no state snapshot yet" (honest
    per CONSTRAINT #6, but functionally dead) while the on-demand
    ``GET /pilots/weekly-digest`` endpoint -- which already correctly used
    ``_snapshot_path()`` -- worked fine, so nothing surfaced the gap.
    """
    return str(settings.OUTPUT_DIR / "state_snapshot.json")


def _read_weekly_digest_last_dispatch() -> Optional[datetime]:
    """Read the durable "last dispatched at" timestamp for the weekly digest.

    Returns ``None`` -- never raises (CONSTRAINT #6) -- when the state file
    is missing, empty, malformed, or holds no parseable timestamp. A caller
    must treat ``None`` as "not durably known to have been dispatched" (i.e.
    not throttled): the conservative choice on a read failure is to make the
    digest ELIGIBLE to fire again, not to leave it silently withheld forever
    because of a corrupt state file.
    """
    path = _weekly_digest_state_path()
    try:
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        ts_str = raw.get("last_dispatched_at")
        if not isinstance(ts_str, str) or not ts_str:
            return None
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception as exc:  # noqa: BLE001 - CONSTRAINT #6, a state-file read must never raise
        logger.warning(
            "weekly_digest: failed to read durable state file '%s' (%s); "
            "treating as never dispatched.", path, exc,
        )
        return None


def _write_weekly_digest_state(
    *,
    last_dispatched_at: Optional[datetime],
    last_status: str,
    last_error: Optional[str] = None,
) -> None:
    """Persist the weekly digest's dispatch outcome atomically.

    Mirrors this codebase's established write-to-temp-then-rename convention
    (``execution/kill_switch.py::activate``, ``watch_engine.py::save_watch_state``,
    ``shared/env_io.py::write_many_atomic``) so a crash mid-write can never
    leave a truncated/corrupt state file behind.

    ``last_status`` is one of ``"sent"`` (``compose_digest()`` returned items
    and ``send_alert()`` was called), ``"no_items"`` (``compose_digest()``
    returned an honestly empty payload -- see the fallback ladder in
    ``.claude/weekly-digest_implementation_plan.md`` §4), or ``"failed"`` (an
    unexpected exception was raised before/during dispatch). ``last_error``
    is ``str(exc)`` when ``last_status == "failed"``, else ``None``.

    On a ``"failed"`` call the CALLER is responsible for passing the PRIOR
    ``last_dispatched_at`` (or ``None``) rather than advancing it -- this
    function always writes exactly what it is given; it never advances the
    throttle clock on its own. That matches this feature's pre-existing
    behavior of never updating the in-process throttle timestamp when an
    exception was raised before a dispatch attempt completed.

    Never raises (CONSTRAINT #6) -- a write failure is logged and otherwise
    ignored. This file is a durability aid layered on top of the in-process
    cache, not the sole mechanism by which throttling functions within one
    process's own lifetime; see ``maybe_dispatch_weekly_digest``'s own
    docstring for how a failed write here is recovered from on a
    subsequent check within the SAME process (a persistent failure across
    a restart is not recoverable, since the in-process record is lost too
    -- disclosed there, not silently papered over).

    Uses ``reporting.atomic_write.atomic_write_json`` (temp-file name
    scoped by pid+thread-id) rather than a hand-rolled
    ``path.with_suffix(".tmp")`` write -- ``settings.OUTPUT_DIR`` is
    shared across every git worktree/checkout on this machine, so two
    ``OrchestratorDaemon`` processes can legitimately race on this exact
    path; a plain ``.tmp`` suffix would let them collide on the same temp
    file, which is the exact collision ``atomic_write_json`` exists to
    avoid (see its own docstring).
    """
    path = _weekly_digest_state_path()
    try:
        payload = {
            "last_dispatched_at": (
                last_dispatched_at.astimezone(timezone.utc).isoformat()
                if last_dispatched_at is not None else None
            ),
            "last_status": last_status,
            "last_error": last_error,
            "last_checked_at": datetime.now(timezone.utc).isoformat(),
        }
        atomic_write_json(path, payload)
    except Exception as exc:  # noqa: BLE001 - CONSTRAINT #6, a state-file write must never raise
        logger.warning(
            "weekly_digest: failed to persist durable state file '%s': %s", path, exc,
        )


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

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Build the warm DataEngine + EngineContext once, then start the
        interval timer thread (if configured, or if a self-gated periodic
        check that needs the loop regardless of the pipeline interval is
        enabled). Idempotent."""
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

        # Previously gated on `self._interval_seconds > 0` alone -- under the
        # realistic default deployment (ORCHESTRATOR_INTERVAL_SECONDS=0, "on-
        # demand only"), no timer thread was ever created, so the self-gated
        # periodic checks at the top of _timer_loop
        # (maybe_refresh_google_trends, maybe_dispatch_weekly_digest) never
        # ran automatically. Each check that genuinely needs the timer loop
        # names ITS OWN flag here, rather than depending on whichever one
        # happens to be on.
        #
        # Combined with _PARKED_TIMER_POLL_SECONDS bounding the loop's park
        # below, this is what lets these checks actually fire on a
        # recurring cadence rather than once (or never) per process
        # lifetime. This is a startup-time snapshot of each flag -- flipping
        # one live via runtime_flags after a thread-less daemon has already
        # started takes effect only on the next daemon restart, the same
        # "applies: next_daemon_restart" contract most settings in this
        # codebase already carry.
        needs_timer_thread = (
            self._interval_seconds > 0
            or settings.WEEKLY_DIGEST_ENABLED
            or settings.GOOGLE_TRENDS_ENABLED
        )
        if needs_timer_thread:
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
        if data_engine.live_data_configured():
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
            logger.warning("FRED_API_KEY not configured. Operating with deterministic MockDataEngine.")
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
        (self-gates internally, like ``maybe_refresh_google_trends``) AND from ``trigger_run`` -- ``settings.ORCHESTRATOR_INTERVAL_SECONDS``
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

            # Each symbol is isolated in its own try/except so one bad symbol
            # (a malformed stitcher input, a transient DB write error, ...)
            # never aborts the remaining symbols in this pass -- matching this
            # repo's convention for per-ticker loops (see CLAUDE.md).
            for sym in symbols:
                try:
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
                except Exception as sym_exc:  # noqa: BLE001 - one bad symbol must not abort the rest
                    logger.warning(
                        "maybe_refresh_google_trends: symbol %s failed: %s", sym, sym_exc
                    )

            # Update the throttle timestamp regardless of whether individual
            # symbols failed above -- otherwise a single bad symbol would
            # defeat the throttle entirely and every subsequent timer wake
            # would retry immediately with no backoff.
            self._last_google_trends_refresh = time.monotonic()
        except Exception as exc:
            logger.warning("maybe_refresh_google_trends: unexpected failure: %s", exc)


    def maybe_dispatch_weekly_digest(self) -> None:
        """Periodic trigger for the weekly digest.

        Gated by ``settings.WEEKLY_DIGEST_ENABLED``. Throttled to at most
        once per ``settings.WEEKLY_DIGEST_INTERVAL_HOURS`` via a DURABLE
        state file (``_weekly_digest_state_path()``, under
        ``settings.OUTPUT_DIR``) -- NOT just the in-process
        ``self._last_weekly_digest_dispatch`` attribute, which resets to
        unset on every fresh ``OrchestratorDaemon()`` construction (i.e.
        every daemon restart).

        Why the in-process attribute alone was not enough (2026-09 fix):
        a restart occurring more than ``settings.ALERT_DEDUP_WINDOW_SECONDS``
        (900s / 15 min default) after the digest's last successful send --
        the realistic case, since restarts happen hours or days apart, not
        minutes -- used to (a) treat the fresh instance as never having
        dispatched (the throttle check's ``> 0.0`` guard on an unset
        attribute is ``False``) AND (b) fall outside
        ``observability.alerts.send_alert()``'s own ``dedup_key``
        suppression window, which exists to collapse RAPID re-evaluation of
        a still-true condition within roughly the same short window (its own
        module docstring's example: "sustained portfolio heat on every
        pipeline cycle") -- not to provide durable "already sent this exact
        key, ever" protection hours or days later. Net effect: a daemon
        restart mid-week used to re-fire the digest immediately. See
        ``.claude/weekly-digest_task.md``'s WP-D/E audit for the full
        writeup, and ``tests/test_daemon_runtime.py::TestWeeklyDigest``'s
        durability-across-restart test for the regression proof.

        The durable file is now consulted on EVERY check, not just the
        first one made by a given process (2026-09 follow-up fix) --
        ``last_dispatch`` is the MAX of the durable file's value and this
        process's own in-process record, for two independent reasons:
        (1) if a prior durable-write attempt failed, this process must
        still never forget a dispatch it ITSELF already made (see the
        catch-up-retry paragraph below); (2) re-reading the file on every
        check -- not caching it after the first read for the rest of the
        process's life -- lets this process notice a DIFFERENT daemon
        process's dispatch within one polling interval, narrowing (though
        not eliminating; there is no cross-process file lock here) the
        window for two co-existing daemon processes (a documented,
        real-if-uncommon operational hazard -- see the
        ``ORCHESTRATOR_DAEMON_ENABLED`` bullet in CLAUDE.md) to both
        independently decide to dispatch. The pre-existing ``dedup_key``
        passed to ``send_alert()`` is kept too, as defense in depth for a
        rapid duplicate call within one process's own short window -- but
        note it is PURELY in-process (``observability/alerts.py``'s own
        docstring: "never persisted to disk"), so it offers no cross-process
        protection at all, which is exactly why the durable file is the
        real mechanism here.

        Catch-up retry for a previously-failed durable write: if this
        process's own in-process record is MORE RECENT than what the
        durable file currently shows, a prior ``_write_weekly_digest_state``
        call must have failed -- this method retries that write on every
        subsequent check until it succeeds, rather than leaving the file
        stuck at a stale value forever. This is what actually closes the
        restart gap: without it, a persistently-failing write would leave
        the durable file wrong indefinitely, and a RESTART (which loses
        the in-process record entirely) would then re-send.

        A "no_items" outcome (``compose_digest()`` returned an honestly
        empty payload -- the realistic default state right after a fresh
        install/restart, per the introducing plan's own framing) does
        ``[nothing to advance the throttle]``: unlike a real "sent"
        dispatch, it never updates ``last_dispatched_at`` (in-process or
        durable) -- only a diagnostic ``last_status``/``last_checked_at``
        is recorded. Advancing the throttle on a no-op check used to defer
        the NEXT check by the full interval (up to 7 days at the default),
        even though real data could show up within hours.

        Never raises (CONSTRAINT #6).
        """
        if not settings.WEEKLY_DIGEST_ENABLED:
            return

        try:
            interval = timedelta(hours=settings.WEEKLY_DIGEST_INTERVAL_HOURS)
            now = datetime.now(timezone.utc)

            durable_last_dispatch = _read_weekly_digest_last_dispatch()
            in_process_last_dispatch = getattr(self, "_last_weekly_digest_dispatch", None)
            candidates = [d for d in (durable_last_dispatch, in_process_last_dispatch) if d is not None]
            last_dispatch = max(candidates) if candidates else None

            # Catch-up retry: this process knows about a dispatch the
            # durable file doesn't yet reflect -- a prior write attempt
            # failed. Retry it now; see this method's own docstring.
            if in_process_last_dispatch is not None and (
                durable_last_dispatch is None or in_process_last_dispatch > durable_last_dispatch
            ):
                _write_weekly_digest_state(last_dispatched_at=in_process_last_dispatch, last_status="sent")

            if last_dispatch is not None and (now - last_dispatch) < interval:
                self._last_weekly_digest_dispatch = last_dispatch
                return

            from pilots.weekly_digest import compose_digest
            from observability.alerts import send_alert

            # _weekly_digest_snapshot_path(), NOT compose_digest() called
            # bare -- see that helper's own docstring for the confirmed
            # bug this closes (the bare-call default resolved a
            # CWD-relative path the daemon's working directory almost
            # never matches, so this automatic dispatch path silently
            # never found real data in a real deployment).
            payload = compose_digest(_weekly_digest_snapshot_path())
            if payload and payload.items:
                lines = [f"Weekly Digest ({len(payload.items)} items):"]
                for item in payload.items:
                    lines.append(f"- {item.symbol} ({item.selection_type}): {item.reason}")
                message = "\n".join(lines)

                week_id = int(now.timestamp() / (7 * 86400))
                dedup_key = f"weekly_digest_{week_id}"

                # send_alert() never raises (see its own module docstring) --
                # a channel-level failure (ntfy unreachable, an SMTP error,
                # ...) is caught and logged at ERROR *inside* send_alert
                # itself, per channel. There is no return value/exception
                # from here that could distinguish "delivered" from "every
                # channel failed", so last_status below can only honestly
                # mean "a dispatch was attempted", not "confirmed delivered".
                # Full webapp-surfacing of PER-CHANNEL delivery failure is a
                # disclosed, NOT-done follow-up -- see this PR's summary.
                send_alert("INFO", message, dedup_key=dedup_key)
                logger.info(
                    "maybe_dispatch_weekly_digest: Dispatched weekly digest with %d items.",
                    len(payload.items),
                )
                self._last_weekly_digest_dispatch = now
                _write_weekly_digest_state(last_dispatched_at=now, last_status="sent")
            else:
                logger.info("maybe_dispatch_weekly_digest: No items for weekly digest.")
                # Do NOT advance the throttle clock (in-process OR
                # durable) -- see this method's own docstring on why a
                # no-op check must retry on the next wake, not defer up to
                # a full WEEKLY_DIGEST_INTERVAL_HOURS.
                _write_weekly_digest_state(last_dispatched_at=last_dispatch, last_status="no_items")
        except Exception as exc:  # noqa: BLE001 - CONSTRAINT #6, this check must never break the caller
            logger.warning("maybe_dispatch_weekly_digest: unexpected failure: %s", exc)
            # Distinct, durable record of the failure -- never silently
            # dropped (CONSTRAINT #6 / plan §6's fabrication-risk checklist)
            # -- WITHOUT advancing the throttle clock: a genuine failure
            # (e.g. compose_digest() itself raising) should be retried on
            # the next check, matching this method's pre-existing behavior
            # of never updating the throttle timestamp on this path.
            # last_dispatched_at is RE-READ from disk (not assumed from the
            # in-process cache) so a failure record can never clobber a real
            # prior successful-dispatch timestamp with a stale/None value.
            try:
                _write_weekly_digest_state(
                    last_dispatched_at=_read_weekly_digest_last_dispatch(),
                    last_status="failed",
                    last_error=str(exc),
                )
            except Exception:  # pragma: no cover - best-effort only
                pass


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
            self.maybe_refresh_google_trends()
            self.maybe_dispatch_weekly_digest()
            # Same "called unconditionally, self-gates internally" contract --
            # see maybe_alert_on_pipeline_stall's own docstring.
            self.maybe_alert_on_pipeline_stall()
            with self._lock:
                interval = self._interval_seconds
            if self._stop_event.is_set():
                break
            if interval <= 0:
                # Bounded park (NOT an unbounded wait()): _stop_event.wait(0)
                # would spin a core, but an unbounded self._wake_event.wait()
                # -- woken only by set_interval()/shutdown() -- would leave
                # every self-gated periodic check above (this loop's own
                # maybe_dispatch_weekly_digest included) with no chance to
                # run again for the rest of the process's life once parked.
                # See _PARKED_TIMER_POLL_SECONDS's own module-level docstring
                # for the full rationale; every check above already self-
                # gates on its own settings flag, so a periodic wake-and-
                # recheck costs nothing when they're disabled.
                self._wake_event.wait(timeout=_PARKED_TIMER_POLL_SECONDS)
                continue
            if self._wake_event.wait(timeout=interval):
                continue  # interval changed OR shutting down -- re-check at the top
            if self._stop_event.is_set():
                break
            if settings.RUNTIME_FLAGS_REFRESH_ENABLED:
                self.maybe_refresh_settings()
            self.maybe_refresh_google_trends()
            self.maybe_dispatch_weekly_digest()
            self.maybe_alert_on_pipeline_stall()
            # ALREADY_RUNNING (previous interval cycle still in flight) is
            # expected and fine -- just proceed to the next wait.
            if is_automatic_run_gated(
                datetime.now(timezone.utc), extended_hours_only=settings.ORCHESTRATOR_EXTENDED_HOURS_ONLY
            ):
                logger.debug("Market-hours gate: skipping interval cycle (outside 4am-8pm ET weekday window).")
                continue
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
