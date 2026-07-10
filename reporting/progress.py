"""
reporting/progress.py
=====================
Single source of truth for live **pipeline progress** telemetry.

Why this module exists
----------------------
The Streamlit Command Center (and any external watchdog) needs to answer one
question while a run is in flight: *how far along is it?* Before this module the
backend emitted **no numeric progress at all** — only coarse, best-effort stage
banners scraped from the run log (see
``gui/orchestrator_runner.py::compute_stage_status``). That gave 4 discrete
stage badges but never a real percentage and never a "12 of 48 symbols" count.

``ProgressReporter`` fixes that: the orchestrator / advisory pipelines drive it
as they work, and it atomically writes a small JSON snapshot to
``OUTPUT_DIR/progress.json`` on every update. The GUI polls ``read_progress()``
to render a 0–100 % bar.

Design contract (frozen — GUI + backend both depend on these exact names)
------------------------------------------------------------------------
``output/progress.json`` payload::

    {
      "run_id":        "orch-1720..."|null,
      "state":         "running"|"succeeded"|"failed",
      "stage":         "forecasting",
      "stage_index":   3,      # 0-based index into the stages list
      "stage_total":   6,
      "symbols_done":  12,
      "symbols_total": 48,
      "percent":       58.3,   # 0..100 float, monotonic within a run
      "message":       "Forecasting AAPL",
      "started_at":    "ISO-8601 UTC",
      "updated_at":    "ISO-8601 UTC"
    }

Percent formula::

    within  = symbols_done / symbols_total   if symbols_total > 0 else 0.0
    percent = 100 * (stage_index + within) / stage_total     # clamped to [0, 100]

So each of ``stage_total`` stages is one equal slice of the bar, and per-symbol
loops fill their slice smoothly as ``advance_symbol()`` is called.

Invariants enforced (per the platform CONSTRAINTS)
--------------------------------------------------
* **#4 no fabricated metrics** — ``read_progress()`` returns ``None`` when there
  is no real progress file; the GUI must show an indeterminate spinner, never a
  made-up number.
* **#5 / #6 dead-letter** — every filesystem write is wrapped in try/except and
  logged; a progress-write failure can NEVER crash a pipeline cycle. Readers
  never raise either.
* **thread-safety** — ``advance_symbol()`` is called concurrently from the
  orchestrator's ``ThreadPoolExecutor`` (``ADVISORY_MAX_CONCURRENCY`` workers),
  so the symbol counter is guarded by a ``threading.Lock``.

``output/progress.json`` is per-machine runtime state (gitignored via the
``output/`` convention) and is intentionally never committed.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from settings import settings

logger = logging.getLogger(__name__)

# Canonical artifact location. Callers/tests may override via the ``output_dir``
# argument on ``ProgressReporter`` / ``read_progress``.
PROGRESS_FILENAME = "progress.json"

# Terminal states that mark a finished run.
_TERMINAL_STATES = frozenset({"succeeded", "failed"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp_pct(value: float) -> float:
    """Clamp a percentage into the closed interval [0, 100]."""
    if value != value:  # NaN guard — never emit a fabricated/garbage percent
        return 0.0
    return max(0.0, min(100.0, float(value)))


def _parse_dt(raw: object) -> datetime:
    """Best-effort ISO-8601 → aware datetime; falls back to *now* (never raises)."""
    if isinstance(raw, str):
        try:
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            pass
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ProgressState:
    """Immutable snapshot of a run's progress, as read back from disk.

    This is the shape the GUI consumes. All numeric fields are already
    normalized (``percent`` clamped to [0, 100]); consumers can render it
    directly without re-validating.
    """

    run_id: Optional[str]
    state: str
    stage: str
    stage_index: int
    stage_total: int
    symbols_done: int
    symbols_total: int
    percent: float
    message: str
    started_at: datetime
    updated_at: datetime

    def age_seconds(self) -> float:
        """Seconds since ``updated_at`` (UTC). Used by the GUI to detect a stale
        progress file left behind by a crashed/killed run."""
        return (datetime.now(timezone.utc) - self.updated_at).total_seconds()

    @property
    def is_terminal(self) -> bool:
        """True once the run has reached ``succeeded``/``failed``."""
        return self.state in _TERMINAL_STATES

    @classmethod
    def from_dict(cls, payload: dict) -> "ProgressState":
        """Reconstruct from a parsed ``progress.json`` dict, tolerating missing
        or malformed keys (defaults fill gaps — never raises on shape drift)."""
        try:
            stage_total = int(payload.get("stage_total", 0) or 0)
        except (TypeError, ValueError):
            stage_total = 0
        try:
            stage_index = int(payload.get("stage_index", 0) or 0)
        except (TypeError, ValueError):
            stage_index = 0
        try:
            symbols_done = int(payload.get("symbols_done", 0) or 0)
        except (TypeError, ValueError):
            symbols_done = 0
        try:
            symbols_total = int(payload.get("symbols_total", 0) or 0)
        except (TypeError, ValueError):
            symbols_total = 0
        try:
            percent = _clamp_pct(float(payload.get("percent", 0.0) or 0.0))
        except (TypeError, ValueError):
            percent = 0.0

        run_id = payload.get("run_id")
        return cls(
            run_id=str(run_id) if run_id is not None else None,
            state=str(payload.get("state", "running") or "running"),
            stage=str(payload.get("stage", "") or ""),
            stage_index=stage_index,
            stage_total=stage_total,
            symbols_done=symbols_done,
            symbols_total=symbols_total,
            percent=percent,
            message=str(payload.get("message", "") or ""),
            started_at=_parse_dt(payload.get("started_at")),
            updated_at=_parse_dt(payload.get("updated_at")),
        )


def compute_percent(
    stage_index: int, stage_total: int, symbols_done: int, symbols_total: int
) -> float:
    """Pure percent formula (see module docstring). Clamped to [0, 100].

    Each of ``stage_total`` stages is an equal slice; a per-symbol stage fills
    its slice as ``symbols_done`` approaches ``symbols_total``.
    """
    if stage_total <= 0:
        return 0.0
    within = 0.0
    if symbols_total > 0:
        within = symbols_done / symbols_total
        # Guard against advance_symbol overshoot (defensive; keeps within ≤ 1).
        within = max(0.0, min(1.0, within))
    return _clamp_pct(100.0 * (stage_index + within) / stage_total)


class ProgressReporter:
    """Drives ``output/progress.json`` as a pipeline cycle runs.

    Lifecycle::

        reporter = ProgressReporter(stages=["data", "macro", "forecast", ...])
        reporter.start_stage("data")                       # stage 0
        ...
        reporter.start_stage("forecast", symbols_total=48) # stage k
        for sym in tickers:                                # (may be concurrent)
            ...
            reporter.advance_symbol(f"Forecasting {sym}")
        reporter.finish("succeeded")                       # or "failed"

    Every mutating call rewrites the JSON atomically (write-then-rename, mirroring
    ``execution/kill_switch.py::activate``). All writes are best-effort: a failure
    is logged and swallowed so the pipeline is never held hostage to a bad disk
    (CONSTRAINT #6).

    Parameters
    ----------
    stages:
        Ordered list of stage names. ``len(stages)`` becomes ``stage_total``.
    run_id:
        Optional identifier correlating this progress with a daemon RunRecord /
        subprocess run. ``None`` for ad-hoc/local runs.
    output_dir:
        Directory to write ``progress.json`` into. Defaults to
        ``settings.OUTPUT_DIR``; overridden in tests.
    """

    def __init__(
        self,
        stages: List[str],
        *,
        run_id: Optional[str] = None,
        output_dir: Optional[Path] = None,
    ) -> None:
        self._stages: List[str] = list(stages) if stages else []
        self._run_id = run_id
        self._output_dir = Path(output_dir) if output_dir is not None else settings.OUTPUT_DIR
        self._path = self._output_dir / PROGRESS_FILENAME

        self._lock = threading.Lock()  # guards ALL mutable counters below
        self._started_at = _now_iso()
        self._stage_index = 0
        self._stage_name = self._stages[0] if self._stages else ""
        self._symbols_total = 0
        self._symbols_done = 0
        self._message = ""
        self._state = "running"

        # Emit an initial 0% snapshot so the GUI sees "running" immediately.
        with self._lock:
            self._write_locked()

    # -- public API --------------------------------------------------------

    def start_stage(self, name: str, *, symbols_total: int = 0) -> None:
        """Advance to the named stage and (re)set its per-symbol counter.

        ``name`` is matched against the constructor's ``stages`` list to derive
        the stage index; an unknown name keeps the current index but updates the
        displayed stage label (so callers can't accidentally rewind the bar).
        """
        with self._lock:
            try:
                self._stage_index = self._stages.index(name)
            except ValueError:
                # Unknown stage name: keep ordinal position, still surface label.
                logger.debug("ProgressReporter.start_stage: unknown stage %r", name)
            self._stage_name = name
            self._symbols_total = max(0, int(symbols_total))
            self._symbols_done = 0
            self._message = ""
            self._write_locked()

    def advance_symbol(self, message: str = "") -> None:
        """Increment the per-symbol counter by one (THREAD-SAFE).

        The increment, the message update, and the atomic file write all happen
        under one lock hold. Serializing the write is deliberate: if the snapshot
        were built (or the file replaced) outside the lock, a slow thread holding
        a stale snapshot could win the final ``os.replace`` and the persisted
        counter would lag the true in-memory value. Progress writes are tiny, so
        serializing them costs nothing on the real compute path. Overshoot beyond
        ``symbols_total`` is clamped by the percent formula.
        """
        with self._lock:
            self._symbols_done += 1
            if message:
                self._message = message
            self._write_locked()

    def set_message(self, msg: str) -> None:
        """Update the free-text status line without touching any counter."""
        with self._lock:
            self._message = msg
            self._write_locked()

    def finish(self, state: str = "succeeded") -> None:
        """Mark the run terminal (``succeeded`` or ``failed``).

        On success the bar is pinned to 100 %. The file is left on disk (not
        deleted) so the GUI can render a final "done" frame before the next run
        overwrites it; ``read_progress`` callers use ``age_seconds`` /
        ``is_terminal`` to decide when to stop showing it.
        """
        with self._lock:
            self._state = state if state in _TERMINAL_STATES else "succeeded"
            if self._state == "succeeded":
                # Pin to the end of the bar regardless of counter rounding.
                self._stage_index = max(0, len(self._stages) - 1)
                self._symbols_total = max(self._symbols_total, 1)
                self._symbols_done = self._symbols_total
            self._message = self._message or self._state
            self._write_locked(force_full=(self._state == "succeeded"))

    # -- internals ---------------------------------------------------------

    def _snapshot(self, *, force_full: bool) -> dict:
        """Build the JSON payload from current state. **Caller must hold
        ``self._lock``** — every invocation is from inside a locked section so
        the counters are read atomically with the mutation that preceded them."""
        stage_total = len(self._stages)
        if force_full and self._state == "succeeded":
            percent = 100.0
        else:
            percent = compute_percent(
                self._stage_index, stage_total, self._symbols_done, self._symbols_total
            )
        return {
            "run_id": self._run_id,
            "state": self._state,
            "stage": self._stage_name,
            "stage_index": self._stage_index,
            "stage_total": stage_total,
            "symbols_done": self._symbols_done,
            "symbols_total": self._symbols_total,
            "percent": round(percent, 2),
            "message": self._message,
            "started_at": self._started_at,
            "updated_at": _now_iso(),
        }

    def _write_locked(self, *, force_full: bool = False) -> None:
        """Atomically persist the current snapshot. **Caller must hold the lock.**

        Never raises (CONSTRAINT #6). Because the whole build+replace runs under
        the lock, the file always reflects the most recent committed state — the
        last writer is necessarily the last mutator.
        """
        try:
            payload = self._snapshot(force_full=force_full)
            self._output_dir.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(f".tmp.{os.getpid()}")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp, self._path)  # atomic on POSIX + Windows
        except Exception:  # noqa: BLE001 — dead-letter: progress must not crash runs
            logger.warning("Failed to write progress snapshot to %s", self._path, exc_info=True)


def read_progress(output_dir: Optional[Path] = None) -> Optional[ProgressState]:
    """Read the current progress snapshot, or ``None`` if unavailable.

    Never raises (CONSTRAINT #6). Returns ``None`` when the file is missing,
    empty, or unparseable — the GUI must treat ``None`` as "no real progress"
    and show an indeterminate indicator rather than a fabricated number
    (CONSTRAINT #4).
    """
    base = Path(output_dir) if output_dir is not None else settings.OUTPUT_DIR
    path = base / PROGRESS_FILENAME
    try:
        if not path.exists():
            return None
        raw = path.read_text(encoding="utf-8")
        if not raw.strip():
            return None
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return None
        return ProgressState.from_dict(payload)
    except Exception:  # noqa: BLE001 — reader must never raise
        logger.debug("Could not read progress snapshot from %s", path, exc_info=True)
        return None


def clear_progress(output_dir: Optional[Path] = None) -> None:
    """Remove the progress file if present (best-effort; never raises).

    Useful at the very start of a run to avoid a stale terminal snapshot from a
    previous cycle briefly showing through before the first ``_write``.
    """
    base = Path(output_dir) if output_dir is not None else settings.OUTPUT_DIR
    path = base / PROGRESS_FILENAME
    try:
        if path.exists():
            path.unlink()
    except OSError:
        logger.debug("Could not clear progress snapshot at %s", path, exc_info=True)
