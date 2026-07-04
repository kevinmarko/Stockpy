"""
gui/robinhood_execution_panel.py — read-side status for the Robinhood execution bridge.

Why this module exists
-----------------------
``execution/queue_builder.py`` (Tier 8) writes a gated, dry-run proposed-order
queue to ``output/execution_queue.json``.  A separate Claude Code agent (the
``robinhood-execution`` skill / ``/rh-execute`` command) is the only actor that
turns queue entries into real Robinhood MCP calls, appending outcomes to
``output/execution_receipts.jsonl``.  Until now neither file had a GUI view —
the operator had to inspect them by hand.  This module is the **read side**
only: it never contacts the MCP, never mutates the queue, and never writes
receipts.  It mirrors the shape of :mod:`gui.dead_letter` and
:mod:`gui.robinhood_mode` (tolerant, Streamlit-free, headlessly testable).

Public API
----------
:class:`QueuedIntent`      — one proposed order from the queue (frozen).
:class:`ExecutionQueueSnapshot` — the full queue payload (frozen).
:func:`read_execution_queue`     — parse ``output/execution_queue.json`` → snapshot or ``None``.
:func:`read_execution_receipts`  — tail ``output/execution_receipts.jsonl`` → list[dict].
:func:`queue_age_seconds`        — age of the snapshot relative to ``now``.
:func:`is_queue_stale`           — mirrors the skill's ~30-minute staleness rule.
:func:`mfa_secret_configured`    — whether ``RH_MFA_SECRET`` is set (boolean only — never the value).
:data:`EXECUTION_QUEUE_PATH`, :data:`EXECUTION_RECEIPTS_PATH` — canonical file paths.

Constraints honoured
---------------------
* CONSTRAINT #4 (no fabricated data): missing/corrupt files return ``None`` /
  ``[]`` — the GUI renders a "no data yet" hint, never a fabricated queue.
* CONSTRAINT #6 (dead-letter): every read is wrapped in try/except; a bad file
  never raises past this module.
* CONSTRAINT #3 (secrets): :func:`mfa_secret_configured` returns a boolean only
  — the MFA secret value itself is never read into a variable that could be
  displayed.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Resolve output paths without importing the full settings object so this
# module stays importable in minimal test environments (mirrors gui/dead_letter.py).
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
EXECUTION_QUEUE_PATH: Path = _REPO_ROOT / "output" / "execution_queue.json"
EXECUTION_RECEIPTS_PATH: Path = _REPO_ROOT / "output" / "execution_receipts.jsonl"

# Mirrors the staleness threshold documented in
# .claude/skills/robinhood-execution/SKILL.md ("more than ~30 minutes old").
STALE_QUEUE_SECONDS: float = 30 * 60.0


@dataclass(frozen=True)
class QueuedIntent:
    """One proposed order from ``output/execution_queue.json``.

    Attributes mirror the per-intent schema written by
    :func:`execution.queue_builder.build_execution_queue` verbatim — this
    module does not reinterpret or re-derive any field.
    """

    symbol: str
    action: str
    side: str
    qty: Optional[float]
    target_notional: Optional[float]
    conviction: Optional[float]
    gate_allowed: bool
    gate_reasons: List[str]
    allow_place: bool
    rationale: str
    client_order_id: str


@dataclass(frozen=True)
class ExecutionQueueSnapshot:
    """Full ``output/execution_queue.json`` payload."""

    generated_at: str
    mode: str
    kill_switch_active: bool
    max_notional_per_order: float
    n_intents: int
    n_placeable: int
    intents: List[QueuedIntent]


def _coerce_intent(raw: Dict[str, Any]) -> Optional[QueuedIntent]:
    try:
        return QueuedIntent(
            symbol=str(raw.get("symbol", "")).upper(),
            action=str(raw.get("action", "")),
            side=str(raw.get("side", "")),
            qty=raw.get("qty"),
            target_notional=raw.get("target_notional"),
            conviction=raw.get("conviction"),
            gate_allowed=bool(raw.get("gate_allowed", False)),
            gate_reasons=list(raw.get("gate_reasons") or []),
            allow_place=bool(raw.get("allow_place", False)),
            rationale=str(raw.get("rationale", "")),
            client_order_id=str(raw.get("client_order_id", "")),
        )
    except Exception:
        logger.debug("robinhood_execution_panel: skipping malformed intent %r", raw, exc_info=True)
        return None


def read_execution_queue(path: Optional[Path] = None) -> Optional[ExecutionQueueSnapshot]:
    """Parse the execution queue JSON file into a snapshot.

    Returns ``None`` when the file is missing, empty, corrupt, or not a JSON
    object — never raises (CONSTRAINT #6) and never fabricates a queue
    (CONSTRAINT #4).
    """
    target = path or EXECUTION_QUEUE_PATH
    try:
        if not target.exists():
            return None
        raw_text = target.read_text(encoding="utf-8").strip()
        if not raw_text:
            return None
        payload = json.loads(raw_text)
        if not isinstance(payload, dict):
            return None
        raw_intents = payload.get("intents") or []
        intents = [
            intent
            for intent in (_coerce_intent(i) for i in raw_intents if isinstance(i, dict))
            if intent is not None
        ]
        return ExecutionQueueSnapshot(
            generated_at=str(payload.get("generated_at", "")),
            mode=str(payload.get("mode", "off")),
            kill_switch_active=bool(payload.get("kill_switch_active", False)),
            max_notional_per_order=float(payload.get("max_notional_per_order", 0.0) or 0.0),
            n_intents=int(payload.get("n_intents", len(intents))),
            n_placeable=int(payload.get("n_placeable", sum(1 for i in intents if i.allow_place))),
            intents=intents,
        )
    except Exception:
        logger.debug("read_execution_queue: failed to parse %s", target, exc_info=True)
        return None


def read_execution_receipts(path: Optional[Path] = None, max_lines: int = 50) -> List[Dict[str, Any]]:
    """Tail the agent-authored receipts JSONL file.

    Returns ``[]`` when the file is missing or every line is corrupt — never
    raises. Malformed individual lines are skipped (dead-letter tolerant),
    matching the append-only, best-effort nature of the receipts log.
    """
    target = path or EXECUTION_RECEIPTS_PATH
    try:
        if not target.exists():
            return []
        lines = target.read_text(encoding="utf-8").splitlines()
    except Exception:
        logger.debug("read_execution_receipts: failed to read %s", target, exc_info=True)
        return []

    entries: List[Dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except Exception:
            continue
        if isinstance(parsed, dict):
            entries.append(parsed)
    return entries[-max_lines:] if max_lines > 0 else entries


def queue_age_seconds(snapshot: ExecutionQueueSnapshot, *, now: Optional[datetime] = None) -> float:
    """Seconds elapsed since ``snapshot.generated_at``; ``NaN`` if unparsable (CONSTRAINT #4)."""
    try:
        generated = datetime.fromisoformat(snapshot.generated_at)
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return max(0.0, (current - generated).total_seconds())
    except Exception:
        return float("nan")


def is_queue_stale(snapshot: ExecutionQueueSnapshot, *, now: Optional[datetime] = None) -> bool:
    """Mirrors the ``robinhood-execution`` skill's ~30-minute staleness rule.

    An unparsable ``generated_at`` is treated as stale (fail toward caution).
    """
    age = queue_age_seconds(snapshot, now=now)
    return True if age != age else age > STALE_QUEUE_SECONDS  # NaN check


def mfa_secret_configured(settings_obj: Any = None) -> bool:
    """Whether ``RH_MFA_SECRET`` is set to a non-empty value.

    Returns a boolean only — the secret's value is never captured in a
    variable outside this function's local scope, so it can never be
    accidentally rendered (CONSTRAINT #3). Falls back to importing the
    project's ``settings`` singleton when no object is supplied.
    """
    try:
        obj = settings_obj
        if obj is None:
            from settings import settings as obj  # noqa: PLC0415
        value = getattr(obj, "RH_MFA_SECRET", None)
        return bool(value) and len(str(value).strip()) > 0
    except Exception:
        return False
