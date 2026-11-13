"""Phase 4 of the four-agent Robinhood plan: the unattended cycle's plumbing.

``/rh-agentic-cycle`` (``.claude/commands/rh-agentic-cycle.md``) is an LLM
command that runs the four agents in order: Research Scout → Strategy Analyst
→ Portfolio & Risk Manager → Trader. Everything around it that can be
deterministic code lives here, so no "should we run?", "did it finish?" or
"what goes in the push?" decision is left to model judgement:

* :func:`gate` — whether a cycle should run for the current queue at all.
  It skips (cheaply, with no LLM call) when the mode is off, the kill switch
  is on, the US market is closed, the queue is missing/stale/has nothing
  actionable, or this queue was already handled. Anything unreadable is a
  skip (CONSTRAINT #6: fail closed).
* ``cycle.json`` per run (``OUTPUT_DIR/agentic/<run_id>/``) — each stage's
  start, end, status and reason. It is the audit trail and how :func:`gate`
  knows a queue was already handled. ``OUTPUT_DIR/agentic/cycles.jsonl`` gets
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
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

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

# Stage order is the plan's: each agent reads the previous one's file.
STAGES: Tuple[str, ...] = ("scout", "analyst", "risk", "trader")
STAGE_AGENTS: Dict[str, str] = {
    "scout": "rh-research-scout",
    "analyst": "rh-strategy-analyst",
    "risk": "rh-risk-manager",
    "trader": "rh-trader",
}
# The coordinator's own tools, on top of the four agents' allowlists.
# "Agent" is Claude Code's subagent tool ("Task" in older releases).
COORDINATOR_TOOLS: Tuple[str, ...] = ("Agent", "Task", "Read", "Bash", "Glob", "Grep")

# Hard ceiling on one unattended cycle. A code constant, not a setting (the
# feature freeze forbids new flags): four agents with a handful of MCP calls
# each finish in minutes; 30 min means something is stuck.
LAUNCH_TIMEOUT_SECONDS = 1800
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
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


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
) -> GateResult:
    """Decide whether one cycle should run for ``queue``. Pure.

    The checks run cheapest-and-safest first; the first failing one is the
    reason. ``existing_cycle`` is this queue's ``cycle.json`` if one exists:
    any status (running, completed, failed) means the queue was already
    handled, so a failed cycle is never retried automatically every 15
    minutes. A new queue from the daemon gets a fresh run.
    """
    now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    s_mode = str(settings_mode or "off").strip().lower()
    if s_mode not in ("live", "review"):
        return GateResult(False, SKIP_MODE_OFF, mode=s_mode)
    if kill_switch_active:
        return GateResult(False, SKIP_KILL_SWITCH, mode=s_mode)
    if not market_open:
        return GateResult(False, SKIP_MARKET_CLOSED, mode=s_mode)
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
    if (now - gen).total_seconds() / 60.0 > stale_after_minutes:
        return GateResult(False, SKIP_QUEUE_STALE, run_id=run_id, mode=mode)
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
        "stages": {s: {"status": "pending", "started_at": None, "finished_at": None, "reason": None}
                   for s in STAGES},
    }
    _write_json_atomic(cycle_path(str(gate_result.run_id), output_dir), state)
    return state


def record_stage(run_id: str, stage: str, status: str, *, reason: Optional[str] = None,
                 now: Optional[datetime] = None, output_dir: Optional[Any] = None) -> Dict[str, Any]:
    """Mark ``stage`` as ``started`` or ``completed``. Stages must run in order."""
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}; expected one of {STAGES}")
    if status not in ("started", "completed"):
        raise ValueError("status must be 'started' or 'completed' (use fail for failures)")
    state = read_cycle(run_id, output_dir)
    if state is None:
        raise ValueError(f"no cycle.json for run {run_id}")
    if state.get("status") != "running":
        raise ValueError(f"cycle {run_id} is {state.get('status')}, not running")
    idx = STAGES.index(stage)
    for earlier in STAGES[:idx]:
        if state["stages"][earlier]["status"] != "completed":
            raise ValueError(f"stage {stage!r} cannot start before {earlier!r} completed")
    now = now or _now()
    entry = state["stages"][stage]
    if status == "started":
        entry.update(status="running", started_at=_iso(now))
    else:
        entry.update(status="completed", finished_at=_iso(now), reason=reason)
    _write_json_atomic(cycle_path(run_id, output_dir), state)
    return state


def _current_stage(state: Mapping[str, Any]) -> str:
    for s in STAGES:
        if state.get("stages", {}).get(s, {}).get("status") != "completed":
            return s
    return "finish"


def finish_cycle(run_id: str, *, now: Optional[datetime] = None,
                 output_dir: Optional[Any] = None) -> Dict[str, Any]:
    """Mark the cycle completed. Every stage must have completed."""
    state = read_cycle(run_id, output_dir)
    if state is None:
        raise ValueError(f"no cycle.json for run {run_id}")
    missing = [s for s in STAGES if state["stages"][s]["status"] != "completed"]
    if missing:
        raise ValueError(f"cannot finish: stages not completed: {missing}")
    now = now or _now()
    state.update(status="completed", finished_at=_iso(now))
    _write_json_atomic(cycle_path(run_id, output_dir), state)
    _append_log(output_dir, {"run_id": run_id, "status": "completed", "mode": state.get("mode"),
                             "finished_at": state["finished_at"]})
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
    try:
        if run_id:
            state = read_cycle(run_id, output_dir) or {}
        stage = stage or (_current_stage(state) if state else "launch")
        if stage in STAGES and state.get("stages"):
            state["stages"][stage].update(status="failed", finished_at=_iso(now), reason=reason)
        state.update(run_id=run_id, status="failed", failed_stage=stage, reason=reason,
                     finished_at=_iso(now))
        state.setdefault("started_at", _iso(now))
        if run_id:
            _write_json_atomic(cycle_path(run_id, output_dir), state)
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


def build_summary(
    *,
    mode: str,
    preflight: Optional[Mapping[str, Any]],
    placed_records: Sequence[Mapping[str, Any]],
    valid_theses: Mapping[str, Mapping[str, Any]],
    stale_pending: Sequence[Mapping[str, Any]] = (),
) -> Tuple[str, str]:
    """``(title, body)`` for the end-of-cycle push. Pure.

    Every number comes from ``preflight`` (qty, limit price) or the placed
    records; every thesis is one the validator accepted. An order without a
    valid thesis says "thesis unavailable". Nothing here is model prose.
    """
    verdicts = [v for v in (preflight or {}).get("verdicts") or [] if isinstance(v, Mapping)]
    by_coid = {str(v.get("client_order_id")): v for v in verdicts}
    blockers = list((preflight or {}).get("run_blockers") or [])

    def thesis_for(coid: str) -> str:
        t = valid_theses.get(coid)
        text = str(t.get("thesis") or "").strip() if isinstance(t, Mapping) else ""
        return text or "thesis unavailable"

    def order_line(sym: str, side: str, qty: Any, coid: str) -> str:
        v = by_coid.get(coid) or {}
        limit = v.get("limit_price")
        price = f", limit ${float(limit):.2f}" if isinstance(limit, (int, float)) else ""
        return f"{side.upper()} {_fmt_qty(qty)} {sym}{price}: {thesis_for(coid)}"

    lines: List[str] = []
    if preflight is None:
        lines.append("No preflight for this queue, so nothing was checked against the account.")
    if blockers:
        lines.append("Blocked for the whole cycle: " + ", ".join(blockers) + ".")

    if mode == "live":
        n = len(placed_records)
        title = (f"{n} trade{'s' if n != 1 else ''} awaiting your approval in Robinhood"
                 if n else "Agentic cycle: no orders sent")
        for r in placed_records:
            lines.append(order_line(str(r.get("symbol") or "?"), str(r.get("side") or "?"),
                                    r.get("qty"), str(r.get("client_order_id") or "")))
    else:
        passed = [v for v in verdicts if v.get("passed")]
        title = f"Review run: {len(passed)} would pass, {len(verdicts) - len(passed)} vetoed"
        for v in passed:
            lines.append("Would send " + order_line(str(v.get("symbol") or "?"), str(v.get("side") or "?"),
                                                    v.get("qty"), str(v.get("client_order_id") or "")))

    vetoed = [v for v in verdicts if not v.get("passed")]
    if vetoed:
        lines.append("Vetoed: " + "; ".join(
            f"{v.get('symbol')} {str(v.get('side') or '').upper()} ({', '.join(v.get('reasons') or []) or 'no reason recorded'})"
            for v in vetoed) + ".")
    for p in stale_pending:
        lines.append(f"Still pending from earlier: {p.get('side', '?')} {p.get('symbol', '?')}. "
                     "It is no longer in the queue; decline it in the Robinhood app if you don't want it.")
    return title, _trim("\n".join(lines) or "Nothing to report.")


def summary_for_run(run_id: str, output_dir: Optional[Any] = None) -> Tuple[str, str, Dict[str, Any]]:
    """Load the files for ``run_id`` and build the push. Returns (title, body, facts)."""
    from execution.agentic_research import load_run_artifacts
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
    coids: set = set()
    if same_queue:
        coids = {str(i.get("client_order_id")) for i in queue.get("intents") or [] if isinstance(i, Mapping)}
        try:
            art = load_run_artifacts(queue, _output_dir(output_dir))
            doc = (art.get("theses") or {}).get("theses") or {}
            valid = {c: doc[c] for c in art.get("valid_thesis_ids") or [] if c in doc}
        except Exception as exc:
            logger.warning("agentic_cycle: theses unavailable for summary (%s)", exc)
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

    title, body = build_summary(mode=mode, preflight=preflight, placed_records=placed,
                                valid_theses=valid, stale_pending=stale)
    facts = {"run_id": run_id, "mode": mode, "n_placed": len(placed),
             "has_preflight": preflight is not None, "n_valid_theses": len(valid),
             "valid_thesis_ids": sorted(valid), "n_stale_pending": len(stale)}
    return title, body, facts


def record_summary(run_id: str, title: str, body: str, facts: Mapping[str, Any],
                   output_dir: Optional[Any] = None) -> None:
    """Keep the push text and the validated thesis ids in ``cycle.json``.

    The webapp's pending-approvals card quotes a thesis only when its id is in
    ``valid_thesis_ids`` here, so it never re-validates against a queue that
    has since been replaced. Never raises.
    """
    try:
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
            # The coordinator's own gate skipped (state changed in between).
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
    except ValueError as exc:
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
        finish_cycle(args.run_id, output_dir=args.output_dir)
    except ValueError as exc:
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
    fi = sub.add_parser("finish", help="mark the cycle completed, then print (and push) the summary")
    fi.add_argument("run_id")
    fi.add_argument("--notify", action="store_true")
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
                "finish": _cmd_finish, "summary": _cmd_summary, "launch": _cmd_launch}
    return handlers[args.cmd](args)


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
