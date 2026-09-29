"""Daemon agentic-queue mode, run ownership, and the primary-mode side effects.

Step 5.3 of ``.claude/shrink_step5_retire_main_py_implementation_plan.md``.
With ``settings.DAEMON_AGENTIC_QUEUE_MODE=primary`` the orchestrator daemon
(``pipeline.production_steps.AgenticQueueStep``) writes the REAL
``queue_sources/advisory.json`` + ``execution_queue.json`` and runs the two
side effects main.py's ``_run_cycle`` used to own: the run-summary push and
the symbol watch engine. main.py then skips all of them (see
``daemon_owns_agentic_side_effects``), so there is only one writer.

Deliberately light (stdlib only at import time) because ``main.py`` imports
it too. ``watch_engine`` and ``alerting`` are imported inside the functions
that use them, as main.py does.

Run ownership
-------------
``AsyncPipelineRunner`` bounds a sync step with ``asyncio.wait_for(
asyncio.to_thread(...))``. A timeout cancels the await, NOT the worker
thread, so a timed-out ``AgenticQueueStep`` can keep running and write the
real queue after its cycle has been marked failed, possibly while the next
cycle is already running. To stop that:

* the step claims a token (``claim_queue_writer``) when it starts;
* the cycle (``main_orchestrator._main_body_impl``) releases that token in a
  ``finally`` when the runner returns OR raises, and a later claim replaces it;
* every real write commits through ``commit_guard(token)``, which holds one
  lock while it checks the token is still current and does the final
  ``os.replace``. ``release_queue_writer`` takes the same lock, so once the
  cycle has released the token no commit from that run can land. Pushes check
  ``is_current_queue_writer`` first (a push can't be un-sent, and the check
  is not atomic with it; see the walkthrough).
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, ContextManager, Iterator, List, Optional

logger = logging.getLogger(__name__)

DAEMON_AGENTIC_QUEUE_MODES = ("off", "shadow", "primary")

# ctx.context_extras keys written by AgenticQueueStep.
OWNER_TOKEN_KEY = "agentic_queue_owner_token"
MODE_USED_KEY = "agentic_queue_mode"


def resolve_daemon_agentic_queue_mode(value: Any) -> str:
    """Normalise a ``DAEMON_AGENTIC_QUEUE_MODE`` value; anything unknown is ``off``."""
    mode = str(value or "").strip().lower()
    return mode if mode in DAEMON_AGENTIC_QUEUE_MODES else "off"


def daemon_owns_agentic_side_effects(value: Any) -> bool:
    """True when the daemon is the queue/watch/summary-push writer (``primary``).

    main.py's ``_run_cycle`` checks this and skips its queue write, watch
    engine, summary push and ``daily_report.html`` so there are never two
    writers of ``execution_queue.json`` / ``watch_state.json``.
    """
    return resolve_daemon_agentic_queue_mode(value) == "primary"


# ---------------------------------------------------------------------------
# Run ownership
# ---------------------------------------------------------------------------

_owner_lock = threading.Lock()
_owner_current: Optional[int] = None
_owner_counter = 0


CYCLE_CLOSED_KEY = "agentic_queue_cycle_closed"


def claim_queue_writer(cycle_extras: Optional[dict] = None) -> Optional[int]:
    """Make a new token the current queue writer and return it.

    Any earlier token (a still-running thread from an older cycle) stops
    being current from this point on. With ``cycle_extras`` (the cycle's
    ``ctx.context_extras``), the token is also recorded there under
    ``OWNER_TOKEN_KEY``, and the claim is refused (returns None) if
    ``close_cycle_queue_writer`` already ran for that cycle -- the case of a
    worker thread that only started after its step had already timed out.
    """
    global _owner_current, _owner_counter
    with _owner_lock:
        if cycle_extras is not None and cycle_extras.get(CYCLE_CLOSED_KEY):
            return None
        _owner_counter += 1
        _owner_current = _owner_counter
        if cycle_extras is not None:
            cycle_extras[OWNER_TOKEN_KEY] = _owner_current
        return _owner_current


def close_cycle_queue_writer(cycle_extras: dict) -> None:
    """End the cycle: release its token (if any) and refuse any later claim
    for it. Called by ``main_orchestrator._main_body_impl`` in a ``finally``
    once the runner returns or raises."""
    global _owner_current
    with _owner_lock:
        cycle_extras[CYCLE_CLOSED_KEY] = True
        token = cycle_extras.get(OWNER_TOKEN_KEY)
        if token is not None and _owner_current == token:
            _owner_current = None


def release_queue_writer(token: Optional[int]) -> None:
    """End ``token``'s ownership (no-op if a newer token already replaced it).

    Blocks until any in-flight ``commit_guard`` body finishes, so after this
    returns no commit made under ``token`` can land.
    """
    global _owner_current
    if token is None:
        return
    with _owner_lock:
        if _owner_current == token:
            _owner_current = None


def is_current_queue_writer(token: Optional[int]) -> bool:
    """Whether ``token`` still owns the real queue."""
    with _owner_lock:
        return token is not None and _owner_current == token


@contextmanager
def commit_guard(token: Optional[int]) -> Iterator[bool]:
    """Hold the ownership lock for one commit; yields whether ``token`` owns it.

    Use it around the final ``os.replace`` only (it blocks
    ``release_queue_writer`` while held)::

        with commit_guard(token) as allowed:
            if allowed:
                tmp.replace(path)
    """
    with _owner_lock:
        yield token is not None and _owner_current == token


def guard_for(token: Optional[int]) -> Callable[[], ContextManager[bool]]:
    """A zero-arg ``commit_guard`` factory bound to ``token`` (the shape
    ``execution.compose``/``execution.queue_builder`` take)."""
    return lambda: commit_guard(token)


# ---------------------------------------------------------------------------
# Watch engine (port of main.py's _run_cycle block)
# ---------------------------------------------------------------------------

def run_watch_engine(
    recommendations: List[Any],
    *,
    rules_file: Any,
    state_path: Path,
    dashboard_url: Optional[str],
    owner_token: Optional[int] = None,
) -> Optional[list]:
    """Evaluate the symbol watch rules against this cycle's recommendations,
    dispatch the alerts, and save ``watch_state.json``.

    Same calls, same inputs and the same order as main.py's ``_run_cycle``
    block: load rules and the PREVIOUS state, evaluate against the CURRENT
    recommendations, dispatch, then always save. With ``owner_token`` set,
    nothing is dispatched unless the token still owns the queue, and the save
    commits through ``commit_guard``. Returns the alerts that were dispatched
    (``None`` when the step no longer owned the queue). Never raises.
    """
    try:
        from watch_engine import (  # noqa: PLC0415
            dispatch_watch_alerts,
            evaluate_watch_rules,
            load_watch_rules,
            load_watch_state,
            save_watch_state,
        )

        watch_rules = load_watch_rules(rules_file)
        prev_state = load_watch_state(state_path)
        alerts, new_state = evaluate_watch_rules(watch_rules, recommendations, prev_state)
        if owner_token is not None and not is_current_queue_writer(owner_token):
            logger.warning(
                "Symbol watch: this cycle no longer owns the agentic queue "
                "(it timed out or a newer cycle started); alerts not sent, state not saved."
            )
            return None
        dispatch_watch_alerts(alerts, dashboard_url=dashboard_url)
        # Always save -- keeps edge-trigger state current even on quiet runs.
        if owner_token is None:
            save_watch_state(new_state, state_path)
        else:
            with commit_guard(owner_token) as allowed:
                if allowed:
                    save_watch_state(new_state, state_path)
                else:
                    logger.warning("Symbol watch: ownership lost before the state save; not saved.")
        if alerts:
            logger.info(
                "Symbol watch: %d alert(s) dispatched across %d rule(s).",
                len(alerts), len(watch_rules),
            )
        return alerts
    except Exception as exc:  # noqa: BLE001 - non-critical, as in main.py
        logger.warning("Symbol watch alert evaluation failed (non-critical): %s", exc)
        return None


# ---------------------------------------------------------------------------
# Run-summary push (port of main.py's _run_cycle alerting block)
# ---------------------------------------------------------------------------

class _RunSummary:
    """``main.RunResult``-shaped view for ``alerting.summarize_run``."""

    def __init__(self, recommendations, errors, started_at, duration_seconds):
        self.recommendations = recommendations
        self.errors = errors
        self.started_at = started_at
        self.duration_seconds = duration_seconds


_clean_push_lock = threading.Lock()
_clean_push_day: Optional[str] = None


def _et_day(now: datetime) -> str:
    try:
        from zoneinfo import ZoneInfo

        return now.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:  # pragma: no cover - tzdata missing
        return now.astimezone(timezone.utc).date().isoformat()


def reset_clean_push_memory() -> None:
    """Forget the last clean-run push day (tests)."""
    global _clean_push_day
    with _clean_push_lock:
        _clean_push_day = None


def send_run_summary_push(
    recommendations: List[Any],
    errors: List[dict],
    *,
    started_at: datetime,
    finished_at: Optional[datetime] = None,
    owner_token: Optional[int] = None,
) -> Optional[str]:
    """main.py's summary push, for the daemon: log the ``summarize_run`` text,
    then push the high-priority error notification when any symbol failed,
    else the "Refresh Complete" notification.

    main.py sent the clean-run push once per LAUNCH, and its launchd job
    launched once per weekday. The daemon launches rarely and cycles hourly,
    so the clean push is sent at most once per US/Eastern day (kept in
    memory: a daemon restart can send a second one that day). The error push
    goes out on every cycle with errors, as in main.py. Returns the push
    kind sent (``"errors"``/``"clean"``) or None. Never raises.
    """
    global _clean_push_day
    try:
        from alerting import notify, summarize_run  # noqa: PLC0415

        started_utc = started_at.astimezone(timezone.utc)
        finished = finished_at or datetime.now(timezone.utc)
        view = _RunSummary(
            list(recommendations or []), list(errors or []), started_utc,
            max(0.0, (finished - started_utc).total_seconds()),
        )
        summary = summarize_run(view)
        logger.info("\n%s", summary)
        if owner_token is not None and not is_current_queue_writer(owner_token):
            logger.warning("Run-summary push skipped: this cycle no longer owns the agentic queue.")
            return None
        if view.errors:
            err_preview = ", ".join(
                f"{e.get('symbol', '?')} ({e.get('stage', '?')})" for e in view.errors[:3]
            )
            suffix = f" +{len(view.errors) - 3} more" if len(view.errors) > 3 else ""
            notify(
                title="InvestYo ⚠ Errors Detected",
                message=(
                    f"{len(view.errors)} symbol(s) failed: {err_preview}{suffix}\n"
                    f"OK={len(view.recommendations)}  "
                    f"Duration={view.duration_seconds:.1f}s"
                ),
                priority="high",
            )
            return "errors"
        day = _et_day(finished)
        with _clean_push_lock:
            if _clean_push_day == day:
                return None
            _clean_push_day = day
        notify(title="InvestYo ✓ Refresh Complete", message=summary, priority="default")
        return "clean"
    except Exception as exc:  # noqa: BLE001 - alerting must never fail a cycle
        logger.warning("Run-summary push failed (non-critical): %s", exc)
        return None
