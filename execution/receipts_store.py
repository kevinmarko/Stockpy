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
#        "client_order_id", "mcp_order_id"}
#     where dedup_key = f"{YYYY-MM-DD}:{SYMBOL}:{SIDE}" (UTC date) — stable
#     across the 60s client_order_id bucketing, so a repeated recommendation on
#     the same day is recognised as already-placed.
#
# Everything is dead-letter resilient (CONSTRAINT #6): blank/corrupt JSONL lines
# are skipped (logged at DEBUG), reconciliation never raises (returns an
# error-shaped report with ok=False on any failure), and the atomic-append helper
# swallows write failures rather than crashing a best-effort caller.
# =============================================================================

from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

RECEIPTS_FILENAME = "execution_receipts.jsonl"
PLACED_FILENAME = "execution_placed.jsonl"

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
    }


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

    Returns a structured report::

        {
          "placed_count": int,          # placed-ledger entries considered
          "filled_matched": int,        # ledger entries with a matching fill
          "unmatched_placed": [ ... ],  # ledger entries with NO matching fill
          "unexpected_fills": [ ... ],  # fills with NO matching ledger entry
          "ok": bool,                   # True iff no unmatched/unexpected
        }

    Never raises: on any failure it returns an error-shaped report with
    ``ok=False`` and an ``error`` key (CONSTRAINT #6).
    """
    try:
        placed = read_placed_ledger(output_dir)
        placed_keys = {
            rec.get("dedup_key") or make_dedup_key(
                rec.get("symbol", ""), rec.get("side", ""), rec.get("ts"),
            )
            for rec in placed
        }

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

        matched_keys = placed_keys & set(fill_keys.keys())

        unmatched_placed = [
            rec for rec in placed
            if (rec.get("dedup_key") or make_dedup_key(
                rec.get("symbol", ""), rec.get("side", ""), rec.get("ts"),
            )) not in fill_keys
        ]

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

        ok = not unmatched_placed and not unexpected_fills
        return {
            "placed_count": len(placed),
            "filled_matched": len(matched_keys),
            "unmatched_placed": unmatched_placed,
            "unexpected_fills": unexpected_fills,
            "ok": ok,
        }
    except Exception as exc:
        logger.error("receipts_store: reconciliation failed (%s)", exc)
        return {
            "placed_count": 0,
            "filled_matched": 0,
            "unmatched_placed": [],
            "unexpected_fills": [],
            "ok": False,
            "error": str(exc),
        }
