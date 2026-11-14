"""Phase 4 of the four-agent Robinhood plan: the unattended cycle's plumbing.

``/rh-agentic-cycle`` (``.claude/commands/rh-agentic-cycle.md``) is an LLM
command that runs the four agents: Portfolio & Risk Manager first (so the
preflight's quotes are fresh and the queue's 30-minute staleness budget is
not spent on research), then Research Scout and Strategy Analyst (in either
order or in parallel, covering only the preflight-passed orders), then the
Trader. When the preflight passes nothing, ``finish --nothing-passed`` ends
the cycle without the other three. Everything around it that can be
deterministic code lives here, so no "should we run?", "did it finish?" or
"what goes in the push?" decision is left to model judgement:

* :func:`gate` — whether a cycle should run for the current queue at all.
  It skips (cheaply, with no LLM call) when the mode is off, the kill switch
  is on, the US market is closed or closes within 20 minutes, the queue is
  missing/stale (or within 10 minutes of stale, so the cycle can finish
  before the preflight's own staleness veto), has nothing actionable, or
  this queue was already handled. Anything unreadable is a skip
  (CONSTRAINT #6: fail closed).
* ``cycle.json`` per run (``OUTPUT_DIR/agentic/<run_id>/``) — each stage's
  start, end, status and reason. It is the audit trail and how :func:`gate`
  knows a queue was already handled. Every read-modify-write holds a per-run
  file lock and replaces the file atomically, so the Scout and Analyst can
  record their stages at the same moment. ``OUTPUT_DIR/agentic/cycles.jsonl`` gets
  one line per finished or failed cycle.
* :func:`fail_cycle` — marks the cycle failed and sends ONE push.
* :func:`build_summary` — the push text, built only from files
  (``preflight.json``, the placed ledger, validated theses), never from LLM
  prose. A number in the push is a number in those files.
* :func:`launch` — what the launchd timer runs: take a single-flight lock,
  gate, then run ``claude -p "/rh-agentic-cycle --launched"`` with a hard
  timeout and an explicit tool allowlist parsed from the agent definitions.

This module never calls a Robinhood tool and holds no order logic (the
``_EXECUTION_ZONE_GUARDED_FILES`` AST guard in ``tests/test_pipeline_smoke.py``
covers it). Placing stays the Trader agent's job, and every order still waits
for the operator's tap in the Robinhood app.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Sequence, Set, Tuple

from execution.agentic_preflight import (
    DEFAULT_STALE_AFTER_MINUTES,
    PREFLIGHT_FILENAME,
    _output_dir,
    _parse_dt,
)

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = REPO_ROOT / ".claude" / "agents"

CYCLE_FILENAME = "cycle.json"
CYCLE_LOG_FILENAME = "cycles.jsonl"
LOCK_FILENAME = "cycle.lock"

# Risk runs first: its preflight needs fresh quotes and decides which orders
# the Scout and Analyst must cover. Scout and Analyst need only risk, so they
# may run in either order or concurrently; the Trader needs all three.
STAGES: Tuple[str, ...] = ("risk", "scout", "analyst", "trader")
STAGE_PREREQS: Dict[str, Tuple[str, ...]] = {
    "risk": (),
    "scout": ("risk",),
    "analyst": ("risk",),
    "trader": ("risk", "scout", "analyst"),
}
STAGE_AGENTS: Dict[str, str] = {
    "risk": "rh-risk-manager",
    "scout": "rh-research-scout",
    "analyst": "rh-strategy-analyst",
    "trader": "rh-trader",
}
# Stage statuses that count as "done" when finishing a cycle.
_STAGE_DONE = ("completed", "skipped")
# Reason recorded on the stages ``finish --nothing-passed`` skips.
REASON_NOTHING_PASSED = "nothing_passed"
# The coordinator's own tools, on top of the four agents' allowlists.
# "Agent" is Claude Code's subagent tool ("Task" in older releases).
COORDINATOR_TOOLS: Tuple[str, ...] = ("Agent", "Task", "Read", "Bash", "Glob", "Grep")

# Hard ceiling on one unattended cycle. A code constant, not a setting (the
# feature freeze forbids new flags): four agents with a handful of MCP calls
# each finish in minutes; 30 min means something is stuck.
LAUNCH_TIMEOUT_SECONDS = 1800
# Gate headroom (code constants, not settings: the feature freeze forbids new
# flags). A cycle takes minutes; starting one with less than this left before
# the preflight's own QUEUE_STALE veto, or this close to the bell, would only
# end in a vetoed or after-hours run.
QUEUE_STALE_HEADROOM_MINUTES = 10.0
CLOSE_BUFFER_MINUTES = 20.0
# Bound on waiting for another writer's cycle.json lock (writes take ms).
STATE_LOCK_TIMEOUT_SECONDS = 30.0
# ntfy accepts 4096 bytes; leave room for the multibyte "…" and headers.
NTFY_MAX_CHARS = 3800

EXIT_RUN = 0
EXIT_ERROR = 2
EXIT_SKIP = 3

# Gate skip reasons (stable codes; the runbook explains each).
SKIP_MODE_OFF = "mode_off"
SKIP_KILL_SWITCH = "kill_switch_active"
SKIP_MARKET_CLOSED = "market_closed"
SKIP_QUEUE_MISSING = "queue_missing"
SKIP_QUEUE_UNREADABLE = "queue_unreadable"
SKIP_QUEUE_TIMESTAMP = "queue_timestamp_unavailable"
SKIP_QUEUE_STALE = "queue_stale"
SKIP_QUEUE_NEAR_STALE = "queue_near_stale"
SKIP_NEAR_CLOSE = "near_market_close"
SKIP_NO_ACTIONABLE = "no_actionable_intents"
SKIP_ALREADY_HANDLED = "already_handled"
SKIP_IN_PROGRESS = "cycle_in_progress"
SKIP_STATE_UNAVAILABLE = "state_unavailable"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def run_id_for_queue(queue: Mapping[str, Any]) -> Optional[str]:
    """Same id the preflight uses (``%Y%m%dT%H%M%SZ`` of ``generated_at``), or None."""
    gen = _parse_dt(queue.get("generated_at"))
    return gen.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ") if gen else None


def _agentic_dir(output_dir: Optional[Any]) -> Path:
    return _output_dir(output_dir) / "agentic"


def _read_json(path: Path) -> Optional[Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json_atomic(path: Path, data: Mapping[str, Any]) -> None:
    """Write via a unique temp file + ``os.replace`` (readers never see a
    partial file, and two writers never share a temp name)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2, sort_keys=True))
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def _append_log(output_dir: Optional[Any], record: Mapping[str, Any]) -> None:
    """One line per finished/failed cycle. Never raises."""
    try:
        path = _agentic_dir(output_dir) / CYCLE_LOG_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(record), sort_keys=True) + "\n")
    except Exception as exc:
        logger.warning("agentic_cycle: could not append to cycles.jsonl (%s)", exc)


def _actionable(intent: Mapping[str, Any], mode: str) -> bool:
    """Same per-intent gate the preflight applies first (check 1)."""
    if mode == "live":
        return intent.get("allow_place") is True
    return intent.get("gate_allowed") is True


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------

@dataclass
class GateResult:
    run: bool
    reason: Optional[str]
    run_id: Optional[str] = None
    mode: Optional[str] = None
    n_actionable: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def minutes_to_regular_close(now: datetime) -> Optional[float]:
    """Minutes until today's regular 16:00 ET close, or None outside regular
    hours (weekend, before 09:30 or at/after 16:00 ET). Pure.

    Uses ``shared.market_hours``' regular-session clock. Disclosed gap: it has
    no early-close calendar, so on a half day the 20-minute buffer is measured
    from 16:00; :func:`gate`'s ``market_open`` input (FMP's live
    ``isMarketOpen``) still stops a cycle once the half day has closed.
    """
    from shared.market_hours import _ET, _RTH_CLOSE_HM, is_us_market_open

    now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    if not is_us_market_open(now):
        return None
    now_et = now.astimezone(_ET)
    close_et = now_et.replace(hour=_RTH_CLOSE_HM[0], minute=_RTH_CLOSE_HM[1], second=0, microsecond=0)
    left = (close_et - now_et).total_seconds() / 60.0
    return left if left > 0 else None


def gate(
    queue: Optional[Mapping[str, Any]],
    *,
    now: datetime,
    settings_mode: str,
    kill_switch_active: bool,
    market_open: bool,
    existing_cycle: Optional[Mapping[str, Any]] = None,
    queue_error: Optional[str] = None,
    stale_after_minutes: float = DEFAULT_STALE_AFTER_MINUTES,
    stale_headroom_minutes: float = QUEUE_STALE_HEADROOM_MINUTES,
    close_buffer_minutes: float = CLOSE_BUFFER_MINUTES,
) -> GateResult:
    """Decide whether one cycle should run for ``queue``. Pure.

    The checks run cheapest-and-safest first; the first failing one is the
    reason. ``existing_cycle`` is this queue's ``cycle.json`` if one exists:
    any status (running, completed, failed) means the queue was already
    handled, so a failed cycle is never retried automatically every 15
    minutes. A new queue from the daemon gets a fresh run.

    Headroom: a cycle needs time, so it is skipped when ``now`` is outside
    regular hours or within ``close_buffer_minutes`` of the regular close
    (``near_market_close``), and when the queue is older than
    ``stale_after_minutes - stale_headroom_minutes`` (``queue_near_stale``):
    started later, the preflight's own ``queue_stale`` veto would fire
    mid-cycle.
    """
    now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    s_mode = str(settings_mode or "off").strip().lower()
    if s_mode not in ("live", "review"):
        return GateResult(False, SKIP_MODE_OFF, mode=s_mode)
    if kill_switch_active:
        return GateResult(False, SKIP_KILL_SWITCH, mode=s_mode)
    if not market_open:
        return GateResult(False, SKIP_MARKET_CLOSED, mode=s_mode)
    to_close = minutes_to_regular_close(now)
    if to_close is None:
        return GateResult(False, SKIP_MARKET_CLOSED, mode=s_mode)
    if to_close <= close_buffer_minutes:
        return GateResult(False, SKIP_NEAR_CLOSE, mode=s_mode)
    if queue_error is not None:
        return GateResult(False, queue_error, mode=s_mode)
    if queue is None:
        return GateResult(False, SKIP_QUEUE_MISSING, mode=s_mode)
    if queue.get("kill_switch_active"):
        return GateResult(False, SKIP_KILL_SWITCH, mode=s_mode)
    q_mode = str(queue.get("mode") or "off").strip().lower()
    if q_mode not in ("live", "review"):
        return GateResult(False, SKIP_MODE_OFF, mode=q_mode)
    # The stricter of the two modes wins: a queue written in live mode is only
    # reviewed if the operator has since switched settings back to review.
    mode = "live" if (q_mode == "live" and s_mode == "live") else "review"
    run_id = run_id_for_queue(queue)
    gen = _parse_dt(queue.get("generated_at"))
    if gen is None or run_id is None:
        return GateResult(False, SKIP_QUEUE_TIMESTAMP, mode=mode)
    age_min = (now - gen).total_seconds() / 60.0
    if age_min > stale_after_minutes:
        return GateResult(False, SKIP_QUEUE_STALE, run_id=run_id, mode=mode)
    if age_min > stale_after_minutes - stale_headroom_minutes:
        return GateResult(False, SKIP_QUEUE_NEAR_STALE, run_id=run_id, mode=mode)
    intents = [i for i in (queue.get("intents") or []) if isinstance(i, Mapping)]
    # Actionability follows the QUEUE's own mode (that is what the daemon's
    # gate fields mean), the same rule the preflight applies.
    n_act = sum(1 for i in intents if _actionable(i, q_mode))
    if n_act == 0:
        return GateResult(False, SKIP_NO_ACTIONABLE, run_id=run_id, mode=mode)
    if existing_cycle is not None:
        return GateResult(False, SKIP_ALREADY_HANDLED, run_id=run_id, mode=mode, n_actionable=n_act)
    return GateResult(True, None, run_id=run_id, mode=mode, n_actionable=n_act)


def _load_queue(output_dir: Optional[Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    path = _output_dir(output_dir) / "execution_queue.json"
    if not path.exists():
        return None, None
    data = _read_json(path)
    if not isinstance(data, dict):
        return None, SKIP_QUEUE_UNREADABLE
    return data, None


def evaluate_gate(output_dir: Optional[Any] = None, *, now: Optional[datetime] = None) -> GateResult:
    """:func:`gate` fed from live state. Any failure reading state → skip."""
    now = now or _now()
    try:
        from settings import settings
        from execution.kill_switch import GlobalKillSwitch
        from shared.market_hours import is_us_market_open_now

        queue, q_err = _load_queue(output_dir)
        existing = None
        if queue is not None:
            rid = run_id_for_queue(queue)
            if rid is not None:
                existing = read_cycle(rid, output_dir)
        return gate(
            queue,
            now=now,
            settings_mode=settings.ROBINHOOD_EXECUTION_MODE,
            kill_switch_active=GlobalKillSwitch().is_active(),
            market_open=is_us_market_open_now(now),
            existing_cycle=existing,
            queue_error=q_err,
        )
    except Exception as exc:
        logger.warning("agentic_cycle: gate state unavailable (%s); skipping", exc)
        return GateResult(False, SKIP_STATE_UNAVAILABLE)


# ---------------------------------------------------------------------------
# Single-flight lock
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def cycle_lock(output_dir: Optional[Any] = None) -> Iterator[bool]:
    """Non-blocking exclusive lock on ``agentic/cycle.lock``.

    Yields True when held, False when another process holds it or locking is
    unavailable (no ``fcntl``): fail closed, the caller must not run.
    """
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-POSIX
        yield False
        return
    path = _agentic_dir(output_dir) / LOCK_FILENAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = path.open("a+")
    except Exception as exc:
        logger.warning("agentic_cycle: cannot open lock file (%s)", exc)
        yield False
        return
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()


def lock_is_free(output_dir: Optional[Any] = None) -> bool:
    with cycle_lock(output_dir) as held:
        return held


# ---------------------------------------------------------------------------
# cycle.json
# ---------------------------------------------------------------------------

def cycle_path(run_id: str, output_dir: Optional[Any] = None) -> Path:
    return _agentic_dir(output_dir) / run_id / CYCLE_FILENAME


def read_cycle(run_id: str, output_dir: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    data = _read_json(cycle_path(run_id, output_dir))
    return data if isinstance(data, dict) else None


@contextlib.contextmanager
def _state_lock(run_id: str, output_dir: Optional[Any] = None,
                timeout: float = STATE_LOCK_TIMEOUT_SECONDS) -> Iterator[None]:
    """Exclusive lock on ``<run>/cycle.json.lock`` around a read-modify-write.

    Separate from :func:`cycle_lock` (which ``launch`` holds for the whole
    cycle). Waits up to ``timeout`` seconds, then raises ``TimeoutError``.
    Without ``fcntl`` (non-POSIX) it does not lock.
    """
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-POSIX
        yield
        return
    path = cycle_path(run_id, output_dir).with_name(CYCLE_FILENAME + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as fh:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"cycle.json for run {run_id} is locked by another writer")
                time.sleep(0.02)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _new_stage_entry() -> Dict[str, Any]:
    return {"status": "pending", "started_at": None, "finished_at": None, "reason": None}


def _stage_entry(state: Dict[str, Any], stage: str) -> Dict[str, Any]:
    """``state['stages'][stage]``, created pending if absent (a cycle.json
    written before a stage existed must not crash the update)."""
    stages = state.setdefault("stages", {})
    if not isinstance(stages.get(stage), dict):
        stages[stage] = _new_stage_entry()
    return stages[stage]


def start_cycle(gate_result: GateResult, queue: Mapping[str, Any], *, now: Optional[datetime] = None,
                output_dir: Optional[Any] = None, launched: bool = False) -> Dict[str, Any]:
    now = now or _now()
    state = {
        "run_id": gate_result.run_id,
        "queue_generated_at": queue.get("generated_at"),
        "mode": gate_result.mode,
        "launched_by": "timer" if launched else "manual",
        "status": "running",
        "started_at": _iso(now),
        "finished_at": None,
        "failed_stage": None,
        "reason": None,
        "stages": {s: _new_stage_entry() for s in STAGES},
    }
    with _state_lock(str(gate_result.run_id), output_dir):
        _write_json_atomic(cycle_path(str(gate_result.run_id), output_dir), state)
    return state


def record_stage(run_id: str, stage: str, status: str, *, reason: Optional[str] = None,
                 now: Optional[datetime] = None, output_dir: Optional[Any] = None) -> Dict[str, Any]:
    """Mark ``stage`` as ``started`` or ``completed``.

    A stage may start only once its prerequisites (:data:`STAGE_PREREQS`) have
    completed: ``risk`` first, then ``scout`` and ``analyst`` in either order
    or at the same time, then ``trader``. Concurrent calls are serialised by
    a per-run file lock, so neither update is lost.
    """
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}; expected one of {STAGES}")
    if status not in ("started", "completed"):
        raise ValueError("status must be 'started' or 'completed' (use fail for failures)")
    with _state_lock(run_id, output_dir):
        state = read_cycle(run_id, output_dir)
        if state is None:
            raise ValueError(f"no cycle.json for run {run_id}")
        if state.get("status") != "running":
            raise ValueError(f"cycle {run_id} is {state.get('status')}, not running")
        for earlier in STAGE_PREREQS[stage]:
            if _stage_entry(state, earlier).get("status") != "completed":
                raise ValueError(f"stage {stage!r} cannot start before {earlier!r} completed")
        entry = _stage_entry(state, stage)
        if entry.get("status") in ("completed", "skipped", "failed"):
            raise ValueError(f"stage {stage!r} is already {entry.get('status')}")
        now = now or _now()
        if status == "started":
            entry.update(status="running", started_at=_iso(now))
        else:
            entry.update(status="completed", finished_at=_iso(now), reason=reason)
        _write_json_atomic(cycle_path(run_id, output_dir), state)
    return state


def _current_stage(state: Mapping[str, Any]) -> str:
    """The stage a failure belongs to: the first one running, else the first
    one not done yet, else ``finish``."""
    stages = state.get("stages", {}) if isinstance(state.get("stages"), Mapping) else {}
    for s in STAGES:
        if (stages.get(s) or {}).get("status") == "running":
            return s
    for s in STAGES:
        if (stages.get(s) or {}).get("status") not in _STAGE_DONE:
            return s
    return "finish"


def preflight_passed(run_id: str, output_dir: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    """What this run's preflight passed, read from files only, or None.

    ``{"n_passed", "passed_client_order_ids", "symbols", "run_blockers"}``.
    None when ``cycle.json`` or ``preflight.json`` is missing/unreadable or the
    preflight is for another queue (fail closed: unknown is not "zero").
    """
    state = read_cycle(run_id, output_dir)
    pf = _read_json(_agentic_dir(output_dir) / run_id / PREFLIGHT_FILENAME)
    if state is None or not isinstance(pf, dict) or not isinstance(pf.get("verdicts"), list):
        return None
    if pf.get("queue_generated_at") != state.get("queue_generated_at"):
        return None
    passed = [v for v in pf["verdicts"] if isinstance(v, Mapping) and v.get("passed") is True]
    return {
        "n_passed": len(passed),
        "passed_client_order_ids": sorted(str(v.get("client_order_id")) for v in passed),
        "symbols": sorted({str(v.get("symbol") or "").upper() for v in passed if v.get("symbol")}),
        "run_blockers": list(pf.get("run_blockers") or []),
    }


def finish_cycle(run_id: str, *, now: Optional[datetime] = None,
                 output_dir: Optional[Any] = None, nothing_passed: bool = False) -> Dict[str, Any]:
    """Mark the cycle completed.

    Normally every stage must have completed. With ``nothing_passed`` the
    cycle may end right after ``risk``: this run's ``preflight.json`` must
    exist, be for this queue and pass zero intents (checked here, from the
    file, not taken on the coordinator's word); every stage not yet done is
    then recorded ``skipped`` with reason ``nothing_passed``.
    """
    with _state_lock(run_id, output_dir):
        state = read_cycle(run_id, output_dir)
        if state is None:
            raise ValueError(f"no cycle.json for run {run_id}")
        if state.get("status") != "running":
            raise ValueError(f"cycle {run_id} is {state.get('status')}, not running")
        now = now or _now()
        if nothing_passed:
            if _stage_entry(state, "risk").get("status") != "completed":
                raise ValueError("cannot finish with nothing passed: the risk stage has not completed")
            facts = preflight_passed(run_id, output_dir)
            if facts is None:
                raise ValueError("cannot finish with nothing passed: no readable preflight for this queue")
            if facts["n_passed"]:
                raise ValueError(f"cannot finish with nothing passed: the preflight passed "
                                 f"{facts['n_passed']} intent(s)")
            for s in STAGES:
                entry = _stage_entry(state, s)
                if entry.get("status") not in _STAGE_DONE:
                    entry.update(status="skipped", finished_at=_iso(now), reason=REASON_NOTHING_PASSED)
        missing = [s for s in STAGES if _stage_entry(state, s).get("status") not in _STAGE_DONE]
        if missing:
            raise ValueError(f"cannot finish: stages not completed: {missing}")
        state.update(status="completed", finished_at=_iso(now))
        if nothing_passed:
            state["reason"] = REASON_NOTHING_PASSED
        _write_json_atomic(cycle_path(run_id, output_dir), state)
    record = {"run_id": run_id, "status": "completed", "mode": state.get("mode"),
              "finished_at": state["finished_at"]}
    if nothing_passed:
        record["reason"] = REASON_NOTHING_PASSED
    _append_log(output_dir, record)
    return state


def fail_cycle(run_id: Optional[str], stage: Optional[str], reason: str, *,
               now: Optional[datetime] = None, output_dir: Optional[Any] = None,
               notify: bool = True,
               notifier: Optional[Callable[..., bool]] = None) -> Dict[str, Any]:
    """Mark the cycle failed (creating ``cycle.json`` if needed), log it, push once.

    Never raises: a failure path that crashed would hide the failure.
    """
    now = now or _now()
    state: Dict[str, Any] = {}

    def _record() -> None:
        nonlocal state, stage
        if run_id:
            state = read_cycle(run_id, output_dir) or {}
        stage = stage or (_current_stage(state) if state else "launch")
        if stage in STAGES and isinstance(state.get("stages"), dict):
            _stage_entry(state, stage).update(status="failed", finished_at=_iso(now), reason=reason)
        state.update(run_id=run_id, status="failed", failed_stage=stage, reason=reason,
                     finished_at=_iso(now))
        state.setdefault("started_at", _iso(now))
        if run_id:
            _write_json_atomic(cycle_path(run_id, output_dir), state)

    try:
        try:
            if run_id:
                with _state_lock(run_id, output_dir):
                    _record()
            else:
                _record()
        except TimeoutError as exc:
            # Recording the failure matters more than the lock: write anyway.
            logger.warning("agentic_cycle: %s; recording the failure without the lock", exc)
            _record()
        _append_log(output_dir, {"run_id": run_id, "status": "failed", "failed_stage": stage,
                                 "reason": reason, "finished_at": state["finished_at"]})
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("agentic_cycle: could not record failure (%s)", exc)
    if notify:
        try:
            send = notifier or _default_notifier()
            send(
                "Agentic cycle stopped",
                f"Stage '{state.get('failed_stage') or stage}' failed: {reason}. "
                f"No further orders were sent this cycle (run {run_id or 'unknown'}).",
                priority="high",
            )
        except Exception as exc:
            logger.warning("agentic_cycle: failure push not sent (%s)", exc)
    return state


def latest_cycle(output_dir: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    """The most recent run folder's ``cycle.json`` (run ids sort by time), or None."""
    root = _agentic_dir(output_dir)
    try:
        runs = sorted((p for p in root.iterdir() if p.is_dir() and (p / CYCLE_FILENAME).exists()),
                      key=lambda p: p.name, reverse=True)
    except Exception:
        return None
    for p in runs:
        data = _read_json(p / CYCLE_FILENAME)
        if isinstance(data, dict):
            return data
    return None


# ---------------------------------------------------------------------------
# Summary push (built from files only)
# ---------------------------------------------------------------------------

def _default_notifier() -> Callable[..., bool]:
    from alerting import notify
    return notify


def _fmt_qty(q: Any) -> str:
    try:
        f = float(q)
    except (TypeError, ValueError):
        return "?"
    return f"{f:g}"


def _trim(text: str, limit: int = NTFY_MAX_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _clip(text: str, limit: Optional[int]) -> str:
    if limit is None or len(text) <= limit:
        return text
    if limit <= 1:
        return "…"
    return text[: limit - 1].rstrip() + "…"


def _flags_of(t: Any) -> List[str]:
    """The Analyst's flags on a thesis: short strings only, at most five."""
    raw = t.get("flags") if isinstance(t, Mapping) else None
    if not isinstance(raw, list):
        return []
    out = [str(f).strip()[:_FLAG_MAX_CHARS] for f in raw if isinstance(f, (str, int, float)) and str(f).strip()]
    return out[:_FLAGS_MAX]


_FLAG_MAX_CHARS = 40
_FLAGS_MAX = 5
# Thesis/invalidation length caps tried in turn until the push fits; the
# per-order warnings (flags, earnings) and the veto/pending lists are never
# shortened before the theses are.
_THESIS_CAPS: Tuple[Optional[int], ...] = (None, 160, 100, 60, 30, 0)


def build_summary(
    *,
    mode: str,
    preflight: Optional[Mapping[str, Any]],
    placed_records: Sequence[Mapping[str, Any]],
    valid_theses: Mapping[str, Mapping[str, Any]],
    stale_pending: Sequence[Mapping[str, Any]] = (),
    earnings: Optional[Mapping[str, Any]] = None,
    not_sent_notes: Optional[Mapping[str, str]] = None,
) -> Tuple[str, str]:
    """``(title, body)`` for the end-of-cycle push. Pure.

    Every number comes from ``preflight`` (qty, limit price), the placed
    records or the research file's ``next_earnings`` date (``earnings``:
    ``{SYMBOL: "YYYY-MM-DD"}``, validated entries only); every thesis,
    invalidation and flag list is from a thesis the validator accepted. An
    order without a valid thesis says "thesis unavailable". In live mode a
    preflight-passed order the Trader did not send is listed too, with the
    Trader's skip note when ``not_sent_notes`` has one, so it never vanishes
    from the push. Nothing here is model prose the validator did not see.

    To fit ntfy's limit the theses and invalidations are shortened first;
    only if that is not enough is the tail cut.
    """
    verdicts = [v for v in (preflight or {}).get("verdicts") or [] if isinstance(v, Mapping)]
    by_coid = {str(v.get("client_order_id")): v for v in verdicts}
    blockers = list((preflight or {}).get("run_blockers") or [])
    earnings = earnings or {}
    not_sent_notes = not_sent_notes or {}

    def order_line(sym: str, side: str, qty: Any, coid: str, cap: Optional[int]) -> str:
        v = by_coid.get(coid) or {}
        limit = v.get("limit_price")
        price = f", limit ${float(limit):.2f}" if isinstance(limit, (int, float)) and not isinstance(limit, bool) else ""
        t = valid_theses.get(coid)
        thesis = str(t.get("thesis") or "").strip() if isinstance(t, Mapping) else ""
        invalid = str(t.get("invalidation") or "").strip() if isinstance(t, Mapping) else ""
        if not thesis:
            text = "thesis unavailable"
        elif cap == 0:
            text = "thesis omitted for length"
        else:
            text = _clip(thesis, cap)
        parts = [f"{side.upper()} {_fmt_qty(qty)} {sym}{price}: {text}"]
        if thesis and invalid and cap != 0:
            parts.append(f"Invalid if: {_clip(invalid, cap)}")
        flags = _flags_of(t)
        if flags:
            parts.append("Flags: " + ", ".join(flags))
        ne = earnings.get(sym.upper())
        if ne:
            parts.append(f"Next earnings {ne}")
        return " | ".join(parts)

    passed = [v for v in verdicts if v.get("passed")]
    placed_coids = {str(r.get("client_order_id") or "") for r in placed_records}
    if mode == "live":
        n = len(placed_records)
        title = (f"{n} trade{'s' if n != 1 else ''} awaiting your approval in Robinhood"
                 if n else "Agentic cycle: no orders sent")
    else:
        title = f"Review run: {len(passed)} would pass, {len(verdicts) - len(passed)} vetoed"

    def render(cap: Optional[int]) -> str:
        lines: List[str] = []
        if preflight is None:
            lines.append("No preflight for this queue, so nothing was checked against the account.")
        if blockers:
            lines.append("Blocked for the whole cycle: " + ", ".join(blockers) + ".")
        if mode == "live":
            for r in placed_records:
                lines.append(order_line(str(r.get("symbol") or "?"), str(r.get("side") or "?"),
                                        r.get("qty"), str(r.get("client_order_id") or ""), cap))
            not_sent = [v for v in passed if str(v.get("client_order_id") or "") not in placed_coids]
            for v in not_sent:
                coid = str(v.get("client_order_id") or "")
                note = not_sent_notes.get(coid)
                lines.append(f"Passed but not sent: {str(v.get('side') or '?').upper()} {_fmt_qty(v.get('qty'))} "
                             f"{v.get('symbol') or '?'} ({note or 'no send recorded by the Trader'}).")
        else:
            for v in passed:
                lines.append("Would send " + order_line(str(v.get("symbol") or "?"), str(v.get("side") or "?"),
                                                        v.get("qty"), str(v.get("client_order_id") or ""), cap))
        vetoed = [v for v in verdicts if not v.get("passed")]
        if vetoed:
            lines.append("Vetoed: " + "; ".join(
                f"{v.get('symbol')} {str(v.get('side') or '').upper()} "
                f"({', '.join(v.get('reasons') or []) or 'no reason recorded'})"
                for v in vetoed) + ".")
        for p in stale_pending:
            lines.append(f"Still pending from earlier: {p.get('side', '?')} {p.get('symbol', '?')}. "
                         "It is no longer in the queue; decline it in the Robinhood app if you don't want it.")
        return "\n".join(lines) or "Nothing to report."

    body = render(None)
    for cap in _THESIS_CAPS[1:]:
        if len(body) <= NTFY_MAX_CHARS:
            break
        body = render(cap)
    return title, _trim(body)


def _skip_notes(output_dir: Optional[Any], verdicts: Sequence[Mapping[str, Any]],
                since: Optional[datetime]) -> Dict[str, str]:
    """``{client_order_id: note}`` from this cycle's ``skipped`` receipts.

    Receipts carry no client_order_id, so a passed verdict is matched on
    symbol + side, newest receipt since the cycle started. Never raises.
    """
    try:
        from execution.receipts_store import read_receipts
        receipts = read_receipts(output_dir)
    except Exception as exc:
        logger.warning("agentic_cycle: receipts unavailable for summary (%s)", exc)
        return {}
    latest: Dict[Tuple[str, str], str] = {}
    for r in receipts:
        if not isinstance(r, Mapping) or str(r.get("action") or "").lower() != "skipped":
            continue
        ts = _parse_dt(r.get("ts"))
        if since is not None and (ts is None or ts < since):
            continue
        note = str(r.get("note") or "").strip()[:120]
        latest[(str(r.get("symbol") or "").upper(), str(r.get("side") or "").lower())] = (
            f"skipped: {note}" if note else "skipped")
    out: Dict[str, str] = {}
    for v in verdicts:
        key = (str(v.get("symbol") or "").upper(), str(v.get("side") or "").lower())
        if key in latest:
            out[str(v.get("client_order_id") or "")] = latest[key]
    return out


def summary_for_run(run_id: str, output_dir: Optional[Any] = None) -> Tuple[str, str, Dict[str, Any]]:
    """Load the files for ``run_id`` and build the push. Returns (title, body, facts)."""
    from execution.agentic_research import earnings_dates_from_research, load_run_artifacts
    from execution.receipts_store import latest_approval_states, read_placed_ledger

    state = read_cycle(run_id, output_dir) or {}
    mode = str(state.get("mode") or "review")
    queue, _ = _load_queue(output_dir)
    same_queue = queue is not None and run_id_for_queue(queue) == run_id
    preflight = _read_json(_agentic_dir(output_dir) / run_id / PREFLIGHT_FILENAME)
    if not isinstance(preflight, dict) or (
            same_queue and preflight.get("queue_generated_at") != queue.get("generated_at")):
        preflight = None

    valid: Dict[str, Mapping[str, Any]] = {}
    earnings: Dict[str, str] = {}
    coids: Set[str] = set()
    if same_queue:
        coids = {str(i.get("client_order_id")) for i in queue.get("intents") or [] if isinstance(i, Mapping)}
        try:
            art = load_run_artifacts(queue, _output_dir(output_dir), preflight=preflight)
            doc = (art.get("theses") or {}).get("theses") or {}
            valid = {c: doc[c] for c in art.get("valid_thesis_ids") or [] if c in doc}
            ok_syms = set(art.get("valid_research_symbols") or [])
            earnings = {sym: d.isoformat() for sym, d in earnings_dates_from_research(art.get("research")).items()
                        if d is not None and sym in ok_syms}
        except Exception as exc:
            logger.warning("agentic_cycle: theses/research unavailable for summary (%s)", exc)
    elif preflight is not None:
        coids = {str(v.get("client_order_id")) for v in preflight.get("verdicts") or []}

    placed = [r for r in read_placed_ledger(output_dir)
              if str(r.get("client_order_id") or "") in coids and coids]

    queue_keys = set()
    if queue is not None:
        queue_keys = {(str(i.get("symbol") or "").upper(), str(i.get("side") or "").lower())
                      for i in queue.get("intents") or [] if isinstance(i, Mapping)}
    stale = [rec for rec in latest_approval_states(output_dir).values()
             if rec.get("state") == "pending"
             and (str(rec.get("symbol") or "").upper(), str(rec.get("side") or "").lower()) not in queue_keys]

    notes: Dict[str, str] = {}
    n_not_sent = 0
    if mode == "live" and preflight is not None:
        placed_coids = {str(r.get("client_order_id") or "") for r in placed}
        not_sent = [v for v in preflight.get("verdicts") or [] if isinstance(v, Mapping)
                    and v.get("passed") and str(v.get("client_order_id") or "") not in placed_coids]
        n_not_sent = len(not_sent)
        if not_sent:
            notes = _skip_notes(output_dir, not_sent, _parse_dt(state.get("started_at")))

    title, body = build_summary(mode=mode, preflight=preflight, placed_records=placed,
                                valid_theses=valid, stale_pending=stale, earnings=earnings,
                                not_sent_notes=notes)
    facts = {"run_id": run_id, "mode": mode, "n_placed": len(placed),
             "has_preflight": preflight is not None, "n_valid_theses": len(valid),
             "valid_thesis_ids": sorted(valid), "n_stale_pending": len(stale),
             "n_passed_not_sent": n_not_sent}
    return title, body, facts


def record_summary(run_id: str, title: str, body: str, facts: Mapping[str, Any],
                   output_dir: Optional[Any] = None) -> None:
    """Keep the push text and the validated thesis ids in ``cycle.json``.

    The webapp's pending-approvals card quotes a thesis only when its id is in
    ``valid_thesis_ids`` here, so it never re-validates against a queue that
    has since been replaced. Never raises.
    """
    try:
        with _state_lock(run_id, output_dir):
            state = read_cycle(run_id, output_dir)
            if state is None:
                return
            state["summary"] = {"title": title, "body": body}
            state["valid_thesis_ids"] = list(facts.get("valid_thesis_ids") or [])
            _write_json_atomic(cycle_path(run_id, output_dir), state)
    except Exception as exc:
        logger.warning("agentic_cycle: could not record summary (%s)", exc)


# ---------------------------------------------------------------------------
# Tool allowlist (parsed from the agent definitions, never hand-copied)
# ---------------------------------------------------------------------------

_TOOLS_LINE = re.compile(r"^tools:\s*(.+)$", re.MULTILINE)


def agent_tools(agent_name: str, agents_dir: Path = AGENTS_DIR) -> List[str]:
    text = (agents_dir / f"{agent_name}.md").read_text(encoding="utf-8")
    front = text.split("---", 2)[1] if text.startswith("---") else ""
    m = _TOOLS_LINE.search(front)
    if not m:
        raise ValueError(f"{agent_name}.md has no tools: line")
    return [t.strip() for t in m.group(1).split(",") if t.strip()]


def allowed_tools(agents_dir: Path = AGENTS_DIR) -> List[str]:
    tools = set(COORDINATOR_TOOLS)
    for name in STAGE_AGENTS.values():
        tools.update(agent_tools(name, agents_dir))
    return sorted(tools)


# ---------------------------------------------------------------------------
# Launcher (what the launchd timer runs)
# ---------------------------------------------------------------------------

def _output_tail(proc: Any, limit: int = 500) -> str:
    """Last ``limit`` characters of a finished (or timed-out) subprocess's
    stdout + stderr, stripped. ``""`` when nothing was captured. Never raises.

    The launchers run ``claude -p`` with ``capture_output=True``, so without
    this an error like "Failed to authenticate: OAuth session expired" is
    swallowed and the alert can only say "exited with code 1".
    """
    parts: List[str] = []
    for attr in ("stdout", "stderr"):
        val = getattr(proc, attr, None)
        if isinstance(val, bytes):
            val = val.decode("utf-8", errors="replace")
        if isinstance(val, str) and val.strip():
            parts.append(val.strip())
    return "\n".join(parts)[-limit:].strip()


def _last_line(text: str, limit: int = 300) -> str:
    """The last non-empty line of ``text``, trimmed to ``limit`` characters."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return lines[-1][:limit] if lines else ""


def _said(proc: Any, limit: int = 300) -> str:
    """What ``claude`` itself last said: the last line of stdout (its final
    message, or an error like "Failed to authenticate"), falling back to
    stderr only when stdout is empty. stderr is mostly library warnings
    (e.g. the MCP SDK's), which would otherwise bury the real message."""
    for attr in ("stdout", "stderr"):
        val = getattr(proc, attr, None)
        if isinstance(val, bytes):
            val = val.decode("utf-8", errors="replace")
        line = _last_line(val if isinstance(val, str) else "", limit)
        if line:
            return line
    return ""


def launch(*, output_dir: Optional[Any] = None, now: Optional[datetime] = None,
           runner: Callable[..., Any] = subprocess.run, claude_bin: str = "claude",
           notifier: Optional[Callable[..., bool]] = None) -> int:
    """Lock, gate, run the coordinator headless with a hard timeout. Never raises."""
    with cycle_lock(output_dir) as held:
        if not held:
            print(json.dumps({"run": False, "reason": SKIP_IN_PROGRESS}))
            return EXIT_SKIP
        g = evaluate_gate(output_dir, now=now)
        if not g.run:
            print(json.dumps(g.to_dict()))
            return EXIT_SKIP
        try:
            tools = allowed_tools()
        except Exception as exc:
            fail_cycle(g.run_id, "launch", f"agent definitions unreadable: {exc}",
                       output_dir=output_dir, notifier=notifier)
            return EXIT_ERROR
        cmd = [claude_bin, "-p", "/rh-agentic-cycle --launched", "--allowedTools", ",".join(tools)]
        reason: Optional[str] = None
        proc: Any = None
        try:
            proc = runner(cmd, cwd=str(REPO_ROOT), timeout=LAUNCH_TIMEOUT_SECONDS,
                          capture_output=True, text=True)
            if getattr(proc, "returncode", 1) != 0:
                reason = f"coordinator exited with code {getattr(proc, 'returncode', '?')}"
                said = _said(proc)
                if said:
                    reason = f"{reason}: {said}"
        except subprocess.TimeoutExpired as exc:
            reason = f"timed out after {LAUNCH_TIMEOUT_SECONDS} s"
            said = _said(exc)
            if said:
                reason = f"{reason}: {said}"
        except FileNotFoundError:
            reason = f"'{claude_bin}' CLI not found on PATH"
        except Exception as exc:
            reason = f"launch error: {exc}"

        state = read_cycle(str(g.run_id), output_dir)
        if state is None:
            if reason:  # it never got as far as starting the cycle
                fail_cycle(g.run_id, "launch", reason, output_dir=output_dir, notifier=notifier)
                return EXIT_ERROR
            # claude exited 0 without a cycle.json. Legitimate only if the
            # coordinator's own `begin` gate skipped because state changed in
            # between; re-gate to tell that apart from a coordinator that
            # never started the cycle (which must alert, not stay silent).
            g2 = evaluate_gate(output_dir, now=now)
            if g2.run:
                said = _said(proc) if proc is not None else ""
                why = "coordinator exited without starting the cycle although the gate still allows it"
                if said:
                    why = f"{why}: {said}"
                logger.warning("agentic_cycle: %s (run %s)", why, g.run_id)
                fail_cycle(g.run_id, "launch", why, output_dir=output_dir, notifier=notifier)
                return EXIT_ERROR
            logger.info("agentic_cycle: coordinator skipped run %s; gate now says %s", g.run_id, g2.reason)
            print(json.dumps(g2.to_dict()))
            return EXIT_SKIP
        if state.get("status") == "running":
            fail_cycle(g.run_id, None, reason or "coordinator ended without finishing the cycle",
                       output_dir=output_dir, notifier=notifier)
            return EXIT_ERROR
        return EXIT_RUN if state.get("status") == "completed" else EXIT_ERROR


# ---------------------------------------------------------------------------
# CLI (called by the coordinator command)
# ---------------------------------------------------------------------------

def _cmd_begin(args: argparse.Namespace) -> int:
    if not args.launched and not lock_is_free(args.output_dir):
        print(json.dumps({"run": False, "reason": SKIP_IN_PROGRESS}))
        return EXIT_SKIP
    g = evaluate_gate(args.output_dir)
    if not g.run:
        print(json.dumps(g.to_dict()))
        return EXIT_SKIP
    queue, _ = _load_queue(args.output_dir)
    start_cycle(g, queue or {}, output_dir=args.output_dir, launched=args.launched)
    print(json.dumps(g.to_dict()))
    return EXIT_RUN


def _cmd_stage(args: argparse.Namespace) -> int:
    try:
        record_stage(args.run_id, args.stage, args.status, reason=args.reason, output_dir=args.output_dir)
    except (ValueError, OSError) as exc:
        print(json.dumps({"error": str(exc)}))
        return EXIT_ERROR
    print(json.dumps({"run_id": args.run_id, "stage": args.stage, "status": args.status}))
    return EXIT_RUN


def _cmd_fail(args: argparse.Namespace) -> int:
    state = fail_cycle(args.run_id, args.stage, args.reason, output_dir=args.output_dir)
    print(json.dumps({"run_id": args.run_id, "status": state.get("status"),
                      "failed_stage": state.get("failed_stage")}))
    return EXIT_RUN


def _cmd_finish(args: argparse.Namespace) -> int:
    try:
        finish_cycle(args.run_id, output_dir=args.output_dir, nothing_passed=args.nothing_passed)
    except (ValueError, OSError) as exc:
        fail_cycle(args.run_id, None, str(exc), output_dir=args.output_dir)
        print(json.dumps({"error": str(exc)}))
        return EXIT_ERROR
    return _cmd_summary(args)


def _cmd_summary(args: argparse.Namespace) -> int:
    title, body, facts = summary_for_run(args.run_id, args.output_dir)
    record_summary(args.run_id, title, body, facts, args.output_dir)
    sent = None
    if args.notify:
        sent = bool(_default_notifier()(title, body))
    print(json.dumps({"title": title, "body": body, "facts": facts, "notified": sent}, indent=2))
    return EXIT_RUN


def _cmd_passed(args: argparse.Namespace) -> int:
    facts = preflight_passed(args.run_id, args.output_dir)
    if facts is None:
        print(json.dumps({"error": f"no readable preflight for this queue in run {args.run_id}"}))
        return EXIT_ERROR
    print(json.dumps(facts))
    return EXIT_RUN


def _cmd_gate(args: argparse.Namespace) -> int:
    g = evaluate_gate(args.output_dir)
    print(json.dumps(g.to_dict()))
    return EXIT_RUN if g.run else EXIT_SKIP


def _cmd_launch(args: argparse.Namespace) -> int:
    return launch(output_dir=args.output_dir)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Unattended four-agent Robinhood cycle (Phase 4).")
    ap.add_argument("--output-dir", help="override $OUTPUT_DIR")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("gate", help="print whether a cycle would run now (exit 0 run, 3 skip)")
    b = sub.add_parser("begin", help="gate and, if it passes, start this queue's cycle.json")
    b.add_argument("--launched", action="store_true", help="started by `launch` (which holds the lock)")
    s = sub.add_parser("stage", help="record a stage as started or completed")
    s.add_argument("run_id")
    s.add_argument("stage", choices=STAGES)
    s.add_argument("status", choices=("started", "completed"))
    s.add_argument("--reason")
    f = sub.add_parser("fail", help="mark the cycle failed and push one alert")
    f.add_argument("run_id")
    f.add_argument("stage")
    f.add_argument("--reason", required=True)
    pa = sub.add_parser("passed", help="print what this run's preflight passed (n_passed, ids, symbols)")
    pa.add_argument("run_id")
    fi = sub.add_parser("finish", help="mark the cycle completed, then print (and push) the summary")
    fi.add_argument("run_id")
    fi.add_argument("--notify", action="store_true")
    fi.add_argument("--nothing-passed", action="store_true",
                    help="end right after `risk` when the preflight passed zero intents "
                         "(scout/analyst/trader are recorded skipped: nothing_passed)")
    su = sub.add_parser("summary", help="print the summary push for a run")
    su.add_argument("run_id")
    su.add_argument("--notify", action="store_true")
    sub.add_parser("launch", help="lock + gate + run the coordinator headless (the timer's entry point)")
    sub.add_parser("allowed-tools", help="print the tool allowlist the launcher passes to claude")

    args = ap.parse_args(argv)
    if args.cmd == "allowed-tools":
        print("\n".join(allowed_tools()))
        return EXIT_RUN
    handlers = {"gate": _cmd_gate, "begin": _cmd_begin, "stage": _cmd_stage, "fail": _cmd_fail,
                "passed": _cmd_passed, "finish": _cmd_finish, "summary": _cmd_summary,
                "launch": _cmd_launch}
    return handlers[args.cmd](args)


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
