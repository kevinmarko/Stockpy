"""Tier 8 execution-receipts store: the 'what actually happened' layer for the Robinhood human-in-loop path. Reads the receipts JSONL the robinhood-execution skill appends, owns an append-only placed-intent ledger, and reconciles that ledger against actual FILLED Robinhood orders (reusing the FIFO reconstruction in data/robinhood_orders.py). Contains no order-submission code and is fully dead-letter resilient."""

# =============================================================================
# MODULE: EXECUTION RECEIPTS STORE  (Tier 8 — Robinhood human-in-loop bridge)
# File: execution/receipts_store.py
#
# This is the "what actually happened" layer for the Robinhood human-in-loop
# execution path.  It lives INSIDE the sanctioned `execution/` order-code zone
# but contains NO order-submission code of any kind — it only READS receipts the
# `robinhood-execution` skill appends, OWNS a placed-intent ledger, and
# RECONCILES the ledger against actual FILLED Robinhood orders (via the existing
# PURE FIFO reconstruction in `data/robinhood_orders.py`, never reimplemented).
#
# Two shared on-disk files (both APPEND-ONLY JSONL, one JSON object per line):
#
#   output/execution_receipts.jsonl  — written by the skill, READ-ONLY here:
#       {"ts", "symbol", "side", "qty", "action":"reviewed|placed|skipped",
#        "mcp_order_id", "note"}
#
#   output/execution_placed.jsonl    — the placed-intent ledger, OWNED here:
#       {"ts", "dedup_key", "symbol", "side", "qty", "target_notional",
#        "client_order_id", "mcp_order_id", "approval_id", "ref_id", "run_id"}
#     In Robinhood trade-approval mode a ledger line means "submitted for the
#     operator's approval", not "executed": `approval_id` carries the id the
#     MCP returned and the approval's outcome lives in the approvals log below.
#     The last three fields are optional (None on pre-approval-mode rows).
#
#   output/execution_approvals.jsonl — the approval-outcome log, OWNED here:
#       {"ts", "approval_id", "state", "state_raw", "symbol", "side",
#        "dedup_key", "ref_id", "note"}
#     Append-only; the LAST line for an approval_id is its current state.
#     where dedup_key = f"{YYYY-MM-DD}:{SYMBOL}:{SIDE}" (UTC date) — stable
#     across the 60s client_order_id bucketing, so a repeated recommendation on
#     the same day is recognised as already-placed.
#
# Everything is dead-letter resilient (CONSTRAINT #6): blank/corrupt JSONL lines
# are skipped (logged at DEBUG), reconciliation never raises (returns an
# error-shaped report with ok=False on any failure), and the atomic-append helper
# swallows write failures rather than crashing a best-effort caller.
#
# Reviewed false positive (stockpy_codebase_auditor `orphaned_module`, 2026-08):
# this module has no in-repo Python caller by design — its producer is the
# external `robinhood-execution` Claude Code skill (appends
# output/execution_receipts.jsonl directly, not via a Python import), and it
# is exercised by tests/test_receipts_store.py and tests/test_robinhood_e2e.py.
# Not dead code; simply not yet wired to an in-repo CLI/API reader. Wiring one
# up is a deliberate follow-on decision, not something to bolt on silently
# here.
# =============================================================================

from __future__ import annotations

import json
import logging
import math
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

RECEIPTS_FILENAME = "execution_receipts.jsonl"
PLACED_FILENAME = "execution_placed.jsonl"
APPROVALS_FILENAME = "execution_approvals.jsonl"

# Fixed namespace for deterministic Robinhood `ref_id`s. NEVER change it: every
# ref_id already sent to Robinhood was derived from it, and a new namespace would
# make a retry of an already-submitted order look like a brand-new order.
_REF_ID_NAMESPACE = uuid.UUID("6f1c2e0a-5b7d-4c3e-9a41-8d2f6b0e7c15")

# Normalised Robinhood trade-approval states. Robinhood documents these five
# (pending, approved, declined, expired, auto-approved); anything else is kept
# verbatim in `state_raw` and normalised to "unknown", never guessed.
APPROVAL_STATES = frozenset({"pending", "approved", "declined", "expired", "auto_approved"})
_TERMINAL_NOT_EXECUTED = frozenset({"declined", "expired"})

# How close a ledger notional/qty must be to a reconstructed fill to count as a
# match (share-count based reconciliation is qty-driven; notional is advisory).
_QTY_MATCH_TOLERANCE = 1e-6


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _resolve_output_dir(output_dir: Optional[Any]) -> Path:
    """Return the output directory, defaulting to ``settings.OUTPUT_DIR``.

    ``settings`` is imported lazily so this module stays import-light and does
    not pull the whole settings graph into cheap unit tests that pass an
    explicit ``output_dir``.
    """
    if output_dir is not None:
        return Path(output_dir)
    try:
        from settings import settings  # local import — avoid import cycle
        return Path(settings.OUTPUT_DIR)
    except Exception as exc:
        logger.debug("receipts_store: settings.OUTPUT_DIR unavailable (%s); using ./output", exc)
        return Path("./output")


# ---------------------------------------------------------------------------
# JSONL read helper (tolerant of blank / corrupt lines)
# ---------------------------------------------------------------------------

def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    """Parse a JSONL file into a list of dicts, skipping blank/corrupt lines.

    Never raises: a missing file yields ``[]``; a malformed line is logged at
    DEBUG and skipped so one bad line never voids the whole ledger.
    """
    if not path.exists():
        return []
    records: List[Dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.debug("receipts_store: could not read %s (%s)", path, exc)
        return []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except Exception as exc:
            logger.debug("receipts_store: skipping corrupt line %d in %s (%s)", lineno, path, exc)
            continue
        if isinstance(obj, dict):
            records.append(obj)
        else:
            logger.debug("receipts_store: skipping non-object line %d in %s", lineno, path)
    return records


# ---------------------------------------------------------------------------
# Dedup key
# ---------------------------------------------------------------------------

def make_dedup_key(symbol: str, side: str, ts: Optional[Any] = None) -> str:
    """Build a stable per-day dedup key: ``f"{YYYY-MM-DD}:{SYMBOL}:{SIDE}"``.

    The date is the UTC calendar date of ``ts`` (a ``datetime``, an ISO8601
    string, or ``None`` → now).  Because it drops the time component, two
    recommendations for the same symbol/side on the same UTC day map to the same
    key even though their 60s-bucketed ``client_order_id`` differs.
    """
    dt = _coerce_dt(ts) or datetime.now(timezone.utc)
    date_str = dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
    return f"{date_str}:{str(symbol).upper()}:{str(side).lower()}"


def _coerce_dt(ts: Optional[Any]) -> Optional[datetime]:
    """Coerce ``ts`` (datetime | ISO8601 str | None) to a UTC-aware datetime."""
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public read API
# ---------------------------------------------------------------------------

def read_receipts(output_dir: Optional[Any] = None) -> List[Dict[str, Any]]:
    """Read ``output/execution_receipts.jsonl`` (skill-written, READ-ONLY here).

    Tolerant of blank/corrupt lines.  Returns ``[]`` when the file is absent.
    """
    path = _resolve_output_dir(output_dir) / RECEIPTS_FILENAME
    return _read_jsonl(path)


def read_placed_ledger(output_dir: Optional[Any] = None) -> List[Dict[str, Any]]:
    """Read the placed-intent ledger ``output/execution_placed.jsonl``.

    Tolerant of blank/corrupt lines.  Returns ``[]`` when the file is absent.
    """
    path = _resolve_output_dir(output_dir) / PLACED_FILENAME
    return _read_jsonl(path)


# ---------------------------------------------------------------------------
# Ledger append (atomic-append: write-then-rename over a rebuilt copy)
# ---------------------------------------------------------------------------

def append_placed(record: Dict[str, Any], output_dir: Optional[Any] = None) -> None:
    """Append one placed-intent record to the ledger, atomically.

    A ``dedup_key`` is derived (from the record's ``symbol``/``side``/``ts``)
    when absent, and missing optional fields are normalised to ``None`` so every
    line conforms to the ledger schema.  The write is atomic: the existing
    ledger is read, the new line appended in-memory, then the whole file is
    written to a temp path and ``os.replace``d over the original — so a crash
    mid-write never leaves a truncated/torn JSONL file.  Never raises
    (CONSTRAINT #6): any failure is logged and swallowed.
    """
    try:
        out_dir = _resolve_output_dir(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / PLACED_FILENAME

        norm = _normalise_placed_record(record)
        existing = _read_jsonl(path)
        existing.append(norm)

        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for rec in existing:
                fh.write(json.dumps(rec, sort_keys=True) + "\n")
        os.replace(tmp, path)
        logger.debug("receipts_store: appended placed record %s → %s",
                     norm.get("dedup_key"), path)
    except Exception as exc:
        logger.warning("receipts_store: failed to append placed record (%s); skipping", exc)


def _normalise_placed_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce an incoming record to the full placed-ledger schema."""
    rec = dict(record or {})
    symbol = str(rec.get("symbol", "")).upper()
    side = str(rec.get("side", "")).lower()
    ts_val = rec.get("ts")
    dt = _coerce_dt(ts_val) or datetime.now(timezone.utc)
    ts_iso = dt.astimezone(timezone.utc).isoformat()
    dedup_key = rec.get("dedup_key") or make_dedup_key(symbol, side, dt)
    return {
        "ts": ts_iso,
        "dedup_key": dedup_key,
        "symbol": symbol,
        "side": side,
        "qty": _opt_float(rec.get("qty")),
        "target_notional": _opt_float(rec.get("target_notional")),
        "client_order_id": str(rec.get("client_order_id", "") or ""),
        "mcp_order_id": (str(rec["mcp_order_id"]) if rec.get("mcp_order_id") not in (None, "") else None),
        "approval_id": _opt_str(rec.get("approval_id")),
        "ref_id": _opt_str(rec.get("ref_id")),
        "run_id": _opt_str(rec.get("run_id")),
    }


def _opt_str(x: Any) -> Optional[str]:
    """Coerce to a non-empty string, or ``None``."""
    if x is None:
        return None
    s = str(x).strip()
    return s or None


def _opt_float(x: Any) -> Optional[float]:
    """Coerce to a finite float, or ``None`` (never a fabricated 0.0)."""
    if x is None or x == "":
        return None
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Robinhood ref_id + trade-approval outcome log
# ---------------------------------------------------------------------------

def make_ref_id(client_order_id: Any) -> Optional[str]:
    """Deterministic Robinhood ``ref_id`` (a UUID string) for one logical order.

    ``place_equity_order`` deduplicates on ``ref_id``. Deriving it from the
    queue's ``client_order_id`` (uuid5 under a fixed namespace) means a retry,
    a re-run of the skill, or a second agent session submitting the same
    intent FROM THE SAME QUEUE FILE sends the SAME ref_id, so Robinhood itself
    refuses the duplicate. ``client_order_id`` is time-bucketed
    (``make_client_order_id``), so a refreshed queue gives the same trade a new
    id — the day-wide guard is the ``dedup_key`` ledger; this is the second
    layer under it, not a replacement. Returns ``None`` for a blank
    ``client_order_id`` (never a random fallback, which would defeat the point).
    """
    coid = _opt_str(client_order_id)
    if coid is None:
        return None
    return str(uuid.uuid5(_REF_ID_NAMESPACE, coid))


def normalise_approval_state(raw: Any) -> str:
    """Map a Robinhood approval state string onto :data:`APPROVAL_STATES`.

    Tolerates case, ``-``/space separators and ``STATE_``/``APPROVAL_STATE_``
    prefixes (``"STATE_AUTO_APPROVED"`` → ``"auto_approved"``). Anything that
    does not map is ``"unknown"`` — never a guessed state.
    """
    s = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    for prefix in ("trade_approval_state_", "approval_state_", "state_"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    if s == "autoapproved":
        s = "auto_approved"
    return s if s in APPROVAL_STATES else "unknown"


def append_approval_event(record: Dict[str, Any], output_dir: Optional[Any] = None) -> bool:
    """Append one approval-state observation to ``execution_approvals.jsonl``.

    ``record`` needs an ``approval_id`` and a ``state`` (raw Robinhood value or
    an already-normalised one). Returns ``True`` on success, ``False`` when the
    record is unusable (no approval_id) or the write failed. Never raises
    (CONSTRAINT #6). Same atomic rewrite-and-replace as :func:`append_placed`.
    """
    try:
        rec = dict(record or {})
        approval_id = _opt_str(rec.get("approval_id"))
        if approval_id is None:
            logger.warning("receipts_store: approval event without approval_id; not recorded")
            return False
        raw_state = rec.get("state_raw", rec.get("state"))
        dt = _coerce_dt(rec.get("ts")) or datetime.now(timezone.utc)
        symbol = str(rec.get("symbol", "") or "").upper()
        side = str(rec.get("side", "") or "").lower()
        norm = {
            "ts": dt.astimezone(timezone.utc).isoformat(),
            "approval_id": approval_id,
            "state": normalise_approval_state(raw_state),
            "state_raw": _opt_str(raw_state),
            "symbol": symbol or None,
            "side": side or None,
            "dedup_key": _opt_str(rec.get("dedup_key")),
            "ref_id": _opt_str(rec.get("ref_id")),
            "note": _opt_str(rec.get("note")),
        }
        out_dir = _resolve_output_dir(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / APPROVALS_FILENAME
        existing = _read_jsonl(path)
        existing.append(norm)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for r in existing:
                fh.write(json.dumps(r, sort_keys=True) + "\n")
        os.replace(tmp, path)
        return True
    except Exception as exc:
        logger.warning("receipts_store: failed to append approval event (%s); skipping", exc)
        return False


def read_approval_events(output_dir: Optional[Any] = None) -> List[Dict[str, Any]]:
    """Read ``execution_approvals.jsonl`` (tolerant; ``[]`` when absent)."""
    return _read_jsonl(_resolve_output_dir(output_dir) / APPROVALS_FILENAME)


def latest_approval_states(output_dir: Optional[Any] = None) -> Dict[str, Dict[str, Any]]:
    """Return ``{approval_id: latest event}`` — the last line per id wins."""
    latest: Dict[str, Dict[str, Any]] = {}
    for rec in read_approval_events(output_dir):
        aid = _opt_str(rec.get("approval_id"))
        if aid is not None:
            latest[aid] = rec
    return latest


def pending_approval_ids(output_dir: Optional[Any] = None) -> List[str]:
    """Approval ids whose latest recorded state is ``pending``.

    These are the ids the skill must re-check with ``get_trade_approvals`` —
    the log only knows what was last observed, never the live state.
    """
    return [aid for aid, rec in latest_approval_states(output_dir).items()
            if rec.get("state") == "pending"]


# ---------------------------------------------------------------------------
# Idempotency check
# ---------------------------------------------------------------------------

def already_placed(
    symbol: str,
    side: str,
    output_dir: Optional[Any] = None,
    *,
    on_date: Optional[Any] = None,
) -> bool:
    """Return ``True`` if a placed-ledger entry already exists for this
    ``symbol``/``side`` on the given UTC date (default: today).

    Uses the ``dedup_key`` contract, so it is robust to the 60s
    ``client_order_id`` bucketing.  Never raises — on any read failure it
    conservatively returns ``False`` (an unknown state is treated as "not yet
    placed" so the operator is prompted rather than silently skipped).
    """
    try:
        key = make_dedup_key(symbol, side, on_date)
        for rec in read_placed_ledger(output_dir):
            if rec.get("dedup_key") == key:
                return True
        return False
    except Exception as exc:
        logger.debug("receipts_store: already_placed check failed (%s); returning False", exc)
        return False


# ---------------------------------------------------------------------------
# Reconciliation against actual FILLED Robinhood orders
# ---------------------------------------------------------------------------

def reconcile(
    output_dir: Optional[Any] = None,
    *,
    orders_fetcher: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    symbol_resolver: Optional[Callable[[str], Optional[str]]] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Cross-check the placed-intent ledger against actual FILLED orders.

    Reuses the PURE FIFO fill reconstruction in :mod:`data.robinhood_orders`
    (``fetch_filled_orders`` → ``OrderFill``) — the FIFO logic is NEVER
    reimplemented here.  ``orders_fetcher`` / ``symbol_resolver`` are injectable
    so tests run fully offline.

    Matching is per (UTC-date, SYMBOL, SIDE) — the same granularity as the
    ledger's ``dedup_key`` — because the ledger tracks placement *intent* while
    fills carry exact execution prices/quantities that need not equal the
    intended qty (BUY intents are notional-based, sells may partially fill).

    Within a shared key, a genuine "match" additionally requires the
    aggregated placed quantity to be within ``_QTY_MATCH_TOLERANCE`` of the
    aggregated fill quantity — but ONLY when every placed-ledger entry for
    that key carries a known ``qty`` (i.e. was NOT a purely notional-based
    intent, whose realized share count legitimately can't be predicted in
    advance — see the docstring paragraph above). When a known aggregate
    quantity diverges from the fill quantity beyond tolerance (e.g. placed
    100 shares, only 3 actually filled), that key is NOT silently counted
    as matched (nor folded into ``unmatched_placed``/``unexpected_fills``,
    which would misrepresent it as "no fill at all") — it is surfaced in its
    own honest ``quantity_mismatch`` bucket.

    Returns a structured report::

        {
          "placed_count": int,          # placed-ledger entries considered
          "filled_matched": int,        # keys with a matching (within-tolerance) fill
          "unmatched_placed": [ ... ],  # ledger entries with NO matching fill key
          "unexpected_fills": [ ... ],  # fills with NO matching ledger key
          "quantity_mismatch": [ ... ], # shared keys whose known qty diverges beyond tolerance
          "not_approved": [ ... ],      # approval requests the operator declined / that expired
          "awaiting_approval": [ ... ], # approval requests still pending (last observed state)
          "ok": bool,                   # True iff no unmatched/unexpected/mismatched
        }

    Never raises: on any failure it returns an error-shaped report with
    ``ok=False`` and an ``error`` key (CONSTRAINT #6).
    """
    try:
        placed = read_placed_ledger(output_dir)
        placed_by_key: Dict[str, List[Dict[str, Any]]] = {}
        for rec in placed:
            key = rec.get("dedup_key") or make_dedup_key(
                rec.get("symbol", ""), rec.get("side", ""), rec.get("ts"),
            )
            placed_by_key.setdefault(key, []).append(rec)
        placed_keys = set(placed_by_key.keys())

        # Reuse the existing FIFO-source fetch (READ ONLY) — do NOT reimplement.
        from data.robinhood_orders import fetch_filled_orders
        fills = fetch_filled_orders(
            force=force,
            orders_fetcher=orders_fetcher,
            symbol_resolver=symbol_resolver,
        )

        # Fills grouped by the same (date, symbol, side) key as the ledger.
        fill_keys: Dict[str, List[Any]] = {}
        for f in fills:
            key = make_dedup_key(f.symbol, f.side, f.timestamp)
            fill_keys.setdefault(key, []).append(f)

        shared_keys = placed_keys & set(fill_keys.keys())

        matched_keys: set = set()
        quantity_mismatch: List[Dict[str, Any]] = []
        for key in shared_keys:
            group = placed_by_key[key]
            known_qtys = [rec.get("qty") for rec in group if rec.get("qty") is not None]
            fill_qty_total = round(sum(getattr(x, "quantity", 0.0) for x in fill_keys[key]), 8)

            if len(known_qtys) == len(group):
                # Every placed record for this key carries a known intended
                # qty (e.g. a SELL of a known position), so a real
                # tolerance-gated comparison against the actual fill qty is
                # meaningful.
                placed_qty_total = round(sum(known_qtys), 8)
                if abs(placed_qty_total - fill_qty_total) <= _QTY_MATCH_TOLERANCE:
                    matched_keys.add(key)
                else:
                    symbol = key.split(":")[1] if ":" in key else ""
                    side = key.split(":")[2] if key.count(":") >= 2 else ""
                    quantity_mismatch.append({
                        "dedup_key": key,
                        "symbol": symbol,
                        "side": side,
                        "placed_qty": placed_qty_total,
                        "filled_qty": fill_qty_total,
                        "diff": round(placed_qty_total - fill_qty_total, 8),
                    })
            else:
                # At least one placed record for this key has no known qty
                # (a notional-based BUY intent) — the realized fill quantity
                # legitimately can't be predicted in advance, so key
                # presence alone remains the honest match criterion here,
                # exactly as before this fix.
                matched_keys.add(key)

        unfilled = [
            rec for rec in placed
            if (rec.get("dedup_key") or make_dedup_key(
                rec.get("symbol", ""), rec.get("side", ""), rec.get("ts"),
            )) not in fill_keys
        ]

        # Trade-approval mode: a ledger line is an approval REQUEST. One the
        # operator declined, or that expired, legitimately has no fill — that
        # is the gate working, not a discrepancy. One still pending has not
        # had its chance to fill yet. Both get their own honest bucket; an
        # approval with no recorded outcome (or approved but unfilled) stays
        # in unmatched_placed exactly as before.
        approval_states = latest_approval_states(output_dir)
        unmatched_placed: List[Dict[str, Any]] = []
        not_approved: List[Dict[str, Any]] = []
        awaiting_approval: List[Dict[str, Any]] = []
        for rec in unfilled:
            aid = _opt_str(rec.get("approval_id"))
            state = (approval_states.get(aid) or {}).get("state") if aid else None
            if state in _TERMINAL_NOT_EXECUTED:
                not_approved.append({**rec, "approval_state": state})
            elif state == "pending":
                awaiting_approval.append({**rec, "approval_state": state})
            else:
                unmatched_placed.append(rec)

        unexpected_fills = [
            {
                "dedup_key": key,
                "symbol": key.split(":")[1] if ":" in key else "",
                "side": key.split(":")[2] if key.count(":") >= 2 else "",
                "n_fills": len(group),
                "total_qty": round(sum(getattr(x, "quantity", 0.0) for x in group), 8),
            }
            for key, group in sorted(fill_keys.items())
            if key not in placed_keys
        ]

        ok = not unmatched_placed and not unexpected_fills and not quantity_mismatch
        return {
            "placed_count": len(placed),
            "filled_matched": len(matched_keys),
            "unmatched_placed": unmatched_placed,
            "unexpected_fills": unexpected_fills,
            "quantity_mismatch": quantity_mismatch,
            "not_approved": not_approved,
            "awaiting_approval": awaiting_approval,
            "ok": ok,
        }
    except Exception as exc:
        logger.error("receipts_store: reconciliation failed (%s)", exc)
        return {
            "placed_count": 0,
            "filled_matched": 0,
            "unmatched_placed": [],
            "unexpected_fills": [],
            "quantity_mismatch": [],
            "not_approved": [],
            "awaiting_approval": [],
            "ok": False,
            "error": str(exc),
        }


# NOTE: the auditor's LOW `orphaned_module` finding on this file is expected and accurate —
# see the module docstring above: its real caller is the `robinhood-execution` agent skill
# (.agents/skills/), not a Python import the static AST auditor can see. Do not silence it
# by re-adding a fake dunder-main entry-point stub (the auditor's _is_entrypoint check does
# a literal source substring match, so even a comment containing that guard's exact text
# would fool it — write around it, as done here).
