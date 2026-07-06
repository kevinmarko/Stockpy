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
:class:`NotificationState`      — the last ntfy push `execution/queue_builder.py` attempted (frozen).
:func:`read_notification_state`  — parse ``output/execution_queue_notified.json`` → state or ``None``.
:func:`notification_age_seconds` — age of a `NotificationState` relative to ``now``.
:func:`ntfy_topic_configured`    — whether ``NTFY_TOPIC`` is set (boolean only — never the value).
:func:`read_placed_ledger`       — tail ``output/execution_placed.jsonl`` → list[dict] (absent-file tolerant).
:func:`derive_intent_status`     — map one queued intent + receipts → a :class:`IntentStatus` badge.
:func:`build_reconciliation_summary` — cross-check the placed ledger against receipts → :class:`ReconciliationSummary`.
:data:`EXECUTION_QUEUE_PATH`, :data:`EXECUTION_RECEIPTS_PATH`, :data:`NOTIFIED_STATE_PATH`, :data:`EXECUTION_PLACED_PATH` — canonical file paths.

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
NOTIFIED_STATE_PATH: Path = _REPO_ROOT / "output" / "execution_queue_notified.json"
# Append-only placement ledger written by the execution agent's receipts store
# (a sibling module — this panel reads the file format directly and never
# imports that module, so the two stay decoupled).
EXECUTION_PLACED_PATH: Path = _REPO_ROOT / "output" / "execution_placed.jsonl"

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


# ---------------------------------------------------------------------------
# Per-intent status derivation
# ---------------------------------------------------------------------------
# Canonical status strings a queued intent can carry once cross-referenced with
# the agent-authored receipts log.  Ordered by "how far along" the intent is so
# the GUI can pick a colour deterministically.
STATUS_QUEUED = "queued"
STATUS_BLOCKED = "blocked"
STATUS_PREVIEWED = "previewed"
STATUS_SKIPPED = "skipped"
STATUS_PLACED = "placed"

# Receipt ``action`` field → panel status.  The receipts schema uses
# ``"reviewed"`` for a preview-only outcome; the GUI surfaces that as
# ``"previewed"`` for consistency with the queue-side vocabulary.
_RECEIPT_ACTION_TO_STATUS: Dict[str, str] = {
    "reviewed": STATUS_PREVIEWED,
    "previewed": STATUS_PREVIEWED,
    "placed": STATUS_PLACED,
    "skipped": STATUS_SKIPPED,
}

# Colour band for each status — consumed by the GUI to tint the status cell.
# "success" (green) / "warning" (amber) / "neutral" (grey).
STATUS_COLOR: Dict[str, str] = {
    STATUS_PLACED: "success",
    STATUS_PREVIEWED: "neutral",
    STATUS_SKIPPED: "warning",
    STATUS_BLOCKED: "warning",
    STATUS_QUEUED: "neutral",
}

# Precedence when multiple receipts match one intent: the most-advanced /
# most-informative outcome wins (a later "placed" trumps an earlier "reviewed").
_STATUS_PRECEDENCE: Dict[str, int] = {
    STATUS_QUEUED: 0,
    STATUS_BLOCKED: 1,
    STATUS_SKIPPED: 2,
    STATUS_PREVIEWED: 3,
    STATUS_PLACED: 4,
}


@dataclass(frozen=True)
class IntentStatus:
    """Derived status badge for one queued intent.

    Attributes
    ----------
    symbol, side : str
        The intent's identity (matched against receipts by symbol+side).
    status : str
        One of the ``STATUS_*`` constants.
    color : str
        ``"success"`` / ``"warning"`` / ``"neutral"`` — a colour band the GUI
        maps to green / amber / grey.
    detail : str
        Human-readable annotation (gate reasons for ``blocked``, the receipt
        note / mcp_order_id for placed/skipped/previewed, or "" for queued).
    """

    symbol: str
    side: str
    status: str
    color: str
    detail: str


def _matching_receipts(symbol: str, side: str, receipts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Receipts whose symbol+side match the given intent (case-insensitive)."""
    sym = (symbol or "").strip().upper()
    sd = (side or "").strip().lower()
    out: List[Dict[str, Any]] = []
    for r in receipts:
        if not isinstance(r, dict):
            continue
        if str(r.get("symbol", "")).strip().upper() != sym:
            continue
        if str(r.get("side", "")).strip().lower() != sd:
            continue
        out.append(r)
    return out


def derive_intent_status(
    intent: QueuedIntent, receipts: List[Dict[str, Any]]
) -> IntentStatus:
    """Derive a status badge for one queued intent.

    Rules (in order):
      1. A matching receipt (by symbol+side) wins — its ``action`` maps to
         ``placed`` / ``previewed`` / ``skipped``.  When several receipts match,
         the most-advanced outcome wins (placed > previewed > skipped).
      2. No receipt + ``allow_place`` False → ``blocked`` (gate reasons shown).
      3. Otherwise → ``queued`` (default).

    Never raises (CONSTRAINT #6); never fabricates a receipt that doesn't
    exist (CONSTRAINT #4).
    """
    matches = _matching_receipts(intent.symbol, intent.side, receipts)
    if matches:
        best: Optional[Dict[str, Any]] = None
        best_rank = -1
        for r in matches:
            status = _RECEIPT_ACTION_TO_STATUS.get(
                str(r.get("action", "")).strip().lower()
            )
            if status is None:
                continue
            rank = _STATUS_PRECEDENCE.get(status, 0)
            if rank > best_rank:
                best_rank = rank
                best = r
        if best is not None:
            status = _RECEIPT_ACTION_TO_STATUS[str(best.get("action", "")).strip().lower()]
            note = str(best.get("note", "") or "").strip()
            oid = str(best.get("mcp_order_id", "") or "").strip()
            detail = note or (f"order {oid}" if oid else "")
            return IntentStatus(
                symbol=intent.symbol,
                side=intent.side,
                status=status,
                color=STATUS_COLOR.get(status, "neutral"),
                detail=detail,
            )

    if not intent.allow_place:
        detail = "; ".join(intent.gate_reasons) if intent.gate_reasons else "not placeable"
        return IntentStatus(
            symbol=intent.symbol,
            side=intent.side,
            status=STATUS_BLOCKED,
            color=STATUS_COLOR[STATUS_BLOCKED],
            detail=detail,
        )

    return IntentStatus(
        symbol=intent.symbol,
        side=intent.side,
        status=STATUS_QUEUED,
        color=STATUS_COLOR[STATUS_QUEUED],
        detail="",
    )


# ---------------------------------------------------------------------------
# Placement ledger + reconciliation
# ---------------------------------------------------------------------------


def read_placed_ledger(path: Optional[Path] = None, max_lines: int = 200) -> List[Dict[str, Any]]:
    """Tail the append-only placement ledger ``output/execution_placed.jsonl``.

    Schema per line: ``{"ts","dedup_key","symbol","side","qty","target_notional",
    "client_order_id","mcp_order_id"}``.  Returns ``[]`` when the file is absent
    or every line is corrupt — never raises (CONSTRAINT #6).  Malformed lines
    are skipped individually (dead-letter tolerant).
    """
    target = path or EXECUTION_PLACED_PATH
    try:
        if not target.exists():
            return []
        lines = target.read_text(encoding="utf-8").splitlines()
    except Exception:
        logger.debug("read_placed_ledger: failed to read %s", target, exc_info=True)
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


@dataclass(frozen=True)
class ReconciliationSummary:
    """Cross-check of the placement ledger against the receipts log.

    Attributes
    ----------
    placed_count : int
        Number of entries in ``execution_placed.jsonl``.
    matched : list[dict]
        Ledger entries that have a corresponding ``placed`` receipt (matched by
        symbol+side).
    unmatched : list[dict]
        Ledger entries with no corresponding ``placed`` receipt — surfaced so
        the operator can investigate a possible receipt/ledger divergence.
    """

    placed_count: int
    matched: List[Dict[str, Any]]
    unmatched: List[Dict[str, Any]]


def build_reconciliation_summary(
    placed_ledger: List[Dict[str, Any]], receipts: List[Dict[str, Any]]
) -> ReconciliationSummary:
    """Reconcile the placement ledger against ``placed`` receipts.

    A ledger entry is "matched" when at least one receipt with
    ``action == "placed"`` shares its symbol+side.  Never raises (CONSTRAINT
    #6); never fabricates ledger rows (CONSTRAINT #4).
    """
    placed_receipts = [
        r for r in receipts
        if isinstance(r, dict) and str(r.get("action", "")).strip().lower() == "placed"
    ]
    matched: List[Dict[str, Any]] = []
    unmatched: List[Dict[str, Any]] = []
    for entry in placed_ledger:
        if not isinstance(entry, dict):
            continue
        if _matching_receipts(str(entry.get("symbol", "")), str(entry.get("side", "")), placed_receipts):
            matched.append(entry)
        else:
            unmatched.append(entry)
    return ReconciliationSummary(
        placed_count=len(placed_ledger),
        matched=matched,
        unmatched=unmatched,
    )


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


@dataclass(frozen=True)
class NotificationState:
    """Last ntfy push `execution/queue_builder.py` attempted (or none yet).

    Mirrors ``output/execution_queue_notified.json`` verbatim; see
    `execution.queue_builder._notify_new_intents` for how it's written.
    """

    last_notified_at: str
    last_notified_title: str
    last_notified_count: int
    last_notified_priority: str


def read_notification_state(path: Optional[Path] = None) -> Optional[NotificationState]:
    """Parse the notify-dedup sidecar into the last-push state, or ``None``.

    Returns ``None`` when the file is missing, corrupt, or no push has been
    attempted yet (e.g. the queue has existed since before any intent cleared
    the dedup check) — never raises (CONSTRAINT #6), never fabricates a push
    that didn't happen (CONSTRAINT #4).
    """
    target = path or NOTIFIED_STATE_PATH
    try:
        if not target.exists():
            return None
        raw_text = target.read_text(encoding="utf-8").strip()
        if not raw_text:
            return None
        payload = json.loads(raw_text)
        if not isinstance(payload, dict):
            return None
        last_at = payload.get("last_notified_at")
        if not last_at:
            return None  # sidecar exists (dedup keys only) but nothing sent yet
        return NotificationState(
            last_notified_at=str(last_at),
            last_notified_title=str(payload.get("last_notified_title") or ""),
            last_notified_count=int(payload.get("last_notified_count") or 0),
            last_notified_priority=str(payload.get("last_notified_priority") or "default"),
        )
    except Exception:
        logger.debug("read_notification_state: failed to parse %s", target, exc_info=True)
        return None


def notification_age_seconds(state: NotificationState, *, now: Optional[datetime] = None) -> float:
    """Seconds elapsed since ``state.last_notified_at``; ``NaN`` if unparsable (CONSTRAINT #4)."""
    try:
        sent = datetime.fromisoformat(state.last_notified_at)
        if sent.tzinfo is None:
            sent = sent.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return max(0.0, (current - sent).total_seconds())
    except Exception:
        return float("nan")


def ntfy_topic_configured() -> bool:
    """Whether ``NTFY_TOPIC`` is set to a non-empty value.

    Returns a boolean only — mirrors `alerting.notify`'s own env read
    (``NTFY_TOPIC`` is a plain ``os.environ`` var, not a pydantic Settings
    field, and is classified as a GUI secret in `gui/env_io.py`, so its
    cleartext must never be captured here — CONSTRAINT #3).
    """
    try:
        import os  # noqa: PLC0415
        return bool(os.environ.get("NTFY_TOPIC", "").strip())
    except Exception:
        return False


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
